"""Central authorization and cancellation policy for Kuza actions."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from utils.config import AGENT_CONFIG

_MUTATING = {"write", "patch", "append", "delete"}
_SENSITIVE_NAMES = {
    ".env", "id_rsa", "id_ed25519", "credentials.json", "secrets.py"
}
_SENSITIVE_SUFFIXES = {".key", ".pem", ".token"}

@dataclass(frozen=True)
class ActionRequest:
    kind: str
    description: str
    target: str = ""
    dangerous: bool = False

def cancellation_requested() -> bool:
    check: Callable[[], bool] | None = AGENT_CONFIG.get("_cancel_check")
    if not callable(check):
        return False
    try:
        return bool(check())
    except Exception:
        return False

def _sensitive_target(target: str) -> bool:
    if not target:
        return False
    path = Path(target)
    return path.name in _SENSITIVE_NAMES or path.suffix.lower() in _SENSITIVE_SUFFIXES

def authorize(
    request: ActionRequest,
    *,
    yolo: bool | None = None,
    confirm_fn: Callable[[str], bool] | None = None,
) -> tuple[bool, str]:
    if cancellation_requested():
        return False, "Task was cancelled"

    mode = str(AGENT_CONFIG.get("execution_mode", "interactive")).lower()
    effective_yolo = bool(
        AGENT_CONFIG.get("_yolo", False) if yolo is None else yolo
    )
    sensitive = _sensitive_target(request.target)

    if mode == "read_only" and request.kind in (_MUTATING | {"shell"}):
        return False, "Read-only mode blocks this action"

    if mode == "daemon":
        if sensitive and request.kind in _MUTATING:
            return False, "Daemon mode blocks sensitive-file mutation"
        return True, "daemon capability policy"

    if effective_yolo:
        return True, "yolo"

    confirm_flag = (
        AGENT_CONFIG.get("confirm_shell", False)
        if request.kind == "shell"
        else AGENT_CONFIG.get("confirm_write", False)
    )
    must_confirm = bool(request.dangerous or sensitive or confirm_flag)
    if not must_confirm:
        return True, "authorized by active profile"

    from utils.logger import confirm, warning
    confirm_action = confirm_fn or confirm
    if request.dangerous or sensitive:
        warning(f"Potentially dangerous {request.kind}: {request.description}")
    target = f" [{request.target}]" if request.target else ""
    if confirm_action(f"Allow {request.kind}{target}: {request.description}?"):
        return True, "confirmed"
    return False, "User declined"
