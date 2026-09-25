"""Improver + proposal views for the arete observability GUI —
read-only."""

from __future__ import annotations

import json

from starlette.responses import HTMLResponse

from ...state.models import ImproverVersion
from ...state.store import ImproverStore
from ..templates import (
    escape,
    format_timestamp,
    render_base,
    render_error,
)


def _champion_card(store: ImproverStore) -> str:
    champion = store.get_champion()
    if champion is None:
        return (
            '<div class="card"><h2>Champion</h2>'
            '<p class="muted">No champion yet — the first registered '
            "improver bootstraps the pointer.</p></div>"
        )
    return f"""
    <div class="card">
        <h2>Champion</h2>
        <p><a href="/improver/{escape(champion.id)}"
              class="mono">{escape(champion.id)}</a>
           &nbsp;·&nbsp; model <b>{escape(champion.model_ref)}</b>
           &nbsp;·&nbsp; parent
           <span class="mono muted">{escape(champion.parent_id or '—')}</span>
        </p>
        <p class="muted">digest {escape(champion.code_artifact_digest[:24])}…
           &nbsp;·&nbsp; promoted into place {format_timestamp(champion.created_at)}</p>
    </div>
    """


def _imp_row(imp: ImproverVersion) -> str:
    badge = (
        ' <span class="status status-open">champion</span>'
        if imp.is_champion else ""
    )
    return (
        "<tr>"
        f'<td><a href="/improver/{escape(imp.id)}" class="mono">'
        f"{escape(imp.id)}</a>{badge}</td>"
        f'<td class="mono muted">{escape(imp.parent_id or "—")}</td>'
        f"<td>{escape(imp.model_ref)}</td>"
        f'<td class="mono muted">{escape(imp.proposal_id or "—")}</td>'
        f"<td>{format_timestamp(imp.created_at)}</td>"
        "</tr>"
    )


def _imp_table(imps: list[ImproverVersion]) -> str:
    if not imps:
        return (
            '<div class="empty-state"><p>No improver versions yet.</p>'
            "<p>They arrive via <code>register_improver</code>.</p></div>"
        )
    rows = "".join(_imp_row(i) for i in imps)
    return (
        "<table><thead><tr>"
        "<th>Improver</th><th>Parent</th><th>Model</th>"
        "<th>Proposal</th><th>Registered</th>"
        "</tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def _proposal_row(p) -> str:
    return (
        "<tr>"
        f'<td><a href="/proposal/{escape(p.id)}" class="mono">'
        f"{escape(p.id)}</a></td>"
        f'<td class="mono muted">{escape(p.proposer_improver_id)}</td>'
        f'<td><span class="status status-{_status_class(p.status.value)}">'
        f"{escape(p.status.value)}</span></td>"
        f"<td>{len(p.class_map)}</td>"
        f"<td>{format_timestamp(p.created_at)}</td>"
        "</tr>"
    )


def _status_class(status: str) -> str:
    return {
        "admitted": "open",
        "conditional": "provisional",
        "rejected": "dropped",
        "superseded": "abandoned",
    }.get(status, "provisional")


def _proposal_table(proposals) -> str:
    if not proposals:
        return (
            '<div class="empty-state"><p>No proposals yet.</p></div>'
        )
    rows = "".join(_proposal_row(p) for p in proposals)
    return (
        "<table><thead><tr>"
        "<th>Proposal</th><th>Proposer</th><th>Status</th>"
        "<th>Components</th><th>Submitted</th>"
        "</tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def render_improver_list(
    store: ImproverStore, view: str = "all"
) -> HTMLResponse:
    """Landing page: champion, stats, improvers, proposals, tournaments."""
    stats = store.stats()
    imps, _ = store.list_improvers(limit=200)
    status = None if view == "all" else view
    proposals, _ = store.list_proposals(status=status, limit=200)
    tournaments, _ = store.list_tournaments(limit=100)

    pstat = stats["proposals"]

    def tab(label: str, v: str) -> str:
        active = " active" if v == view else ""
        return (
            f'<a class="filter-tab{active}" href="/?view={v}">{label}</a>'
        )

    view_tabs = "".join(
        tab(label, v)
        for label, v in [
            (f"Admitted ({pstat.get('admitted', 0)})", "admitted"),
            (f"Conditional ({pstat.get('conditional', 0)})", "conditional"),
            (f"Rejected ({pstat.get('rejected', 0)})", "rejected"),
            (f"All ({stats['proposals_total']})", "all"),
        ]
    )

    tstat = stats["tournaments"]
    tourn_rows = "".join(
        "<tr>"
        f'<td><a href="/tournament/{escape(t.id)}" class="mono">'
        f"{escape(t.id)}</a></td>"
        f'<td class="mono muted">{escape(t.parent_improver_id)} → '
        f"{escape(t.candidate_improver_id)}</td>"
        f'<td><span class="status status-{_status_class2(t.status.value)}">'
        f"{escape(t.status.value)}</span></td>"
        f"<td>{escape(f'{t.recursive_gain:.3f}' if t.recursive_gain is not None else '—')}</td>"
        f"<td>{format_timestamp(t.created_at)}</td>"
        "</tr>"
        for t in tournaments
    ) or (
        '<tr><td colspan="5" class="muted">No tournaments yet — they '
        "arrive via <code>open_tournament</code>.</td></tr>"
    )

    body = f"""
    <h1>Arete — the recursive loop</h1>
    <div class="stat-grid">
        <span class="stat"><b>{stats['improvers']}</b> improvers</span>
        <span class="stat"><b>{stats['proposals_total']}</b> proposals</span>
        <span class="stat"><b>{stats['tournaments_total']}</b> tournaments</span>
        <span class="stat"><b>{stats['tournament_results']}</b> results</span>
        <span class="stat"><b>{stats['decisions']}</b> decisions</span>
        <span class="stat"><b>{stats['policy_versions']}</b> policies</span>
        <span class="stat"><b>{stats['evidence_refs']}</b> evidence pulls</span>
    </div>
    {_champion_card(store)}
    <h2>Improver versions</h2>
    {_imp_table(imps)}
    <h2>Meta-change proposals</h2>
    <div class="filter-tabs">{view_tabs}</div>
    {_proposal_table(proposals)}
    <h2>Tournaments</h2>
    <table><thead><tr>
        <th>Tournament</th><th>Arms</th><th>Status</th>
        <th>Recursive gain</th><th>Opened</th>
    </tr></thead>
    <tbody>{tourn_rows}</tbody></table>
    <p class="muted">Tournaments: {tstat.get('open', 0)} open ·
        {tstat.get('closed', 0)} closed</p>
    """
    return HTMLResponse(render_base("Arete", body))


def _status_class2(status: str) -> str:
    return "open" if status == "open" else "concluded"


def render_improver_detail(
    store: ImproverStore, improver_id: str
) -> HTMLResponse:
    """Improver detail: record + lineage + decisions + policies."""
    imp = store.get_improver(improver_id)
    if imp is None:
        return HTMLResponse(
            render_error(f"Improver not found: {improver_id}", 404),
            status_code=404,
        )

    chain = store.lineage(improver_id)
    lineage_rows = "".join(
        "<tr>"
        f'<td><a href="/improver/{escape(i.id)}" class="mono">'
        f"{escape(i.id)}</a>"
        + (
            ' <span class="status status-open">champion</span>'
            if i.is_champion else ""
        )
        + "</td>"
        f"<td>{escape(i.model_ref)}</td>"
        f'<td class="mono muted">{escape(i.parent_id or "—")}</td>'
        f"<td>{format_timestamp(i.created_at)}</td>"
        "</tr>"
        for i in chain
    )

    decisions = store.list_meta_decisions(improver_id)
    decision_rows = "".join(
        "<tr>"
        f'<td><span class="status status-{_status_class(d.verdict.value)}">'
        f"{escape(d.verdict.value)}</span></td>"
        f'<td class="mono muted">{escape(d.id)}</td>'
        f"<td>{escape(d.decided_by)}</td>"
        f'<td class="muted">{escape(d.rationale[:140])}</td>'
        f'<td class="mono muted">{escape(d.claim_id or "—")}</td>'
        f"<td>{format_timestamp(d.created_at)}</td>"
        "</tr>"
        for d in decisions
    ) or (
        '<tr><td colspan="6" class="muted">No decisions recorded '
        "for this improver.</td></tr>"
    )

    policies = store.list_policy_versions(improver_id)
    policy_rows = "".join(
        "<tr>"
        f'<td class="mono">{escape(p.id)}</td>'
        f'<td><span class="status status-{_status_class(p.status.value)}">'
        f"{escape(p.status.value)}</span></td>"
        f'<td class="mono muted">{escape(p.decision_id)}</td>'
        f"<td>{format_timestamp(p.created_at)}</td>"
        "</tr>"
        for p in policies
    ) or (
        '<tr><td colspan="4" class="muted">No policy versions '
        "minted.</td></tr>"
    )

    tournaments = store.list_tournaments_for_improver(improver_id)
    tourn_rows = "".join(
        "<tr>"
        f'<td><a href="/tournament/{escape(t.id)}" class="mono">'
        f"{escape(t.id)}</a></td>"
        f"<td>{'candidate' if t.candidate_improver_id == improver_id else 'parent'}</td>"
        f"<td>{escape(t.status.value)}</td>"
        f"<td>{escape(f'{t.recursive_gain:.3f}' if t.recursive_gain is not None else '—')}</td>"
        "</tr>"
        for t in tournaments
    ) or (
        '<tr><td colspan="4" class="muted">No tournaments.</td></tr>'
    )

    badge = (
        ' <span class="status status-open">champion</span>'
        if imp.is_champion else ""
    )
    body = f"""
    <h1><span class="mono">{escape(imp.id)}</span>{badge}</h1>
    <div class="card">
        <p>model <b>{escape(imp.model_ref)}</b> &nbsp;·&nbsp; parent
           <span class="mono">{escape(imp.parent_id or '— (genesis)')}</span>
           &nbsp;·&nbsp; proposal
           <span class="mono">{escape(imp.proposal_id or '—')}</span></p>
        <p class="muted">code digest
           <span class="mono">{escape(imp.code_artifact_digest)}</span></p>
        {(f'<p class="muted">harness digest <span class="mono">'
          f'{escape(imp.harness_artifact_digest)}</span></p>')
         if imp.harness_artifact_digest else ""}
        {(f'<p class="muted">search policy ref <span class="mono">'
          f'{escape(imp.search_policy_ref)}</span></p>')
         if imp.search_policy_ref else ""}
        <p class="muted">capability profile:</p>
        <pre>{escape(json.dumps(imp.capability_profile, indent=2))}</pre>
    </div>
    <h2>Lineage (genesis → this improver)</h2>
    <table><thead><tr><th>Improver</th><th>Model</th><th>Parent</th>
        <th>Registered</th></tr></thead>
        <tbody>{lineage_rows}</tbody></table>
    <h2>Tournaments</h2>
    <table><thead><tr><th>Tournament</th><th>Role</th><th>Status</th>
        <th>Recursive gain</th></tr></thead>
        <tbody>{tourn_rows}</tbody></table>
    <h2>Decisions</h2>
    <table><thead><tr><th>Verdict</th><th>ID</th><th>Decided by</th>
        <th>Rationale</th><th>Claim</th><th>When</th></tr></thead>
        <tbody>{decision_rows}</tbody></table>
    <h2>Policy versions</h2>
    <table><thead><tr><th>Policy</th><th>Status</th><th>Decision</th>
        <th>Minted</th></tr></thead>
        <tbody>{policy_rows}</tbody></table>
    """
    return HTMLResponse(render_base(f"Improver {improver_id}", body))
