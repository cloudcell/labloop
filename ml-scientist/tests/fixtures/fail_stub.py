"""Test fixture: a training script whose run_training raises.

Used by tests that need the executor to report a real failure
(trial transitions to failed, not completed).
"""


def run_training(config):
    """Always fails — for exercising the failure transition."""
    raise RuntimeError("deliberate training failure for transition tests")
