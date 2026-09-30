"""Claim tool handlers — assert_claim, relate, get_claim, list_claims.

Claims are hypotheses that outlive their programme. The numeric
provenance invariant (plan-20260930-0240Z) is the integrity core: a
caller never supplies a confidence value — it supplies evidence edges
and optionally a registered computation, and this server computes or
leaves NULL. Claim + evidence edges insert in one transaction — a
claim never exists without its asserted evidence.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from ..enforcement.checks import (
    check_claim_type_valid,
    check_edge_valid,
    check_ref_type_valid,
    check_ref_type_consistent,
    check_relation_valid,
    check_supersedes_exists,
)
from ..state.models import (
    EVIDENCE_RELATIONS,
    Claim,
    ClaimEdge,
    ClaimType,
    RefType,
    Relation,
    _utc_now,
)
from ..state.store import MemoryStore, content_hash
from .. import _grounded_constants as _gc
from .schemas import coerce_json, fail, ok, AssertClaimOut, GetClaimOut, GetClaimsOut, ListClaimsOut, RelateOut
from typing import Annotated, Literal
from pydantic import Field
from mcp.types import CallToolResult


def register(
    mcp,
    store: MemoryStore,
) -> None:
    """Register claim-graph tools on the MCP server."""

    @mcp.tool()
    def assert_claim(
        content: Annotated[str, Field(description='The claim text — deduplicated by normalized content (re-asserting identical content returns the existing claim_id).')],
        type: Annotated[Literal['empirical', 'methodological'], Field(description='empirical | methodological.')],
        confidence_computation: Annotated[dict | str | None, Field(description='Optional derivation record {procedure, inputs, evidence_refs?} — e.g. {"procedure": "posterior_from_2lnbf", "inputs": {"prior": 0.3, "bf_2ln": 2.71}}; may be JSON-encoded. The ONLY way a claim stores a numeric confidence: this server recomputes the registered procedure on the given inputs and stores the result plus the computation. Requires ≥1 evidence-bearing edge. Without it, confidence stays NULL — absence is the honest value.')] = None,
        evidence: Annotated[list | str | None, Field(description='List of {to_ref, ref_type, relation} dicts linking the claim to its support (e.g. {"to_ref": "trial-abc", "ref_type": "trial", "relation": "tested_by"}); may be JSON-encoded.')] = None,
        supersedes_id: Annotated[str | None, Field(description='ID of an existing claim this one replaces — the supersedes edge is recorded automatically.')] = None,
        source_id: Annotated[str | None, Field(description='Provenance tag — the entity (decision/investigation) that minted this claim.')] = None,
        valid_from: Annotated[str | None, Field(description="Optional ISO-8601 UTC validity start (e.g. '2026-01-01T00:00:00Z') — defaults to mint time. Use it to date the claim to when its evidence holds, not when the row was written. Must be ≤ valid_until when both are given.")] = None,
        valid_until: Annotated[str | None, Field(description="Optional ISO-8601 UTC expiry (e.g. '2026-12-31T00:00:00Z') — the claim's live→expired transition. Claims past valid_until are hidden from list_claims unless include_expired=true; the row is never deleted. A past timestamp mints an already-expired claim — allowed, the record says so.")] = None,
    ) -> Annotated[CallToolResult, AssertClaimOut]:
        """Assert a claim into the memory graph.

        type: empirical | methodological. evidence is a list of
        {to_ref, ref_type, relation} dicts linking this claim to its
        support (e.g. {"to_ref": "trial-abc", "ref_type": "trial",
        "relation": "tested_by"}); may be sent as a JSON-encoded string.

        Numeric provenance invariant (plan-20260930-0240Z): there is
        no confidence parameter — an LLM never originates a numeric
        confidence. To mint a number, pass confidence_computation
        {procedure, inputs} naming a registered derivation
        (posterior_from_2lnbf | h0_bound_from_2lnbf |
        verdict_posterior); this server recomputes it and stores the
        result with the derivation record. confidence_basis is derived
        server-side — grounded when a computation verifies,
        weakly_grounded when evidence-bearing edges exist without one,
        ungrounded for a bare mint; confidence stays NULL on the
        latter two.

        supersedes_id names an existing claim this one replaces; the
        supersedes edge is recorded automatically. Claims are
        deduplicated by normalized content — re-asserting identical
        content returns the existing claim_id, and any evidence edges
        supplied with the re-assertion are still recorded on it
        (edges_added reports how many were new). supersedes_id,
        valid_from, valid_until and confidence_computation are
        mint-time fields — on a dedup hit the existing claim keeps
        its own values; re-assertion cannot launder a computation
        onto an old row. valid_until is the agent-reachable expiry:
        claims past it leave the default list view but are never
        deleted.
        """
        try:
            if confidence_computation is not None:
                confidence_computation = coerce_json(
                    confidence_computation, dict, "confidence_computation"
                )
            if evidence is not None:
                evidence = coerce_json(evidence, list, "evidence")

            for _name in ("valid_from", "valid_until"):
                _raw = locals()[_name]
                if _raw is None:
                    continue
                try:
                    _dt = datetime.fromisoformat(
                        _raw.replace("Z", "+00:00")
                    )
                except (ValueError, AttributeError):
                    return fail(json.dumps({
                        "error": f"{_name} is not ISO-8601: {_raw!r}"
                    }))
                if _dt.tzinfo is None:
                    return fail(json.dumps({
                        "error": f"{_name} must be timezone-aware "
                                 "(ISO-8601 UTC — e.g. 2026-12-31T00:00:00Z)"
                    }))
                # Normalize to UTC ISO — the expiry filter compares
                # lexicographically against +00:00-suffixed now().
                if _name == "valid_from":
                    valid_from = _dt.astimezone(timezone.utc).isoformat()
                else:
                    valid_until = _dt.astimezone(timezone.utc).isoformat()
            if (
                valid_from is not None
                and valid_until is not None
                and valid_from > valid_until
            ):
                return fail(json.dumps({
                    "error": "valid_from is after valid_until — an "
                             "empty validity interval"
                }))

            # Validate every evidence edge spec before writing
            # anything — same rules on the mint path and the
            # dedup-attach path; a malformed edge is a named error,
            # never silently dropped.
            evidence = evidence or []
            for e in evidence:
                for err in (
                    check_ref_type_valid(e.get("ref_type", "")),
                    check_relation_valid(e.get("relation", "")),
                    check_ref_type_consistent(
                        e.get("to_ref", ""), e.get("ref_type", "")
                    ),
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

            # Dedup: re-asserting existing content finds the claim —
            # a lookup, not a new claim — but the caller's evidence
            # edges still land on it. Mint-time fields stay
            # mint-time-only: re-assertion cannot launder a
            # computation or a supersedes edge onto an existing row —
            # correcting a claim means minting a new one with
            # supersedes_id (plan-20260930-0240Z, review-3 dedup
            # amendment).
            chash = content_hash(content)
            existing = store.get_claim_by_hash(chash)
            if existing is not None:
                edge_ids = []
                edges_added = 0
                with store.transaction():
                    for e in evidence:
                        dup = store.find_edge(
                            existing.id, e["to_ref"],
                            e["ref_type"], e["relation"],
                        )
                        if dup is not None:
                            edge_ids.append(dup.id)
                            continue
                        edge = ClaimEdge(
                            id=f"edge-{uuid.uuid4().hex[:8]}",
                            from_claim=existing.id,
                            to_ref=e["to_ref"],
                            ref_type=RefType(e["ref_type"]),
                            relation=Relation(e["relation"]),
                            source_id=e.get("source_id", source_id),
                        )
                        store.create_edge(edge)
                        edge_ids.append(edge.id)
                        edges_added += 1
                return ok({
                    "claim_id": existing.id,
                    "deduplicated": True,
                    "edge_ids": edge_ids,
                    "edges_added": edges_added,
                    "confidence": existing.confidence,
                    "confidence_basis": existing.confidence_basis,
                    "confidence_computation": existing.confidence_computation,
                    "valid_until": existing.valid_until,
                    "status": "exists",
                })

            has_evidence = any(
                e.get("relation") in EVIDENCE_RELATIONS for e in evidence
            )

            # The only path to a numeric confidence: a registered
            # procedure recomputed here, on the caller's inputs. The
            # computation record is stored alongside — replayable.
            confidence = None
            stored_computation = None
            if confidence_computation is not None:
                try:
                    confidence = _gc.compute_confidence(
                        confidence_computation
                    )
                except Exception as e:
                    return fail(json.dumps({"error": str(e)}))
                if not has_evidence:
                    return fail(json.dumps({
                        "error": "confidence_computation requires at "
                        "least one evidence-bearing edge (relation: "
                        "supports|derived_from|tested_by|valid_under) "
                        "— a posterior floating free of the graph is "
                        "not grounded."
                    }))
                confidence_basis = "grounded"
                stored_computation = confidence_computation
            elif has_evidence:
                confidence_basis = "weakly_grounded"
            else:
                confidence_basis = "ungrounded"

            for err in (
                check_claim_type_valid(type),
                check_supersedes_exists(supersedes_id, store),
            ):
                if err:
                    return fail(json.dumps({"error": err}))

            claim = Claim(
                id=f"claim-{uuid.uuid4().hex[:8]}",
                content=content,
                type=ClaimType(type),
                confidence=confidence,
                supersedes_id=supersedes_id,
                content_hash=chash,
                source_id=source_id,
                confidence_basis=confidence_basis,
                confidence_computation=stored_computation,
                valid_from=valid_from if valid_from is not None else _utc_now(),
                valid_until=valid_until,
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
                        source_id=e.get("source_id", source_id),
                    )
                    store.create_edge(edge)
                    edge_ids.append(edge.id)

            return ok({
                "claim_id": claim.id,
                "edge_ids": edge_ids,
                "confidence": claim.confidence,
                "confidence_basis": claim.confidence_basis,
                "confidence_computation": claim.confidence_computation,
                "valid_until": claim.valid_until,
                "status": "asserted",
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def relate(
        from_claim: Annotated[str, Field(description='ID of the claim the edge starts from.')],
        to_ref: Annotated[str, Field(description='Edge target — claim-typed IDs are verified; other ref types are trusted opaque IDs.')],
        ref_type: Annotated[Literal['claim', 'trial', 'observation', 'conclusion', 'programme', 'hypothesis', 'investigation', 'finding', 'archive', 'improver', 'tournament', 'tournament_result', 'proposal', 'meta_contract', 'meta_decision', 'policy_version', 'canary_deployment', 'candidate', 'contract', 'decision', 'bundle', 'dataref', 'reference', 'evidence_ref', 'campaign', 'campaign_spawn', 'external'], Field(description="Type of to_ref — the closed RefType vocabulary: claim | trial | observation | conclusion | programme | hypothesis | investigation | finding | archive | improver | tournament | tournament_result | proposal | meta_contract | meta_decision | policy_version | canary_deployment | candidate | contract | decision | bundle | dataref | reference | evidence_ref | campaign | campaign_spawn | external.")],
        relation: Annotated[Literal['supports', 'contradicts', 'derived_from', 'tested_by', 'valid_under', 'supersedes', 'generalizes', 'specializes', 'similar_to', 'failed_because', 'cites'], Field(description='Edge type: supports | contradicts | derived_from | tested_by | valid_under | supersedes | generalizes | specializes | similar_to | failed_because | cites.')],
        source_id: Annotated[str | None, Field(description='Provenance tag — the entity that recorded this edge.')] = None,
    ) -> Annotated[CallToolResult, RelateOut]:
        """Link a claim to evidence or another claim.

        relation (closed vocabulary): supports | contradicts |
        derived_from | tested_by | valid_under | supersedes |
        generalizes | specializes | similar_to | failed_because |
        cites.
        ref_type: the closed RefType vocabulary — claim | trial |
        observation | conclusion | programme | hypothesis |
        investigation | finding | archive | improver | tournament |
        tournament_result | proposal | meta_contract | meta_decision |
        policy_version | canary_deployment | candidate | contract |
        decision | bundle | dataref | reference | evidence_ref |
        campaign | campaign_spawn | external. claim-typed to_ref values are
        verified; other ref types are trusted opaque IDs across the
        protocol boundary. 'external' is refused when to_ref carries a
        known internal prefix — internal ids take their own type.
        Exact duplicate edges return the existing edge_id.
        """
        try:
            for err in (
                check_relation_valid(relation),
                check_ref_type_valid(ref_type),
                check_ref_type_consistent(to_ref, ref_type),
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
                }
                for e in store.edges_from(claim_id)
            ]
            inc = [
                {
                    "edge_id": e.id,
                    "from_claim": e.from_claim,
                    "relation": e.relation.value,
                }
                for e in store.edges_to(claim_id)
            ]
            return ok({
                "claim": {
                    "id": claim.id,
                    "content": claim.content,
                    "type": claim.type.value,
                    "confidence": claim.confidence,
                    "valid_from": claim.valid_from,
                    "valid_until": claim.valid_until,
                    "supersedes_id": claim.supersedes_id,
                    "source_id": claim.source_id,
                    "confidence_basis": claim.confidence_basis,
                    "confidence_computation": claim.confidence_computation,
                    "created_at": claim.created_at,
                },
                "outgoing_edges": out,
                "incoming_edges": inc,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def get_claims(
        claim_ids: Annotated[list[str] | str, Field(description='Claim IDs to fetch in one call — list or JSON-encoded list. Caps the get_claim amplification loop (one call per claim → one call per subgraph).')],
    ) -> Annotated[CallToolResult, GetClaimsOut]:
        """Batch-read claims — one call, many claims.

        Same provenance bundle as get_claim (claim + outgoing +
        incoming edges) for every requested ID. Missing IDs are
        reported under 'missing' rather than failing the batch —
        a stale subgraph reference should not strand the whole pull.
        """
        try:
            claim_ids = coerce_json(claim_ids, list, "claim_ids")
            claims_out, missing = [], []
            for cid in claim_ids:
                claim = store.get_claim(cid)
                if claim is None:
                    missing.append(cid)
                    continue
                claims_out.append({
                    "claim": {
                        "id": claim.id,
                        "content": claim.content,
                        "type": claim.type.value,
                        "confidence": claim.confidence,
                            "valid_from": claim.valid_from,
                        "valid_until": claim.valid_until,
                        "supersedes_id": claim.supersedes_id,
                        "source_id": claim.source_id,
                        "confidence_basis": claim.confidence_basis,
                        "confidence_computation": claim.confidence_computation,
                        "created_at": claim.created_at,
                    },
                    "outgoing_edges": [
                        {
                            "edge_id": e.id,
                            "to_ref": e.to_ref,
                            "ref_type": e.ref_type.value,
                            "relation": e.relation.value,
                                }
                        for e in store.edges_from(cid)
                    ],
                    "incoming_edges": [
                        {
                            "edge_id": e.id,
                            "from_claim": e.from_claim,
                            "relation": e.relation.value,
                                }
                        for e in store.edges_to(cid)
                    ],
                })
            return ok({"claims": claims_out, "missing": missing})
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
                        "confidence_basis": c.confidence_basis,
                        "confidence_computation": c.confidence_computation,
                        "valid_from": c.valid_from,
                        "valid_until": c.valid_until,
                        "supersedes_id": c.supersedes_id,
                        "source_id": c.source_id,
                        "created_at": c.created_at,
                    }
                    for c in claims
                ],
                "total": total,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))
