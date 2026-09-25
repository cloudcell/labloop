"""Input coercion helpers for tool handlers.

Deliberately duplicated from ml-episteme's tools/schemas.py (ADR-0002):
the two servers share no code — a shared module would be coupling by
another name. Keep both copies small and identical in behaviour.
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


class AssertClaimOut(TypedDict, total=False):
    claim_id: str | None
    deduplicated: bool | None
    status: str | None


class RelateOut(TypedDict, total=False):
    edge_id: str | None
    deduplicated: bool | None
    status: str | None


class GetClaimOut(TypedDict, total=False):
    claim: dict | None


class ListClaimsOut(TypedDict, total=False):
    claims: list[dict] | None
    total: int | None

class CheckInvariantsOut(TypedDict, total=False):
    server: str | None
    status: str | None
    checked_at: str | None
    duration_ms: int | float | None
    checks: list[dict] | None
    trigger: str | None
    log_file: str | None
