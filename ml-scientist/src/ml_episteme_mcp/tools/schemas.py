"""Shared input schemas for tool handlers.

Typed, not strings (commitment 4: constraints are first-class).
"""

from __future__ import annotations

import json
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field

from mcp.types import CallToolResult, TextContent


def fail(msg: str) -> CallToolResult:
    """Return a tool-error result — surfaces as MCP isError=True.

    Returning (not raising) keeps the wire text exactly ``msg`` —
    ``ToolError`` would gain an ``Error executing tool <name>:``
    prefix and break clients parsing the payload. The content keeps
    the legacy ``{"error": ...}`` JSON shape during transition.
    """
    return CallToolResult(
        content=[TextContent(type="text", text=msg)],
        is_error=True,
    )


def ok(payload: dict) -> CallToolResult:
    """Return a success result carrying the payload twice: JSON text
    for clients that parse ``content``, and ``structured_content``
    for clients that want the object directly.

    The payload is normalized through ``json.dumps(default=str)``
    first — bytes/BLOB columns and other non-serializable values
    become strings exactly as the legacy text payloads did — and the
    cleaned object feeds both channels, so text and
    structured_content are identical by construction (the parity
    invariant the suite asserts).
    """
    clean = json.loads(json.dumps(payload, default=str))
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(clean))],
        structured_content=clean,
    )



def coerce_json(value: Any, expected: type, name: str) -> Any:
    """Accept dicts/lists sent as JSON-encoded strings.

    LLM clients frequently serialize nested parameters as JSON strings
    ('{"max_trials": 10}' instead of a real object). The MCP schema
    declares such params as `dict | str` / `list | str` unions; this
    helper decodes the string form. Raises ValueError on anything
    unparseable — the caller's except block returns it as a tool error.
    """
    if isinstance(value, expected):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            raise ValueError(
                f"{name} must be a {expected.__name__}; "
                "got a string that is not valid JSON"
            )
        if isinstance(parsed, expected):
            return parsed
        raise ValueError(
            f"{name} must be a {expected.__name__}; "
            f"JSON string decoded to {type(parsed).__name__}"
        )
    raise ValueError(
        f"{name} must be a {expected.__name__} or JSON string; "
        f"got {type(value).__name__}"
    )


def coerce_int_list(value: Any, name: str) -> list[int]:
    """Accept a list of ints, a JSON-encoded list, or a bare int.

    LLM clients frequently send seeds as '[7, 42]' or even 7.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise ValueError(
                f"{name} must be a list of integers; "
                "got a string that is not valid JSON"
            )
    if isinstance(value, int) and not isinstance(value, bool):
        value = [value]
    if not isinstance(value, list):
        raise ValueError(
            f"{name} must be a list of integers; got {type(value).__name__}"
        )
    out: list[int] = []
    for v in value:
        if isinstance(v, bool) or not isinstance(v, (int, str)):
            raise ValueError(f"{name} elements must be integers")
        try:
            out.append(int(v))
        except ValueError:
            raise ValueError(f"{name} elements must be integers")
    return out


class ConstraintsInput(BaseModel):
    """Typed constraints (commitment 4: first-class, not afterthoughts).

    Extra fields are allowed so LLMs can pass additional constraints without
    crashing the tool. Only known fields are used by the server.
    """

    model_config = {"extra": "allow"}

    gpu_memory_gb: float | None = None
    max_training_hours_per_trial: float | None = None
    max_parameter_count: int | None = None


class BudgetInput(BaseModel):
    """Budget as an epistemic resource (commitment 5).

    Extra fields are allowed so LLMs can pass additional budget dimensions
    (e.g. min_seeds_per_config, compute_estimate_hours) without crashing.
    Only max_trials and max_wall_time_hours are enforced by the core;
    extras are preserved under 'budget_extras' in the constraints record
    and admitted in the create_programme response — never silently
    dropped.
    """

    model_config = {"extra": "allow"}

    max_trials: int = Field(..., ge=0)
    max_wall_time_hours: float = Field(0.0, ge=0.0)


# ── Declared output models (W5b) ────────────────────────────────
# TypedDict(total=False): every key optional — payloads legitimately
# vary by branch (e.g. claim_status only when the claims channel
# answers), and undeclared keys pass validation unharmed. These are
# published as each tool's outputSchema; a wrong *type* on a declared
# key fails loudly, which is the point.
#
# Fields not worth typing individually stay `dict`/`list` — an agent
# reading the schema learns the top-level contract, not nested rows.


class CreateProgrammeOut(TypedDict, total=False):
    programme_id: str | None
    status: str | None
    budget_extras_not_enforced: list[str] | None


class ListActiveProgrammesOut(TypedDict, total=False):
    active_programmes: list[dict] | None
    count: int | None


class ConcludeHypothesisOut(TypedDict, total=False):
    conclusion_id: str | None
    hypothesis_id: str | None
    verdict: str | None
    status: str | None
    claim_status: str | None
    claim_error: str | None


class CloseProgrammeOut(TypedDict, total=False):
    programme_id: str | None
    status: str | None
    auto_marked: list[dict] | None
    trials_auto_marked: list[dict] | None
    archived: dict | None
    archive_error: str | None


class FormulateHypothesisOut(TypedDict, total=False):
    hypothesis_id: str | None
    status: str | None


class DesignExperimentOut(TypedDict, total=False):
    trial_id: str | None
    status: str | None


class CaptureBundleOut(TypedDict, total=False):
    bundle_id: str | None
    status: str | None
    code_hash: str | None
    code_hash_extra: list[str] | None
    code_ref: str | None
    warnings: list[dict] | None


class RunTrialOut(TypedDict, total=False):
    trial_id: str | None
    status: str | None
    message: str | None
    executor_output: str | None
    error: str | None


class TrialStatusOut(TypedDict, total=False):
    trial_id: str | None
    status: str | None
    started_at: str | None
    finished_at: str | None
    elapsed_seconds: float | None
    duration_seconds: float | None
    progress: dict | None
    eta_seconds: float | None
    hint: str | None
    artifact_path: str | None
    executor_output: str | None
    cancelled: bool | None
    reason: str | None


class ListTrialsOut(TypedDict, total=False):
    programme_id: str | None
    trials: list[dict] | None


class RecordObservationOut(TypedDict, total=False):
    observation_id: str | None
    status: str | None


class UpdateBeliefOut(TypedDict, total=False):
    belief_id: str | None
    status: str | None


class RegisterCandidateOut(TypedDict, total=False):
    candidate_id: str | None
    status: str | None


class CandidateScorecardOut(TypedDict, total=False):
    candidate_version_id: str | None
    programmes: list[dict] | None
    contract_id: str | None
    totals: dict | None



class ArchivePendingProgrammesOut(TypedDict, total=False):
    archived: list | None
    marked_archived: int | None
    errors: list | None
    skipped: int | None
    dry_run: bool | None
    would_archive: list | None
    would_mark_archived: list | None


class AssessProgrammeOut(TypedDict, total=False):
    programme_id: str | None
    health: str | None
    total_trials: int | None
    completed_trials: int | None
    total_hypotheses: int | None
    total_conclusions: int | None
    best_trials_from_optimizer: list[dict] | None
    total_observations: int | None


class CancelTrialOut(TypedDict, total=False):
    trial_id: str | None
    status: str | None
    cancelled: bool | None
    executor_output: str | None


class CaptureBundleFromCodeHashOut(TypedDict, total=False):
    bundle_id: str | None
    status: str | None
    code_hash: str | None
    code_hash_extra: list[str] | None
    code_ref: str | None
    warnings: list | None


class CapturePendingArtifactsOut(TypedDict, total=False):
    trials_scanned: int | None
    trials_with_existing: int | None
    captured: list | None
    oversized: list | None
    lost: list | None


CorrectTrialStatusOut = TypedDict(
    "CorrectTrialStatusOut",
    {
    'trial_id': str | None,
    'from': str | None,
    'to': str | None,
    'corrected_at': str | None,
    },
    total=False,
)


class CreateEvaluationContractOut(TypedDict, total=False):
    contract_id: str | None
    version: int | None
    status: str | None


class GetArchiveOut(TypedDict, total=False):
    archive: dict | None


class GetArchivedProgrammeOut(TypedDict, total=False):
    id: str | None
    goal: str | None
    constraints_json: str | None
    allowed_variables_json: str | None
    budget_max_trials: int | None
    budget_max_wall_time_hours: int | float | None
    metric_direction: str | None
    candidate_version_id: Any | None
    status: str | None
    created_at: str | None
    archive_id: str | None
    archive_path: str | None
    archived_at: str | None
    row_count: int | None
    hypotheses: list[dict] | None
    trials: list[dict] | None
    beliefs: list[dict] | None
    conclusions: list[dict] | None
    evaluation_contracts: list | None
    candidate_versions: list | None
    observations: list[dict] | None
    bundles: list[dict] | None
    data_refs: list | None
    code_snippets: list[dict] | None
    trial_artifacts: list[dict] | None
    artifact_files: list[dict] | None


class GetCandidateOut(TypedDict, total=False):
    candidate: dict | None


class GetCandidateLineageOut(TypedDict, total=False):
    lineage: list[dict] | None


class GetEvaluationContractOut(TypedDict, total=False):
    contract: dict | None


class GetIncumbentOut(TypedDict, total=False):
    candidate_id: str | None
    candidate: dict | None


class GetNextExperimentOut(TypedDict, total=False):
    programme_id: str | None
    remaining_budget: dict | None
    next_config: dict | None


class ListArchivesOut(TypedDict, total=False):
    archives: list[dict] | None


class ListCandidatesOut(TypedDict, total=False):
    candidates: list[dict] | None
    total: int | None


class ListHypothesesOut(TypedDict, total=False):
    programme_id: str | None
    hypotheses: list[dict] | None


class ListProgrammesOut(TypedDict, total=False):
    programmes: list[dict] | None
    total: int | None


class ListPromotionDecisionsOut(TypedDict, total=False):
    decisions: list[dict] | None


class MarkRetryableOut(TypedDict, total=False):
    trial_id: str | None
    status: str | None
    reason: str | None


class RecordPromotionDecisionOut(TypedDict, total=False):
    decision_id: str | None
    status: str | None


class UpdateMetricDirectionOut(TypedDict, total=False):
    programme_id: str | None
    metric_direction: str | None
    status: str | None


class VerifyArchiveOut(TypedDict, total=False):
    verified: bool | None
    error: str | None
    sealed: bool | None


class VerifyDataOut(TypedDict, total=False):
    verified: bool | None
    recorded_hash: str | None
    computed_hash: str | None
    error: str | None

class CheckInvariantsOut(TypedDict, total=False):
    server: str | None
    status: str | None
    checked_at: str | None
    duration_ms: int | float | None
    checks: list[dict] | None
    trigger: str | None
    log_file: str | None


class DescribeBlobOut(TypedDict, total=False):
    exists: bool | None
    resolved_in: list[str] | None
    size_bytes: int | None
    content_type: str | None
    captured_at: str | None


class PrepareDataOut(TypedDict, total=False):
    data_ref_id: str | None
    split: str | None
    regime: str | None
    content_hash: str | None
    reproducibility_risk: Any
    storage_uri: str | None
