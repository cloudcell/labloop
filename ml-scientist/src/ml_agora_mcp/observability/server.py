"""Observability web GUI for ml-agora — read-only views over the lab.

Same contract as the siblings' GUIs: a separate port, a read-only
projection, a health pill that probes the MCP server server-side and
goes red on any failed poll.

Dashboard pages are **plugins**: each module under ``views/``
exports ``ROUTE``, ``NAV_TITLE``, and ``routes(ctx)``; the
``views.PLUGINS`` list is the whole registry. The right-side menu is
generated from it — retiring a dashboard is one file + one list
entry, extracting it to a separate package is moving one file.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import templates as _templates
from .templates import render_error
from .views import PLUGINS


def create_observability_app(
    adaptors,
    *,
    log_dir: Path,
    mcp_health_url: str | None = None,
    observability_config: dict | None = None,
) -> Starlette:
    """Create the agora observability Starlette app.

    Args:
        adaptors: The shared Adaptors container — channel state is
            read live so supervisor reconnects show up immediately.
        log_dir: the audit-log dir (~/.ml-agora/logs).
        mcp_health_url: URL of the MCP server's /health endpoint. When
            set, the GUI's /health probes it server-side so the pill
            reflects both services.
        observability_config: the [observability] table —
            health_poll_seconds / integrity_poll_seconds.
    """
    _obs_cfg = observability_config or {}
    _templates.configure_polling(
        health_poll_seconds=_obs_cfg.get("health_poll_seconds"),
        integrity_poll_seconds=_obs_cfg.get("integrity_poll_seconds"),
    )
    _templates.set_nav([(p.ROUTE, p.NAV_TITLE) for p in PLUGINS])

    ctx = SimpleNamespace(
        adaptors=adaptors,
        log_dir=log_dir,
        mcp_health_url=mcp_health_url,
    )

    def health(request: Request) -> Response:
        """Liveness probe — JSON for plain probes, pill fragment for HTMX."""
        import httpx

        mcp_ok = True
        if mcp_health_url:
            try:
                r = httpx.get(mcp_health_url, timeout=2.0)
                mcp_ok = r.status_code == 200
            except Exception:
                mcp_ok = False

        status = "ok" if mcp_ok else "down"
        if request.headers.get("HX-Request") == "true":
            return HTMLResponse(
                f'<span class="health-pill {status}" id="health-pill"'
                ' hx-get="/health" hx-trigger="every 10s"'
                ' hx-swap="outerHTML">'
                '<span class="dot"></span>'
                f'<span>{status}</span></span>'
            )
        return JSONResponse({"status": status, "mcp": "up" if mcp_ok else "down"})

    def integrity_status(request: Request) -> HTMLResponse:
        """Nav-link fragment — amber when the latest run has violations."""
        from ..integrity.log import list_check_logs

        logs = list_check_logs(log_dir, limit=1)
        bad = logs and logs[0].get("status") == "violations"
        style = (
            ' style="color: var(--status-inconclusive)"' if bad else ""
        )
        return HTMLResponse(
            f'<a href="/integrity" id="integrity-link"{style}'
            ' hx-get="/integrity/status" hx-trigger="every 30s"'
            ' hx-swap="outerHTML">Integrity</a>'
        )

    def not_found(request: Request, exc: Exception) -> HTMLResponse:
        return HTMLResponse(render_error("Not found", 404), status_code=404)

    def favicon(request: Request) -> Response:
        svg = (
            "<svg xmlns='http://www.w3.org/2000/svg' width='32' height='32' "
            "viewBox='0 0 32 32' fill='none'>"
            "<rect width='32' height='32' rx='7' fill='#172033'/>"
            "<circle cx='16' cy='16' r='8.5' stroke='#1f5f70' "
            "stroke-width='2.6' fill='none'/>"
            "<circle cx='16' cy='16' r='2.8' fill='#1f5f70'/>"
            "<path d='M16 7.5a8.5 8.5 0 0 1 7.3 12.8' stroke='#3a8fa3' "
            "stroke-width='2.6' fill='none' stroke-linecap='round'/>"
            "</svg>"
        )
        return Response(svg, media_type="image/svg+xml")

    static_dir = Path(__file__).parent / "static"

    routes = [
        Route("/health", health),
        Route("/favicon.ico", favicon),
        Route("/integrity/status", integrity_status),
        Mount("/static", app=StaticFiles(directory=str(static_dir)),
              name="static"),
    ]
    for plugin in PLUGINS:
        routes.extend(plugin.routes(ctx))

    app = Starlette(routes=routes, exception_handlers={404: not_found})
    app.state.adaptors = adaptors
    return app
