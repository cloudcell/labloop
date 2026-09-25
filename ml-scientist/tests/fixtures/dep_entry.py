"""Test fixture: a training script that imports a local module by dotted path.

Mirrors the real failure mode: `from depmod.helper import compute` must
resolve through the import machinery when the bundle is content-addressed
(code://) — inlining dep text cannot satisfy a real import statement.
"""

from depmod.helper import compute


def run_training(config):
    return {"metrics": {"acc": compute()}, "variance": {"acc": 0.01}}
