"""Keep tests isolated from a developer's real Kuza state."""

import os
import shutil
import tempfile


_TEST_STATE_DIR = tempfile.mkdtemp(prefix="kuza-pytest-state-")
os.environ["KUZA_STATE_DIR"] = _TEST_STATE_DIR


def pytest_sessionfinish(session, exitstatus):
    """Remove the isolated state directory after the test session."""
    del session, exitstatus
    shutil.rmtree(_TEST_STATE_DIR, ignore_errors=True)
