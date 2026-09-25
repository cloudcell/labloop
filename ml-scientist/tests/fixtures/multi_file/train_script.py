"""Subprocess-dispatched script (NOT discoverable by AST import analysis).

This file is invoked via subprocess.run() in some executors.
It must be explicitly declared via extra_code_refs to be captured.
"""

import json
import sys


def main():
    config = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    lr = config.get("lr", 0.001)
    result = {"metrics": {"val_accuracy": 1.0 - lr * 100}, "variance": {"val_accuracy": 0.01}}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
