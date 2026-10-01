"""Promotion-decision detail view — verdict, attribution, evidence refs."""

from __future__ import annotations

from starlette.responses import HTMLResponse

from ...state.store import StateStore
from ..links import link_id, linkify
from ..templates import (
    escape,
    format_timestamp,
    render_base,
    render_error,
    render_status_badge,
)


def render_decision_detail(
    store: StateStore, decision_id: str
) -> HTMLResponse:
    """Promotion decision: verdict, attribution, contract, evidence."""
    d = store.get_promotion_decision(decision_id)
    if d is None:
        return HTMLResponse(
            render_error(f"Decision not found: {decision_id}", 404),
            status_code=404,
        )

    unverified = set(d.unverified_refs)
    evidence_rows = "".join(
        f'<tr><td>{link_id(ref)}</td><td class="muted">'
        f'{"unverified" if ref in unverified else "resolved"}</td></tr>'
        for ref in d.evidence_refs
    ) or '<tr><td class="muted">No evidence refs recorded.</td></tr>'

    body = f"""
    <h1><span class="mono">{escape(d.id)}</span>
        {render_status_badge(d.verdict.value)}</h1>
    <div class="card">
        <table>
            <tr><th>candidate</th><td>{link_id(d.candidate_id)}</td></tr>
            <tr><th>contract</th><td>{link_id(d.contract_id)}</td></tr>
            <tr><th>verdict</th>
                <td>{render_status_badge(d.verdict.value)}</td></tr>
            <tr><th>decided by</th><td>{escape(d.decided_by)}</td></tr>
            <tr><th>decided at</th>
                <td>{format_timestamp(d.created_at)}</td></tr>
        </table>
        <p><strong>rationale:</strong> {linkify(d.rationale)}</p>
    </div>
    <div class="card">
        <h2>Evidence refs ({len(d.evidence_refs)})</h2>
        <table><tbody>{evidence_rows}</tbody></table>
    </div>
    """
    return HTMLResponse(render_base(f"Decision {decision_id}", body))
