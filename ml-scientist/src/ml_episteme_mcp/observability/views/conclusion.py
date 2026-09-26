"""Conclusion view — verdict, evidence summary."""

from __future__ import annotations

import json
from typing import Any

from starlette.responses import HTMLResponse

from ...state.store import StateStore
from ..templates import escape, format_timestamp, render_base, render_status_badge


def render_conclusion_view(store: StateStore, programme_id: str) -> HTMLResponse:
    """Render the conclusions page."""
    programme = store.get_programme(programme_id)
    if programme is None:
        return HTMLResponse(
            render_base("Not Found", f'<h1 style="color: var(--status-failed)">Programme not found</h1>'),
            status_code=404,
        )

    conclusions = store.list_conclusions(programme_id)
    hypotheses = {h.id: h for h in store.list_hypotheses(programme_id)}

    if not conclusions:
        body = f"""
        <h1>Conclusions</h1>
        <div class="muted" style="margin-bottom: 1rem">
            programme: <a href="/programme/{escape(programme_id)}">{escape(programme_id)}</a>
        </div>
        <p class="muted">No conclusions yet.</p>
        """
        return HTMLResponse(render_base(f"Conclusions — {programme_id}", body))

    cards = []
    for c in conclusions:
        hyp = hypotheses.get(c.hypothesis_id)
        hyp_statement = hyp.statement if hyp else "(hypothesis not found)"

        cards.append(f"""
        <div class="card" id="conc-{escape(c.id)}" style="margin-bottom: 1rem">
            <h3>{render_status_badge(c.verdict.value)}</h3>
            <div class="muted" style="margin-bottom: 0.5rem">{escape(c.id)}</div>
            <p style="margin-bottom: 0.5rem"><strong>Hypothesis:</strong> {escape(hyp_statement)}</p>
            <p style="margin-bottom: 0.5rem"><strong>Evidence:</strong> {escape(c.evidence_summary)}</p>
            <p class="muted" style="font-size: 0.8rem">{format_timestamp(c.created_at)}</p>
        </div>""")

    body = f"""
    <h1>Conclusions</h1>
    <div class="muted" style="margin-bottom: 1rem">
        programme: <a href="/programme/{escape(programme_id)}">{escape(programme_id)}</a>
    </div>
    {''.join(cards)}
    """
    return HTMLResponse(render_base(f"Conclusions — {programme_id}", body))
