"""Shared redaction helpers for data persisted by Kuza."""

from __future__ import annotations

import hashlib
import re
from typing import Any


_SECRET_PATTERNS = (
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{12,}", re.IGNORECASE),
)
_KEY_VALUE_SECRET_RE = re.compile(
    r"(?i)([\"']?(?:password|passwd|secret|access[_-]?token|api[_-]?key|"
    r"authorization)[\"']?\s*[:=]\s*)([\"']?)([^\s\"',}]+)([\"']?)"
)
_EMAIL_RE = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?![\w.-])"
)
_PRIVATE_LOG_KEYS = {
    "access_token",
    "apikey",
    "authorization",
    "command",
    "content",
    "email",
    "new_str",
    "old_str",
    "password",
    "prompt",
    "query",
    "secret",
    "token",
    "api_key",
}


def redact_sensitive(text: str, *, redact_emails: bool = True) -> str:
    """Redact common credentials and optionally email addresses from text."""
    if not isinstance(text, str):
        return text
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED_SECRET]", text)
    text = _KEY_VALUE_SECRET_RE.sub(r"\1[REDACTED_SECRET]", text)
    if redact_emails:
        text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    return text


def private_value_summary(value: Any) -> dict:
    """Return a non-reversible descriptor for a value omitted from logs."""
    raw = str(value).encode("utf-8", errors="replace")
    return {
        "redacted": True,
        "length": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest()[:16],
    }


def sanitize_for_log(value: Any, *, key: str = "", max_string: int = 500) -> Any:
    """Recursively sanitize structured data before persistent logging."""
    normalized_key = key.casefold().replace("-", "_")
    if normalized_key in _PRIVATE_LOG_KEYS:
        return private_value_summary(value)
    if isinstance(value, dict):
        return {
            str(item_key): sanitize_for_log(item_value, key=str(item_key), max_string=max_string)
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [sanitize_for_log(item, max_string=max_string) for item in value]
    if isinstance(value, str):
        redacted = redact_sensitive(value)
        if len(redacted) > max_string:
            return redacted[:max_string] + "...[truncated]"
        return redacted
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_sensitive(repr(value))[:max_string]
