"""Test fixture: a training script that reads data from config['data_paths'].

This demonstrates the Data Source integration:
- run_trial resolves DataRefs to read-only paths
- The paths are injected into config['data_paths'] as {split: path}
- The training script reads the data but does not own or mutate it

Used by the e2e demo to test the full data provenance loop.
"""

import csv
import statistics


def run_training(config: dict) -> dict:
    """Run a 'training' trial that reads data from the injected paths.

    Args:
        config: dict with 'lr' (float) and 'data_paths' (dict of split → path).

    Returns:
        {"metrics": {"val_accuracy": float}, "variance": {"val_accuracy": float}}
    """
    lr = config.get("lr", 0.001)
    data_paths = config.get("data_paths", {})

    # If we have data paths, read the data (read-only — we don't write)
    train_rows = 0
    val_rows = 0
    if "train" in data_paths:
        try:
            with open(data_paths["train"]) as f:
                train_rows = sum(1 for _ in csv.reader(f)) - 1  # minus header
        except Exception:
            train_rows = 0
    if "validation" in data_paths:
        try:
            with open(data_paths["validation"]) as f:
                val_rows = sum(1 for _ in csv.reader(f)) - 1
        except Exception:
            val_rows = 0

    # Simulate a metric that depends on lr and data size
    base = 1.0 - lr * 100
    data_bonus = min(train_rows / 1000.0, 0.1)  # more data = better
    metric = base + data_bonus

    # Variance across seeds (simulated)
    seeds = config.get("seeds", [42])
    if len(seeds) > 1:
        results = [metric + (s - 42) * 0.001 for s in seeds]
        se = statistics.stdev(results) / len(results) ** 0.5 if len(results) > 1 else 0.01
    else:
        se = 0.01

    return {
        "metrics": {
            "val_accuracy": round(metric, 4),
            "train_rows": train_rows,
            "val_rows": val_rows,
        },
        "variance": {
            "val_accuracy": round(se, 4),
        },
    }
