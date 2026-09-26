"""Input coercion helpers for tool handlers.

Deliberately duplicated from ml-episteme's tools/schemas.py
(ADR-0001): the loop servers share no code — a shared module would be
coupling by another name. Keep both copies small and identical in
behaviour.
"""

from __future__ import annotations

import json
from typing import Any, TypedDict

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


# ── Declared output models (W5b) ────────────────────────────────
# TypedDict(total=False): every key optional — payloads legitimately
# vary by branch; undeclared keys pass validation unharmed.


class RegisterImproverOut(TypedDict, total=False):
    improver_id: str | None
    is_champion: bool | None
    parent_id: str | None
    proposal_id: str | None


class RecordMetaDecisionOut(TypedDict, total=False):
    decision_id: str | None
    verdict: str | None
    claim_id: str | None
    claim_status: str | None
    edges_created: int | None


class PromotePolicyOut(TypedDict, total=False):
    policy_version_id: str | None
    champion: str | None
    displaced_champion: str | None
    status: str | None


class OpenArmCampaignOut(TypedDict, total=False):
    campaign_id: str | None
    tournament_id: str | None
    arm: str | None
    link_id: str | None
    status: str | None
    contract_id: str | None
    challenger_id: str | None
    budget: dict | None
    seeds: list[int] | None


class CloseArmCampaignOut(TypedDict, total=False):
    tournament_id: str | None
    arm: str | None
    campaign_id: str | None
    result: dict | None


class CloseCanaryOut(TypedDict, total=False):
    canary_id: str | None
    status: str | None
    next_step: str | None


class CloseTournamentOut(TypedDict, total=False):
    tournament_id: str | None
    status: str | None
    recursive_gain: int | float | None
    primary_metric: str | None
    interpretation: str | None


class CorrectTournamentResultOut(TypedDict, total=False):
    result_id: str | None
    tournament_id: str | None
    corrections_count: int | None
    corrected_at: str | None


class CreateMetaContractOut(TypedDict, total=False):
    contract_id: str | None
    version: int | None


class GetImproverOut(TypedDict, total=False):
    improver: dict | None
    decisions: list | None
    policy_versions: list | None
    tournaments: list | None


class GetImproverLineageOut(TypedDict, total=False):
    improver_id: str | None
    depth: int | None
    lineage: list[dict] | None


class GetProposalOut(TypedDict, total=False):
    proposal: dict | None


class ListDecisionsOut(TypedDict, total=False):
    decisions: list | None
    total: int | None


class ListImproversOut(TypedDict, total=False):
    improvers: list[dict] | None
    total: int | None


class ListProposalsOut(TypedDict, total=False):
    proposals: list[dict] | None
    total: int | None


class ListTournamentsOut(TypedDict, total=False):
    tournaments: list | None
    total: int | None


class OpenTournamentOut(TypedDict, total=False):
    tournament_id: str | None
    status: str | None
    contract_frozen: bool | None


class ProposeMetaChangeOut(TypedDict, total=False):
    proposal_id: str | None
    status: str | None
    classification: dict | None
    rejection_reason: str | None
    conditional_note: str | None


class PullArmEvidenceOut(TypedDict, total=False):
    evidence_ref_id: str | None
    upstream_evidence_ref_id: str | None
    ref_ids: list[str] | None
    result: dict | None


class RecordArmResultOut(TypedDict, total=False):
    tournament_id: str | None
    arm: str | None
    campaign_id: str | None
    result: dict | None


class RecordArmVerdictOut(TypedDict, total=False):
    tournament_id: str | None
    arm: str | None
    campaign_id: str | None
    result: dict | None


class RecordCanaryOut(TypedDict, total=False):
    canary_id: str | None
    status: str | None


class RecordTournamentResultOut(TypedDict, total=False):
    result_id: str | None
    tournament_id: str | None
    arm: str | None


class RollbackOut(TypedDict, total=False):
    decision_id: str | None
    verdict: str | None
    was_champion: bool | None
    restored_champion: str | None


class SpawnArmProgrammeOut(TypedDict, total=False):
    tournament_id: str | None
    arm: str | None
    campaign_id: str | None
    spawn: dict | None

class CheckInvariantsOut(TypedDict, total=False):
    server: str | None
    status: str | None
    checked_at: str | None
    duration_ms: int | float | None
    checks: list[dict] | None
    trigger: str | None
    log_file: str | None


class AcknowledgeViolationOut(TypedDict, total=False):
    ack_id: str | None
    status: str | None
    matched_open_violation: bool | None
    open_violations: int | None


class PullEvidenceOut(TypedDict, total=False):
    evidence_ref_id: str | None
    ref_ids: list | None
    result: Any


class GetTournamentOut(TypedDict, total=False):
    tournament: dict | None


class VoidTournamentOut(TypedDict, total=False):
    tournament_id: str | None
    status: str | None
    voided_at: str | None
