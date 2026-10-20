"""Portable deterministic suite; never connects to a home or model service."""
from pathlib import Path
import os
import sys
import tempfile
import unittest

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root))
# Test engines share one run-local OS-lock namespace, including child processes.
# This avoids stale Windows ACLs when sandboxed and ordinary workstation runs
# use the same system temp directory. Production fence logic is not patched.
previous_temp = tempfile.tempdir
previous_env = {key: os.environ.get(key) for key in ("TEMP", "TMP", "TMPDIR")}
with tempfile.TemporaryDirectory(prefix="shared-home-tests-") as test_temp:
    try:
        tempfile.tempdir = test_temp
        for key in previous_env:
            os.environ[key] = test_temp
        suite = unittest.defaultTestLoader.discover(str(root / "tests"))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    finally:
        tempfile.tempdir = previous_temp
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
raise SystemExit(not result.wasSuccessful())
