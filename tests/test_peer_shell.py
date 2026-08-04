"""Security regressions for external peer CLI execution."""

import pytest

from core.peer_shell import _parse_command, run_direct


def test_peer_command_is_parsed_as_argv():
    assert _parse_command('gemini --model "safe model"') == [
        "gemini",
        "--model",
        "safe model",
    ]


@pytest.mark.parametrize(
    "command",
    [
        "claude; touch marker",
        "claude | tee output",
        "claude && touch marker",
        "claude $(touch marker)",
        "claude `touch marker`",
        "claude\ntouch marker",
    ],
)
def test_peer_command_rejects_shell_control_syntax(command):
    with pytest.raises(ValueError, match="unsupported shell syntax"):
        _parse_command(command)


def test_direct_runner_rejects_injection_before_starting_process(monkeypatch):
    monkeypatch.setattr(
        "core.peer_shell.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("process started")),
    )
    result = run_direct("peer", "peer; touch marker")
    assert result.startswith("[PEER_ERROR:")
