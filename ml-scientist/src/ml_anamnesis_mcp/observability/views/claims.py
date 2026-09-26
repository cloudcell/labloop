"""Claim views for the anamnesis observability GUI — read-only renders."""

from __future__ import annotations

from starlette.responses import HTMLResponse

from ...state.models import EVIDENCE_RELATIONS, Claim
from ...state.store import MemoryStore
from ..templates import (
    PEER_GUI_URLS,
    escape,
    format_timestamp,
    render_base,
    render_error,
)

_REL_EVIDENCE = EVIDENCE_RELATIONS


def _claim_status(claim: Claim, store: MemoryStore) -> str:
    """Derive display status: live | superseded | expired."""
    if claim.valid_until is not None:
        return "expired"
    superseded = store._fetchone(
        "SELECT 1 FROM claim_edges WHERE to_ref = ? AND relation = 'supersedes' "
        "UNION SELECT 1 FROM claims WHERE supersedes_id = ? LIMIT 1",
        (claim.id, claim.id),
    )
    return "superseded" if superseded else "live"


def _rel_badge(relation: str) -> str:
    cls = "rel-badge"
    if relation == "contradicts":
        cls += " rel-contradicts"
    elif relation in _REL_EVIDENCE:
        cls += " rel-evidence"
    return f'<span class="{cls}">{escape(relation)}</span>'


# ID prefix → (owning server, GUI detail path). Only prefixes with a
# real detail page on a single unambiguous owner are mapped — mdec-*,
# pol-*, hyp-*, obs-*, conc-* and friends have no page to link to and
# stay opaque.
_PEER_REF_ROUTES = (
    ("mcontract-", "arete", "/contract/"),
    ("tourn-", "arete", "/tournament/"),
    ("imp-", "arete", "/improver/"),
    ("mcp-", "arete", "/proposal/"),
    ("cand-", "zetesis", "/candidate/"),
    ("camp-", "zetesis", "/campaign/"),
    ("inv-", "zetesis", "/investigation/"),
    ("trial-", "episteme", "/trial/"),
    ("prog-", "episteme", "/programme/"),
    ("contract-", "episteme", "/contract/"),
    ("archive-", "episteme", "/archive/"),
)

# eref-* is minted by BOTH arete and zetesis — the id alone can't name
# the owner, and the citing edge's source_id is no oracle either (a
# decision on arete can cite an eref that surfaced inside a zetesis
# pull's ref_ids). So eref refs route through the local /ref/ resolver,
# which probes each candidate owner's /evidence-ref/ page at click
# time and redirects to whichever actually has the record.
_EREF_OWNER_CANDIDATES = ("arete", "zetesis")


def _ref_link(to_ref: str, ref_type: str) -> str:
    """claim refs link locally; known peer prefixes link cross-GUI."""
    if ref_type == "claim":
        return f'<a class="mono" href="/claim/{escape(to_ref)}">{escape(to_ref)}</a>'
    if to_ref.startswith("eref-"):
        return (
            f'<a class="mono" href="/ref/{escape(to_ref)}" '
            f'title="resolves to the owning GUI">'
            f"{escape(to_ref)}</a>"
        )
    for prefix, server, path in _PEER_REF_ROUTES:
        if to_ref.startswith(prefix):
            if base := PEER_GUI_URLS.get(server):
                href = f"{escape(base)}{path}{escape(to_ref)}"
                return (
                    f'<a class="mono" href="{href}" '
                    f'title="{escape(server)} GUI">{escape(to_ref)}</a>'
                )
            break
    return f'<span class="mono">{escape(to_ref)}</span>'


def resolve_ref_redirect(ref_id: str) -> HTMLResponse:
    """Click-time owner resolution for ambiguous refs (eref-*).

    Probes each candidate peer GUI's /evidence-ref/ page; redirects to
    the first that actually serves the record. If none do — the record
    is gone or every candidate is down — render an honest miss page
    naming what was tried. Never guess.
    """
    from urllib.parse import quote

    import httpx
    from starlette.responses import RedirectResponse

    tried: list[tuple[str, str | None]] = []
    if ref_id.startswith("eref-"):
        for server in _EREF_OWNER_CANDIDATES:
            base = PEER_GUI_URLS.get(server)
            if not base:
                tried.append((server, None))
                continue
            url = f"{base}/evidence-ref/{quote(ref_id)}"
            try:
                if httpx.get(url, timeout=2.0).status_code == 200:
                    return RedirectResponse(url)
                tried.append((server, url))
            except Exception:
                tried.append((server, url))
    tried_rows = "".join(
        f"<tr><td>{escape(s)}</td>"
        f'<td class="mono muted">{escape(u or "no GUI URL configured")}</td></tr>'
        for s, u in tried
    ) or '<tr><td colspan="2" class="muted">no candidates</td></tr>'
    body = f"""
    <h1><span class="mono">{escape(ref_id)}</span>
        <span class="status status-failed">unresolved</span></h1>
    <div class="card">
        <p>This reference could not be resolved to a live detail page
        on any candidate server — the record may be gone, or every
        owning GUI may be down or unconfigured.</p>
        <table><thead><tr><th>Candidate</th><th>Tried</th></tr></thead>
        <tbody>{tried_rows}</tbody></table>
    </div>
    """
    return HTMLResponse(render_base(f"Ref {ref_id}", body), status_code=404)


def _confidence(conf: float) -> str:
    pct = int(conf * 100)
    return (
        f'<span class="confidence-bar"><span class="confidence-fill" '
        f'style="width: {pct}%"></span></span>{conf:.2f}'
    )


def _claim_row(claim: Claim, store: MemoryStore) -> str:
    status = _claim_status(claim, store)
    content = claim.content
    short = content if len(content) <= 110 else content[:107] + "…"
    return (
        "<tr>"
        f'<td><a href="/claim/{escape(claim.id)}">{escape(short)}</a></td>'
        f'<td><span class="status status-{escape(claim.type.value)}">'
        f"{escape(claim.type.value)}</span></td>"
        f"<td>{_confidence(claim.confidence)}</td>"
        f"<td>{claim.importance:.2f}</td>"
        f'<td><span class="status status-{status}">{status}</span></td>'
        f"<td>{format_timestamp(claim.created_at)}</td>"
        "</tr>"
    )


def _claims_table(claims: list[Claim], store: MemoryStore) -> str:
    if not claims:
        return (
            '<div class="empty-state"><p>No claims match.</p>'
            "<p>Claims arrive via <code>assert_claim</code>.</p></div>"
        )
    rows = "".join(_claim_row(c, store) for c in claims)
    return (
        "<table><thead><tr>"
        "<th>Claim</th><th>Type</th><th>Confidence</th>"
        "<th>Importance</th><th>Status</th><th>Asserted</th>"
        "</tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def render_claim_list(
    store: MemoryStore,
    view: str = "live",
    claim_type: str | None = None,
) -> HTMLResponse:
    """Landing page: stats, view tabs, type tabs, search box, table."""
    stats = store.stats()

    view_args = {
        "live": {},
        "all": {"include_superseded": True, "include_expired": True},
        "superseded": {"only_superseded": True},
        "expired": {"only_expired": True},
    }
    claims, _ = store.list_claims(
        type=claim_type, limit=200, **view_args.get(view, {})
    )

    def tab(label: str, v: str, base: str) -> str:
        active = " active" if v == view else ""
        t = f"&type={claim_type}" if claim_type else ""
        return f'<a class="filter-tab{active}" href="{base}{t}">{label}</a>'

    view_tabs = "".join(
        tab(label, v, f"/?view={v}")
        for label, v in [
            (f"Live ({stats['live']})", "live"),
            (f"Superseded ({stats['superseded']})", "superseded"),
            (f"Expired ({stats['expired']})", "expired"),
            (f"All ({stats['total']})", "all"),
        ]
    )
    type_tabs = "".join(
        f'<a class="filter-tab{" active" if claim_type == t else ""}" '
        f'href="/?view={view}&type={t}">{t}</a>'
        for t in ("empirical", "methodological")
    )
    type_tabs += (
        f'<a class="filter-tab{" active" if claim_type is None else ""}" '
        f'href="/?view={view}">any type</a>'
    )

    body = f"""
    <h1>Claims Memory</h1>
    <div class="stat-grid">
        <span class="stat"><b>{stats['total']}</b> claims</span>
        <span class="stat"><b>{stats['live']}</b> live</span>
        <span class="stat"><b>{stats['edges']}</b> edges</span>
        <span class="stat"><b>{stats['by_type'].get('empirical', 0)}</b> empirical</span>
        <span class="stat"><b>{stats['by_type'].get('methodological', 0)}</b> methodological</span>
    </div>
    <div class="filter-tabs">{view_tabs}<span class="muted">·</span>{type_tabs}
        <input class="search-box" type="search" name="q"
               placeholder="Search claims…"
               hx-get="/search" hx-trigger="keyup changed delay:300ms, search"
               hx-target="#claims-table" hx-swap="innerHTML"
               hx-include="this">
    </div>
    <div id="claims-table">{_claims_table(claims, store)}</div>
    """
    return HTMLResponse(render_base("Claims", body))


def render_search_results(store: MemoryStore, q: str) -> HTMLResponse:
    """HTMX fragment: filtered claims table (search box target)."""
    claims = store.search_claims(q, limit=100) if q else []
    if not q:
        claims, _ = store.list_claims(limit=200)
    return HTMLResponse(_claims_table(claims, store))


def render_claim_detail(store: MemoryStore, claim_id: str) -> HTMLResponse:
    """Claim detail: fields + outgoing/incoming provenance edges."""
    claim = store.get_claim(claim_id)
    if claim is None:
        return HTMLResponse(
            render_error(f"Claim not found: {claim_id}", 404), status_code=404
        )

    status = _claim_status(claim, store)
    out_edges = store.edges_from(claim_id)
    in_edges = store.edges_to(claim_id)

    out_rows = "".join(
        "<tr>"
        f"<td>{_rel_badge(e.relation.value)}</td>"
        f"<td>{_ref_link(e.to_ref, e.ref_type.value)}</td>"
        f'<td class="muted">{escape(e.ref_type.value)}</td>'
        f"<td>{e.weight:.2f}</td>"
        f"<td>{format_timestamp(e.created_at)}</td>"
        "</tr>"
        for e in out_edges
    ) or '<tr><td colspan="5" class="muted">No outgoing edges — this claim cites nothing.</td></tr>'

    in_rows = "".join(
        "<tr>"
        f'<td><a class="mono" href="/claim/{escape(e.from_claim)}">{escape(e.from_claim)}</a></td>'
        f"<td>{_rel_badge(e.relation.value)}</td>"
        f"<td>{e.weight:.2f}</td>"
        f"<td>{format_timestamp(e.created_at)}</td>"
        "</tr>"
        for e in in_edges
    ) or '<tr><td colspan="4" class="muted">Nothing cites this claim yet.</td></tr>'

    superseded_line = ""
    if claim.supersedes_id:
        superseded_line = (
            f'<p><span class="muted">supersedes:</span> '
            f'<a class="mono" href="/claim/{escape(claim.supersedes_id)}">'
            f"{escape(claim.supersedes_id)}</a></p>"
        )

    body = f"""
    <h1><span class="mono">{escape(claim.id)}</span></h1>
    <div class="card">
        <p style="font-size: 1.05rem; margin-bottom: 0.75rem">{escape(claim.content)}</p>
        <p>
            <span class="status status-{escape(claim.type.value)}">{escape(claim.type.value)}</span>
            <span class="status status-{status}">{status}</span>
        </p>
        <p style="margin-top: 0.5rem">
            confidence {_confidence(claim.confidence)}
            &nbsp;·&nbsp; importance {claim.importance:.2f}
        </p>
        <p class="muted" style="margin-top: 0.5rem">
            valid from {format_timestamp(claim.valid_from)}
            {('until ' + format_timestamp(claim.valid_until)) if claim.valid_until else '(live)'}
            &nbsp;·&nbsp; asserted {format_timestamp(claim.created_at)}
            {('&nbsp;·&nbsp; source ' + escape(claim.source_id)) if claim.source_id else ''}
        </p>
        {superseded_line}
    </div>
    <div class="card">
        <h2>Cites ({len(out_edges)})</h2>
        <table><thead><tr><th>Relation</th><th>Reference</th>
        <th>Type</th><th>Weight</th><th>When</th></tr></thead>
        <tbody>{out_rows}</tbody></table>
    </div>
    <div class="card">
        <h2>Cited by ({len(in_edges)})</h2>
        <table><thead><tr><th>Claim</th><th>Relation</th>
        <th>Weight</th><th>When</th></tr></thead>
        <tbody>{in_rows}</tbody></table>
    </div>
    """
    return HTMLResponse(render_base(f"Claim {claim_id}", body))
