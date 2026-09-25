"""Integrity view — renders the check-run audit log.

Read-only: this view never runs checks and never writes. It renders
what check_invariants / /health/deep persisted under
<db_dir>/logs/check-<YYYY-MM-DD>.jsonl — one line per run, a new file
at each UTC midnight.
"""

from __future__ import annotations

from html import escape

from ...integrity.checks import log_dir_for
from ...integrity.log import (
    list_check_logs,
    read_check_log,
    read_run,
)
from ..templates import render_base


def _pretty(data):
    import json as _json
    return '<pre>' + escape(_json.dumps(data, indent=2, default=str)) + '</pre>'


def _check_row(check: dict) -> str:
    status = "ok" if check.get("ok") else "violations"
    if str(check.get("detail", "")).startswith("skipped"):
        status = "skipped"
    badge = (
        '<span class="status status-live">ok</span>'
        if status == "ok"
        else f'<span class="status status-expired">{status}</span>'
    )
    n = len(check.get("violations", []))
    return (
        f"<tr><td>{escape(check.get('name', ''))}</td>"
        f"<td>{badge}</td><td>{n}</td>"
        f"<td class='muted'>{escape(str(check.get('detail', '')))}</td></tr>"
    )


def _run_block(run: dict) -> str:
    checks = run.get("checks", [])
    status = run.get("status", "unknown")
    badge = (
        '<span class="status status-live">ok</span>'
        if status == "ok"
        else f'<span class="status status-expired">{escape(status)}</span>'
    )
    rows = "".join(_check_row(c) for c in checks)
    return f"""
    <h3>{escape(str(run.get('checked_at', '')))} {badge}
    <span class="muted">{run.get('duration_ms', '?')}ms</span></h3>
    <table>
        <thead><tr><th>Check</th><th>Status</th><th>Violations</th>
        <th>Detail</th></tr></thead>
        <tbody>{rows}</tbody>
    </table>"""


_CHECK_HELP: list[tuple[str, str]] = [
    (
        "upstream_connectivity",
        "Always <code>skipped</code> on this server — anamnesis is the "
        "<em>semantic-memory endpoint</em>: every other server "
        "consumes its claims channel, but it consumes nothing itself. "
        "The check is reported (rather than omitted) so the check "
        "list is uniform across the four servers and the skip's "
        "reason is on the record.",
    ),
    (
        "unsupported_high_confidence",
        "Live claims above <code>[integrity] prior_confidence_max</code> "
        "with no evidence-bearing edge — the write-time evidence rule "
        "audited, not just enforced. The audit reads the same "
        "configured ceiling as the <code>assert_claim</code> gate, so "
        "a violation means a high-confidence claim exists with "
        "nothing supporting it (bypassed write path or deleted "
        "support).",
    ),
    (
        "dangling_claim_refs",
        "<code>claim_edges</code> with <code>ref_type='claim'</code> "
        "pointing at a nonexistent claim — these refs are verified "
        "when the edge is written, so a dangling one means "
        "corruption or a deleted target.",
    ),
    (
        "broken_supersession",
        "Two supersession defects: a <code>supersedes_id</code> "
        "pointing at a nonexistent claim (<em>target missing</em>), "
        "and supersedes chains that <em>cycle</em> — a chain must "
        "terminate to be a lineage; a cycle means the belief "
        "history is malformed.",
    ),
]


def render_integrity_help() -> str:
    """What each check means — the operator's reading of the audit."""
    check_rows = "".join(
        f"<tr><td><code>{escape(name)}</code></td><td>{text}</td></tr>"
        for name, text in _CHECK_HELP
    )
    body = f"""
    <h1>Integrity — what the checks mean</h1>

    <p>The integrity layer is the <strong>detection complement to
    write-time enforcement</strong>: invariants that can't be enforced
    at the write boundary (hand-edits, races, crashes, historical
    debt) are audited after the fact. Every run is
    <strong>report-only</strong> — nothing is repaired or mutated;
    violations carry entity ids so the proper tool can correct them.</p>

    <h2>Verdicts</h2>
    <table>
      <thead><tr><th>Verdict</th><th>Meaning</th></tr></thead>
      <tbody>
        <tr><td><code>ok</code></td><td>The check ran and found no
        violations.</td></tr>
        <tr><td><code>violations</code></td><td>The check ran and
        flagged rows — the violation entries carry the entity ids and
        signals. The run's overall <code>status</code> is
        <code>violations</code> if any check is.</td></tr>
        <tr><td><code>skipped</code></td><td>The check's precondition
        doesn't apply — e.g. anamnesis consumes no upstream channels,
        so <code>upstream_connectivity</code> reports
        <code>skipped — reason</code> rather than a silent
        <code>ok</code>.</td></tr>
      </tbody>
    </table>

    <h2>Triggers</h2>
    <p>The <code>trigger</code> field records what invoked a run:
    <code>startup</code> (the monitor's boot sweep — proves logging
    began), <code>interval</code> (periodic sweep every
    <code>[integrity] check_interval_seconds</code>, default 300;
    <code>0</code> disables the periodic part), <code>tool</code>
    (<code>check_invariants</code> MCP call), <code>route</code>
    (<code>/health/deep</code> request).</p>

    <h2>Where the trail lives</h2>
    <p>Every run appends one JSON line to
    <code>&lt;db_dir&gt;/logs/check-&lt;YYYY-MM-DD&gt;.jsonl</code> —
    a new file at each UTC midnight, retained to
    <code>[integrity] log_max_files</code>. This page renders those
    logs <strong>read-only</strong>: it never runs checks itself.
    The monitor audits store state, not process liveness — a hung
    server's log simply <em>stops</em>, which an external supervisor
    notices as silence.</p>

    <h2>The checks</h2>
    <table>
      <thead><tr><th>Check</th><th>What it audits / what a violation
      means</th></tr></thead>
      <tbody>{check_rows}</tbody>
    </table>

    <p><a href="/integrity">← Integrity</a></p>"""
    return render_base("Integrity help", body)


def render_integrity(store) -> str:
    """Latest check run + history of past runs."""
    log_dir = log_dir_for(store)
    runs = list_check_logs(log_dir)

    if not runs:
        body = (
            "<h1>Integrity</h1>"
            "<p class='muted'>No integrity checks have been logged yet. "
            "Run the <code>check_invariants</code> MCP tool or hit "
            "<code>/health/deep</code> on the MCP server — every run is "
            "recorded under <code>"
            f"{escape(str(log_dir))}</code> (one file per UTC day). "
            '<a href="/integrity/help">what do these checks mean?</a></p>'
        )
        return render_base("Integrity", body)

    latest = read_run(log_dir, runs[0]["file"], runs[0]["index"]) or {}
    checks = latest.get("checks", [])
    status = latest.get("status", "unknown")
    status_badge = (
        '<span class="status status-live">ok</span>'
        if status == "ok"
        else f'<span class="status status-expired">{escape(status)}</span>'
    )

    rows = "".join(_check_row(c) for c in checks)
    history_rows = "".join(
        f"<tr><td><a href='/integrity/run/{escape(r['file'])}/{r['index']}'>"
        f"{escape(str(r.get('checked_at', r['file'])))}</a></td>"
        f"<td>{escape(str(r.get('status', '')))}</td>"
        f"<td class='muted'>{escape(str(r.get('trigger', '') or '—'))}</td>"
        f"<td>{r.get('violations', '')}</td>"
        f"<td class='muted'>{escape(r['file'])}</td></tr>"
        for r in runs
    )

    body = f"""
    <h1>Integrity {status_badge}</h1>
    <p class="muted">Latest run {escape(str(latest.get('checked_at', '')))}
    — {latest.get('duration_ms', '?')}ms.
    Source: <code>{escape(str(log_dir))}</code> (one file per UTC day).
    <a href="/integrity/help">what do these checks mean?</a></p>
    <table>
        <thead><tr><th>Check</th><th>Status</th><th>Violations</th>
        <th>Detail</th></tr></thead>
        <tbody>{rows}</tbody>
    </table>
    <h2>Run history</h2>
    <table>
        <thead><tr><th>Run</th><th>Status</th><th>Trigger</th>
        <th>Violations</th><th>Log file</th></tr></thead>
        <tbody>{history_rows}</tbody>
    </table>"""
    return render_base("Integrity", body)


def render_integrity_log(store, name: str) -> str:
    """One day file — every recorded run in it."""
    runs = read_check_log(log_dir_for(store), name)
    if runs is None:
        return render_base(
            "Integrity log",
            f"<h1>Log not found: {escape(name)}</h1>",
        )
    blocks = "".join(_run_block(r) for r in runs)
    body = f"""
    <h1>{escape(name)}</h1>
    <p class="muted">{len(runs)} run(s) recorded in this file.</p>
    {blocks}
    <p><a href="/integrity">← Integrity</a></p>"""
    return render_base("Integrity log", body)


def render_integrity_run(store, name: str, index: int) -> str:
    """One recorded run — checks plus its violations detail."""
    run = read_run(log_dir_for(store), name, index)
    if run is None:
        return render_base(
            "Integrity run",
            f"<h1>Run not found: {escape(name)} #{index}</h1>",
        )
    checks = run.get("checks", [])
    rows = "".join(_check_row(c) for c in checks)
    body = f"""
    <h1>Check run {escape(str(run.get('checked_at', name)))}</h1>
    <table>
        <thead><tr><th>Check</th><th>Status</th><th>Violations</th>
        <th>Detail</th></tr></thead>
        <tbody>{rows}</tbody>
    </table>
    <h2>Violations</h2>
    {_pretty(
        {c['name']: c['violations'] for c in checks if c.get('violations')}
        or "none"
    )}
    <p><a href="/integrity">← Integrity</a> ·
    <a href="/integrity/log/{escape(name)}">day file</a></p>"""
    return render_base("Integrity run", body)
