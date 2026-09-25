"""Multi-file executor that imports a local module and dispatches a subprocess."""

from utils import compute_metric


def run_training(config: dict) -> dict:
    """Executor that uses a local import AND a subprocess-dispatched script."""
    lr = config.get("lr", 0.001)
    metric = compute_metric(lr)
    return {
        "metrics": {"val_accuracy": metric},
        "variance": {"val_accuracy": 0.01},
    }
