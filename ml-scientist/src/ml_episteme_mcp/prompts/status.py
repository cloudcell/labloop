"""status_report prompt — the LLM-facing render of protocol://status.

Renders the live status digest into marching orders: where the loop
stands, what is open, what is blocked, and the recommended next
action. Reads the same ``status_digest`` the resource serves — the
prompt is a render, never a second source of truth.
"""

from __future__ import annotations

from ..resources.status import status_digest
from ..state.store import StateStore


def _render(digest: dict) -> str:
    lines = [
        f"# Status report — {digest['server']}",
        "",
        f"Role: {digest['role']} · generated {digest['generated_at']}",
        f"Workflow position: {digest['workflow_position']}",
        "",
    ]

    open_work = digest.get("open_work", [])
    if open_work:
        lines.append("## Open work")
        for item in open_work:
            ref = item["id"]
            extra = f" ({item['flag']})" if item.get("flag") else ""
            stale = f" — STALE, idle {item['idle_hours']}h" if item.get("stale") else ""
            lines.append(
                f"- {item['kind']} `{ref}` — {item['state']}{extra}{stale}"
            )
        lines.append("")
    else:
        lines += ["## Open work", "- none", ""]

    blockers = digest.get("blockers", [])
    if blockers:
        lines.append("## Blockers")
        for b in blockers:
            tools = ", ".join(b.get("blocks") or ["?"])
            lines.append(
                f"- {b['channel']} channel is DOWN — blocks: {tools} "
                f"({b.get('detail', '')})"
            )
        lines.append("")

    recs = digest.get("recommended_next", [])
    lines.append("## Recommended next")
    if recs:
        for r in recs:
            tool = r.get("tool") or r["action"]
            flag = " [BLOCKED]" if r.get("blocked") else ""
            refs = (
                " on " + ", ".join(f"`{x}`" for x in r["entity_refs"])
                if r.get("entity_refs") else ""
            )
            lines.append(
                f"{r['rank']}. `{tool}`{refs} — {r['reason']}{flag}"
            )
    else:
        lines.append("- nothing pending")
    lines.append("")

    up = digest.get("upstream_summary", {})
    integ = digest.get("integrity_summary", {})
    lines += [
        "## Context",
        f"- upstreams: {up.get('up', 0)}/{up.get('configured', 0)} up"
        f" ({up.get('verdict', '?')})",
        f"- integrity: {integ.get('verdict', '?')}"
        + (
            f" — {integ['violations']} violation(s)"
            if integ.get("violations")
            else ""
        )
        + f" (last run {integ.get('last_run_at')})",
        "",
        "Read `protocol://session` for the full loop protocol and "
        "tool catalog. Read `protocol://status` for this digest as "
        "JSON.",
    ]
    return "\n".join(lines)


def register(mcp, store: StateStore, adaptor=None) -> None:
    """Register the status_report prompt."""

    @mcp.prompt()
    def status_report() -> str:
        """Where the loop stands and what to do next — read at
        session start and after any context compaction."""
        return _render(status_digest(store, adaptor))
