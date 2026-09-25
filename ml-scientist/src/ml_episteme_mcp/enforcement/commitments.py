"""Enforcement layer — the 10 commitments as rejection points.

Each commitment is a function that returns None if the check passes,
or an error string if it fails. Tools call these before proceeding.

The commitments are the governance backbone of the system. They enforce
the philosophy from a-00 §5 as code.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..state.models import Hypothesis, Programme, Trial, Bundle
from ..state.store import StateStore


# --- Commitment 1: The loop is the unit ---


def check_loop_is_unit(trial: Trial | None, programme_id: str) -> str | None:
    """Commitment 1: The loop is the unit.

    Rejects orphan runs — trials that don't belong to the given programme.
    """
    if trial is None:
        return "Trial not found"
    if trial.programme_id != programme_id:
        return "The loop is the unit: trial does not belong to this programme"
    return None


def check_programme_has_trials(
    programme_id: str, trials: list[Trial]
) -> str | None:
    """Commitment 1: The loop is the unit.

    Rejects completing a programme with zero trials — a programme with
    no experiments has no evidence and nothing to conclude. Applies to
    status='completed' only; abandoning an unexplored direction is a
    legitimate exit and is not checked here.
    """
    if not trials:
        return (
            "The loop is the unit: cannot complete programme "
            f"{programme_id} — it has zero trials. A programme with no "
            "experiments has no evidence. Either run at least one trial "
            "(design_experiment → capture_bundle → run_trial → "
            "record_observation) or close with status='abandoned'."
        )
    return None


def check_programme_active(programme: Programme | None) -> str | None:
    """Commitment 1: The loop is the unit.

    A programme is mutable only while active. completed, abandoned and
    archived are terminal — mutating them (new hypotheses, trials,
    bundles, observations, beliefs, conclusions, contracts) would
    rewrite a closed record. Also rejects mutations against a
    programme that does not exist, which would otherwise create orphan
    rows.
    """
    if programme is None:
        return "Programme not found."
    if programme.status.value != "active":
        return (
            f"The loop is the unit: programme {programme.id} is "
            f"'{programme.status.value}' — a closed programme is "
            "immutable. Mutations require an active programme."
        )
    return None


# --- Commitment 2: Memory precedes optimization ---


def check_memory_precedes_optimization(
    observation_trial_id: str | None,
    trial_id: str,
    trial_programme_id: str | None,
    programme_id: str,
) -> str | None:
    """Commitment 2: Memory precedes optimization.

    The observation must be persisted before the optimizer can use it.
    state.db IS the episodic memory (ADR-0005) — the check verifies the
    recorded observation is bound to this trial and that the trial is
    part of this programme's episodic record.
    """
    if observation_trial_id != trial_id or trial_programme_id != programme_id:
        return (
            "Memory precedes optimization: observation not found in memory. "
            "Call record_observation first."
        )
    return None


# --- Commitment 3: Falsifiability is required for admission ---


def check_falsifiability(failure_criterion: str) -> str | None:
    """Commitment 3: Falsifiability is required for admission.

    Rejects a hypothesis with no failure criterion or a tautological one.
    """
    if not failure_criterion or failure_criterion.strip() == "":
        return "Falsifiability is required for admission: failure_criterion is empty"
    if failure_criterion.lower() in ("the run was unlucky", "bad luck", "noise"):
        return "Falsifiability is required for admission: failure_criterion is tautological"
    return None


# --- Commitment 4: Constraints are first-class, not afterthoughts ---


# Enforced structurally via ConstraintsInput (typed Pydantic model).
# No runtime check needed — the type system enforces it.


# --- Commitment 5: Budget is an epistemic resource ---


def check_budget_exhausted(
    programme: Programme | None,
    trials: list[Trial],
) -> str | None:
    """Commitment 5: Budget is an epistemic resource.

    Rejects when the trial budget is exhausted.
    """
    if programme is None:
        return "Programme not found"
    if len(trials) >= programme.budget_max_trials:
        return (
            "Budget is an epistemic resource: trial budget exhausted. "
            f"Used {len(trials)}/{programme.budget_max_trials} trials. "
            "Call assess_programme and conclude_hypothesis to conclude."
        )
    return None


def check_budget_remaining(
    programme: Programme | None,
    trials: list[Trial],
) -> str | None:
    """Commitment 5 (for get_next_experiment): rejects when budget is 0."""
    if programme is None:
        return "Programme not found"
    remaining = programme.budget_max_trials - len(trials)
    if remaining <= 0:
        return (
            "Budget is an epistemic resource: trial budget exhausted. "
            f"Used {len(trials)}/{programme.budget_max_trials} trials. "
            "Call assess_programme and conclude_hypothesis to conclude."
        )
    return None


def check_wall_time_exhausted(
    programme: Programme | None,
    trials: list[Trial],
) -> str | None:
    """Commitment 5: wall-time budget enforcement.

    Rejects when the cumulative wall time of completed trials exceeds
    the programme's wall-time budget. Uses duration_seconds from each
    trial (set by the executor). If no trials have duration data, the
    check is skipped (can't enforce without data).

    This is a conservative check — it only rejects when we're confident
    the budget is exhausted, based on actual measured durations.
    """
    if programme is None:
        return "Programme not found"
    if programme.budget_max_wall_time_hours <= 0:
        return None  # No wall-time budget set

    completed = [t for t in trials if t.status.value == "completed"]
    if not completed:
        return None  # No completed trials — can't estimate

    # Sum durations where available
    durations = [t.duration_seconds for t in completed if t.duration_seconds is not None]
    if not durations:
        return None  # No duration data — can't enforce

    total_seconds = sum(durations)
    budget_seconds = programme.budget_max_wall_time_hours * 3600

    if total_seconds >= budget_seconds:
        hours_used = total_seconds / 3600
        return (
            "Budget is an epistemic resource: wall-time budget exhausted. "
            f"Used {hours_used:.2f}h / {programme.budget_max_wall_time_hours}h. "
            "Call assess_programme and conclude_hypothesis to conclude."
        )
    return None


# --- Commitment 6: The bundle must be controlled ---


def check_bundle_controlled(trial: Trial, store: StateStore) -> str | None:
    """Commitment 6: The bundle must be controlled.

    Rejects if the bundle is not fully captured.
    """
    if trial.bundle_id is None:
        return "The bundle must be controlled: no bundle linked to this trial"
    bundle = store.get_bundle(trial.bundle_id)
    if bundle is None or not bundle.is_complete():
        return "The bundle must be controlled: bundle is incomplete"
    return None


# --- Commitment 7: Reproducibility is the price of admission ---


def check_reproducibility(variance: dict[str, float]) -> str | None:
    """Commitment 7: Reproducibility is the price of admission.

    Rejects a single point estimate with no variance.
    """
    if not variance or all(v == 0 for v in variance.values()):
        return "Reproducibility is the price of admission: variance is required"
    return None


# --- Commitment 8: Programmes, not runs ---


def check_duplicate_conclusion(
    programme_id: str,
    hypothesis_id: str,
    store: StateStore,
) -> str | None:
    """Commitment 8: Programmes, not runs.

    Rejects duplicate conclusions for the same hypothesis.
    """
    existing = store.list_conclusions(programme_id)
    for c in existing:
        if c.hypothesis_id == hypothesis_id:
            return (
                "Programmes, not runs: this hypothesis already has a conclusion "
                f"(verdict: {c.verdict.value}). Create a new hypothesis to test further."
            )
    return None


def check_duplicate_programme(goal: str, store: StateStore) -> str | None:
    """Commitment 8: Programmes, not runs.

    Rejects if an active programme already has a goal sharing a long
    normalized prefix (first 80 chars) with the proposed goal. A
    duplicate goal means the LLM should add a hypothesis to the
    existing programme, not create a new one.
    """
    # list_programmes_paginated returns raw dicts, not Programme objects.
    active = store.list_programmes_paginated(
        status_filter="active", per_page=1000
    )
    goal_prefix = goal.strip().lower()[:80]
    for p in active:
        existing = p["goal"].strip().lower()[:80]
        if goal_prefix == existing:
            return (
                "Programmes, not runs: an active programme already covers "
                f"this goal: {p['id']} — \"{p['goal'][:100]}\". "
                f"Add a hypothesis to {p['id']} instead of creating a "
                f"duplicate. Call formulate_hypothesis(programme_id={p['id']}, "
                "...) or use update_metric_direction if the programme's "
                "direction needs changing."
            )
    return None


# Anchored to paper/pdf/archive context — a bare "capture.*artifacts"
# pattern would false-positive on legitimate goals like "capture
# artifacts of generalization in the loss landscape".
_UTILITY_PATTERNS = [
    r"paper\s+capture",
    r"capture\b.*\b(paper|pdf|latex|bibliograph)",
    r"\b(paper|pdf|latex)\b.*\bcapture\b",
    r"paper_id",
    r"re-?run\b.*\b(paper|archive)",
    r"archive\b.*\b(paper|pdf)\b",
]


def check_programme_is_research(goal: str) -> str | None:
    """Commitment 8: Programmes, not runs.

    Rejects goals describing utility tasks (paper/artifact capture,
    paper-archival re-runs) rather than research questions. A utility
    task belongs inside a research programme as a trial, not as a
    standalone programme. Also rejects an empty goal — a programme
    must carry a research question.
    """
    normalized = goal.strip().lower()
    if not normalized:
        return (
            "Programmes, not runs: a programme must carry a research "
            "question — got an empty goal. A programme is a long-lived "
            "container for a line of inquiry; give it a goal that "
            "describes what it is trying to find out."
        )
    for pattern in _UTILITY_PATTERNS:
        if re.search(pattern, normalized):
            return (
                "Programmes, not runs: this goal describes a utility "
                f"task, not a research question: \"{goal[:80]}\". "
                "Utility tasks (paper/artifact capture, paper-archival "
                "re-runs) should be trials inside an existing research "
                "programme. Call list_active_programmes to find the "
                "parent programme, then design_experiment to add a trial."
            )
    return None


# --- Commitment 9: Belief is a tracked quantity ---


def check_belief_has_metrics(metrics: dict[str, Any]) -> str | None:
    """Commitment 9: Belief is a tracked quantity.

    Rejects if the observation has no metrics (cannot move the posterior).
    """
    if not metrics:
        return (
            "Belief is a tracked quantity: observation has no metrics, "
            "cannot update the posterior."
        )
    return None


# --- Commitment 10: The agent is a scientist, not a scribe ---


def check_hypothesis_exists(hypothesis: Hypothesis | None) -> str | None:
    """Commitment 10: The agent is a scientist, not a scribe.

    Rejects if the hypothesis doesn't exist.
    """
    if hypothesis is None:
        return "The agent is a scientist, not a scribe: hypothesis not found"
    return None


def check_hypothesis_testable(hypothesis: Hypothesis) -> str | None:
    """Commitment 10: The agent is a scientist, not a scribe.

    Rejects if the hypothesis is already concluded (not testable).
    """
    if hypothesis.status.value not in ("proposed", "under_test"):
        return (
            f"The agent is a scientist, not a scribe: hypothesis is {hypothesis.status.value} "
            f"(must be proposed or under_test to design experiments)"
        )
    return None


# --- Close-programme evidence enforcement ---


def check_hypothesis_in_programme(
    programme_id: str, hypothesis_id: str, store: StateStore
) -> str | None:
    """Commitment 10: the agent is a scientist, not a scribe.

    Rejects if the hypothesis doesn't exist or doesn't belong to the
    given programme. Used by close_programme to verify the hypothesis
    being concluded is real and linked.
    """
    hypothesis = store.get_hypothesis(hypothesis_id)
    if hypothesis is None:
        return "The agent is a scientist, not a scribe: hypothesis not found"
    if hypothesis.programme_id != programme_id:
        return (
            "The agent is a scientist, not a scribe: hypothesis does not "
            "belong to this programme"
        )
    return None


def check_evidence_exists(
    programme_id: str, hypothesis_id: str, store: StateStore
) -> str | None:
    """Commitment 1: the loop is the unit.

    Rejects if no completed trial exists for this hypothesis. A
    conclusion requires at least one completed experiment. Failed
    trials do not count as evidence — a crashed trial produces no
    metrics and cannot ground a conclusion.
    """
    trials = store.list_trials(programme_id)
    completed = [
        t for t in trials
        if t.hypothesis_id == hypothesis_id and t.status.value == "completed"
    ]
    if not completed:
        return (
            "The loop is the unit: no completed trials for hypothesis "
            f"{hypothesis_id}. Run at least one trial and record its "
            "results before concluding."
        )
    return None


def check_all_results_recorded(
    programme_id: str, hypothesis_id: str, store: StateStore
) -> str | None:
    """Commitment 7: reproducibility is the price of admission.

    Rejects if any completed trial for this hypothesis has zero
    observations. Every completed trial's results must be recorded
    before the hypothesis can be concluded.
    """
    trials = store.list_trials(programme_id)
    completed = [
        t for t in trials
        if t.hypothesis_id == hypothesis_id and t.status.value == "completed"
    ]
    unrecorded = [
        t for t in completed
        if len(store.list_observations(t.id)) == 0
    ]
    if unrecorded:
        ids = ", ".join(t.id for t in unrecorded)
        return (
            "Reproducibility is the price of admission: completed trial(s) "
            f"have no observations: {ids}. Record results before concluding."
        )
    return None


def check_observation_prerequisites(trial: Trial | None) -> str | None:
    """Commitments 6 + 1: an observation requires a completed trial with a
    captured bundle.

    Rejects if:
      - the trial doesn't exist
      - the trial has no captured bundle (commitment 6 — reproducibility)
      - the trial is not completed (commitment 1 — the loop is the unit)

    This prevents an agent from recording observations for trials that
    were never run through the server's executor, or that have no bundle
    for reproducibility.
    """
    if trial is None:
        return "Trial not found. record_observation requires an existing trial."
    if trial.bundle_id is None:
        return (
            "The bundle must be controlled: trial has no captured bundle. "
            "Call capture_bundle before record_observation."
        )
    if trial.status.value != "completed":
        return (
            f"The loop is the unit: trial status is '{trial.status.value}', "
            f"not 'completed'. run_trial must complete before record_observation."
        )
    return None


def check_belief_recorded(programme_id: str, store: StateStore) -> str | None:
    """Commitment 2: memory precedes optimization.

    Rejects if no belief update has been recorded for the programme. A
    conclusion requires that the posterior was updated at least once —
    the agent cannot skip the statistical analysis stage of the loop.
    """
    beliefs = store.list_beliefs(programme_id)
    if not beliefs:
        return (
            "Memory precedes optimization: no belief update found for this "
            "programme. Call update_belief before conclude_hypothesis."
        )
    return None


# --- RSI Phase 0: candidate lineage ---


DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def check_digest_claim(
    field: str, value: str | None, resolves
) -> str | None:
    """Resolution-or-none for artifact digests.

    A content hash is a claim that bytes exist under that key — a
    well-formed hash of nothing is still a lie. ``resolves`` is a
    callable (``store.has_blob`` locally, a channel call across
    loops) returning True when the digest is a live content
    address. ``"none"`` is the explicit, honest declaration of no
    artifact — never a fake digest.
    """
    if value is None or value == "none":
        return None
    if not DIGEST_RE.match(value):
        return (
            f"{field} '{value}' is not a content digest — expected "
            "sha256:<64 hex> resolving to an ingested artifact, or "
            "'none' to declare no artifact."
        )
    if not resolves(value):
        return (
            f"{field} '{value}' does not resolve to any ingested "
            "artifact — a hash of nothing is a claim with no "
            "referent. Ingest the bytes first and pass the returned "
            "digest, or pass 'none' to declare no artifact."
        )
    return None


def check_candidate_parent_exists(
    parent_id: str | None, store: StateStore
) -> str | None:
    """Lineage integrity: a non-genesis candidate must name an existing parent.

    Cycles are impossible by construction — parent_id must reference a
    pre-existing insert-only row, so no candidate can ever point at a
    descendant.
    """
    if parent_id is None:
        return None
    if store.get_candidate_version(parent_id) is None:
        return (
            f"Candidate parent not found: {parent_id}. "
            "Register the parent candidate first, or omit parent_id for a "
            "genesis candidate."
        )
    return None


def check_programme_candidate_exists(
    candidate_version_id: str | None, store: StateStore
) -> str | None:
    """Rejects create_programme(candidate_version_id=…) naming a nonexistent
    candidate — attribution must point at a real registered candidate."""
    if candidate_version_id is None:
        return None
    if store.get_candidate_version(candidate_version_id) is None:
        return (
            f"Candidate not found: {candidate_version_id}. "
            "Call register_candidate first, then pass its id as "
            "candidate_version_id."
        )
    return None


def check_contract_programme_exists(
    programme_id: str, store: StateStore
) -> str | None:
    """An evaluation contract must attach to a real programme."""
    if store.get_programme(programme_id) is None:
        return f"Programme not found: {programme_id}."
    return None


def check_decision_valid(
    verdict: str,
    rationale: str,
    decided_by: str,
    candidate_id: str,
    contract_id: str | None,
    store: StateStore,
) -> str | None:
    """Promotion decisions require a valid verdict, attribution, and real
    references. Attribution is first-class: a decision with no decided_by
    is not a decision."""
    if verdict not in ("promote", "reject", "hold", "rollback"):
        return (
            f"verdict must be one of promote|reject|hold|rollback, "
            f"got: {verdict}"
        )
    if not rationale or not rationale.strip():
        return "rationale is required — a promotion decision must say why."
    if not decided_by or not decided_by.strip():
        return (
            "decided_by is required — attribution is a first-class "
            "property of a promotion decision."
        )
    if store.get_candidate_version(candidate_id) is None:
        return f"Candidate not found: {candidate_id}."
    if contract_id is not None and store.get_evaluation_contract(contract_id) is None:
        return f"Evaluation contract not found: {contract_id}."
    return None
