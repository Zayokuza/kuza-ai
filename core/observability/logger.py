"""Structured JSONL event logger for Kuza."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.config import KUZA_STATE_DIR
from utils.redaction import sanitize_for_log

_LOG_LOCK = threading.Lock()
_DEFAULT_LOG_DIR = Path(
    os.environ.get("KUZA_LOG_DIR", KUZA_STATE_DIR / "logs")
)


def new_session_id() -> str:
    """Return a short unique ID for one Kuza request."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


def log_event(
    event: str,
    *,
    session_id: str,
    level: str = "info",
    log_dir: str | Path | None = None,
    **data: Any,
) -> Path:
    """Append one structured event to the session JSONL log."""
    target_dir = Path(log_dir) if log_dir else _DEFAULT_LOG_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        target_dir.chmod(0o700)
    except OSError:
        pass

    path = target_dir / f"{session_id}.jsonl"
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "monotonic": time.monotonic(),
        "session_id": session_id,
        "level": level,
        "event": event,
        "caller": _caller_info(),
        "data": {key: sanitize_for_log(value, key=key) for key, value in data.items()},
    }

    with _LOG_LOCK:
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
        except OSError:
            pass
        with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    return path

import inspect

def _caller_info(depth: int = 2) -> dict:
    """
    Automatically determine where log_event() was called from.
    """
    frame = inspect.currentframe()
    for _ in range(depth):
        frame = frame.f_back if frame else None
    if frame is None:
        return {"module": "", "function": "", "file": "", "folder": "", "line": 0}

    filename = Path(frame.f_code.co_filename).resolve()

    try:
        relative = filename.relative_to(Path.cwd())
    except ValueError:
        relative = Path(filename.name)

    return {
        "module": frame.f_globals.get("__name__", ""),
        "function": frame.f_code.co_name,
        "file": str(relative),
        "folder": str(relative.parent),
        "line": frame.f_lineno,
    }
