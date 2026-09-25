"""Assessment tool handlers — assess_programme, get_next_experiment."""

from __future__ import annotations

from typing import Annotated
from pydantic import Field

import json

from ..state.store import StateStore
from ..clients.adaptor import MCPAdaptor
from ..enforcement.commitments import check_budget_remaining
from .schemas import fail, ok, AssessProgrammeOut, GetNextExperimentOut
from mcp.types import CallToolResult


def register(mcp, store: StateStore, adaptor: MCPAdaptor) -> None:
    """Register assessment-related tools on the MCP server."""

    @mcp.tool()
    async def assess_programme(programme_id: Annotated[str, Field(description='ID of the target research programme.')]) -> Annotated[CallToolResult, AssessProgrammeOut]:
        """Assess programme health: progressive vs degenerating (commitment 8).

        Reads observations from state.db and best_trials from the optimizer.
        """
        try:
            programme = store.get_programme(programme_id)
            if programme is None:
                return fail(json.dumps({"error": "Programme not found"}))

            trials = store.list_trials(programme_id)
            conclusions = store.list_conclusions(programme_id)
            hypotheses = store.list_hypotheses(programme_id)

            # Query downstream roles for additional data
            best = await adaptor.optimizer.best_trials(
                programme_id, direction=programme.metric_direction
            )

            # Simple heuristic: more completed trials with conclusions = progressive
            completed = [t for t in trials if t.status.value == "completed"]
            total_observations = sum(
                len(store.list_observations(t.id)) for t in completed
            )
            health = (
                "progressive"
                if len(completed) > 0 and len(conclusions) > 0
                else "insufficient-data"
            )

            return ok({
                    "programme_id": programme_id,
                    "health": health,
                    "total_trials": len(trials),
                    "completed_trials": len(completed),
                    "total_hypotheses": len(hypotheses),
                    "total_conclusions": len(conclusions),
                    "best_trials_from_optimizer": best,
                    "total_observations": total_observations,
                })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    async def get_next_experiment(programme_id: Annotated[str, Field(description='ID of the target research programme.')]) -> Annotated[CallToolResult, GetNextExperimentOut]:
        """Get the next experiment configuration from the optimizer role.

        Reports remaining budget (commitment 5: budget is an epistemic resource).
        Enforcement: commitment 5 — rejects when budget is exhausted.
        """
        try:
            programme = store.get_programme(programme_id)
            trials = store.list_trials(programme_id)

            # Enforcement: commitment 5 — budget is an epistemic resource
            err = check_budget_remaining(programme, trials)
            if err:
                return fail(json.dumps(
                    {
                        "error": err,
                        "remaining_budget": {"trials": 0},
                    }
                ))

            remaining_trials = programme.budget_max_trials - len(trials)

            # Call the optimizer role for the next configuration
            next_config = await adaptor.optimizer.ask(programme_id)

            return ok({
                    "programme_id": programme_id,
                    "remaining_budget": {
                        "trials": remaining_trials,
                        "wall_time_hours": programme.budget_max_wall_time_hours,
                    },
                    "next_config": next_config,
                })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))
