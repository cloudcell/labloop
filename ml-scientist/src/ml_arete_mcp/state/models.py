"""Pydantic domain models for the arete recursive loop (Loop 2).

Entity typing per a-01 (see the Loop-2 plan):

improver_versions   → generically dependent continuant (a
                      specification — sibling of Loop-0's
                      candidate_version, one loop up)
meta_contracts      → directive information entity (what counts as
                      improvement-of-the-improver; versioned,
                      frozen once a tournament opens on it)
meta_change_proposals → directive information entity (a typed
                      meta-change request; admission enforces the
                      ADR-0003 writable boundary)
tournaments         → process, recorded as a durable event (paired
                      ancestral comparison under equal budget)
tournament_results  → data items (what one arm produced; insert-only)
meta_decisions      → directive information entity (insert-only;
                      reversal is a new decision, never an edit)
policy_versions     → directive information entity (the versioned
                      policy object later written down to Loop 1)
canary_deployments  → process, recorded as a durable event (scoped
                      activation record)
evidence_refs       → event record (a logged upstream read — the
                      provenance of what was consulted; insert-only,
                      owned by a proposal or tournament context)

recursive_gain lives on the closed tournament: it is a data item
about a relational quality between two improver versions, and is
structurally unable to sit on a single version row.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProposalStatus(str, Enum):
    admitted = "admitted"
    conditional = "conditional"
    rejected = "rejected"
    superseded = "superseded"


class TournamentStatus(str, Enum):
    open = "open"
    closed = "closed"
    # Terminal dead-record state — opened but never completed. No
    # recursive_gain, no comparison implied; same family as
    # programme → abandoned. Set only by void_tournament.
    voided = "voided"


class TournamentArm(str, Enum):
    parent = "parent"
    candidate = "candidate"


class DecisionVerdict(str, Enum):
    promote = "promote"
    reject = "reject"
    hold = "hold"
    rollback = "rollback"


class PolicyStatus(str, Enum):
    minted = "minted"
    canary = "canary"
    active = "active"
    rolled_back = "rolled_back"


class CanaryStatus(str, Enum):
    open = "open"
    passed = "passed"
    regressed = "regressed"


class EvidenceSource(str, Enum):
    loop0 = "loop0"
    loop1 = "loop1"
    anamnesis = "anamnesis"


class EvidenceContext(str, Enum):
    proposal = "proposal"
    tournament = "tournament"


class ImproverVersion(BaseModel):
    id: str
    parent_id: str | None = None
    proposal_id: str | None = None
    code_artifact_digest: str
    harness_artifact_digest: str | None = None
    model_ref: str
    search_policy_ref: str | None = None
    capability_profile: dict[str, Any] = Field(default_factory=dict)
    is_champion: bool = False
    created_at: str = Field(default_factory=_utc_now)


class MetaContract(BaseModel):
    id: str
    version: int
    metrics: dict[str, Any]
    promotion_policy: dict[str, Any]
    holdouts: dict[str, Any] | None = None
    budget: dict[str, Any] | None = None
    frozen: bool = False
    created_at: str = Field(default_factory=_utc_now)


class MetaChangeProposal(BaseModel):
    id: str
    proposer_improver_id: str
    spec_delta: dict[str, Any]
    class_map: dict[str, Any]
    expected_benefit: str
    falsification: str
    budget: dict[str, Any] | None = None
    rollback_plan: str
    status: ProposalStatus = ProposalStatus.admitted
    rejection_reason: str | None = None
    created_at: str = Field(default_factory=_utc_now)


class Tournament(BaseModel):
    id: str
    contract_id: str
    parent_improver_id: str
    candidate_improver_id: str
    budget: dict[str, Any]
    seeds: list[int] | None = None
    status: TournamentStatus = TournamentStatus.open
    recursive_gain: float | None = None
    created_at: str = Field(default_factory=_utc_now)
    closed_at: str | None = None
    # The attributed void record {rationale, decided_by, voided_at} —
    # set when status=voided, NULL otherwise.
    void: dict[str, Any] | None = None
    # Contract-declared power design, snapshotted at open — the frozen
    # contract stays the authority; these keep the row self-contained.
    n_required: int | None = None
    n_requested: int | None = None
    power_acknowledged: bool = False
    sesoi_d: float | None = None
    target_power: float | None = None
    min_evidence_rung: str | None = None
    alpha: float | None = None
    # Declared prior P[H₁] (plan-20260929-1642Z) — frozen with the
    # contract; None → PRIOR_CONFIDENCE_MAX at mint.
    prior: float | None = None
    # Close-time accounting — set by close_tournament.
    n_achieved: int | None = None
    underpowered: bool | None = None
    type_s_risk: float | None = None
    type_m_ratio: float | None = None
    p_value: float | None = None   # one-sided, candidate-favored
    bf_2ln: float | None = None    # oracle LR bound: max(z,0)²


class TournamentResult(BaseModel):
    id: str
    tournament_id: str
    arm: TournamentArm
    descendant_spec: dict[str, Any]
    metrics: dict[str, Any]
    corrections: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str = Field(default_factory=_utc_now)


class TournamentCampaign(BaseModel):
    """The durable correlation between a Loop-2 tournament arm and
    the Loop-1 campaign that arm spawned — a recorded data item, not
    prose. UNIQUE(tournament_id, arm): one upstream campaign per arm,
    so a retry of open_arm_campaign is idempotent rather than
    link-multiplying."""
    id: str
    tournament_id: str
    arm: TournamentArm
    campaign_id: str
    upstream_contract_id: str
    challenger_id: str
    created_at: str = Field(default_factory=_utc_now)


class MetaDecision(BaseModel):
    id: str
    candidate_improver_id: str
    contract_id: str | None = None
    tournament_id: str | None = None
    verdict: DecisionVerdict
    evidence_refs: list[str] = Field(default_factory=list)
    rationale: str
    decided_by: str
    claim_id: str | None = None
    claim_error: str | None = None
    # Evidence-rung accounting: the contract's declared minimum vs the
    # rung the decision claims vs the rung the cited tournament
    # measured at close (plan-20260929-1642Z — evidence-vs-ladder).
    declared_rung: str | None = None
    claimed_rung: str | None = None
    computed_rung: str | None = None
    # What the minted claim confidence rests on (CONFIDENCE_BASIS
    # enum) — grounded = posterior bound computed; weakly_grounded =
    # evidence consulted, no likelihood.
    confidence_basis: str | None = None
    created_at: str = Field(default_factory=_utc_now)


class PolicyVersion(BaseModel):
    id: str
    improver_version_id: str
    decision_id: str
    policy: dict[str, Any]
    previous_champion_id: str | None = None
    status: PolicyStatus = PolicyStatus.minted
    created_at: str = Field(default_factory=_utc_now)


class CanaryDeployment(BaseModel):
    id: str
    policy_version_id: str
    scope: dict[str, Any]
    status: CanaryStatus = CanaryStatus.open
    created_at: str = Field(default_factory=_utc_now)
    closed_at: str | None = None


class EvidenceRef(BaseModel):
    id: str
    context_type: EvidenceContext
    context_id: str
    source: EvidenceSource
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    ref_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=_utc_now)
