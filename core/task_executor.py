#!/usr/bin/env python3
"""
Task executor for Kuza-v2 daemon.

Executes queued tasks using the full run_agent() pipeline — the same code
path used by the interactive CLI.  Both modes are now identical in capability:
layered prompts, RAG, recursive self-critique, linting, hallucination detection.

Daemon-specific behaviour is injected via AGENT_CONFIG overrides:
- confirm_write / confirm_shell set to False  (no interactive prompts)
- _shell_fn replaced with _daemon_shell       (allowlist-only execution)
These are restored after each task so they never bleed into interactive sessions.
"""

import asyncio
import os
from typing import Optional, Dict

from utils.logger import info, warning, error, success
from utils.config import AGENT_CONFIG
from core.state import StateStore
from core.daemon_config import DaemonConfig
from core.thermal import start_inference, end_inference


class IncompleteTaskError(RuntimeError):
    """Raised when the agent exhausts its budget without verified completion."""


# ---------------------------------------------------------------------------
# Daemon shell allowlist
# Commands the daemon may run without user confirmation.
# Extend this list when new safe operations are needed.
#
# Rationale for each prefix:
#   python / python3  — run scripts / test files the agent just wrote
#   pip / pip3        — install/check packages the agent determines are missing
#   pytest            — run the test suite as part of TDD/fix loops
#   node/npm/cargo/go  — common build, check, and test workflows
#   ruff/mypy/black/flake8 — local static analysis
#   ls / cat / echo   — read-only inspection of files and directories
#   grep              — search file contents; read-only
#   find              — locate files; read-only (daemon never passes -delete)
#   git status/log/diff/show — read-only git introspection; no write ops
#                      (git commit/push/reset are intentionally excluded)
#   cd                — change working directory for subsequent commands
#   pwd / which / env / printenv — environment introspection; read-only
#
# Security note: "python" and "pip" are broad prefixes — a malformed task
# could use them to run arbitrary code.  This is acceptable in daemon mode
# because the agent already has full filesystem access; the allowlist mainly
# prevents accidental destructive shell commands (rm, curl, chmod, etc.).
# ---------------------------------------------------------------------------
_DAEMON_ALLOWED_COMMANDS = {
    "python", "python3", "pytest", "pip", "pip3",
    "node", "npm", "cargo", "go",
    "ruff", "mypy", "black", "flake8",
    "ls", "cat", "echo", "grep", "find", "pwd", "which", "printenv",
}
_DAEMON_GIT_SUBCOMMANDS = {"status", "log", "diff", "show"}
_DAEMON_SUBCOMMANDS = {
    "pip": {"install", "check", "list", "show", "freeze", "download"},
    "pip3": {"install", "check", "list", "show", "freeze", "download"},
    "npm": {"test", "run", "install", "ci", "list", "view"},
    "cargo": {"test", "check", "build", "fmt", "clippy"},
    "go": {"test", "build", "vet", "fmt", "list"},
}
_DAEMON_NEVER_ALLOW = {
    "rm", "rmdir", "dd", "mkfs", "mount", "umount", "reboot",
    "shutdown", "su", "sudo",
}


def _daemon_command_allowed(command: str) -> bool:
    """Return whether a simple daemon command is in the narrow allowlist."""
    import shlex
    from pathlib import Path
    from tools.shell_tools import validate_command_structure

    valid, _ = validate_command_structure(command)
    if not valid:
        return False
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    if not argv:
        return False

    executable = Path(argv[0]).name
    if executable in _DAEMON_NEVER_ALLOW:
        return False
    if executable == "git":
        return len(argv) >= 2 and argv[1] in _DAEMON_GIT_SUBCOMMANDS

    configured = {
        item.strip()
        for item in os.environ.get("KUZA_DAEMON_ALLOW", "").split(",")
        if item.strip()
    }
    if executable not in _DAEMON_ALLOWED_COMMANDS and executable not in configured:
        return False

    allowed_subcommands = _DAEMON_SUBCOMMANDS.get(executable)
    if allowed_subcommands is not None:
        if len(argv) < 2 or argv[1] not in allowed_subcommands:
            return False

    if executable == "node" and any(
        flag in argv for flag in ("-e", "--eval", "-p", "--print")
    ):
        return False
    if executable == "find" and any(
        flag in argv for flag in ("-delete", "-exec", "-execdir", "-ok", "-okdir")
    ):
        return False
    if executable in {"python", "python3"} and "-c" in argv:
        return False
    return True


class TaskExecutor:
    """
    Executes tasks from the daemon's task queue using the full agent pipeline.

    Each task is run via run_agent() in a thread executor so the asyncio event
    loop stays responsive during blocking inference calls.  AGENT_CONFIG is
    temporarily patched to suppress interactive prompts and enforce the daemon
    shell allowlist, then restored unconditionally in a finally block.
    """

    def __init__(self, state: StateStore, config: DaemonConfig):
        self.state = state
        self.config = config
        self.current_task: Optional[Dict] = None

    # ------------------------------------------------------------------
    # Core execution — delegates to the full run_agent() pipeline
    # ------------------------------------------------------------------

    async def _execute_task(self, prompt: str, task_id: int | None = None) -> str:
        """
        Execute a single task using the full run_agent() pipeline.

        Installs a daemon shell guard and disables interactive confirmations
        for the duration of the call, then unconditionally restores the
        previous AGENT_CONFIG values so interactive sessions are unaffected.

        The prompt should already be the enriched step string produced by
        daemon._handle_command (includes original task + step number).
        """
        start_inference()
        try:
            from core.agent import run_agent
            from prompts.layered_prompt import invalidate_prompt_cache

            # Fresh file context for every step — previous steps may have
            # written files that must appear in this step's system prompt.
            invalidate_prompt_cache()

            # Clear working memory between daemon steps — previous step's
            # files and turn counter bleed into this step causing stale
            # context and premature LRU eviction of files we haven't seen yet.
            from core.memory_v2 import memory as _mem
            _mem.clear()

            # Save and override AGENT_CONFIG for daemon execution.
            _saved = {
                "_shell_fn":     AGENT_CONFIG.get("_shell_fn"),
                "confirm_shell": AGENT_CONFIG.get("confirm_shell"),
                "confirm_write": AGENT_CONFIG.get("confirm_write"),
                "execution_mode": AGENT_CONFIG.get("execution_mode"),
                "_cancel_check": AGENT_CONFIG.get("_cancel_check"),
                "_yolo": AGENT_CONFIG.get("_yolo"),
            }
            self.current_task = {"id": task_id, "prompt": prompt}
            AGENT_CONFIG["_shell_fn"] = self._daemon_shell
            AGENT_CONFIG["confirm_shell"] = False
            AGENT_CONFIG["confirm_write"] = False
            AGENT_CONFIG["execution_mode"] = "daemon"
            AGENT_CONFIG["_yolo"] = True
            AGENT_CONFIG["_cancel_check"] = ((lambda: self.state.is_task_cancelled(task_id)) if task_id is not None else None)

            try:
                loop = asyncio.get_event_loop()
                response, _ = await loop.run_in_executor(
                    None,
                    lambda: run_agent(
                        prompt,
                        history=[],
                        yolo=True,
                        no_plan=True,      # each daemon step is already planned
                        _in_subtask=True,  # suppress git prompts; scale max_steps
                    ),
                )
                if task_id is not None and self.state.is_task_cancelled(task_id):
                    return "[CANCELLED] Task cancelled by user."
                if response.lstrip().startswith("[INCOMPLETE]"):
                    raise IncompleteTaskError(response)
                return response
            finally:
                self.current_task = None
                AGENT_CONFIG.update(_saved)

        except Exception as e:
            import traceback
            error(f"Task execution error: {e}\n{traceback.format_exc()}")
            raise  # re-raise original exception; already logged above
        finally:
            end_inference()

    # ------------------------------------------------------------------
    # Daemon shell guard
    # ------------------------------------------------------------------

    def _daemon_shell(self, command: str) -> str:
        """
        Execute a shell command in daemon context.

        Only prefixes listed in _DAEMON_ALLOWED_PREFIXES are permitted.
        Anything else is blocked with a clear message so the model knows
        to restructure its approach rather than silently failing.
        """
        from tools.shell_tools import shell, validate_command_structure

        cmd = command.strip()
        from core.action_policy import cancellation_requested
        if cancellation_requested():
            return "[CANCELLED] Task cancelled by user."
        valid, reason = validate_command_structure(cmd)
        if not valid:
            warning(f"Daemon: blocked compound shell command: {cmd[:80]}")
            return f"[BLOCKED] Daemon shell requires a simple command: {reason}"
        if not _daemon_command_allowed(cmd):
            warning(f"Daemon: blocked shell command: {cmd[:80]}")
            return (
                f"[BLOCKED] Daemon mode will not run '{cmd[:60]}' without "
                "explicit authorization. Add the exact command policy to "
                "core/task_executor.py to enable it."
            )
        return shell(command, yolo=True)

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------

    def get_current_task(self) -> Optional[Dict]:
        """Return the task currently being executed, or None."""
        return self.current_task


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_executor: Optional[TaskExecutor] = None


def get_executor() -> TaskExecutor:
    """Return the module-level TaskExecutor singleton."""
    global _executor
    if _executor is None:
        from core.state import get_state_store
        from core.daemon_config import get_config
        _executor = TaskExecutor(get_state_store(), get_config())
    return _executor


def reset_executor():
    """Reset the singleton (used in tests)."""
    global _executor
    _executor = None
