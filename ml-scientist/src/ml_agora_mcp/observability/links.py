"""Entity-id → owning-GUI link resolution.

Shared by the dashboard plugins: entity ids carry a lab-wide prefix
(``prog-``, ``camp-``, ``claim-``, …) that identifies the owning
server and its detail page. Keeping the map here means retiring a
plugin never strands link knowledge, and new entity kinds get links
everywhere at once.

``gui_path`` is ``None`` when the owning server has no detail page
for that kind — the id still resolves to a channel for stub
purposes, it just isn't clickable.
"""

from __future__ import annotations

# channel name -> dependency level (claims bottom-up source order)
LEVELS = {"claims": 0, "loop0": 1, "loop1": 2, "loop2": 3}

# Server process name -> channel (digests report the former).
SERVER_CHANNELS = {
    "ml-anamnesis": "claims",
    "ml-anamnesis-mcp": "claims",
    "ml-episteme": "loop0",
    "ml-episteme-mcp": "loop0",
    "ml-zetesis": "loop1",
    "ml-zetesis-mcp": "loop1",
    "ml-arete": "loop2",
    "ml-arete-mcp": "loop2",
}

# id prefix -> (channel, gui path prefix or None, kind)
ENTITY_PREFIXES = {
    "prog-": ("loop0", "/programme/", "programme"),
    "trial-": ("loop0", "/trial/", "trial"),
    "data-ref-": ("loop0", "/dataref/", "dataref"),
    "contract-": ("loop0", "/contract/", "evaluation_contract"),
    "hyp-": ("loop0", None, "hypothesis"),
    "cand-": ("loop1", "/candidate/", "candidate"),
    "camp-": ("loop1", "/campaign/", "campaign"),
    "inv-": ("loop1", "/investigation/", "investigation"),
    "imp-": ("loop2", "/improver/", "improver"),
    "mcp-": ("loop2", "/proposal/", "proposal"),
    "tourn-": ("loop2", "/tournament/", "tournament"),
    "tcamp-": ("loop2", "/campaign-link/", "tournament_campaign"),
    "mcontract-": ("loop2", "/contract/", "meta_contract"),
    "mdec-": ("loop2", None, "meta_decision"),
    "claim-": ("claims", "/claim/", "claim"),
}


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


def entity_url(entity_id: str, gui_bases: dict,
               context: dict | None = None) -> str | None:
    """Resolve an entity id to its owning server's GUI page.

    ``context`` carries the surrounding record (e.g. an open_work
    item) for kinds whose page is nested under a parent — hypotheses
    live at ``/programme/{pid}#hyp-{id}``, so they need the parent
    ``programme_id`` from the item.
    """
    for prefix, (channel, path, _kind) in ENTITY_PREFIXES.items():
        if entity_id.startswith(prefix):
            base = gui_bases.get(channel)
            if prefix == "hyp-":
                pid = (context or {}).get("programme_id")
                if base and pid:
                    return f"{base}/programme/{pid}#hyp-{entity_id}"
                return None
            return base + path + entity_id if base and path else None
    return None
