import json
import re
import time
from core.inference_v2 import infer
from core.context import build_file_context_block, auto_load_from_prompt, list_loaded
from core.project import get_project_summary
from core.kuzamd import read_kuzamd, find_kuzamd
from core.summarizer import should_summarize, summarize_history
from core.tokens import get_context_usage, usage_bar
from core.learning import get_learning_manager
from tools.file_tools import tool_read_file, tool_write_file, tool_append_file, tool_list_dir
from tools.patch_tools import tool_patch_file
from tools.shell_tools import shell, search_files
from utils.logger import tool_call, tool_result, warning, separator, info, success
from utils.config import AGENT_CONFIG, RECURSIVE_CONFIG
from core.display import show_file_write, show_patch, show_shell, show_tool_generic, show_response
from core.implementation.confirmation import PlannedAction, confirm_actions
from core.observability.logger import log_event, new_session_id
from tools.holehe_tool import tool_holehe
from tools.web_tools import web_search, read_webpage
from utils.redaction import redact_sensitive

# Learning manager for adaptive behavior
_learning = None

def _get_learning():
    """Get learning manager singleton."""
    global _learning
    if _learning is None:
        _learning = get_learning_manager()
    return _learning

def _note_save(args):
    from core.notes import add_note
    add_note(args["key"], args["value"])
    return f"Remembered: {args['key']} = {args['value']}"

def _note_forget(args):
    from core.notes import remove_note
    if remove_note(args["key"]):
        return f"Forgot: {args['key']}"
    return f"No note found for: {args['key']}"

TOOLS = {
    "read_file":    lambda args: tool_read_file(args["path"]),
    "write_file":   lambda args: tool_write_file(args["path"], args["content"]),
    "patch_file":   lambda args: tool_patch_file(args["path"], args["old_str"], args["new_str"]),
    "append_file":  lambda args: tool_append_file(args["path"], args["content"]),
    "list_dir":     lambda args: tool_list_dir(args.get("path", ".")),
    # Route through AGENT_CONFIG["_shell_fn"] when set (e.g. daemon allowlist guard).
    # Falls back to the standard shell() when no override is installed.
    "shell":        lambda args: (AGENT_CONFIG.get("_shell_fn") or shell)(args["command"]),
    "search_files": lambda args: search_files(
        args["pattern"], args.get("path", ".")
    ),
    "holehe": tool_holehe,
    "web_search": lambda args: web_search(
        args["query"], args.get("limit", 5)
    ),
    "read_webpage": lambda args: read_webpage(
        args["url"], args.get("max_chars", 12000)
    ),
    "note_save": _note_save,
    "note_forget":  _note_forget,
}
ROGUE_TAG_MAP = {
    "write_file": "write_file", "read_file": "read_file",
    "patch_file": "patch_file", "shell": "shell",
    "append_file": "append_file", "list_dir": "list_dir",
    "search_files": "search_files",
    "holehe": "holehe",
    "web_search": "web_search",
    "read_webpage": "read_webpage",
    "note_save": "note_save", "note_forget": "note_forget",
}

_EMAIL_ADDRESS_RE = re.compile(
    r"(?<![\w.+-])([A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)(?![\w.-])"
)
_EMAIL_LOOKUP_ACTION_RE = re.compile(
    r"\b(?:search|find|check|scan|lookup|look\s+up|investigate)\b",
    re.IGNORECASE,
)
_EMAIL_ACCOUNT_RE = re.compile(
    r"\b(?:accounts?|registered|registration|signed\s+up|signups?|"
    r"linked|used\s+on|holehe)\b",
    re.IGNORECASE,
)
_IMPLEMENTATION_REQUEST_RE = re.compile(
    r"\b(?:create|write|build|implement|code|develop)\b.*"
    r"\b(?:file|script|program|module|function|class|app|tool|feature|capability)\b",
    re.IGNORECASE | re.DOTALL,
)

_SEARCH_REQUEST_RE = re.compile(
    r"\b(?:find|locate|search|research|discover|look\s+up|investigate|"
    r"identify|track\s+down|gather)\b",
    re.IGNORECASE,
)
_SEARCH_BLOCKER_RE = re.compile(
    r"\b(?:i\s+can(?:not|'t)|unable\s+to|could(?:not|n't)|"
    r"no\s+access|nothing\s+found|not\s+found|cannot\s+find)\b",
    re.IGNORECASE,
)
_INSPECTION_TOOLS = {
    "read_file", "list_dir", "search_files", "web_search", "read_webpage"
}
_SEARCH_TOOLS = {"search_files", "web_search", "read_webpage"}


def _is_search_goal(message: str) -> bool:
    return bool(_SEARCH_REQUEST_RE.search(message or ""))


def _project_has_reusable_source(root=None) -> bool:
    """Cheap check used to require inspection before changing an existing repo."""
    from pathlib import Path as _Path

    base = _Path(root or ".").resolve()
    suffixes = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs"}
    ignored = {".git", "__pycache__", ".venv", "venv", "node_modules"}
    try:
        for entry in base.iterdir():
            if entry.name in ignored or entry.name.startswith("."):
                continue
            if entry.is_file() and entry.suffix in suffixes:
                return True
            if entry.is_dir():
                for child in entry.iterdir():
                    if child.is_file() and child.suffix in suffixes:
                        return True
    except OSError:
        return False
    return False


def detect_email_account_lookup(message: str):
    """Return the requested email for a direct account-registration lookup.

    The deterministic route is intentionally narrow: the message must contain
    an email address, a lookup verb, and account-registration language. Requests
    to build software are left to the normal coding workflow.
    """
    if not isinstance(message, str) or _IMPLEMENTATION_REQUEST_RE.search(message):
        return None

    match = _EMAIL_ADDRESS_RE.search(message)
    if not match:
        return None

    candidate = match.group(1)

    # A bare email entered at Kuza's prompt means: check its registrations.
    if message.strip().lower() == candidate.lower():
        return candidate

    if not _EMAIL_LOOKUP_ACTION_RE.search(message):
        return None
    if not _EMAIL_ACCOUNT_RE.search(message):
        return None

    return candidate

HALLUCINATION_MARKERS = [
    # ChatML tokens — always strip (model leaking special tokens)
    "<|im_start|>", "<|im_end|>",
    # System-prompt echo — model regurgitating its own context (## headers)
    "\n## Loaded Files", "\n## Project Memory", "\n## Current Project",
    "\n## User Notes", "\n## Project Map", "\n## User Preferences",
    "\n## Relevant Skills", "\n## Reference Material", "\n## Repo Map",
    # KUZA.md echo — model regurgitating project memory (# headers)
    "\n# Project", "\n# Stack", "\n# Structure", "\n# Commands",
    "\n# Conventions", "\n# Notes",
    # KUZA.md list items — model echoing config lines
    "\n- Code style:", "\n- Naming:", "\n- Logging:", "\n- Imports:",
    # Code leakage — model echoing source after prose (common with small models)
    "\nfrom core.", "\nfrom utils.", "\nfrom prompts.", "\nfrom tools.",
    "\nimport core.", "\nimport utils.",
]

# Subset of markers safe to use as server-side stop sequences.
# These stop llama-server generation before leakage gets streamed to stdout.
_LEAK_STOP_SEQUENCES = [
    "\n## Loaded Files", "\n## Project Memory", "\n## Current Project",
    "\n## User Notes", "\n## Project Map", "\n## User Preferences",
    "\n## Relevant Skills", "\n## Reference Material", "\n## Repo Map",
    "\n# Project", "\n# Stack", "\n# Structure", "\n# Commands",
    "\n# Conventions", "\n# Notes",
    "\n- Code style:", "\n- Naming:", "\n- Logging:", "\n- Imports:",
    "\nfrom core.", "\nfrom utils.", "\nfrom prompts.", "\nfrom tools.",
    "\nimport core.", "\nimport utils.",
]

def clean_response(text):
    for marker in HALLUCINATION_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            text = text[:idx]
    return text.strip()

def extract_json(raw):
    """
    Extract JSON from LLM output. Handles trailing commas, missing closing
    braces, literal newlines inside strings, and Python triple-quotes.
    """
    raw = raw.strip()

    # Fix Python triple-quotes → JSON strings (common 7B model error).
    # The model writes """content""" instead of a proper JSON string.
    # Handles nested docstrings inside the code content.
    def _fix_triple_quotes(s):
        # The original s.find('"""') always matched the FIRST triple-quote
        # found after the opening — which is the docstring opener inside the
        # code content, not the real closing delimiter.  Fix: scan all """
        # positions and pick the LAST one followed by } or , (JSON context),
        # so nested docstrings in the code are captured as part of the content.
        result = []
        i = 0
        while i < len(s):
            if s[i:i+3] == '"""':
                rest = s[i + 3:]
                positions = [m.start() for m in re.finditer(r'"""', rest)]
                closing_pos = -1
                for pos in reversed(positions):
                    after = rest[pos + 3:].lstrip()
                    if not after or after[0] in ',}':
                        closing_pos = pos
                        break
                if closing_pos == -1 and positions:
                    closing_pos = positions[-1]
                if closing_pos != -1:
                    inner = rest[:closing_pos]
                    i = i + 3 + closing_pos + 3
                else:
                    inner = rest
                    i = len(s)
                # Encode raw content as a proper JSON string
                inner = inner.replace('\\', '\\\\')
                inner = inner.replace('"', '\\"')
                inner = inner.replace('\n', '\\n')
                inner = inner.replace('\t', '\\t')
                inner = inner.replace('\r', '\\r')
                result.append('"' + inner + '"')
            else:
                result.append(s[i])
                i += 1
        return ''.join(result)

    if '"""' in raw:
        raw = _fix_triple_quotes(raw)

    if not raw.startswith('{'):
        # Try to find the start of a JSON block
        idx = raw.find('{')
        if idx != -1:
            raw = raw[idx:]
        else:
            return None

    # Improved depth tracking that ignores braces inside strings
    depth = 0
    in_string = False
    escape = False
    end = 0

    for i, ch in enumerate(raw):
        if escape:
            escape = False
            continue
        if ch == '\\':
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if not in_string:
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

    if end == 0:
        # If we didn't find the end, maybe the model just outputted incomplete JSON
        # Let's try to close it as a last resort
        if depth > 0:
            candidate = raw + ("}" * depth)
        else:
            return None
    else:
        candidate = raw[:end]

    # Clean candidate for common LLM artifacts: trailing commas
    cleaned = re.sub(r',\s*([}\]])', r'\1', candidate)

    # Fix literal newlines inside JSON strings (common 7B model error)
    # Replace actual newlines inside string values with \n
    def _fix_literal_newlines(s):
        result = []
        in_str = False
        esc = False
        for ch in s:
            if esc:
                result.append(ch)
                esc = False
                continue
            if ch == '\\':
                result.append(ch)
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                result.append(ch)
                continue
            if in_str and ch == '\n':
                result.append('\\n')
                continue
            result.append(ch)
        return ''.join(result)

    def _fix_unquoted_values(s):
        """Quote unquoted string values emitted by smaller models.

        Handles cases like {"path": /tmp/foo.py} or {"cmd": ls -la}
        where the model omits quotes around non-JSON-primitive values.
        """
        def _replacer(m):
            key_part = m.group(1)
            val = m.group(2).strip()
            # Leave JSON primitives alone
            if val in ('true', 'false', 'null'):
                return m.group(0)
            if re.match(r'^-?\d+\.?\d*$', val):
                return m.group(0)
            escaped = val.replace('\\', '\\\\').replace('"', '\\"')
            return key_part + '"' + escaped + '"'
        # Match ": unquoted_value  where value is not already quoted/object/array
        return re.sub(
            r'(":\s*)([^",\{\[\s][^,\}]*?)(?=\s*[,\}])',
            _replacer,
            s,
        )

    # Try raw candidate first, then cleaned, then newline-fixed, then unquoted-fixed
    for s in [candidate, cleaned, _fix_literal_newlines(cleaned),
              _fix_unquoted_values(cleaned)]:
        try:
            return json.loads(s)
        except (json.JSONDecodeError, ValueError):
            pass

    return None

def parse_tool_call(text):
    # ── Primary: JSON format in <tool> tags ──────────────────────────
    match = re.search(r"<tool>\s*(\{.*)", text, re.DOTALL)
    if match:
        result = extract_json(match.group(1))
        if result and "name" in result:
            return result
    # Also accept Markdown fenced tool calls:
    # ```tool
    # {"name": "shell", "args": {"command": "pwd"}}
    # ```
    match = re.search(
        r"```tool\s*(\{.*?\})\s*```",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if match:
        result = extract_json(match.group(1))
        if result and "name" in result:
            return result

    # Rogue tags: <write_file>{json}</write_file> etc.
    for tag, canonical in ROGUE_TAG_MAP.items():
        match = re.search(r"<" + tag + r">\s*(\{.*)", text, re.DOTALL)
        if match:
            inner = extract_json(match.group(1))
            if inner:
                if "name" in inner:
                    return inner
                return {"name": canonical, "args": inner}

    # ── Fallback: block-style tags (no JSON escaping) ────────────────
    # <write_file path="...">...code...</write_file>
    m = re.search(r'<write_file\s+path="([^"]+)">\s*\n?(.*?)(?:</write_file>|\Z)', text, re.DOTALL)
    if m and m.group(2).strip():
        return {"name": "write_file", "args": {"path": m.group(1), "content": m.group(2).strip()}}

    return None

def _format_tool_for_history(tool_dict):
    """Format a tool call for conversation history."""
    return "<tool>\n" + json.dumps(tool_dict) + "\n</tool>"


def execute_tool(tool_dict):
    """
    Execute a tool call with learning integration.
    
    Learns from:
    - File operations (for preference learning)
    - Errors (for error database)
    - Strategy effectiveness (for adaptive recovery)
    """
    name = tool_dict.get("name", "")
    args = tool_dict.get("args", {})
    learning = _get_learning()
    
    if name not in TOOLS:
        return "[ERROR] Unknown tool: " + name
    
    start_time = time.time()
    session_id = new_session_id()

    log_event(
        "tool_start",
        session_id=session_id,
        tool=name,
        arguments=args,
    )
    
    try:
        # For write_file: read old content for diff display BEFORE the write,
        # show the display panel immediately after, then release old_content.
        # This avoids holding both old and new content for the entire function.
        _is_write = name == "write_file"
        _is_patch = name == "patch_file"
        old_content = None
        if _is_write:
            from pathlib import Path as _P
            p = _P(args.get("path", ""))
            if p.exists():
                try: old_content = p.read_text()
                except: pass

        if name in ("write_file", "patch_file", "append_file"):
            path = args.get("path", "<unknown>")

            if name == "write_file":
                op = "Create or overwrite file"
            elif name == "patch_file":
                op = "Patch existing file"
            else:
                op = "Append to existing file"

            actions = [
                PlannedAction(
                    title=name.upper(),
                    details=[
                        f"Target: {path}",
                        f"Operation: {op}",
                        "Validation: compile/lint after write",
                    ],
                )
            ]

            if not confirm_actions(actions):
                return "[CANCELLED] User declined the requested file modification."

        result = TOOLS[name](args)
        duration = time.time() - start_time

        # Display IMMEDIATELY after write — then release old_content so GC
        # can reclaim it before linting/learning/memory-loading pile on.
        try:
            if _is_write:
                show_file_write(args.get("path",""), args.get("content",""), old_content)
                del old_content  # release ~10-50KB before next steps
            elif _is_patch:
                show_patch(args.get("path",""), args.get("old_str",""), args.get("new_str",""))
            elif name == "shell":
                is_err = is_error(result, "shell")
                show_shell(args.get("command",""), result, error=is_err)
            elif name != "read_file":
                show_tool_generic(name, args, result)
        except Exception:
            pass  # display failure must not mask a successful tool result
        old_content = None  # ensure released even if display was skipped

        # ── Post-write lint (replaces pre-write syntax check + post-write lint)
        # Single pass — the linter reads from disk (no extra content copy).
        if (_is_write or _is_patch) and not result.startswith("[ERROR]"):
            _lpath = args.get("path", "")
            if _lpath.endswith(".py"):
                try:
                    from core.linter import run_linter, format_issues
                    _issues, _linter_used = run_linter(_lpath)
                    if _issues:
                        _errors   = [i for i in _issues if i.severity == "error"]
                        _warnings = [i for i in _issues if i.severity != "error"]
                        if _errors:
                            result += format_issues(_errors)
                        if _warnings:
                            from utils.logger import warning as _lwarn
                            _lwarn(f"[Linter/{_linter_used}] {len(_warnings)} style warning(s) in {_lpath}:")
                            for _w in _warnings[:5]:
                                _lwarn(f"  Line {_w.line}: [{_w.code}] {_w.message}")
                            if len(_warnings) > 5:
                                _lwarn(f"  ... and {len(_warnings) - 5} more (run /review for full list)")
                except Exception:
                    pass  # linter unavailable — continue normally

        # Log successful actions to episodic memory (lightweight — just a string)
        if (_is_write or _is_patch or name == "shell") and not result.startswith("[ERROR]"):
            from core.memory_v2 import memory as _mem
            _mem.log_action(name, redact_sensitive(result[:100]))

        # NOTE: learning.learn_from_file is NOT called here — it's called once
        # in the agent loop after execute_tool returns, avoiding a duplicate pass.

        log_event(
            "tool_end",
            session_id=session_id,
            tool=name,
            elapsed_seconds=round(duration, 3),
            success=not result.startswith("[ERROR]"),
        )

        return result
        
    except Exception as e:
        duration = time.time() - start_time
        error_msg = str(e)
        safe_error_msg = redact_sensitive(error_msg)
        
        # Learn from errors
        error_type = type(e).__name__
        learning.record_error(error_type, safe_error_msg, {
            "tool": name,
            "arg_keys": sorted(str(key) for key in args),
        })
        
        log_event(
            "tool_exception",
            session_id=session_id,
            tool=name,
            elapsed_seconds=round(duration, 3),
            success=False,
            error_type=error_type,
            error=safe_error_msg,
        )

        return "[ERROR] " + error_msg

def is_error(result, tool_name):
    if not isinstance(result, str):
        return False
    result_lower = result.lower()
    if "[cancelled]" in result_lower:
        return False
    # All tools: treat [ERROR] prefix as an error
    if result.startswith("[ERROR]"):
        return True
    # Shell-specific: detect Python tracebacks and command failures
    if tool_name == "shell":
        error_signals = [
            "traceback", "syntaxerror", "nameerror", "typeerror",
            "importerror", "modulenotfounderror", "indentationerror",
            "attributeerror", "valueerror", "filenotfounderror",
            "permissionerror", "error:", "exception:", "failed",
            "command not found", "no such file",
        ]
        return any(s in result_lower for s in error_signals)
    return False

_FILE_CHANGE_WORDS = (
    "create", "write", "make", "build", "implement", "modify", "add",
    "edit", "fix", "delete", "remove", "update", "patch", "refactor",
    "generate", "rewrite", "deploy", "setup", "configure", "replace",
    "rename", "swap", "convert", "change", "append", "insert", "move",
    "copy",
)

_NEGATED_FILE_CHANGE_PATTERNS = (
    r"\b(?:do\s+not|don't|dont|never)\s+"
    r"(?:modify|edit|change|write|create|patch|append|delete|remove|rename|move)\b",
    r"\bwithout\s+"
    r"(?:modifying|editing|changing|writing|creating|patching|appending|"
    r"deleting|removing|renaming|moving)\b",
    r"\bno\s+(?:file\s+)?(?:modifications?|edits?|changes?|writes?)\b",
    r"\bread[- ]only\b",
)


def _file_change_intent(message):
    """Return (file_change_requested, explicitly_read_only)."""
    remaining = re.sub(r"\bmake\s+sure\b", " ", message.lower())
    read_only_marker = False

    for pattern in _NEGATED_FILE_CHANGE_PATTERNS:
        remaining, count = re.subn(pattern, " ", remaining)
        read_only_marker = read_only_marker or count > 0

    needs_file = any(
        re.search(r"\b" + re.escape(word) + r"\b", remaining)
        for word in _FILE_CHANGE_WORDS
    )
    return needs_file, read_only_marker and not needs_file


def is_read_only_request(message):
    """Return True when the user explicitly forbids file changes."""
    return _file_change_intent(message)[1]


def _is_safe_read_only_shell_command(command):
    """Allow only single, non-mutating inspection commands."""
    import shlex

    if not isinstance(command, str):
        return False

    command = command.strip()
    if not command or "\n" in command or "$(" in command or "`" in command:
        return False

    try:
        lexer = shlex.shlex(
            command,
            posix=True,
            punctuation_chars=";&|<>",
        )
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return False

    if not tokens:
        return False

    blocked_operators = {
        ";", "&", "&&", "|", "||", ">", ">>", "<", "<<",
    }
    if any(token in blocked_operators for token in tokens):
        return False

    command_name = tokens[0]

    if command_name == "git":
        return (
            len(tokens) > 1
            and tokens[1] in {
                "grep", "status", "diff", "log",
                "show", "ls-files", "rev-parse",
            }
        )

    if command_name == "find":
        blocked_find_actions = {
            "-delete", "-exec", "-execdir", "-ok", "-okdir",
            "-fprint", "-fprintf",
        }
        return not any(token in blocked_find_actions for token in tokens)

    if command_name == "sed":
        return (
            any(token == "-n" or token.startswith("-n") for token in tokens[1:])
            and not any(token.startswith("-i") for token in tokens[1:])
        )

    return command_name in {
        "grep", "rg", "ls", "pwd", "cat", "head", "tail",
        "wc", "stat", "file", "du", "which", "type",
    }


def _extract_direct_safe_read_only_shell_command(message):
    """Return a literal safe inspection command entered at the prompt."""
    if not isinstance(message, str):
        return ""

    command = message.strip()
    if not command or "\n" in command:
        return ""

    if not _is_safe_read_only_shell_command(command):
        return ""

    return command


def _extract_safe_read_only_shell_block(response):
    """Extract one safe command from a fenced shell block."""
    match = re.search(
        r"```(?:shell|bash|sh)\s*\n(.*?)```",
        response,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""

    command = match.group(1).strip()
    if not _is_safe_read_only_shell_command(command):
        return ""
    return command


def _ground_read_only_response(response, last_tool_result):
    """Prevent negative inspection claims that contradict tool output."""
    evidence_text = (last_tool_result or "").strip()
    empty_evidence_markers = {
        "",
        "[no output]",
        "(no output)",
        "no output",
        "[ok]",
        "ok",
    }
    negative_evidence_claim = re.search(
        r"\b(?:no|zero)\s+"
        r"(?:[A-Za-z0-9_.-]+\s+){0,2}"
        r"(?:matches|references|results|occurrences|instances|findings)\b"
        r"|\b(?:did not|didn't|could not|couldn't)\s+find(?:\s+any)?\b"
        r"|\bfound\s+(?:no|zero)\b"
        r"|\bnothing\s+(?:was\s+)?found\b",
        response,
        re.IGNORECASE,
    )
    if (
        negative_evidence_claim
        and evidence_text.lower() not in empty_evidence_markers
    ):
        return (
            "The inspection produced the following evidence:\n"
            + evidence_text
        )
    return response


def is_hallucination(response, user_message, tools_used):
    """
    Detect obvious claims that work was completed without the required tool.

    Returns:
        Tuple of (false_file, false_run) booleans
    """
    msg_lower = user_message.lower()
    resp_lower = response.lower()

    needs_file, _ = _file_change_intent(user_message)
    needs_run = any(k in msg_lower for k in ["run", "execute", "test"])
    inspection_verbs = (
        "search", "find", "locate", "inspect", "review", "audit",
        "examine", "analyze", "analyse", "check", "verify", "list",
    )
    inspection_scopes = (
        "repository", "repo", "project", "codebase",
        "file", "files", "directory", "folder",
    )
    needs_inspection = (
        any(
            re.search(r"\b" + re.escape(word) + r"\b", msg_lower)
            for word in inspection_verbs
        )
        and any(
            re.search(r"\b" + re.escape(scope) + r"\b", msg_lower)
            for scope in inspection_scopes
        )
    )

    file_done = any(
        "write_file" in str(tool) or "patch_file" in str(tool)
        for tool in tools_used
    )
    shell_done = any("shell" in str(tool) for tool in tools_used)
    inspection_done = any(
        any(name in str(tool) for name in ("shell", "read_file", "list_dir"))
        for tool in tools_used
    )

    file_claims = [
        "has been created",
        "have created",
        "i created",
        "i've created",
        "successfully created",
        "has been successfully created",
        "has been written",
        "have written",
        "i wrote",
        "i have written",
        "i modified",
        "already implemented",
    ]

    run_claims = [
        "i ran",
        "i ran the",
        "ran successfully",
        "executed successfully",
        "tests passed",
        "tests and they passed",
    ]

    false_file = (
        needs_file
        and not file_done
        and any(claim in resp_lower for claim in file_claims)
    )

    if needs_file and not file_done and "```" in response:
        false_file = True

    false_run = (
        (
            needs_run
            and not shell_done
            and any(claim in resp_lower for claim in run_claims)
        )
        or (needs_inspection and not inspection_done)
    )

    return false_file, false_run

def build_system_prompt(message=""):
    """
    Alias for build_recursive_prompt(phase="draft") — kept for compatibility.
    All new call sites should use build_recursive_prompt() directly.
    """
    from prompts.layered_prompt import build_recursive_prompt
    return build_recursive_prompt(message, phase="draft")

def enrich_message(user_message):
    loaded = list_loaded()
    if not loaded:
        return user_message
    fix_keywords = [
        "fix", "correct", "bug", "wrong", "error", "broken",
        "update", "change", "edit", "modify", "patch",
        "replace", "rename", "swap", "convert", "append",
        "insert", "move", "add", "remove", "delete",
    ]
    if any(kw in user_message.lower() for kw in fix_keywords):
        return (
            user_message + "\n\n"
            "Files loaded: " + ", ".join(loaded) + ". "
            "Prefer patch_file for small edits. Use write_file only for new files or full rewrites."
        )
    return user_message

def check_git_and_offer_commit(user_message, tools_used):
    if is_read_only_request(user_message):
        return
    if not tools_used:
        return
    # Only offer if write_file or patch_file was used
    if not any("write_file" in s or "patch_file" in s for s in tools_used):
        return
        
    from core.githelper import is_git_repo, git_status, git_commit
    from utils.logger import confirm as ask_confirm, info, success, error
    
    if not is_git_repo():
        return
        
    status = git_status()
    if status == "Nothing to commit.":
        return
        
    info("\nChanges detected. Reviewing git status...")
    print(status)
    if ask_confirm("\nStage all and commit these changes?"):
        # Simple heuristic for commit message from user request
        msg = f"Kuza: {user_message[:50]}..."
        res = git_commit(msg)
        if res.startswith("[ERROR]"):
            error(res)
        else:
            success(f"Committed: {msg}")

def _extract_peer_output_from_history(history: list, peer_name: str) -> str:
    """
    Scan conversation history backwards for the most recent output from a named
    peer CLI.  Returns the content string if found, or "" if not found.

    Matches the format produced by PeerCLIManager.summarize_result():
      "[Peer CLI — {peer_name}]\nTask: ...\nOutput:\n..."

    Fallback: if not in history (e.g. session resumed after compression),
    reads {peer_name}_design.md from the current working directory.
    """
    prefix = f"[Peer CLI — {peer_name.lower()}]"
    for msg in reversed(history):
        if msg.get("role") == "assistant":
            content = msg.get("content", "")
            if content.lower().startswith(prefix):
                return content
    # Disk fallback — design tasks write raw output here for cross-step durability
    import os as _os
    _design_path = _os.path.join(_os.getcwd(), f"{peer_name.lower()}_design.md")
    if _os.path.exists(_design_path):
        try:
            with open(_design_path, "r", encoding="utf-8") as _df:
                _content = _df.read().strip()
            if _content:
                info(f"[peer] Loaded {peer_name} design from {peer_name.lower()}_design.md (history fallback)")
                return _content
        except Exception:
            pass
    return ""


def _detect_peer_delegation(user_message: str):
    """
    Detect phrases like:
      "ask gemini to X"   "have claude do X"   "call qwen and X"
      "use gemini to X"   "tell claude to X"   "let qwen X"
      "get claude to X"

    Returns (peer_name, task_string) or (None, None).
    """
    _PEER_NAMES = ["claude", "gemini", "qwen"]
    _pattern = re.compile(
        r'\b(?:ask|call|have|tell|use|get|let)\s+('
        + '|'.join(_PEER_NAMES)
        + r')\s+(?:to\s+|and\s+|do\s+|to\s+do\s+|to\s+help\s+with\s+)?(.+)',
        re.IGNORECASE,
    )
    m = _pattern.search(user_message)
    if m:
        return m.group(1).lower(), m.group(2).strip()

    # Also match direct-name patterns: "gemini, X" / "qwen: X" / "claude - X"
    _direct = re.compile(
        r'^('
        + '|'.join(_PEER_NAMES)
        + r')[\s,:\-]+(.+)',
        re.IGNORECASE,
    )
    m2 = _direct.match(user_message.strip())
    if m2:
        return m2.group(1).lower(), m2.group(2).strip()

    return None, None


def _auto_apply_peer_code(peer_output, context_message=""):
    """
    Extract code blocks from peer CLI output and write them to disk.

    Primary pattern — filename header before a code block:
      **`app.py`** — description        **app.py**        `app.py`:
      ```python
      code...
      ```

    Fallback pattern — bare triple-backtick block with no filename header.
    When no filename is found in the block, the expected filename is inferred
    from `context_message` (the original user request).  Only the first
    qualifying bare block is used to avoid writing ambiguous files.

    Returns list of filenames written, or empty list if none found.
    """
    import os
    files_written = []
    _CODE_EXTS = ('.py', '.js', '.ts', '.html', '.css', '.json')

    def _safe_write(fname, code):
        """Syntax-check (Python only), then write via safety layer."""
        if fname.endswith('.py'):
            try:
                from core.linter import check_syntax
                if check_syntax(code.rstrip(), fname):
                    return False  # syntax error — skip
            except Exception:
                pass
        fpath = os.path.join(os.getcwd(), fname)
        result = tool_write_file(fpath, code.rstrip() + '\n')
        if result.startswith("[ERROR]") or result.startswith("[CANCELLED]"):
            warning(f"Failed to write {fname} from peer: {result}")
            return False
        files_written.append(fname)
        success(f"Written {fname} from peer review ({len(code)} chars)")
        return True

    # ── Primary: filename header immediately before a fenced code block ──────
    _block_re = re.compile(
        r'(?:\*{1,2}`?(\w[\w.\-]*\.\w+)`?\*{0,2}|`(\w[\w.\-]*\.\w+)`:?)'
        r'\s*(?:—[^\n]*)?\s*\n'
        r'```(?:\w+)?\n(.*?)```',
        re.DOTALL,
    )
    for m in _block_re.finditer(peer_output):
        fname = m.group(1) or m.group(2)
        code = m.group(3)
        if not fname or not code or len(code.strip()) < 50:
            continue
        if not any(fname.endswith(ext) for ext in _CODE_EXTS):
            continue
        _safe_write(fname, code)

    # ── Secondary: fuzzy heading patterns (### File: x.py / ## x.py / File: x.py)
    if not files_written:
        _fuzzy_re = re.compile(
            r'(?:#{1,4}\s+(?:[Ff]ile:\s*)?|[Ff]ile:\s*)([\w][\w.\-/]*\.\w+)'
            r'[^\n]*\n'
            r'```(?:\w+)?\n(.*?)```',
            re.DOTALL,
        )
        for m in _fuzzy_re.finditer(peer_output):
            fname = os.path.basename(m.group(1))
            code = m.group(2)
            if not fname or not code or len(code.strip()) < 50:
                continue
            if not any(fname.endswith(ext) for ext in _CODE_EXTS):
                continue
            _safe_write(fname, code)

    # ── Fallback: bare fenced blocks — infer filename from context ────────────
    # Only runs when the primary pass wrote nothing.
    if not files_written:
        _expected_fname = None
        if context_message:
            _fname_re = re.compile(
                r'\b([\w][\w\-]*\.(?:py|js|ts|html|css|json))\b'
            )
            _m = _fname_re.search(context_message)
            if _m:
                _expected_fname = _m.group(1)

        if _expected_fname and any(_expected_fname.endswith(ext) for ext in _CODE_EXTS):
            _bare_re = re.compile(
                r'```(?:python|py|javascript|js|typescript|ts|html|css|json)?\n(.*?)```',
                re.DOTALL,
            )
            for m in _bare_re.finditer(peer_output):
                code = m.group(1)
                if not code or len(code.strip()) < 50:
                    continue
                if _safe_write(_expected_fname, code):
                    break  # first qualifying block only

    return files_written


def run_agent(user_message, history, yolo=False, use_plan=False, no_plan=False, _in_subtask=False, _plan_rag_block=""):
    # Reset streaming flag at start of each agent turn
    import core.inference_v2 as _inf_mod
    _inf_mod._last_was_streamed = False

    # Learn preferences from natural language in the user's message
    _get_learning().learn_from_message(user_message)

    # Main/sidecar shared evidence. Static repository analysis starts early so
    # Python can map reusable code while the main model handles the goal.
    _sidecar = None
    _sidecar_sequence = 0
    _preflight_job_id = None
    if AGENT_CONFIG.get("share_sidecar_evidence", True):
        try:
            from core.sidecar.manager import get_sidecar
            _sidecar = get_sidecar()
            _sidecar.publish_main(
                "goal",
                user_message,
                details={"cwd": __import__("os").getcwd()},
            )
            if _IMPLEMENTATION_REQUEST_RE.search(user_message):
                from core.implementation.analyzer import analyze_repository_async
                _preflight_job_id = analyze_repository_async(
                    ".",
                    goal=user_message,
                )
        except Exception:
            _sidecar = None

    # Email-registration lookups already have a purpose-built local tool. Route
    # them deterministically so a coding-focused model cannot invent search.py
    # or claim that comparing a hardcoded string searched online accounts.
    _lookup_email = detect_email_account_lookup(user_message)
    if _lookup_email and not _in_subtask:
        info(f"Checking account registrations for {_lookup_email} with Holehe...")
        _result = execute_tool({
            "name": "holehe",
            "args": {"email": _lookup_email, "only_used": True},
        })
        _summary = _result.strip()
        if _summary.lower().startswith("holehe error:"):
            _summary = "[INCOMPLETE] " + _summary
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": _summary})
        return _summary, history

    # Fast path for literal safe inspection commands entered directly at the
    # prompt. Execute them deterministically instead of asking the model to
    # interpret the command and potentially fabricate its output.
    _direct_shell_command = _extract_direct_safe_read_only_shell_command(
        user_message
    )
    if _direct_shell_command and not _in_subtask:
        _result = execute_tool({
            "name": "shell",
            "args": {"command": _direct_shell_command},
        })
        _summary = _result.strip() or "[No output]"
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": _summary})
        return _summary, history

    # Fast path for explicit shell requests. Avoid an expensive model call when
    # the user already supplied the exact command to execute.
    _shell_match = re.search(
        r"(?:use the shell tool to execute|use shell to execute|execute):\s*(.+)",
        user_message,
        re.IGNORECASE,
    )
    if _shell_match and not _in_subtask:
        _command = _shell_match.group(1).splitlines()[0].strip()
        _result = execute_tool({
            "name": "shell",
            "args": {"command": _command},
        })
        _summary = (
            f"Command executed:\n{_command}\n\n"
            f"Output:\n{_result.strip()}"
        )
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": _summary})
        return _summary, history

    # ── Explicit peer delegation ──────────────────────────────────────────────
    # Handle: "ask gemini to X", "have claude do X", etc.
    # The peer runs, its output is injected as context, then the agent applies it.
    if not _in_subtask:
        _peer_name, _peer_task = _detect_peer_delegation(user_message)
        if _peer_name and _peer_task:
            from core.peer_cli import get_peer_cli_manager
            _mgr = get_peer_cli_manager()
            _by_name = {c.name: c for c in _mgr.available()}
            if _peer_name in _by_name:
                _cli = _by_name[_peer_name]

                # For review/check/verify tasks, build rich context with current file contents
                # so the peer actually has something to review.
                _REVIEW_KW = {
                    "check", "review", "verify", "test", "examine",
                    "correct", "validate", "look at", "is it right", "did i"
                }
                _is_review = any(k in _peer_task.lower() for k in _REVIEW_KW)

                # ── Design-only phase detection ───────────────────────────────
                # When the peer is asked to "design / plan / spec / outline"
                # without any implement/build/code verb, we use prose instructions
                # and save the output as a .md design document instead of trying
                # to extract code blocks — which would corrupt the pipeline.
                _STRONG_DESIGN = re.compile(
                    r'\b(design|plan|spec(?:ify)?|specification|outline|blueprint|'
                    r'architecture|feature\s+list|roadmap|requirements?)\b',
                    re.IGNORECASE,
                )
                _STRONG_IMPLEMENT = re.compile(
                    r'\b(implement|build|code|develop|program|write\s+(?:code|it|the)|'
                    r'make\s+it\s+work|working\s+version)\b',
                    re.IGNORECASE,
                )
                _is_design_only = (
                    not _is_review  # review tasks always use code output format
                    and bool(_STRONG_DESIGN.search(_peer_task))
                    and not bool(_STRONG_IMPLEMENT.search(_peer_task))
                )

                # Output format instructions — Kuza extracts code blocks to write files.
                # Claude -p returns plain text; without explicit format instructions it
                # asks for permission or returns prose instead of extractable code.
                _FORMAT_INSTRUCTIONS = (
                    "\n\nOUTPUT FORMAT (required — Kuza will parse this automatically):\n"
                    "You are responding to an automated system. Do NOT ask for permission.\n"
                    "Do NOT ask clarifying questions. Act immediately.\n"
                    "For each file to create or modify, output it using this exact format:\n\n"
                    "**`filename.py`**\n"
                    "```python\n"
                    "# complete file content here\n"
                    "```\n\n"
                    "Use the correct language tag for non-Python files (javascript, json, etc.).\n"
                    "Write COMPLETE file content — no stubs, no placeholders, no '...'.\n"
                    "Kuza will write these files to disk automatically."
                )
                # Design tasks: ask for prose, NOT code blocks.
                # Code blocks in design output are misinterpreted by _auto_apply_peer_code.
                _DESIGN_INSTRUCTIONS = (
                    "\n\nOUTPUT FORMAT:\n"
                    "You are responding to an automated system. Write a clear, detailed design "
                    "specification in prose and markdown.\n"
                    "Do NOT write any code. Do NOT include code blocks (no triple backticks).\n"
                    "For data structures or schemas, describe them in plain text or markdown "
                    "tables — NOT code blocks.\n"
                    "Describe: features, CLI commands and their arguments, data model, "
                    "behavior, edge cases, and any constraints.\n"
                    "Your output will be saved as a design document for another AI to implement from."
                )

                _enriched_task = (
                    f"Task: {_peer_task}"
                    + (_DESIGN_INSTRUCTIONS if _is_design_only else _FORMAT_INSTRUCTIONS)
                )

                # ── Multi-peer output passing ─────────────────────────────────
                # Only relevant for implementation steps that reference a prior peer's design.
                # Design steps (step 1 of a pipeline) never have a prior peer to inject.
                if not _is_design_only:
                    _OTHER_PEERS = [p for p in ["claude", "gemini", "qwen"] if p != _peer_name]
                    _referenced_peer = None
                    for _op in _OTHER_PEERS:
                        if _op in _peer_task.lower():
                            _referenced_peer = _op
                            break
                    # Also catch implicit references ("the previous design", "what was planned")
                    if not _referenced_peer:
                        _IMPLICIT_REF = re.compile(
                            r'\b(previous\s+(?:design|plan|output|step|result)|'
                            r'what\s+was\s+(?:designed|planned|created)|'
                            r'the\s+(?:design|plan|spec|feature\s+list)\s+(?:above|from\s+before|provided))\b',
                            re.IGNORECASE,
                        )
                        if _IMPLICIT_REF.search(_peer_task):
                            # Take the most recent peer output of any other peer
                            for _op in _OTHER_PEERS:
                                _candidate = _extract_peer_output_from_history(history, _op)
                                if _candidate:
                                    _referenced_peer = _op
                                    break
                    if _referenced_peer:
                        _prior_output = _extract_peer_output_from_history(history, _referenced_peer)
                        if _prior_output:
                            info(f"Injecting {_referenced_peer}'s previous output into {_peer_name}'s context")
                            _enriched_task = (
                                f"Task: {_peer_task}\n\n"
                                f"Context from {_referenced_peer.capitalize()}'s previous output "
                                f"(use this as your specification):\n\n"
                                f"{_prior_output}\n\n"
                                f"Your task: {_peer_task}"
                                + _FORMAT_INSTRUCTIONS
                            )

                if _is_review:
                    # Data-privacy gate: warn before sending local file contents
                    # to an external AI service. This is explicit and opt-in.
                    from utils.logger import warning as _priv_warn, confirm as _priv_confirm
                    _priv_warn(
                        f"Sending project files to {_peer_name} (external AI). "
                        "Local source code will leave this device."
                    )
                    _include_files = _priv_confirm(
                        f"Share local project file contents with {_peer_name}?"
                    )
                    if not _include_files:
                        # Use task only — no file contents sent externally
                        _is_review = False
                if _is_review:
                    from pathlib import Path as _PP
                    _file_parts = []
                    for _f in sorted(_PP.cwd().iterdir()):
                        if _f.is_file() and _f.suffix in ('.py', '.js', '.ts', '.txt', '.md', '.json'):
                            try:
                                _fc = _f.read_text(encoding='utf-8', errors='replace')
                                if len(_fc) < 4000:
                                    _file_parts.append(f"=== {_f.name} ===\n{_fc}")
                            except Exception:
                                pass
                    if _file_parts:
                        # Use the current user message as the task — do NOT override
                        # from history.  A resumed session may have old messages that
                        # would replace the current task with an unrelated one.
                        _orig_goal = user_message
                        _enriched_task = (
                            f"Task: {_orig_goal}\n\n"
                            "Current project files for context:\n\n"
                            + "\n\n".join(_file_parts[:6])
                            + f"\n\nYou must: {_peer_task}"
                            + _FORMAT_INSTRUCTIONS
                        )

                info(f"Delegating to {_cli.description}: {_peer_task[:80]}")
                _output = _mgr.call(_cli, _enriched_task)
                if _mgr.is_peer_error(_output):
                    warning(f"Peer '{_peer_name}' unavailable — falling back to local inference.")
                    # Fall through to normal agent inference below
                elif _output and len(_output.strip()) > 10:
                    _summary = _mgr.summarize_result(_cli.name, _output, _peer_task)
                    # Store peer exchange in history so context is preserved
                    history.append({"role": "user", "content": user_message})
                    history.append({"role": "assistant", "content": _summary})

                    # ── Design-only: save prose output to {peer}_design.md ────
                    # For design tasks we skip _auto_apply_peer_code entirely —
                    # code-block extraction would corrupt a markdown spec that
                    # contains data-structure examples in backtick blocks.
                    if _is_design_only:
                        import os as _os
                        _design_fname = f"{_peer_name}_design.md"
                        _design_path = _os.path.join(_os.getcwd(), _design_fname)
                        try:
                            with open(_design_path, "w", encoding="utf-8") as _dfile:
                                _dfile.write(_output)
                            success(f"Design saved to {_design_fname} ({len(_output)} chars)")
                        except Exception as _de:
                            warning(f"Could not save design file: {_de}")
                        # Plan will handle the next step (implement from design)
                        return _summary, history

                    # Auto-extract and write code blocks from peer output.
                    # The 7B local model struggles to parse large peer responses,
                    # so we extract ```python blocks with filenames and write them directly.
                    _files_written = _auto_apply_peer_code(_output, user_message)

                    if _files_written:
                        from utils.logger import success as _suc
                        _suc(f"[Peer: {_peer_name}] done. Applied {len(_files_written)} file(s): {', '.join(_files_written)}")
                        # Run tests if the peer provided test files AND the original task
                        # asked for them to be run.  Do this directly (no model inference)
                        # so the step cannot be skipped by a weak or confused model.
                        _has_tests = any('test' in f.lower() for f in _files_written)
                        _run_requested = any(k in user_message.lower() for k in [
                            "run", "show", "result", "execute", "test it",
                        ])
                        # When running as a plan step (no_plan=True), the plan itself
                        # has a dedicated "Run:" follow-up step — don't spawn another one.
                        if no_plan:
                            return _summary, history
                        if _has_tests and _run_requested:
                            _test_file = next(f for f in _files_written if 'test' in f.lower())
                            _cmd = f"python -m pytest {_test_file} -v"
                            info(f"Running tests: {_cmd}")
                            _test_result = shell(_cmd, yolo=yolo)
                            show_shell(_cmd, _test_result)
                            history.append({"role": "user", "content": f"Run: {_cmd}"})
                            history.append({"role": "assistant", "content": _test_result})
                            return _test_result, history
                        elif _has_tests:
                            # Tests written but not explicitly requested — ask model
                            _test_file = next(f for f in _files_written if 'test' in f.lower())
                            _follow_up = f"Run: python -m pytest {_test_file} -v"
                            return run_agent(_follow_up, history, yolo=yolo, _in_subtask=True)
                        else:
                            _follow_up = (
                                f"Original task: {user_message}\n\n"
                                f"Files written from peer review: {', '.join(_files_written)}. "
                                "Verify ALL requirements from the original task are met. "
                                "Summarize what was done in 2-3 sentences."
                            )
                            return run_agent(_follow_up, history, yolo=yolo, _in_subtask=True)
                    else:
                        # No code blocks found — fall back to asking agent to interpret
                        _follow_up = (
                            f"Original task: {user_message}\n\n"
                            f"The peer CLI {_peer_name} responded:\n\n"
                            f"{_output[:1500]}\n\n"
                            "Apply any code fixes the peer identified using write_file. "
                            "Then verify ALL requirements from the original task are met — "
                            "if anything is missing, implement it now. "
                            "Summarize what was done in 2-3 sentences."
                        )
                        from utils.logger import success as _suc
                        _suc(f"[Peer: {_peer_name}] done. Applying result...")
                        return run_agent(_follow_up, history, yolo=yolo, _in_subtask=True)
                else:
                    warning(f"Peer '{_peer_name}' returned no output. Continuing locally.")
            else:
                warning(
                    f"Peer '{_peer_name}' not available "
                    f"({', '.join(_by_name) or 'none installed'}). Continuing locally."
                )

    from prompts.layered_prompt import build_recursive_prompt
    used, total = get_context_usage([{"role": "system", "content": build_recursive_prompt("")}])
    if used < total * 0.5:
        auto_load_from_prompt(user_message)
    enriched = enrich_message(user_message)
    # Orchestrator — complex tasks get broken into subtask queue
    from core.orchestrator import is_complex, plan_tasks, run_queue
    from core.display import show_task_plan, console
    if is_complex(user_message) and not _in_subtask and not no_plan:
        info("Planning subtasks...")
        queue = plan_tasks(user_message, read_kuzamd())
        if queue.tasks:
            show_task_plan(queue)
            if AGENT_CONFIG.get("auto_execute_plans", False):
                info("Active autonomy: executing the verified plan.")
            else:
                try:
                    ans = console.input("  Execute this plan? [Y/n]: ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    ans = "n"
                if ans in ("n", "no"):
                    return "[Cancelled]", history
            run_queue(queue, yolo=yolo)
            _failed = [t for t in queue.tasks if t.status == 'failed']
            _audit_passed = getattr(queue, 'completion_audit_passed', False)

            if _audit_passed:
                summary = f"Completed {queue.done_count()}/{len(queue.tasks)} tasks."
            else:
                summary = (
                    f"[INCOMPLETE] Completed {queue.done_count()}/"
                    f"{len(queue.tasks)} tasks, but final verification failed."
                )

            if _failed:
                summary += " Failed: " + "; ".join(
                    t.description[:50] for t in _failed
                ) + "."

            _states = getattr(queue, "new_save_states", [])
            if _states:
                _state_lines = []
                for _state in _states[:8]:
                    _files = ", ".join(_state.files[:4]) or "[no files]"
                    if len(_state.files) > 4:
                        _files += f", +{len(_state.files) - 4} more"
                    _state_lines.append(
                        f"- {_state.save_state_id}: {_files} ({_state.reason})"
                    )
                summary += "\n\nSave states created:\n" + "\n".join(_state_lines)

            _evidence = getattr(queue, "execution_evidence", [])
            _evidence_lines = []
            for _item in _evidence:
                _detail = (_item.get("error") or _item.get("result") or "").strip()
                if not _detail:
                    continue
                if len(_detail) > 600:
                    _detail = _detail[-600:]
                _evidence_lines.append(
                    f"- Step {_item.get('id')} [{_item.get('status')}]: "
                    f"{_item.get('description', '')[:90]}\n  {_detail}"
                )
            if _evidence_lines:
                summary += (
                    "\n\nExecution and test evidence:\n"
                    + "\n".join(_evidence_lines[-8:])
                )

            try:
                _get_learning().record_task_outcome(
                    user_message,
                    [item.get("description", "") for item in _evidence],
                    success=_audit_passed,
                    validation=[
                        (item.get("error") or item.get("result") or "")
                        for item in _evidence
                        if item.get("error") or item.get("result")
                    ],
                )
            except Exception:
                pass

            history.append({"role": "user",     "content": user_message})
            history.append({"role": "assistant", "content": summary})
            return summary, history

    if use_plan:
        from core.planner import get_plan, show_and_confirm_plan
        from core.repo_context import RepositoryContext

        info("Generating plan...")
        repo_context = RepositoryContext.collect(".").to_prompt()
        planning_context = f"{read_kuzamd()}\n\n{repo_context}"
        plan = get_plan(user_message, planning_context)
        approved, enriched = show_and_confirm_plan(plan)
        if not approved:
            return "[Cancelled]", history
    # Tick memory manager — evicts stale files, advances turn counter
    from core.memory_v2 import memory as _mem
    _mem.tick()
    # ── Phase 3: Layered system prompt (draft phase) ──────────────────────────
    sys_prompt = build_recursive_prompt(user_message, phase="draft", plan_rag_block=_plan_rag_block)
    messages = [{"role": "system", "content": sys_prompt}]

    # Make persisted experience actionable rather than write-only telemetry.
    try:
        _experience_context = _get_learning().format_experience_context(
            user_message,
            limit=3,
        )
        if _experience_context:
            messages.append({
                "role": "system",
                "content": _experience_context,
            })
    except Exception:
        pass

    # Adaptive context management — only compress when context > 75% of n_ctx
    # Build a temporary full messages array for accurate token measurement
    _tmp_msgs = messages + history + [{"role": "user", "content": user_message}]
    if should_summarize(history, system_messages=_tmp_msgs):
        history = summarize_history(history)
        # NOTE: _mem.compress_summary() was removed here — it calls infer()
        # on the same 7B model that's about to run the real task, causing
        # a single-slot collision. The 0.5B summarize_history() is sufficient.
    # Rebuild messages with potentially compressed history
    messages = [{"role": "system", "content": sys_prompt}]
    
    # Pre-inference guide: if it's a question or conversation, tell it NOT to use tools
    msg_low = user_message.lower().strip()
    _read_only_mode = is_read_only_request(user_message)
    _action_kws = [
        "create", "write", "make", "build", "edit", "fix", "run", "execute",
        "install", "add", "delete", "remove", "update", "patch", "refactor",
        "implement", "generate", "rewrite", "deploy", "setup", "configure",
        "review", "analyze", "analyse", "audit", "examine", "inspect", "assess", "find", "locate", "search", "research", "discover", "lookup",
        "read", "look at", "show me", "check",
        # Previously missing — caused QA false-positives for real edit requests:
        "replace", "rename", "swap", "convert", "change", "append", "insert",
        "move", "copy", "print", "output", "display", "open",
        # Memory triggers — should use note_save/note_forget tools:
        "remember", "don't forget", "forget",
        # Peer delegation triggers — "ask gemini to X" should never be QA:
        "ask gemini", "ask claude", "call gemini", "call claude",
        # Planner step verbs — daemon steps like "verify the output" must use shell,
        # not return plain text answers:
        "verify", "test", "validate", "confirm", "complete", "finish",
    ]
    _has_action = any(re.search(r'\b' + re.escape(k) + r'\b', msg_low) for k in _action_kws)
    _question_starters = (
        "what", "why", "how", "when", "where", "who", "which",
        "is ", "are ", "do ", "does ", "can ", "could ", "would ",
        "should ", "will ", "was ", "were ", "has ", "have ",
    )
    _qa_phrases = [
        "tell me", "tell me about", "explain", "help me understand",
        "what can you", "hello", "hi", "hey", "thanks", "thank you",
    ]
    _explicit_tool_request = any(phrase in msg_low for phrase in (
        "use the shell tool", "use shell", "run the shell tool",
        "use the tool", "run a command", "execute a command",
    ))
    is_qa = not _has_action and not _explicit_tool_request and (
        msg_low.endswith("?") or
        msg_low.startswith(_question_starters) or
        any(re.search(r'\b' + re.escape(k) + r'\b', msg_low) for k in _qa_phrases)
    )
    if is_qa:
        messages.append({"role": "user", "content": "IMPORTANT: This is a question or conversation. Respond with plain text only. DO NOT use any tools. Keep your response concise — 2-3 sentences max unless more detail is needed."})

    if _read_only_mode:
        messages.append({
            "role": "user",
            "content": (
                "IMPORTANT: This task is explicitly read-only. Never create, "
                "modify, append, delete, stage, or commit files. You must gather "
                "real evidence using read_file, list_dir, or a read-only shell "
                "command before reporting results. Never claim that no matches "
                "exist without tool evidence."
            ),
        })

    keep = AGENT_CONFIG["history_turns"] * 2
    messages.extend(history[-keep:] if len(history) > keep else history)
    messages.append({"role": "user", "content": enriched})
    if _sidecar is not None:
        try:
            _shared, _sidecar_sequence = _sidecar.shared_context(
                since_sequence=_sidecar_sequence,
                limit=12,
                max_chars=AGENT_CONFIG.get("sidecar_context_chars", 6000),
            )
            if _shared:
                messages.append({"role": "user", "content": _shared})
        except Exception:
            pass
    step = 0
    max_steps = AGENT_CONFIG["max_steps"]
    tools_used = []
    last_tool_result = ""
    last_failed_attempt = None
    duplicate_count = 0
    hallucination_count = 0
    auto_retries = 0
    max_retries = AGENT_CONFIG.get("max_retries", 4)
    hard_max_steps = max(
        max_steps,
        AGENT_CONFIG.get("hard_max_steps", max_steps),
    )
    error_log = []        # accumulates error text for peer CLI context
    files_touched = []    # accumulates file paths for peer CLI context
    validation_evidence = []
    inspection_done = False
    search_attempts = 0
    search_goal = _is_search_goal(user_message)
    require_inspection = (
        AGENT_CONFIG.get("inspect_before_write", True)
        and bool(_IMPLEMENTATION_REQUEST_RE.search(user_message))
        and _project_has_reusable_source()
    )
    # Subtasks writing large files need more steps than simple Q&A.
    # If running inside the orchestrator (in_subtask) and the message contains
    # code-generation signals, raise the cap to 10.
    if _in_subtask:
        _complex_signals = ["overall goal", "write", "implement", "create", "build", "api", "server"]
        if any(s in user_message.lower() for s in _complex_signals):
            max_steps = max(max_steps, 10)

    while step < max_steps:
        step += 1
        used, total = get_context_usage(messages)
        pct = used / total if total > 0 else 0.0
        if pct > 0.85:
            warning("Context: " + usage_bar(used, total))
        else:
            info("Context: " + usage_bar(used, total))
        if _sidecar is not None:
            try:
                _shared, _sidecar_sequence = _sidecar.shared_context(
                    since_sequence=_sidecar_sequence,
                    limit=8,
                    max_chars=max(
                        1200,
                        AGENT_CONFIG.get("sidecar_context_chars", 6000) // 2,
                    ),
                )
                if _shared:
                    messages.append({"role": "user", "content": _shared})
            except Exception:
                pass
        # ── Phase 2: Recursive inference on first step for non-QA tasks ─────────
        # Subsequent steps (reacting to tool results) use regular infer — recursion
        # is only valuable when generating the initial response/tool call.
        # Wrapped in try/except — recursive failure must never break the agent loop.
        _use_recursive = (
            step == 1
            and not is_qa
            and not _explicit_tool_request
            and not _read_only_mode
            and RECURSIVE_CONFIG.get("enabled", True)
        )
        _stop = ["</tool>"] + _LEAK_STOP_SEQUENCES
        _qa_max_tokens = 512 if is_qa else None
        if _use_recursive:
            try:
                from core.recursive import recursive_infer, classify_breadth_need
                _breadth = classify_breadth_need(user_message)
                if _breadth == "minimal":
                    response = infer(messages, stream=True,
                                     extra_stop=_stop, show_thinking=True,
                                     max_tokens=_qa_max_tokens)
                else:
                    _depth = 2 if _breadth == "deep" else 1
                    response = recursive_infer(
                        messages,
                        task_type="code",
                        user_message=user_message,
                        max_depth=_depth,
                        extra_stop=_stop,
                        stream=True,
                    )
            except Exception:
                # Recursive inference unavailable — fall back to plain infer
                response = infer(messages, stream=True,
                                 extra_stop=_stop, show_thinking=True,
                                 max_tokens=_qa_max_tokens)
        else:
            response = infer(messages, stream=True, extra_stop=_stop,
                             show_thinking=True, max_tokens=_qa_max_tokens)
        response = clean_response(response)
        tool_dict = parse_tool_call(response)

        # Small local models sometimes return a fenced shell command instead
        # of the required tool-call envelope. Rescue only commands proven to
        # be read-only; all other shell blocks remain ordinary text.
        if not tool_dict and _read_only_mode:
            rescued_command = _extract_safe_read_only_shell_block(response)
            if rescued_command:
                info(
                    "Recovered safe read-only shell command from response."
                )
                tool_dict = {
                    "name": "shell",
                    "args": {"command": rescued_command},
                }

        # ── Malformed tool call: <tool> tag present but JSON failed to parse ──
        # The model tried to call a tool but emitted invalid JSON (e.g. missing
        # quote: {"name": patch_file"}).  Surface this as an explicit retry so
        # the model sees the error — without this it silently falls through to
        # the no-tool-call path and the step is skipped.
        if not tool_dict and "<tool>" in response and auto_retries < max_retries:
            auto_retries += 1
            warning("Malformed tool call — JSON parse failed, retrying")
            messages.append({"role": "assistant", "content": response})
            messages.append({
                "role": "user",
                "content": (
                    "Your tool call had invalid JSON (e.g. a missing quote or bracket). "
                    "Fix the syntax and output ONLY a corrected <tool>...</tool> block.\n"
                    "Example: <tool>\n"
                    "{\"name\": \"write_file\", \"args\": {\"path\": \"x.py\", \"content\": \"code\"}}\n"
                    "</tool>"
                ),
            })
            continue

        # ── Recursive code-rescue: if recursive_infer produced good code as
        # prose (no tool call), extract the code block and synthesize write_file
        # directly — never ask the model again (prevents "YOUR CODE" placeholder).
        # Only fires on step 1 (_use_recursive), only for create-file requests,
        # only when a filename is present in the message and code is in the response.
        if not tool_dict and _use_recursive and not is_qa and not _read_only_mode:
            _create_kws = ["create", "write", "make", "build", "generate", "implement"]
            if any(k in user_message.lower() for k in _create_kws):
                _fname_m = re.search(
                    r'\b([\w][\w\-]*\.(?:py|js|ts|html|css|json|sh|txt|md))\b',
                    user_message,
                )
                _code_m = re.search(
                    r'```(?:python|py|js|ts|bash|sh|json|html|css)?\n(.*?)```',
                    response, re.DOTALL,
                )
                if _fname_m and _code_m:
                    _extracted = _code_m.group(1).rstrip()
                    if len(_extracted) > 30:
                        tool_dict = {
                            "name": "write_file",
                            "args": {"path": _fname_m.group(1), "content": _extracted},
                        }

        if tool_dict:
            name = tool_dict.get("name", "")
            args = tool_dict.get("args", {})

            if (
                require_inspection
                and not inspection_done
                and name in {"write_file", "patch_file", "append_file"}
            ):
                messages.append({
                    "role": "assistant",
                    "content": _format_tool_for_history(tool_dict),
                })
                messages.append({
                    "role": "user",
                    "content": (
                        "Before changing an existing project, inspect reusable "
                        "code with search_files, read_file, or list_dir. Find the "
                        "closest existing implementation, then adapt it instead "
                        "of duplicating it."
                    ),
                })
                continue
            
            # SANITY CHECK: prevent hallucinated tool usage
            if is_qa and name not in ["read_file", "list_dir", "note_save", "note_forget"]:
                 warning(f"Model tried to use '{name}' for a general question.")
                 messages.append({"role": "assistant", "content": response})
                 messages.append({"role": "user", "content": "Just answer my question directly with text. No tools needed. Final answer format: 'I can help with [tasks].'"})
                 continue
            
            # Read-only mode also protects direct shell tool calls.
            if _read_only_mode and name == "shell":
                command = args.get("command", "")
                if not _is_safe_read_only_shell_command(command):
                    warning(
                        "Blocked mutating or unsupported shell command "
                        "during read-only task."
                    )
                    messages.append({
                        "role": "assistant",
                        "content": _format_tool_for_history(tool_dict),
                    })
                    messages.append({
                        "role": "user",
                        "content": (
                            "The original task is read-only. Use one safe "
                            "inspection command such as git grep, grep, rg, "
                            "ls, find without actions, or sed -n."
                        ),
                    })
                    continue

            # Explicit read-only requests may inspect, but never modify files.
            if _read_only_mode and name in [
                "write_file", "patch_file", "append_file", "delete_file"
            ]:
                path = args.get("path", "") or "(unknown path)"
                warning(
                    f"Blocked '{name}' during read-only task: {path}"
                )
                messages.append({
                    "role": "assistant",
                    "content": _format_tool_for_history(tool_dict),
                })
                messages.append({
                    "role": "user",
                    "content": (
                        "The original request is explicitly read-only. "
                        "Do not create, modify, append, or delete files. "
                        "Use read_file, list_dir, or a read-only shell command "
                        "to gather evidence, then report exact results."
                    ),
                })
                continue

            # Allow supporting files required by implementation tasks.
            # Only block surprise writes when the original request was classified
            # as ordinary Q&A rather than an action.
            if name in ["write_file", "patch_file", "append_file"] and is_qa:
                path = args.get("path", "")
                if path:
                    warning(f"Model tried to modify a file during Q&A: {path}")
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": (
                            "This was classified as a question, so do not modify files. "
                            "Answer directly with text."
                        ),
                    })
                    continue

            sig = name + ":" + json.dumps(args, sort_keys=True)
            if sig in tools_used:
                duplicate_count += 1
                if duplicate_count >= 2:
                    messages.append({
                        "role": "assistant",
                        "content": _format_tool_for_history(tool_dict),
                    })
                    messages.append({
                        "role": "user",
                        "content": (
                            "That exact action was already executed. Do not "
                            "repeat it or claim completion from repetition. Use "
                            "a different search source, implementation strategy, "
                            "or validation test that adds new evidence."
                        ),
                    })
                    continue
                messages.append({"role": "assistant", "content": _format_tool_for_history(tool_dict)})
                messages.append({"role": "user", "content": "Already ran that. Use a different action that adds new evidence."})
                continue
            tools_used.append(sig)
            last_tool_result = execute_tool(tool_dict)

            if name in _INSPECTION_TOOLS:
                inspection_done = True
            if name in _SEARCH_TOOLS:
                search_attempts += 1
            if name == "shell":
                command_text = str(args.get("command", "")).lower()
                if any(marker in command_text for marker in (
                    "pytest", "unittest", "py_compile", "compileall",
                    "npm test", "cargo test", "go test",
                )):
                    validation_evidence.append(last_tool_result[:1200])

            # Deterministic validation is a hard completion gate for supported
            # mutations; it does not depend on the model remembering to test.
            if (
                name in {"write_file", "patch_file", "append_file"}
                and not is_error(last_tool_result, name)
                and AGENT_CONFIG.get("require_validation", True)
            ):
                changed_path = args.get("path", "")
                try:
                    from core.validation import validate_changed_paths
                    validation_results = validate_changed_paths([changed_path])
                    for validation in validation_results:
                        summary = validation.summary()
                        validation_evidence.append(summary)
                        if validation.passed:
                            last_tool_result += "\n" + summary
                        else:
                            last_tool_result = (
                                "[ERROR] Post-change validation failed.\n"
                                + last_tool_result
                                + "\n"
                                + summary
                            )
                            break
                except Exception as validation_error:
                    last_tool_result = (
                        "[ERROR] Validation could not run after the change: "
                        + str(validation_error)
                    )

            if _sidecar is not None:
                try:
                    _sidecar.publish_main(
                        "tool_result",
                        f"{name}: {last_tool_result[:800]}",
                        details={"path": args.get("path", "")},
                    )
                except Exception:
                    pass

            # Continue while each step produces new evidence, up to the hard cap.
            if (
                not is_error(last_tool_result, name)
                and step >= max_steps - 1
                and max_steps < hard_max_steps
            ):
                max_steps = min(hard_max_steps, max_steps + 4)
                info(f"Progress detected — extending task budget to {max_steps} steps.")

            if name in ("write_file", "patch_file"):
                from core.memory_v2 import memory as _mem
                fpath = args.get("path", "")
                # Load into working memory directly from args — avoids re-reading
                # from disk (the content is already in args["content"]).
                _wcontent = args.get("content", "") or args.get("new_str", "")
                if _wcontent and fpath:
                    _mem.load_file(fpath, _wcontent)
                _mem.touch_file(fpath)
                # Learn preferences (single call — removed duplicate from execute_tool)
                if fpath.endswith(".py") and _wcontent:
                    try:
                        _get_learning().learn_from_file(fpath, _wcontent)
                    except Exception:
                        pass
                del _wcontent  # release content ref
            elif name == "read_file":
                fpath = args.get("path", "")
                if fpath.endswith(".py") and not last_tool_result.startswith("[ERROR]"):
                    try:
                        _get_learning().learn_from_file(fpath, last_tool_result)
                    except Exception:
                        pass
            if is_error(last_tool_result, name):
                error_log.append(last_tool_result[:300])
                last_failed_attempt = {
                    "strategy": name,
                    "error_type": f"{name}_error",
                    "error_message": last_tool_result[:1000],
                    "args": dict(args),
                }
            fpath_touched = args.get("path", "")
            if fpath_touched and fpath_touched not in files_touched:
                files_touched.append(fpath_touched)

            if is_error(last_tool_result, name) and auto_retries < max_retries:
                auto_retries += 1
                warning("Error detected — auto-retry " + str(auto_retries) + "/" + str(max_retries))
                messages.append({"role": "assistant", "content": _format_tool_for_history(tool_dict)})
                # If the user cancelled the command (declined confirmation), suggest
                # write_file as an alternative when the task is about creating a file.
                if name == "shell" and "[CANCELLED]" in last_tool_result:
                    _file_words = ["create", "write", "make", "build", "file", ".py", ".js", ".html", ".txt", ".md"]
                    if any(w in msg_low for w in _file_words):
                        messages.append({"role": "user", "content": "Command was not run. Use the write_file tool instead to create the file. Output ONLY a <tool> block with write_file."})
                        continue
                # FIX 3: argparse / usage errors mean the command was called wrong,
                # not that the source code is broken.  Tell the model to re-run with
                # correct arguments instead of patching working files.
                if name == "shell":
                    _res_low = last_tool_result.lower()
                    _USAGE_SIGNALS = [
                        "usage:",
                        "error: the following arguments are required",
                        "unrecognized arguments",
                    ]
                    if any(sig in _res_low for sig in _USAGE_SIGNALS):
                        messages.append({
                            "role": "user",
                            "content": (
                                "The command failed because it was called incorrectly. "
                                "Run it again with the correct arguments based on the original task. "
                                "Do not modify any files."
                            ),
                        })
                        continue
                messages.append({"role": "user", "content": "Error:\n" + last_tool_result[:400] + "\n\nFix the error and try again."})
                continue
            elif is_error(last_tool_result, name) and auto_retries >= max_retries and not _in_subtask:
                # Exhausted retries — offer to escalate to a peer CLI
                from core.peer_cli import escalate
                peer_result = escalate(user_message, error_log, files_touched)
                if peer_result and peer_result.startswith("[redirect]:"):
                    # User told Kuza to try a different approach
                    new_instruction = peer_result[len("[redirect]: "):]
                    messages.append({"role": "user", "content": new_instruction})
                    auto_retries = 0
                    continue
                elif peer_result:
                    # Peer CLI ran — inject its output and let Kuza act on it
                    messages.append({"role": "assistant", "content": _format_tool_for_history(tool_dict)})
                    messages.append({"role": "user", "content": peer_result + "\n\nBased on the above, complete the task or summarize what was accomplished."})
                    auto_retries = 0
                    continue
                # else: user skipped escalation, fall through to normal handling
            if last_failed_attempt and not is_error(last_tool_result, name):
                try:
                    _get_learning().learn_from_error_and_fix(
                        error_type=last_failed_attempt["error_type"],
                        error_message=last_failed_attempt["error_message"],
                        fix=f"{name}: {last_tool_result[:500]}",
                        success=True,
                        strategy=name,
                        context={
                            "failed_strategy": last_failed_attempt["strategy"],
                            "failed_args": last_failed_attempt["args"],
                            "successful_args": dict(args),
                        },
                    )
                    last_failed_attempt = None
                except Exception:
                    pass

            messages.append({"role": "assistant", "content": _format_tool_for_history(tool_dict)})
            # Give the model real evidence, including save-state and deterministic
            # validation output, before it selects the next action or reports.
            messages.append({
                "role": "user",
                "content": (
                    "Tool result: " + last_tool_result[:2400]
                    + "\nUse a different next action if more evidence is needed; "
                    "otherwise report the save state, changes, and validation."
                ),
            })
            continue

        # A find/search goal may not terminate on an unsupported assertion.
        # Force an evidence-producing search, then alternate source/query/tool.
        if search_goal:
            if search_attempts == 0:
                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": (
                        "This is a search goal and no search evidence exists yet. "
                        "Use search_files for local code or web_search for external "
                        "information, then open/read the strongest result."
                    ),
                })
                continue
            if (
                _SEARCH_BLOCKER_RE.search(response)
                and search_attempts < max_retries
            ):
                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": (
                        "Do not stop at the first failed search. Change the query, "
                        "use a different source/tool, inspect related terms, and "
                        "collect concrete evidence before concluding."
                    ),
                })
                continue

        false_file, false_run = is_hallucination(
            response, user_message, tools_used
        )
        if (false_file or false_run) and hallucination_count == 0:
            hallucination_count += 1
            messages.append({"role": "assistant", "content": response})

            if false_file:
                fname_match = re.search(r"(\w+\.py)", user_message)
                fname = fname_match.group(1) if fname_match else "output.py"
                tool_hint = (
                    '<tool>\n{"name": "write_file", "args": '
                    '{"path": "' + fname + '", "content": "YOUR CODE"}}'
                    '\n</tool>'
                )
                correction_message = (
                    "You must call write_file before claiming the file exists. "
                    "Output ONLY a tool call:\n" + tool_hint
                )
            else:
                correction_message = (
                    "You must gather real evidence before answering. Call shell "
                    "now with a concrete read-only command that verifies the "
                    "request. Output ONLY the actual tool call, with no "
                    "placeholder and no final answer yet."
                )

            messages.append({
                "role": "user",
                "content": correction_message,
            })
            continue
        # Never accept a useless generic final response after a tool ran.
        # Preserve the actual tool evidence so Kuza does not hide results
        # behind replies such as "Done."
        _generic_final = response.strip().lower().rstrip(".!")
        if last_tool_result and _generic_final in {"done", "completed", "finished", "success"}:
            response = (
                "Command/tool completed successfully.\n"
                "Result:\n" + last_tool_result.strip()
            )

        # Ground read-only conclusions in the actual tool output.
        if _read_only_mode:
            response = _ground_read_only_response(
                response, last_tool_result
            )

        if validation_evidence and "validation" not in response.lower():
            response += (
                "\n\nValidation evidence:\n"
                + "\n".join(validation_evidence[-2:])
            )
        try:
            _get_learning().record_task_outcome(
                user_message,
                tools_used,
                success=not response.lstrip().startswith("[INCOMPLETE]"),
                validation=validation_evidence,
            )
        except Exception:
            pass
        history.append({"role": "user",     "content": user_message})
        history.append({"role": "assistant", "content": response})
        if not _in_subtask:
            check_git_and_offer_commit(user_message, tools_used)
        return response, history
    warning("Reached max steps (" + str(max_steps) + ").")
    if not _in_subtask:
        check_git_and_offer_commit(user_message, tools_used)
    # Return a failure marker so run_queue() can flag this subtask as incomplete
    # instead of silently marking it done. The last tool result is included so
    # the next subtask knows what was attempted.
    _incomplete_msg = "[INCOMPLETE] Max steps reached."
    if last_tool_result and not last_tool_result.startswith("["):
        _incomplete_msg += " Last result: " + last_tool_result[:200]
    try:
        _get_learning().record_task_outcome(
            user_message,
            tools_used,
            success=False,
            validation=validation_evidence,
        )
    except Exception:
        pass
    return _incomplete_msg, history
