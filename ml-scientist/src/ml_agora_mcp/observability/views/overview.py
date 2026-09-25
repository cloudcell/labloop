"""Bird's-eye overview — the lab at a glance.

Renders what agora already aggregates for ``lab://status``: one card
per server with its status digest (workflow position, open work,
integrity verdict, top recommendation) plus a lab-level card. A
down/unreachable channel renders a degraded card with the last error
— absent means absent, never a blank panel.

Read-only: every datum comes from ``resources/read`` on the owning
servers. Nothing here mutates anything.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.routing import Route

import re

from ...clients.adaptors import CHANNEL_ORDER
from ...resources.status import lab_status
from ..links import (
    ENTITY_PREFIXES, SERVER_CHANNELS, entity_url, gui_base,
)
from ..templates import escape, render_base

ROUTE = "/"
NAV_TITLE = "Overview"

_SERVER_LABEL = {
    "claims": "Claims — ml-anamnesis",
    "loop0": "Loop 0 — ml-episteme",
    "loop1": "Loop 1 — ml-zetesis",
    "loop2": "Loop 2 — ml-arete",
}

# Matches lab entity ids inside already-escaped text (id chars are
# escape-safe).
_ID_RE = re.compile(
    r"\b(" + "|".join(re.escape(p) for p in ENTITY_PREFIXES)
    + r")[a-z0-9]+"
)


def _link_id(entity_id: str, gui_bases: dict,
             context: dict | None = None) -> str:
    """An entity id as a link to its owning GUI detail page."""
    url = entity_url(entity_id, gui_bases, context)
    code = f"<code>{escape(entity_id)}</code>"
    return f'<a href="{escape(url)}">{code}</a>' if url else code


def _linkify(text: str, gui_bases: dict,
             context: dict | None = None) -> str:
    """Escape text, then link every recognizable entity id in it."""
    return _ID_RE.sub(
        lambda m: _link_id(m.group(0), gui_bases, context), escape(text)
    )


def _integrity_pill(digest: dict) -> str:
    integ = digest.get("integrity") or {}
    verdict = integ.get("verdict")
    if verdict is None:
        return '<span class="muted">integrity —</span>'
    cls = "status-live" if verdict == "ok" else "status-expired"
    return f'<span class="status {cls}">{escape(verdict)}</span>'


def _server_card(name: str, srv: dict, target: str | None,
                 gui_bases: dict) -> str:
    label = _SERVER_LABEL.get(name, name)
    gui = gui_base(target)
    link = f' <a href="{escape(gui)}">gui ↗</a>' if gui else ""

    if srv.get("status") != "ok":
        return f"""<div class="card">
            <h2>{escape(label)}{link}</h2>
            <p><span class="status status-expired">
                {escape(srv.get('status', 'down'))}</span></p>
            <p class="muted">{escape(srv.get('detail', ''))}</p>
        </div>"""

    d = srv["digest"]
    open_work = d.get("open_work") or []
    blockers = d.get("blockers") or []
    recs = d.get("recommended_next") or []
    position = d.get("workflow_position") or "—"

    rows = []
    if blockers:
        rows.append(
            "<p>" + " ".join(
                f'<span class="status status-expired">blocked: '
                f'{escape(b.get("channel", "?"))}</span>'
                for b in blockers
            ) + "</p>"
        )
    rows.append(
        f'<div class="stat-grid">'
        f'<div class="stat"><b>{len(open_work)}</b> open</div>'
        f'<div class="stat"><b>{escape(str(position))}</b></div>'
        f'<div class="stat">{_integrity_pill(d)}</div>'
        f'</div>'
    )
    if open_work:
        items = "".join(
            f"<li>{_linkify(w.get('id', w.get('kind', '?')), gui_bases, w)}"
            f" <span class='muted'>{escape(w.get('kind', ''))}</span></li>"
            for w in open_work[:5]
        )
        rows.append(f"<ul>{items}</ul>")
    if recs:
        top = recs[0]
        rows.append(
            f'<p class="muted">next: <code>'
            f'{escape(top.get("tool") or top.get("action", "?"))}</code>'
            f' — {_linkify(top.get("reason", "")[:120], gui_bases)}</p>'
        )
    return f'<div class="card"><h2>{escape(label)}{link}</h2>' \
        + "".join(rows) + "</div>"


async def _render(adaptors) -> str:
    report = await lab_status(adaptors)
    servers = report["servers"]
    targets = {
        n: (adaptors._channels.get(n).target
            if adaptors._channels.get(n) else None)
        for n in CHANNEL_ORDER
    }
    gui_bases = {n: gui_base(t) for n, t in targets.items()}

    cards = "".join(
        _server_card(n, servers.get(n, {}), targets.get(n), gui_bases)
        for n in CHANNEL_ORDER
    )
    actions = report.get("lab_next_actions") or []
    def _server_cell(a: dict) -> str:
        srv = a.get("server", "?")
        base = gui_bases.get(srv) or gui_bases.get(
            SERVER_CHANNELS.get(srv, ""))
        code = f"<code>{escape(srv)}</code>"
        return f'<a href="{escape(base)}">{code}</a>' if base else code

    action_rows = "".join(
        f"<tr><td class='mono'>{a['rank']}</td>"
        f"<td>{_server_cell(a)}</td>"
        f"<td><code>{escape(a.get('tool') or a.get('action', '?'))}</code></td>"
        f"<td class='muted'>{_linkify(a.get('reason', '')[:100], gui_bases)}</td></tr>"
        for a in actions[:10]
    )
    actions_html = (
        f"<table><thead><tr><th>#</th><th>Server</th>"
        f"<th>Action</th><th>Why</th></tr></thead>"
        f"<tbody>{action_rows}</tbody></table>"
        if action_rows
        else '<p class="muted">No recommended actions — the lab is idle.</p>'
    )

    body = f"""
    <h1>Lab overview</h1>
    <p class="muted">{report['servers_reachable']} of
    {report['servers_configured']} servers reachable —
    generated {escape(report['generated_at'])}.</p>
    {cards}
    <h2>Lab next actions</h2>
    {actions_html}"""
    return render_base("Overview", body, active=ROUTE)


def routes(ctx) -> list[Route]:
    """Plugin contract — the bird's-eye page."""

    async def index(request: Request) -> HTMLResponse:
        return HTMLResponse(await _render(ctx.adaptors))

    return [Route(ROUTE, index)]
