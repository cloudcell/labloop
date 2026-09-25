"""Investigation views for the zetesis observability GUI — read-only."""

from __future__ import annotations

import json

from starlette.responses import HTMLResponse

from ...state.models import Investigation
from ...state.store import SearchStore
from ..templates import (
    escape,
    format_timestamp,
    render_base,
    render_error,
)


def _confidence(conf: float) -> str:
    pct = int(conf * 100)
    return (
        f'<span class="confidence-bar"><span class="confidence-fill" '
        f'style="width: {pct}%"></span></span>{conf:.2f}'
    )


def _inv_row(inv: Investigation, store: SearchStore) -> str:
    findings = store.list_findings(inv.id)
    refs = store.list_evidence_refs(inv.id)
    minted = sum(1 for f in findings if f.claim_id)
    question = inv.question
    short = question if len(question) <= 110 else question[:107] + "…"
    verdict = inv.verdict.value if inv.verdict else "—"
    return (
        "<tr>"
        f'<td><a href="/investigation/{escape(inv.id)}">{escape(short)}</a></td>'
        f'<td><span class="mono muted">{escape(inv.id)}</span></td>'
        f'<td><span class="status status-{escape(inv.status.value)}">'
        f"{escape(inv.status.value)}</span></td>"
        f"<td>{escape(verdict)}</td>"
        f"<td>{len(refs)}</td><td>{len(findings)}</td><td>{minted}</td>"
        f"<td>{format_timestamp(inv.created_at)}</td>"
        "</tr>"
    )


def _inv_table(invs: list[Investigation], store: SearchStore) -> str:
    if not invs:
        return (
            '<div class="empty-state"><p>No investigations yet.</p>'
            "<p>They arrive via <code>open_investigation</code>.</p></div>"
        )
    rows = "".join(_inv_row(i, store) for i in invs)
    return (
        "<table><thead><tr>"
        "<th>Question</th><th>ID</th><th>Status</th><th>Verdict</th>"
        "<th>Pulls</th><th>Findings</th><th>Claims</th><th>Opened</th>"
        "</tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def render_investigation_list(
    store: SearchStore, view: str = "all"
) -> HTMLResponse:
    """Landing page: stats, status tabs, investigations table."""
    stats = store.stats()

    status = None if view == "all" else view
    invs, _ = store.list_investigations(status=status, limit=200)
    by = stats["by_status"]

    def tab(label: str, v: str) -> str:
        active = " active" if v == view else ""
        return f'<a class="filter-tab{active}" href="/?view={v}">{label}</a>'

    view_tabs = "".join(
        tab(label, v)
        for label, v in [
            (f"Open ({by.get('open', 0)})", "open"),
            (f"Concluded ({by.get('concluded', 0)})", "concluded"),
            (f"Abandoned ({by.get('abandoned', 0)})", "abandoned"),
            (f"All ({stats['total']})", "all"),
        ]
    )

    body = f"""
    <h1>Zetesis — the search loop</h1>
    <div class="stat-grid">
        <span class="stat"><b>{stats['total']}</b> investigations</span>
        <span class="stat"><b>{by.get('open', 0)}</b> open</span>
        <span class="stat"><b>{stats['evidence_refs']}</b> evidence pulls</span>
        <span class="stat"><b>{stats['findings']}</b> findings</span>
        <span class="stat"><b>{stats['minted_claims']}</b> claims minted</span>
    </div>
    <div class="filter-tabs">{view_tabs}</div>
    {_inv_table(invs, store)}
    """
    return HTMLResponse(render_base("Investigations", body))


def render_investigation_detail(
    store: SearchStore, investigation_id: str
) -> HTMLResponse:
    """Investigation detail: record + evidence trail + findings."""
    inv = store.get_investigation(investigation_id)
    if inv is None:
        return HTMLResponse(
            render_error(f"Investigation not found: {investigation_id}", 404),
            status_code=404,
        )

    refs = store.list_evidence_refs(inv.id)
    findings = store.list_findings(inv.id)

    ref_rows = "".join(
        "<tr>"
        f'<td><span class="mono">{escape(r.id)}</span></td>'
        f"<td>{escape(r.source.value)}</td>"
        f'<td><span class="mono">{escape(r.tool)}</span></td>'
        f"<td class=\"muted\">{escape(json.dumps(r.args))[:120]}</td>"
        f'<td class="mono">{escape(", ".join(r.ref_ids) or "—")}</td>'
        f"<td>{format_timestamp(r.created_at)}</td>"
        "</tr>"
        for r in refs
    ) or (
        '<tr><td colspan="6" class="muted">No evidence pulled yet — '
        "an investigation without pulls is speculation.</td></tr>"
    )

    finding_cards = ""
    for f in findings:
        grounding = store.finding_evidence_refs(f.id)
        grounding_html = "".join(
            f'<span class="badge mono">{escape(r.id)} → {escape(r.source.value)}/'
            f"{escape(r.tool)}</span>"
            for r in grounding
        ) or '<span class="muted">no evidence refs — prior-level</span>'
        claim_line = (
            f'<p class="muted">minted → <span class="mono">'
            f"{escape(f.claim_id)}</span></p>"
            if f.claim_id else ""
        )
        finding_cards += f"""
        <div class="card">
            <p style="font-size: 1.0rem; margin-bottom: 0.5rem">{escape(f.content)}</p>
            <p>
                <span class="status status-{escape(f.status.value)}">{escape(f.status.value)}</span>
                &nbsp;confidence {_confidence(f.confidence)}
            </p>
            <p style="margin-top: 0.4rem">{grounding_html}</p>
            {claim_line}
        </div>
        """

    if not finding_cards:
        finding_cards = (
            '<div class="empty-state"><p>No findings recorded.</p></div>'
        )

    implications_block = ""
    if inv.implications:
        implications_block = f"""
        <div class="card">
            <h2>Implications — the Loop-0 handoff</h2>
            <pre>{escape(json.dumps(inv.implications, indent=2))}</pre>
        </div>
        """

    verdict_line = ""
    if inv.verdict:
        verdict_line = (
            f'<p>verdict: <b>{escape(inv.verdict.value)}</b>'
            f" &nbsp;·&nbsp; concluded "
            f"{format_timestamp(inv.concluded_at)}</p>"
        )

    body = f"""
    <h1><span class="mono">{escape(inv.id)}</span></h1>
    <div class="card">
        <p style="font-size: 1.05rem; margin-bottom: 0.75rem">{escape(inv.question)}</p>
        <p>
            <span class="status status-{escape(inv.status.value)}">{escape(inv.status.value)}</span>
        </p>
        {verdict_line}
        <p class="muted" style="margin-top: 0.5rem">
            opened {format_timestamp(inv.created_at)}
        </p>
        <p class="muted" style="margin-top: 0.5rem">scope:</p>
        <pre>{escape(json.dumps(inv.scope, indent=2))}</pre>
        {(f'<p class="muted">summary:</p><p>{escape(inv.summary)}</p>')
         if inv.summary else ""}
    </div>
    <div class="card">
        <h2>Evidence trail ({len(refs)})</h2>
        <table><thead><tr><th>Ref</th><th>Source</th><th>Tool</th>
        <th>Args</th><th>Entities returned</th><th>When</th></tr></thead>
        <tbody>{ref_rows}</tbody></table>
    </div>
    <h2>Findings ({len(findings)})</h2>
    {finding_cards}
    {implications_block}
    """
    return HTMLResponse(render_base(f"Investigation {investigation_id}", body))
