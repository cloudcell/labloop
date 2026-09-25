"""HTML templates for the arete observability GUI.

Same visual language as the other loop GUIs — deliberately duplicated
(ADR-0001): the servers share no code. Keep both copies small.
"""

from __future__ import annotations

import html as html_module
from typing import Any


def escape(text: Any) -> str:
    """HTML-escape a value."""
    return html_module.escape(str(text))

# Nav-widget poll cadences — operator-tunable via [observability] in
# the server's toml; process-wide, applied at app construction.
HEALTH_POLL_SECONDS = 10
INTEGRITY_POLL_SECONDS = 30


def configure_polling(
    health_poll_seconds: int | None = None,
    integrity_poll_seconds: int | None = None,
) -> None:
    """Apply configured nav-poll intervals ([observability] table)."""
    global HEALTH_POLL_SECONDS, INTEGRITY_POLL_SECONDS
    if health_poll_seconds is not None:
        HEALTH_POLL_SECONDS = int(health_poll_seconds)
    if integrity_poll_seconds is not None:
        INTEGRITY_POLL_SECONDS = int(integrity_poll_seconds)



def render_base(title: str, body: str) -> str:
    """Render the base HTML page with nav, head, and HTMX script."""
    htmx_script = "https://unpkg.com/htmx.org@2.0.4"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{escape(title)} — ml-arete</title>
    <script src="{htmx_script}"></script>
    <style>
        :root {{
            --status-open: #22c55e;
            --status-failed: #ef4444;
            --status-inconclusive: #f59e0b;
            --bg: #0f172a;
            --bg-card: #1e293b;
            --bg-hover: #334155;
            --fg: #e2e8f0;
            --fg-muted: #94a3b8;
            --accent: #2dd4bf;
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
        a {{ color: var(--accent); text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        .nav {{ margin-bottom: 1.5rem; display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; }}
        .nav .spacer {{ flex: 1; }}
        .card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 0.5rem;
            padding: 1rem;
            margin-bottom: 1rem;
        }}
        .card h2 {{ margin-top: 0; }}
        table {{ width: 100%; border-collapse: collapse; margin: 0.5rem 0; }}
        th, td {{ text-align: left; padding: 0.5rem; border-bottom: 1px solid var(--border); }}
        th {{ color: var(--fg-muted); font-size: 0.85rem; text-transform: uppercase; }}
        td {{ font-size: 0.9rem; vertical-align: top; }}
        tbody tr:nth-child(even) {{ background: rgba(255,255,255,0.02); }}
        tbody tr:hover {{ background: rgba(255,255,255,0.05); }}
        .status {{
            display: inline-block;
            padding: 0.15rem 0.5rem;
            border-radius: 0.25rem;
            font-size: 0.8rem;
            font-weight: bold;
        }}
        .status-open {{ background: rgba(34,197,94,0.2); color: var(--status-open); }}
        .status-concluded {{ background: rgba(45,212,191,0.2); color: var(--accent); }}
        .status-abandoned {{ background: rgba(239,68,68,0.2); color: var(--status-failed); }}
        .status-provisional {{ background: rgba(245,158,11,0.2); color: var(--status-inconclusive); }}
        .status-asserted {{ background: rgba(45,212,191,0.2); color: var(--accent); }}
        .status-dropped {{ background: rgba(239,68,68,0.2); color: var(--status-failed); }}
        .muted {{ color: var(--fg-muted); }}
        .mono {{ font-family: ui-monospace, 'SF Mono', 'Cascadia Code', monospace; font-size: 0.85rem; }}
        .badge {{
            display: inline-block;
            padding: 0.1rem 0.4rem;
            border-radius: 0.25rem;
            font-size: 0.75rem;
            background: var(--border);
            color: var(--fg-muted);
            margin-right: 0.4rem;
        }}
        .filter-tabs {{ display: flex; gap: 0.5rem; margin-bottom: 1rem; flex-wrap: wrap; align-items: center; }}
        .filter-tab {{
            padding: 0.3rem 0.8rem;
            border-radius: 0.25rem;
            background: var(--bg-card);
            border: 1px solid var(--border);
            color: var(--fg-muted);
            font-size: 0.85rem;
            text-decoration: none;
        }}
        .filter-tab:hover {{ background: var(--bg-hover); text-decoration: none; }}
        .filter-tab.active {{ background: var(--accent); color: #0f172a; border-color: var(--accent); }}
        .timestamp {{ cursor: help; }}
        .confidence-bar {{
            background: var(--border);
            border-radius: 0.25rem;
            height: 0.4rem;
            width: 4.5rem;
            display: inline-block;
            vertical-align: middle;
            margin-right: 0.4rem;
            overflow: hidden;
        }}
        .confidence-fill {{ background: var(--accent); height: 100%; }}
        .empty-state {{ text-align: center; padding: 3rem 1rem; color: var(--fg-muted); }}
        .empty-state code {{ background: var(--bg-card); padding: 0.2rem 0.5rem; border-radius: 0.25rem; }}
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
        .health-pill .dot {{ width: 0.5rem; height: 0.5rem; border-radius: 50%; background: var(--fg-muted); }}
        .health-pill.ok {{ color: var(--status-open); border-color: var(--status-open); }}
        .health-pill.ok .dot {{ background: var(--status-open); }}
        .health-pill.down {{ color: var(--status-failed); border-color: var(--status-failed); }}
        .health-pill.down .dot {{ background: var(--status-failed); }}
        .stat-grid {{ display: flex; gap: 1.5rem; flex-wrap: wrap; margin-bottom: 1rem; }}
        .stat {{ font-size: 0.85rem; color: var(--fg-muted); }}
        .stat b {{ color: var(--fg); font-size: 1.1rem; margin-right: 0.25rem; }}
        pre {{
            background: var(--bg);
            border: 1px solid var(--border);
            border-radius: 0.35rem;
            padding: 0.6rem;
            overflow-x: auto;
            font-size: 0.8rem;
        }}
        @media (max-width: 768px) {{
            body {{ padding: 1rem; }}
            table {{ font-size: 0.8rem; }}
        }}
    </style>
</head>
<body>
    <div class="nav">
        <a href="/">← Improvers</a>
        <span class="spacer"></span>
        <span class="health-pill" id="health-pill"
              hx-get="/health" hx-trigger="load, every {HEALTH_POLL_SECONDS}s"
              hx-swap="outerHTML">
            <span class="dot"></span>
            <span>checking…</span>
        </span>
        <a href="/integrity" id="integrity-link"
           hx-get="/integrity/status" hx-trigger="load, every {INTEGRITY_POLL_SECONDS}s"
           hx-swap="outerHTML">Integrity</a>
    </div>
    {body}
    <script>
        // Same contract as the other GUIs: a failed health poll means
        // the service is down — flip the pill red instead of leaving a
        // stale green. The replacement keeps polling.
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


def format_timestamp(ts: str | None) -> str:
    """ISO timestamp → 'Sep 13 21:32 UTC' with full value on hover."""
    if not ts:
        return "—"
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(ts)
        short = dt.strftime("%b %d %H:%M UTC")
        return f'<span class="timestamp" title="{escape(ts)}">{escape(short)}</span>'
    except Exception:
        return f'<span class="timestamp" title="{escape(ts)}">{escape(ts[:19])}</span>'
