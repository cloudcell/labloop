"""Test fixture: a training script that reports progress mid-run.

Writes progress.json to $ML_SCI_ARTIFACT_DIR (the documented progress
convention), then sleeps past the sync wait window so get_trial_status
observes a live running trial with progress + ETA telemetry.
"""

import json
import os
import time
from pathlib import Path


def run_training(config):
    """Emit a progress.json, then outlive the sync window."""
    artifact_dir = Path(os.environ["ML_SCI_ARTIFACT_DIR"])
    (artifact_dir / "progress.json").write_text(json.dumps({
        "pct": 0.5,
        "message": "seed 1 of 2 done",
    }))
    time.sleep(15)
    return {"metrics": {"x": 1.0}, "variance": {"x": 0.01}}
