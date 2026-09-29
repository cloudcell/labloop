"""Hypothesis tool handlers — formulate_hypothesis, list_hypotheses, abandon_hypothesis."""

from __future__ import annotations

from typing import Annotated
from pydantic import Field

import json
import uuid

from ..state.models import Hypothesis
from ..state.store import StateStore
from ..clients.adaptor import MCPAdaptor
from ..enforcement.commitments import (
    check_falsifiability,
    check_hypothesis_in_programme,
    check_programme_active,
)
from .schemas import (
    AbandonHypothesisOut,
    coerce_json,
    fail,
    ok,
    FormulateHypothesisOut,
    ListHypothesesOut,
)
from mcp.types import CallToolResult


def register(mcp, store: StateStore, adaptor: MCPAdaptor) -> None:
    """Register hypothesis-related tools on the MCP server."""

    @mcp.tool()
    def formulate_hypothesis(
        programme_id: Annotated[str, Field(description='ID of the target research programme.')],
        statement: Annotated[str, Field(description='The falsifiable claim under test.')],
        failure_criterion: Annotated[str, Field(description='What observation would falsify the hypothesis — required (commitment 3).')],
        variables_involved: Annotated[list[str] | str, Field(description='Variables the hypothesis ranges over; list or JSON-encoded list.')],
    ) -> Annotated[CallToolResult, FormulateHypothesisOut]:
        """Formulate a falsifiable hypothesis (commitment 3).

        Rejects a hypothesis with no failure criterion.
        variables_involved may be sent as a JSON-encoded string list.
        """
        try:
            variables_involved = coerce_json(variables_involved, list, "variables_involved")
            # Enforcement: commitment 3 — falsifiability required for admission
            err = check_falsifiability(failure_criterion)
            if err:
                return fail(json.dumps({"error": err}))

            # Enforcement: commitment 1 — the loop is the unit; the
            # programme must exist and still be active (a closed
            # programme is immutable).
            err = check_programme_active(store.get_programme(programme_id))
            if err:
                return fail(json.dumps({"error": err}))

            hypothesis = Hypothesis(
                id=f"hyp-{uuid.uuid4().hex[:8]}",
                programme_id=programme_id,
                statement=statement,
                failure_criterion=failure_criterion,
                variables_involved=variables_involved,
            )
            store.create_hypothesis(hypothesis)
            return ok({"hypothesis_id": hypothesis.id, "status": hypothesis.status.value})
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def abandon_hypothesis(
        programme_id: Annotated[str, Field(description='ID of the owning research programme.')],
        hypothesis_id: Annotated[str, Field(description='ID of the hypothesis to abandon.')],
        rationale: Annotated[str, Field(description='Non-empty justification — why this hypothesis leaves the loop without a verdict.')],
        decided_by: Annotated[str, Field(description="Attributable decider — 'human:<name>' or an agent identity.")],
    ) -> Annotated[CallToolResult, AbandonHypothesisOut]:
        """Abandon a hypothesis — attributed, rationaled, never erased.

        The exit for a hypothesis that can never conclude: falsified
        premises, superseded questions, trials that can only fail.
        Abandoning is NOT a verdict — no conclusion is recorded and no
        claim is minted; the hypothesis simply leaves the loop as
        'abandoned', stamped with who decided and why. Trials already
        recorded stay on the record.

        Applies to proposed and under_test hypotheses; terminal states
        (accepted|rejected|inconclusive|abandoned) refuse. To abandon
        every hypothesis in a programme at once, use
        close_programme(status="abandoned").
        """
        try:
            rationale = (rationale or "").strip()
            if not rationale:
                return fail(json.dumps({
                    "error": "abandon_hypothesis requires a non-empty "
                             "rationale — governance acts are accountable."
                }))
            decided_by = (decided_by or "").strip()
            if not decided_by:
                return fail(json.dumps({
                    "error": "abandon_hypothesis requires decided_by — "
                             "governance acts are attributable."
                }))

            # Enforcement: commitment 10 — hypothesis exists and belongs
            # to the programme (the agent acts on real records)
            err = check_hypothesis_in_programme(programme_id, hypothesis_id, store)
            if err:
                return fail(json.dumps({"error": err}))

            # Enforcement: commitment 1 — a closed programme is immutable
            err = check_programme_active(store.get_programme(programme_id))
            if err:
                return fail(json.dumps({"error": err}))

            hypothesis = store.get_hypothesis(hypothesis_id)
            from_status = hypothesis.status.value
            store.abandon_hypothesis(hypothesis_id, rationale, decided_by)
            return ok({
                "hypothesis_id": hypothesis_id,
                "status": "abandoned",
                "from_status": from_status,
                "decided_by": decided_by,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def list_hypotheses(programme_id: Annotated[str, Field(description='ID of the target research programme.')]) -> Annotated[CallToolResult, ListHypothesesOut]:
        """List all hypotheses in a programme with their status.

        Read-only enumeration — the tool-level counterpart of the
        programme://{id}/hypotheses resource. Use it to recover a
        hypothesis id (e.g. in a new session) rather than querying
        the state database directly.
        """
        try:
            if store.get_programme(programme_id) is None:
                return fail(json.dumps({"error": f"Programme not found: {programme_id}"}))
            hyps = store.list_hypotheses(programme_id)
            return ok({
                    "programme_id": programme_id,
                    "hypotheses": [
                        {
                            "id": h.id,
                            "statement": h.statement,
                            "failure_criterion": h.failure_criterion,
                            "status": h.status.value,
                        }
                        for h in hyps
                    ],
                })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))
