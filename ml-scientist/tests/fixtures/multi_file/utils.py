"""Local utility module imported by executor.py (discoverable by AST)."""


def compute_metric(lr: float) -> float:
    """Compute a simple metric from learning rate."""
    return 1.0 - lr * 100
