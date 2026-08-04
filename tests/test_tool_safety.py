"""Regression tests for shell, patch, search, and web tool boundaries."""

from pathlib import Path
from types import SimpleNamespace

from tools import web_tools
from tools.patch_tools import tool_patch_file
from tools.shell_tools import (
    is_dangerous,
    search_files,
    shell,
    validate_command_structure,
)


class TestShellSafety:
    def test_nonzero_exit_is_an_error(self):
        result = shell("python -c \"import sys; sys.exit(7)\"")
        assert result.startswith("[ERROR] Command exited with status 7")

    def test_simple_command_uses_argv_without_shell(self, monkeypatch):
        calls = []

        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(stdout="ok\n", stderr="", returncode=0)

        monkeypatch.setattr("tools.shell_tools.subprocess.run", fake_run)
        assert shell("python script.py --flag value") == "ok"
        assert calls[0][0] == ["python", "script.py", "--flag", "value"]
        assert calls[0][1]["shell"] is False

    def test_compound_command_requires_authorization(self, monkeypatch):
        monkeypatch.setattr("tools.shell_tools.ask_confirm", lambda _prompt: False)
        assert shell("echo ok && echo no").startswith("[CANCELLED]")

    def test_command_substitution_is_not_simple(self):
        valid, reason = validate_command_structure("echo $(whoami)")
        assert valid is False
        assert "$(" in reason

    def test_quoted_python_semicolon_is_simple(self):
        valid, reason = validate_command_structure(
            "python -c \"import sys; print(sys.version)\""
        )
        assert valid is True
        assert reason == ""

    def test_dangerous_command_is_detected_through_env_wrapper(self):
        assert is_dangerous("env MODE=test rm file.txt") is True


class TestFileToolBoundaries:
    def test_agent_search_mapping_unpacks_arguments(self, monkeypatch):
        import core.agent as agent

        captured = {}

        def fake_search(pattern, path):
            captured.update(pattern=pattern, path=path)
            return "ok"

        monkeypatch.setattr(agent, "search_files", fake_search)
        assert agent.TOOLS["search_files"]({"pattern": "*.py", "path": "src"}) == "ok"
        assert captured == {"pattern": "*.py", "path": "src"}

    def test_search_files_accepts_pattern_and_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "one.py").write_text("pass\n", encoding="utf-8")
        (tmp_path / "two.txt").write_text("text\n", encoding="utf-8")

        result = search_files("*.py", ".")
        assert "one.py" in result
        assert "two.txt" not in result

    def test_search_files_rejects_outside_workspace(self, tmp_path, monkeypatch):
        workspace = tmp_path / "workspace"
        outside = tmp_path / "outside"
        workspace.mkdir()
        outside.mkdir()
        monkeypatch.chdir(workspace)

        result = search_files("*", str(outside))
        assert result.startswith("[ERROR] Search path is outside the workspace")

    def test_patch_validates_before_reading(self, tmp_path, monkeypatch):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("PRIVATE-CONTENT", encoding="utf-8")

        import core.filesystem as filesystem
        monkeypatch.setattr(filesystem, "WORKSPACE_ROOT", workspace)
        filesystem.reset_filesystem()

        result = tool_patch_file(str(outside), "missing", "replacement")
        assert result.startswith("[ERROR] Access denied")
        assert "PRIVATE-CONTENT" not in result
        filesystem.reset_filesystem()

    def test_failed_patch_does_not_echo_file(self, tmp_path, monkeypatch):
        import core.filesystem as filesystem
        monkeypatch.setattr(filesystem, "WORKSPACE_ROOT", tmp_path)
        filesystem.reset_filesystem()
        target = tmp_path / "notes.txt"
        target.write_text("PRIVATE-CONTENT", encoding="utf-8")

        result = tool_patch_file(str(target), "missing", "replacement")
        assert result.startswith("[ERROR] String not found")
        assert "PRIVATE-CONTENT" not in result
        filesystem.reset_filesystem()


class TestWebSafety:
    def test_private_address_is_rejected(self, monkeypatch):
        monkeypatch.setattr(
            web_tools.socket,
            "getaddrinfo",
            lambda *_args, **_kwargs: [(None, None, None, None, ("127.0.0.1", 0))],
        )
        assert web_tools._safe_public_url("http://example.test") is False

    def test_redirect_is_validated_before_second_request(self, monkeypatch):
        class RedirectResponse:
            status_code = 302
            headers = {"location": "http://127.0.0.1/private"}

            def close(self):
                pass

        class FakeSession:
            def __init__(self):
                self.urls = []

            def get(self, url, **_kwargs):
                self.urls.append(url)
                return RedirectResponse()

            def close(self):
                pass

        fake_session = FakeSession()
        monkeypatch.setattr(web_tools.requests, "Session", lambda: fake_session)
        monkeypatch.setattr(
            web_tools,
            "_safe_public_url",
            lambda url: "127.0.0.1" not in url,
        )

        result = web_tools.read_webpage("https://public.example/start")
        assert result == "[ERROR] Redirected to a non-public address"
        assert fake_session.urls == ["https://public.example/start"]

    def test_streamed_response_has_hard_byte_limit(self):
        response = SimpleNamespace(
            iter_content=lambda chunk_size: iter([b"a" * 6, b"b" * 6])
        )
        try:
            web_tools._read_limited_bytes(response, max_bytes=10)
            assert False, "expected byte-limit failure"
        except ValueError as exc:
            assert "10 byte limit" in str(exc)


class TestDaemonShellPolicy:
    def test_daemon_allows_only_exact_simple_commands(self):
        from core.task_executor import _daemon_command_allowed

        assert _daemon_command_allowed("python script.py") is True
        assert _daemon_command_allowed("git status --short") is True
        assert _daemon_command_allowed("python_evil script.py") is False
        assert _daemon_command_allowed("python -c 'print(1)'") is False
        assert _daemon_command_allowed("find . -delete") is False
        assert _daemon_command_allowed("git commit -m nope") is False
        assert _daemon_command_allowed("python script.py; rm file") is False
