"""Hypothesis tool handlers — formulate_hypothesis, list_hypotheses."""

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
    check_programme_active,
)
from .schemas import coerce_json, fail, ok, FormulateHypothesisOut, ListHypothesesOut
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
            return ok({"hypothesis_id": hypothesis.id, "status": "created"})
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
