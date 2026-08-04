"""Regression tests for targeted, non-mutating Kuza checkpoints."""

import json
import subprocess
from pathlib import Path

import pytest

import core.checkpoint as checkpoint


class MemoryState:
    """Small state-store double used to keep checkpoint tests self-contained."""

    def __init__(self):
        self.checkpoints = {}
        self.actions = []

    def execute(self, _sql, params):
        checkpoint_id, created_at, reason, files_modified, git_hash = params
        self.checkpoints[checkpoint_id] = {
            "id": checkpoint_id,
            "created_at": created_at,
            "reason": reason,
            "files_modified": files_modified,
            "git_commit_hash": git_hash,
        }

    def get_checkpoint(self, checkpoint_id):
        return self.checkpoints.get(checkpoint_id)

    def log_action(self, action, details):
        self.actions.append((action, details))


@pytest.fixture
def checkpoint_env(tmp_path, monkeypatch):
    code_dir = tmp_path / "Kuza"
    backup_dir = tmp_path / "checkpoints"
    state = MemoryState()
    code_dir.mkdir()

    monkeypatch.setattr(checkpoint, "CODE_DIR", code_dir)
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", backup_dir)
    monkeypatch.setattr(checkpoint, "_get_state_store", lambda: state)
    return code_dir, backup_dir, state


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _init_git_repo(repo: Path) -> None:
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Kuza Test")
    _git(repo, "config", "user.email", "kuza-test@example.invalid")


def test_only_executable_kuza_source_is_protected(checkpoint_env):
    code_dir, _, _ = checkpoint_env

    protected = [
        code_dir / "core" / "agent.py",
        code_dir / "tools" / "shell_tools.py",
        code_dir / "utils" / "config.py",
        code_dir / "prompts" / "system_prompt.py",
        code_dir / "main.py",
        code_dir / "kuza",
        code_dir / "kuza2",
    ]
    ordinary = [
        code_dir / "test_patch.txt",
        code_dir / "README.md",
        code_dir / "tests" / "test_patch.py",
        code_dir / "generated" / "app.py",
    ]

    assert all(checkpoint.is_core_file(str(path)) for path in protected)
    assert not any(checkpoint.is_core_file(str(path)) for path in ordinary)


def test_checkpoint_preserves_head_index_and_unrelated_changes(checkpoint_env):
    code_dir, backup_root, state = checkpoint_env
    (code_dir / "core").mkdir()
    source_file = code_dir / "core" / "agent.py"
    source_file.write_text("original agent\n", encoding="utf-8")
    unrelated = code_dir / "unrelated.txt"
    unrelated.write_text("original unrelated\n", encoding="utf-8")
    staged = code_dir / "staged.txt"
    staged.write_text("original staged\n", encoding="utf-8")

    _init_git_repo(code_dir)
    _git(code_dir, "add", ".")
    _git(code_dir, "commit", "-q", "-m", "baseline")

    unrelated.write_text("unstaged user work\n", encoding="utf-8")
    staged.write_text("staged user work\n", encoding="utf-8")
    _git(code_dir, "add", "staged.txt")

    head_before = _git(code_dir, "rev-parse", "HEAD")
    branch_before = _git(code_dir, "symbolic-ref", "--short", "HEAD")
    status_before = _git(code_dir, "status", "--porcelain=v1")

    checkpoint_id = checkpoint.create_checkpoint(
        "test targeted backup",
        [str(source_file)],
    )

    assert _git(code_dir, "rev-parse", "HEAD") == head_before
    assert _git(code_dir, "symbolic-ref", "--short", "HEAD") == branch_before
    assert _git(code_dir, "status", "--porcelain=v1") == status_before

    checkpoint_dir = backup_root / checkpoint_id
    saved_files = {
        path.relative_to(checkpoint_dir).as_posix()
        for path in checkpoint_dir.rglob("*")
        if path.is_file()
    }
    assert saved_files == {"core/agent.py", checkpoint.MANIFEST_NAME}
    assert (checkpoint_dir / "core" / "agent.py").read_text() == "original agent\n"
    assert json.loads(state.checkpoints[checkpoint_id]["files_modified"]) == [
        "core/agent.py"
    ]
    assert state.checkpoints[checkpoint_id]["git_commit_hash"] == head_before


def test_rollback_restores_only_recorded_file_and_keeps_branch(checkpoint_env):
    code_dir, _, _ = checkpoint_env
    (code_dir / "core").mkdir()
    source_file = code_dir / "core" / "agent.py"
    source_file.write_text("before\n", encoding="utf-8")
    unrelated = code_dir / "notes.txt"
    unrelated.write_text("keep me\n", encoding="utf-8")

    _init_git_repo(code_dir)
    _git(code_dir, "add", ".")
    _git(code_dir, "commit", "-q", "-m", "baseline")
    branch_before = _git(code_dir, "symbolic-ref", "--short", "HEAD")

    checkpoint_id = checkpoint.create_checkpoint("before edit", [str(source_file)])
    source_file.write_text("after\n", encoding="utf-8")
    unrelated.write_text("unrelated user edit\n", encoding="utf-8")

    assert checkpoint.rollback(checkpoint_id) is True
    assert source_file.read_text(encoding="utf-8") == "before\n"
    assert unrelated.read_text(encoding="utf-8") == "unrelated user edit\n"
    assert _git(code_dir, "symbolic-ref", "--short", "HEAD") == branch_before


def test_rollback_removes_file_that_did_not_exist_at_checkpoint(checkpoint_env):
    code_dir, _, _ = checkpoint_env
    (code_dir / "core").mkdir()
    new_file = code_dir / "core" / "new_feature.py"

    checkpoint_id = checkpoint.create_checkpoint("before create", [str(new_file)])
    new_file.write_text("created later\n", encoding="utf-8")

    assert checkpoint.rollback(checkpoint_id) is True
    assert not new_file.exists()


def test_checkpoint_rejects_non_source_and_empty_targets(checkpoint_env):
    code_dir, backup_root, _ = checkpoint_env

    with pytest.raises(ValueError, match="not protected Kuza source"):
        checkpoint.create_checkpoint("wrong target", [str(code_dir / "README.md")])

    with pytest.raises(ValueError, match="requires at least one"):
        checkpoint.create_checkpoint("no target", [])

    assert not backup_root.exists()


def test_rollback_rejects_manifest_path_outside_source(checkpoint_env):
    _, backup_root, _ = checkpoint_env
    checkpoint_dir = backup_root / "malicious"
    checkpoint_dir.mkdir(parents=True)
    (checkpoint_dir / checkpoint.MANIFEST_NAME).write_text(
        json.dumps({
            "version": 2,
            "files": [{"path": "../outside.txt", "existed": False}],
        }),
        encoding="utf-8",
    )

    assert checkpoint.rollback("malicious") is False
