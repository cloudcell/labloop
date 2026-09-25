"""Belief state view — posterior, best trials, parameter importance."""

from __future__ import annotations

import json
from typing import Any

from starlette.responses import HTMLResponse

from ...state.store import StateStore
from ..templates import escape, format_timestamp, render_base, render_json_pretty


def render_belief_detail(store: StateStore, programme_id: str) -> HTMLResponse:
    """Render the belief state page."""
    programme = store.get_programme(programme_id)
    if programme is None:
        return HTMLResponse(
            render_base("Not Found", f'<h1 style="color: var(--status-failed)">Programme not found</h1>'),
            status_code=404,
        )

    beliefs = store.list_beliefs(programme_id)

    # Latest belief
    latest_belief = beliefs[-1] if beliefs else None
    belief_state = {}
    if latest_belief and latest_belief.state_json:
        belief_state = json.loads(latest_belief.state_json)

    body = f"""
    <h1>Belief State</h1>
    <div class="muted" style="margin-bottom: 1rem">
        programme: <a href="/programme/{escape(programme_id)}">{escape(programme_id)}</a>
    </div>

    <div class="section">
        <h2>Direction</h2>
        <p>The optimizer steers toward <strong>{escape(programme.metric_direction)}</strong>.</p>
    </div>

    <div class="section">
        <h2>Current Belief State</h2>
        {render_json_pretty(belief_state) if belief_state else '<p class="muted">No belief state recorded yet.</p>'}
    </div>

    <div class="section">
        <h2>Belief History</h2>
        <table>
            <thead><tr><th>ID</th><th>Updated</th></tr></thead>
            <tbody>
                {''.join(f'<tr><td>{escape(b.id)}</td><td>{format_timestamp(b.updated_at)}</td></tr>' for b in beliefs)
                 if beliefs else '<tr><td colspan="2" class="muted">No beliefs recorded.</td></tr>'}
            </tbody>
        </table>
    </div>
    """
    return HTMLResponse(render_base(f"Belief — {programme_id}", body))


def render_belief_partial(store: StateStore, programme_id: str) -> HTMLResponse:
    """Render a belief summary partial (for HTMX auto-refresh)."""
    beliefs = store.list_beliefs(programme_id)
    latest = beliefs[-1] if beliefs else None

    if latest and latest.state_json:
        state = json.loads(latest.state_json)
        best = state.get("best_trials", [])
        if best:
            best_item = best[0] if isinstance(best, list) else best
            result = best_item.get("result", {}) if isinstance(best_item, dict) else {}
            metrics_str = ", ".join(f"{k}={v:.4f}" for k, v in result.items())
            return HTMLResponse(
                f'<div><strong>Best:</strong> {escape(metrics_str)}</div>'
            )

    return HTMLResponse('<div class="muted">No belief state yet.</div>')
