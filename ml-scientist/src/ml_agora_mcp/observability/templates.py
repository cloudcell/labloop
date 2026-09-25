"""HTML templates for the agora observability GUI.

Same visual language as the siblings' GUIs — deliberately duplicated
(ADR-0001/0002): the products share no code. Keep the copy small.
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


# Plugin nav — (route, label) pairs registered at app construction
# from views.PLUGINS. The right-side panel renders these; retiring a
# plugin removes its menu entry automatically.
_NAV_ITEMS: list[tuple[str, str]] = []


def set_nav(items: list[tuple[str, str]]) -> None:
    """Install the plugin nav entries for the right-side panel."""
    global _NAV_ITEMS
    _NAV_ITEMS = list(items)


def render_base(
    title: str, body: str, active: str | None = None,
    wide: bool = False,
) -> str:
    """Render the base HTML page: top bar (health/integrity), main
    content, and the right-side plugin menu panel."""
    htmx_script = "https://unpkg.com/htmx.org@2.0.4"
    menu = "".join(
        f'<a class="menu-item{" active" if route == active else ""}"'
        f' href="{escape(route)}">{escape(label)}</a>'
        for route, label in _NAV_ITEMS
    )
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
    <title>{escape(title)} — ml-agora</title>
    <script src="{htmx_script}"></script>
    <style>
        :root {{
            --status-active: #22c55e;
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
            line-height: 1.5;
        }}
        h1 {{ font-size: 1.5rem; margin-bottom: 1rem; color: var(--accent); }}
        h2 {{ font-size: 1.2rem; margin: 1.5rem 0 0.75rem; color: var(--fg); }}
        a {{ color: var(--accent); text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        /* App shell — fixed left rail + content (ChatGPT-style). */
        .sidebar {{
            position: fixed;
            top: 0; left: 0; bottom: 0;
            width: 220px;
            display: flex;
            flex-direction: column;
            background: var(--bg-card);
            border-right: 1px solid var(--border);
            padding: 1rem 0.6rem;
        }}
        .sidebar .brand {{
            color: var(--accent);
            font-weight: 700;
            font-size: 1.05rem;
            padding: 0.3rem 0.6rem 1rem;
            display: block;
        }}
        .sidebar .brand:hover {{ text-decoration: none; }}
        .sidebar .panel-title {{
            font-size: 0.72rem;
            text-transform: uppercase;
            color: var(--fg-muted);
            letter-spacing: 0.06em;
            padding: 0.4rem 0.6rem;
        }}
        .sidebar a.menu-item {{
            display: block;
            padding: 0.5rem 0.6rem;
            border-radius: 0.4rem;
            color: var(--fg);
            font-size: 0.92rem;
            margin-bottom: 0.1rem;
        }}
        .sidebar a.menu-item:hover {{
            background: var(--bg-hover);
            text-decoration: none;
        }}
        .sidebar a.menu-item.active {{
            background: var(--bg-hover);
            color: var(--accent);
        }}
        .sidebar .sidebar-footer {{
            margin-top: auto;
            padding: 0.6rem;
            border-top: 1px solid var(--border);
            display: flex;
            align-items: center;
            gap: 0.6rem;
            font-size: 0.85rem;
        }}
        .sidebar .sidebar-footer a {{ color: var(--fg-muted); }}
        .main {{
            margin-left: 220px;
            padding: 1.5rem 2rem;
        }}
        .main-inner {{ max-width: 1100px; margin: 0 auto; }}
        .card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 0.5rem;
            padding: 1rem;
            margin-bottom: 1rem;
        }}
        .card h2 {{ margin-top: 0; }}
        ul {{ padding-left: 1.4rem; }}
        ul li {{ margin-bottom: 0.15rem; }}
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
        .status-live {{ background: rgba(34,197,94,0.2); color: var(--status-active); }}
        .status-expired {{ background: rgba(239,68,68,0.2); color: var(--status-failed); }}
        .status-superseded {{ background: rgba(245,158,11,0.2); color: var(--status-inconclusive); }}
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
        .empty-state {{ text-align: center; padding: 3rem 1rem; color: var(--fg-muted); }}
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
        .health-pill.ok {{ color: var(--status-active); border-color: var(--status-active); }}
        .health-pill.ok .dot {{ background: var(--status-active); }}
        .health-pill.down {{ color: var(--status-failed); border-color: var(--status-failed); }}
        .health-pill.down .dot {{ background: var(--status-failed); }}
        .stat-grid {{ display: flex; gap: 1.5rem; flex-wrap: wrap; margin-bottom: 1rem; }}
        .stat {{ font-size: 0.85rem; color: var(--fg-muted); }}
        .stat b {{ color: var(--fg); font-size: 1.1rem; margin-right: 0.25rem; }}
        #graph-layout {{
            display: flex;
            gap: 0.75rem;
            height: calc(100vh - 10rem);
        }}
        #graph-side {{
            width: 170px;
            flex: none;
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }}
        #mini-holder {{
            flex: 1;
            min-height: 0;
            border: 1px solid var(--border);
            border-radius: 0.5rem;
            background: #0f172a;
            overflow-y: auto;
            overflow-x: hidden;
            scrollbar-width: thin;
            scrollbar-color: rgba(148, 163, 184, 0.45) transparent;
        }}
        #mini-holder::-webkit-scrollbar {{ width: 8px; }}
        #mini-holder::-webkit-scrollbar-track {{
            background: transparent;
        }}
        #mini-holder::-webkit-scrollbar-thumb {{
            background: rgba(148, 163, 184, 0.45);
            border-radius: 4px;
        }}
        #graph-canvas {{
            position: relative;
            flex: 1;
            min-width: 0;
            border: 1px solid var(--border);
            border-radius: 0.5rem;
            background: var(--bg);
            overflow: hidden;
        }}
        #zoom-ctl {{ display: flex; gap: 4px; }}
        #zoom-ctl a {{
            flex: 1;
            height: 26px;
            line-height: 24px;
            text-align: center;
            color: var(--fg-muted);
            background: #0f172a;
            border: 1px solid var(--border);
            border-radius: 0.4rem;
            text-decoration: none;
            font-size: 0.95rem;
        }}
        #zoom-ctl a:hover {{ color: var(--fg); }}
        #graph-tip {{
            min-height: 1.2rem;
            font-size: 0.8rem;
            font-family: ui-monospace, monospace;
        }}
        /* Right-side detail panel — opens on graph double-click. */
        #detail-panel {{
            position: fixed;
            top: 0; right: 0; bottom: 0;
            width: 42vw;
            min-width: 380px;
            display: none;
            flex-direction: column;
            background: var(--bg-card);
            border-left: 1px solid var(--border);
            z-index: 100;
        }}
        #detail-panel.open {{ display: flex; }}
        #detail-head {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0.6rem 0.8rem;
            border-bottom: 1px solid var(--border);
            font-family: ui-monospace, monospace;
            font-size: 0.85rem;
        }}
        #detail-close {{
            color: var(--fg-muted);
            font-size: 1.1rem;
            line-height: 1;
        }}
        #detail-close:hover {{ color: var(--fg); text-decoration: none; }}
        #detail-frame {{ flex: 1; border: none; width: 100%; }}
        @media (max-width: 900px) {{
            .sidebar {{ position: static; width: 100%;
                min-height: 0; border-right: none;
                border-bottom: 1px solid var(--border); }}
            .main {{ margin-left: 0; padding: 1rem; }}
            table {{ font-size: 0.8rem; }}
        }}
    </style>
</head>
<body>
    <aside class="sidebar">
        <a class="brand" href="/">ml-agora</a>
        <div class="panel-title">Dashboards</div>
        {menu}
        <div class="sidebar-footer">
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
    </aside>
    <div class="main"><div class="{'' if wide else 'main-inner'}">{body}</div></div>
    <script>
        // A failed health poll means the service is down — flip the
        // pill red instead of leaving a stale green.
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
