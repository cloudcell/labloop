"""Test fixture: run_training with a hardcoded shared-scratch path.

The capture-time lint should flag the '/tmp/...' literal in the
capture_bundle response warnings — concurrent trials would race
on this file.
"""


def run_training(config):
    cfg_path = "/tmp/ml_sci_shared_cfg.json"
    with open(cfg_path, "w") as f:
        f.write("{}")
    return {
        "metrics": {"acc": 0.5},
        "variance": {"acc": 0.01},
    }
