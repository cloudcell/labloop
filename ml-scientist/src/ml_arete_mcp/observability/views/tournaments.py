"""Tournament + proposal detail views — read-only."""

from __future__ import annotations

import json

from starlette.responses import HTMLResponse

from ...state.models import TournamentArm
from ...state.store import ImproverStore
from ..links import entity_url
from ..templates import (
    escape,
    format_timestamp,
    render_base,
    render_error,
)


def _link_id(entity_id: str, gui_bases: dict) -> str:
    """An upstream entity id, linked when its owning GUI has a page."""
    url = entity_url(entity_id, gui_bases)
    code = f'<span class="mono">{escape(entity_id)}</span>'
    return f'<a href="{escape(url)}">{code}</a>' if url else code


def _result_rows(results) -> str:
    return "".join(
        "<tr>"
        f'<td class="mono">{escape(r.id)}'
        + (f' <span class="muted" title="'
           f'{escape(json.dumps(r.corrections))}">✎{len(r.corrections)}'
           "</span>" if r.corrections else "")
        + "</td>"
        f"<td>{escape(json.dumps(r.metrics))}</td>"
        f'<td class="muted">{escape(json.dumps(r.descendant_spec))[:140]}</td>'
        f"<td>{format_timestamp(r.created_at)}</td>"
        "</tr>"
        for r in results
    ) or (
        '<tr><td colspan="4" class="muted">No results recorded on '
        "this arm — the substrate has produced nothing.</td></tr>"
    )


def render_tournament_detail(
    store: ImproverStore, tournament_id: str,
    gui_bases: dict | None = None,
) -> HTMLResponse:
    """Tournament detail: arms, contract, per-arm results, gain."""
    t = store.get_tournament(tournament_id)
    if t is None:
        return HTMLResponse(
            render_error(f"Tournament not found: {tournament_id}", 404),
            status_code=404,
        )
    contract = store.get_meta_contract(t.contract_id)
    results = store.list_tournament_results(t.id)
    refs = store.list_evidence_refs("tournament", t.id)
    links = store.list_tournament_campaigns(t.id)

    parent_results = [
        r for r in results if r.arm == TournamentArm.parent
    ]
    candidate_results = [
        r for r in results if r.arm == TournamentArm.candidate
    ]

    gain_block = ""
    if t.recursive_gain is not None:
        verdict = (
            "candidate out-improves" if t.recursive_gain > 1
            else "parent holds" if t.recursive_gain < 1
            else "dead heat"
        )
        gain_block = f"""
        <div class="card">
            <h2>Recursive gain: {t.recursive_gain:.3f}</h2>
            <p class="muted">E[best descendant | candidate] /
            E[best descendant | parent] over
            <code>{escape(contract.metrics['primary_metric']
                          if contract else '?')}</code>
            — {escape(verdict)}.</p>
        </div>
        """

    ref_rows = "".join(
        "<tr>"
        f'<td class="mono">{escape(e.id)}</td>'
        f"<td>{escape(e.source.value)}</td>"
        f'<td class="mono">{escape(e.tool)}</td>'
        f'<td class="mono muted">{escape(", ".join(e.ref_ids) or "—")}</td>'
        f"<td>{format_timestamp(e.created_at)}</td>"
        "</tr>"
        for e in refs
    ) or (
        '<tr><td colspan="5" class="muted">No evidence pulled in '
        "this context.</td></tr>"
    )

    link_rows = "".join(
        "<tr>"
        f'<td class="mono"><a href="/campaign-link/{escape(l.id)}">'
        f"{escape(l.id)}</a></td>"
        f'<td><span class="status status-{escape(l.arm.value)}">'
        f"{escape(l.arm.value)}</span></td>"
        f'<td class="mono">{_link_id(l.campaign_id, gui_bases or {})}</td>'
        f'<td class="mono muted">'
        f'{_link_id(l.upstream_contract_id, gui_bases or {})}</td>'
        f'<td class="mono muted">{_link_id(l.challenger_id, gui_bases or {})}</td>'
        f"<td>{format_timestamp(l.created_at)}</td>"
        "</tr>"
        for l in links
    ) or (
        '<tr><td colspan="6" class="muted">No orchestrated '
        "campaigns — client-driven tournament, or the "
        "loop1_orchestration channel is unwired.</td></tr>"
    )

    body = f"""
    <h1><span class="mono">{escape(t.id)}</span>
        <span class="status status-{'open' if t.status.value == 'open' else 'concluded'}">
        {escape(t.status.value)}</span></h1>
    <div class="card">
        <p>
          parent
          <a href="/improver/{escape(t.parent_improver_id)}" class="mono">
            {escape(t.parent_improver_id)}</a>
          &nbsp;vs&nbsp;
          candidate
          <a href="/improver/{escape(t.candidate_improver_id)}" class="mono">
            {escape(t.candidate_improver_id)}</a>
        </p>
        <p class="muted">contract
          <a href="/contract/{escape(t.contract_id)}" class="mono">
            {escape(t.contract_id)}</a>
          &nbsp;·&nbsp; equal budget
          <code>{escape(json.dumps(t.budget))}</code>
          {(f'&nbsp;·&nbsp; seeds <code>{escape(json.dumps(t.seeds))}</code>')
           if t.seeds else ""}</p>
        <p class="muted">opened {format_timestamp(t.created_at)}
          {(f'&nbsp;·&nbsp; closed {format_timestamp(t.closed_at)}')
           if t.closed_at else ""}</p>
        {(f'<p class="muted">contract metrics:</p><pre>'
          f'{escape(json.dumps(contract.metrics, indent=2))}</pre>')
         if contract else ""}
    </div>
    {gain_block}
    <h2>Orchestrated campaigns ({len(links)})</h2>
    <table><thead><tr><th>Link</th><th>Arm</th><th>Campaign</th>
        <th>Upstream contract</th><th>Challenger</th><th>Opened</th>
        </tr></thead>
        <tbody>{link_rows}</tbody></table>
    <h2>Parent arm ({len(parent_results)} results)</h2>
    <table><thead><tr><th>Result</th><th>Metrics</th>
        <th>Descendant spec</th><th>When</th></tr></thead>
        <tbody>{_result_rows(parent_results)}</tbody></table>
    <h2>Candidate arm ({len(candidate_results)} results)</h2>
    <table><thead><tr><th>Result</th><th>Metrics</th>
        <th>Descendant spec</th><th>When</th></tr></thead>
        <tbody>{_result_rows(candidate_results)}</tbody></table>
    <h2>Evidence trail ({len(refs)})</h2>
    <table><thead><tr><th>Ref</th><th>Source</th><th>Tool</th>
        <th>Entities</th><th>When</th></tr></thead>
        <tbody>{ref_rows}</tbody></table>
    """
    return HTMLResponse(
        render_base(f"Tournament {tournament_id}", body)
    )


def render_proposal_detail(
    store: ImproverStore, proposal_id: str
) -> HTMLResponse:
    """Proposal detail: the typed delta, admission verdict, trail."""
    from ...enforcement.checks import classify_class_map

    p = store.get_proposal(proposal_id)
    if p is None:
        return HTMLResponse(
            render_error(f"Proposal not found: {proposal_id}", 404),
            status_code=404,
        )
    classification = classify_class_map(p.class_map)
    refs = store.list_evidence_refs("proposal", p.id)

    class_rows = "".join(
        "<tr>"
        f"<td>{escape(comp)}</td>"
        f"<td class=\"muted\">{escape(str(p.class_map.get(comp)))}</td>"
        f"<td><b>{escape(cls)}</b></td>"
        "</tr>"
        for comp, cls in sorted(classification.items())
    )

    ref_rows = "".join(
        "<tr>"
        f'<td class="mono">{escape(e.id)}</td>'
        f"<td>{escape(e.source.value)}</td>"
        f'<td class="mono">{escape(e.tool)}</td>'
        f'<td class="mono muted">{escape(", ".join(e.ref_ids) or "—")}</td>'
        f"<td>{format_timestamp(e.created_at)}</td>"
        "</tr>"
        for e in refs
    ) or (
        '<tr><td colspan="5" class="muted">No evidence pulled in '
        "this context.</td></tr>"
    )

    rejection = (
        f'<p style="color: var(--status-failed)">'
        f"{escape(p.rejection_reason)}</p>"
        if p.rejection_reason else ""
    )

    body = f"""
    <h1><span class="mono">{escape(p.id)}</span>
        <span class="status status-provisional">
        {escape(p.status.value)}</span></h1>
    <div class="card">
        <p>proposed by
          <a href="/improver/{escape(p.proposer_improver_id)}"
             class="mono">{escape(p.proposer_improver_id)}</a>
          &nbsp;·&nbsp; {format_timestamp(p.created_at)}</p>
        {rejection}
        <p class="muted">expected benefit:</p>
        <p>{escape(p.expected_benefit)}</p>
        <p class="muted">falsification:</p>
        <p>{escape(p.falsification)}</p>
        <p class="muted">rollback plan:</p>
        <p>{escape(p.rollback_plan)}</p>
        <p class="muted">spec delta:</p>
        <pre>{escape(json.dumps(p.spec_delta, indent=2))}</pre>
    </div>
    <h2>Boundary classification (authoritative)</h2>
    <table><thead><tr><th>Component</th><th>Declared</th>
        <th>Kernel class</th></tr></thead>
        <tbody>{class_rows}</tbody></table>
    <h2>Evidence trail ({len(refs)})</h2>
    <table><thead><tr><th>Ref</th><th>Source</th><th>Tool</th>
        <th>Entities</th><th>When</th></tr></thead>
        <tbody>{ref_rows}</tbody></table>
    """
    return HTMLResponse(
        render_base(f"Proposal {proposal_id}", body)
    )


def render_campaign_link_detail(
    store: ImproverStore, link_id: str,
    gui_bases: dict | None = None,
) -> HTMLResponse:
    """One tournament↔campaign correlation: arm, upstream contract,
    challenger — the join record as a pageable entity."""
    l = store.get_tournament_campaign_by_id(link_id)
    if l is None:
        return HTMLResponse(
            render_error(f"Campaign link not found: {link_id}", 404),
            status_code=404,
        )
    bases = gui_bases or {}

    body = f"""
    <h1><span class="mono">{escape(l.id)}</span>
        <span class="status status-{escape(l.arm.value)}">
        {escape(l.arm.value)} arm</span></h1>
    <div class="card">
        <table>
            <tr><th>ID</th><td class="mono">{escape(l.id)}</td></tr>
            <tr><th>tournament</th><td>
                <a href="/tournament/{escape(l.tournament_id)}"
                   class="mono">{escape(l.tournament_id)}</a></td></tr>
            <tr><th>arm</th><td>{escape(l.arm.value)}</td></tr>
            <tr><th>campaign</th><td>
                {_link_id(l.campaign_id, bases)}</td></tr>
            <tr><th>upstream contract</th><td>
                {_link_id(l.upstream_contract_id, bases)}</td></tr>
            <tr><th>challenger</th><td>
                {_link_id(l.challenger_id, bases)}</td></tr>
            <tr><th>opened</th><td>{format_timestamp(l.created_at)}</td></tr>
        </table>
    </div>
    """
    return HTMLResponse(render_base(f"Campaign link {link_id}", body))


def render_contract_detail(
    store: ImproverStore, contract_id: str
) -> HTMLResponse:
    """One MetaContract: version, metrics, promotion policy, and the
    tournaments run under it."""
    c = store.get_meta_contract(contract_id)
    if c is None:
        return HTMLResponse(
            render_error(f"Contract not found: {contract_id}", 404),
            status_code=404,
        )
    tournaments, _ = store.list_tournaments(limit=500)
    under = [t for t in tournaments if t.contract_id == c.id]

    tourn_rows = "".join(
        "<tr>"
        f'<td class="mono"><a href="/tournament/{escape(t.id)}">'
        f"{escape(t.id)}</a></td>"
        f'<td><span class="status status-'
        f'{escape(t.status.value)}">{escape(t.status.value)}</span></td>'
        f"<td>{format_timestamp(t.created_at)}</td>"
        "</tr>"
        for t in under
    ) or (
        '<tr><td colspan="3" class="muted">No tournaments under '
        "this contract.</td></tr>"
    )

    optional_rows = ""
    if c.holdouts:
        optional_rows += (
            '<tr><th>holdouts</th><td class="muted"><pre>'
            f"{escape(json.dumps(c.holdouts, indent=2))}</pre></td></tr>"
        )
    if c.budget:
        optional_rows += (
            f"<tr><th>budget</th><td><pre>"
            f"{escape(json.dumps(c.budget, indent=2))}</pre></td></tr>"
        )

    body = f"""
    <h1><span class="mono">{escape(c.id)}</span>
        <span class="muted">v{c.version}</span>
        {('<span class="status status-concluded">frozen</span>'
          if c.frozen else "")}</h1>
    <div class="card">
        <table>
            <tr><th>ID</th><td class="mono">{escape(c.id)}</td></tr>
            <tr><th>version</th><td>{c.version}</td></tr>
            <tr><th>frozen</th><td>{"yes" if c.frozen else "no"}</td></tr>
            <tr><th>created</th><td>{format_timestamp(c.created_at)}</td></tr>
            {optional_rows}
        </table>
    </div>
    <h2>Metrics</h2>
    <pre>{escape(json.dumps(c.metrics, indent=2))}</pre>
    <h2>Promotion policy</h2>
    <pre>{escape(json.dumps(c.promotion_policy, indent=2))}</pre>
    <h2>Tournaments under this contract ({len(under)})</h2>
    <table><thead><tr><th>Tournament</th><th>Status</th><th>Opened</th>
        </tr></thead>
        <tbody>{tourn_rows}</tbody></table>
    """
    return HTMLResponse(
        render_base(f"Contract {contract_id}", body)
    )
