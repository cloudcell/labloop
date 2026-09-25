"""Pydantic domain models for each entity in the state layer.

Each model corresponds to a row in a SQLite table and maps to exactly one
terminal ontological category (Rule 5.1) with explicit aboutness (Rule 5.3).

Ontological categories per b-01 §2.1:
  programmes     → information content entity
  hypotheses     → information content entity
  trials         → data item
  observations   → data item
  beliefs        → information content entity
  conclusions    → information content entity
  bundles        → information content entity
  code_snippets  → textual entity (model source code — the definition)
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Enums for controlled vocabularies ---


class ProgrammeStatus(str, Enum):
    active = "active"
    completed = "completed"
    abandoned = "abandoned"
    archived = "archived"


class HypothesisStatus(str, Enum):
    proposed = "proposed"
    under_test = "under_test"
    accepted = "accepted"
    rejected = "rejected"
    inconclusive = "inconclusive"
    abandoned = "abandoned"


class TrialStatus(str, Enum):
    designed = "designed"
    running = "running"
    completed = "completed"
    failed = "failed"
    retryable = "retryable"
    abandoned = "abandoned"


class Verdict(str, Enum):
    accepted = "accepted"
    rejected = "rejected"
    inconclusive = "inconclusive"


# --- Ontological metadata (Rule 5.1: single terminal category) ---


class OntologicalMeta(BaseModel):
    """Metadata every entity carries to enforce Rule 5.1 and 5.3."""

    ontological_category: str = Field(
        ..., description="Terminal ontological category from a-01 §4 MECE map"
    )
    aboutness: str = Field(
        ..., description="What this entity is about (Rule 5.3 / IAO aboutness)"
    )


# --- Entity models ---


class Programme(BaseModel):
    """A research programme (the loop as first-class object).

    Ontological category: information content entity
    Aboutness: a research line (hard core + protective belt)
    """

    id: str
    goal: str = Field(..., description="Objective specification (directive ICE)")
    constraints: dict[str, Any] = Field(
        ..., description="Typed constraints (commitment 4: first-class, not strings)"
    )
    allowed_variables: list[str] = Field(
        ..., description="The search space variables"
    )
    budget_max_trials: int = Field(..., ge=0)
    budget_max_wall_time_hours: float = Field(..., ge=0.0)
    metric_direction: str = Field(
        "maximize",
        description="Optimization direction: 'minimize' (e.g. perplexity, loss) or 'maximize' (e.g. accuracy)",
    )
    candidate_version_id: str | None = Field(
        default=None,
        description="Candidate that created this programme (RSI Phase 0 correlation)",
    )
    status: ProgrammeStatus = ProgrammeStatus.active
    created_at: str = Field(default_factory=_utc_now)

    ontological_category: str = "information content entity"
    aboutness: str = "a research line (hard core + protective belt)"


class Hypothesis(BaseModel):
    """A falsifiable hypothesis.

    Ontological category: information content entity
    Aboutness: a relation between a variable and an outcome
    """

    id: str
    programme_id: str
    statement: str = Field(..., description="The claim")
    failure_criterion: str = Field(
        ..., description="What observation would falsify it (commitment 3)"
    )
    variables_involved: list[str] = Field(
        ..., description="Subset of the programme's allowed_variables"
    )
    status: HypothesisStatus = HypothesisStatus.proposed
    created_at: str = Field(default_factory=_utc_now)

    ontological_category: str = "information content entity"
    aboutness: str = "a relation between a variable and an outcome"


class Bundle(BaseModel):
    """The auxiliary assumptions of a trial (commitment 6: controlled bundle).

    Ontological category: information content entity
    Aboutness: the auxiliary assumptions of a trial
    """

    id: str
    trial_id: str
    code_ref: str = Field(..., description="Reference to the code version")
    code_hash: str | None = Field(
        default=None,
        description="SHA-256 hash of the captured code text (content address). "
        "When present, the code content is stored in code_snippets and is "
        "reproducible. When absent (old bundles), reproducibility depends on "
        "code_ref path survival.",
    )
    code_hash_extra_json: str | None = Field(
        default=None,
        description="JSON array of additional code_hash values for locally-"
        "imported modules captured recursively (commitment 2 + 5). The "
        "primary code_hash is the executor; these are its dependencies. "
        "When absent (old bundles), only the primary was captured.",
    )
    env_ref: str = Field(..., description="Reference to the environment")
    seeds_json: str = Field(..., description="JSON array of seeds")
    splits_json: str = Field(..., description="JSON description of data splits")
    data_refs_json: str | None = Field(
        default=None,
        description="JSON array of DataRef IDs (structured data provenance)",
    )
    baseline_ref: str | None = Field(
        default=None, description="Reference to the baseline configuration"
    )
    created_at: str = Field(default_factory=_utc_now)

    ontological_category: str = "information content entity"
    aboutness: str = "the auxiliary assumptions of a trial"

    def is_complete(self) -> bool:
        """Check if all required bundle fields are captured (commitment 6)."""
        return all([self.code_ref, self.env_ref, self.seeds_json, self.splits_json])


class DataRef(BaseModel):
    """A reference to a dataset provided by the Data Source.

    Ontological category: information content entity
    Aboutness: a reproducible reference to a data item about a distribution

    Two regimes, matching the two ontological pathways:
    - generated: created by a process the system controls
      (generator code + seed)
    - captured: observed from the world
      (source + hash + version)
    """

    id: str
    split: str = Field(..., description="train | validation | test")
    regime: str = Field(..., description="generated | captured")

    # Provenance (primary record)
    # Generated: how to regenerate
    generator_code_ref: str | None = Field(
        default=None, description="Path to generation script (in code storage)"
    )
    generator_code_hash: str | None = Field(
        default=None,
        description="SHA-256 hash of the captured generator code (content address). "
        "When present, the generator code is stored in code_snippets.",
    )
    generator_seed: int | None = Field(default=None)
    generator_params: dict[str, Any] | None = Field(default=None)

    # Captured: how to find and verify
    source_uri: str | None = Field(
        default=None, description="file://, s3://, hf://, stream://"
    )
    content_hash: str | None = Field(
        default=None, description="SHA-256 of the dataset content"
    )
    version: str | None = Field(
        default=None, description="version tag / commit hash / dataset revision"
    )

    # Stream capture provenance (captured + temporal)
    capture_window_start: str | None = Field(default=None)
    capture_window_end: str | None = Field(default=None)
    capture_source_metadata: dict[str, Any] | None = Field(default=None)

    # Verification (secondary)
    schema_hash: str | None = Field(default=None)
    size_bytes: int | None = Field(default=None)
    num_samples: int | None = Field(default=None)

    # Storage location (where the carrier lives — data storage, not code storage)
    storage_uri: str | None = Field(default=None)

    # Reproducibility risk
    reproducibility_risk: str = Field(
        default="none",
        description="none | external-dependency | temporal-source",
    )

    # Process boundary timestamp (when the data item was created)
    created_at: str = Field(default_factory=_utc_now)

    ontological_category: str = "information content entity"
    aboutness: str = "a reproducible reference to a data item about a distribution"


class Trial(BaseModel):
    """A configuration proposed for execution.

    Ontological category: data item
    Aboutness: a configuration proposed for execution
    """

    id: str
    programme_id: str
    hypothesis_id: str
    config_json: str = Field(..., description="JSON configuration for the trial")
    bundle_id: str | None = Field(
        default=None, description="Reference to the bundle (required before execution)"
    )
    status: TrialStatus = TrialStatus.designed
    duration_seconds: float | None = Field(
        default=None,
        description="Execution duration in seconds (set when trial completes or fails)",
    )
    artifact_path: str | None = Field(
        default=None,
        description="Path to the trial's artifact workspace directory",
    )
    executor_output_json: str | None = Field(
        default=None,
        description="Raw executor output JSON (durable record — the "
        "in-memory executor cache dies on restart, SQLite does not)",
    )
    started_at: str | None = Field(
        default=None,
        description="When execution actually began (→running). Distinct "
        "from created_at, which is design time.",
    )
    finished_at: str | None = Field(
        default=None,
        description="When the trial reached its end boundary "
        "(→completed/failed/retryable/abandoned)",
    )
    created_at: str = Field(default_factory=_utc_now)

    ontological_category: str = "data item"
    aboutness: str = "a configuration proposed for execution"


class Observation(BaseModel):
    """A quality measured during a trial.

    Ontological category: data item
    Aboutness: a quality measured during a trial

    Note: the quality itself is not stored; the data item that records it is.
    This is the quality/data-item separation (Rule 5.5) enforced structurally.
    """

    id: str
    trial_id: str
    metrics_json: str = Field(
        ..., description="JSON dict of measured values (data items about qualities)"
    )
    variance_json: str = Field(
        ..., description="JSON dict of variance across seeds (commitment 7)"
    )
    spatiotemporal_region: str = Field(
        ..., description="Where+when the run occurred (4D footprint)"
    )
    created_at: str = Field(default_factory=_utc_now)

    ontological_category: str = "data item"
    aboutness: str = "a quality measured during a trial"


class Belief(BaseModel):
    """The posterior over the objective landscape.

    Ontological category: information content entity
    Aboutness: the posterior over the objective landscape
    """

    id: str
    programme_id: str
    state_json: str = Field(
        ..., description="JSON representation of the belief state"
    )
    updated_at: str = Field(default_factory=_utc_now)

    ontological_category: str = "information content entity"
    aboutness: str = "the posterior over the objective landscape"


class Conclusion(BaseModel):
    """A hypothesis acceptance/rejection (immutable once created).

    Ontological category: information content entity
    Aboutness: a hypothesis acceptance/rejection

    Created at the process boundary (trial end or budget exhaustion).
    Once closed, a conclusion cannot be revised; a new hypothesis must be
    created if the programme continues.
    """

    id: str
    hypothesis_id: str
    programme_id: str
    verdict: Verdict
    evidence_ref: str = Field(
        ..., description="Reference to the evidence (observation IDs, belief state)"
    )
    evidence_summary: str = Field(
        ..., description="Human-readable summary of the evidence"
    )
    created_at: str = Field(default_factory=_utc_now)

    ontological_category: str = "information content entity"
    aboutness: str = "a hypothesis acceptance/rejection"


class CodeSnippet(BaseModel):
    """A captured code text, content-addressed by SHA-256.

    Ontological category: textual entity (model source code — the definition)
    Aboutness: the training or generator code that a bundle or data ref
    references. The code-as-text is the ICE; the file on disk is the carrier.
    Storing the text here makes the ICE survive carrier destruction (Rule 5.4).

    Content-addressing: the code_hash is the SHA-256 of the code text.
    Multiple bundles using the same script share one entry (deduplication).
    """

    code_hash: str = Field(..., description="sha256:hex of the code text")
    code_text: str = Field(..., description="The actual source code")
    language: str = Field(default="python", description="Programming language")
    captured_at: str = Field(default_factory=_utc_now)
    original_path: str | None = Field(
        default=None, description="Original file path (for reference only)"
    )
    size_bytes: int = Field(..., description="Size of the code text in bytes")

    ontological_category: str = "textual entity"
    aboutness: str = "the training or generator code referenced by a bundle or data ref"


class PromotionVerdict(str, Enum):
    promote = "promote"
    reject = "reject"
    hold = "hold"
    rollback = "rollback"


class CandidateVersion(BaseModel):
    """A versioned researcher specification with parent lineage (RSI Phase 0).

    Ontological category: information content entity
    Aboutness: a researcher version specification — which model, code,
    search policy, and capability profile performed research work.

    The thing *doing* the research is a durable, auditable object.
    parent_id forms the lineage ("never latest wins"): a candidate is a
    spec, immutable once registered; whether it is champion or challenger
    is derived from promotion_decisions, not stored here.
    """

    id: str
    parent_id: str | None = Field(
        default=None, description="Parent candidate id; NULL = genesis"
    )
    code_artifact_digest: str = Field(
        ..., description="Digest of the research code/harness the candidate runs"
    )
    harness_artifact_digest: str | None = Field(
        default=None,
        description="Digest of the client harness, if distinct from the research code",
    )
    model_ref: str = Field(..., description="Which model drives the researcher")
    search_policy_ref: str | None = Field(
        default=None,
        description="Prompt/policy reference (URI or code_snippet hash)",
    )
    capability_profile: dict[str, Any] = Field(
        ..., description="Tools/roles available to this candidate"
    )
    created_at: str = Field(default_factory=_utc_now)

    ontological_category: str = "information content entity"
    aboutness: str = "a researcher version specification"


class EvaluationContract(BaseModel):
    """A versioned, insert-only evaluation policy for a programme (RSI Phase 0).

    Ontological category: information content entity (directive ICE)
    Aboutness: the evaluation policy under which a candidate's work is judged.

    Insert-only: no update path exists, so a contract is immutable by
    surface. Supersession is a new row with version = max+1 for the same
    programme. holdouts records hidden-evaluation refs opaquely — the
    mechanics that use them arrive with the evaluator role (Phase 2).
    """

    id: str
    programme_id: str
    version: int = Field(..., ge=1)
    metrics: dict[str, Any] = Field(
        ..., description="Metrics + directions the evaluation must produce"
    )
    holdouts: dict[str, Any] | None = Field(
        default=None, description="Hidden-holdout references (opaque to candidates)"
    )
    budget: dict[str, Any] | None = Field(
        default=None, description="Evaluation budget"
    )
    promotion_policy: dict[str, Any] = Field(
        ..., description="Thresholds/criteria for promotion decisions"
    )
    created_at: str = Field(default_factory=_utc_now)

    ontological_category: str = "information content entity"
    aboutness: str = "an evaluation policy for a programme"


class PromotionDecision(BaseModel):
    """An attributed, evidence-referenced verdict on a candidate (RSI Phase 0).

    Ontological category: information content entity
    Aboutness: a candidate promotion verdict with attribution.

    Insert-only event record: reversal is a new decision (verdict
    'rollback'), never a mutation. decided_by is required — attribution
    is a first-class property of the decision.
    """

    id: str
    candidate_id: str
    contract_id: str | None = Field(
        default=None, description="Contract the decision was made under, if any"
    )
    verdict: PromotionVerdict
    evidence_refs: list[str] = Field(
        ..., description="Trial/observation/archive references supporting the verdict"
    )
    rationale: str = Field(..., description="Why this verdict was reached")
    decided_by: str = Field(
        ..., description="Attribution: 'human', operator id, or role ref"
    )
    created_at: str = Field(default_factory=_utc_now)

    ontological_category: str = "information content entity"
    aboutness: str = "a candidate promotion verdict with attribution"


# --- MECE discipline (Rule 5.1 enforcement) ---

# Map each entity type to its single allowed ontological category.
ENTITY_CATEGORIES: dict[type[BaseModel], str] = {
    Programme: "information content entity",
    Hypothesis: "information content entity",
    Trial: "data item",
    Observation: "data item",
    Belief: "information content entity",
    Conclusion: "information content entity",
    Bundle: "information content entity",
    DataRef: "information content entity",
    CodeSnippet: "textual entity",
    CandidateVersion: "information content entity",
    EvaluationContract: "information content entity",
    PromotionDecision: "information content entity",
}


def verify_mece(entity: BaseModel) -> None:
    """Verify an entity's ontological_category matches its type's allowed
    category (Rule 5.1: single-category placement).

    Raises ValueError if the category does not match.
    """
    expected = ENTITY_CATEGORIES.get(type(entity))
    if expected is None:
        raise ValueError(f"Unknown entity type: {type(entity).__name__}")
    actual = getattr(entity, "ontological_category", None)
    if actual != expected:
        raise ValueError(
            f"MECE violation for {type(entity).__name__}: "
            f"expected '{expected}', got '{actual}'"
        )
