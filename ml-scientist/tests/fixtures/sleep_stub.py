"""Test fixture: a training script that outlives the sync wait window.

run_trial waits ~10s synchronously before falling back to async
dispatch; sleeping 30s guarantees the trial is still "running" when
the tool returns — the state needed to test cancel_trial and
mark_retryable.
"""

import time


def run_training(config):
    """Sleeps past the sync window, then returns valid output."""
    time.sleep(30)
    return {"metrics": {"x": 1.0}, "variance": {"x": 0.01}}
