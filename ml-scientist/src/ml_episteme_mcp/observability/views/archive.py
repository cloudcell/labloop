"""Archive views — browse archived programmes.

Read-only views over the archive_registry and archive SQLite files.
"""

from __future__ import annotations

import json
from pathlib import Path

from starlette.responses import HTMLResponse

from ...archive import Archiver
from ...state.store import StateStore
from ..templates import (
    escape,
    format_timestamp,
    render_base,
    render_json_pretty,
    render_status_badge,
)


def render_archive_list(
    store: StateStore,
    archiver: Archiver | None = None,
) -> HTMLResponse:
    """Render the archive list page."""
    if archiver is None:
        body = """
        <h1>Archives</h1>
        <div class="empty-state">
            <p>Archiving is not enabled.</p>
            <p style="margin-top: 1rem">Enable archiving in <code>ml-episteme.toml</code>:</p>
            <pre>[archive]
enabled = true
batch_size = 10</pre>
        </div>
        """
        return HTMLResponse(render_base("Archives", body))

    archives = archiver.list_archives()

    if not archives:
        body = """
        <h1>Archives</h1>
        <div class="empty-state">
            <p>No archives yet.</p>
            <p style="margin-top: 1rem">Archives are created automatically when programmes
            are closed (completed or abandoned).</p>
        </div>
        """
        return HTMLResponse(render_base("Archives", body))

    rows_html = []
    for a in archives:
        archive_id = a["archive_id"]
        sealed_badge = (
            '<span class="status status-completed">Sealed</span>'
            if a["sealed"]
            else '<span class="status status-active">Open</span>'
        )
        archive_hash = a.get("archive_hash") or "—"
        hash_display = archive_hash[:20] + "..." if len(archive_hash) > 20 else archive_hash
        sealed_at = format_timestamp(a["sealed_at"]) if a.get("sealed_at") else "—"

        rows_html.append(f"""
        <tr>
            <td><a href="/archive/{escape(archive_id)}">{escape(archive_id)}</a></td>
            <td>{sealed_badge}</td>
            <td>{a['programme_count']} / {a['batch_size']}</td>
            <td><span class="hash-prefix">{escape(hash_display)}</span></td>
            <td>{format_timestamp(a.get('sealed_at'))}</td>
            <td>{format_timestamp(a['created_at'])}</td>
        </tr>
        """)

    body = f"""
    <h1>Archives</h1>
    <p class="muted">Archived programmes are read-only historical evidence.
    They cannot be restored to the live database.</p>
    <table>
        <thead>
            <tr>
                <th>Archive ID</th>
                <th>Status</th>
                <th>Programmes</th>
                <th>Hash</th>
                <th>Sealed At</th>
                <th>Created At</th>
            </tr>
        </thead>
        <tbody>
            {''.join(rows_html)}
        </tbody>
    </table>
    """
    return HTMLResponse(render_base("Archives", body))


def render_archive_detail(
    store: StateStore,
    archiver: Archiver | None,
    archive_id: str,
) -> HTMLResponse:
    """Render a single archive's detail page."""
    if archiver is None:
        body = '<div class="empty-state"><p>Archiving is not enabled.</p></div>'
        return HTMLResponse(render_base("Archive", body))

    archive = archiver.get_archive(archive_id)
    if archive is None:
        body = f'<div class="empty-state"><p>Archive not found: {escape(archive_id)}</p></div>'
        return HTMLResponse(render_base("Archive", body))

    sealed_badge = (
        '<span class="status status-completed">Sealed</span>'
        if archive["sealed"]
        else '<span class="status status-active">Open</span>'
    )
    archive_hash = archive.get("archive_hash") or "—"

    # Programme list
    programmes = archive.get("programmes", [])
    if programmes:
        prog_rows = []
        for p in programmes:
            prog_rows.append(f"""
            <tr>
                <td><a href="/archive/{escape(archive_id)}/programme/{escape(p['programme_id'])}">{escape(p['programme_id'])}</a></td>
                <td>{escape(p['programme_goal'])}</td>
                <td>{p['row_count']}</td>
                <td>{format_timestamp(p['archived_at'])}</td>
            </tr>
            """)
        prog_table = f"""
        <h2>Programmes ({len(programmes)})</h2>
        <table>
            <thead>
                <tr><th>Programme ID</th><th>Goal</th><th>Rows</th><th>Archived At</th></tr>
            </thead>
            <tbody>{''.join(prog_rows)}</tbody>
        </table>
        """
    else:
        prog_table = "<p class='muted'>No programmes in this archive.</p>"

    body = f"""
    <h1>{escape(archive_id)}</h1>
    <div class="grid-2">
        <div>
            <h2>Archive Details</h2>
            <table>
                <tr><th>Status</th><td>{sealed_badge}</td></tr>
                <tr><th>Path</th><td><code>{escape(archive['archive_path'])}</code></td></tr>
                <tr><th>Programmes</th><td>{archive['programme_count']} / {archive['batch_size']}</td></tr>
                <tr><th>Hash</th><td><span class="hash-prefix">{escape(archive_hash)}</span></td></tr>
                <tr><th>Size</th><td>{archive.get('archive_size_bytes') or '—'} bytes</td></tr>
                <tr><th>Created</th><td>{format_timestamp(archive['created_at'])}</td></tr>
                <tr><th>Sealed</th><td>{format_timestamp(archive.get('sealed_at'))}</td></tr>
            </table>
        </div>
        <div>
            <h2>Verify</h2>
            <p>Run <code>verify_archive</code> MCP tool to verify integrity:</p>
            <pre>verify_archive(archive_id="{escape(archive_id)}")</pre>
        </div>
    </div>
    {prog_table}
    """
    return HTMLResponse(render_base(f"Archive {archive_id}", body))


def render_archived_programme_detail(
    store: StateStore,
    archiver: Archiver | None,
    archive_id: str,
    programme_id: str,
) -> HTMLResponse:
    """Render an archived programme's detail page (read-only).

    Shows all artifacts: hypotheses, trials (with links to trial detail),
    beliefs, conclusions, bundles, data refs, and code snippets.
    """
    if archiver is None:
        body = '<div class="empty-state"><p>Archiving is not enabled.</p></div>'
        return HTMLResponse(render_base("Archived Programme", body))

    programme = archiver.get_archived_programme(programme_id)
    if programme is None or "error" in programme:
        body = f'<div class="empty-state"><p>Archived programme not found: {escape(programme_id)}</p></div>'
        return HTMLResponse(render_base("Archived Programme", body))

    # Basic info
    pid = programme.get("id", programme_id)
    goal = programme.get("goal", "")
    status = programme.get("status", "")
    created_at = programme.get("created_at", "")
    archived_at = programme.get("archived_at", "")
    direction = programme.get("metric_direction", "maximize")
    constraints_json = programme.get("constraints_json", "{}")
    allowed_vars_json = programme.get("allowed_variables_json", "[]")

    base_url = f"/archive/{escape(archive_id)}/programme/{escape(pid)}"

    # --- Hypotheses ---
    hypotheses = programme.get("hypotheses", [])
    hyp_rows = []
    for h in hypotheses:
        hyp_rows.append(f"""
        <tr id="hyp-{escape(h['id'])}">
            <td><a href="#hyp-{escape(h['id'])}">{escape(h['id'])}</a></td>
            <td>{escape(h['statement'][:200])}{'...' if len(h['statement']) > 200 else ''}</td>
            <td>{render_status_badge(h['status'])}</td>
        </tr>""")
    hyp_section = f"""
    <div class="section">
        <h2>Hypotheses ({len(hypotheses)})</h2>
        <table>
            <thead><tr><th>ID</th><th>Statement</th><th>Status</th></tr></thead>
            <tbody>
                {''.join(hyp_rows) if hyp_rows else '<tr><td colspan="3" class="muted">No hypotheses.</td></tr>'}
            </tbody>
        </table>
    </div>""" if hypotheses else ""

    # --- Trials ---
    trials = programme.get("trials", [])
    trial_rows = []
    for t in trials:
        dur = f"{t.get('duration_seconds'):.1f}s" if t.get('duration_seconds') else "—"
        trial_rows.append(f"""
        <tr>
            <td><a href="{base_url}/trial/{escape(t['id'])}">{escape(t['id'])}</a></td>
            <td>{render_status_badge(t['status'])}</td>
            <td>{escape(t.get('config_json', '')[:80])}{'...' if len(t.get('config_json', '')) > 80 else ''}</td>
            <td>{dur}</td>
        </tr>""")
    trial_section = f"""
    <div class="section">
        <h2>Trials ({len(trials)})</h2>
        <table>
            <thead><tr><th>ID</th><th>Status</th><th>Config</th><th>Duration</th></tr></thead>
            <tbody>
                {''.join(trial_rows) if trial_rows else '<tr><td colspan="4" class="muted">No trials.</td></tr>'}
            </tbody>
        </table>
    </div>""" if trials else ""

    # --- Observations ---
    observations = programme.get("observations", [])
    obs_rows = []
    for o in observations:
        metrics_short = escape(o.get("metrics_json", "")[:100])
        obs_rows.append(f"""
        <tr>
            <td><a href="{base_url}/trial/{escape(o['trial_id'])}">{escape(o['trial_id'])}</a></td>
            <td><code>{metrics_short}{'...' if len(o.get('metrics_json', '')) > 100 else ''}</code></td>
            <td>{escape(o.get('spatiotemporal_region', '—'))}</td>
        </tr>""")
    obs_section = f"""
    <div class="section">
        <h2>Observations ({len(observations)})</h2>
        <table>
            <thead><tr><th>Trial</th><th>Metrics</th><th>Region</th></tr></thead>
            <tbody>
                {''.join(obs_rows) if obs_rows else '<tr><td colspan="3" class="muted">No observations.</td></tr>'}
            </tbody>
        </table>
    </div>""" if observations else ""

    # --- Beliefs ---
    beliefs = programme.get("beliefs", [])
    belief_rows = []
    for b in beliefs:
        # Parse belief state to show best trial + metrics
        try:
            state = json.loads(b.get("state_json", "{}")) if b.get("state_json") else {}
        except Exception:
            state = {}
        best_trials = state.get("best_trials", [])
        metrics = state.get("metrics", {})
        # Build best trial links
        best_trial_links = []
        if isinstance(best_trials, list):
            for bt in best_trials[:3]:
                tid = bt.get("trial_id", "") if isinstance(bt, dict) else str(bt)
                if tid:
                    best_trial_links.append(
                        f'<a href="{base_url}/trial/{escape(tid)}">{escape(tid)}</a>'
                    )
        best_html = ", ".join(best_trial_links) if best_trial_links else "—"
        metrics_html = render_json_pretty(metrics) if metrics else "—"
        belief_rows.append(f"""
        <tr>
            <td>{escape(b['id'])}</td>
            <td>{best_html}</td>
            <td>{metrics_html}</td>
            <td>{format_timestamp(b.get('updated_at'))}</td>
        </tr>""")
    belief_section = f"""
    <div class="section">
        <h2>Beliefs ({len(beliefs)})</h2>
        <table>
            <thead><tr><th>ID</th><th>Best Trials</th><th>Metrics</th><th>Updated</th></tr></thead>
            <tbody>
                {''.join(belief_rows) if belief_rows else '<tr><td colspan="4" class="muted">No beliefs.</td></tr>'}
            </tbody>
        </table>
    </div>""" if beliefs else ""

    # --- Conclusions ---
    conclusions = programme.get("conclusions", [])
    conc_rows = []
    for c in conclusions:
        # Link hypothesis_id to the hypothesis anchor on this page
        hyp_id = c.get("hypothesis_id", "")
        hyp_link = f'<a href="{base_url}#hyp-{escape(hyp_id)}">{escape(hyp_id)}</a>' if hyp_id else "—"
        conc_rows.append(f"""
        <tr id="conc-{escape(c['id'])}">
            <td>{escape(c['id'])}</td>
            <td>{render_status_badge(c['verdict'])}</td>
            <td>{hyp_link}</td>
            <td>{escape(c.get('evidence_summary', ''))}</td>
            <td>{format_timestamp(c.get('created_at'))}</td>
        </tr>""")
    conc_section = f"""
    <div class="section">
        <h2>Conclusions ({len(conclusions)})</h2>
        <table>
            <thead><tr><th>ID</th><th>Verdict</th><th>Hypothesis</th><th>Evidence</th><th>Created</th></tr></thead>
            <tbody>
                {''.join(conc_rows) if conc_rows else '<tr><td colspan="5" class="muted">No conclusions.</td></tr>'}
            </tbody>
        </table>
    </div>""" if conclusions else ""

    # --- Bundles ---
    bundles = programme.get("bundles", [])
    bundle_rows = []
    for b in bundles:
        code_hash = b.get("code_hash") or ""
        code_ref = b.get("code_ref", "—")
        if code_hash:
            code_hash_short = code_hash[:20] + "..." if len(code_hash) > 20 else code_hash
            # Link to code snippet section if we have it
            code_cell = f'<a href="#code-{escape(code_hash[:12])}"><span class="hash-prefix">{escape(code_hash_short)}</span></a>'
        else:
            # Pre-Phase-0 bundle: no content hash, show code_ref as-is
            code_cell = f'<span class="muted">{escape(code_ref)}</span>'
        bundle_rows.append(f"""
        <tr>
            <td><a href="{base_url}/trial/{escape(b['trial_id'])}">{escape(b['trial_id'])}</a></td>
            <td>{escape(b['id'])}</td>
            <td>{code_cell}</td>
            <td>{escape(b.get('env_ref', '—'))}</td>
            <td><span class="muted" title="Provenance: original carrier path at capture time">{escape(code_ref)}</span></td>
        </tr>""")
    bundle_section = f"""
    <div class="section">
        <h2>Bundles ({len(bundles)})</h2>
        <table>
            <thead><tr><th>Trial</th><th>Bundle ID</th><th>Code Hash</th><th>Env</th><th>Code Ref (provenance)</th></tr></thead>
            <tbody>
                {''.join(bundle_rows) if bundle_rows else '<tr><td colspan="5" class="muted">No bundles.</td></tr>'}
            </tbody>
        </table>
    </div>""" if bundles else ""

    # --- Data Refs ---
    data_refs = programme.get("data_refs", [])
    ref_rows = []
    for d in data_refs:
        content_hash = d.get("content_hash") or "—"
        content_hash_short = content_hash[:20] + "..." if len(content_hash) > 20 else content_hash
        ref_rows.append(f"""
        <tr>
            <td>{escape(d['id'])}</td>
            <td>{escape(d.get('split', '—'))}</td>
            <td>{escape(d.get('regime', '—'))}</td>
            <td><span class="hash-prefix">{escape(content_hash_short)}</span></td>
            <td>{escape(d.get('reproducibility_risk', '—'))}</td>
        </tr>""")
    ref_section = f"""
    <div class="section">
        <h2>Data Refs ({len(data_refs)})</h2>
        <table>
            <thead><tr><th>ID</th><th>Split</th><th>Regime</th><th>Content Hash</th><th>Risk</th></tr></thead>
            <tbody>
                {''.join(ref_rows) if ref_rows else '<tr><td colspan="5" class="muted">No data refs.</td></tr>'}
            </tbody>
        </table>
    </div>""" if data_refs else ""

    # --- Code Snippets ---
    code_snippets = programme.get("code_snippets", [])
    snippet_rows = []
    for cs in code_snippets:
        code_hash = cs.get("code_hash", "")
        code_hash_short = code_hash[:20] + "..." if len(code_hash) > 20 else code_hash
        size = cs.get("size_bytes", 0)
        snippet_rows.append(f"""
        <tr id="code-{escape(code_hash[:12])}">
            <td><span class="hash-prefix">{escape(code_hash_short)}</span></td>
            <td>{escape(cs.get('language', '—'))}</td>
            <td>{size} bytes</td>
            <td>{escape(cs.get('original_path', '—'))}</td>
        </tr>""")
    snippet_section = f"""
    <div class="section">
        <h2>Code Snippets ({len(code_snippets)})</h2>
        <p class="muted" style="margin-bottom: 0.5rem">Source code captured at bundle time, content-addressed by SHA-256.</p>
        <table>
            <thead><tr><th>Code Hash</th><th>Language</th><th>Size</th><th>Original Path</th></tr></thead>
            <tbody>
                {''.join(snippet_rows) if snippet_rows else '<tr><td colspan="4" class="muted">No code snippets.</td></tr>'}
            </tbody>
        </table>
    </div>""" if code_snippets else ""

    # Constraints
    try:
        constraints = json.loads(constraints_json) if constraints_json else {}
    except Exception:
        constraints = {}
    try:
        allowed_vars = json.loads(allowed_vars_json) if allowed_vars_json else []
    except Exception:
        allowed_vars = []

    body = f"""
    <h1>{escape(goal)}</h1>
    <div class="status status-completed">Archived</div>
    <p class="muted">This programme is archived and read-only. It cannot be modified or restored.</p>
    <table>
        <tr><th>Programme ID</th><td><code>{escape(pid)}</code></td></tr>
        <tr><th>Status</th><td>{render_status_badge(status)}</td></tr>
        <tr><th>Direction</th><td>{escape(direction)}</td></tr>
        <tr><th>Created</th><td>{format_timestamp(created_at)}</td></tr>
        <tr><th>Archived</th><td>{format_timestamp(archived_at)}</td></tr>
        <tr><th>Archive</th><td><a href="/archive/{escape(archive_id)}">{escape(archive_id)}</a></td></tr>
        <tr><th>Row Count</th><td>{programme.get('row_count', '—')}</td></tr>
    </table>

    <div class="section">
        <h2>Constraints</h2>
        {render_json_pretty(constraints)}
    </div>

    <div class="section">
        <h2>Allowed Variables</h2>
        <pre>{escape(', '.join(allowed_vars))}</pre>
    </div>

    {hyp_section}
    {trial_section}
    {obs_section}
    {belief_section}
    {conc_section}
    {bundle_section}
    {ref_section}
    {snippet_section}
    """
    return HTMLResponse(render_base(f"Archived: {goal}", body))


def render_archived_trial_detail(
    store: StateStore,
    archiver: Archiver | None,
    archive_id: str,
    programme_id: str,
    trial_id: str,
) -> HTMLResponse:
    """Render an archived trial's detail page (read-only).

    Shows the trial config, bundle, observations, and code snippets.
    """
    if archiver is None:
        body = '<div class="empty-state"><p>Archiving is not enabled.</p></div>'
        return HTMLResponse(render_base("Archived Trial", body))

    trial = archiver.get_archived_trial(programme_id, trial_id)
    if trial is None or "error" in trial:
        body = f'<div class="empty-state"><p>Archived trial not found: {escape(trial_id)}</p></div>'
        return HTMLResponse(render_base("Archived Trial", body))

    base_url = f"/archive/{escape(archive_id)}/programme/{escape(programme_id)}"

    # Trial config
    try:
        config = json.loads(trial.get("config_json", "{}")) if trial.get("config_json") else {}
    except Exception:
        config = {}

    # Observations
    observations = trial.get("observations", [])
    obs_rows = []
    for o in observations:
        try:
            metrics = json.loads(o.get("metrics_json", "{}")) if o.get("metrics_json") else {}
        except Exception:
            metrics = {}
        try:
            variance = json.loads(o.get("variance_json", "{}")) if o.get("variance_json") else {}
        except Exception:
            variance = {}
        obs_rows.append(f"""
        <tr id="obs-{escape(o['id'])}">
            <td>{escape(o['id'])}</td>
            <td>{render_json_pretty(metrics)}</td>
            <td>{render_json_pretty(variance)}</td>
            <td>{escape(o.get('spatiotemporal_region', '—'))}</td>
            <td>{format_timestamp(o.get('created_at'))}</td>
        </tr>""")

    # Bundle
    bundles = trial.get("bundle", [])
    bundle_html = ""
    for b in bundles:
        try:
            seeds = json.loads(b.get("seeds_json", "[]")) if b.get("seeds_json") else []
        except Exception:
            seeds = []
        try:
            splits = json.loads(b.get("splits_json", "{}")) if b.get("splits_json") else {}
        except Exception:
            splits = {}
        try:
            data_ref_ids = json.loads(b.get("data_refs_json", "[]")) if b.get("data_refs_json") else []
        except Exception:
            data_ref_ids = []
        # Parse extra code hashes for display
        try:
            extra_hashes = json.loads(b.get("code_hash_extra_json", "[]")) if b.get("code_hash_extra_json") else []
        except Exception:
            extra_hashes = []
        extra_hash_html = ""
        if extra_hashes:
            extra_links = []
            for eh in extra_hashes:
                extra_links.append(
                    f'<a href="#code-{escape(eh[:12])}"><span class="hash-prefix">{escape(eh)}</span></a>'
                )
            extra_hash_html = f'<tr><th>Extra Code Hashes</th><td>{", ".join(extra_links)}</td></tr>'
        bundle_html += f"""
        <table>
            <tr><th>Bundle ID</th><td><code>{escape(b['id'])}</code></td></tr>
            <tr><th>Code Hash</th><td><a href="#code-{escape((b.get('code_hash') or '')[:12])}"><span class="hash-prefix">{escape(b.get('code_hash', '—'))}</span></a></td></tr>
            {extra_hash_html}
            <tr><th>Code Ref</th><td><span class="muted" title="Provenance: original carrier path at capture time">{escape(b.get('code_ref', '—'))}</span></td></tr>
            <tr><th>Env Ref</th><td>{escape(b.get('env_ref', '—'))}</td></tr>
            <tr><th>Seeds</th><td>{escape(str(seeds))}</td></tr>
            <tr><th>Splits</th><td>{render_json_pretty(splits)}</td></tr>
            <tr><th>Data Refs</th><td>{escape(', '.join(data_ref_ids) if data_ref_ids else '—')}</td></tr>
            <tr><th>Baseline</th><td>{escape(b.get('baseline_ref', '—'))}</td></tr>
        </table>"""

    # Data refs
    data_refs = trial.get("data_refs", [])
    ref_rows = []
    for d in data_refs:
        ref_rows.append(f"""
        <tr>
            <td>{escape(d['id'])}</td>
            <td>{escape(d.get('split', '—'))}</td>
            <td>{escape(d.get('regime', '—'))}</td>
            <td><span class="hash-prefix">{escape((d.get('content_hash') or '—')[:20])}</span></td>
            <td>{escape(d.get('reproducibility_risk', '—'))}</td>
        </tr>""")

    # Code snippets
    code_snippets = trial.get("code_snippets", [])
    snippet_html = ""
    for cs in code_snippets:
        code_text = cs.get("code_text", "")
        code_hash = cs.get("code_hash", "—")
        snippet_html += f"""
        <div class="card" style="margin-top: 1rem" id="code-{escape(code_hash[:12])}">
            <h3>{escape(code_hash[:30])}...</h3>
            <p class="muted">Language: {escape(cs.get('language', '—'))} | Size: {cs.get('size_bytes', 0)} bytes | Original path: {escape(cs.get('original_path', '—'))}</p>
            <pre style="margin-top: 0.5rem; max-height: 400px; overflow: auto">{escape(code_text)}</pre>
        </div>"""

    # Trial artifacts (from SQLite, gzip-compressed)
    trial_artifacts = trial.get("trial_artifacts", [])
    artifact_rows = ""
    for art in trial_artifacts:
        size = art.get("size_bytes", 0)
        size_str = f"{size} bytes" if size < 1024 else f"{size / 1024:.1f} KB"
        comp = art.get("compressed_size_bytes", 0)
        ratio = f"{comp / size * 100:.0f}%" if size > 0 else "—"
        fname = art.get("filename", "—")
        artifact_url = f"{base_url}/trial/{escape(trial_id)}/artifact/{escape(fname)}"
        artifact_rows += f"""
        <tr>
            <td class="artifact-file"><a href="{artifact_url}">{escape(fname)}</a></td>
            <td>{escape(art.get('artifact_type', '—'))}</td>
            <td>{size_str}</td>
            <td class="muted">{ratio}</td>
        </tr>"""

    body = f"""
    <h1>Trial: {escape(trial_id)}</h1>
    <div class="status status-completed">Archived</div>
    <p class="muted"><a href="{base_url}">← Back to programme</a></p>
    <table>
        <tr><th>Trial ID</th><td><code>{escape(trial_id)}</code></td></tr>
        <tr><th>Programme</th><td><a href="{base_url}">{escape(programme_id)}</a></td></tr>
        <tr><th>Hypothesis</th><td>{escape(trial.get('hypothesis_id', '—'))}</td></tr>
        <tr><th>Status</th><td>{render_status_badge(trial.get('status', '—'))}</td></tr>
        <tr><th>Duration</th><td>{trial.get('duration_seconds') or '—'}</td></tr>
        <tr><th>Created</th><td>{format_timestamp(trial.get('created_at'))}</td></tr>
    </table>

    <div class="section">
        <h2>Config</h2>
        {render_json_pretty(config)}
    </div>

    <div class="section">
        <h2>Bundle</h2>
        {bundle_html if bundle_html else '<p class="muted">No bundle.</p>'}
    </div>

    <div class="section">
        <h2>Observations ({len(observations)})</h2>
        <table>
            <thead><tr><th>ID</th><th>Metrics</th><th>Variance</th><th>Region</th><th>Created</th></tr></thead>
            <tbody>
                {''.join(obs_rows) if obs_rows else '<tr><td colspan="5" class="muted">No observations.</td></tr>'}
            </tbody>
        </table>
    </div>

    {f'''
    <div class="section">
        <h2>Data Refs ({len(data_refs)})</h2>
        <table>
            <thead><tr><th>ID</th><th>Split</th><th>Regime</th><th>Content Hash</th><th>Risk</th></tr></thead>
            <tbody>{''.join(ref_rows)}</tbody>
        </table>
    </div>''' if data_refs else ''}

    {f'''
    <div class="section">
        <h2>Code Snippets ({len(code_snippets)})</h2>
        {snippet_html if snippet_html else '<p class="muted">No code snippets.</p>'}
    </div>''' if code_snippets else ''}

    {f'''
    <div class="section">
        <h2>Artifacts ({len(trial_artifacts)})</h2>
        <p class="muted" style="margin-bottom: 0.5rem">Stored in SQLite (gzip-compressed).</p>
        <table>
            <thead><tr><th>Filename</th><th>Type</th><th>Size</th><th>Compressed</th></tr></thead>
            <tbody>
                {artifact_rows if artifact_rows else '<tr><td colspan="4" class="muted">No artifacts.</td></tr>'}
            </tbody>
        </table>
    </div>''' if trial_artifacts else ''}
    """
    return HTMLResponse(render_base(f"Archived Trial: {trial_id}", body))
