"""Error learning stores useful metadata without persisting secrets."""

from core.error_database import ErrorPattern
from core.recovery import FallbackStrategy, execute_strategy


def test_error_pattern_redacts_message_fix_and_private_context():
    pattern = ErrorPattern(
        "RuntimeError",
        "failed for person@example.com using sk-proj-abcdefghijklmnopqrstuvwxyz",
        "retry with gho_abcdefghijklmnopqrstuvwxyz123456",
        False,
        {"command": "private command", "path": "app.py"},
    )
    saved = pattern.to_dict()
    assert "person@example.com" not in saved["error_message"]
    assert "sk-proj-" not in saved["error_message"]
    assert "gho_" not in saved["fix"]
    assert saved["context"]["command"]["redacted"] is True
    assert saved["context"]["path"] == "app.py"


def test_import_recovery_never_installs_automatically(monkeypatch):
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("pip ran")),
    )
    strategy = FallbackStrategy("install", "install missing", "pip_install", 0.9)
    result = execute_strategy(
        strategy,
        {"error_message": "ModuleNotFoundError: No module named 'example_package'"},
    )
    assert "install it explicitly" in result
