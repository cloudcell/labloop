"""DataRef detail view — data provenance, hash, reproducibility risk."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from starlette.responses import HTMLResponse

from ...state.store import StateStore
from ..templates import (
    escape,
    format_timestamp,
    render_base,
    render_json_pretty,
    risk_class,
)


def render_dataref_detail(store: StateStore, data_ref_id: str) -> HTMLResponse:
    """M1: Render the DataRef detail page."""
    ref = store.get_data_ref(data_ref_id)
    if ref is None:
        return HTMLResponse(
            render_base("Not Found", f'<h1 style="color: var(--status-failed)">DataRef not found: {escape(data_ref_id)}</h1>'),
            status_code=404,
        )

    risk_cls = risk_class(ref.reproducibility_risk)
    risk_label = ref.reproducibility_risk or "—"

    # Check if the data file exists and is read-only
    storage_info = ""
    if ref.storage_uri:
        path_str = ref.storage_uri
        if path_str.startswith("file://"):
            local_path = Path(path_str[7:])
            try:
                if local_path.exists():
                    stat = local_path.stat()
                    is_ro = not (stat.st_mode & 0o200)
                    size_str = f"{stat.st_size} bytes" if stat.st_size < 1024 else f"{stat.st_size / 1024:.1f} KB"
                    ro_label = "read-only ✓" if is_ro else "WRITABLE ✗"
                    ro_color = "var(--status-active)" if is_ro else "var(--status-failed)"
                    storage_info = f"""
                    <table>
                        <tr><th>storage path</th><td><code>{escape(str(local_path))}</code></td></tr>
                        <tr><th>file size</th><td>{size_str}</td></tr>
                        <tr><th>access</th><td style="color: {ro_color}; font-weight: bold">{ro_label}</td></tr>
                    </table>"""
                else:
                    storage_info = f'<p class="muted">File not found at: <code>{escape(str(local_path))}</code></p>'
            except Exception as e:
                storage_info = f'<p class="muted">Could not stat file: {escape(str(e))}</p>'
        else:
            storage_info = f'<p class="muted">Remote URI: <code>{escape(ref.storage_uri)}</code></p>'

    # Generator details (for generated data)
    generator_html = ""
    if ref.regime == "generated":
        gen_params = ref.generator_params if ref.generator_params else {}
        generator_html = f"""
        <div class="section">
            <h2>Generator</h2>
            <table>
                <tr><th>code_ref</th><td><code>{escape(ref.generator_code_ref or '—')}</code></td></tr>
                <tr><th>seed</th><td>{escape(ref.generator_seed) if ref.generator_seed is not None else '—'}</td></tr>
                <tr><th>params</th><td>{render_json_pretty(gen_params)}</td></tr>
            </table>
        </div>"""

    # Capture details (for captured data)
    capture_html = ""
    if ref.regime == "captured":
        capture_meta = ref.capture_source_metadata if ref.capture_source_metadata else {}
        capture_html = f"""
        <div class="section">
            <h2>Capture Source</h2>
            <table>
                <tr><th>source_uri</th><td><code>{escape(ref.source_uri or '—')}</code></td></tr>
                <tr><th>version</th><td>{escape(ref.version or '—')}</td></tr>
                <tr><th>capture_window</th><td>{escape(ref.capture_window_start or '—')} → {escape(ref.capture_window_end or '—')}</td></tr>
                <tr><th>source_metadata</th><td>{render_json_pretty(capture_meta)}</td></tr>
            </table>
        </div>"""

    body = f"""
    <h1>DataRef {escape(data_ref_id)}</h1>

    <div class="section">
        <h2>Overview</h2>
        <table>
            <tr><th>ID</th><td class="data-ref-link">{escape(ref.id)}</td></tr>
            <tr><th>split</th><td>{escape(ref.split)}</td></tr>
            <tr><th>regime</th><td>{escape(ref.regime)}</td></tr>
            <tr><th>content_hash</th><td class="hash-prefix">{escape(ref.content_hash or '—')}</td></tr>
            <tr><th>schema_hash</th><td class="hash-prefix">{escape(ref.schema_hash or '—')}</td></tr>
            <tr><th>size</th><td>{escape(f"{ref.size_bytes} bytes" if ref.size_bytes is not None else "—")}</td></tr>
            <tr><th>num_samples</th><td>{escape(ref.num_samples if ref.num_samples is not None else "—")}</td></tr>
            <tr><th>reproducibility_risk</th><td><span class="{risk_cls}" style="font-weight: bold">{escape(risk_label)}</span></td></tr>
            <tr><th>created</th><td>{format_timestamp(ref.created_at)}</td></tr>
        </table>
    </div>

    <div class="section">
        <h2>Storage</h2>
        {storage_info or '<p class="muted">No storage URI recorded.</p>'}
    </div>

    {generator_html}

    {capture_html}
    """
    return HTMLResponse(render_base(f"DataRef {data_ref_id}", body))
