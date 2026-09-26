"""Observability web GUI — read-only views over the loop state.

This module serves a lightweight HTMX-based web GUI on a separate port
from the MCP server. It reads from the same StateStore the MCP server
uses. It has no write path — all mutations flow through MCP tools.

Ontological category: process (it renders; it does not persist).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles

from ..state.store import StateStore
from .views import programme as programme_views
from .views import trial as trial_views
from .views import belief as belief_views
from .views import conclusion as conclusion_views
from .views import dataref as dataref_views
from .views import contract as contract_views
from .views import integrity as integrity_views
from .templates import render_base, render_error


def create_observability_app(
    store: StateStore,
    refresh_interval: int = 5,
    archiver=None,
    mcp_health_url: str | None = None,
    observability_config: dict | None = None,
) -> Starlette:
    """Create the observability Starlette app.

    Args:
        store: The shared StateStore (read-only access).
        refresh_interval: HTMX polling interval in seconds (0 = disabled).
        archiver: Optional Archiver instance for archive views.
        mcp_health_url: Optional URL of the MCP server's /health endpoint.
            When set, the GUI's /health endpoint probes the MCP server
            server-side and reports combined status, so the nav status
            pill reflects both services rather than only the GUI.
        observability_config: the [observability] table —
            health_poll_seconds / integrity_poll_seconds for the
            nav-widget poll cadences.
    """
    from . import templates as _templates

    _obs_cfg = observability_config or {}
    _templates.configure_polling(
        health_poll_seconds=_obs_cfg.get("health_poll_seconds"),
        integrity_poll_seconds=_obs_cfg.get("integrity_poll_seconds"),
        agora_url=_obs_cfg.get("agora_gui_url"),
        peer_gui_urls={
            peer: _obs_cfg[f"{peer}_gui_url"]
            for peer in ("zetesis", "anamnesis")
            if _obs_cfg.get(f"{peer}_gui_url")
        },
    )

    static_dir = Path(__file__).parent / "static"

    from .views import archive as archive_views

    def index(request: Request) -> HTMLResponse:
        """Programme list — the landing page (paginated, sortable)."""
        page = int(request.query_params.get("page", 1))
        per_page = int(request.query_params.get("per_page", 50))
        sort = request.query_params.get("sort", "created_desc")
        status = request.query_params.get("status")
        return programme_views.render_programme_list(
            store, refresh_interval, page=page, per_page=per_page,
            archiver=archiver, sort=sort, status=status,
        )

    def search(request: Request) -> HTMLResponse:
        """Search across live and archived programmes (HTMX endpoint)."""
        q = request.query_params.get("q", "")
        return programme_views.render_search_results(store, q, archiver=archiver)

    def programme_detail(request: Request) -> HTMLResponse:
        """Programme detail — hypotheses, trials, beliefs, conclusions.

        If the programme is not in the live DB, check if it's archived
        and redirect to the archived programme view.
        """
        pid = request.path_params["programme_id"]
        return programme_views.render_programme_detail(
            store, pid, refresh_interval, archiver=archiver
        )

    def trial_detail(request: Request) -> HTMLResponse:
        """Trial detail — config, bundle, observations, sparkline."""
        pid = request.path_params["programme_id"]
        tid = request.path_params["trial_id"]
        return trial_views.render_trial_detail(store, pid, tid)

    def trial_shortcut(request: Request) -> Response:
        """Redirect /trial/{id} → the canonical nested trial URL.

        Trial IDs are globally unique, so the programme is resolved
        from the trial alone. Live trials go to
        /programme/{pid}/trial/{tid}; trials of archived programmes go
        to the archive copy (the archive DB is authoritative). Trials
        missing from live entirely (purged pre-no-purge) are found by
        scanning the archive DBs.
        """
        tid = request.path_params["trial_id"]

        trial = store.get_trial(tid)
        if trial is not None:
            pid = trial.programme_id
            # Archived programmes → link to the archive copy
            prog = store.get_programme(pid)
            if prog is not None and prog.status.value == "archived" \
                    and archiver is not None:
                entry = store._fetchone(
                    "SELECT archive_id FROM archive_entries WHERE programme_id = ?",
                    (pid,),
                )
                if entry is not None:
                    return RedirectResponse(
                        f"/archive/{entry['archive_id']}/programme/{pid}/trial/{tid}"
                    )
            return RedirectResponse(f"/programme/{pid}/trial/{tid}")

        # Not in live — scan archive DBs (pre-no-purge purged programmes)
        if archiver is not None:
            for arch in archiver.list_archives():
                apath = Path(arch["archive_path"])
                if not apath.exists():
                    continue
                try:
                    import sqlite3
                    conn = sqlite3.connect(str(apath))
                    conn.row_factory = sqlite3.Row
                    row = conn.execute(
                        "SELECT programme_id FROM trials WHERE id = ?",
                        (tid,),
                    ).fetchone()
                    conn.close()
                except Exception:
                    continue
                if row is not None:
                    return RedirectResponse(
                        f"/archive/{arch['archive_id']}/programme/"
                        f"{row['programme_id']}/trial/{tid}"
                    )

        return HTMLResponse(
            render_error(f"Trial not found: {tid}", 404), status_code=404
        )

    def _archived_programme_url(programme_id: str) -> str | None:
        """Archive URL for a programme, if it was archived."""
        prog = store.get_programme(programme_id)
        if (
            prog is not None
            and prog.status.value == "archived"
            and archiver is not None
        ):
            entry = store._fetchone(
                "SELECT archive_id FROM archive_entries "
                "WHERE programme_id = ?",
                (programme_id,),
            )
            if entry is not None:
                return f"/archive/{entry['archive_id']}/programme/{programme_id}"
        return None

    def hypothesis_shortcut(request: Request) -> Response:
        """Redirect /hypothesis/{id} → the programme page's hyp- anchor."""
        hid = request.path_params["hypothesis_id"]
        hyp = store.get_hypothesis(hid)
        if hyp is not None:
            if url := _archived_programme_url(hyp.programme_id):
                return RedirectResponse(f"{url}#hyp-{hid}")
            return RedirectResponse(
                f"/programme/{hyp.programme_id}#hyp-{hid}"
            )
        return HTMLResponse(
            render_error(f"Hypothesis not found: {hid}", 404),
            status_code=404,
        )

    def conclusion_shortcut(request: Request) -> Response:
        """Redirect /conclusion/{id} → the conclusions page anchor."""
        cid = request.path_params["conclusion_id"]
        conc = store.get_conclusion(cid)
        if conc is not None:
            if url := _archived_programme_url(conc.programme_id):
                return RedirectResponse(f"{url}#conc-{cid}")
            return RedirectResponse(
                f"/programme/{conc.programme_id}/conclusions#conc-{cid}"
            )
        return HTMLResponse(
            render_error(f"Conclusion not found: {cid}", 404),
            status_code=404,
        )

    def observation_shortcut(request: Request) -> Response:
        """Redirect /observation/{id} → its trial page's obs- anchor."""
        oid = request.path_params["observation_id"]
        obs = store.get_observation(oid)
        if obs is not None:
            trial = store.get_trial(obs.trial_id)
            if trial is not None:
                if url := _archived_programme_url(trial.programme_id):
                    return RedirectResponse(
                        f"{url}/trial/{obs.trial_id}#obs-{oid}"
                    )
                return RedirectResponse(
                    f"/programme/{trial.programme_id}/trial/"
                    f"{obs.trial_id}#obs-{oid}"
                )
        return HTMLResponse(
            render_error(f"Observation not found: {oid}", 404),
            status_code=404,
        )

    def belief_shortcut(request: Request) -> Response:
        """Redirect /belief/{id} → its programme's belief page."""
        bid = request.path_params["belief_id"]
        belief = store.get_belief_by_id(bid)
        if belief is not None:
            return RedirectResponse(
                f"/programme/{belief.programme_id}/belief"
            )
        return HTMLResponse(
            render_error(f"Belief not found: {bid}", 404), status_code=404
        )

    def decision_detail(request: Request) -> HTMLResponse:
        """Promotion decision detail — verdict, evidence, attribution."""
        from .views import decision as decision_views

        return decision_views.render_decision_detail(
            store, request.path_params["decision_id"]
        )

    def health(request: Request) -> Response:
        """Liveness probe.

        Returns JSON ``{"status": "ok"}`` for plain HTTP probes, or an
        HTML fragment for HTMX-driven swaps (the nav status pill polls this
        endpoint and replaces itself).

        When ``mcp_health_url`` is configured, this endpoint also probes
        the MCP server server-side and reports combined status, so the
        pill reflects both services. The browser stays same-origin; no
        CORS is required.
        """
        import httpx

        mcp_ok = True
        if mcp_health_url:
            try:
                # Short timeout — a slow MCP server is a down MCP server
                # for monitoring purposes.
                r = httpx.get(mcp_health_url, timeout=2.0)
                mcp_ok = r.status_code == 200
            except Exception:
                mcp_ok = False

        status = "ok" if mcp_ok else "down"
        if request.headers.get("HX-Request") == "true":
            # NOTE: the returned fragment must NOT include hx-trigger="load"
            # — only "every 10s". The "load" trigger fires on every swap,
            # so including it here creates an infinite rapid-poll loop
            # (each swap triggers load triggers swap...). The initial
            # "load" trigger lives only on the placeholder in render_base.
            return HTMLResponse(
                f'<span class="health-pill {status}" id="health-pill"'
                ' hx-get="/health" hx-trigger="every 10s"'
                ' hx-swap="outerHTML">'
                '<span class="dot"></span>'
                f'<span>{status}</span></span>'
            )
        return JSONResponse({"status": status, "mcp": "up" if mcp_ok else "down"})

    def belief_detail(request: Request) -> HTMLResponse:
        """Belief state — posterior, best trials, parameter importance."""
        pid = request.path_params["programme_id"]
        return belief_views.render_belief_detail(store, pid)

    def conclusion_view(request: Request) -> HTMLResponse:
        """Conclusions — verdict, evidence summary."""
        pid = request.path_params["programme_id"]
        return conclusion_views.render_conclusion_view(store, pid)

    def dataref_detail(request: Request) -> HTMLResponse:
        """M1: DataRef detail — provenance, hash, reproducibility risk."""
        ref_id = request.path_params["data_ref_id"]
        return dataref_views.render_dataref_detail(store, ref_id)

    def contract_detail(request: Request) -> HTMLResponse:
        """EvaluationContract detail — metrics + promotion policy."""
        cid = request.path_params["contract_id"]
        return contract_views.render_contract_detail(store, cid)

    def archive_list(request: Request) -> HTMLResponse:
        """Archive list — browse all archives."""
        return archive_views.render_archive_list(store, archiver)

    def archive_detail(request: Request) -> HTMLResponse:
        """Archive detail — programmes in an archive."""
        aid = request.path_params["archive_id"]
        return archive_views.render_archive_detail(store, archiver, aid)

    def archived_programme_detail(request: Request) -> HTMLResponse:
        """Archived programme detail — read-only view from the archive DB."""
        aid = request.path_params["archive_id"]
        pid = request.path_params["programme_id"]
        return archive_views.render_archived_programme_detail(store, archiver, aid, pid)

    def archived_trial_detail(request: Request) -> HTMLResponse:
        """Archived trial detail — read-only view from the archive DB."""
        aid = request.path_params["archive_id"]
        pid = request.path_params["programme_id"]
        tid = request.path_params["trial_id"]
        return archive_views.render_archived_trial_detail(store, archiver, aid, pid, tid)

    def archived_trial_artifact_file(request: Request) -> Response:
        """Serve an artifact file from an archived trial (gzip-decompressed)."""
        import gzip as _gzip
        import mimetypes

        aid = request.path_params["archive_id"]
        pid = request.path_params["programme_id"]
        tid = request.path_params["trial_id"]
        filename = request.path_params["filename"]

        if archiver is None:
            return HTMLResponse(render_error("Archiving not enabled", 404), status_code=404)

        trial = archiver.get_archived_trial(pid, tid)
        if trial is None or "error" in trial:
            return HTMLResponse(render_error("Trial not found", 404), status_code=404)

        # Find the artifact in the trial_artifacts list
        for art in trial.get("trial_artifacts", []):
            if art["filename"] == filename:
                # Read the content from the archive DB directly
                entry = store._fetchone(
                    "SELECT archive_path FROM archive_registry WHERE archive_id = ?",
                    (aid,),
                )
                if entry is None:
                    return HTMLResponse(render_error("Archive not found", 404), status_code=404)

                import sqlite3
                conn = sqlite3.connect(entry["archive_path"])
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT content, content_type, size_bytes FROM artifact_files WHERE content_hash = ?",
                    (art["content_hash"],),
                ).fetchone()
                conn.close()

                if row is None:
                    return HTMLResponse(render_error("File content not found", 404), status_code=404)

                data = _gzip.decompress(row["content"])
                content_type = row["content_type"] or "text/plain"
                size = len(data)
                inline_types = (
                    "text/", "application/json", "application/xml",
                    "image/", "application/pdf",
                )
                disposition = "inline" if any(
                    content_type.startswith(t) for t in inline_types
                ) and size < 1_000_000 else "attachment"
                headers = {"Content-Disposition": f'{disposition}; filename="{filename}"'}
                return Response(data, media_type=content_type, headers=headers)

        return HTMLResponse(render_error("File not found in archive", 404), status_code=404)

    # HTMX partials — return HTML fragments, not full pages
    def programme_trials_partial(request: Request) -> HTMLResponse:
        """Partial: trial rows for a programme (for HTMX auto-refresh)."""
        pid = request.path_params["programme_id"]
        return programme_views.render_trials_partial(store, pid)

    def programme_budget_partial(request: Request) -> HTMLResponse:
        """Partial: budget bar for a programme (for HTMX auto-refresh)."""
        pid = request.path_params["programme_id"]
        return programme_views.render_budget_partial(store, pid)

    def programme_belief_partial(request: Request) -> HTMLResponse:
        """Partial: belief summary for a programme (for HTMX auto-refresh)."""
        pid = request.path_params["programme_id"]
        return belief_views.render_belief_partial(store, pid)

    def integrity(request: Request) -> HTMLResponse:
        """Integrity — latest check run + log history (read-only)."""
        return HTMLResponse(
            integrity_views.render_integrity(store, refresh_interval)
        )

    def integrity_log(request: Request) -> HTMLResponse:
        """One day file — every recorded run in it."""
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
        style = ' style="color: var(--status-inconclusive)"' if bad else ""
        return HTMLResponse(
            f'<a href="/integrity" id="integrity-link"{style}'
            ' hx-get="/integrity/status" hx-trigger="every 30s"'
            ' hx-swap="outerHTML">Integrity</a>'
        )

    def not_found(request: Request, exc: Exception) -> HTMLResponse:
        """404 handler."""
        return HTMLResponse(render_error("Not found", 404), status_code=404)

    def method_not_allowed(request: Request) -> Response:
        """405 handler — the GUI is read-only."""
        return Response("Method not allowed — the observability GUI is read-only", status_code=405)

    def favicon(request: Request) -> Response:
        """Serve the Lab Loop favicon for direct /favicon.ico requests."""
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

    def trial_artifact_file(request: Request) -> Response:
        """Serve a single artifact file for a trial (read-only).

        Path: /programme/{pid}/trial/{tid}/artifact/{filename}

        Serves ONLY from SQLite (artifact_files BLOB, gzip-decompressed
        transparently). The on-disk artifacts/ directory is transient
        staging — deleted after capture — so we never read from it.
        Rule 5.4: SQLite is the durable carrier; disk is transient.
        """
        pid = request.path_params["programme_id"]
        tid = request.path_params["trial_id"]
        filename = request.path_params["filename"]

        trial = store.get_trial(tid)
        if trial is None or trial.programme_id != pid:
            return HTMLResponse(render_error("Trial not found", 404), status_code=404)

        # --- Serve from SQLite (content-addressed, gzip-compressed) ---
        artifacts = store.list_trial_artifacts(tid)
        if not artifacts:
            return HTMLResponse(
                render_error(
                    "No artifacts captured for this trial. "
                    "If this trial predates artifact capture, "
                    "call capture_pending_artifacts to migrate.",
                    404,
                ),
                status_code=404,
            )

        for art in artifacts:
            if art["filename"] == filename:
                af = store.get_artifact_file(art["content_hash"])
                if af is not None:
                    data = af["content"]  # already decompressed
                    content_type = af["content_type"] or "text/plain"
                    size = len(data)
                    inline_types = (
                        "text/", "application/json", "application/xml",
                        "image/", "application/pdf",
                    )
                    disposition = "inline" if any(
                        content_type.startswith(t) for t in inline_types
                    ) and size < 1_000_000 else "attachment"
                    headers = {"Content-Disposition": f'{disposition}; filename="{filename}"'}
                    return Response(data, media_type=content_type, headers=headers)

        # Trial has artifacts but not this filename
        return HTMLResponse(
            render_error(
                f"File not found in captured artifacts: {filename}", 404
            ),
            status_code=404,
        )

    routes = [
        Route("/", index),
        Route("/search", search),
        Route("/favicon.ico", favicon),
        Route("/health", health),
        Route("/programme/{programme_id}", programme_detail),
        Route("/programme/{programme_id}/trials", programme_trials_partial),
        Route("/programme/{programme_id}/budget", programme_budget_partial),
        Route("/programme/{programme_id}/trial/{trial_id}", trial_detail),
        Route("/trial/{trial_id}", trial_shortcut),
        Route("/hypothesis/{hypothesis_id}", hypothesis_shortcut),
        Route("/conclusion/{conclusion_id}", conclusion_shortcut),
        Route("/observation/{observation_id}", observation_shortcut),
        Route("/belief/{belief_id}", belief_shortcut),
        Route("/decision/{decision_id}", decision_detail),
        Route("/programme/{programme_id}/trial/{trial_id}/artifact/{filename:path}", trial_artifact_file),
        Route("/programme/{programme_id}/belief", belief_detail),
        Route("/programme/{programme_id}/belief/summary", programme_belief_partial),
        Route("/programme/{programme_id}/conclusions", conclusion_view),
        Route("/dataref/{data_ref_id}", dataref_detail),
        Route("/contract/{contract_id}", contract_detail),
        Route("/integrity", integrity),
        Route("/integrity/help", integrity_help),
        Route("/integrity/status", integrity_status),
        Route("/integrity/log/{name}", integrity_log),
        Route("/integrity/run/{name}/{index}", integrity_run),
        Route("/archives", archive_list),
        Route("/archive/{archive_id}", archive_detail),
        Route("/archive/{archive_id}/programme/{programme_id}", archived_programme_detail),
        Route("/archive/{archive_id}/programme/{programme_id}/trial/{trial_id}", archived_trial_detail),
        Route("/archive/{archive_id}/programme/{programme_id}/trial/{trial_id}/artifact/{filename:path}", archived_trial_artifact_file),
        Mount("/static", app=StaticFiles(directory=str(static_dir)), name="static"),
    ]

    app = Starlette(
        routes=routes,
        exception_handlers={404: not_found},
    )
    app.state.store = store
    app.state.refresh_interval = refresh_interval

    return app
