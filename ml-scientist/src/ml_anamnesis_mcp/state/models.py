"""Pydantic domain models for the anamnesis claim graph.

claims      → information content entity (a cross-programme assertion
              about a disposition or relation — a hypothesis that
              outlives its programme)
claim_edges → relational quality (typed, weighted relations between a
              claim and evidence/other claims)

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
    opaque IDs trusted across the protocol boundary (ADR-0002).
    """

    claim = "claim"
    trial = "trial"
    observation = "observation"
    conclusion = "conclusion"
    programme = "programme"
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


class Claim(BaseModel):
    id: str
    content: str
    type: ClaimType
    confidence: float = Field(ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    valid_from: str = Field(default_factory=_utc_now)
    valid_until: str | None = None
    supersedes_id: str | None = None
    content_hash: str
    source_id: str | None = None
    created_at: str = Field(default_factory=_utc_now)


class ClaimEdge(BaseModel):
    id: str
    from_claim: str
    to_ref: str
    ref_type: RefType
    relation: Relation
    weight: float = 1.0
    source_id: str | None = None
    created_at: str = Field(default_factory=_utc_now)
