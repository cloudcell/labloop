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
    hypothesis = "hypothesis"
    evidence_ref = "evidence_ref"
    campaign = "campaign"
    campaign_spawn = "campaign_spawn"
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
# or is guessing. Extending this map means extending RefType first.
INTERNAL_REF_PREFIXES: dict[str, tuple[str, ...]] = {
    "claim-": ("claim",),
    "trial-": ("trial",),
    "obs-": ("observation",),
    "conc-": ("conclusion",),
    "prog-": ("programme",),
    "hyp-": ("hypothesis",),
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
    "eref-": ("evidence_ref",),
    "camp-": ("campaign",),
    "spawn-": ("campaign_spawn",),
}


class Claim(BaseModel):
    id: str
    content: str
    type: ClaimType
    # Numeric provenance invariant (plan-20260930-0240Z): non-NULL
    # confidence exists iff a registered derivation produced it —
    # an unevidenced claim has no computable confidence, so the
    # column says NULL rather than wearing a policy constant.
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    valid_from: str = Field(default_factory=_utc_now)
    valid_until: str | None = None
    supersedes_id: str | None = None
    content_hash: str
    source_id: str | None = None
    # Confidence provenance (plan-20260929-1642Z; server-derived
    # since plan-20260930-0240Z): grounded = a registered derivation
    # produced the number; weakly_grounded = evidence-bearing edges
    # attached, no derivation; ungrounded = bare mint. NULL on
    # legacy rows only — post-cutover mints always carry a basis.
    confidence_basis: str | None = None
    # The derivation record: {procedure, inputs} that produced
    # confidence — replayable by any auditor. NULL ⟺ confidence is
    # not derived (NULL confidence or a legacy row).
    confidence_computation: dict | None = None
    created_at: str = Field(default_factory=_utc_now)


class ClaimEdge(BaseModel):
    id: str
    from_claim: str
    to_ref: str
    ref_type: RefType
    relation: Relation
    source_id: str | None = None
    created_at: str = Field(default_factory=_utc_now)
