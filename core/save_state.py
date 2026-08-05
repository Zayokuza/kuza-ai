"""Persistent, file-scoped save states for project changes.

Save states are read-only backups created before Kuza mutates project files.
They never stage, commit, reset, or switch Git state.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from utils.config import KUZA_STATE_DIR


SAVE_STATE_DIR = KUZA_STATE_DIR / "save_states"
_MANIFEST = "manifest.json"
_LOCK = threading.RLock()


@dataclass(frozen=True)
class SaveStateInfo:
    save_state_id: str
    reason: str
    workspace: str
    created_at: int
    files: tuple[str, ...]
    git_head: str | None = None
    git_branch: str | None = None


def _safe_relative(path: str | Path, workspace: Path) -> tuple[Path, Path]:
    root = workspace.expanduser().resolve()
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve(strict=False)
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Save-state target is outside workspace: {candidate}") from exc
    if not relative.parts:
        raise ValueError("Save-state target must be a file, not the workspace root")
    return candidate, relative


def _git_value(workspace: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = proc.stdout.strip()
    return value if proc.returncode == 0 and value else None


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_save_state(
    paths: Iterable[str | Path],
    reason: str,
    workspace: str | Path | None = None,
) -> SaveStateInfo:
    """Back up the named files before mutation.

    Missing files are recorded as ``existed=False`` so restoring the save state
    removes files that Kuza created after the snapshot.
    """
    root = Path(workspace or Path.cwd()).expanduser().resolve()
    targets: list[tuple[Path, Path]] = []
    seen: set[str] = set()

    for raw_path in paths:
        absolute, relative = _safe_relative(raw_path, root)
        key = relative.as_posix()
        if key in seen:
            continue
        if absolute.exists() and not absolute.is_file():
            raise ValueError(f"Save-state target is not a file: {absolute}")
        targets.append((absolute, relative))
        seen.add(key)

    if not targets:
        raise ValueError("A save state requires at least one file target")

    created_at = int(time.time())
    save_state_id = f"{time.time_ns()}-{os.getpid()}"
    destination = SAVE_STATE_DIR / save_state_id

    manifest = {
        "version": 1,
        "id": save_state_id,
        "reason": reason,
        "workspace": str(root),
        "created_at": created_at,
        "git_head": _git_value(root, "rev-parse", "HEAD"),
        "git_branch": _git_value(root, "branch", "--show-current"),
        "files": [],
    }

    with _LOCK:
        destination.mkdir(parents=True, exist_ok=False)
        try:
            SAVE_STATE_DIR.chmod(0o700)
            destination.chmod(0o700)
        except OSError:
            pass

        try:
            for absolute, relative in targets:
                existed = absolute.is_file()
                entry = {
                    "path": relative.as_posix(),
                    "existed": existed,
                    "mode": (absolute.stat().st_mode & 0o777) if existed else None,
                    "sha256": _sha256(absolute),
                }
                if existed:
                    backup = destination / relative
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(absolute, backup)
                manifest["files"].append(entry)

            (destination / _MANIFEST).write_text(
                json.dumps(manifest, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise

    return SaveStateInfo(
        save_state_id=save_state_id,
        reason=reason,
        workspace=str(root),
        created_at=created_at,
        files=tuple(entry["path"] for entry in manifest["files"]),
        git_head=manifest["git_head"],
        git_branch=manifest["git_branch"],
    )


def restore_save_state(
    save_state_id: str,
    workspace: str | Path | None = None,
) -> list[str]:
    """Restore a save state into its original workspace.

    Returns the relative paths restored or removed.
    """
    source = SAVE_STATE_DIR / save_state_id
    manifest_path = source / _MANIFEST
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Save state not found: {save_state_id}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != 1 or not isinstance(manifest.get("files"), list):
        raise ValueError(f"Malformed save-state manifest: {save_state_id}")

    recorded_root = Path(manifest["workspace"]).expanduser().resolve()
    root = Path(workspace).expanduser().resolve() if workspace else recorded_root
    restored: list[str] = []

    with _LOCK:
        for entry in manifest["files"]:
            relative_text = entry.get("path")
            existed = entry.get("existed")
            if not isinstance(relative_text, str) or not isinstance(existed, bool):
                raise ValueError("Malformed save-state file entry")

            target, relative = _safe_relative(relative_text, root)
            if existed:
                backup = source / relative
                if not backup.is_file():
                    raise FileNotFoundError(f"Missing save-state backup: {relative}")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, target)
                if isinstance(entry.get("mode"), int):
                    target.chmod(entry["mode"])
            elif target.exists() or target.is_symlink():
                if target.is_dir():
                    raise IsADirectoryError(f"Restore target became a directory: {target}")
                target.unlink()
            restored.append(relative.as_posix())

    return restored


def list_save_states(limit: int = 20) -> list[SaveStateInfo]:
    """Return recent save states, newest first."""
    if not SAVE_STATE_DIR.is_dir():
        return []

    results: list[SaveStateInfo] = []
    for directory in sorted(SAVE_STATE_DIR.iterdir(), reverse=True):
        manifest_path = directory / _MANIFEST
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            results.append(
                SaveStateInfo(
                    save_state_id=str(manifest["id"]),
                    reason=str(manifest.get("reason", "")),
                    workspace=str(manifest["workspace"]),
                    created_at=int(manifest["created_at"]),
                    files=tuple(
                        entry["path"] for entry in manifest.get("files", [])
                        if isinstance(entry, dict) and isinstance(entry.get("path"), str)
                    ),
                    git_head=manifest.get("git_head"),
                    git_branch=manifest.get("git_branch"),
                )
            )
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
        if len(results) >= max(0, limit):
            break
    return results
