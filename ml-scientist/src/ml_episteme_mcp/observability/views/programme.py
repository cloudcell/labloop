"""Programme views — list and detail."""

from __future__ import annotations

import json
from typing import Any

from starlette.responses import HTMLResponse

from ...state.store import StateStore
from ..templates import (
    escape,
    format_timestamp,
    render_base,
    render_budget_bar,
    render_json_pretty,
    render_status_badge,
    status_class,
)


def _render_pagination(page: int, total_pages: int, base_url: str = "/") -> str:
    """Render pagination controls (Prev / page numbers / Next)."""
    if total_pages <= 1:
        return ""

    def page_link(p: int, label: str, cls: str = "") -> str:
        sep = "&" if "?" in base_url else "?"
        return f'<a class="page-link {cls}" href="{base_url}{sep}page={p}">{label}</a>'

    links = []
    # Prev
    if page > 1:
        links.append(page_link(page - 1, "&larr; Prev", "page-nav"))
    else:
        links.append('<span class="page-link page-nav disabled">&larr; Prev</span>')

    # Page numbers (show up to 7 around current)
    start = max(1, page - 3)
    end = min(total_pages, page + 3)
    if start > 1:
        links.append(page_link(1, "1"))
        if start > 2:
            links.append('<span class="page-ellipsis">...</span>')
    for p in range(start, end + 1):
        cls = "page-current" if p == page else ""
        links.append(page_link(p, str(p), cls))
    if end < total_pages:
        if end < total_pages - 1:
            links.append('<span class="page-ellipsis">...</span>')
        links.append(page_link(total_pages, str(total_pages)))

    # Next
    if page < total_pages:
        links.append(page_link(page + 1, "Next &rarr;", "page-nav"))
    else:
        links.append('<span class="page-link page-nav disabled">Next &rarr;</span>')

    return f'<div class="pagination">{" ".join(links)}</div>'


def render_search_results(
    store: StateStore,
    query: str,
    archiver=None,
) -> HTMLResponse:
    """Render search results across live and archived programmes.

    Returns an HTML fragment (for HTMX) with matching programmes from
    both the live DB and the archive_entries table.
    """
    q = query.lower().strip()
    if not q:
        return HTMLResponse("")

    results_html = []

    # Search live programmes
    live_rows = store._fetchall(
        "SELECT * FROM programmes ORDER BY created_at DESC"
    )
    for row in live_rows:
        goal = row["goal"]
        pid = row["id"]
        status = row["status"]
        if q in goal.lower() or q in pid.lower():
            direction = row["metric_direction"]
            card_class = "card card-active" if status == "active" else "card card-inactive"
            results_html.append(f"""
            <a class="{card_class}" href="/programme/{escape(pid)}" data-status="{escape(status)}">
                <h3>{escape(goal)}</h3>
                <div>
                    {render_status_badge(status)}
                    <span class="direction">{escape(direction)}</span>
                </div>
                <div class="muted" style="font-size: 0.8rem; margin-top: 0.25rem">{escape(pid)}</div>
            </a>""")

    # Search archived programmes
    if archiver is not None:
        try:
            archive_rows = store._fetchall(
                "SELECT * FROM archive_entries ORDER BY archived_at DESC"
            )
            for row in archive_rows:
                goal = row["programme_goal"]
                pid = row["programme_id"]
                archive_id = row["archive_id"]
                if q in goal.lower() or q in pid.lower():
                    results_html.append(f"""
                    <a class="card card-archived" href="/archive/{escape(archive_id)}/programme/{escape(pid)}" data-status="archived" data-goal="{escape(goal.lower())}">
                        <h3>{escape(goal)}</h3>
                        <div>
                            <span class="status status-completed">Archived</span>
                        </div>
                        <div class="muted" style="font-size: 0.8rem; margin-top: 0.25rem">{escape(pid)}</div>
                    </a>""")
        except Exception:
            pass  # archive_entries table might not exist yet

    if not results_html:
        return HTMLResponse('<p class="muted" style="grid-column: 1/-1">No programmes found.</p>')

    return HTMLResponse("".join(results_html))


_PROGRAMME_STATUSES = ("active", "completed", "abandoned", "archived")


def render_programme_list(
    store: StateStore,
    refresh_interval: int = 0,
    page: int = 1,
    per_page: int = 50,
    archiver=None,
    sort: str = "created_desc",
    status: str | None = None,
) -> HTMLResponse:
    """Render the programme list (landing page) with pagination + sort."""
    if status not in _PROGRAMME_STATUSES:
        status = None

    # Get total counts for filter tabs (all statuses)
    total = store.count_programmes()
    if total == 0:
        body = """
        <h1>Programmes</h1>
        <div class="empty-state">
            <p>No programmes yet.</p>
            <p style="margin-top: 1rem">Create one using the <code>create_programme</code> MCP tool,
            or run <code>uv run python scripts/e2e_demo.py</code> for a demo.</p>
        </div>
        """
        return HTMLResponse(render_base("Programmes", body))

    # Count by status for filter tabs
    active_count = store.count_programmes("active")
    completed_count = store.count_programmes("completed")
    abandoned_count = store.count_programmes("abandoned")
    archived_count = store.count_programmes("archived")

    # Status filtering is server-side: with pagination, a client-side
    # filter can only hide cards on the current page — matches on other
    # pages stay invisible and a page can render empty despite having
    # matches (the "empty Active tab" bug). The filtered total drives
    # pagination; the unfiltered counts still label the tabs.
    filtered_total = (
        store.count_programmes(status) if status else total
    )
    rows = store.list_programmes_paginated(
        page=page, per_page=per_page, sort=sort, status_filter=status,
    )

    # Build filter tabs + sort controls. Filter tabs are real links so
    # the filter is a URL — shareable, back-button friendly, and it
    # applies across pages, not just to the cards currently rendered.
    def _url(s: str | None = status, so: str = sort) -> str:
        qs = f"sort={so}"
        if s:
            qs += f"&status={s}"
        return f"/?{qs}"

    status_tabs = [("All", None, total), ("Active", "active", active_count),
                   ("Completed", "completed", completed_count),
                   ("Abandoned", "abandoned", abandoned_count),
                   ("Archived", "archived", archived_count)]
    status_links = "".join(
        f'<a class="filter-tab{" active" if status == s else ""}" '
        f'href="{_url(s)}">{label} ({n})</a>'
        for label, s, n in status_tabs
    )
    sort_options = [
        ("activity_desc", "Latest activity ↓"),
        ("activity_asc", "Latest activity ↑"),
        ("created_desc", "Newest"),
    ]
    sort_links = "".join(
        f'<a class="filter-tab{" active" if sort == s else ""}" '
        f'href="{_url(so=s)}">{label}</a>'
        for s, label in sort_options
    )
    filter_tabs = f"""
    <div class="filter-tabs" id="filter-tabs">
        {status_links}
        <span style="flex: 1"></span>
        <span class="muted" style="align-self: center; font-size: 0.8rem">Sort:</span>
        {sort_links}
    </div>
    <input type="text" class="search-box" id="programme-search" placeholder="Search live + archived programmes..."
           hx-get="/search" hx-trigger="input changed delay:300ms, search" hx-target="#programme-grid" hx-swap="innerHTML" />
    """

    cards = []
    for row in rows:
        pid = row["id"]
        goal = row["goal"]
        status = row["status"]
        direction = row["metric_direction"]

        # Count trials
        trials = store.list_trials(pid)
        completed = [t for t in trials if t.status.value == "completed"]
        budget = row["budget_max_trials"]
        used_wall = sum((t.duration_seconds or 0) for t in trials) / 3600.0
        budget_wall = row["budget_max_wall_time_hours"]

        # Visual hierarchy: active cards get a left border, inactive are dimmed
        card_class = "card"
        if status == "active":
            card_class += " card-active"
        elif status == "archived":
            card_class += " card-archived"
        else:
            card_class += " card-inactive"

        # Archived programmes link to the archive copy (authoritative)
        if status == "archived" and archiver is not None:
            entry = store._fetchone(
                "SELECT archive_id FROM archive_entries WHERE programme_id = ?",
                (pid,),
            )
            if entry is not None:
                card_href = f"/archive/{escape(entry['archive_id'])}/programme/{escape(pid)}"
            else:
                card_href = f"/programme/{escape(pid)}"
        else:
            card_href = f"/programme/{escape(pid)}"

        # C1 fix: make the card a real <a> link, not a broken hx-get
        card = f"""
        <a class="{card_class}" href="{card_href}" data-status="{escape(status)}" data-goal="{escape(goal.lower())}">
            <h3>{escape(goal)}</h3>
            <div>
                {render_status_badge(status)}
                <span class="direction">{escape(direction)}</span>
            </div>
            {render_budget_bar(len(completed), len(completed), len(trials), budget, used_wall, budget_wall)}
            <div class="muted" style="font-size: 0.8rem; margin-top: 0.25rem">
                {escape(pid)}
            </div>
            <div class="muted" style="font-size: 0.75rem; margin-top: 0.5rem; text-align: right">
                Last activity: {format_timestamp(row.get("last_activity"))}
            </div>
        </a>"""
        cards.append(card)

    # Pagination controls (preserve sort + status across pages)
    total_pages = (filtered_total + per_page - 1) // per_page
    pagination = _render_pagination(page, total_pages, base_url=_url())

    empty = ""
    if not rows:
        empty = (
            f'<div class="empty-state"><p>No {escape(status)} '
            "programmes.</p></div>"
        )

    body = f"""
    <h1>Programmes</h1>
    {filter_tabs}
    <div class="grid" id="programme-grid" style="grid-template-columns: repeat(auto-fill, minmax(300px, 1fr))">
        {''.join(cards)}
    </div>
    {empty}
    {pagination}
    <script>
    // Search behaviour only — status filtering is server-side (real
    // links), so there is no client-side card hiding to keep in sync.
    (function() {{
        const search = document.getElementById('programme-search');
        const pagination = document.querySelector('.pagination');

        // When search is cleared, reload the page to restore original content
        if (search) {{
            search.addEventListener('input', (e) => {{
                if (!e.target.value) {{
                    window.location.reload();
                }} else {{
                    // Hide pagination while searching
                    if (pagination) pagination.style.display = 'none';
                }}
            }});
        }}
    }})();
    </script>
    """
    return HTMLResponse(render_base("Programmes", body, refresh_interval))


def render_programme_detail(
    store: StateStore,
    programme_id: str,
    refresh_interval: int = 0,
    archiver=None,
) -> HTMLResponse:
    """Render the programme detail page.

    If the programme is not in the live DB but is archived, redirect
    to the archived programme view instead of showing a 404.
    """
    programme = store.get_programme(programme_id)
    if programme is None:
        # Check if the programme is archived
        if archiver is not None:
            entry = store._fetchone(
                "SELECT * FROM archive_entries WHERE programme_id = ?",
                (programme_id,),
            )
            if entry is not None:
                archive_id = entry["archive_id"]
                # Redirect to the archived programme view
                from starlette.responses import RedirectResponse
                return RedirectResponse(
                    url=f"/archive/{archive_id}/programme/{programme_id}",
                    status_code=302,
                )
        return HTMLResponse(
            render_base("Not Found", f'<h1 style="color: var(--status-failed)">Programme not found: {escape(programme_id)}</h1>'),
            status_code=404,
        )

    # If the programme is archived, show a banner linking to the
    # authoritative archive copy (no-purge: the live DB row is a backup).
    archive_banner = ""
    if programme.status.value == "archived" and archiver is not None:
        entry = store._fetchone(
            "SELECT * FROM archive_entries WHERE programme_id = ?",
            (programme_id,),
        )
        if entry is not None:
            archive_id = entry["archive_id"]
            archive_url = f"/archive/{escape(archive_id)}/programme/{escape(programme_id)}"
            archive_banner = f"""
            <div class="archive-banner" style="background: var(--bg-card); border-left: 3px solid var(--status-completed); padding: 0.75rem 1rem; margin-bottom: 1rem; border-radius: 4px;">
                <strong>This programme is archived.</strong>
                The archive copy is the authoritative durable record.
                <a href="{archive_url}">View archive copy →</a>
            </div>"""

    hypotheses = store.list_hypotheses(programme_id)
    trials = store.list_trials(programme_id)
    conclusions = store.list_conclusions(programme_id)

    completed_trials = [t for t in trials if t.status.value == "completed"]
    terminal_trials = [
        t for t in trials if t.status.value in ("completed", "failed", "retryable")
    ]
    used_wall_hours = sum(
        (t.duration_seconds or 0) for t in trials
    ) / 3600.0

    # Hypotheses section
    hyp_rows = []
    for h in hypotheses:
        hyp_rows.append(f"""
        <tr id="hyp-{escape(h.id)}">
            <td><a href="/programme/{escape(programme_id)}#hyp-{escape(h.id)}">{escape(h.id)}</a></td>
            <td>{escape(h.statement[:200])}{'...' if len(h.statement) > 200 else ''}</td>
            <td>{render_status_badge(h.status.value)}</td>
        </tr>""")

    # C5: find the best trial from the belief state
    best_trial_id = None
    beliefs = store.list_beliefs(programme_id)
    if beliefs:
        latest_belief = beliefs[-1]
        if latest_belief and latest_belief.state_json:
            import json as _json
            try:
                belief_state = _json.loads(latest_belief.state_json)
                best_trials = belief_state.get("best_trials", [])
                if best_trials:
                    best_item = best_trials[0] if isinstance(best_trials, list) else best_trials
                    if isinstance(best_item, dict):
                        best_trial_id = best_item.get("trial_id")
            except Exception:
                pass

    # Trials section (as partial for auto-refresh)
    trials_section = _render_trials_table(programme_id, trials, best_trial_id)

    # Conclusions section
    conc_rows = []
    for c in conclusions:
        conc_rows.append(f"""
        <tr>
            <td>{escape(c.id)}</td>
            <td>{render_status_badge(c.verdict.value)}</td>
            <td>{escape(c.evidence_summary[:150])}{'...' if len(c.evidence_summary) > 150 else ''}</td>
            <td>{format_timestamp(c.created_at)}</td>
        </tr>""")

    body = f"""
    {archive_banner}
    <h1>{escape(programme.goal)}</h1>
    <div class="muted" style="margin-bottom: 1rem">{escape(programme_id)}</div>

    <div class="grid grid-2" style="margin-bottom: 1.5rem">
        <div class="card">
            <h3>Status</h3>
            {render_status_badge(programme.status.value)}
            <span class="direction" style="margin-left: 1rem">
                direction: <strong>{escape(programme.metric_direction)}</strong>
            </span>
        </div>
        <div class="card">
            <h3>Budget</h3>
            <div id="budget-section"
                 {"hx-get=\"/programme/" + escape(programme_id) + "/budget\" hx-trigger=\"every " + str(refresh_interval) + "s\" hx-target=\"this\"" if refresh_interval > 0 else ""}>
                {render_budget_bar(len(completed_trials), len(terminal_trials), len(trials), programme.budget_max_trials, used_wall_hours, programme.budget_max_wall_time_hours)}
            </div>
        </div>
    </div>

    <div class="section">
        <h2>Constraints</h2>
        {render_json_pretty(programme.constraints)}
    </div>

    <div class="section">
        <h2>Allowed Variables</h2>
        <pre>{escape(', '.join(programme.allowed_variables))}</pre>
    </div>

    <div class="section">
        <h2>Hypotheses</h2>
        <table>
            <thead><tr><th>ID</th><th>Statement</th><th>Status</th></tr></thead>
            <tbody>
                {''.join(hyp_rows) if hyp_rows else '<tr><td colspan="3" class="muted">No hypotheses yet.</td></tr>'}
            </tbody>
        </table>
    </div>

    <div class="section" id="trials-section"
         {"hx-get=\"/programme/" + escape(programme_id) + "/trials\" hx-trigger=\"every " + str(refresh_interval) + "s\" hx-target=\"this\"" if refresh_interval > 0 else ""}>
        {trials_section}
    </div>

    <div class="section">
        <h2>Conclusions</h2>
        <table>
            <thead><tr><th>ID</th><th>Verdict</th><th>Evidence</th><th>Created</th></tr></thead>
            <tbody>
                {''.join(conc_rows) if conc_rows else '<tr><td colspan="4" class="muted">No conclusions yet.</td></tr>'}
            </tbody>
        </table>
    </div>
    """
    return HTMLResponse(render_base(f"Programme {programme_id}", body, refresh_interval))


def render_trials_partial(store: StateStore, programme_id: str) -> HTMLResponse:
    """Render just the trials table (for HTMX auto-refresh)."""
    trials = store.list_trials(programme_id)
    # C5: find the best trial from the belief state
    best_trial_id = None
    beliefs = store.list_beliefs(programme_id)
    if beliefs:
        latest_belief = beliefs[-1]
        if latest_belief and latest_belief.state_json:
            try:
                belief_state = json.loads(latest_belief.state_json)
                best_trials = belief_state.get("best_trials", [])
                if best_trials:
                    best_item = best_trials[0] if isinstance(best_trials, list) else best_trials
                    if isinstance(best_item, dict):
                        best_trial_id = best_item.get("trial_id")
            except Exception:
                pass
    return HTMLResponse(_render_trials_table(programme_id, trials, best_trial_id))


def render_budget_partial(store: StateStore, programme_id: str) -> HTMLResponse:
    """Render just the budget bar (for HTMX auto-refresh)."""
    programme = store.get_programme(programme_id)
    if programme is None:
        return HTMLResponse("")
    trials = store.list_trials(programme_id)
    completed = [t for t in trials if t.status.value == "completed"]
    terminal = [
        t for t in trials
        if t.status.value in ("completed", "failed", "retryable")
    ]
    used_wall_hours = sum((t.duration_seconds or 0) for t in trials) / 3600.0
    return HTMLResponse(
        render_budget_bar(
            len(completed), len(terminal), len(trials),
            programme.budget_max_trials,
            used_wall_hours, programme.budget_max_wall_time_hours,
        )
    )


def _render_trials_table(programme_id: str, trials: list, best_trial_id: str | None = None) -> str:
    """Render the trials table HTML.

    Args:
        programme_id: The programme ID.
        trials: List of Trial objects.
        best_trial_id: If set, highlight this trial row (C5: best-trial highlight).
    """
    if not trials:
        return "<h2>Trials</h2><p class=\"muted\">No trials yet.</p>"

    rows = []
    for t in trials:
        config = json.loads(t.config_json) if t.config_json else {}
        # C3: show up to 5 config items, prioritizing common keys
        priority_keys = ["arch_family", "trial_name", "n_layers", "d_model", "lr", "learning_rate", "seed", "seeds"]
        ordered = sorted(config.items(), key=lambda x: (0 if x[0] in priority_keys else 1, x[0]))
        config_summary = ", ".join(f"{k}={v}" for k, v in ordered[:5])
        if len(config) > 5:
            config_summary += "..."

        # C5: highlight best trial
        row_class = ' class="row-best"' if t.id == best_trial_id else ""
        best_badge = ' <span class="status status-best">best</span>' if t.id == best_trial_id else ""

        rows.append(f"""
        <tr{row_class}>
            <td><a href="/programme/{escape(programme_id)}/trial/{escape(t.id)}">{escape(t.id)}</a>{best_badge}</td>
            <td>{escape(config_summary)}</td>
            <td>{render_status_badge(t.status.value)}</td>
            <td>{format_timestamp(t.created_at)}</td>
        </tr>""")

    return f"""
    <h2>Trials</h2>
    <table>
        <thead><tr><th>ID</th><th>Config</th><th>Status</th><th>Created</th></tr></thead>
        <tbody>
            {''.join(rows)}
        </tbody>
    </table>
    """
