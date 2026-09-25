"""Topology view — the lab's channel map and per-server posture.

Read-only: renders the channel registry plus the last aggregate
fetch's per-server status. Never writes, never mutates.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.routing import Route

from ...clients.adaptors import CHANNEL_ORDER, CHANNEL_STATUS_URIS
from ..templates import escape, render_base

ROUTE = "/topology"
NAV_TITLE = "Topology"


def render_topology(adaptors) -> str:
    """The channel map — roles, targets, live states."""
    report = {
        e["channel"]: e for e in adaptors.connectivity_report()
    }
    rows = []
    for name in CHANNEL_ORDER:
        spec = adaptors._channels.get(name)
        uri = CHANNEL_STATUS_URIS[name]
        if spec is None:
            rows.append(
                f"<tr><td><code>{escape(name)}</code></td>"
                f"<td><code>{escape(uri)}</code></td>"
                "<td class='muted'>—</td>"
                "<td><span class='status status-superseded'>"
                "not configured</span></td>"
                "<td class='muted'>no [adaptors."
                f"{escape(name)}] wired</td></tr>"
            )
            continue
        live = report.get(name, {})
        state = live.get("state", "down")
        badge = (
            '<span class="status status-live">up</span>'
            if state == "up"
            else '<span class="status status-expired">down</span>'
        )
        detail = live.get("last_error") or (
            f"connected {live.get('connected_at')}"
            if live.get("connected_at") else "—"
        )
        rows.append(
            f"<tr><td><code>{escape(name)}</code></td>"
            f"<td><code>{escape(uri)}</code></td>"
            f"<td class='mono'>{escape(spec.target)}</td>"
            f"<td>{badge}</td>"
            f"<td class='muted'>{escape(str(detail))}</td></tr>"
        )

    body = f"""
    <h1>Lab topology</h1>
    <p class="muted">Agora is the lab's status hub — a role-filler,
    not a loop. Its channels are read-only (<code>resources/read</code>
    only): it aggregates every server's status digest and can never
    write into a loop.</p>
    <table>
        <thead><tr><th>Channel</th><th>Status URI</th><th>Target</th>
        <th>State</th><th>Detail</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
    </table>
    <p class="muted">Fetch order is dependency order — claims →
    loop0 → loop1 → loop2. A down channel stays registered and the
    connectivity supervisor keeps retrying.</p>"""
    return render_base("Topology", body, active=ROUTE)


def routes(ctx) -> list[Route]:
    """Plugin contract — the topology page."""

    def index(request: Request) -> HTMLResponse:
        return HTMLResponse(render_topology(ctx.adaptors))

    return [Route(ROUTE, index)]
