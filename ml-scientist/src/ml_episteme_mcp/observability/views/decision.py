"""Promotion-decision detail view — verdict, attribution, evidence refs."""

from __future__ import annotations

from starlette.responses import HTMLResponse

from ...state.store import StateStore
from ..templates import (
    PEER_GUI_URLS,
    escape,
    format_timestamp,
    render_base,
    render_error,
    render_status_badge,
)

# id prefix → local detail route. Evidence refs cite loop-0 entities,
# all of which resolve inside this GUI.
_LOCAL_REF_ROUTES = (
    ("trial-", "/trial/"),
    ("prog-", "/programme/"),
    ("hyp-", "/hypothesis/"),
    ("obs-", "/observation/"),
    ("conc-", "/conclusion/"),
    ("contract-", "/contract/"),
    ("archive-", "/archive/"),
    ("data-ref-", "/dataref/"),
    ("belief-", "/belief/"),
    ("decision-", "/decision/"),
)

# id prefix → (peer name, GUI detail path) for ids minted downstream.
_PEER_REF_ROUTES = (
    ("cand-", "zetesis", "/candidate/"),
    ("claim-", "anamnesis", "/claim/"),
)


def _ref_link(ref: str) -> str:
    """Link an evidence ref to its detail page; unresolvable → plain."""
    for prefix, path in _LOCAL_REF_ROUTES:
        if ref.startswith(prefix):
            return (
                f'<a class="mono" href="{path}{escape(ref)}">'
                f"{escape(ref)}</a>"
            )
    for prefix, peer, path in _PEER_REF_ROUTES:
        if ref.startswith(prefix):
            if base := PEER_GUI_URLS.get(peer):
                href = f"{escape(base)}{path}{escape(ref)}"
                return (
                    f'<a class="mono" href="{href}" '
                    f'title="{escape(peer)} GUI">{escape(ref)}</a>'
                )
            break
    return f'<span class="mono">{escape(ref)}</span>'


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

    contract_cell = (
        f'<a class="mono" href="/contract/{escape(d.contract_id)}">'
        f"{escape(d.contract_id)}</a>"
        if d.contract_id
        else '<span class="muted">—</span>'
    )
    evidence_rows = "".join(
        f'<tr><td>{_ref_link(ref)}</td></tr>' for ref in d.evidence_refs
    ) or '<tr><td class="muted">No evidence refs recorded.</td></tr>'

    body = f"""
    <h1><span class="mono">{escape(d.id)}</span>
        {render_status_badge(d.verdict.value)}</h1>
    <div class="card">
        <table>
            <tr><th>candidate</th><td>{_ref_link(d.candidate_id)}</td></tr>
            <tr><th>contract</th><td>{contract_cell}</td></tr>
            <tr><th>verdict</th>
                <td>{render_status_badge(d.verdict.value)}</td></tr>
            <tr><th>decided by</th><td>{escape(d.decided_by)}</td></tr>
            <tr><th>decided at</th>
                <td>{format_timestamp(d.created_at)}</td></tr>
        </table>
        <p><strong>rationale:</strong> {escape(d.rationale)}</p>
    </div>
    <div class="card">
        <h2>Evidence refs ({len(d.evidence_refs)})</h2>
        <table><tbody>{evidence_rows}</tbody></table>
    </div>
    """
    return HTMLResponse(render_base(f"Decision {decision_id}", body))
