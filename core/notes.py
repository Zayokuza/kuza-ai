"""
Persistent user notes for Kuza-v2.

Simple key-value store for facts the user asks Kuza to remember
(e.g., "my name is Ish", "I prefer tabs over spaces").

Stored at KUZA_STATE_DIR/notes.json — survives across sessions.
"""

import json
from pathlib import Path
from typing import Optional
from utils.config import KUZA_STATE_DIR

_NOTES_FILE = KUZA_STATE_DIR / "notes.json"


def _load() -> dict:
    """Load notes from disk."""
    if _NOTES_FILE.exists():
        try:
            return json.loads(_NOTES_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save(notes: dict):
    """Save notes to disk."""
    _NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _NOTES_FILE.write_text(json.dumps(notes, indent=2))
    try:
        _NOTES_FILE.parent.chmod(0o700)
        _NOTES_FILE.chmod(0o600)
    except OSError:
        pass


def add_note(key: str, value: str):
    """Save a note (overwrites if key exists)."""
    notes = _load()
    notes[key.lower().strip()] = value.strip()
    _save(notes)


def remove_note(key: str) -> bool:
    """Remove a note. Returns True if it existed."""
    notes = _load()
    k = key.lower().strip()
    if k in notes:
        del notes[k]
        _save(notes)
        return True
    return False


def get_note(key: str) -> Optional[str]:
    """Get a specific note."""
    return _load().get(key.lower().strip())


def get_all_notes() -> dict:
    """Get all notes."""
    return _load()


def get_notes_block() -> str:
    """Format notes as a system prompt block."""
    notes = _load()
    if not notes:
        return ""
    lines = [f"- {k}: {v}" for k, v in notes.items()]
    return "## User Notes\nThings the user has told you to remember:\n" + "\n".join(lines)
