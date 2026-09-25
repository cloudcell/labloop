"""Integrity tool — check_invariants.

Audits memory.db against the memory layer's invariants and returns
structured violations. Every invocation is logged to
<db_dir>/logs/ — the check trail is itself evidence. Report-only.
"""

from __future__ import annotations

from typing import Annotated

import json

from ..integrity.checks import run_and_log
from .schemas import ok, CheckInvariantsOut
from mcp.types import CallToolResult


def register(mcp, store, integrity_config: dict | None = None) -> None:
    """Register the integrity tool.

    integrity_config: the [integrity] table from ml-anamnesis.toml —
    log_max_files.
    """

    @mcp.tool()
    def check_invariants() -> Annotated[CallToolResult, CheckInvariantsOut]:
        """Audit the claim graph against the memory layer's invariants.

        Returns {status: ok|violations, checks: [{name, ok,
        violations, detail}]}: unsupported high-confidence claims (the
        evidence rule audited), dangling claim-typed references,
        broken supersession chains. Report-only — nothing is repaired
        or mutated. The run is logged to <db_dir>/logs/ (retention:
        [integrity] log_max_files).
        """
        return ok(run_and_log(store, config=integrity_config, trigger="tool"))
