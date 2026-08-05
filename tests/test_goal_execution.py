"""Regression tests for Kuza's goal-driven execution workflow."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core.agent import _is_search_goal, _project_has_reusable_source
from core.filesystem import Filesystem
from core.save_state import create_save_state, restore_save_state
from core.sidecar.evidence import EvidenceChannel, reset_evidence_channel
from core.sidecar.manager import SidecarManager
from core.validation import (
    discover_validation_commands,
    run_validation,
    validate_changed_paths,
)


def test_save_state_restores_existing_file(tmp_path, monkeypatch):
    import core.save_state as save_state

    monkeypatch.setattr(save_state, "SAVE_STATE_DIR", tmp_path / ".states")
    target = tmp_path / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")

    state = create_save_state([target], "before improvement", workspace=tmp_path)
    target.write_text("value = 2\n", encoding="utf-8")

    restored = restore_save_state(state.save_state_id, workspace=tmp_path)

    assert restored == ["app.py"]
    assert target.read_text(encoding="utf-8") == "value = 1\n"
    assert state.files == ("app.py",)


def test_save_state_removes_file_created_after_snapshot(tmp_path, monkeypatch):
    import core.save_state as save_state

    monkeypatch.setattr(save_state, "SAVE_STATE_DIR", tmp_path / ".states")
    target = tmp_path / "new_feature.py"

    state = create_save_state([target], "before creation", workspace=tmp_path)
    target.write_text("print('new')\n", encoding="utf-8")
    restore_save_state(state.save_state_id, workspace=tmp_path)

    assert not target.exists()


def test_save_state_rejects_paths_outside_workspace(tmp_path, monkeypatch):
    import core.save_state as save_state

    monkeypatch.setattr(save_state, "SAVE_STATE_DIR", tmp_path / ".states")
    outside = tmp_path.parent / "outside.py"

    with pytest.raises(ValueError, match="outside workspace"):
        create_save_state([outside], "invalid", workspace=tmp_path)


def test_filesystem_write_reports_persistent_save_state(tmp_path, monkeypatch):
    import core.save_state as save_state

    monkeypatch.setattr(save_state, "SAVE_STATE_DIR", tmp_path / ".states")
    fs = Filesystem(workspace=tmp_path)

    result = fs.write("feature.py", "answer = 42\n")

    assert "save state:" in result
    assert fs.get_last_save_state_id()
    assert (tmp_path / "feature.py").read_text(encoding="utf-8") == "answer = 42\n"


def test_validation_discovers_compile_and_matching_test(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "tests").mkdir()
    source = tmp_path / "core" / "maths.py"
    source.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    test_file = tmp_path / "tests" / "test_maths.py"
    test_file.write_text(
        "from core.maths import add\n\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )

    commands = discover_validation_commands([source], root=tmp_path)

    assert commands[0][1:3] == ("-m", "py_compile")
    assert any("tests/test_maths.py" in command for command in commands)


def test_validation_returns_real_failure_evidence(tmp_path):
    broken = tmp_path / "broken.py"
    broken.write_text("def broken(:\n    pass\n", encoding="utf-8")

    results = validate_changed_paths([broken], root=tmp_path)

    assert len(results) == 1
    assert results[0].passed is False
    assert "SyntaxError" in results[0].output
    assert "[Validation FAIL]" in results[0].summary()


def test_validation_commands_do_not_use_shell(tmp_path):
    good = tmp_path / "good.py"
    good.write_text("value = 1\n", encoding="utf-8")
    commands = discover_validation_commands([good], root=tmp_path)

    results = run_validation(commands, root=tmp_path)

    assert results and all(result.passed for result in results)
    assert all(isinstance(result.command, tuple) for result in results)


def test_evidence_channel_shares_redacted_main_and_sidecar_context(tmp_path):
    channel = EvidenceChannel(tmp_path / "evidence.jsonl")
    channel.publish("main", "goal", "Find reusable parser code")
    channel.publish(
        "sidecar",
        "job_completed",
        "Found parser",
        details={"token": "sk-secret-value"},
    )

    context, sequence = channel.format_context(limit=5)

    assert "Find reusable parser code" in context
    assert "Found parser" in context
    assert sequence == 2
    assert (tmp_path / "evidence.jsonl").is_file()
    assert "sk-secret-value" not in (tmp_path / "evidence.jsonl").read_text()


def test_sidecar_worker_publishes_job_lifecycle(tmp_path):
    channel = reset_evidence_channel(tmp_path / "sidecar-events.jsonl")
    manager = SidecarManager(workers=1)
    try:
        job_id = manager.submit(
            "fast_python_job",
            lambda value: value * 2,
            21,
            context={"goal": "calculate"},
        )
        result = manager.wait(job_id, timeout=2.0)
        events = channel.recent(limit=10)

        assert result is not None
        assert result.status == "completed"
        assert result.result == 42
        assert [event.kind for event in events] == [
            "job_started",
            "job_completed",
        ]
        assert all(event.task_id == job_id for event in events)
    finally:
        manager.worker.stop()


@pytest.mark.parametrize(
    "message",
    [
        "Find every implementation of parse_tool_call",
        "Research the most reliable source",
        "Locate reusable authentication code",
        "Look up the official API documentation",
    ],
)
def test_search_goals_are_detected(message):
    assert _is_search_goal(message)


def test_existing_source_requires_reuse_inspection(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "existing.py").write_text("def reusable():\n    return 1\n")

    assert _project_has_reusable_source(tmp_path)


def test_empty_project_does_not_force_fake_reuse(tmp_path):
    (tmp_path / "README.md").write_text("empty project\n")

    assert not _project_has_reusable_source(tmp_path)


def test_validation_discovers_json_and_shell_checks(tmp_path):
    data = tmp_path / "settings.json"
    script = tmp_path / "run.sh"
    data.write_text('{"enabled": true}\n', encoding="utf-8")
    script.write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")

    commands = discover_validation_commands([data, script], root=tmp_path)

    assert any(command[1:3] == ("-m", "json.tool") for command in commands)
    if any(Path(command[0]).name == "bash" for command in commands):
        assert any("-n" in command and "run.sh" in command for command in commands)


def test_validation_reports_invalid_json(tmp_path):
    data = tmp_path / "broken.json"
    data.write_text('{"enabled": }\n', encoding="utf-8")

    results = validate_changed_paths([data], root=tmp_path)

    assert len(results) == 1
    assert results[0].passed is False
    assert "Expecting value" in results[0].output


def test_save_state_supports_explicit_kuza_code_root(tmp_path, monkeypatch):
    import core.filesystem as filesystem
    import core.save_state as save_state

    project = tmp_path / "project"
    code_root = tmp_path / "kuza-code"
    project.mkdir()
    code_root.mkdir()
    target = code_root / "core.py"
    target.write_text("version = 1\n", encoding="utf-8")

    monkeypatch.setattr(save_state, "SAVE_STATE_DIR", tmp_path / ".states")
    monkeypatch.setattr(filesystem, "CODE_DIR", code_root)

    fs = Filesystem(workspace=project, allow_self_modification=True)
    state_id = fs._save_before_mutation(target, "Patch")

    assert state_id
    target.write_text("version = 2\n", encoding="utf-8")
    restore_save_state(state_id)
    assert target.read_text(encoding="utf-8") == "version = 1\n"


def test_help_exposes_durable_save_state_commands():
    source = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")

    assert "/save-states" in source
    assert "/restore-state <id>" in source


def test_orchestrated_results_report_save_states_and_execution_evidence():
    source = (Path(__file__).parents[1] / "core" / "agent.py").read_text(
        encoding="utf-8"
    )

    assert "Save states created:" in source
    assert "Execution and test evidence:" in source


def test_learning_retrieves_relevant_verified_experience(monkeypatch):
    import json
    import core.state as state
    from core.learning import LearningManager

    class FakeStore:
        def get_recent_actions(self, limit=200):
            return [
                {
                    "action": "task_outcome",
                    "details": json.dumps({
                        "goal": "Implement reusable parser and run parser tests",
                        "actions": ["read_file", "patch_file", "pytest"],
                        "success": True,
                        "validation": ["3 passed"],
                        "recorded_at": 20,
                    }),
                },
                {
                    "action": "task_outcome",
                    "details": json.dumps({
                        "goal": "Change website colors",
                        "actions": ["patch_file"],
                        "success": True,
                        "validation": [],
                        "recorded_at": 30,
                    }),
                },
            ]

    monkeypatch.setattr(state, "get_state_store", lambda: FakeStore())
    manager = LearningManager.__new__(LearningManager)

    experiences = manager.get_relevant_experiences(
        "Improve the parser implementation and test it"
    )

    assert len(experiences) == 1
    assert experiences[0]["goal"].startswith("Implement reusable parser")
    assert experiences[0]["validation"] == ["3 passed"]


def test_learning_formats_actions_and_evidence(monkeypatch):
    import core.learning as learning

    manager = learning.LearningManager.__new__(learning.LearningManager)
    monkeypatch.setattr(
        manager,
        "get_relevant_experiences",
        lambda goal, limit=3: [{
            "goal": "Fix import routing",
            "actions": ["search_files", "patch_file"],
            "success": False,
            "validation": ["ImportError remained"],
        }],
    )

    context = manager.format_experience_context("Fix routing")

    assert "Fix import routing [failed]" in context
    assert "search_files, patch_file" in context
    assert "ImportError remained" in context


def test_web_search_falls_back_to_second_provider(monkeypatch):
    import json
    from tools import web_tools

    class FakeResponse:
        def __init__(self, body):
            self.body = body
            self.closed = False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=16384):
            yield self.body

        def close(self):
            self.closed = True

    calls = []
    empty_ddg = b"<html><body>No results</body></html>"
    bing = b"""
    <html><body><ol>
      <li class="b_algo">
        <h2><a href="https://example.com/code">Reusable code</a></h2>
        <div class="b_caption"><p>Implementation details</p></div>
      </li>
    </ol></body></html>
    """

    def fake_get(url, **kwargs):
        calls.append(url)
        return FakeResponse(empty_ddg if len(calls) == 1 else bing)

    monkeypatch.setattr(web_tools.requests, "get", fake_get)

    results = json.loads(web_tools.web_search("reusable parser", limit=5))

    assert len(calls) == 2
    assert results[0]["provider"] == "bing"
    assert results[0]["url"] == "https://example.com/code"


def test_search_result_parser_respects_requested_limit():
    from tools.web_tools import _parse_search_results

    body = b"""
    <div class="result"><a class="result__a" href="https://a.example">A</a></div>
    <div class="result"><a class="result__a" href="https://b.example">B</a></div>
    """

    results = _parse_search_results(body, "duckduckgo", limit=1)

    assert len(results) == 1
    assert results[0]["title"] == "A"


def test_active_mode_edits_project_metadata_without_prompt(tmp_path, monkeypatch):
    import core.save_state as save_state
    import tools.file_tools as file_tools
    import utils.logger as logger

    target = tmp_path / "README.md"
    target.write_text("old documentation\n", encoding="utf-8")

    monkeypatch.setattr(save_state, "SAVE_STATE_DIR", tmp_path / ".states")
    monkeypatch.setattr(
        logger,
        "confirm",
        lambda prompt: (_ for _ in ()).throw(
            AssertionError("active mode should not prompt for project metadata")
        ),
    )
    monkeypatch.setitem(file_tools.AGENT_CONFIG, "confirm_protected_writes", False)
    monkeypatch.setitem(file_tools.AGENT_CONFIG, "confirm_write", False)
    monkeypatch.setitem(file_tools.AGENT_CONFIG, "allow_self_modification", False)
    monkeypatch.setattr(file_tools, "_fs", Filesystem(workspace=tmp_path))
    monkeypatch.setattr(file_tools, "_fs_allow_self_mod", False)

    result = file_tools.tool_write_file("README.md", "new documentation\n")

    assert "[save state:" in result
    assert target.read_text(encoding="utf-8") == "new documentation\n"


@pytest.mark.parametrize(
    "command",
    [
        "pip install requests",
        "npm test",
        "cargo check",
        "go test ./...",
        "node --check app.js",
    ],
)
def test_daemon_allows_common_development_commands(command):
    from core.task_executor import _daemon_command_allowed

    assert _daemon_command_allowed(command)


def test_daemon_custom_allowlist_cannot_enable_destructive_commands(monkeypatch):
    from core.task_executor import _daemon_command_allowed

    monkeypatch.setenv("KUZA_DAEMON_ALLOW", "custom-linter,rm")

    assert _daemon_command_allowed("custom-linter --check src") is True
    assert _daemon_command_allowed("rm file.txt") is False


def test_project_context_collects_nested_reusable_code(tmp_path, monkeypatch):
    from core.orchestrator import _collect_project_files

    nested = tmp_path / "src" / "package" / "service"
    nested.mkdir(parents=True)
    (nested / "worker.py").write_text(
        "def reusable_worker():\n    return 'ready'\n",
        encoding="utf-8",
    )
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "noise.js").write_text("ignored", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    context = _collect_project_files(max_chars=4000)

    assert "src/package/service/worker.py" in context
    assert "reusable_worker" in context
    assert "node_modules" not in context
