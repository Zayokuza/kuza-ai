"""Shared evidence channel for Kuza's main agent and Python sidecar.

The channel is thread-safe, bounded in memory, and persisted as JSONL so useful
sidecar findings survive process restarts. It stores summaries, not raw secrets.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from utils.config import KUZA_STATE_DIR
from utils.redaction import redact_sensitive, sanitize_for_log


@dataclass(frozen=True)
class EvidenceEvent:
    sequence: int
    timestamp: float
    source: str
    kind: str
    summary: str
    task_id: str | None = None
    details: dict[str, Any] | None = None


class EvidenceChannel:
    """Thread-safe shared evidence bus with bounded persistence."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        max_events: int = 200,
    ) -> None:
        self.path = Path(path or (KUZA_STATE_DIR / "sidecar" / "evidence.jsonl"))
        self.max_events = max(20, max_events)
        self._events: deque[EvidenceEvent] = deque(maxlen=self.max_events)
        self._lock = threading.RLock()
        self._sequence = 0
        self._loaded = False

    def _ensure_loaded(self) -> None:
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            if not self.path.is_file():
                return
            try:
                lines = self.path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()[-self.max_events :]
            except OSError:
                return
            for line in lines:
                try:
                    raw = json.loads(line)
                    event = EvidenceEvent(
                        sequence=int(raw["sequence"]),
                        timestamp=float(raw["timestamp"]),
                        source=str(raw["source"]),
                        kind=str(raw["kind"]),
                        summary=str(raw["summary"]),
                        task_id=raw.get("task_id"),
                        details=raw.get("details")
                        if isinstance(raw.get("details"), dict)
                        else None,
                    )
                except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                    continue
                self._events.append(event)
                self._sequence = max(self._sequence, event.sequence)

    @staticmethod
    def _clean_text(value: Any, max_chars: int) -> str:
        text = redact_sensitive(str(value))
        text = " ".join(text.split())
        if len(text) > max_chars:
            text = text[: max_chars - 3] + "..."
        return text

    def publish(
        self,
        source: str,
        kind: str,
        summary: Any,
        *,
        task_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> EvidenceEvent:
        """Publish an event and return the stored, redacted representation."""
        self._ensure_loaded()
        safe_details = None
        if details:
            sanitized = sanitize_for_log(details, max_string=500)
            safe_details = sanitized if isinstance(sanitized, dict) else {
                "value": self._clean_text(sanitized, 500)
            }
        with self._lock:
            self._sequence += 1
            event = EvidenceEvent(
                sequence=self._sequence,
                timestamp=time.time(),
                source=self._clean_text(source, 40),
                kind=self._clean_text(kind, 60),
                summary=self._clean_text(summary, 1200),
                task_id=self._clean_text(task_id, 80) if task_id else None,
                details=safe_details,
            )
            self._events.append(event)
            self._append(event)
            return event

    def _append(self, event: EvidenceEvent) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                self.path.parent.chmod(0o700)
            except OSError:
                pass
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(event), sort_keys=True) + "\n")
            try:
                self.path.chmod(0o600)
            except OSError:
                pass
            # Compact occasionally so persistence stays bounded.
            if self.path.stat().st_size > 2_000_000:
                self._compact()
        except OSError:
            # Evidence sharing must never break the main task.
            return

    def _compact(self) -> None:
        temp = self.path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as handle:
            for event in self._events:
                handle.write(json.dumps(asdict(event), sort_keys=True) + "\n")
        os.replace(temp, self.path)

    def recent(
        self,
        *,
        limit: int = 20,
        since_sequence: int = 0,
    ) -> list[EvidenceEvent]:
        self._ensure_loaded()
        with self._lock:
            selected = [
                event for event in self._events
                if event.sequence > since_sequence
            ]
            return selected[-max(0, limit) :]

    def format_context(
        self,
        *,
        limit: int = 8,
        since_sequence: int = 0,
        max_chars: int = 2400,
    ) -> tuple[str, int]:
        """Format recent events for model context and return latest sequence."""
        events = self.recent(limit=limit, since_sequence=since_sequence)
        if not events:
            return "", since_sequence
        lines = ["Shared main/sidecar evidence:"]
        latest = since_sequence
        for event in events:
            latest = max(latest, event.sequence)
            prefix = f"- [{event.source}/{event.kind}]"
            line = f"{prefix} {event.summary}"
            if sum(len(part) + 1 for part in lines) + len(line) > max_chars:
                break
            lines.append(line)
        return "\n".join(lines), latest

    def clear_memory(self) -> None:
        """Clear in-memory events without deleting durable evidence."""
        with self._lock:
            self._events.clear()
            self._loaded = False
            self._sequence = 0


_channel: EvidenceChannel | None = None
_channel_lock = threading.Lock()


def get_evidence_channel() -> EvidenceChannel:
    global _channel
    if _channel is None:
        with _channel_lock:
            if _channel is None:
                _channel = EvidenceChannel()
    return _channel


def reset_evidence_channel(path: str | Path | None = None) -> EvidenceChannel:
    """Replace the singleton, primarily for tests."""
    global _channel
    with _channel_lock:
        _channel = EvidenceChannel(path)
        return _channel
