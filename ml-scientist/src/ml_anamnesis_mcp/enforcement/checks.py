"""Enforcement checks for the claim graph.

Each check returns an error string or None — same convention as
ml-episteme's commitments layer. These are the integrity rules of
semantic memory: a claim graph that accepts unsupported high-confidence
assertions is worse than no memory at all.
"""

from __future__ import annotations

from ..state.models import (
    INTERNAL_REF_PREFIXES,
    RefType,
    Relation,
)
from ..state.store import MemoryStore
from .. import _grounded_constants as _gc

# The prior-level ceiling that once capped caller-declared confidence
# (pre plan-20260930-0240Z). Kept exported: the config key
# [integrity] prior_confidence_max still exists and downstream code
# references the value as a declared policy threshold — it is no
# longer a write gate, only a documented constant.
PRIOR_CONFIDENCE_MAX = _gc.PRIOR_CONFIDENCE_MAX.value


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


def check_ref_type_consistent(to_ref: str, ref_type: str) -> str | None:
    """'external' is for refs no internal family owns. An id carrying
    a known internal prefix filed as 'external' is a mislabel — refuse
    it at write time and name the honest type."""
    if ref_type != RefType.external.value:
        return None
    for prefix, types in INTERNAL_REF_PREFIXES.items():
        if to_ref.startswith(prefix):
            want = "|".join(types)
            return (
                f"to_ref {to_ref!r} carries an internal prefix — "
                f"ref_type must be '{want}', not 'external'"
            )
    return None


def check_claim_type_valid(claim_type: str) -> str | None:
    if claim_type not in ("empirical", "methodological"):
        return (
            f"type must be empirical|methodological, got: {claim_type}"
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
