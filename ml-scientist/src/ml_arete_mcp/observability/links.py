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


def entity_url(entity_id: str, gui_bases: dict) -> str | None:
    """An upstream entity id → its owning server's GUI page.

    Only kinds with a real page link: campaigns and roster entries
    (anchored on /promotion) live on zetesis (loop1); episteme
    programmes and evaluation contracts link through loop0; claims
    through claims. Arete-local kinds (tcamp-, mcontract-) resolve
    through this server's own routes, not entity_url.
    """
    if entity_id.startswith("camp-"):
        base = gui_bases.get("loop1")
        return f"{base}/campaign/{entity_id}" if base else None
    if entity_id.startswith("cand-"):
        base = gui_bases.get("loop1")
        return f"{base}/candidate/{entity_id}" if base else None
    if entity_id.startswith("contract-"):
        base = gui_bases.get("loop0")
        return f"{base}/contract/{entity_id}" if base else None
    if entity_id.startswith("claim-"):
        base = gui_bases.get("claims")
        return f"{base}/claim/{entity_id}" if base else None
    if entity_id.startswith("prog-"):
        base = gui_bases.get("loop0")
        return f"{base}/programme/{entity_id}" if base else None
    # trial- needs its parent programme_id for /programme/{p}/trial/{t}
    # and isn't rendered standalone — no honest link without context.
    return None
