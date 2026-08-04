#!/usr/bin/env python3
"""
Checkpoint system for Kuza-v2 self-modification.

Before modifying core files, creates a checkpoint:
- Targeted file backup in ~/.kuza-v2/checkpoints/
- Read-only reference to the current Git commit, when available
- SQLite record for tracking

Supports rollback to any checkpoint.
"""

import shutil
import time
import json
import subprocess
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass

from utils.logger import info, warning, error, success
from utils.config import CODE_DIR


# Checkpoint directory
CHECKPOINT_DIR = Path.home() / ".kuza-v2" / "checkpoints"

# Only Kuza's executable source is considered self-modification. Project files,
# tests, documentation, and generated files in the repository are deliberately
# excluded so ordinary work never triggers a checkpoint.
CORE_DIRECTORIES = {"core", "tools", "utils", "prompts"}
CORE_ROOT_FILES = {"main.py", "kuza", "kuza2"}
MANIFEST_NAME = "manifest.json"


@dataclass
class Checkpoint:
    """Represents a checkpoint."""
    id: str
    created_at: int
    reason: str
    files_modified: List[str]
    git_commit_hash: Optional[str]


def _get_state_store():
    """Import state lazily so importing this module never creates state files."""
    from core.state import get_state_store
    return get_state_store()


def _is_core_relative_path(path: Path) -> bool:
    """Return whether a path relative to CODE_DIR is protected Kuza source."""
    if not path.parts:
        return False
    if len(path.parts) == 1:
        return path.as_posix() in CORE_ROOT_FILES
    return path.parts[0] in CORE_DIRECTORIES and path.suffix == ".py"


def _resolve_core_path(file_path: str) -> tuple[Path, Path]:
    """Resolve and validate a checkpoint target, returning path and repo path."""
    path = Path(file_path).expanduser()
    if not path.is_absolute():
        path = CODE_DIR / path
    path = path.resolve()

    try:
        relative = path.relative_to(CODE_DIR.resolve())
    except ValueError as exc:
        raise ValueError(f"Checkpoint target is outside Kuza source: {path}") from exc

    if not _is_core_relative_path(relative):
        raise ValueError(f"Checkpoint target is not protected Kuza source: {relative}")

    return path, relative


def is_core_file(file_path: str) -> bool:
    """Check if a file is a Kuza-v2 file that needs checkpointing."""
    try:
        _resolve_core_path(file_path)
        return True
    except ValueError:
        return False


def create_checkpoint(reason: str, files_modified: List[str] = None) -> str:
    """
    Create a checkpoint before self-modification.
    
    Args:
        reason: Reason for checkpoint (e.g., "Adding new feature")
        files_modified: List of files that will be modified
        
    Returns:
        Unique checkpoint ID
    """
    targets = []
    seen = set()
    for file_path in files_modified or []:
        path, relative = _resolve_core_path(file_path)
        relative_name = relative.as_posix()
        if relative_name not in seen:
            targets.append((path, relative))
            seen.add(relative_name)

    if not targets:
        raise ValueError("A checkpoint requires at least one protected Kuza source file")

    checkpoint_id = str(time.time_ns())
    backup_dir = CHECKPOINT_DIR / checkpoint_id
    backup_dir.mkdir(parents=True, exist_ok=True)

    info(f"Checkpoint: creating '{checkpoint_id}' - {reason}")

    manifest = {"version": 2, "files": []}
    try:
        for path, relative in targets:
            if path.exists() and not path.is_file():
                raise ValueError(f"Checkpoint target is not a file: {path}")

            existed = path.is_file()
            if existed:
                destination = backup_dir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)

            manifest["files"].append({
                "path": relative.as_posix(),
                "existed": existed,
            })

        (backup_dir / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2),
            encoding="utf-8",
        )

        # Git is read-only here. Never stage, commit, switch branches, or alter
        # the user's index as part of a safety checkpoint.
        git_hash = _get_git_head()

        state = _get_state_store()
        state.execute("""
            INSERT INTO checkpoints (id, created_at, reason, files_modified, git_commit_hash)
            VALUES (?, ?, ?, ?, ?)
        """, (
            checkpoint_id,
            int(time.time()),
            reason,
            json.dumps([relative.as_posix() for _, relative in targets]),
            git_hash,
        ))
    except Exception:
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise

    success(f"Checkpoint '{checkpoint_id}' created ({len(targets)} files backed up)")
    return checkpoint_id


def _get_git_head() -> Optional[str]:
    """Return the current Git HEAD without changing the worktree or index."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=CODE_DIR,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception as e:
        warning(f"Checkpoint: could not read Git HEAD: {e}")
        return None


def rollback(checkpoint_id: str) -> bool:
    """
    Rollback to a checkpoint.
    
    Args:
        checkpoint_id: Checkpoint ID to rollback to
        
    Returns:
        True if rollback successful
    """
    backup_dir = CHECKPOINT_DIR / checkpoint_id
    
    if not backup_dir.exists():
        error(f"Rollback: checkpoint '{checkpoint_id}' not found")
        return False
    
    info(f"Rollback: restoring from '{checkpoint_id}'")

    try:
        manifest_path = backup_dir / MANIFEST_NAME
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entries = manifest.get("files")
            if manifest.get("version") != 2 or not isinstance(entries, list):
                raise ValueError("unsupported or malformed checkpoint manifest")
        else:
            # Backward compatibility for checkpoints made before targeted
            # manifests were introduced. These backups only contain files that
            # existed, so they can restore but cannot remove newly-created files.
            entries = [
                {
                    "path": backup_file.relative_to(backup_dir).as_posix(),
                    "existed": True,
                }
                for backup_file in backup_dir.rglob("*")
                if backup_file.is_file()
            ]

        operations = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise ValueError("malformed checkpoint file entry")
            if not isinstance(entry.get("existed"), bool):
                raise ValueError("checkpoint file entry is missing an existence flag")

            destination, relative = _resolve_core_path(entry["path"])
            existed = entry["existed"]
            source = backup_dir / relative
            if existed and not source.is_file():
                raise FileNotFoundError(f"missing checkpoint backup: {relative}")
            operations.append((source, destination, existed))

        restored = 0
        removed = 0
        for source, destination, existed in operations:
            if existed:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                restored += 1
            elif destination.exists() or destination.is_symlink():
                if destination.is_dir():
                    raise IsADirectoryError(f"rollback target became a directory: {destination}")
                destination.unlink()
                removed += 1
    except Exception as exc:
        error(f"Rollback: failed for checkpoint '{checkpoint_id}': {exc}")
        return False

    # Rollback is file-scoped. It deliberately never checks out a commit or
    # switches branches, so unrelated work and the active branch are preserved.
    success(
        f"Rollback: restored {restored} files, removed {removed} created files "
        f"from checkpoint '{checkpoint_id}'"
    )

    try:
        _get_state_store().log_action("rollback", f"Restored from checkpoint {checkpoint_id}")
    except Exception as exc:
        warning(f"Rollback: restored files but could not record action: {exc}")

    return True


def list_checkpoints(limit: int = 10) -> List[Dict]:
    """
    List recent checkpoints.
    
    Args:
        limit: Maximum number of checkpoints to return
        
    Returns:
        List of checkpoint info dicts
    """
    state = _get_state_store()
    checkpoints = state.get_checkpoints(limit)
    
    result = []
    for cp in checkpoints:
        result.append({
            "id": cp["id"],
            "created_at": cp["created_at"],
            "reason": cp["reason"],
            "git_commit": cp["git_commit_hash"][:8] if cp["git_commit_hash"] else None,
        })
    
    return result


def get_latest_checkpoint() -> Optional[str]:
    """Get the most recent checkpoint ID."""
    state = _get_state_store()
    checkpoints = state.get_checkpoints(1)
    return checkpoints[0]["id"] if checkpoints else None


def prune_checkpoints(keep_count: int = 5):
    """
    Remove old checkpoints, keeping only the most recent ones.
    
    Args:
        keep_count: Number of recent checkpoints to keep
    """
    state = _get_state_store()
    checkpoints = state.get_checkpoints(100)  # Get all
    
    if len(checkpoints) <= keep_count:
        return
    
    to_remove = checkpoints[keep_count:]
    
    for cp in to_remove:
        checkpoint_id = cp["id"]
        backup_dir = CHECKPOINT_DIR / checkpoint_id
        
        # Remove backup directory
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        
        # Remove from database
        state.delete_checkpoint(checkpoint_id)
        
        info(f"Checkpoint: pruned '{checkpoint_id}'")
    
    success(f"Checkpoint: pruned {len(to_remove)} old checkpoints")
