"""Test fixture: a generator script that exposes generate_data(config, output_path).

Used by the prepare_data tool to test generated data provenance.

The generator writes a small CSV dataset to output_path. The data depends
on the seed and n_samples parameters so it's reproducible.
"""

import csv
import random


def generate_data(config: dict, output_path: str) -> None:
    """Generate a small CSV dataset and write it to output_path.

    Args:
        config: dict with 'seed' (int) and 'n_samples' (int) keys.
        output_path: where to write the CSV file.
    """
    seed = config.get("seed", 42)
    n_samples = config.get("n_samples", 100)
    rng = random.Random(seed)

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["x", "y", "label"])
        for i in range(n_samples):
            x = rng.gauss(0, 1)
            y = rng.gauss(0, 1)
            label = 1 if x + y > 0 else 0
            writer.writerow([round(x, 4), round(y, 4), label])
