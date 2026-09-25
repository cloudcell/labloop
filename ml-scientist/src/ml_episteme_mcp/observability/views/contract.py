"""EvaluationContract detail view — the policy a programme is judged under."""

from __future__ import annotations

from starlette.responses import HTMLResponse

from ...state.store import StateStore
from ..templates import (
    escape,
    format_timestamp,
    render_base,
    render_error,
    render_json_pretty,
)


def render_contract_detail(store: StateStore, contract_id: str) -> HTMLResponse:
    """Render one evaluation contract: version, metrics, promotion policy."""
    c = store.get_evaluation_contract(contract_id)
    if c is None:
        return HTMLResponse(
            render_error(f"Contract not found: {contract_id}", 404),
            status_code=404,
        )

    holdouts_row = ""
    if c.holdouts:
        holdouts_row = (
            "<tr><th>holdouts</th><td class=\"muted\">"
            f"{render_json_pretty(c.holdouts)}</td></tr>"
        )
    budget_row = ""
    if c.budget:
        budget_row = (
            f"<tr><th>budget</th><td>{render_json_pretty(c.budget)}</td></tr>"
        )

    body = f"""
    <h1><span class="mono">{escape(c.id)}</span>
        <span class="muted">v{c.version}</span></h1>

    <div class="section">
        <h2>Overview</h2>
        <table>
            <tr><th>ID</th><td class="mono">{escape(c.id)}</td></tr>
            <tr><th>programme</th><td>
                <a href="/programme/{escape(c.programme_id)}" class="mono">
                    {escape(c.programme_id)}</a></td></tr>
            <tr><th>version</th><td>{c.version}</td></tr>
            <tr><th>created</th><td>{format_timestamp(c.created_at)}</td></tr>
        </table>
    </div>

    <div class="section">
        <h2>Metrics</h2>
        {render_json_pretty(c.metrics)}
    </div>

    <div class="section">
        <h2>Promotion policy</h2>
        {render_json_pretty(c.promotion_policy)}
    </div>

    {(f'<div class="section"><h2>Evaluation detail</h2><table>'
       f'{holdouts_row}{budget_row}</table></div>')
      if holdouts_row or budget_row else ""}
    """
    return HTMLResponse(render_base(f"Contract {contract_id}", body))
