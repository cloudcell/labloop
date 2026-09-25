"""Observation tool handlers — record_observation."""

from __future__ import annotations

from typing import Annotated
from pydantic import Field

import json
import uuid

from ..state.models import Observation
from ..state.store import StateStore
from ..clients.adaptor import MCPAdaptor
from ..enforcement.commitments import (
    check_reproducibility,
    check_observation_prerequisites,
    check_programme_active,
)
from .schemas import coerce_json, fail, ok, RecordObservationOut
from mcp.types import CallToolResult


def register(mcp, store: StateStore, adaptor: MCPAdaptor) -> None:
    """Register observation-related tools on the MCP server."""

    @mcp.tool()
    async def record_observation(
        trial_id: Annotated[str, Field(description='ID of the target trial.')],
        metrics: Annotated[dict[str, float] | str, Field(description='Measured values {metric_name: float}; may be a JSON-encoded string.')],
        variance: Annotated[dict[str, float] | str, Field(description='Per-seed variance {metric: float} — all-zero rejected (commitment 7); may be JSON-encoded.')],
        spatiotemporal_region: Annotated[str, Field(description='Where/when the observation was produced (run footprint tag).')],
    ) -> Annotated[CallToolResult, RecordObservationOut]:
        """Record an observation (data item about a quality).

        metrics and variance may be sent as JSON-encoded strings.

        Only completed trials can be observed — a failed trial produced
        no measurement; its failure lives in its status, executor_output,
        and artifacts, not here. All-zero variance is rejected (n
        identical outcomes are one effective measurement, not
        reproducibility). If nothing is concludable, close the programme
        'abandoned' rather than fabricating variance.

        Enforcement: commitment 7 — reproducibility is the price of admission.
        Rejects a single point estimate with no variance.
        """
        try:
            metrics = coerce_json(metrics, dict, "metrics")
            variance = coerce_json(variance, dict, "variance")
            # Enforcement: commitments 6 + 1 — trial must exist, have a bundle, and be completed
            trial = store.get_trial(trial_id)
            if trial is not None:
                # Enforcement: commitment 1 — a closed programme is immutable
                err = check_programme_active(
                    store.get_programme(trial.programme_id)
                )
                if err:
                    return fail(json.dumps({"error": err}))
            err = check_observation_prerequisites(trial)
            if err:
                return fail(json.dumps({"error": err}))

            # Enforcement: commitment 7 — variance is required
            err = check_reproducibility(variance)
            if err:
                return fail(json.dumps({"error": err}))

            observation = Observation(
                id=f"obs-{uuid.uuid4().hex[:8]}",
                trial_id=trial_id,
                metrics_json=json.dumps(metrics),
                variance_json=json.dumps(variance),
                spatiotemporal_region=spatiotemporal_region,
            )
            store.create_observation(observation)

            return ok({"observation_id": observation.id, "status": "recorded"})
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))
