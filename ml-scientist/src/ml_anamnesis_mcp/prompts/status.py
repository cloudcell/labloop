"""status_report prompt — the LLM-facing render of claims://status.

Renders the capability status digest. Anamnesis is a role-filler —
the report says so honestly rather than fabricating a workflow
position. Reads the same ``status_digest`` the resource serves.
"""

from __future__ import annotations

from ..resources.status import status_digest
from ..state.store import MemoryStore


def _render(digest: dict) -> str:
    claims = digest.get("claims", {})
    integ = digest.get("integrity_summary", {})
    up = digest.get("upstream_summary", {})
    lines = [
        f"# Status report — {digest['server']}",
        "",
        f"Role: {digest['role']} · generated {digest['generated_at']}",
        "Workflow position: none — this is a capability server, not "
        "a loop",
        "",
        "## Store",
        f"- claims: {claims.get('total', 0)} total"
        f" ({claims.get('superseded', 0)} superseded,"
        f" {claims.get('expired', 0)} expired)",
        "",
        "## Next action",
        "- determined by the consuming loops — this server serves "
        "assert_claim / relate / list_claims / get_claim on demand",
        "",
        "## Context",
        f"- upstreams: {up.get('verdict', 'skipped')} —"
        f" {up.get('detail', 'no upstream channels')}",
        f"- integrity: {integ.get('verdict', '?')}"
        + (
            f" — {integ['violations']} violation(s)"
            if integ.get("violations")
            else ""
        )
        + f" (last run {integ.get('last_run_at')})",
        "",
        "Read `claims://session` for the capability surface and "
        "boundary rules. Read `claims://status` for this digest as "
        "JSON.",
    ]
    return "\n".join(lines)


def register(mcp, store: MemoryStore) -> None:
    """Register the status_report prompt."""

    @mcp.prompt()
    def status_report() -> str:
        """Capability status — claim counts, integrity. This server
        has no workflow; the consuming loops determine next actions."""
        return _render(status_digest(store))
