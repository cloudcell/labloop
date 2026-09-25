"""Shared tool helpers — deliberate per-package duplication (ADR-0001)."""

from __future__ import annotations

import json
from typing import Any, TypedDict

from mcp.types import CallToolResult, TextContent


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


# ── Declared output models (W5b) ────────────────────────────────


class CheckInvariantsOut(TypedDict, total=False):
    server: str | None
    status: str | None
    checked_at: str | None
    duration_ms: float | None
    checks: list[dict] | None
    trigger: str | None
    log_file: str | None
