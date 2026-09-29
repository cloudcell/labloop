"""Pydantic domain models for the anamnesis claim graph.

claims      → information content entity (a cross-programme assertion
              about a disposition or relation — a hypothesis that
              outlives its programme)
claim_edges → relational quality (typed relations between a
              claim and evidence/other claims — unweighted by design:
              the graph does not rank evidence)

Both tables are insert-only by surface: bi-temporal invalidation
(valid_until) replaces deletion.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ClaimType(str, Enum):
    empirical = "empirical"
    methodological = "methodological"


class RefType(str, Enum):
    """What a claim_edge's to_ref points at.

    'claim' refs are verified against the claims table; the rest are
    opaque IDs trusted across the protocol boundary (ADR-0002). The
    vocabulary covers every entity family a Loop can cite — Loop-2
    entities are first-class citizens of the claim graph.
    """

    claim = "claim"
    trial = "trial"
    observation = "observation"
    conclusion = "conclusion"
    programme = "programme"
    investigation = "investigation"
    finding = "finding"
    archive = "archive"
    improver = "improver"
    tournament = "tournament"
    tournament_result = "tournament_result"
    proposal = "proposal"
    meta_contract = "meta_contract"
    meta_decision = "meta_decision"
    policy_version = "policy_version"
    canary_deployment = "canary_deployment"
    candidate = "candidate"
    contract = "contract"
    decision = "decision"
    bundle = "bundle"
    dataref = "dataref"
    reference = "reference"
    external = "external"


class Relation(str, Enum):
    """Closed relation vocabulary (extends only by ontology amendment)."""

    supports = "supports"
    contradicts = "contradicts"
    derived_from = "derived_from"
    tested_by = "tested_by"
    valid_under = "valid_under"
    supersedes = "supersedes"
    generalizes = "generalizes"
    specializes = "specializes"
    similar_to = "similar_to"
    failed_because = "failed_because"
    cites = "cites"


# Relations that count as evidence for the evidence requirement
# (enforcement check 3). Structural/interpretive relations do not.
EVIDENCE_RELATIONS = frozenset(
    {
        Relation.supports.value,
        Relation.derived_from.value,
        Relation.tested_by.value,
        Relation.valid_under.value,
    }
)

# ID prefix → the RefType(s) that honestly describe an internal id.
# 'external' is for refs no internal family owns; an id carrying one
# of these prefixes filed as 'external' is a mislabel — the caller had
# no better choice before bundle/dataref/reference joined the enum,
# or is guessing. Families without a RefType member (hyp-,
# eref-, camp-, spawn-, …) are deliberately absent — there is no
# honest type to name for them yet; extending this map means extending
# RefType first.
INTERNAL_REF_PREFIXES: dict[str, tuple[str, ...]] = {
    "claim-": ("claim",),
    "trial-": ("trial",),
    "obs-": ("observation",),
    "conc-": ("conclusion",),
    "prog-": ("programme",),
    "inv-": ("investigation",),
    "find-": ("finding",),
    "archive-": ("archive",),
    "imp-": ("improver",),
    "tourn-": ("tournament",),
    "tres-": ("tournament_result",),
    "mcp-": ("proposal",),
    "mcontract-": ("meta_contract",),
    "mdec-": ("meta_decision",),
    "pol-": ("policy_version",),
    "canary-": ("canary_deployment",),
    "cand-": ("candidate",),
    "contract-": ("contract",),
    "decision-": ("decision",),
    "bundle-": ("bundle",),
    "data-ref-": ("dataref", "reference"),
}


class Claim(BaseModel):
    id: str
    content: str
    type: ClaimType
    confidence: float = Field(ge=0.0, le=1.0)
    valid_from: str = Field(default_factory=_utc_now)
    valid_until: str | None = None
    supersedes_id: str | None = None
    content_hash: str
    source_id: str | None = None
    # Confidence provenance (plan-20260929-1642Z): what the float
    # rests on — grounded = a posterior bound was computed;
    # weakly_grounded = evidence consulted, no likelihood;
    # ungrounded = prior-ceiling mint, no evidence. NULL on
    # legacy rows — recorded as ungrounded, not guessed.
    confidence_basis: str | None = None
    created_at: str = Field(default_factory=_utc_now)


class ClaimEdge(BaseModel):
    id: str
    from_claim: str
    to_ref: str
    ref_type: RefType
    relation: Relation
    source_id: str | None = None
    created_at: str = Field(default_factory=_utc_now)
