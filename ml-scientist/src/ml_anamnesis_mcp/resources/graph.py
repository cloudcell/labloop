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

    @mcp.resource("claims://by-relation/{relation}")
    def get_by_relation(relation: str) -> str:
        """Claims participating in edges of one relation kind —
        e.g. claims://by-relation/contradicts lists every claim that
        contradicts or is contradicted. The contradiction-chain pull
        without a tool call per claim."""
        edges = [
            e for e in store.list_edges(limit=1000)
            if e.relation.value == relation
        ]
        claim_ids = sorted({e.from_claim for e in edges})
        claims = {}
        for cid in claim_ids:
            c = store.get_claim(cid)
            if c is not None:
                claims[cid] = {
                    "id": c.id,
                    "content": c.content,
                    "type": c.type.value,
                    "confidence": c.confidence,
                    "supersedes_id": c.supersedes_id,
                    "created_at": c.created_at,
                }
        return json.dumps({
            "server": "ml-anamnesis-mcp",
            "relation": relation,
            "generated_at": _now(),
            "edges": [
                {
                    "edge_id": e.id,
                    "from_claim": e.from_claim,
                    "to_ref": e.to_ref,
                    "ref_type": e.ref_type.value,
                    "weight": e.weight,
                }
                for e in edges
            ],
            "claims": list(claims.values()),
        }, indent=2)
