"""Roster + campaign views for the zetesis observability GUI —
read-only projections of the Loop-1 promotion state."""

from __future__ import annotations

import json

from starlette.responses import HTMLResponse

from ...state.models import PromotionCampaign, RosterEntry
from ...state.store import SearchStore
from ..links import link_id, link_ids
from ..templates import (
    escape,
    format_timestamp,
    render_base,
    render_error,
)


def _cand_link(candidate_id: str | None) -> str:
    """A candidate id linked to its /candidate page; '—' when absent."""
    if not candidate_id:
        return "—"
    return (
        f'<a href="/candidate/{escape(candidate_id)}">'
        f'<span class="mono">{escape(candidate_id)}</span></a>'
    )


def _roster_row(e: RosterEntry) -> str:
    notes = json.dumps(e.notes) if e.notes else ""
    return (
        f'<tr id="{escape(e.id)}">'
        f"<td>{_cand_link(e.id)}</td>"
        f'<td><span class="mono muted">{_cand_link(e.parent_id)}'
        "</span></td>"
        f'<td><span class="status status-{escape(e.derived_status.value)}">'
        f"{escape(e.derived_status.value)}</span></td>"
        f'<td class="muted">{escape(notes[:100])}</td>'
        f"<td>{format_timestamp(e.annotated_at)}</td>"
        "</tr>"
    )


def _campaign_row(c: PromotionCampaign) -> str:
    score = f"{c.promotion_score:.3f}" if c.promotion_score is not None else "—"
    return (
        "<tr>"
        f'<td><a href="/campaign/{escape(c.id)}"><span class="mono">'
        f"{escape(c.id)}</span></a></td>"
        f"<td>{_cand_link(c.champion_id)}</td>"
        f"<td>{_cand_link(c.challenger_id)}</td>"
        f'<td><span class="mono muted">{escape(c.primary_metric)}</span></td>'
        f'<td><span class="status status-{escape(c.status.value)}">'
        f"{escape(c.status.value)}</span></td>"
        f"<td>{score}</td>"
        f"<td>{format_timestamp(c.created_at)}</td>"
        "</tr>"
    )


def render_promotion_page(store: SearchStore) -> HTMLResponse:
    """The promotion page: roster table + campaigns table."""
    stats = store.stats()
    entries, _ = store.list_roster_entries(limit=200)
    campaigns, _ = store.list_campaigns(limit=200)

    roster_rows = "".join(_roster_row(e) for e in entries) or (
        '<tr><td colspan="5" class="muted">Roster is empty — '
        "refresh_roster adopts upstream candidates.</td></tr>"
    )
    campaign_rows = "".join(_campaign_row(c) for c in campaigns) or (
        '<tr><td colspan="7" class="muted">No campaigns yet — '
        "open_campaign starts a paired evaluation.</td></tr>"
    )

    body = f"""
    <h1>Promotion — the Loop-1 pipeline</h1>
    <div class="stat-grid">
        <span class="stat"><b>{stats['roster']}</b> roster entries</span>
        <span class="stat"><b>{stats['campaigns']}</b> campaigns</span>
        <span class="stat"><b>{stats['open_campaigns']}</b> open</span>
    </div>
    <div class="card">
        <h2>Roster ({len(entries)})</h2>
        <table><thead><tr><th>Candidate</th><th>Parent</th>
        <th>Status</th><th>Notes</th><th>Annotated</th></tr></thead>
        <tbody>{roster_rows}</tbody></table>
    </div>
    <div class="card">
        <h2>Campaigns ({len(campaigns)})</h2>
        <table><thead><tr><th>Campaign</th><th>Champion</th>
        <th>Challenger</th><th>Metric</th><th>Status</th>
        <th>Score</th><th>Opened</th></tr></thead>
        <tbody>{campaign_rows}</tbody></table>
    </div>
    """
    return HTMLResponse(render_base("Promotion", body))


def render_campaign_detail(
    store: SearchStore, campaign_id: str,
    gui_bases: dict | None = None,
) -> HTMLResponse:
    """Campaign detail: record + per-arm results + evidence trail."""
    c = store.get_campaign(campaign_id)
    if c is None:
        return HTMLResponse(
            render_error(f"Campaign not found: {campaign_id}", 404),
            status_code=404,
        )

    results = store.list_campaign_results(campaign_id)
    refs = store.list_evidence_refs(campaign_id=campaign_id)
    spawns = store.list_campaign_spawns(campaign_id)
    bases = gui_bases or {}

    result_rows = "".join(
        "<tr>"
        f'<td><span class="mono">{escape(r.id)}</span></td>'
        f'<td><span class="status status-{escape(r.arm.value)}">'
        f"{escape(r.arm.value)}</span></td>"
        f"<td>{link_id(r.programme_id, bases)}</td>"
        f'<td class="muted">{escape(json.dumps(r.metrics))[:140]}</td>'
        f"<td>{format_timestamp(r.created_at)}</td>"
        "</tr>"
        for r in results
    ) or (
        '<tr><td colspan="5" class="muted">No results recorded — '
        "an unrun arm cannot be promoted.</td></tr>"
    )

    spawn_rows = "".join(
        "<tr>"
        f'<td><span class="mono">{escape(s.id)}</span></td>'
        f'<td><span class="status status-{escape(s.arm.value)}">'
        f"{escape(s.arm.value)}</span></td>"
        f"<td>{link_id(s.programme_id, bases)}</td>"
        f'<td class="muted">{escape(json.dumps(s.budget))[:100]}</td>'
        f'<td><span class="status status-{escape(s.status.value)}">'
        f"{escape(s.status.value)}</span></td>"
        f"<td>{format_timestamp(s.created_at)}</td>"
        "</tr>"
        for s in spawns
    ) or (
        '<tr><td colspan="6" class="muted">No spawns — '
        "client-driven campaign, or orchestration never ran.</td></tr>"
    )

    ref_rows = "".join(
        "<tr>"
        f'<td><a class="mono" href="/evidence-ref/{escape(r.id)}">'
        f"{escape(r.id)}</a></td>"
        f"<td>{escape(r.source.value)}</td>"
        f'<td><span class="mono">{escape(r.tool)}</span></td>'
        f'<td class="mono">{link_ids(r.ref_ids, bases)}</td>'
        f"<td>{format_timestamp(r.created_at)}</td>"
        "</tr>"
        for r in refs
    ) or (
        '<tr><td colspan="5" class="muted">No campaign evidence '
        "pulled yet.</td></tr>"
    )

    score = (
        f"<p>promotion_score: <b>{c.promotion_score:.4f}</b></p>"
        if c.promotion_score is not None else ""
    )
    decision = (
        f'<p>decision: <span class="mono">{escape(c.decision_id)}</span></p>'
        if c.decision_id else ""
    )
    claim = (
        f'<p class="muted">minted → <span class="mono">'
        f"{escape(c.claim_id)}</span></p>"
        if c.claim_id else ""
    )
    closed = (
        f'<p class="muted">closed {format_timestamp(c.closed_at)}</p>'
        if c.closed_at else ""
    )

    body = f"""
    <h1><span class="mono">{escape(c.id)}</span></h1>
    <div class="card">
        <p>
            <span class="status status-{escape(c.status.value)}">{escape(c.status.value)}</span>
            &nbsp;·&nbsp; contract <span class="mono">{escape(c.contract_id)}</span>
            &nbsp;·&nbsp; metric <b>{escape(c.primary_metric)}</b>
        </p>
        <p>
            champion {_cand_link(c.champion_id)}
            &nbsp;vs&nbsp;
            challenger {_cand_link(c.challenger_id)}
        </p>
        {score}{decision}{claim}
        <p class="muted" style="margin-top: 0.5rem">
            opened {format_timestamp(c.created_at)}
        </p>{closed}
        <p class="muted" style="margin-top: 0.5rem">budget:</p>
        <pre>{escape(json.dumps(c.budget, indent=2))}</pre>
        <p class="muted">seeds: {escape(json.dumps(c.seeds))}</p>
    </div>
    <div class="card">
        <h2>Results ({len(results)})</h2>
        <table><thead><tr><th>Result</th><th>Arm</th><th>Programme</th>
        <th>Metrics</th><th>When</th></tr></thead>
        <tbody>{result_rows}</tbody></table>
    </div>
    <div class="card">
        <h2>Spawns ({len(spawns)})</h2>
        <table><thead><tr><th>Spawn</th><th>Arm</th><th>Programme</th>
        <th>Budget</th><th>Status</th><th>When</th></tr></thead>
        <tbody>{spawn_rows}</tbody></table>
    </div>
    <div class="card">
        <h2>Evidence trail ({len(refs)})</h2>
        <table><thead><tr><th>Ref</th><th>Source</th><th>Tool</th>
        <th>Entities returned</th><th>When</th></tr></thead>
        <tbody>{ref_rows}</tbody></table>
    </div>
    """
    return HTMLResponse(render_base(f"Campaign {campaign_id}", body))


def render_candidate_detail(
    store: SearchStore, candidate_id: str
) -> HTMLResponse:
    """Candidate detail: roster entry, lineage, and every campaign
    the candidate ran in — as challenger or as the incumbent."""
    e = store.get_roster_entry(candidate_id)
    campaigns = store.list_campaigns_for_candidate(candidate_id)
    if e is None and not campaigns:
        return HTMLResponse(
            render_error(f"Candidate not found: {candidate_id}", 404),
            status_code=404,
        )

    overview_rows = ""
    if e is not None:
        children = store.list_roster_children(e.id)
        overview_rows = f"""
        <table>
            <tr><th>ID</th><td class="mono">{escape(e.id)}</td></tr>
            <tr><th>parent</th><td>{_cand_link(e.parent_id)}</td></tr>
            <tr><th>status</th><td>
                <span class="status status-{escape(e.derived_status.value)}">
                {escape(e.derived_status.value)}</span></td></tr>
            <tr><th>annotated</th>
                <td>{format_timestamp(e.annotated_at)}</td></tr>
        </table>
        {('<p class="muted">notes:</p><pre>'
          f'{escape(json.dumps(e.notes, indent=2))}</pre>')
         if e.notes else ""}
        """
    else:
        children = []
        overview_rows = (
            '<p class="muted">Not in the local roster — this candidate '
            "is known only through campaign records below.</p>"
        )

    child_rows = "".join(
        "<tr>"
        f"<td>{_cand_link(c.id)}</td>"
        f'<td><span class="status status-{escape(c.derived_status.value)}">'
        f"{escape(c.derived_status.value)}</span></td>"
        f"<td>{format_timestamp(c.annotated_at)}</td>"
        "</tr>"
        for c in children
    ) or (
        '<tr><td colspan="3" class="muted">No descendants '
        "in the roster.</td></tr>"
    )

    campaign_rows = "".join(
        "<tr>"
        f'<td><a href="/campaign/{escape(c.id)}"><span class="mono">'
        f"{escape(c.id)}</span></a></td>"
        f'<td><span class="status status-{escape(c.status.value)}">'
        f"{escape(c.status.value)}</span></td>"
        f"<td>{'challenger' if c.challenger_id == candidate_id else 'incumbent'}</td>"
        f'<td><span class="mono muted">{escape(c.primary_metric)}</span></td>'
        f"<td>{(f'{c.promotion_score:.3f}' if c.promotion_score is not None else '—')}</td>"
        f"<td>{format_timestamp(c.created_at)}</td>"
        "</tr>"
        for c in campaigns
    ) or (
        '<tr><td colspan="6" class="muted">No campaigns — '
        "registered but never evaluated.</td></tr>"
    )

    status_badge = (
        f'<span class="status status-{escape(e.derived_status.value)}">'
        f"{escape(e.derived_status.value)}</span>"
        if e is not None else ""
    )
    body = f"""
    <h1><span class="mono">{escape(candidate_id)}</span>
        {status_badge}</h1>
    <div class="card">
        <h2>Roster entry</h2>
        {overview_rows}
    </div>
    <div class="card">
        <h2>Descendants ({len(children)})</h2>
        <table><thead><tr><th>Candidate</th><th>Status</th>
        <th>Annotated</th></tr></thead>
        <tbody>{child_rows}</tbody></table>
    </div>
    <div class="card">
        <h2>Campaigns ({len(campaigns)})</h2>
        <table><thead><tr><th>Campaign</th><th>Status</th><th>Role</th>
        <th>Metric</th><th>Score</th><th>Opened</th></tr></thead>
        <tbody>{campaign_rows}</tbody></table>
    </div>
    """
    return HTMLResponse(
        render_base(f"Candidate {candidate_id}", body)
    )
