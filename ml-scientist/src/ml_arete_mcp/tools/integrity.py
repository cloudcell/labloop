"""Integrity tool — check_invariants.

Audits improver.db against the recursive loop's invariants, including
the one honest cross-server check: minted claim_ids resolving through
the claims channel. Every invocation is logged to <db_dir>/logs/.
Report-only.
"""

from __future__ import annotations

from typing import Annotated

import json

from ..integrity.checks import run_and_log
from .schemas import ok, CheckInvariantsOut
from mcp.types import CallToolResult


def register(
    mcp, store, adaptors, integrity_config: dict | None = None
) -> None:
    """Register the integrity tool.

    integrity_config: the [integrity] table from ml-arete.toml —
    stale_tournament_seconds, log_max_files.
    """

    @mcp.tool()
    async def check_invariants() -> Annotated[CallToolResult, CheckInvariantsOut]:
        """Audit the improver store against the loop's invariants.

        Returns {status: ok|violations, checks: [{name, ok,
        violations, detail}]}: lineage integrity (dangling parents,
        cycles), single-champion pointer, stale open tournaments,
        rejected proposals missing reasons, closed tournaments missing
        recursive_gain, conditional promotions lacking a human
        decided_by, dangling decision references, and minted claims
        that fail to resolve through the claims channel (skipped —
        not ok — when the channel is absent). Report-only. The run is
        logged to <db_dir>/logs/ (retention: [integrity]
        log_max_files).
        """
        payload = await run_and_log(
            store,
            claims=adaptors.claims,
            loop1=adaptors.loop1,
            connectivity=adaptors.connectivity_report(),
            config=integrity_config,
            trigger="tool",
        )
        return ok(payload)
