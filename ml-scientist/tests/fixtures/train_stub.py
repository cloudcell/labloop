"""Test fixture: a training script that exposes run_training(config).

Used by tests that need a valid code_ref for capture_bundle.
"""


def run_training(config):
    """Stub training function for tests.

    Returns metrics and variance as required by the executor contract.
    """
    lr = config.get("lr", 0.001)
    # Simulate a metric that depends on lr
    metric = 1.0 - lr * 100
    return {
        "metrics": {"val_accuracy": metric},
        "variance": {"val_accuracy": 0.01},
    }
