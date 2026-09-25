"""Observability web GUI for arete — read-only views over the
recursive-loop state. Same contract as the other GUIs: a separate
port, a read-only projection, a health pill that probes the MCP
server server-side and goes red on any failed poll.
"""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from ..state.store import ImproverStore
from .templates import render_error
from .views import improvers as imp_views
from .views import integrity as integrity_views
from .views import tournaments as tourn_views


def create_observability_app(
    store: ImproverStore,
    mcp_health_url: str | None = None,
    observability_config: dict | None = None,
    upstream_gui_bases: dict | None = None,
) -> Starlette:
    """Create the arete observability Starlette app.

    Args:
        store: The shared ImproverStore (read-only access).
        mcp_health_url: URL of the MCP server's /health endpoint.
        observability_config: the [observability] table —
            health_poll_seconds / integrity_poll_seconds.
        upstream_gui_bases: {channel: gui_base} for linking upstream
            entity ids (camp-/cand-/claim-/prog-) to their owning
            server's GUI.
    """
    from . import templates as _templates

    _obs_cfg = observability_config or {}
    _templates.configure_polling(
        health_poll_seconds=_obs_cfg.get("health_poll_seconds"),
        integrity_poll_seconds=_obs_cfg.get("integrity_poll_seconds"),
    )

    def index(request: Request) -> HTMLResponse:
        view = request.query_params.get("view", "all")
        return imp_views.render_improver_list(store, view)

    def improver_detail(request: Request) -> HTMLResponse:
        return imp_views.render_improver_detail(
            store, request.path_params["improver_id"]
        )

    def tournament_detail(request: Request) -> HTMLResponse:
        return tourn_views.render_tournament_detail(
            store, request.path_params["tournament_id"],
            gui_bases=upstream_gui_bases or {},
        )

    def proposal_detail(request: Request) -> HTMLResponse:
        return tourn_views.render_proposal_detail(
            store, request.path_params["proposal_id"]
        )

    def campaign_link_detail(request: Request) -> HTMLResponse:
        return tourn_views.render_campaign_link_detail(
            store, request.path_params["link_id"],
            gui_bases=upstream_gui_bases or {},
        )

    def contract_detail(request: Request) -> HTMLResponse:
        return tourn_views.render_contract_detail(
            store, request.path_params["contract_id"]
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

    def integrity(request: Request) -> HTMLResponse:
        """Integrity — latest check run + log history (read-only)."""
        return HTMLResponse(integrity_views.render_integrity(store))

    def integrity_log(request: Request) -> HTMLResponse:
        """One recorded check run, rendered from its log file."""
        return HTMLResponse(
            integrity_views.render_integrity_log(
                store, request.path_params["name"]
            )
        )

    def integrity_run(request: Request) -> HTMLResponse:
        """One recorded run within a day file."""
        return HTMLResponse(
            integrity_views.render_integrity_run(
                store,
                request.path_params["name"],
                int(request.path_params["index"]),
            )
        )

    def integrity_help(request: Request) -> HTMLResponse:
        """What each integrity check means — static reference."""
        return HTMLResponse(integrity_views.render_integrity_help())

    def integrity_status(request: Request) -> HTMLResponse:
        """Nav-link fragment — amber when the latest run has violations."""
        from ..integrity.checks import log_dir_for
        from ..integrity.log import list_check_logs

        logs = list_check_logs(log_dir_for(store), limit=1)
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

    routes = [
        Route("/", index),
        Route("/health", health),
        Route("/favicon.ico", favicon),
        Route("/improver/{improver_id}", improver_detail),
        Route("/tournament/{tournament_id}", tournament_detail),
        Route("/proposal/{proposal_id}", proposal_detail),
        Route("/campaign-link/{link_id}", campaign_link_detail),
        Route("/contract/{contract_id}", contract_detail),
        Route("/integrity", integrity),
        Route("/integrity/help", integrity_help),
        Route("/integrity/status", integrity_status),
        Route("/integrity/log/{name}", integrity_log),
        Route("/integrity/run/{name}/{index}", integrity_run),
    ]

    app = Starlette(routes=routes, exception_handlers={404: not_found})
    app.state.store = store
    return app
