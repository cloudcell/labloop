"""Test fixture: probes /tmp isolation between trials.

Reports whether a fixed shared /tmp path existed when this trial
started, then creates it. Under sandboxed execution each trial sees
a private tmpfs, so a later trial must report existed=False even
though an earlier trial wrote the same path.
"""
import os

PROBE = "/tmp/ml_sci_conc_probe.txt"


def run_training(config):
    existed = os.path.exists(PROBE)
    with open(PROBE, "w") as f:
        f.write("x")
    return {
        "metrics": {"probe": 1.0},
        "variance": {"probe": 0.01},
        "_existed": existed,
    }
