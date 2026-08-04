"""Regression tests for phone-friendly runtime defaults."""

import os
import subprocess
import sys


def _read_defaults(extra_environment=None):
    environment = os.environ.copy()
    environment.pop("KUZA_CTX", None)
    environment.pop("KUZA_RECURSIVE", None)
    environment.update(extra_environment or {})
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from utils.config import MODEL_CONFIG, RECURSIVE_CONFIG; "
            "print(MODEL_CONFIG['n_ctx']); print(RECURSIVE_CONFIG['enabled'])",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.splitlines()


def test_phone_friendly_defaults_use_one_pass_and_16k_context():
    assert _read_defaults() == ["16384", "False"]


def test_performance_defaults_remain_user_configurable():
    assert _read_defaults({"KUZA_CTX": "32768", "KUZA_RECURSIVE": "1"}) == [
        "32768",
        "True",
    ]
