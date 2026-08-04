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

_LOG_LOCK = threading.Lock()
_DEFAULT_LOG_DIR = Path(
    os.environ.get("KUZA_LOG_DIR", Path.home() / ".kuza" / "logs")
)


def new_session_id() -> str:
    """Return a short unique ID for one Kuza request."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


def _json_safe(value: Any) -> Any:
    """Convert values into JSON-safe representations."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)


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

    path = target_dir / f"{session_id}.jsonl"
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "monotonic": time.monotonic(),
        "session_id": session_id,
        "level": level,
        "event": event,
        "caller": _caller_info(),
        "data": {key: _json_safe(value) for key, value in data.items()},
    }

    with _LOG_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    return path

import inspect

def _caller_info(depth: int = 2) -> dict:
    """
    Automatically determine where log_event() was called from.
    """
    frame = inspect.stack()[depth]

    filename = Path(frame.filename).resolve()

    try:
        relative = filename.relative_to(Path.cwd())
    except ValueError:
        relative = filename

    return {
        "module": frame.frame.f_globals.get("__name__", ""),
        "function": frame.function,
        "file": str(relative),
        "folder": str(relative.parent),
        "absolute_path": str(filename),
        "line": frame.lineno,
    }
