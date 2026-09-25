"""Enforcement checks for the claim graph.

Each check returns an error string or None — same convention as
ml-episteme's commitments layer. These are the integrity rules of
semantic memory: a claim graph that accepts unsupported high-confidence
assertions is worse than no memory at all.
"""

from __future__ import annotations

from ..state.models import EVIDENCE_RELATIONS, RefType, Relation
from ..state.store import MemoryStore

# Claims asserted without evidence-bearing edges may not exceed this
# confidence — a prior-level ceiling, not a truth judgement. Named so
# the threshold is tunable without archaeology (i-review note).
PRIOR_CONFIDENCE_MAX = 0.3


def check_relation_valid(relation: str) -> str | None:
    """Relation must be in the closed vocabulary."""
    if relation not in {r.value for r in Relation}:
        return (
            f"relation must be one of "
            f"{'|'.join(r.value for r in Relation)}, got: {relation}"
        )
    return None


def check_ref_type_valid(ref_type: str) -> str | None:
    """ref_type must be in the closed vocabulary."""
    if ref_type not in {t.value for t in RefType}:
        return (
            f"ref_type must be one of "
            f"{'|'.join(t.value for t in RefType)}, got: {ref_type}"
        )
    return None


def check_claim_type_valid(claim_type: str) -> str | None:
    if claim_type not in ("empirical", "methodological"):
        return (
            f"type must be empirical|methodological, got: {claim_type}"
        )
    return None


def check_evidence_requirement(
    evidence: list[dict] | None,
    confidence: float,
    prior_max: float = PRIOR_CONFIDENCE_MAX,
) -> str | None:
    """A claim without evidence-bearing edges is capped at prior-level
    confidence — the barrier against unsupported high-confidence spam.
    prior_max is the configured ceiling ([integrity]
    prior_confidence_max); the audit uses the same value."""
    has_evidence = bool(
        evidence
        and any(e.get("relation") in EVIDENCE_RELATIONS for e in evidence)
    )
    if not has_evidence and confidence > prior_max:
        return (
            f"confidence {confidence} exceeds prior ceiling "
            f"{prior_max} without evidence. Provide at least "
            f"one evidence edge (relation: supports|derived_from|"
            f"tested_by|valid_under) or lower confidence."
        )
    return None


def check_supersedes_exists(
    supersedes_id: str | None, store: MemoryStore
) -> str | None:
    if supersedes_id is None:
        return None
    if store.get_claim(supersedes_id) is None:
        return f"Claim to supersede not found: {supersedes_id}."
    return None


def check_edge_valid(
    from_claim: str, to_ref: str, ref_type: str, store: MemoryStore
) -> str | None:
    """Edge endpoints: from_claim must exist; to_ref is verified only
    when it is claim-typed (external refs are trusted opaque IDs);
    self-edges are meaningless."""
    if store.get_claim(from_claim) is None:
        return f"Claim not found: {from_claim}."
    if ref_type == RefType.claim.value:
        if to_ref == from_claim:
            return "Self-edge rejected: from_claim == to_ref."
        if store.get_claim(to_ref) is None:
            return f"Claim-typed to_ref not found: {to_ref}."
    return None
