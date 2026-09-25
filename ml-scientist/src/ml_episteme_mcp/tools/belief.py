"""Belief tool handlers — update_belief."""

from __future__ import annotations

from typing import Annotated
from pydantic import Field

import json
import uuid

from ..state.models import Belief
from ..state.store import StateStore
from ..clients.adaptor import MCPAdaptor
from ..enforcement.commitments import (
    check_belief_has_metrics,
    check_memory_precedes_optimization,
    check_programme_active,
)
from .schemas import fail, ok, UpdateBeliefOut
from mcp.types import CallToolResult


def register(mcp, store: StateStore, adaptor: MCPAdaptor) -> None:
    """Register belief-related tools on the MCP server."""

    @mcp.tool()
    async def update_belief(
        programme_id: Annotated[str, Field(description='ID of the target research programme.')], trial_id: Annotated[str, Field(description='ID of the target trial.')], observation_id: Annotated[str, Field(description='ID of the observation the belief update consumes.')]
    ) -> Annotated[CallToolResult, UpdateBeliefOut]:
        """Update the belief state for a programme.

        Calls the optimizer role's tell, then reads the updated posterior.
        Enforcement: commitment 2 — memory precedes optimization.
        Enforcement: commitment 9 — belief is a tracked quantity.
        """
        try:
            # Enforcement: commitment 1 — a closed programme is immutable
            err = check_programme_active(store.get_programme(programme_id))
            if err:
                return fail(json.dumps({"error": err}))

            obs = store.get_observation(observation_id)
            if obs is None:
                return fail(json.dumps({"error": "Observation not found"}))

            # Enforcement: commitment 2 — memory precedes optimization.
            # state.db IS the episodic memory (ADR-0005): the observation
            # must be persisted, bound to this trial, and the trial part
            # of this programme before the optimizer may use it.
            trial = store.get_trial(trial_id)
            err = check_memory_precedes_optimization(
                obs.trial_id, trial_id,
                trial.programme_id if trial else None, programme_id,
            )
            if err:
                return fail(json.dumps({"error": err}))

            # Enforcement: commitment 9 — belief is a tracked quantity
            metrics = json.loads(obs.metrics_json)
            err = check_belief_has_metrics(metrics)
            if err:
                return fail(json.dumps({"error": err}))

            # Call the optimizer role's tell with the trial result
            await adaptor.optimizer.tell(programme_id, trial_id, metrics)

            # Read the updated posterior from the optimizer
            programme = store.get_programme(programme_id)
            direction = programme.metric_direction if programme else "maximize"
            best = await adaptor.optimizer.best_trials(
                programme_id, direction=direction
            )
            importance = await adaptor.optimizer.param_importance(programme_id)

            belief = Belief(
                id=f"belief-{uuid.uuid4().hex[:8]}",
                programme_id=programme_id,
                state_json=json.dumps(
                    {
                        "updated_from_observation": observation_id,
                        "metrics": metrics,
                        "best_trials": best,
                        "param_importance": importance,
                    }
                ),
            )
            store.create_belief(belief)
            return ok({"belief_id": belief.id, "status": "updated"})
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))
