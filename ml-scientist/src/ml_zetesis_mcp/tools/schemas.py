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


class OpenInvestigationOut(TypedDict, total=False):
    investigation_id: str | None
    status: str | None


class RecordFindingOut(TypedDict, total=False):
    finding_id: str | None
    status: str | None


class ConcludeInvestigationOut(TypedDict, total=False):
    investigation_id: str | None
    status: str | None
    verdict: str | None
    claim_ids: list[str] | None
    claim_status: str | None
    findings_minted: int | None
    edges_created: int | None
    implications: dict | None


class GetInvestigationOut(TypedDict, total=False):
    investigation: dict | None
    evidence_refs: list[dict] | None
    findings: list[dict] | None


class ListInvestigationsOut(TypedDict, total=False):
    investigations: list[dict] | None
    total: int | None


class AbandonInvestigationOut(TypedDict, total=False):
    investigation_id: str | None
    status: str | None


class CloseCampaignOut(TypedDict, total=False):
    campaign_id: str | None
    status: str | None
    promotion_score: int | float | None
    champion_mean: int | float | None
    challenger_mean: int | float | None


class DropFindingOut(TypedDict, total=False):
    finding_id: str | None
    status: str | None


class GetCampaignOut(TypedDict, total=False):
    campaign: dict | None
    results: list[dict] | None
    spawns: list[dict] | None
    evidence_refs: list[dict] | None


class ListCampaignsOut(TypedDict, total=False):
    campaigns: list | None
    total: int | None


class ListSearchPoliciesOut(TypedDict, total=False):
    policies: list[dict] | None


class OpenCampaignOut(TypedDict, total=False):
    campaign_id: str | None
    champion_id: str | None
    challenger_id: str | None
    primary_metric: str | None
    status: str | None


class PullCampaignEvidenceOut(TypedDict, total=False):
    evidence_ref_id: str | None
    ref_ids: list[str] | None
    result: dict | None


class PullEvidenceOut(TypedDict, total=False):
    evidence_ref_id: str | None
    ref_ids: list[str] | None
    result: dict | str | None


class RecordCampaignResultOut(TypedDict, total=False):
    result_id: str | None
    campaign_id: str | None
    arm: str | None


class RecordPromotionVerdictOut(TypedDict, total=False):
    campaign_id: str | None
    verdict: str | None
    decision_id: str | None
    claim_id: str | None
    claim_status: str | None


class RefreshRosterOut(TypedDict, total=False):
    upstream_candidates: int | None
    adopted: list[str] | None
    already_tracked: int | None
    incumbent: str | None
    reconciled: dict | None
    dry_run: bool | None
    would_adopt: list[str] | None
    would_reconcile: dict | None


class RegisterChallengerOut(TypedDict, total=False):
    candidate_id: str | None
    derived_status: str | None


class RegisterSearchPolicyOut(TypedDict, total=False):
    policy_id: str | None
    name: str | None
    version: int | None
    status: str | None


class SpawnCampaignProgrammeOut(TypedDict, total=False):
    spawn_id: str | None
    campaign_id: str | None
    arm: str | None
    programme_id: str | None
    candidate_version_id: str | None
    metric_direction: str | None
    budget: dict | None

class CheckInvariantsOut(TypedDict, total=False):
    server: str | None
    status: str | None
    checked_at: str | None
    duration_ms: int | float | None
    checks: list[dict] | None
    trigger: str | None
    log_file: str | None


class ListCandidatesOut(TypedDict, total=False):
    candidates: list[dict] | None
    total: int | None


class GetIncumbentOut(TypedDict, total=False):
    candidate_id: str | None
    candidate: dict | None
