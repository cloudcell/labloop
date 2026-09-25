"""Integrity tools — check_invariants, describe_blob.

The agent-facing surface of the integrity layer: audits state.db
against the loop's invariants and returns structured violations.
Every check_invariants invocation is logged to <db_dir>/logs/ — the
check trail is itself evidence. Report-only: the tools never mutate.
"""

from __future__ import annotations

from typing import Annotated
from pydantic import Field

import json

from ..integrity.checks import run_and_log
from .schemas import ok, CheckInvariantsOut, DescribeBlobOut
from mcp.types import CallToolResult


def register(mcp, store, adaptor, integrity_config: dict | None = None) -> None:
    """Register the integrity tool.

    integrity_config: the [integrity] table from ml-episteme.toml —
    stalled_trial_seconds, log_max_files.
    """

    @mcp.tool()
    def check_invariants() -> Annotated[CallToolResult, CheckInvariantsOut]:
        """Audit the experiment store against the loop's invariants.

        Returns {status: ok|violations, checks: [{name, ok,
        violations, detail}]}. Detection complement to write-time
        enforcement: orphaned running trials, completed trials lacking
        observations, unsealed executions, strace divergence, budget
        overruns, stuck hypotheses, stalled trials. Report-only —
        nothing is repaired or mutated. The run is logged to
        <db_dir>/logs/ (retention: [integrity] log_max_files).
        """
        executor = getattr(adaptor, "_executor", None)
        connectivity = (
            adaptor.connectivity_report()
            if hasattr(adaptor, "connectivity_report") else None
        )
        return ok(run_and_log(
                store, executor=executor, connectivity=connectivity,
                config=integrity_config, trigger="tool",
            ))

    @mcp.tool()
    def describe_blob(
        content_hash: Annotated[str, Field(description="The sha256:<64 hex> digest to look up.")],
    ) -> Annotated[CallToolResult, DescribeBlobOut]:
        """Describe a content-addressed blob by digest (read-only).

        The resolution check behind digest claims: returns {exists,
        resolved_in, size_bytes, content_type, captured_at} across the
        content stores — artifact_files (HTTP ingest), code_snippets,
        bundles.code_hash. resolved_in names every store holding the
        hash; a digest may live in more than one. Existence +
        metadata only — bytes are never returned. This is the read
        upstream servers use to verify a digest before accepting it
        on a registration call.
        """
        return ok(store.describe_blob(content_hash))
