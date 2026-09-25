"""Trial detail view — config, bundle, data provenance, artifacts, observations, sparkline."""

from __future__ import annotations

import json
import os
from typing import Any

from starlette.responses import HTMLResponse

from ...state.store import StateStore
from ..templates import (
    escape,
    format_timestamp,
    render_base,
    render_json_pretty,
    render_status_badge,
    risk_class,
)
from ..sparkline import sparkline


def _render_data_refs_section(store: StateStore, bundle) -> str:
    """C2: Render the data provenance section from the bundle's data_refs."""
    if not bundle or not hasattr(bundle, "data_refs_json") or not bundle.data_refs_json:
        return ""

    data_ref_ids = json.loads(bundle.data_refs_json) if isinstance(bundle.data_refs_json, str) else bundle.data_refs_json
    if not data_ref_ids:
        return ""

    rows = []
    for ref_id in data_ref_ids:
        ref = store.get_data_ref(ref_id)
        if ref is None:
            rows.append(f"""
            <tr>
                <td class="data-ref-link">{escape(ref_id)}</td>
                <td colspan="5" class="muted">DataRef not found</td>
            </tr>""")
            continue

        hash_short = ref.content_hash[:20] + "..." if ref.content_hash and len(ref.content_hash) > 20 else (ref.content_hash or "—")
        risk_cls = risk_class(ref.reproducibility_risk)
        risk_label = ref.reproducibility_risk or "—"

        rows.append(f"""
        <tr>
            <td><a href="/dataref/{escape(ref_id)}" class="data-ref-link">{escape(ref_id)}</a></td>
            <td>{escape(ref.split)}</td>
            <td>{escape(ref.regime)}</td>
            <td class="hash-prefix">{escape(hash_short)}</td>
            <td><span class="{risk_cls}">{escape(risk_label)}</span></td>
            <td>{format_timestamp(ref.created_at)}</td>
        </tr>""")

    return f"""
    <div class="section">
        <h2>Data Provenance</h2>
        <table>
            <thead><tr><th>DataRef ID</th><th>Split</th><th>Regime</th><th>Content Hash</th><th>Reproducibility Risk</th><th>Created</th></tr></thead>
            <tbody>
                {''.join(rows)}
            </tbody>
        </table>
    </div>"""


def _render_artifacts_section(store: StateStore, trial) -> str:
    """C2 + M2: Render the artifacts section.

    Serves ONLY from SQLite (artifact_files + trial_artifacts).
    The on-disk artifacts/ directory is transient staging — deleted
    after capture — so we never list or link to it. Rule 5.4: SQLite
    is the durable carrier; disk is transient.
    """
    captured = store.list_trial_artifacts(trial.id)
    if not captured:
        # No artifacts in SQLite — the trial may predate capture, or
        # the executor produced no files. Either way, nothing to serve.
        return """
        <div class="section">
            <h2>Artifacts</h2>
            <p class="muted">No artifacts captured for this trial.
            If this trial predates artifact capture, call
            <code>capture_pending_artifacts</code> to migrate.</p>
        </div>"""

    pid = trial.programme_id
    tid = trial.id
    artifact_url_base = f"/programme/{escape(pid)}/trial/{escape(tid)}/artifact"

    file_rows = []
    for art in captured:
        size = art.get("size_bytes", 0)
        size_str = f"{size} bytes" if size < 1024 else f"{size / 1024:.1f} KB"
        comp = art.get("compressed_size_bytes", 0)
        ratio = f"{comp / size * 100:.0f}%" if size > 0 else "—"
        file_rows.append(f"""
        <tr>
            <td class="artifact-file"><a href="{artifact_url_base}/{escape(art['filename'])}">{escape(art['filename'])}</a></td>
            <td>{escape(art.get('artifact_type', '—'))}</td>
            <td>{size_str}</td>
            <td class="muted">{ratio}</td>
        </tr>""")

    return f"""
    <div class="section">
        <h2>Artifacts ({len(captured)})</h2>
        <p class="muted" style="margin-bottom: 0.5rem">
            Stored in SQLite (gzip-compressed, content-addressed).
        </p>
        <table>
            <thead><tr><th>Filename</th><th>Type</th><th>Size</th><th>Compressed</th></tr></thead>
            <tbody>{''.join(file_rows)}</tbody>
        </table>
    </div>"""


def render_trial_detail(
    store: StateStore, programme_id: str, trial_id: str
) -> HTMLResponse:
    """Render the trial detail page."""
    trial = store.get_trial(trial_id)
    if trial is None:
        return HTMLResponse(
            render_base("Not Found", f'<h1 style="color: var(--status-failed)">Trial not found: {escape(trial_id)}</h1>'),
            status_code=404,
        )

    # Get observations for this trial
    observations = store.list_observations(trial_id)

    # Get bundle if linked
    bundle = None
    if trial.bundle_id:
        bundle = store.get_bundle(trial.bundle_id)

    # Config section
    config = json.loads(trial.config_json) if trial.config_json else {}

    # Observations section with sparklines
    obs_rows = []
    metric_values: dict[str, list[float]] = {}
    for obs in observations:
        metrics = json.loads(obs.metrics_json) if obs.metrics_json else {}
        variance = json.loads(obs.variance_json) if obs.variance_json else {}

        for k, v in metrics.items():
            metric_values.setdefault(k, []).append(v)

        metrics_str = ", ".join(f"{k}={v:.4f}" for k, v in metrics.items())
        variance_str = ", ".join(f"{k}={v:.4f}" for k, v in variance.items())

        obs_rows.append(f"""
        <tr>
            <td>{escape(obs.id)}</td>
            <td>{escape(metrics_str)}</td>
            <td>{escape(variance_str)}</td>
            <td>{escape(obs.spatiotemporal_region or '—')}</td>
            <td>{format_timestamp(obs.created_at)}</td>
        </tr>""")

    # Sparklines for each metric
    sparkline_html = ""
    if metric_values:
        sparkline_items = []
        for metric_name, values in metric_values.items():
            sparkline_items.append(f"""
            <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.5rem">
                <span class="muted" style="width: 120px">{escape(metric_name)}</span>
                {sparkline(values)}
                <span class="muted" style="font-size: 0.8rem">
                    {min(values):.4f} → {max(values):.4f}
                </span>
            </div>""")
        sparkline_html = f"""
        <div class="section">
            <h2>Metric Trends</h2>
            {''.join(sparkline_items)}
        </div>"""

    # Bundle section
    bundle_html = ""
    if bundle:
        seeds = json.loads(bundle.seeds_json) if bundle.seeds_json else []
        splits = json.loads(bundle.splits_json) if bundle.splits_json else {}
        data_refs_count = 0
        if hasattr(bundle, "data_refs_json") and bundle.data_refs_json:
            try:
                dr = json.loads(bundle.data_refs_json) if isinstance(bundle.data_refs_json, str) else bundle.data_refs_json
                data_refs_count = len(dr) if dr else 0
            except Exception:
                pass
        data_refs_badge = f' <span class="badge">{data_refs_count} data refs</span>' if data_refs_count else ""
        # Parse extra code hashes for display
        extra_hashes = []
        if getattr(bundle, "code_hash_extra_json", None):
            try:
                extra_hashes = json.loads(bundle.code_hash_extra_json)
            except Exception:
                extra_hashes = []
        extra_hash_row = ""
        if extra_hashes:
            extra_links = []
            for eh in extra_hashes:
                extra_links.append(
                    f'<a href="#code-{escape(eh[:12])}"><span class="hash-prefix">{escape(eh)}</span></a>'
                )
            extra_hash_row = f'<tr><th>extra_code_hashes</th><td>{", ".join(extra_links)}</td></tr>'
        bundle_html = f"""
        <div class="section">
            <h2>Bundle{data_refs_badge}</h2>
            <table>
                <tr><th>code_hash</th><td><a href="#code-{escape((bundle.code_hash or '')[:12])}"><span class="hash-prefix">{escape(bundle.code_hash or '—')}</span></a></td></tr>
                {extra_hash_row}
                <tr><th>code_ref</th><td><span class="muted" title="Provenance: original carrier path at capture time">{escape(bundle.code_ref)}</span></td></tr>
                <tr><th>env_ref</th><td>{escape(bundle.env_ref)}</td></tr>
                <tr><th>seeds</th><td>{escape(seeds)}</td></tr>
                <tr><th>splits</th><td>{render_json_pretty(splits)}</td></tr>
                <tr><th>baseline_ref</th><td>{escape(bundle.baseline_ref or '—')}</td></tr>
            </table>
        </div>"""

    # C2: Data provenance section
    data_refs_html = _render_data_refs_section(store, bundle)

    # C2 + M2: Artifacts section
    artifacts_html = _render_artifacts_section(store, trial)

    # Executed code — the strace-observed set of .py files the run
    # actually opened (executed_code.json artifact). This is the
    # authoritative "what ran"; the bundle closure below is the sealed
    # pre-run superset (it must contain every branch the config could
    # have selected, so it is expected to be larger).
    executed_html = ""
    executed_hashes: set[str] = set()
    exec_art = next(
        (a for a in store.list_trial_artifacts(trial.id)
         if a["filename"] == "executed_code.json"),
        None,
    )
    if exec_art is not None:
        try:
            f = store.get_artifact_file(exec_art["content_hash"])
            manifest = json.loads(f["content"]) if f else {}
            files = manifest.get("files", [])
            bundle_set = set()
            if bundle:
                bundle_set.add(bundle.code_hash or "")
                bundle_set.update(extra_hashes)
            rows = []
            for c in files:
                ch = c.get("code_hash", "")
                executed_hashes.add(ch)
                in_bundle = "" if ch in bundle_set else (
                    ' <span class="badge" style="background: var(--status-failed); '
                    'color: #fff">not in sealed bundle</span>'
                )
                rows.append(f"""
                <tr>
                    <td>{escape(c.get("original_path", "—"))}{in_bundle}</td>
                    <td><a href="#code-{escape(ch[:12])}"><span class="hash-prefix">{escape(ch or '—')}</span></a></td>
                    <td class="muted">{c.get("size_bytes", 0)} bytes</td>
                </tr>""")
            # Executed files absent from the sealed bundle have no
            # #code- anchor — render them here so the divergence is
            # inspectable (e.g. dynamically-imported deps the static
            # capture could not see).
            missing_cards = []
            for c in files:
                ch = c.get("code_hash", "")
                if ch and ch not in bundle_set:
                    cs = store.get_code_snippet(ch)
                    if cs is not None:
                        missing_cards.append(f"""
                        <details id="code-{escape(ch[:12])}" style="margin-top: 0.5rem">
                            <summary>{escape(cs.original_path or ch[:30])}
                                <span class="muted"> — {cs.size_bytes} bytes</span>
                            </summary>
                            <pre style="margin-top: 0.5rem; max-height: 400px; overflow: auto">{escape(cs.code_text)}</pre>
                        </details>""")
            executed_html = f"""
            <div class="section">
                <h2>Executed Code ({len(files)})</h2>
                <p class="muted" style="margin-bottom: 0.5rem">
                    Files the trial's process tree actually opened
                    (strace read-trace) — the observed truth of what ran.
                    Files badged <em>not in sealed bundle</em> escaped
                    static capture — the archive is not self-contained
                    for them.
                </p>
                <table>
                    <thead><tr><th>Path</th><th>Content Hash</th><th>Size</th></tr></thead>
                    <tbody>{''.join(rows)}</tbody>
                </table>
                {''.join(missing_cards)}
            </div>"""
        except Exception:
            executed_html = ""

    # Bundle closure — every captured snippet (primary + spawned/
    # imported extras). Rendered collapsed: it is a superset by design
    # (sealed before the config's branches were taken), so wall-of-code
    # is noise; executed files are badged to show the diff.
    code_html = ""
    if bundle:
        all_hashes = ([bundle.code_hash] if bundle.code_hash else []) + extra_hashes
        cards = []
        for h in all_hashes:
            cs = store.get_code_snippet(h)
            if cs is None:
                continue
            path_label = cs.original_path or f"{h[:30]}..."
            ran = (
                ' <span class="badge">executed</span>'
                if h in executed_hashes else ""
            )
            cards.append(f"""
            <details id="code-{escape(h[:12])}" style="margin-top: 0.5rem">
                <summary>{escape(path_label)}{ran}
                    <span class="muted"> — {escape(h[:24])}… | {cs.size_bytes} bytes</span>
                </summary>
                <pre style="margin-top: 0.5rem; max-height: 400px; overflow: auto">{escape(cs.code_text)}</pre>
            </details>""")
        if cards:
            n_exec = sum(1 for h in all_hashes if h in executed_hashes)
            diff = (
                f" — {n_exec} executed" if executed_hashes
                else " — sealed before run"
            )
            code_html = f"""
            <div class="section">
                <details>
                    <summary><h2 style="display:inline">Bundle Closure ({len(cards)} files){escape(diff)}</h2></summary>
                    <p class="muted" style="margin-top: 0.5rem">
                        The sealed bundle is a superset by design: it must
                        contain every branch the config could have
                        selected. Compare with Executed Code above for
                        what this trial actually ran.
                    </p>
                    {''.join(cards)}
                </details>
            </div>"""

    body = f"""
    <h1>Trial {escape(trial_id)}</h1>
    <div class="muted" style="margin-bottom: 1rem">
        programme: <a href="/programme/{escape(programme_id)}">{escape(programme_id)}</a>
    </div>

    <div class="section">
        <h2>Status</h2>
        {render_status_badge(trial.status.value)}
    </div>

    <div class="section">
        <h2>Configuration</h2>
        {render_json_pretty(config)}
    </div>

    {bundle_html}

    {data_refs_html}

    {artifacts_html}

    {executed_html}

    {code_html}

    {sparkline_html}

    <div class="section">
        <h2>Observations</h2>
        <table>
            <thead><tr><th>ID</th><th>Metrics</th><th>Variance</th><th>Region</th><th>Created</th></tr></thead>
            <tbody>
                {''.join(obs_rows) if obs_rows else '<tr><td colspan="5" class="muted">No observations yet.</td></tr>'}
            </tbody>
        </table>
    </div>
    """
    return HTMLResponse(render_base(f"Trial {trial_id}", body))
