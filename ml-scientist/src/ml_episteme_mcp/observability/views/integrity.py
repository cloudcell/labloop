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
from ..templates import render_base, render_json_pretty


def _check_row(check: dict) -> str:
    status = "ok" if check.get("ok") else "violations"
    if str(check.get("detail", "")).startswith("skipped"):
        status = "skipped"
    badge = (
        '<span class="status status-completed">ok</span>'
        if status == "ok"
        else f'<span class="status status-failed">{status}</span>'
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
        '<span class="status status-completed">ok</span>'
        if status == "ok"
        else f'<span class="status status-failed">{escape(status)}</span>'
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
        "<code>optimizer</code>, <code>executor</code>, "
        "<code>data_source</code> (local implementations fill these by "
        "default; <code>target</code> shows <code>local</code> or the "
        "MCP endpoint), and <code>claims</code> (the anamnesis "
        "channel). A channel <em>configured but down</em> is a "
        "violation carrying <code>channel</code>, <code>role</code>, "
        "<code>target</code>, <code>last_error</code> — the "
        "connectivity supervisor keeps retrying it every "
        "<code>[adaptors] reconnect_seconds</code>, so it clears "
        "itself when the peer appears. A channel <em>not "
        "configured</em> is absent from the report entirely — "
        "supported standalone mode, never flagged.",
    ),
    (
        "orphaned_running_trials",
        "A trial whose status is <code>running</code> but whose "
        "executor task no longer exists — typically a server restart "
        "mid-trial. The status is a lie: nothing is executing. "
        "Skipped when no executor is wired.",
    ),
    (
        "completed_without_observation",
        "A <code>completed</code> trial past "
        "<code>observation_grace_seconds</code> (default 86400s — "
        "observations can legitimately lag execution by a whole "
        "session) with no observation recorded. Inside the grace "
        "window the completed→observe gap is normal loop order; past "
        "it, the completion is debt. "
        "<code>conclude_hypothesis</code> hard-blocks on this class.",
    ),
    (
        "unsealed_execution",
        "A trial that ran with <code>seal_enforced=false</code> under "
        "an enforcing sandbox mode — the code that ran was not "
        "content-sealed, so 'what executed' is not pinned to the "
        "recorded bundle.",
    ),
    (
        "strace_divergence",
        "A <code>.py</code> file the trial actually opened (per "
        "strace) whose hash is absent from the sealed bundle — the "
        "trial ran code outside what was captured. The provenance "
        "claim 'this bundle is what ran' is broken.",
    ),
    (
        "budget_exceeded",
        "An active programme past a declared budget "
        "(<code>wall_time_hours</code>, <code>max_trials</code>). A "
        "wall budget of <code>0</code> means undeclared — unbounded, "
        "not exceeded. Archived programmes are not audited.",
    ),
    (
        "stuck_hypotheses",
        "A hypothesis in <code>under_test</code> with zero trials — "
        "declared but never exercised. Visible debt, not an error: "
        "the loop stalled between formulate and run.",
    ),
    (
        "mislabeled_outcome",
        "A <code>completed</code> trial with no executor record AND "
        "no observation — nothing evidences the run at all — or "
        "whose executor record documents a failure (inner "
        "<code>status: \"error\"</code>, nonzero inner exit code). "
        "Correct via <code>correct_trial_status → failed</code>; the "
        "correction is appended to the record, not rewritten. A "
        "completed trial <em>with</em> an observation but no executor "
        "record is corroborated, not mislabeled — reported in "
        "<code>detail</code> as a bounded historical provenance gap.",
    ),
    (
        "stalled_running_trials",
        "A <code>running</code> trial past its executor-enforced "
        "deadline (<code>[executor] timeout_seconds</code>) plus the "
        "<code>stalled_trial_seconds</code> margin — the kill/finalize "
        "path wedged — or whose executor task finished without "
        "finalizing. Deadline-relative: a 4-hour trial inside its "
        "deadline never flags. Skipped when no executor is wired.",
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
        absent (e.g. no executor wired, no claims channel) — reported
        honestly as <code>skipped — reason</code>, never silently
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


def render_integrity(store, refresh_interval: int = 0) -> str:
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
        return render_base("Integrity", body, refresh_interval)

    latest = read_run(log_dir, runs[0]["file"], runs[0]["index"]) or {}
    checks = latest.get("checks", [])
    status = latest.get("status", "unknown")
    status_badge = (
        '<span class="status status-completed">ok</span>'
        if status == "ok"
        else f'<span class="status status-failed">{escape(status)}</span>'
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
    return render_base("Integrity", body, refresh_interval)


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
    {render_json_pretty(
        {c['name']: c['violations'] for c in checks if c.get('violations')}
        or "none"
    )}
    <p><a href="/integrity">← Integrity</a> ·
    <a href="/integrity/log/{escape(name)}">day file</a></p>"""
    return render_base("Integrity run", body)
