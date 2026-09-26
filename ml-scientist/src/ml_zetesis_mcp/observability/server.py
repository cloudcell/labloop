"""Observability web GUI for zetesis — read-only views over the
search-loop state. Same contract as the other GUIs: a separate port, a
read-only projection, a health pill that probes the MCP server
server-side and goes red on any failed poll.
"""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import Route

from ..state.store import SearchStore
from .templates import render_error
from .views import campaigns as campaign_views
from .views import integrity as integrity_views
from .views import investigations as inv_views


def create_observability_app(
    store: SearchStore,
    mcp_health_url: str | None = None,
    observability_config: dict | None = None,
    upstream_gui_bases: dict | None = None,
) -> Starlette:
    """Create the zetesis observability Starlette app.

    Args:
        store: The shared SearchStore (read-only access).
        mcp_health_url: URL of the MCP server's /health endpoint. When
            set, the GUI's /health probes it server-side so the pill
            reflects both services.
        observability_config: the [observability] table —
            health_poll_seconds / integrity_poll_seconds.
    """
    from . import templates as _templates

    _obs_cfg = observability_config or {}
    _templates.configure_polling(
        health_poll_seconds=_obs_cfg.get("health_poll_seconds"),
        integrity_poll_seconds=_obs_cfg.get("integrity_poll_seconds"),
        agora_url=_obs_cfg.get("agora_gui_url"),
    )

    def index(request: Request) -> HTMLResponse:
        view = request.query_params.get("view", "all")
        return inv_views.render_investigation_list(store, view)

    def investigation_detail(request: Request) -> HTMLResponse:
        return inv_views.render_investigation_detail(
            store, request.path_params["investigation_id"],
            gui_bases=upstream_gui_bases or {},
        )

    def promotion(request: Request) -> HTMLResponse:
        return campaign_views.render_promotion_page(store)

    def campaign_detail(request: Request) -> HTMLResponse:
        return campaign_views.render_campaign_detail(
            store, request.path_params["campaign_id"],
            gui_bases=upstream_gui_bases or {},
        )

    def candidate_detail(request: Request) -> HTMLResponse:
        return campaign_views.render_candidate_detail(
            store, request.path_params["candidate_id"]
        )

    def evidence_ref_detail(request: Request) -> HTMLResponse:
        return inv_views.render_evidence_ref_detail(
            store, request.path_params["evidence_ref_id"],
            gui_bases=upstream_gui_bases or {},
        )

    def finding_shortcut(request: Request) -> Response:
        """Redirect /finding/{id} → its investigation's find- anchor."""
        fid = request.path_params["finding_id"]
        f = store.get_finding(fid)
        if f is not None:
            return RedirectResponse(
                f"/investigation/{f.investigation_id}#find-{fid}"
            )
        return HTMLResponse(
            render_error(f"Finding not found: {fid}", 404),
            status_code=404,
        )

    def spawn_shortcut(request: Request) -> Response:
        """Redirect /spawn/{id} → its campaign's spawn- anchor."""
        sid = request.path_params["spawn_id"]
        s = store.get_spawn(sid)
        if s is not None:
            return RedirectResponse(
                f"/campaign/{s.campaign_id}#spawn-{sid}"
            )
        return HTMLResponse(
            render_error(f"Spawn not found: {sid}", 404), status_code=404
        )

    def campaign_result_shortcut(request: Request) -> Response:
        """Redirect /campaign-result/{id} → campaign's cres- anchor."""
        rid = request.path_params["result_id"]
        r = store.get_campaign_result(rid)
        if r is not None:
            return RedirectResponse(
                f"/campaign/{r.campaign_id}#cres-{rid}"
            )
        return HTMLResponse(
            render_error(
                f"Campaign result not found: {rid}", 404
            ),
            status_code=404,
        )

    def search_policy_detail(request: Request) -> HTMLResponse:
        """Search-policy detail — standalone page (no parent page)."""
        return campaign_views.render_search_policy_detail(
            store, request.path_params["policy_id"]
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
        Route("/investigation/{investigation_id}", investigation_detail),
        Route("/promotion", promotion),
        Route("/campaign/{campaign_id}", campaign_detail),
        Route("/candidate/{candidate_id}", candidate_detail),
        Route("/evidence-ref/{evidence_ref_id}", evidence_ref_detail),
        Route("/finding/{finding_id}", finding_shortcut),
        Route("/spawn/{spawn_id}", spawn_shortcut),
        Route("/campaign-result/{result_id}", campaign_result_shortcut),
        Route("/search-policy/{policy_id}", search_policy_detail),
        Route("/integrity", integrity),
        Route("/integrity/help", integrity_help),
        Route("/integrity/status", integrity_status),
        Route("/integrity/log/{name}", integrity_log),
        Route("/integrity/run/{name}/{index}", integrity_run),
    ]

    app = Starlette(routes=routes, exception_handlers={404: not_found})
    app.state.store = store
    return app
