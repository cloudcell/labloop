"""Upstream entity-id → owning-GUI link resolution (arete-local).

Same lab convention as the other GUIs: an entity id's prefix names
the owning server, and its GUI base is the upstream MCP target's
port + 1. Kept local to arete — the packages stay independent, so
each server carries its own copy of the convention rather than
importing another's internals.
"""

from __future__ import annotations


def gui_base(target: str | None) -> str | None:
    """MCP target URL → GUI base (port + 1, the lab convention)."""
    if not target or "://" not in target:
        return None
    try:
        scheme, rest = target.split("://", 1)
        host, _, tail = rest.partition(":")
        port = int(tail.split("/")[0])
        return f"{scheme}://{host}:{port + 1}"
    except (ValueError, IndexError):
        return None


def upstream_gui_bases(channels: dict) -> dict:
    """{channel_name: gui_base} from the adaptors' ChannelSpec map."""
    return {
        name: base
        for name, spec in channels.items()
        if (base := gui_base(getattr(spec, "target", None)))
    }


# id prefix → detail path. Local prefixes resolve through this
# server's own routes; upstream prefixes through the adaptor-derived
# GUI bases ({channel: gui_base}). Only kinds with a real page are
# mapped — anything else returns None and the caller renders the id
# unlinked rather than guessing.
_LOCAL_ROUTES = (
    ("tourn-", "/tournament/"),
    ("imp-", "/improver/"),
    ("mcp-", "/proposal/"),
    ("mcontract-", "/contract/"),
    ("tcamp-", "/campaign-link/"),
    ("mdec-", "/decision/"),
    ("pol-", "/policy/"),
    ("canary-", "/canary/"),
    ("tres-", "/result/"),
)

_UPSTREAM_ROUTES = (
    # zetesis (loop1)
    ("cand-", "loop1", "/candidate/"),
    ("camp-", "loop1", "/campaign/"),
    ("inv-", "loop1", "/investigation/"),
    ("find-", "loop1", "/finding/"),
    ("spawn-", "loop1", "/spawn/"),
    ("cres-", "loop1", "/campaign-result/"),
    ("spol-", "loop1", "/search-policy/"),
    # episteme (loop0)
    ("trial-", "loop0", "/trial/"),
    ("prog-", "loop0", "/programme/"),
    ("contract-", "loop0", "/contract/"),
    ("archive-", "loop0", "/archive/"),
    ("hyp-", "loop0", "/hypothesis/"),
    ("obs-", "loop0", "/observation/"),
    ("conc-", "loop0", "/conclusion/"),
    ("decision-", "loop0", "/decision/"),
    ("belief-", "loop0", "/belief/"),
    # anamnesis (claims)
    ("claim-", "claims", "/claim/"),
    # eref- is minted by BOTH arete and zetesis — for ids of unknown
    # provenance, the anamnesis /ref/ resolver probes both owners'
    # /evidence-ref/ pages and redirects to the real one.
    ("eref-", "claims", "/ref/"),
)


def entity_url(entity_id: str, gui_bases: dict) -> str | None:
    """An entity id → its detail page, local or upstream."""
    for prefix, path in _LOCAL_ROUTES:
        if entity_id.startswith(prefix):
            return f"{path}{entity_id}"
    for prefix, channel, path in _UPSTREAM_ROUTES:
        if entity_id.startswith(prefix):
            base = gui_bases.get(channel)
            return f"{base}{path}{entity_id}" if base else None
    return None


def link_id(entity_id: str | None, gui_bases: dict) -> str:
    """An entity id, HTML-linked when a page exists — plain otherwise."""
    from .templates import escape

    if not entity_id:
        return '<span class="muted">—</span>'
    mono = f'<span class="mono">{escape(entity_id)}</span>'
    url = entity_url(entity_id, gui_bases)
    return f'<a href="{escape(url)}">{mono}</a>' if url else mono


def link_ids(ref_ids: list[str], gui_bases: dict) -> str:
    """A comma-joined ref_ids cell with each id linked where possible."""
    return ", ".join(link_id(r, gui_bases) for r in ref_ids) or "—"
