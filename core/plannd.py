"""
plannd — Task planner for Kuza-v2

Provides get_plan(): sends a user prompt to the 0.5B model on port 8081
and returns a numbered step list for the 7B agent to execute.

Port assignments:
  8080 — Qwen2.5-Coder-7B  (agent execution only)
  8081 — Qwen2.5-0.5B       (planning + summarization)
  8082 — nomic-embed-text    (embeddings)
"""

import json
import re
import urllib.request
import urllib.error
from typing import Optional, List


# ── Planner prompt ────────────────────────────────────────────────────────────
# Single prompt used by ALL backends: local 0.5B, OpenRouter, UnlimitedClaude.
# Test and tune this prompt against remote models (faster iteration), then
# the same prompt runs on local — results are directly comparable.

PLANNER_PROMPT = """You plan multi-step software work for a coding agent.

Return either NO_PLAN or a numbered list containing 2 to 8 short, executable steps.

Rules:
- Return NO_PLAN for questions, conversation, searches, account lookups, research-only
  requests, or work that needs only one direct action.
- Plan only actions required by the user's exact software request.
- Copy every filename, path, command, argument, quantity, and peer name exactly.
- Never invent, abbreviate, rename, or relocate a filename supplied by the user.
- Never repeat a step unless the user explicitly requests repeated execution.
- Use concrete verbs such as Inspect, Create, Update, Run, Test, or Verify.
- A verification step must state a real check or command, not an assumption.
- Do not add Git operations unless the user explicitly requested them.
- Do not output code, markdown, explanations, examples, or introductory text.
"""


MAX_PLAN_STEPS = 8

_FILE_RE = re.compile(
    r"(?<![\w.-])((?:[\w.-]+/)*[\w.-]+\."
    r"(?:py|js|ts|jsx|tsx|html|css|json|yaml|yml|toml|txt|md|sh|sql))\b",
    re.IGNORECASE,
)
_SOFTWARE_ACTION_RE = re.compile(
    r"\b(?:create|write|build|implement|refactor|rewrite|edit|fix|patch|add|"
    r"update|delete|remove|install|configure|setup|deploy|run|execute|tests?|"
    r"debug|migrate)\b",
    re.IGNORECASE,
)
_SOFTWARE_NOUN_RE = re.compile(
    r"\b(?:code|file|script|program|module|function|class|app|application|api|"
    r"website|server|database|repository|repo|project|tests?|bug|feature|endpoint|"
    r"cli|package|dependency|config|python|javascript|typescript|shell|git)\b",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(
    r"^\s*(?:what|why|how|when|where|who|which|is|are|do|does|can|could|would|"
    r"should|will|was|were|has|have)\b",
    re.IGNORECASE,
)
_MULTI_STEP_RE = re.compile(
    r"(?:\bthen\b|\bafter(?:\s+that)?\b|\balso\b|\bfinally\b|\bnext\b|"
    r"\band\s+(?:run|execute|test|verify|create|write|add|update|fix|commit|push)\b|"
    r"(?:^|\n)\s*\d+[.)])",
    re.IGNORECASE,
)
_REPEAT_RE = re.compile(
    r"\b(?:again|twice|three\s+times|\d+\s+times|multiple\s+times|each)\b",
    re.IGNORECASE,
)


def should_plan(prompt: str) -> bool:
    """Return whether *prompt* is genuinely multi-step software work."""
    if not isinstance(prompt, str):
        return False
    text = prompt.strip()
    if not text or "@" in text and not _SOFTWARE_ACTION_RE.search(text):
        return False
    if _QUESTION_RE.match(text) or re.search(
        r"\b(?:explain|tell me about|help me understand|what(?:'s| is) the difference)\b",
        text,
        re.IGNORECASE,
    ):
        return False

    actions = {match.group(0).lower() for match in _SOFTWARE_ACTION_RE.finditer(text)}
    has_software_subject = bool(_SOFTWARE_NOUN_RE.search(text) or _FILE_RE.search(text))
    if not actions or not has_software_subject:
        return False

    if _MULTI_STEP_RE.search(text) or len(_FILE_RE.findall(text)) >= 2:
        return True
    if len(actions) >= 2:
        return True

    # A single architectural action can still need planning when it has several
    # requirements, while short one-file edits should execute directly.
    requirement_markers = len(re.findall(
        r"\b(?:with|including|that|for|using|supporting|across)\b",
        text,
        re.IGNORECASE,
    ))
    return len(text) >= 80 and requirement_markers >= 2


# ── Step parser ───────────────────────────────────────────────────────────────

def parse_steps(raw: str) -> List[str]:
    """
    Extract numbered steps from model output.

    Strips <think>...</think> blocks (R1-style reasoning traces),
    then collects lines matching "N. step" or "N) step".
    """
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    if text.upper().startswith("NO_PLAN"):
        return []

    steps: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"^(\d+)[.)]\s+(.+)$", line)
        if m:
            step = m.group(2).strip()
            if step:
                steps.append(step)
    if steps:
        last = steps[-1]
        if last and last[-1] not in ".!?)" and last[-1].isalpha():
            print(
                "[plannd] plan may be truncated — consider increasing max_tokens",
                flush=True,
            )
    return steps


# ── Tool-call step filter ─────────────────────────────────────────────────────

_TOOL_VERBS = re.compile(
    r"^(inspect|read|research|create|write|build|add|run|execute|install|verify|check|"
    r"test|confirm|update|edit|fix|patch|delete|remove|ask|have|use|tell|call|let|get|"
    r"initialize|init|commit|push)\b",
    re.IGNORECASE,
)
_MUTATION_STEP_RE = re.compile(
    r"^(?:create|write|build|add|update|edit|fix|patch|delete|remove)\b",
    re.IGNORECASE,
)
_RUN_STEP_RE = re.compile(r"^(?:run|execute)\b", re.IGNORECASE)

# Peer CLI names — steps mentioning these are always kept regardless of verb
_PEER_NAME_RE = re.compile(r'\b(claude|gemini|qwen)\b', re.IGNORECASE)

def filter_tool_steps(steps: List[str]) -> List[str]:
    """
    Keep only steps that correspond to real tool calls (create file, run
    command, verify output).  Drops implementation-detail steps the 0.5B
    model sometimes emits (e.g. "Count lines using os.linesep").

    Every retained step must begin with a recognised action or explicitly name
    a peer CLI. No step receives special treatment based on its position.
    """
    if not steps:
        return []
    kept = []
    for step in steps:
        if (
            _TOOL_VERBS.match(step)
            or re.search(r"\bRun:|Verify|Check\b", step, re.IGNORECASE)
            or _PEER_NAME_RE.search(step)
        ):
            kept.append(step)
    return kept


def validate_plan(prompt: str, steps: List[str]) -> List[str]:
    """Ground, deduplicate, and cap a model-generated plan."""
    if not should_plan(prompt) or not isinstance(steps, list):
        return []

    candidates = filter_tool_steps([
        re.sub(r"\s+", " ", str(step)).strip()
        for step in steps
        if str(step).strip()
    ])
    if len(candidates) < 2:
        return []

    prompt_files = set(_FILE_RE.findall(prompt))
    plan_files = {
        filename
        for step in candidates
        for filename in _FILE_RE.findall(step)
    }
    if prompt_files:
        # When the user supplies filenames, they are authoritative. Falling
        # back to direct execution is safer than executing a renamed/invented
        # planner path.
        if plan_files - prompt_files or not prompt_files.issubset(plan_files):
            return []

    allow_repeat = bool(_REPEAT_RE.search(prompt))
    result = []
    seen_exact = set()
    mutation_file_indexes = {}

    for step in candidates:
        normalized = step.rstrip(". ").casefold()
        is_run = bool(_RUN_STEP_RE.match(step))
        if normalized in seen_exact:
            if is_run and allow_repeat and len(result) < MAX_PLAN_STEPS:
                result.append(step)
            if len(result) >= MAX_PLAN_STEPS:
                break
            continue
        seen_exact.add(normalized)

        files = _FILE_RE.findall(step)
        if _MUTATION_STEP_RE.match(step) and files:
            existing_index = next(
                (mutation_file_indexes[name] for name in files if name in mutation_file_indexes),
                None,
            )
            if existing_index is not None:
                if len(step) > len(result[existing_index]):
                    result[existing_index] = step
                continue
            new_index = len(result)
            for name in files:
                mutation_file_indexes[name] = new_index

        result.append(step)
        if len(result) >= MAX_PLAN_STEPS:
            break

    return result if 2 <= len(result) <= MAX_PLAN_STEPS else []


# ── Planning via 0.5B on port 8081 (or remote when KUZA_BACKEND_P is set) ──

def _get_plan_remote(prompt: str) -> Optional[List[str]]:
    """Route planning through the active planner backend (OpenRouter or UnlimitedClaude)."""
    try:
        from utils.config import (
            PLANNER_TEMPERATURE, PLANNER_MAX_TOKENS, PLANNER_TIMEOUT_SECONDS,
            KUZA_PLANNER_BACKEND,
            OPENROUTER_PLANNER_MODEL, OPENROUTER_BASE_URL, OPENROUTER_API_KEY,
            UNLIMITEDCLAUDE_PLANNER_MODEL, UNLIMITEDCLAUDE_BASE_URL, UNLIMITEDCLAUDE_API_KEY,
        )
        from utils.logger import info, warning

        if KUZA_PLANNER_BACKEND == "unlimitedclaude":
            planner_model = UNLIMITEDCLAUDE_PLANNER_MODEL
            base_url      = UNLIMITEDCLAUDE_BASE_URL.rstrip("/")
            api_key       = UNLIMITEDCLAUDE_API_KEY
            backend_label = "unlimitedclaude"
        else:
            planner_model = OPENROUTER_PLANNER_MODEL
            base_url      = OPENROUTER_BASE_URL.rstrip("/")
            api_key       = OPENROUTER_API_KEY
            backend_label = "openrouter"

        messages = [
            {"role": "system", "content": PLANNER_PROMPT},
            {"role": "user",   "content": prompt},
        ]

        # Use the dedicated planner model and low temperature (0.2 not 0.7)
        import json as _json
        import urllib.request as _req
        payload = {
            "model": planner_model,
            "messages": messages,
            "max_tokens": PLANNER_MAX_TOKENS,
            "temperature": PLANNER_TEMPERATURE,
            "stream": False,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://github.com/kuza-v2",
            "X-Title": "Kuza-v2",
        }
        request = _req.Request(
            f"{base_url}/chat/completions",
            data=_json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with _req.urlopen(request, timeout=PLANNER_TIMEOUT_SECONDS) as resp:
                result = _json.loads(resp.read().decode("utf-8"))
            msg = result["choices"][0].get("message", {})
            # content can be null when the model returns a tool_call instead of text
            content = msg.get("content") or ""
            # Qwen3 / thinking models put output in reasoning_content when content is empty
            if not content:
                content = msg.get("reasoning_content") or ""
            # some models return text inside tool_calls[0].function.arguments
            if not content and "tool_calls" in msg:
                try:
                    content = msg["tool_calls"][0]["function"]["arguments"]
                except (KeyError, IndexError):
                    pass
            raw = content.strip()
        except Exception as e:
            warning(f"[plannd] {backend_label} plan request failed: {e}")
            return None

        if not raw:
            warning(f"[plannd] {backend_label} returned empty plan response")
            return None

        steps = validate_plan(prompt, parse_steps(raw))
        if not steps:
            warning(f"[plannd] {backend_label} response had no parseable steps. Raw: {raw[:120]}")
            return None
        info(f"[plannd] {backend_label} plan ({planner_model}): {len(steps)} steps")
        return steps
    except Exception as e:
        from utils.logger import warning
        warning(f"[plannd] remote planning failed: {e}")
        return None


def get_plan(prompt: str) -> Optional[List[str]]:
    """
    Break *prompt* into a numbered plan.

    Uses the local 0.5B on port 8081 by default.
    When KUZA_BACKEND_P (or KUZA_BACKEND) is a remote backend, routes
    there instead so the 0.5B server does not need to be running.
    """
    if not should_plan(prompt):
        return None

    try:
        from utils.config import is_remote_planner_backend
        if is_remote_planner_backend():
            return _get_plan_remote(prompt)
    except ImportError:
        pass

    try:
        from utils.config import (
            PLANNER_TEMPERATURE,
            PLANNER_MAX_TOKENS,
            PLANNER_TIMEOUT_SECONDS,
        )
        temperature = PLANNER_TEMPERATURE
        max_tokens  = PLANNER_MAX_TOKENS
        timeout = PLANNER_TIMEOUT_SECONDS
    except ImportError:
        temperature = 0.2
        max_tokens  = 320
        timeout = 45

    try:
        from utils.config import PLANND_SERVER_PORT
        port = PLANND_SERVER_PORT
    except ImportError:
        port = 8081

    payload = {
        "model": "plannd",
        "messages": [
            {"role": "system", "content": PLANNER_PROMPT},
            {"role": "user",   "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }

    url = f"http://127.0.0.1:{port}/v1/chat/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
            choices = result.get("choices", [])
            if not choices:
                return None
            raw = choices[0].get("message", {}).get("content", "").strip()
            if not raw:
                return None
            steps = validate_plan(prompt, parse_steps(raw))
            return steps if steps else None
    except Exception as e:
        print(f"[plannd] get_plan error: {e}", flush=True)
        return None
