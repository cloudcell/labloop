"""Pydantic domain models for the zetesis search loop (Loop 1).

investigations → information content entity (a bounded inquiry — the
                 search-loop sibling of Loop-0's `programme`)
evidence_refs  → event record (a logged upstream read — the provenance
                 of what the investigator consulted; insert-only)
findings       → information content entity (a proto-claim: a
                 provisional methodological assertion that becomes a
                 claim only when minted to anamnesis at conclusion)

finding_evidence is a junction: which pulls ground which finding.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class InvestigationStatus(str, Enum):
    open = "open"
    concluded = "concluded"
    abandoned = "abandoned"


class InvestigationVerdict(str, Enum):
    findings = "findings"
    null_result = "null_result"


class FindingStatus(str, Enum):
    provisional = "provisional"
    asserted = "asserted"
    dropped = "dropped"


class EvidenceSource(str, Enum):
    loop0 = "loop0"
    anamnesis = "anamnesis"


class DerivedStatus(str, Enum):
    """Roster status — derived from upstream decisions, never stored
    upstream. `champion` = the incumbent; `challenger` = a candidate
    zetesis registered against the incumbent; `candidate` = tracked,
    neither; `rolled_back` = superseded by a rollback verdict."""
    champion = "champion"
    challenger = "challenger"
    candidate = "candidate"
    rolled_back = "rolled_back"


class PolicyStatus(str, Enum):
    draft = "draft"
    active = "active"
    retired = "retired"


class CampaignStatus(str, Enum):
    open = "open"
    closed = "closed"


class CampaignArm(str, Enum):
    champion = "champion"
    challenger = "challenger"


class Investigation(BaseModel):
    id: str
    question: str
    scope: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any] | None = None
    status: InvestigationStatus = InvestigationStatus.open
    verdict: InvestigationVerdict | None = None
    summary: str | None = None
    implications: dict[str, Any] | None = None
    created_at: str = Field(default_factory=_utc_now)
    concluded_at: str | None = None


class EvidenceRef(BaseModel):
    id: str
    investigation_id: str | None = None
    campaign_id: str | None = None
    source: EvidenceSource
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    ref_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=_utc_now)


class Finding(BaseModel):
    id: str
    investigation_id: str
    content: str
    confidence: float = Field(ge=0.0, le=1.0)
    status: FindingStatus = FindingStatus.provisional
    claim_id: str | None = None
    created_at: str = Field(default_factory=_utc_now)


class RosterEntry(BaseModel):
    """Zetesis-tracked candidate annotation — NOT a mirror of upstream
    candidate_versions (authoritative in state.db). Records candidates
    zetesis has acted on plus a refreshed derived-status cache."""
    id: str
    parent_id: str | None = None
    derived_status: DerivedStatus = DerivedStatus.candidate
    annotated_at: str = Field(default_factory=_utc_now)
    notes: dict[str, Any] = Field(default_factory=dict)


class SearchPolicy(BaseModel):
    """A versioned search-policy object — what a candidate's
    search_policy_ref denotes. The registry a Loop-2 policy_version
    will eventually activate against."""
    id: str
    name: str
    version: int = Field(ge=1)
    policy: dict[str, Any] = Field(default_factory=dict)
    status: PolicyStatus = PolicyStatus.draft
    created_at: str = Field(default_factory=_utc_now)


class PromotionCampaign(BaseModel):
    """A bounded paired evaluation: challenger vs the derived incumbent
    under a Loop-0 evaluation_contract. The Loop-1-level analogue of
    arete's tournament — the record of a client-driven evaluation."""
    id: str
    contract_id: str
    champion_id: str
    challenger_id: str
    primary_metric: str
    budget: dict[str, Any] = Field(default_factory=dict)
    seeds: list[int] = Field(default_factory=list)
    status: CampaignStatus = CampaignStatus.open
    promotion_score: float | None = None
    decision_id: str | None = None
    claim_id: str | None = None
    created_at: str = Field(default_factory=_utc_now)
    closed_at: str | None = None


class CampaignResult(BaseModel):
    """Per-arm outcome — insert-only. Each row names the attributed
    Loop-0 programme it reports on; attribution is verified upstream
    at record time (programme.candidate_version_id == arm's candidate)."""
    id: str
    campaign_id: str
    arm: CampaignArm
    programme_id: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_utc_now)


class SpawnStatus(str, Enum):
    spawned = "spawned"
    completed = "completed"
    failed = "failed"


class CampaignSpawn(BaseModel):
    """The durable statement that a programme exists *because* this
    campaign's arm ran — sibling of campaign_results. Insert-only:
    one spawn row per programme ever (UNIQUE programme_id), so
    spawn-scoped attribution cannot be replayed into a different
    campaign."""
    id: str
    campaign_id: str
    arm: CampaignArm
    programme_id: str
    budget: dict[str, Any] = Field(default_factory=dict)
    status: SpawnStatus = SpawnStatus.spawned
    created_at: str = Field(default_factory=_utc_now)
