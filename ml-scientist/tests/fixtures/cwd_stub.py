"""Test fixture: run_training that probes its working directory.

Writes a relative-path file (which must land in the trial's artifact
dir, not the server cwd) and reports os.getcwd() + tempfile's
scratch dir so tests can assert the subprocess ran inside the
artifact workspace with a per-trial TMPDIR.
"""
import os
import tempfile
from pathlib import Path


def run_training(config):
    Path("rel_write.txt").write_text("wrote in cwd")
    scratch = tempfile.mkstemp(suffix=".probe")[1]
    return {
        "metrics": {"probe": 1.0},
        "variance": {"probe": 0.01},
        "_cwd": os.getcwd(),
        "_tmpdir": os.path.dirname(scratch),
    }
