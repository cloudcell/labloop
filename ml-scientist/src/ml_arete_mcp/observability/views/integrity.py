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
        '<span class="status status-open">ok</span>'
        if status == "ok"
        else f'<span class="status status-dropped">{status}</span>'
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
        '<span class="status status-open">ok</span>'
        if status == "ok"
        else f'<span class="status status-dropped">{escape(status)}</span>'
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
        "Who is connected in what role — the launch-order contract "
        "made auditable. Each row is a configured channel: "
        "<code>loop0</code> (read-only evidence into ml-episteme), "
        "<code>loop1</code> (read-only evidence into ml-zetesis), and "
        "<code>claims</code> (anamnesis). A channel <em>configured "
        "but down</em> is a violation carrying <code>channel</code>, "
        "<code>role</code>, <code>target</code>, "
        "<code>last_error</code> — the connectivity supervisor keeps "
        "retrying it every <code>[adaptors] reconnect_seconds</code>, "
        "so it clears itself when the peer appears. A channel "
        "<em>not configured</em> is absent from the report entirely — "
        "supported standalone mode, never flagged.",
    ),
    (
        "lineage_integrity",
        "Dangling parents and cycles in "
        "<code>improver_versions</code> — every candidate's "
        "<code>parent_id</code> must resolve, and the ancestry graph "
        "must be acyclic.",
    ),
    (
        "single_champion",
        "At most one <code>is_champion</code> row — the champion "
        "pointer is unique by definition; two champions means a "
        "promotion raced or was hand-edited.",
    ),
    (
        "stale_open_tournaments",
        "An open tournament with no result or evidence activity in "
        "<code>stale_tournament_seconds</code> — a comparison that "
        "was started and never decided.",
    ),
    (
        "rejected_have_reasons",
        "A rejected proposal must carry "
        "<code>rejection_reason</code> — 'examined and rejected' is "
        "part of the research trail only if it says why.",
    ),
    (
        "closed_have_gain",
        "A closed tournament must carry "
        "<code>recursive_gain</code> — closing without recording the "
        "measured gain makes the tournament's verdict "
        "unverifiable.",
    ),
    (
        "conditional_human_gate",
        "An active policy under a <em>conditional</em> proposal "
        "must trace to a <code>human:</code>-signed promote "
        "decision — conditional-class changes require human signoff "
        "by the gate's definition.",
    ),
    (
        "decisions_reference_candidates",
        "Every meta-decision must reference real candidates — no "
        "dangling <code>improver_id</code>s in decision records.",
    ),
    (
        "minted_claims_resolve",
        "Every <code>meta_decisions.claim_id</code> is fetched "
        "through the claims channel — a minted id that fails to "
        "resolve means the claim is gone or the channel is lying. "
        "<code>skipped</code> when no claims channel is wired.",
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
        <tr><td><code>skipped</code></td><td>The check's dependency is
        absent (e.g. no claims channel) — reported honestly as
        <code>skipped — reason</code>, never silently
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
        '<span class="status status-open">ok</span>'
        if status == "ok"
        else f'<span class="status status-dropped">{escape(status)}</span>'
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
