"""Candidate detail view — the researcher version spec, its lineage,
and the programmes attributed to it (read-only).

Ontological category: information content entity — a CandidateVersion
is a spec, immutable once registered; champion/challenger is derived
from promotion decisions, not stored here.
"""

from __future__ import annotations

import json

from starlette.responses import HTMLResponse

from ...state.store import StateStore
from ..links import link_id
from ..templates import (
    escape,
    format_timestamp,
    render_base,
    render_error,
    render_json_pretty,
)


def render_candidate_detail(
    store: StateStore, candidate_id: str
) -> HTMLResponse:
    """Render one CandidateVersion: spec, lineage, attributed work."""
    c = store.get_candidate_version(candidate_id)
    if c is None:
        return HTMLResponse(
            render_error(f"Candidate not found: {candidate_id}", 404),
            status_code=404,
        )

    lineage = store.get_candidate_lineage(candidate_id)
    lineage_rows = "".join(
        "<tr>"
        f"<td>{link_id(i.id)}</td>"
        f"<td>{escape(i.model_ref)}</td>"
        f"<td>{link_id(i.parent_id)}</td>"
        f"<td>{format_timestamp(i.created_at)}</td>"
        "</tr>"
        for i in lineage
    )

    # Scorecard: programmes this candidate is attributed with, with
    # trial/observation/conclusion counts and best metric values.
    scorecard = store.get_candidate_scorecard(candidate_id)
    prog_rows = "".join(
        "<tr>"
        f"<td>{link_id(p['programme_id'])}</td>"
        f"<td>{escape(p['status'])}</td>"
        f"<td>{p['trials']}</td>"
        f"<td>{p['observations']}</td>"
        f"<td>{p['conclusions']}</td>"
        f'<td class="mono muted">{escape(json.dumps(p["best_metrics"]) if p["best_metrics"] else "—")}</td>'
        "</tr>"
        for p in scorecard["programmes"]
    ) or (
        '<tr><td colspan="6" class="muted">No programmes attributed '
        "to this candidate.</td></tr>"
    )

    harness_row = ""
    if c.harness_artifact_digest:
        harness_row = (
            '<tr><th>harness digest</th>'
            f'<td class="hash-prefix">{escape(c.harness_artifact_digest)}</td>'
            "</tr>"
        )
    policy_row = ""
    if c.search_policy_ref:
        policy_row = (
            "<tr><th>search policy</th>"
            f"<td>{link_id(c.search_policy_ref)}</td></tr>"
        )

    body = f"""
    <h1><span class="mono">{escape(c.id)}</span>
        <span class="muted">candidate</span></h1>
    <div class="section">
        <h2>Overview</h2>
        <table>
            <tr><th>ID</th><td>{link_id(c.id)}</td></tr>
            <tr><th>parent</th><td>{link_id(c.parent_id)}</td></tr>
            <tr><th>model</th><td>{escape(c.model_ref)}</td></tr>
            <tr><th>code digest</th>
                <td class="hash-prefix">{escape(c.code_artifact_digest)}</td></tr>
            {harness_row}
            {policy_row}
            <tr><th>registered</th>
                <td>{format_timestamp(c.created_at)}</td></tr>
        </table>
        <p class="muted">capability profile:</p>
        {render_json_pretty(c.capability_profile)}
    </div>
    <div class="section">
        <h2>Lineage (self → genesis)</h2>
        <table>
            <thead><tr><th>ID</th><th>Model</th><th>Parent</th>
                <th>Registered</th></tr></thead>
            <tbody>{lineage_rows}</tbody>
        </table>
    </div>
    <div class="section">
        <h2>Attributed programmes ({scorecard['programme_count']})</h2>
        <table>
            <thead><tr><th>Programme</th><th>Status</th><th>Trials</th>
                <th>Observations</th><th>Conclusions</th>
                <th>Best metrics</th></tr></thead>
            <tbody>{prog_rows}</tbody>
        </table>
    </div>
    """
    return HTMLResponse(render_base(f"Candidate {candidate_id}", body))
