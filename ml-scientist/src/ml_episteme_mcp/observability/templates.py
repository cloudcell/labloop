"""HTML templates for the observability GUI.

Uses Python string templates — no Jinja2 dependency. Templates are
functions that return HTML strings.
"""

from __future__ import annotations

import html as html_module
import json
from typing import Any


def escape(text: Any) -> str:
    """HTML-escape a value."""
    return html_module.escape(str(text))


# Nav-widget poll cadences — operator-tunable via [observability] in
# ml-episteme.toml; process-wide, applied at app construction.
HEALTH_POLL_SECONDS = 10
INTEGRITY_POLL_SECONDS = 30
# Lab-home link — resolved at app construction, never guessed: the
# [observability] agora_gui_url TOML key wins, else the launcher-
# exported ML_AGORA_GUI_URL (derived from ports.env), else hidden.
AGORA_GUI_URL: str | None = None
# Downstream peer GUIs for cross-GUI reference links (candidate →
# zetesis, claim → anamnesis). Same provenance as AGORA_GUI_URL:
# [observability] <peer>_gui_url wins, else the launcher-exported
# ML_<PEER>_GUI_URL. Missing entries mean "render the id, don't
# guess an address."
PEER_GUI_URLS: dict[str, str] = {}


def configure_polling(
    health_poll_seconds: int | None = None,
    integrity_poll_seconds: int | None = None,
    agora_url: str | None = None,
    peer_gui_urls: dict[str, str] | None = None,
) -> None:
    """Apply configured nav settings ([observability] table)."""
    global HEALTH_POLL_SECONDS, INTEGRITY_POLL_SECONDS, AGORA_GUI_URL
    if health_poll_seconds is not None:
        HEALTH_POLL_SECONDS = int(health_poll_seconds)
    if integrity_poll_seconds is not None:
        INTEGRITY_POLL_SECONDS = int(integrity_poll_seconds)
    if agora_url is not None:
        AGORA_GUI_URL = agora_url
    if peer_gui_urls:
        PEER_GUI_URLS.update(peer_gui_urls)


def _home_link() -> str:
    """Nav home icon → the agora GUI (the lab's read-only hub)."""
    if not AGORA_GUI_URL:
        return ""
    url = escape(AGORA_GUI_URL)
    return (
        f'<a class="home-link" href="{url}" title="Agora — lab home">'
        '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
        'stroke-linejoin="round"><path d="M3 10.5 12 3l9 7.5"/>'
        '<path d="M5 9.5V21h14V9.5"/><path d="M10 21v-6h4v6"/></svg></a>'
    )


def render_base(title: str, body: str, refresh_interval: int = 0) -> str:
    """Render the base HTML page with nav, head, and HTMX script.

    Args:
        title: Page title.
        body: HTML body content.
        refresh_interval: HTMX polling interval in seconds (0 = disabled).
    """
    htmx_script = "https://unpkg.com/htmx.org@2.0.4"
    # Official Lab Loop favicon (from loop.cloudcell.workers.dev)
    favicon = (
        "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
        "width='32' height='32' viewBox='0 0 32 32' fill='none'%3E%3Crect "
        "width='32' height='32' rx='7' fill='%23172033'/%3E%3Ccircle "
        "cx='16' cy='16' r='8.5' stroke='%231f5f70' stroke-width='2.6' "
        "fill='none'/%3E%3Ccircle cx='16' cy='16' r='2.8' fill='%231f5f70'/%3E"
        "%3Cpath d='M16 7.5a8.5 8.5 0 0 1 7.3 12.8' stroke='%233a8fa3' "
        "stroke-width='2.6' fill='none' stroke-linecap='round'/%3E%3C/svg%3E"
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="icon" type="image/svg+xml" href="{favicon}">
    <title>{escape(title)} — ml-episteme</title>
    <script src="{htmx_script}"></script>
    <style>
        :root {{
            --status-active: #22c55e;
            --status-completed: #3b82f6;
            --status-failed: #ef4444;
            --status-abandoned: #6b7280;
            --status-accepted: #22c55e;
            --status-rejected: #ef4444;
            --status-inconclusive: #f59e0b;
            --status-retryable: #f97316;
            --bg: #0f172a;
            --bg-card: #1e293b;
            --bg-hover: #334155;
            --fg: #e2e8f0;
            --fg-muted: #94a3b8;
            --accent: #8b5cf6;
            --border: #334155;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
            background: var(--bg);
            color: var(--fg);
            padding: 1.5rem;
            max-width: 1200px;
            margin: 0 auto;
            line-height: 1.5;
        }}
        h1 {{ font-size: 1.5rem; margin-bottom: 1rem; color: var(--accent); }}
        h2 {{ font-size: 1.2rem; margin: 1.5rem 0 0.75rem; color: var(--fg); }}
        h3 {{ font-size: 1rem; margin: 0.5rem 0; }}
        a {{ color: var(--accent); text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        .nav {{ margin-bottom: 1.5rem; display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; }}
        .nav a {{ margin-right: 0.5rem; }}
        .home-link {{
            display: inline-flex; align-items: center; justify-content: center;
            width: 1.75rem; height: 1.75rem; border-radius: 0.25rem;
            border: 1px solid var(--border); background: var(--bg-card);
            color: var(--fg-muted);
        }}
        .home-link:hover {{ color: var(--accent); border-color: var(--accent); text-decoration: none; }}
        .nav .spacer {{ flex: 1; }}
        .grid {{ display: grid; gap: 1rem; }}
        .grid-2 {{ grid-template-columns: 1fr 1fr; }}
        .card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 0.5rem;
            padding: 1rem;
            cursor: pointer;
            transition: background 0.15s, border-color 0.15s;
            display: block;
            text-decoration: none;
            color: inherit;
        }}
        .card:hover {{ background: var(--bg-hover); text-decoration: none; }}
        .card h3 {{ color: var(--accent); margin-bottom: 0.5rem; }}
        .card-inactive {{ opacity: 0.6; }}
        .card-active {{ border-left: 3px solid var(--status-active); }}
        .card-archived {{ border-left: 3px solid var(--status-completed); opacity: 0.85; }}
        .status {{
            display: inline-block;
            padding: 0.15rem 0.5rem;
            border-radius: 0.25rem;
            font-size: 0.8rem;
            font-weight: bold;
        }}
        .status-active {{ background: rgba(34,197,94,0.2); color: var(--status-active); }}
        .status-completed {{ background: rgba(59,130,246,0.2); color: var(--status-completed); }}
        .status-failed {{ background: rgba(239,68,68,0.2); color: var(--status-failed); }}
        .status-abandoned {{ background: rgba(107,114,128,0.2); color: var(--status-abandoned); }}
        .status-archived {{ background: rgba(59,130,246,0.2); color: var(--status-completed); }}
        .status-accepted {{ background: rgba(34,197,94,0.2); color: var(--status-accepted); }}
        .status-rejected {{ background: rgba(239,68,68,0.2); color: var(--status-rejected); }}
        .status-inconclusive {{ background: rgba(245,158,11,0.2); color: var(--status-inconclusive); }}
        .status-proposed {{ background: rgba(139,92,246,0.2); color: var(--accent); }}
        .status-under_test {{ background: rgba(245,158,11,0.2); color: var(--status-inconclusive); }}
        .status-designed {{ background: rgba(139,92,246,0.2); color: var(--accent); }}
        .status-running {{ background: rgba(245,158,11,0.2); color: var(--status-inconclusive); }}
        .status-retryable {{ background: rgba(249,115,22,0.2); color: var(--status-retryable); }}
        .status-best {{ background: rgba(250,204,21,0.15); color: #facc15; border: 1px solid rgba(250,204,21,0.3); }}
        table {{ width: 100%; border-collapse: collapse; margin: 0.5rem 0; }}
        th, td {{ text-align: left; padding: 0.5rem; border-bottom: 1px solid var(--border); }}
        th {{ color: var(--fg-muted); font-size: 0.85rem; text-transform: uppercase; }}
        td {{ font-size: 0.9rem; }}
        tbody tr:nth-child(even) {{ background: rgba(255,255,255,0.02); }}
        tbody tr:hover {{ background: rgba(255,255,255,0.05); }}
        tr.row-best {{ background: rgba(250,204,21,0.05) !important; }}
        tr.row-best:hover {{ background: rgba(250,204,21,0.1) !important; }}
        .budget-bar {{
            background: var(--border);
            border-radius: 0.25rem;
            height: 0.5rem;
            margin-top: 0.5rem;
            overflow: hidden;
        }}
        .budget-fill {{
            background: var(--accent);
            height: 100%;
            border-radius: 0.25rem;
        }}
        .direction {{ font-size: 0.8rem; color: var(--fg-muted); }}
        .muted {{ color: var(--fg-muted); }}
        .mono {{ font-family: ui-monospace, 'SF Mono', 'Cascadia Code', 'Fira Code', monospace; }}
        pre {{
            background: var(--bg);
            border: 1px solid var(--border);
            border-radius: 0.25rem;
            padding: 0.75rem;
            overflow-x: auto;
            font-size: 0.85rem;
            font-family: ui-monospace, 'SF Mono', 'Cascadia Code', 'Fira Code', monospace;
        }}
        code {{
            font-family: ui-monospace, 'SF Mono', 'Cascadia Code', 'Fira Code', monospace;
            font-size: 0.85rem;
            background: rgba(255,255,255,0.05);
            padding: 0.1rem 0.3rem;
            border-radius: 0.2rem;
        }}
        .sparkline {{ vertical-align: middle; }}
        .section {{ margin-bottom: 1.5rem; }}
        .badge {{
            display: inline-block;
            padding: 0.1rem 0.4rem;
            border-radius: 0.25rem;
            font-size: 0.75rem;
            background: var(--border);
            color: var(--fg-muted);
            margin-left: 0.5rem;
        }}
        .filter-tabs {{ display: flex; gap: 0.5rem; margin-bottom: 1rem; flex-wrap: wrap; }}
        .filter-tab {{
            padding: 0.3rem 0.8rem;
            border-radius: 0.25rem;
            background: var(--bg-card);
            border: 1px solid var(--border);
            color: var(--fg-muted);
            cursor: pointer;
            font-size: 0.85rem;
            text-decoration: none;
        }}
        .filter-tab:hover {{ background: var(--bg-hover); text-decoration: none; }}
        .filter-tab.active {{ background: var(--accent); color: white; border-color: var(--accent); }}
        .search-box {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            color: var(--fg);
            padding: 0.3rem 0.6rem;
            border-radius: 0.25rem;
            font-size: 0.85rem;
            width: 200px;
            margin-bottom: 1rem;
        }}
        .search-box:focus {{ outline: none; border-color: var(--accent); }}
        .timestamp {{ cursor: help; }}
        .data-ref-link {{ font-family: ui-monospace, 'SF Mono', monospace; font-size: 0.85rem; }}
        .artifact-file {{ font-family: ui-monospace, 'SF Mono', monospace; font-size: 0.85rem; }}
        .hash-prefix {{ font-family: ui-monospace, 'SF Mono', monospace; font-size: 0.8rem; color: var(--fg-muted); }}
        .risk-none {{ color: var(--status-active); }}
        .risk-external-dependency {{ color: var(--status-inconclusive); }}
        .risk-temporal-source {{ color: var(--status-retryable); }}
        .empty-state {{
            text-align: center;
            padding: 3rem 1rem;
            color: var(--fg-muted);
        }}
        .empty-state code {{
            background: var(--bg-card);
            padding: 0.2rem 0.5rem;
            border-radius: 0.25rem;
        }}
        .pagination {{
            display: flex;
            gap: 0.25rem;
            justify-content: center;
            align-items: center;
            margin-top: 2rem;
            flex-wrap: wrap;
        }}
        .page-link {{
            padding: 0.3rem 0.6rem;
            border-radius: 0.25rem;
            text-decoration: none;
            color: var(--fg-muted);
            border: 1px solid var(--border);
            font-size: 0.9rem;
        }}
        .page-link:hover {{
            background: var(--bg-card);
            color: var(--fg);
        }}
        .page-link.page-current {{
            background: var(--accent);
            color: white;
            border-color: var(--accent);
            font-weight: 600;
        }}
        .page-link.disabled {{
            opacity: 0.4;
            pointer-events: none;
        }}
        .page-ellipsis {{
            padding: 0.3rem 0.4rem;
            color: var(--fg-muted);
        }}
        .health-pill {{
            display: inline-flex;
            align-items: center;
            gap: 0.35rem;
            padding: 0.2rem 0.55rem;
            border-radius: 0.25rem;
            font-size: 0.8rem;
            font-weight: 600;
            border: 1px solid var(--border);
            background: var(--bg-card);
            color: var(--fg-muted);
        }}
        .health-pill .dot {{
            width: 0.5rem;
            height: 0.5rem;
            border-radius: 50%;
            background: var(--fg-muted);
        }}
        .health-pill.ok {{ color: var(--status-active); border-color: var(--status-active); }}
        .health-pill.ok .dot {{ background: var(--status-active); }}
        .health-pill.down {{ color: var(--status-failed); border-color: var(--status-failed); }}
        .health-pill.down .dot {{ background: var(--status-failed); }}
        @media (max-width: 768px) {{
            body {{ padding: 1rem; }}
            .grid-2 {{ grid-template-columns: 1fr; }}
            table {{ font-size: 0.8rem; }}
            .search-box {{ width: 100%; }}
        }}
    </style>
</head>
<body>
    <div class="nav">
        {_home_link()}
        <a href="/">← Programmes</a>
        <span class="spacer"></span>
        <span class="health-pill" id="health-pill"
              hx-get="/health" hx-trigger="load, every {HEALTH_POLL_SECONDS}s"
              hx-swap="outerHTML">
            <span class="dot"></span>
            <span>checking…</span>
        </span>
        <a href="/archives">Archives</a>
        <a href="/integrity" id="integrity-link"
           hx-get="/integrity/status" hx-trigger="load, every {INTEGRITY_POLL_SECONDS}s"
           hx-swap="outerHTML">Integrity</a>
    </div>
    {body}
    <script>
        // Health pill failure handling: a failed poll (connection
        // refused, HTTP error, timeout) means the service is down —
        // swap the pill to the down state instead of leaving the last
        // (possibly stale "ok") state displayed. The replacement keeps
        // hx-get + hx-trigger so the pill keeps polling and recovers
        // to green when the service returns.
        function markHealthDown() {{
            var pill = document.getElementById('health-pill');
            if (!pill || pill.classList.contains('down')) return;
            var tpl = document.createElement('template');
            tpl.innerHTML = '<span class="health-pill down" id="health-pill"' +
                ' hx-get="/health" hx-trigger="every {HEALTH_POLL_SECONDS}s" hx-swap="outerHTML">' +
                '<span class="dot"></span><span>down</span></span>';
            var el = tpl.content.firstChild;
            pill.replaceWith(el);
            htmx.process(el);
        }}
        ['htmx:sendError', 'htmx:responseError', 'htmx:timeout'].forEach(
            function(evt) {{
                document.addEventListener(evt, function(e) {{
                    if (e.detail.elt && e.detail.elt.id === 'health-pill') {{
                        markHealthDown();
                    }}
                }});
            }}
        );
    </script>
</body>
</html>"""


def render_error(message: str, status_code: int = 404) -> str:
    """Render an error page."""
    return render_base(
        f"Error {status_code}",
        f'<h1 style="color: var(--status-failed)">{escape(message)}</h1>',
    )


def status_class(status: str) -> str:
    """Get the CSS class for a status value."""
    return f"status-{escape(status)}"


def render_status_badge(status: str) -> str:
    """Render a status badge."""
    return f'<span class="status {status_class(status)}">{escape(status)}</span>'


def render_budget_bar(
    completed: int,
    terminal: int | None = None,
    total_trials: int | None = None,
    budget: int = 0,
    used_wall_hours: float | None = None,
    budget_wall_hours: float | None = None,
) -> str:
    """Render a budget progress bar.

    Args:
        completed: Number of completed trials (scientific evidence).
        terminal: Number of terminal trials (completed + failed + retryable).
        total_trials: Total number of trials (all statuses).
        budget: Budget max trials.
        used_wall_hours: Actual wall time used (sum of trial durations).
        budget_wall_hours: Budget max wall time in hours.
    """
    # Use terminal count if provided, else fall back to completed
    used = terminal if terminal is not None else completed
    pct = (used / budget * 100) if budget > 0 else 0
    pct = min(pct, 100)

    # Build the label — show completed/total/budget when all available
    if total_trials is not None:
        label = f"{completed} completed / {total_trials} total / {budget} budget"
    else:
        label = f"{used} / {budget} trials"

    # Wall time line — show used / budget, or just used if no budget set
    wall_line = ""
    if budget_wall_hours is not None:
        used_h = used_wall_hours if used_wall_hours is not None else 0.0
        if budget_wall_hours > 0:
            wall_line = f"<div class=\"muted\" style=\"font-size: 0.8rem; margin-top: 0.25rem\">wall time: {used_h:.1f}h / {budget_wall_hours}h</div>"
        elif used_h > 0:
            wall_line = f"<div class=\"muted\" style=\"font-size: 0.8rem; margin-top: 0.25rem\">wall time: {used_h:.1f}h (no budget)</div>"

    return f"""
    <div class="budget-bar">
        <div class="budget-fill" style="width: {pct:.0f}%"></div>
    </div>
    <div class="muted" style="font-size: 0.8rem; margin-top: 0.25rem">
        {label}
    </div>{wall_line}"""


def render_json_pretty(data: Any) -> str:
    """Render a dict/list as pretty-printed JSON in a <pre> block."""
    return f'<pre>{escape(json.dumps(data, indent=2, default=str))}</pre>'


def format_timestamp(ts: str | None) -> str:
    """Format an ISO timestamp as human-readable with full precision in tooltip.

    "2026-09-13T21:32:13.066856+00:00" → "Sep 13 21:32 UTC"
    with title="2026-09-13T21:32:13.066856+00:00" for precision on hover.
    """
    if not ts:
        return "—"
    try:
        # Parse ISO 8601 and reformat
        from datetime import datetime

        dt = datetime.fromisoformat(ts)
        short = dt.strftime("%b %d %H:%M UTC")
        return f'<span class="timestamp" title="{escape(ts)}">{escape(short)}</span>'
    except Exception:
        return f'<span class="timestamp" title="{escape(ts)}">{escape(ts[:19])}</span>'


def risk_class(risk: str | None) -> str:
    """Get the CSS class for a reproducibility risk value."""
    if not risk:
        return ""
    return f"risk-{escape(risk)}"
