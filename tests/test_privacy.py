"""Tests for persistent-data redaction and private file permissions."""

import json
import os
import stat
import subprocess
import sys

from core.observability.logger import log_event
from core.sessions import redact_secrets
from core.state import StateStore
from utils.redaction import redact_sensitive, sanitize_for_log


def test_redacts_modern_tokens_and_email_addresses():
    text = (
        "sk-proj-abcdefghijklmnopqrstuvwxyz123456 "
        "gho_abcdefghijklmnopqrstuvwxyz123456 "
        "github_pat_abcdefghijklmnopqrstuvwxyz123456 "
        "Bearer abcdefghijklmnopqrstuvwxyz "
        "person@example.com"
    )
    redacted = redact_sensitive(text)
    assert "sk-proj-" not in redacted
    assert "gho_" not in redacted
    assert "github_pat_" not in redacted
    assert "Bearer abc" not in redacted
    assert "person@example.com" not in redacted


def test_session_redaction_uses_shared_patterns():
    assert "person@example.com" not in redact_secrets("email person@example.com")


def test_structured_arguments_store_descriptors_not_values():
    sanitized = sanitize_for_log({
        "path": "app.py",
        "content": "private file body",
        "command": "private command",
        "email": "person@example.com",
    })
    assert sanitized["path"] == "app.py"
    assert sanitized["content"]["redacted"] is True
    assert sanitized["command"]["redacted"] is True
    assert sanitized["email"]["redacted"] is True
    assert "private file body" not in json.dumps(sanitized)


def test_event_log_is_private_and_omits_tool_payloads(tmp_path):
    path = log_event(
        "tool_start",
        session_id="privacy-test",
        log_dir=tmp_path / "logs",
        tool="write_file",
        arguments={
            "path": "app.py",
            "content": "TOP-SECRET-CONTENT",
            "email": "person@example.com",
        },
    )
    saved = path.read_text(encoding="utf-8")
    assert "TOP-SECRET-CONTENT" not in saved
    assert "person@example.com" not in saved
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_importing_state_does_not_create_state_directory(tmp_path):
    state_dir = tmp_path / "not-created-by-import"
    environment = os.environ.copy()
    environment["KUZA_STATE_DIR"] = str(state_dir)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import core.state; "
            "assert not Path(r'" + str(state_dir) + "').exists()",
        ],
        cwd=os.getcwd(),
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_state_store_creates_private_database_only_when_used(tmp_path):
    database = tmp_path / "state" / "state.db"
    store = StateStore(database)
    try:
        assert database.exists()
        assert stat.S_IMODE(database.stat().st_mode) == 0o600
        assert stat.S_IMODE(database.parent.stat().st_mode) == 0o700
    finally:
        store.close()


def test_episodic_log_redacts_sensitive_details(tmp_path):
    database = tmp_path / "state" / "state.db"
    store = StateStore(database)
    try:
        store.log_action("lookup", "person@example.com sk-proj-abcdefghijklmnopqrstuvwxyz")
        details = store.get_recent_actions(1)[0]["details"]
        assert "person@example.com" not in details
        assert "sk-proj-" not in details
    finally:
        store.close()
