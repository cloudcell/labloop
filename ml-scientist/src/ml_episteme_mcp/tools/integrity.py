"""Integrity tools — check_invariants, describe_blob, get_blob.

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
from ..enforcement.recurrence import violation_ack
from .schemas import ok, fail, AcknowledgeViolationOut, CheckInvariantsOut, DescribeBlobOut, GetBlobOut
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
        observations, unsealed executions, strace divergence,
        undigested input data, budget overruns, stuck hypotheses,
        stalled trials. Report-only —
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
    def acknowledge_violation(
        check_name: Annotated[str, Field(description="Name of the integrity check that flagged the violation (as shown in check_invariants or the status digest blockers).")],
        object_ref: Annotated[str, Field(description="The flagged record's reference (e.g. trial-…), exactly as reported by the check.")],
        disposition: Annotated[str, Field(description="What was done about it — e.g. 'remediated via correct_trial_status', 'accepted: trial ran before sealing was enforced'.")],
        decided_by: Annotated[str, Field(description="Who acknowledges — 'agent:<name>' or 'human:<name>'. Attribution is required.")],
    ) -> Annotated[CallToolResult, AcknowledgeViolationOut]:
        """Acknowledge an open integrity violation — insert-only.

        The check log is append-only; this records the disposition
        (remediated | accepted-with-reason) against (check_name,
        object_ref) so the finding stops gating writes and leaves the
        digest's blockers. The underlying record is never touched —
        remediation itself is done by the corrective tools first.
        """
        return ok(violation_ack(
            store, check_name, object_ref, disposition, decided_by,
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
        on a registration call. For the bytes themselves, get_blob.
        """
        return ok(store.describe_blob(content_hash))

    @mcp.tool()
    def get_blob(
        content_hash: Annotated[str, Field(description="The sha256:<64 hex> digest to retrieve.")],
        max_bytes: Annotated[int, Field(description="Maximum returned payload size in bytes (pre-base64). Blobs larger than this return too_large with metadata but no bytes.")] = 4 * 1024 * 1024,
    ) -> Annotated[CallToolResult, GetBlobOut]:
        """Retrieve a content-addressed blob's bytes by digest (read-only).

        The read-back half of describe_blob: returns the stored bytes
        (base64 in content_b64) plus {digest, size_bytes, content_type,
        captured_at, resolved_in, served_from}. The returned bytes are
        re-hashed and verified against the requested digest — a blob
        whose stored bytes don't match its key is refused with the
        computed digest reported, never served.

        Errors: malformed_digest | not_found | no_bytes |
        digest_mismatch | too_large. Read-only: no state is mutated.
        """
        import base64 as _b64

        result = store.read_blob(content_hash)
        if not result["ok"]:
            return fail(json.dumps({
                "error": result["error"],
                **{k: v for k, v in result.items()
                   if k not in ("ok", "error")},
            }))
        if len(result["content"]) > max_bytes:
            return fail(json.dumps({
                "error": "too_large",
                "digest": content_hash,
                "size_bytes": len(result["content"]),
                "resolved_in": result["resolved_in"],
                "detail": f"blob is {len(result['content'])} bytes "
                          f"(> max_bytes={max_bytes}) — raise max_bytes "
                          "or fetch via the GUI export path",
            }))
        return ok({
            "digest": content_hash,
            "size_bytes": result["size_bytes"],
            "content_type": result.get("content_type"),
            "captured_at": result.get("captured_at"),
            "resolved_in": result["resolved_in"],
            "served_from": result["served_from"],
            "content_b64": _b64.b64encode(result["content"]).decode("ascii"),
        })
