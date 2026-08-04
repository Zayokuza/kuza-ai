"""Tests for the Termux launcher and daemon client argument handling."""

import os
import subprocess
from pathlib import Path

from core import daemon_cli


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_launchers_do_not_generate_python_source():
    daemon_launcher = (REPO_ROOT / "kuzad2").read_text(encoding="utf-8")
    assert "mktemp" not in daemon_launcher
    assert "cat >" not in daemon_launcher
    assert "python3 - <<" not in daemon_launcher


def _fake_python(tmp_path: Path) -> tuple[Path, Path]:
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    capture = tmp_path / "python-args.txt"
    fake = binary_dir / "python3"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\" > \"$CAPTURE_FILE\"\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return binary_dir, capture


def test_prompt_is_passed_as_data_not_executed(tmp_path):
    binary_dir, capture = _fake_python(tmp_path)
    marker = tmp_path / "injection-marker"
    prompt = f'quotes \" triple \"\"\" $(touch {marker}) `touch {marker}`'
    environment = os.environ.copy()
    environment.update({
        "PATH": f"{binary_dir}:{environment.get('PATH', '')}",
        "CAPTURE_FILE": str(capture),
        "KUZA_STATE_DIR": str(tmp_path / "state"),
        "KUZA_AUTO_GUI": "0",
    })

    result = subprocess.run(
        [str(REPO_ROOT / "kuza2"), prompt],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert marker.exists() is False
    captured = capture.read_text(encoding="utf-8").splitlines()
    assert captured[0].endswith("main.py")
    assert captured[1] == prompt


def test_daemon_flag_passes_real_arguments(tmp_path):
    binary_dir, capture = _fake_python(tmp_path)
    environment = os.environ.copy()
    environment.update({
        "PATH": f"{binary_dir}:{environment.get('PATH', '')}",
        "CAPTURE_FILE": str(capture),
        "KUZA_STATE_DIR": str(tmp_path / "state"),
    })
    result = subprocess.run(
        [str(REPO_ROOT / "kuza2"), "--daemon", "--no-plan"],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    captured = capture.read_text(encoding="utf-8").splitlines()
    assert captured[-2:] == ["--daemon", "--no-plan"]
    assert "$@" not in captured


def test_daemon_queue_preserves_special_prompt(monkeypatch, capsys):
    prompt = 'line one\nline two with \"quotes\" and $(literal)'
    captured = {}

    def fake_send(command, data, timeout):
        captured.update(command=command, data=data, timeout=timeout)
        return {"status": "ok", "task_id": 7}

    monkeypatch.setattr(daemon_cli, "send_command", fake_send)
    assert daemon_cli.main(["queue", f"--prompt={prompt}"]) == 0
    assert captured["command"] == "command"
    assert captured["data"]["prompt"] == prompt
    assert json_status_ok(capsys.readouterr().out)


def json_status_ok(output: str) -> bool:
    return '"status": "ok"' in output
