"""status_report prompt — the LLM-facing render of lab://status.

Renders the lab aggregate into marching orders: per-server posture,
what is open across the lab, and the ranked next-action list. This
is the session-start surface for a driving client — one call
replaces the read-all-three manual walk. Per-server ``status_report``
remains for drill-down.
"""

from __future__ import annotations

from ..resources.status import lab_status


def _render(aggregate: dict) -> str:
    lines = [
        "# Lab status report — ml-agora-mcp",
        "",
        f"Generated {aggregate['generated_at']} · "
        f"{aggregate['servers_reachable']}/"
        f"{aggregate['servers_configured']} configured servers "
        "reachable",
        "",
        "## Servers",
    ]
    for name, srv in aggregate.get("servers", {}).items():
        st = srv["status"]
        if st == "ok":
            d = srv["digest"]
            pos = d.get("workflow_position") or "capability server"
            n_open = len(d.get("open_work", []))
            n_block = len(d.get("blockers", []))
            lines.append(
                f"- **{name}** ({d.get('server')}) — {pos}; "
                f"{n_open} open item(s), {n_block} blocker(s)"
            )
        elif st == "not_configured":
            lines.append(
                f"- **{name}** — NOT CONFIGURED "
                f"({srv.get('detail', '')})"
            )
        else:
            lines.append(
                f"- **{name}** — {st.upper()} "
                f"({srv.get('detail', '')})"
            )
    lines.append("")

    actions = aggregate.get("lab_next_actions", [])
    lines.append("## Lab next actions")
    if actions:
        for a in actions:
            tool = a.get("tool") or a["action"]
            flag = " [BLOCKED]" if a.get("blocked") else ""
            refs = (
                " on " + ", ".join(
                    f"`{x}`" for x in a.get("entity_refs", [])
                )
                if a.get("entity_refs") else ""
            )
            lines.append(
                f"{a['rank']}. **{a['server']}** `{tool}`{refs} — "
                f"{a['reason']}{flag}"
            )
    else:
        lines.append("- nothing pending across the lab")
    lines.append("")

    lines += [
        "## How to proceed",
        "- Work the list top-down; a `restore_channel` action means "
        "fix connectivity before that server's work can continue.",
        "- For a server's detail, call its own `status_report` "
        "prompt or read its `X://status` resource.",
        "- Read `lab://topology` for the channel map; "
        "`lab://status` for this aggregate as JSON.",
        "- Ranking is advisory — you decide (the agent is a "
        "scientist, not a scribe).",
    ]
    return "\n".join(lines)


def register(mcp, adaptors) -> None:
    """Register the status_report prompt."""

    @mcp.prompt()
    async def status_report() -> str:
        """Lab status — what's open across all servers and the
        ranked next-action list. Read at session start and after
        any context compaction."""
        return _render(await lab_status(adaptors))
