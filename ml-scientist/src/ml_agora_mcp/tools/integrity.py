"""Integrity tool — check_invariants.

Audits the status hub's channels against its invariants and returns
structured violations. Every invocation is logged to
<db_dir>/logs/ — the check trail is itself evidence. Report-only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from ..integrity.checks import run_and_log
from .schemas import ok, CheckInvariantsOut
from mcp.types import CallToolResult


def register(
    mcp, adaptors, *, log_dir: Path,
    integrity_config: dict | None = None,
) -> None:
    """Register the integrity tool.

    integrity_config: the [integrity] table from ml-agora.toml —
    log_max_files.
    """

    @mcp.tool()
    async def check_invariants() -> Annotated[CallToolResult, CheckInvariantsOut]:
        """Audit the lab's channels against the hub's invariants.

        Returns {status: ok|violations, checks: [{name, ok,
        violations, detail}]}: upstream_connectivity (configured-but-
        down channels) and status_reachability (connected channels
        whose status digest fails to serve). Report-only — nothing
        is repaired or mutated. The run is logged to <db_dir>/logs/
        (retention: [integrity] log_max_files).
        """
        return ok(await run_and_log(
                adaptors,
                connectivity=adaptors.connectivity_report(),
                log_dir=log_dir,
                config=integrity_config,
                trigger="tool",
            ))
