"""Local optimizer — real HPO using random search with improvement tracking.

This is a real optimizer that implements:
- Random search over a continuous search space
- Best-trial tracking (maximizes the primary metric)
- Parameter importance via simple variance analysis

No external services needed. For production-grade HPO (TPE, CMA-ES, etc.),
swap this for an MCP-backed optimizer adaptor.
"""

from __future__ import annotations

import json
import random
from typing import Any

from .roles import OptimizerRole


# Default search ranges for common ML hyperparameters.
# The optimizer samples uniformly from these ranges unless a programme
# specifies custom ranges (future enhancement).
DEFAULT_RANGES: dict[str, tuple[float, float]] = {
    "learning_rate": (1e-5, 1e-1),
    "lr": (1e-5, 1e-1),
    "batch_size": (8.0, 256.0),
    "dropout": (0.0, 0.5),
    "weight_decay": (1e-6, 1e-1),
    "warmup_steps": (0.0, 1000.0),
    "label_smoothing": (0.0, 0.2),
    "depth": (1.0, 24.0),
    "num_layers": (1.0, 24.0),
    "hidden_size": (64.0, 2048.0),
    "d_model": (64.0, 2048.0),
    "n_heads": (1.0, 16.0),
    "momentum": (0.0, 0.99),
    "beta1": (0.0, 0.999),
    "beta2": (0.0, 0.9999),
    "epochs": (1.0, 100.0),
    "max_steps": (100.0, 100000.0),
}


def _log_uniform(rng: random.Random, low: float, high: float) -> float:
    """Sample from a log-uniform distribution (for learning rates, etc.)."""
    import math
    log_low = math.log(low)
    log_high = math.log(high)
    return math.exp(rng.uniform(log_low, log_high))


def _uniform(rng: random.Random, low: float, high: float) -> float:
    """Sample from a uniform distribution."""
    return rng.uniform(low, high)


# Parameters that should be sampled log-uniformly.
LOG_UNIFORM_PARAMS = {"learning_rate", "lr", "weight_decay", "batch_size"}


class LocalOptimizer(OptimizerRole):
    """Real optimizer using random search with best-trial tracking.

    Implements:
    - create_study: registers the search space (variable names)
    - ask: samples a new configuration using random search
    - tell: records the trial result
    - best_trials: returns the best trial(s) by primary metric
    - param_importance: estimates importance via variance of results
      across parameter values
    """

    def __init__(self, seed: int | None = None):
        self._studies: dict[str, list[str]] = {}
        self._directions: dict[str, str] = {}
        self._trial_configs: dict[str, list[dict[str, Any]]] = {}
        self._trial_results: dict[str, list[dict[str, float]]] = {}
        self._trial_ids: dict[str, list[str]] = {}
        self._ask_count: dict[str, int] = {}
        self._rng = random.Random(seed)

    async def create_study(
        self, programme_id: str, variables: list[str], direction: str = "maximize"
    ) -> str:
        """Initialize the optimization study."""
        study_id = f"study-{programme_id}"
        self._studies[programme_id] = variables
        self._directions[programme_id] = direction
        self._trial_configs[programme_id] = []
        self._trial_results[programme_id] = []
        self._trial_ids[programme_id] = []
        self._ask_count[programme_id] = 0
        return study_id

    async def ask(self, programme_id: str) -> dict[str, Any]:
        """Sample a new configuration using random search."""
        self._ask_count[programme_id] = self._ask_count.get(programme_id, 0) + 1
        variables = self._studies.get(programme_id, ["learning_rate"])

        config: dict[str, Any] = {}
        for v in variables:
            if v in DEFAULT_RANGES:
                low, high = DEFAULT_RANGES[v]
                if v in LOG_UNIFORM_PARAMS:
                    config[v] = round(_log_uniform(self._rng, low, high), 8)
                else:
                    val = _uniform(self._rng, low, high)
                    # Round integer-valued params
                    if v in ("depth", "num_layers", "n_heads", "batch_size",
                             "hidden_size", "d_model", "epochs", "max_steps",
                             "warmup_steps"):
                        config[v] = int(round(val))
                    else:
                        config[v] = round(val, 8)
            else:
                # Unknown parameter — sample from a generic range
                config[v] = round(_uniform(self._rng, 0.0, 1.0), 4)

        config["_trial_number"] = self._ask_count[programme_id]
        self._trial_configs.setdefault(programme_id, []).append(config)
        return config

    async def tell(
        self, programme_id: str, trial_id: str, result: dict[str, float]
    ) -> None:
        """Record the trial result."""
        self._trial_results.setdefault(programme_id, []).append(result)
        self._trial_ids.setdefault(programme_id, []).append(trial_id)

    async def best_trials(
        self, programme_id: str, direction: str | None = None
    ) -> list[dict[str, Any]]:
        """Return the best trial(s) by the primary metric.

        The primary metric is the first key in the result dict.
        Direction is set at create_study or overridden via the direction arg:
        'minimize' picks the lowest, 'maximize' picks the highest.
        """
        results = self._trial_results.get(programme_id, [])
        trial_ids = self._trial_ids.get(programme_id, [])
        direction = direction or self._directions.get(programme_id, "maximize")

        if not results:
            return []

        # Find the best by the first metric
        primary_key = list(results[0].keys())[0] if results else None
        if primary_key is None:
            return []

        if direction == "minimize":
            best_idx = max(
                range(len(results)),
                key=lambda i: -results[i].get(primary_key, float("inf")),
            )
        else:
            best_idx = max(
                range(len(results)),
                key=lambda i: results[i].get(primary_key, float("-inf")),
            )

        return [
            {
                "trial_id": trial_ids[best_idx] if best_idx < len(trial_ids) else None,
                "result": results[best_idx],
                "primary_metric": primary_key,
                "direction": direction,
            }
        ]

    async def param_importance(self, programme_id: str) -> dict[str, float]:
        """Estimate parameter importance via variance analysis.

        For each parameter, compute the correlation between the parameter
        value and the primary metric across all completed trials.
        Returns absolute correlation as importance (0 to 1).
        """
        variables = self._studies.get(programme_id, [])
        results = self._trial_results.get(programme_id, [])
        configs = self._trial_configs.get(programme_id, [])

        if not results or not configs or not variables:
            # Uniform importance as fallback
            return {v: 1.0 / len(variables) for v in variables} if variables else {}

        # Get the primary metric
        primary_key = list(results[0].keys())[0] if results else None
        if primary_key is None:
            return {v: 1.0 / len(variables) for v in variables}

        metric_values = [r.get(primary_key, 0.0) for r in results]

        importance: dict[str, float] = {}
        for v in variables:
            param_values = [c.get(v, 0.0) for c in configs]
            corr = _abs_correlation(param_values, metric_values)
            importance[v] = round(corr, 4)

        # Normalize so they sum to 1
        total = sum(importance.values())
        if total > 0:
            importance = {k: v / total for k, v in importance.items()}
        else:
            importance = {v: 1.0 / len(variables) for v in variables}

        return importance


def _abs_correlation(x: list[float], y: list[float]) -> float:
    """Compute the absolute Pearson correlation coefficient.

    Pairs elements by position — the i-th sampled config pairs with
    the i-th told result. When ask/tell counts differ the shorter
    side truncates, so all statistics are computed over the paired
    prefix only.
    """
    pairs = list(zip(x, y))
    n = len(pairs)
    if n < 2:
        return 0.0

    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n

    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in pairs)
    var_x = sum((xi - mean_x) ** 2 for xi in xs)
    var_y = sum((yi - mean_y) ** 2 for yi in ys)

    if var_x == 0 or var_y == 0:
        return 0.0

    corr = cov / (var_x ** 0.5 * var_y ** 0.5)
    return abs(corr)
