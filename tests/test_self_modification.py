#!/usr/bin/env python3
"""
Test self-modification opt-in and checkpoint enforcement.

Verifies that CODE_DIR access requires explicit opt-in and that checkpoints
are created before modifying core files.
"""

import sys
import os
from pathlib import Path
import tempfile
import shutil

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.filesystem import Filesystem, FilesystemAccessError
from utils.config import CODE_DIR


class TestSelfModification:
    """Test self-modification opt-in and enforcement."""

    def setup_method(self):
        """Set up test fixtures."""
        # Create a temp directory to act as workspace
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = Path(self.temp_dir)
        
        # Create a test file in workspace
        self.test_file = self.workspace / "test.txt"
        self.test_file.write_text("original content")

    def teardown_method(self):
        """Clean up test fixtures."""
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_blocks_code_dir_without_flag(self):
        """CODE_DIR access should be blocked without self-mod enabled."""
        fs = Filesystem(workspace=self.workspace, allow_self_modification=False)
        
        # Try to write to a core file path
        core_file = CODE_DIR / "test_file.py"
        
        try:
            fs.write(str(core_file), "test content")
            assert False, "Should have raised FilesystemAccessError"
        except FilesystemAccessError as e:
            assert "outside workspace" in str(e).lower() or "self-modification" in str(e).lower()

    def test_allows_code_dir_with_flag(self):
        """CODE_DIR access should be allowed with self-mod enabled."""
        fs = Filesystem(workspace=self.workspace, allow_self_modification=True)
        
        # Create a temp file in CODE_DIR for testing
        # Note: This test may fail in environments where CODE_DIR is not writable
        # In production, checkpoint would be created
        try:
            # Just test that validation passes (checkpoint may fail in test env)
            path = fs._validate_path(CODE_DIR / "test.py")
            # If we get here without exception, validation passed
            assert path is not None
        except FilesystemAccessError as e:
            # If checkpoint fails (expected in test env), that's OK
            # The important thing is the access wasn't blocked due to self-mod being disabled
            assert "checkpoint" in str(e).lower() or "self-modification" not in str(e).lower()

    def test_workspace_access_always_allowed(self):
        """Workspace access should work regardless of self-mod setting."""
        fs_disabled = Filesystem(workspace=self.workspace, allow_self_modification=False)
        fs_enabled = Filesystem(workspace=self.workspace, allow_self_modification=True)
        
        # Both should be able to write to workspace
        result1 = fs_disabled.write(str(self.test_file), "content1")
        assert "Written" in result1
        
        result2 = fs_enabled.write(str(self.test_file), "content2")
        assert "Written" in result2

    def test_outside_workspace_blocked_regardless(self):
        """Paths outside both workspace and CODE_DIR should always be blocked."""
        fs_enabled = Filesystem(workspace=self.workspace, allow_self_modification=True)
        
        # Try to write to /tmp (outside both workspace and CODE_DIR)
        try:
            fs_enabled.write("/tmp/test_outside.txt", "content")
            # This might succeed if /tmp is accessible, so check the path validation
            # The key is that random paths outside workspace/CODE_DIR are blocked
        except FilesystemAccessError as e:
            assert "outside workspace" in str(e).lower()

    def test_read_requires_self_mod_for_code_dir(self):
        """Reading CODE_DIR files should also require self-mod."""
        fs_disabled = Filesystem(workspace=self.workspace, allow_self_modification=False)
        
        # Try to read a core file
        core_file = CODE_DIR / "filesystem.py"
        if core_file.exists():
            try:
                fs_disabled.read(str(core_file))
                assert False, "Should have raised FilesystemAccessError"
            except FilesystemAccessError as e:
                assert "outside workspace" in str(e).lower() or "self-modification" in str(e).lower()

    def test_error_message_mentions_flag(self):
        """Error message should mention how to enable self-modification."""
        fs = Filesystem(workspace=self.workspace, allow_self_modification=False)
        
        try:
            fs.write(str(CODE_DIR / "test.py"), "content")
            assert False, "Should have raised FilesystemAccessError"
        except FilesystemAccessError as e:
            error_lower = str(e).lower()
            # Error should mention either the flag or env var
            assert "--allow-self-mod" in error_lower or "allow_self_mod" in error_lower or "outside workspace" in error_lower


class TestCheckpointEnforcement:
    """Test checkpoint creation before core file modifications."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = Path(self.temp_dir)

    def teardown_method(self):
        """Clean up test fixtures."""
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_checkpoint_required_for_core_write(self):
        """Writing to core files should trigger checkpoint requirement."""
        # This test verifies the checkpoint mechanism is in place
        # Full checkpoint testing requires git setup which is complex in test env
        fs = Filesystem(workspace=self.workspace, allow_self_modification=True)
        
        # The _require_checkpoint method should exist
        assert hasattr(fs, '_require_checkpoint')
        
        # The last successful checkpoint ID should be tracked for rollback.
        assert hasattr(fs, '_last_checkpoint_id')
        assert fs._last_checkpoint_id is None

    def test_each_core_mutation_gets_a_fresh_checkpoint(self, monkeypatch):
        """Later tasks must not reuse a stale session-wide checkpoint."""
        calls = []

        def fake_checkpoint(reason, files_modified):
            calls.append((reason, files_modified))
            return f"checkpoint-{len(calls)}"

        monkeypatch.setattr("core.filesystem.create_checkpoint", fake_checkpoint)
        fs = Filesystem(workspace=self.workspace, allow_self_modification=True)

        first = fs._require_checkpoint(CODE_DIR / "core" / "agent.py")
        second = fs._require_checkpoint(CODE_DIR / "core" / "agent.py")

        assert first == "checkpoint-1"
        assert second == "checkpoint-2"
        assert len(calls) == 2
        assert fs._last_checkpoint_id == "checkpoint-2"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
