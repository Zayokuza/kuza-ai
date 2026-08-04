"""Shell control syntax must not be treated as a simple argv command."""

import pytest

from tools.shell_tools import SHELL_METACHARACTERS, validate_command_structure


@pytest.mark.parametrize("token", SHELL_METACHARACTERS)
def test_shell_control_token_requires_authorization(token):
    valid, reason = validate_command_structure(f"ls {token} test")
    assert valid is False
    assert token in reason


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "cat file.txt",
        "grep pattern file.py",
        "find . -name '*.py'",
        "python3 script.py --arg value",
        "git status",
        "python -c \"import sys; print(sys.version)\"",
    ],
)
def test_simple_argv_command_is_allowed(command):
    assert validate_command_structure(command) == (True, "")


def test_invalid_quoting_is_rejected():
    valid, reason = validate_command_structure("python -c 'unterminated")
    assert valid is False
    assert "quoting" in reason.lower()
