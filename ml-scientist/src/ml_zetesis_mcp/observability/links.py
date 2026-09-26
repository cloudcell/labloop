"""Entity-id → GUI link resolution (zetesis-local).

Same lab convention as the other GUIs: an upstream entity id's owning
GUI base is the adaptor channel's MCP target port + 1; zetesis-local
ids resolve through this server's own routes. Kept local to zetesis —
the packages stay independent, so each server carries its own copy of
the convention rather than importing another's internals.
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


def _loop0_base(gui_bases: dict) -> str | None:
    """The episteme GUI base — the 'evidence'/'promotion' channels both
    target ml-episteme's MCP port."""
    return gui_bases.get("evidence") or gui_bases.get("promotion")


def entity_url(entity_id: str, gui_bases: dict) -> str | None:
    """An entity id → its detail page, local or upstream.

    Zetesis-local kinds resolve through this server's own routes;
    upstream kinds through the adaptor-derived GUI bases. Only kinds
    with a real page link — anything else returns None and the caller
    renders the id unlinked rather than guessing.
    """
    # Local — this server's own pages (relative links).
    for prefix, path in (
        ("cand-", "/candidate/"),
        ("camp-", "/campaign/"),
        ("inv-", "/investigation/"),
        ("eref-", "/evidence-ref/"),
    ):
        if entity_id.startswith(prefix):
            return f"{path}{entity_id}"
    # Upstream — episteme via the loop0 channels.
    for prefix, path in (
        ("trial-", "/trial/"),
        ("prog-", "/programme/"),
        ("contract-", "/contract/"),
        ("archive-", "/archive/"),
    ):
        if entity_id.startswith(prefix):
            base = _loop0_base(gui_bases)
            return f"{base}{path}{entity_id}" if base else None
    # Upstream — anamnesis via the claims channel.
    if entity_id.startswith("claim-"):
        base = gui_bases.get("claims")
        return f"{base}/claim/{entity_id}" if base else None
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
