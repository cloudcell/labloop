"""Entity graph resource — claims://graph.

A pure, read-only projection of the claims store as nodes + edges so
the agora hub can stitch a cross-server graph without tool calls.
Nodes carry ``gui_path`` — the path on this server's own
observability GUI — so the hub can cross-link without knowing our
routes.

Claim edges point *at* entities on other servers (``to_ref`` is an
entity id like ``prog-*`` or ``mdec-*``) — those resolve to stub
nodes at merge time in the hub.

Shape: ``{server, generated_at, truncated, nodes: [...], edges:
[...]}``. Capped at ``_NODE_CAP`` nodes (most-recent first).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

_NODE_CAP = 500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_graph(store) -> dict:
    """Project claims + their provenance edges into a graph."""
    nodes: list[dict] = []
    edges: list[dict] = []

    claims, _ = store.list_claims(
        include_superseded=True, include_expired=True,
        limit=_NODE_CAP,
    )
    live_ids = set()
    for c in claims:
        live_ids.add(c.id)
        status = "superseded" if c.supersedes_id else "active"
        nodes.append({
            "id": c.id,
            "kind": "claim",
            "label": c.content[:60],
            "status": status,
            "ts": c.created_at,
            "gui_path": f"/claim/{c.id}",
        })
        if c.supersedes_id:
            edges.append({"src": c.id, "dst": c.supersedes_id,
                          "kind": "supersedes"})
        if c.source_id:
            edges.append({"src": c.source_id, "dst": c.id,
                          "kind": "sourced"})

    for e in store.list_edges(limit=_NODE_CAP):
        if e.from_claim not in live_ids:
            continue
        edges.append({"src": e.from_claim, "dst": e.to_ref,
                      "kind": e.relation.value})

    return {
        "server": "ml-anamnesis-mcp",
        "loop": "claims",
        "generated_at": _now(),
        "truncated": len(nodes) >= _NODE_CAP,
        "nodes": nodes,
        "edges": edges,
    }


def register(mcp, store) -> None:
    """Register the entity-graph resource."""

    @mcp.resource("claims://graph")
    def get_graph() -> str:
        """Claims entity graph — claims plus provenance edges as
        nodes + edges. Read-only projection for the agora hub."""
        return json.dumps(build_graph(store), indent=2)
