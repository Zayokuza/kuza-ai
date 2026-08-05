import os
import shutil
from pathlib import Path

KUZA_DIR = Path(os.environ.get("KUZA_DIR", Path.home() / "kuza-v2"))
KUZA_STATE_DIR = Path(
    os.environ.get("KUZA_STATE_DIR", Path.home() / ".kuza-v2")
).expanduser()
MODEL_PATH = Path(os.environ.get(
    "KUZA_MODEL",
    Path.home() / "models" / "qwen2.5-coder-7b" / "qwen2.5-coder-7b-instruct-q4_k_m.gguf"
))

# Dedicated embedding model — Option C (v2.6.6)
# nomic-embed-text-v1.5: 80 MB Q4, 2048 ctx, 768-dim vectors.
# Runs on port 8082, separate from the 7B generation server on 8080.
# ~50 ms/chunk, covers 92.6% of chunks; rest use BM25 keyword fallback.
EMBED_MODEL_PATH = Path(os.environ.get(
    "KUZA_EMBED_MODEL",
    Path.home() / "models" / "nomic-embed" / "nomic-embed-text-v1.5.Q4_K_M.gguf"
))
EMBED_SERVER_PORT = int(os.environ.get("KUZA_EMBED_PORT", "8082"))

# Detection of llama-server binary and library path
_HOME_LLAMA = Path.home() / "llama.cpp" / "build" / "bin"
LLAMA_SERVER_BIN = os.environ.get("KUZA_LLAMA_SERVER") or shutil.which("llama-server") or str(_HOME_LLAMA / "llama-server")
LLAMA_LIB = os.environ.get("KUZA_LLAMA_LIB") or str(_HOME_LLAMA)

MODEL_CONFIG = {
    # 16K substantially reduces KV-cache pressure on a phone. Override with
    # KUZA_CTX or main.py --ctx when a task genuinely needs more context.
    "n_ctx":          max(4096, min(32768, int(os.environ.get("KUZA_CTX", "16384")))),
    "n_threads":      4,
    "n_gpu_layers":   0,
    "verbose":        False,
    "temperature":    0.7,
    "max_tokens":     2048,
    "repeat_penalty": 1.1,
    "top_p":          0.8,
    "top_k":          20,
    "batch_size":     1024,
    "kv_type":        "q4_0",
    # Stop the model before it can role-play the next user turn.
    # With /v1/chat/completions, llama-server handles ChatML stop tokens
    # automatically. These extra stops catch hallucinated role-play.
    "stop": ["<|im_end|>", "<|im_start|>", "\nUser:", "\nHuman:", "\nA:"],
}

def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    """Read and clamp an integer environment setting."""
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


# Active is goal-driven but keeps destructive-command, credential, workspace,
# and privacy boundaries. Guided restores plan confirmation and smaller budgets.
AUTONOMY_PROFILE = os.environ.get("KUZA_AUTONOMY", "active").strip().lower()
if AUTONOMY_PROFILE not in {"guided", "active"}:
    AUTONOMY_PROFILE = "active"

_profile_defaults = {
    "guided": {
        "max_steps": 12,
        "hard_max_steps": 24,
        "max_retries": 2,
        "history_turns": 8,
        "auto_execute_plans": False,
    },
    "active": {
        "max_steps": 24,
        "hard_max_steps": 48,
        "max_retries": 4,
        "history_turns": 12,
        "auto_execute_plans": True,
    },
}[AUTONOMY_PROFILE]

AGENT_CONFIG = {
    "autonomy_profile": AUTONOMY_PROFILE,
    "max_steps": _env_int(
        "KUZA_MAX_STEPS", _profile_defaults["max_steps"], 4, 64
    ),
    "hard_max_steps": _env_int(
        "KUZA_HARD_MAX_STEPS", _profile_defaults["hard_max_steps"], 8, 96
    ),
    "max_retries": _env_int(
        "KUZA_MAX_RETRIES", _profile_defaults["max_retries"], 1, 12
    ),
    "token_budget": _env_int("KUZA_TOKEN_BUDGET", 2400, 512, 8192),
    "confirm_shell": False,
    "confirm_write": False,
    "history_turns": _env_int(
        "KUZA_HISTORY_TURNS", _profile_defaults["history_turns"], 4, 24
    ),
    "project_context_chars": _env_int(
        "KUZA_PROJECT_CONTEXT_CHARS", 16000, 4000, 48000
    ),
    "sidecar_context_chars": _env_int(
        "KUZA_SIDECAR_CONTEXT_CHARS", 6000, 1200, 16000
    ),
    "auto_execute_plans": (
        os.environ.get(
            "KUZA_AUTO_EXECUTE",
            "1" if _profile_defaults["auto_execute_plans"] else "0",
        ) == "1"
    ),
    "inspect_before_write": os.environ.get(
        "KUZA_INSPECT_BEFORE_WRITE", "1"
    ) != "0",
    "require_validation": os.environ.get(
        "KUZA_REQUIRE_VALIDATION", "1"
    ) != "0",
    "share_sidecar_evidence": os.environ.get(
        "KUZA_SIDECAR_EVIDENCE", "1"
    ) != "0",
    "confirm_protected_writes": os.environ.get(
        "KUZA_CONFIRM_PROTECTED_WRITES",
        "1" if AUTONOMY_PROFILE == "guided" else "0",
    ) == "1",
    "allow_large_rewrites": os.environ.get(
        "KUZA_ALLOW_LARGE_REWRITES",
        "0" if AUTONOMY_PROFILE == "guided" else "1",
    ) == "1",
    # Optional callable(command: str) -> str that replaces the default shell()
    # invocation. Used by the daemon to enforce an allowlist without modifying
    # the global shell tool. None means use the default shell() function.
    "_shell_fn": None,
}
# Never let the soft budget exceed the hard progress cap.
AGENT_CONFIG["max_steps"] = min(
    AGENT_CONFIG["max_steps"], AGENT_CONFIG["hard_max_steps"]
)

# Thermal management + adaptive depth — Phase 8 (v2.6.8)
THERMAL_CONFIG = {
    "enabled": True,
    "warn_after_sec": 300,       # 5 minutes - log warning
    "reduce_threads_after_sec": 600,  # 10 minutes - reduce to 2 threads
    "min_threads": 2,
    "original_threads": 4,       # Will be set from MODEL_CONFIG
    # Adaptive recursion depth thresholds (tuned for Snapdragon — runs hotter)
    "temp_critical": 90,         # °C — skip recursion entirely
    "temp_warn":     75,         # °C — cap recursion depth to 1
    "batt_critical":  5,         # % — skip recursion (not charging)
    "batt_low":      15,         # % — cap recursion depth to 1 (not charging)
}

# Initialize original_threads from MODEL_CONFIG
THERMAL_CONFIG["original_threads"] = MODEL_CONFIG.get("n_threads", 4)

CODE_DIR = Path(__file__).parent.parent.resolve()
WORKSPACE_ROOT = Path(os.getcwd()).resolve()

# Recursive Inference — Phase 2 (v2.6.2)
# Controls the draft → critique → refine self-improvement loop.
# KUZA_RECURSIVE=1  — force on   (even for remote backends)
# KUZA_RECURSIVE=0  — force off  (single-pass inference)
# unset              — off (one generation per step; best default for phones)
_recursive_env     = os.environ.get("KUZA_RECURSIVE", "").strip()
_recursive_enabled = (
    True  if _recursive_env == "1" else
    False
)
RECURSIVE_CONFIG = {
    "enabled":            _recursive_enabled,
    # Max critique+refine cycles per request (1 = 1 critique + 1 refine = 3 calls total)
    # Raise for higher quality at the cost of 2x–3x inference time.
    "max_depth":          1,
    # Quality gate: skip refinement if the model rates its own output >= this × 10
    "quality_threshold":  0.7,
    # Apply recursion for file-write tasks (write_file / patch_file)
    "recursive_for_writes": True,
    # Apply recursion during task planning (orchestrator)
    "recursive_for_plans":  False,
    # Skip recursion for Q&A / conversational messages (always skipped via breadth=minimal)
    "recursive_for_qa":     False,
    # Max tokens allocated to the critique response (keeps critique calls fast)
    "critique_budget":    512,
    # Max chars of KB context injected into the refine prompt for NEED_DOCS gaps
    "retrieval_budget":   1200,
}

# Knowledge Base + Retrieval — Phase 1 (v2.6.1)
RETRIEVAL_CONFIG = {
    "enabled":            True,
    "kb_path":            str(KUZA_DIR / "knowledge"),
    "semantic_search":    True,         # prefer embeddings when index exists
    "max_chunks":         4,            # max results per retrieval query
    "budget_chars":       2400,         # max chars of retrieved content (~600 tokens)
    "embedding_model":    "all-MiniLM-L6-v2",  # legacy key (sentence-transformers era); actual model is EMBED_MODEL_PATH (nomic-embed-text-v1.5)
    "min_score":          0.0,          # minimum raw score (keyword: overlap count)
    "semantic_threshold": 0.3,          # minimum cosine similarity per chunk
    "relevance_gate":     0.72,         # min best-chunk cosine to inject anything at all
                                        # (prevents noisy general content injection when
                                        # the KB has no specifically relevant material)
}

KUZA_VERSION = "2.0.0"
KUZA_NAME = "KUZA"

# ── OpenRouter backend (optional) ────────────────────────────────────────────
# ── Remote backend selection ─────────────────────────────────────────────────
# Set KUZA_BACKEND to route inference to a remote API instead of local models.
# The embed model (port 8082) always runs locally regardless of backend.
#
# Values:
#   local           — default: all three models run on-device
#   openrouter      — OpenRouter API (openrouter.ai)
#   unlimitedclaude — UnlimitedClaude API (unlimitedclaude.com)
KUZA_BACKEND = os.environ.get("KUZA_BACKEND", "local").lower()

# Planner/summarizer backend — independent of the coder backend.
# Defaults to KUZA_BACKEND so existing setups need no change.
# Set KUZA_BACKEND_P to mix backends, e.g.:
#   export KUZA_BACKEND=openrouter        # coder → OpenRouter
#   export KUZA_BACKEND_P=unlimitedclaude # planner → UnlimitedClaude
#   export KUZA_BACKEND_P=local           # planner → local 0.5B (port 8081)
KUZA_PLANNER_BACKEND = os.environ.get("KUZA_BACKEND_P", KUZA_BACKEND).lower()

# Helpers — True for any backend that uses a remote OpenAI-compatible API
def is_remote_backend() -> bool:
    return KUZA_BACKEND in ("openrouter", "unlimitedclaude")

def is_remote_planner_backend() -> bool:
    return KUZA_PLANNER_BACKEND in ("openrouter", "unlimitedclaude")

# ── OpenRouter ────────────────────────────────────────────────────────────────
# OPENROUTER_API_KEY    — sk-or-... key from openrouter.ai/keys
# OPENROUTER_MODEL      — coding model,  e.g. "qwen/qwen-2.5-coder-7b-instruct"
# OPENROUTER_PLANNER_MODEL — planning model, e.g. "meta-llama/llama-3.2-1b-instruct:free"
OPENROUTER_API_KEY       = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL         = os.environ.get("OPENROUTER_MODEL", "qwen/qwen-2.5-coder-7b-instruct")
# For planning, default to the same model as coding.
# If you have a paid OpenRouter account, a small fast model works well:
#   export OPENROUTER_PLANNER_MODEL=meta-llama/llama-3.2-1b-instruct:free
OPENROUTER_PLANNER_MODEL = os.environ.get("OPENROUTER_PLANNER_MODEL", OPENROUTER_MODEL)
OPENROUTER_BASE_URL      = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# ── UnlimitedClaude ───────────────────────────────────────────────────────────
# UNLIMITEDCLAUDE_API_KEY     — key from unlimitedclaude.com/dashboard
# UNLIMITEDCLAUDE_MODEL       — coding model,   e.g. "claude-sonnet-4-5"
# UNLIMITEDCLAUDE_PLANNER_MODEL — planning model, e.g. "claude-haiku-4-5"
UNLIMITEDCLAUDE_API_KEY       = os.environ.get("UNLIMITEDCLAUDE_API_KEY", "")
UNLIMITEDCLAUDE_MODEL         = os.environ.get("UNLIMITEDCLAUDE_MODEL", "qwen3-coder-next")
UNLIMITEDCLAUDE_PLANNER_MODEL = os.environ.get("UNLIMITEDCLAUDE_PLANNER_MODEL", "claude-haiku-4.5")
UNLIMITEDCLAUDE_BASE_URL      = os.environ.get("UNLIMITEDCLAUDE_BASE_URL", "https://api.unlimitedclaude.com/v1")

# ── 0.5B planner/summarizer (port 8081) ───────────────────────────────────────
# Qwen2.5-0.5B runs as a dedicated planning + summarization model on port 8081,
# entirely separate from the 7B agent server on port 8080.
PLANNER_MODEL_PATH = Path(os.environ.get(
    "KUZA_PLANNER_MODEL",
    Path.home() / "models" / "qwen2.5-0.5b" / "planner-kuza.gguf"
))
SECONDARY_MODEL_PATH = PLANNER_MODEL_PATH  # legacy compatibility
PLANND_SERVER_PORT = int(os.environ.get("KUZA_PLANND_PORT", "8081"))

# ── 7B model memory-mapping settings — Change 2 ─────────────────────────────
# QWEN_7B_MMAP=True  → weights are mmap'd from disk; only touched pages load into RAM.
# QWEN_7B_MLOCK=False → OS can page weights out under memory pressure (default).
# These settings apply ONLY to the Qwen 7B model.
# The 0.5B summarizer model is unaffected.
QWEN_7B_MMAP  = os.environ.get("KUZA_7B_MMAP",  "1") != "0"   # default: True
QWEN_7B_MLOCK = os.environ.get("KUZA_7B_MLOCK", "0") != "0"   # default: False

# ── Planner settings ─────────────────────────────────────────────────────────
# Keep local plans bounded: the planner emits at most twelve one-line steps, and
# long generations multiply latency on a phone.
PLANNER_TEMPERATURE  = 0.2
PLANNER_MAX_TOKENS   = int(os.environ.get("KUZA_PLANNER_MAX_TOKENS", "480"))
PLANNER_TIMEOUT_SECONDS = max(
    5,
    min(120, int(os.environ.get("KUZA_PLANNER_TIMEOUT", "45"))),
)
