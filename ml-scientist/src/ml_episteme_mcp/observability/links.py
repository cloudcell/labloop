"""Entity-id → GUI link resolution (episteme-local).

Same lab convention as the other GUIs: an entity id's prefix names
the owning server. Loop-0 ids resolve through this server's own
routes; peer ids through the configured peer GUI bases
([observability] <peer>_gui_url → PEER_GUI_URLS). Only kinds with a
real page link — anything else renders unlinked rather than
guessing an address. Kept local to episteme: the packages stay
independent, so each server carries its own copy of the convention.
"""

from __future__ import annotations

import re

from .templates import PEER_GUI_URLS, escape

# Loop-0 ids — this server's own routes (relative links). The
# shortcuts resolve context-bound entities (bundle → owning trial,
# archived rows → their archive page) so callers never need the
# nesting path.
_LOCAL_ROUTES = (
    ("trial-", "/trial/"),
    ("bundle-", "/bundle/"),
    ("prog-", "/programme/"),
    ("hyp-", "/hypothesis/"),
    ("obs-", "/observation/"),
    ("conc-", "/conclusion/"),
    ("contract-", "/contract/"),
    ("archive-", "/archive/"),
    ("belief-", "/belief/"),
    ("decision-", "/decision/"),
    ("data-ref-", "/dataref/"),
    # Candidates are minted here by register_candidate — the zetesis
    # roster adopts them upstream, so cand- resolves locally.
    ("cand-", "/candidate/"),
)

# Downstream ids — zetesis (loop1) and anamnesis (claims) mint these;
# they resolve only when the peer GUI is configured.
_PEER_ROUTES = (
    ("camp-", "zetesis", "/campaign/"),
    ("inv-", "zetesis", "/investigation/"),
    ("find-", "zetesis", "/finding/"),
    ("spawn-", "zetesis", "/spawn/"),
    ("cres-", "zetesis", "/campaign-result/"),
    ("spol-", "zetesis", "/search-policy/"),
    ("claim-", "anamnesis", "/claim/"),
    # Edges are anamnesis-local rows — its /edge/ resolver finds the
    # owning claim and redirects to the edge's anchor there.
    ("edge-", "anamnesis", "/edge/"),
    # eref- is minted by BOTH zetesis and arete — the anamnesis /ref/
    # resolver probes both owners' /evidence-ref/ pages and redirects
    # to the real one.
    ("eref-", "anamnesis", "/ref/"),
)


def entity_url(entity_id: str) -> str | None:
    """An entity id → its detail page, local or on a configured peer."""
    for prefix, path in _LOCAL_ROUTES:
        if entity_id.startswith(prefix):
            return f"{path}{entity_id}"
    for prefix, peer, path in _PEER_ROUTES:
        if entity_id.startswith(prefix):
            base = PEER_GUI_URLS.get(peer)
            return f"{base}{path}{entity_id}" if base else None
    return None


def link_id(entity_id: str | None) -> str:
    """An entity id, HTML-linked when a page exists — plain otherwise."""
    if not entity_id:
        return '<span class="muted">—</span>'
    mono = f'<span class="mono">{escape(entity_id)}</span>'
    url = entity_url(entity_id)
    return f'<a href="{escape(url)}">{mono}</a>' if url else mono


def link_ids(entity_ids: list[str]) -> str:
    """A comma-joined id list with each id linked where possible."""
    return ", ".join(link_id(r) for r in entity_ids) or "—"


# Matches lab entity ids inside already-escaped text (id chars are
# escape-safe) — same convention as agora's _linkify.
_ID_RE = re.compile(
    r"\b("
    + "|".join(
        re.escape(p) for p, *_ in (*_LOCAL_ROUTES, *_PEER_ROUTES)
    )
    + r")[a-z0-9]+"
)


def linkify(text: str | None) -> str:
    """Escape prose, then link every recognizable entity id in it.

    For free-text fields that cite entities (evidence summaries,
    rationales) — not for JSON/pre blocks, which must stay verbatim.
    """
    if not text:
        return ""
    return _ID_RE.sub(lambda m: link_id(m.group(0)), escape(text))
