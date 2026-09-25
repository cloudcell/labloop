"""Claim tool handlers — assert_claim, relate, get_claim, list_claims.

Claims are hypotheses that outlive their programme. The evidence rule
(check_evidence_requirement) is the integrity core: unsupported claims
are capped at prior-level confidence. Claim + evidence edges insert in
one transaction — a claim never exists without its asserted evidence.
"""

from __future__ import annotations

import json
import uuid

from ..enforcement.checks import (
    PRIOR_CONFIDENCE_MAX,
    check_claim_type_valid,
    check_edge_valid,
    check_evidence_requirement,
    check_ref_type_valid,
    check_relation_valid,
    check_supersedes_exists,
)
from ..state.models import Claim, ClaimEdge, ClaimType, RefType, Relation
from ..state.store import MemoryStore, content_hash
from .schemas import coerce_json, fail, ok, AssertClaimOut, GetClaimOut, ListClaimsOut, RelateOut
from typing import Annotated, Literal
from pydantic import Field
from mcp.types import CallToolResult


def register(
    mcp,
    store: MemoryStore,
    prior_confidence_max: float = PRIOR_CONFIDENCE_MAX,
) -> None:
    """Register claim-graph tools on the MCP server.

    prior_confidence_max is the configured prior ceiling
    ([integrity] prior_confidence_max) — the same value the
    unsupported_high_confidence audit checks against."""

    @mcp.tool()
    def assert_claim(
        content: Annotated[str, Field(description='The claim text — deduplicated by normalized content (re-asserting identical content returns the existing claim_id).')],
        type: Annotated[Literal['empirical', 'methodological'], Field(description='empirical | methodological.')],
        confidence: Annotated[float, Field(description='0–1 — above the prior ceiling (0.3) requires ≥1 evidence-bearing edge.')],
        importance: Annotated[float, Field(description='Relative importance 0–1.')] = 0.5,
        evidence: Annotated[list | str | None, Field(description='List of {to_ref, ref_type, relation} dicts linking the claim to its support (e.g. {"to_ref": "trial-abc", "ref_type": "trial", "relation": "tested_by"}); may be JSON-encoded. Without ≥1 evidence-bearing edge, confidence is capped at 0.3.')] = None,
        supersedes_id: Annotated[str | None, Field(description='ID of an existing claim this one replaces — the supersedes edge is recorded automatically.')] = None,
        source_id: Annotated[str | None, Field(description='Provenance tag — the entity (decision/investigation) that minted this claim.')] = None,
    ) -> Annotated[CallToolResult, AssertClaimOut]:
        """Assert a claim into the memory graph.

        type: empirical | methodological. evidence is a list of
        {to_ref, ref_type, relation} dicts linking this claim to its
        support (e.g. {"to_ref": "trial-abc", "ref_type": "trial",
        "relation": "tested_by"}); may be sent as a JSON-encoded string.

        Evidence rule: without at least one evidence-bearing edge
        (supports|derived_from|tested_by|valid_under), confidence is
        capped at the prior ceiling (0.3). supersedes_id names an
        existing claim this one replaces; the supersedes edge is
        recorded automatically. Claims are deduplicated by normalized
        content — re-asserting identical content returns the existing
        claim_id.
        """
        try:
            if evidence is not None:
                evidence = coerce_json(evidence, list, "evidence")

            # Dedup first: re-asserting existing content is a lookup,
            # not a new claim — idempotent retries must not be gated
            # by the evidence rule.
            chash = content_hash(content)
            existing = store.get_claim_by_hash(chash)
            if existing is not None:
                return ok({
                    "claim_id": existing.id,
                    "deduplicated": True,
                    "status": "exists",
                })

            for err in (
                check_claim_type_valid(type),
                check_evidence_requirement(
                    evidence, confidence, prior_max=prior_confidence_max
                ),
                check_supersedes_exists(supersedes_id, store),
            ):
                if err:
                    return fail(json.dumps({"error": err}))

            # Validate every evidence edge spec before inserting anything
            evidence = evidence or []
            for e in evidence:
                for err in (
                    check_ref_type_valid(e.get("ref_type", "")),
                    check_relation_valid(e.get("relation", "")),
                ):
                    if err:
                        return fail(json.dumps({"error": err}))
                # claim-typed refs are verified (same rule as relate);
                # other ref types are trusted opaque IDs
                if (
                    e.get("ref_type") == "claim"
                    and store.get_claim(e.get("to_ref", "")) is None
                ):
                    return fail(json.dumps({
                        "error": f"Claim-typed to_ref not found: {e.get('to_ref')}"
                    }))

            claim = Claim(
                id=f"claim-{uuid.uuid4().hex[:8]}",
                content=content,
                type=ClaimType(type),
                confidence=confidence,
                importance=importance,
                supersedes_id=supersedes_id,
                content_hash=chash,
                source_id=source_id,
            )

            edge_specs = list(evidence)
            # Review note: the supersedes_id column and a 'supersedes'
            # edge record one fact — write both atomically.
            if supersedes_id is not None:
                edge_specs.append({
                    "to_ref": supersedes_id,
                    "ref_type": "claim",
                    "relation": "supersedes",
                })

            with store.transaction():
                store.create_claim(claim)
                edge_ids = []
                for e in edge_specs:
                    edge = ClaimEdge(
                        id=f"edge-{uuid.uuid4().hex[:8]}",
                        from_claim=claim.id,
                        to_ref=e["to_ref"],
                        ref_type=RefType(e["ref_type"]),
                        relation=Relation(e["relation"]),
                        weight=e.get("weight", 1.0),
                        source_id=e.get("source_id", source_id),
                    )
                    store.create_edge(edge)
                    edge_ids.append(edge.id)

            return ok({
                "claim_id": claim.id,
                "edge_ids": edge_ids,
                "status": "asserted",
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def relate(
        from_claim: Annotated[str, Field(description='ID of the claim the edge starts from.')],
        to_ref: Annotated[str, Field(description='Edge target — claim-typed IDs are verified; other ref types are trusted opaque IDs.')],
        ref_type: Annotated[Literal['claim', 'trial', 'observation', 'conclusion', 'programme', 'external'], Field(description='Type of to_ref: claim | trial | observation | conclusion | programme | external.')],
        relation: Annotated[Literal['supports', 'contradicts', 'derived_from', 'tested_by', 'valid_under', 'supersedes', 'generalizes', 'specializes', 'similar_to', 'failed_because'], Field(description='Edge type: supports | contradicts | derived_from | tested_by | valid_under | supersedes | generalizes | specializes | similar_to | failed_because.')],
        weight: Annotated[float, Field(description='Edge strength (default 1.0).')] = 1.0,
        source_id: Annotated[str | None, Field(description='Provenance tag — the entity that recorded this edge.')] = None,
    ) -> Annotated[CallToolResult, RelateOut]:
        """Link a claim to evidence or another claim.

        relation (closed vocabulary): supports | contradicts |
        derived_from | tested_by | valid_under | supersedes |
        generalizes | specializes | similar_to | failed_because.
        ref_type: claim | trial | observation | conclusion | programme |
        external. claim-typed to_ref values are verified; other ref
        types are trusted opaque IDs across the protocol boundary.
        Exact duplicate edges return the existing edge_id.
        """
        try:
            for err in (
                check_relation_valid(relation),
                check_ref_type_valid(ref_type),
                check_edge_valid(from_claim, to_ref, ref_type, store),
            ):
                if err:
                    return fail(json.dumps({"error": err}))

            existing = store.find_edge(from_claim, to_ref, ref_type, relation)
            if existing is not None:
                return ok({
                    "edge_id": existing.id,
                    "deduplicated": True,
                    "status": "exists",
                })

            edge = ClaimEdge(
                id=f"edge-{uuid.uuid4().hex[:8]}",
                from_claim=from_claim,
                to_ref=to_ref,
                ref_type=RefType(ref_type),
                relation=Relation(relation),
                weight=weight,
                source_id=source_id,
            )
            store.create_edge(edge)
            store.conn.commit()
            return ok({"edge_id": edge.id, "status": "created"})
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def get_claim(claim_id: Annotated[str, Field(description='ID of the claim to read.')]) -> Annotated[CallToolResult, GetClaimOut]:
        """Read a claim with its full provenance bundle.

        Returns the claim plus outgoing edges (what it asserts/cites)
        and incoming edges (what cites it) — a claim is never returned
        as bare text; the evidence trail travels with it.
        """
        try:
            claim = store.get_claim(claim_id)
            if claim is None:
                return fail(json.dumps({"error": f"Claim not found: {claim_id}"}))
            out = [
                {
                    "edge_id": e.id,
                    "to_ref": e.to_ref,
                    "ref_type": e.ref_type.value,
                    "relation": e.relation.value,
                    "weight": e.weight,
                }
                for e in store.edges_from(claim_id)
            ]
            inc = [
                {
                    "edge_id": e.id,
                    "from_claim": e.from_claim,
                    "relation": e.relation.value,
                    "weight": e.weight,
                }
                for e in store.edges_to(claim_id)
            ]
            return ok({
                "claim": {
                    "id": claim.id,
                    "content": claim.content,
                    "type": claim.type.value,
                    "confidence": claim.confidence,
                    "importance": claim.importance,
                    "valid_from": claim.valid_from,
                    "valid_until": claim.valid_until,
                    "supersedes_id": claim.supersedes_id,
                    "source_id": claim.source_id,
                    "created_at": claim.created_at,
                },
                "outgoing_edges": out,
                "incoming_edges": inc,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def list_claims(
        type: Annotated[Literal['empirical', 'methodological'] | None, Field(description='Optional filter: empirical | methodological.')] = None,
        include_superseded: Annotated[bool, Field(description='Include claims replaced by newer ones (default hidden — the live view).')] = False,
        include_expired: Annotated[bool, Field(description='Include claims past valid_until (default hidden — the live view).')] = False,
        limit: Annotated[int, Field(description='Max rows to return (pagination).')] = 50,
        offset: Annotated[int, Field(description='Rows to skip before returning (pagination).')] = 0,
    ) -> Annotated[CallToolResult, ListClaimsOut]:
        """List claims, newest first, with filters.

        include_superseded=False hides claims replaced by a newer claim;
        include_expired=False hides claims past valid_until. Both default
        to the live memory view — what the ecosystem currently believes.
        """
        try:
            if type is not None:
                err = check_claim_type_valid(type)
                if err:
                    return fail(json.dumps({"error": err}))
            claims, total = store.list_claims(
                type=type,
                include_superseded=include_superseded,
                include_expired=include_expired,
                limit=limit,
                offset=offset,
            )
            return ok({
                "claims": [
                    {
                        "id": c.id,
                        "content": c.content,
                        "type": c.type.value,
                        "confidence": c.confidence,
                        "importance": c.importance,
                        "valid_until": c.valid_until,
                        "supersedes_id": c.supersedes_id,
                        "created_at": c.created_at,
                    }
                    for c in claims
                ],
                "total": total,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))
