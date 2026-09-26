"""Meta-contract and meta-change-proposal tools.

create_meta_contract mints versioned, immutable evaluation contracts;
propose_meta_change runs the ADR-0003 admission gate at ingress —
before any tournament may reference the proposal. Rejected proposals
are stored, not dropped: "examined and refused" is part of the
recursive loop's trail.
"""

from __future__ import annotations

import json
import uuid

from ..enforcement.checks import (
    admission_verdict,
    check_contract_valid,
    check_metric_name_drift,
    check_proposal_fields,
    classify_class_map,
)
from ..state.models import (
    MetaChangeProposal,
    MetaContract,
    ProposalStatus,
)
from ..state.store import ImproverStore
from .schemas import coerce_json, fail, ok, CreateMetaContractOut, GetProposalOut, ListProposalsOut, ProposeMetaChangeOut
from typing import Annotated, Literal
from pydantic import Field
from mcp.types import CallToolResult


def _proposal_json(store: ImproverStore, p: MetaChangeProposal) -> dict:
    return {
        "id": p.id,
        "proposer_improver_id": p.proposer_improver_id,
        "spec_delta": p.spec_delta,
        "class_map": p.class_map,
        "classification": classify_class_map(p.class_map),
        "expected_benefit": p.expected_benefit,
        "falsification": p.falsification,
        "budget": p.budget,
        "rollback_plan": p.rollback_plan,
        "status": p.status.value,
        "rejection_reason": p.rejection_reason,
        "created_at": p.created_at,
        "evidence_refs": [
            {
                "id": r.id, "source": r.source.value, "tool": r.tool,
                "ref_ids": r.ref_ids, "created_at": r.created_at,
            }
            for r in store.list_evidence_refs("proposal", p.id)
        ],
    }


def register(mcp, store: ImproverStore, adaptors) -> None:
    """Register contract + proposal tools on the MCP server."""

    @mcp.tool()
    def create_meta_contract(
        metrics: Annotated[dict | str, Field(description="Declares 'primary_metric' (what recursive_gain is computed over) + optional 'direction' (max|min) and secondaries; may be JSON-encoded.")],
        promotion_policy: Annotated[dict | str, Field(description='Pre-registered promotion rule (confidence bar, safety gates); object or JSON-encoded.')],
        holdouts: Annotated[dict | str | None, Field(description='Held-out evaluation slices the contract declares; object or JSON-encoded.')] = None,
        budget: Annotated[dict | str | None, Field(description='Budget recorded on the meta-contract; object or JSON-encoded.')] = None,
    ) -> Annotated[CallToolResult, CreateMetaContractOut]:
        """Mint a versioned meta-evaluation contract.

        metrics: must declare 'primary_metric' (the name recursive_gain
        is computed over) and may declare 'direction' (max|min,
        default max) plus secondary metrics. promotion_policy: the
        pre-registered promotion rule (confidence bar, safety gates).
        Contracts are immutable — a new create mints the next version;
        open_tournament freezes the version it runs under.
        """
        try:
            metrics = coerce_json(metrics, dict, "metrics")
            promotion_policy = coerce_json(
                promotion_policy, dict, "promotion_policy"
            )
            if holdouts is not None:
                holdouts = coerce_json(holdouts, dict, "holdouts")
            if budget is not None:
                budget = coerce_json(budget, dict, "budget")
            if err := check_contract_valid(metrics):
                return fail(json.dumps({"error": err}))
            if not promotion_policy:
                return fail(json.dumps({
                    "error": "promotion_policy must be non-empty — "
                    "the promotion rule is pre-registered, not "
                    "invented at decision time."
                }))

            contract = MetaContract(
                id=f"mcontract-{uuid.uuid4().hex[:8]}",
                version=store.next_contract_version(),
                metrics=metrics,
                promotion_policy=promotion_policy,
                holdouts=holdouts,
                budget=budget,
            )
            lint = check_metric_name_drift(store, metrics)
            store.create_meta_contract(contract)
            payload = {
                "contract_id": contract.id,
                "version": contract.version,
            }
            if lint:
                payload["lint"] = lint
            return ok(payload)
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def propose_meta_change(
        proposer_improver_id: Annotated[str, Field(description='ID of the improver submitting the meta-change.')],
        spec_delta: Annotated[dict | str, Field(description='The typed change content (OntoArch-style delta); object or JSON-encoded.')],
        class_map: Annotated[dict | str, Field(description='{component_name: declared_boundary_class} for every component the delta touches.')],
        expected_benefit: Annotated[str, Field(description="Why the change should help — the proposal's claim.")],
        falsification: Annotated[str, Field(description='What evidence would refute the claimed benefit.')],
        rollback_plan: Annotated[str, Field(description='How to undo the change if it regresses.')],
        budget: Annotated[dict | str | None, Field(description='Budget the proposal asks for; object or JSON-encoded.')] = None,
    ) -> Annotated[CallToolResult, ProposeMetaChangeOut]:
        """Submit a typed meta-change proposal to the admission gate.

        spec_delta: the typed change (OntoArch-style delta content).
        class_map: {component_name: declared_boundary_class} for every
        component the delta touches — the kernel classifies
        authoritatively from the ADR-0003 table; the declaration is
        kept for the record.

        Admission: any class-1 (immutable/human-governed) component
        touch → status 'rejected' at ingress, stored with the reason.
        Any class-3 (conditionally modifiable) or unknown component →
        'conditional' — promotable only behind a human decided_by.
        Otherwise 'admitted'. Rejection is durable, not silent.
        """
        try:
            if store.get_improver(proposer_improver_id) is None:
                return fail(json.dumps({
                    "error": f"Proposer improver not found: "
                    f"{proposer_improver_id}. Only a registered "
                    "improver version can propose meta-changes."
                }))
            spec_delta = coerce_json(spec_delta, dict, "spec_delta")
            class_map = coerce_json(class_map, dict, "class_map")
            if budget is not None:
                budget = coerce_json(budget, dict, "budget")
            if err := check_proposal_fields(
                class_map, expected_benefit, falsification, rollback_plan
            ):
                return fail(json.dumps({"error": err}))

            classification = classify_class_map(class_map)
            status, reason = admission_verdict(classification)

            proposal = MetaChangeProposal(
                id=f"mcp-{uuid.uuid4().hex[:8]}",
                proposer_improver_id=proposer_improver_id,
                spec_delta=spec_delta,
                class_map=class_map,
                expected_benefit=expected_benefit,
                falsification=falsification,
                budget=budget,
                rollback_plan=rollback_plan,
                status=ProposalStatus(status),
                rejection_reason=reason,
            )
            store.create_proposal(proposal)

            payload = {
                "proposal_id": proposal.id,
                "status": proposal.status.value,
                "classification": classification,
            }
            if reason:
                payload["rejection_reason"] = reason
            if proposal.status == ProposalStatus.conditional:
                payload["conditional_note"] = (
                    "Class-3 or unknown component touched — promotion "
                    "will require a promote decision whose decided_by "
                    "names a human authority ('human:<name>')."
                )
            return ok(payload)
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def get_proposal(proposal_id: Annotated[str, Field(description='ID of the proposal to read.')]) -> Annotated[CallToolResult, GetProposalOut]:
        """Read a proposal with its authoritative classification and
        evidence trail."""
        try:
            p = store.get_proposal(proposal_id)
            if p is None:
                return fail(json.dumps({
                    "error": f"Proposal not found: {proposal_id}"
                }))
            return ok({"proposal": _proposal_json(store, p)})
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def list_proposals(
        status: Annotated[Literal['admitted', 'conditional', 'rejected', 'superseded'] | None, Field(description='Optional status filter — rejected proposals are part of the record.')] = None,
        limit: Annotated[int, Field(description='Max rows to return (pagination).')] = 50,
        offset: Annotated[int, Field(description='Rows to skip before returning (pagination).')] = 0,
    ) -> Annotated[CallToolResult, ListProposalsOut]:
        """List proposals, newest first.

        status filter: admitted | conditional | rejected | superseded.
        Rejected proposals are listed like any other — admission
        refusals are part of the record.
        """
        try:
            if status is not None and status not in {
                s.value for s in ProposalStatus
            }:
                return fail(json.dumps({
                    "error": "status must be one of "
                    f"{'|'.join(s.value for s in ProposalStatus)}, "
                    f"got: {status}"
                }))
            proposals, total = store.list_proposals(
                status=status, limit=limit, offset=offset
            )
            return ok({
                "proposals": [
                    _proposal_json(store, p) for p in proposals
                ],
                "total": total,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))
