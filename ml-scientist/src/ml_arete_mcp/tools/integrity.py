"""Integrity tool — check_invariants.

Audits improver.db against the recursive loop's invariants, including
the one honest cross-server check: minted claim_ids resolving through
the claims channel. Every invocation is logged to <db_dir>/logs/.
Report-only.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

import json

from ..integrity.checks import run_and_log
from ..enforcement.recurrence import canonical_ref, violation_ack
from .schemas import ok, fail, AcknowledgeViolationOut, CheckInvariantsOut, ReadResourceOut
from mcp.types import CallToolResult


def _annotate_acks(store, payload: dict) -> None:
    """Mark already-acknowledged violation rows in the tool payload —
    post-log only, so the logged run's canonical refs stay stable."""
    acked = store.violation_ack_keys()
    for c in payload.get("checks", []):
        for v in c.get("violations", []):
            if (
                isinstance(v, dict)
                and (c.get("name"), canonical_ref(v)) in acked
            ):
                v["acknowledged"] = True


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
        _annotate_acks(store, payload)
        return ok(payload)

    @mcp.tool()
    def acknowledge_violation(
        check_name: Annotated[str, Field(description="Name of the integrity check that flagged the violation (as shown in check_invariants or the status digest blockers).")],
        object_ref: Annotated[str, Field(description="The flagged record's reference, exactly as reported by the check.")],
        disposition: Annotated[str, Field(description="What was done about it — 'remediated via …' or 'accepted: <reason>'.")],
        decided_by: Annotated[str, Field(description="Who acknowledges — 'agent:<name>' or 'human:<name>'. Attribution is required.")],
    ) -> Annotated[CallToolResult, AcknowledgeViolationOut]:
        """Acknowledge an open integrity violation — insert-only.

        Records the disposition against (check_name, object_ref) so
        the finding stops gating writes and leaves the digest's
        blockers; the underlying record is never touched."""
        return ok(violation_ack(
            store, check_name, object_ref, disposition, decided_by,
        ))

    @mcp.tool()
    async def read_resource(
        uri: Annotated[str, Field(description="Resource URI to read — this server's own scheme only (improver://...).")],
    ) -> Annotated[CallToolResult, ReadResourceOut]:
        """Read one of this server's resources by URI (read-only).

        The MCP surface exposes resources only through
        resources/read — this tool gives tool-only clients the same
        read surface. Ownership is by construction: a URI outside
        this server's registered scheme resolves to nothing and
        errors rather than crossing the loop boundary. Never a write
        path.
        """
        try:
            contents = await mcp.read_resource(uri)
            items = []
            for c in contents:
                content = c.content
                if isinstance(content, bytes):
                    content = content.decode("utf-8", errors="replace")
                items.append({
                    "mime_type": c.mime_type,
                    "content": content,
                })
            return ok({"uri": uri, "contents": items})
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))
