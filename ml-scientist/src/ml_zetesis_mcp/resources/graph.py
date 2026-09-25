"""Entity graph resource — search://graph.

A pure, read-only projection of the loop-1 store as nodes + edges so
the agora hub can stitch a cross-server graph without tool calls.
Nodes carry ``gui_path`` — the path on this server's own
observability GUI — so the hub can cross-link without knowing our
routes.

Shape: ``{server, generated_at, truncated, nodes: [...], edges:
[...]}``. Capped at ``_NODE_CAP`` nodes (most-recent first) to bound
the payload.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

_NODE_CAP = 500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_graph(store) -> dict:
    """Project campaigns, spawns, roster, investigations to a graph."""
    nodes: list[dict] = []
    edges: list[dict] = []
    seen: set[str] = set()

    def node(nid, kind, label, status=None, gui_path=None, ts=None):
        if nid in seen or len(nodes) >= _NODE_CAP:
            return
        seen.add(nid)
        n = {"id": nid, "kind": kind, "label": (label or nid)[:60]}
        if status is not None:
            n["status"] = status
        if gui_path:
            n["gui_path"] = gui_path
        if ts is not None:
            n["ts"] = ts
        nodes.append(n)

    # Roster first — campaigns reference roster ids as arm stubs;
    # creating the real entries before any stub keeps their status.
    roster, _ = store.list_roster_entries(limit=200)
    for r in roster:
        node(r.id, "roster_entry", r.id, r.derived_status.value,
             ts=r.annotated_at)

    campaigns, _ = store.list_campaigns(limit=200)
    for c in campaigns:
        node(c.id, "campaign", c.primary_metric, c.status.value,
             f"/campaign/{c.id}", ts=c.created_at)
        # Arm edges — campaign pits challenger against champion.
        for arm, rid in (("champion", c.champion_id),
                         ("challenger", c.challenger_id)):
            if rid:
                node(rid, "roster_entry", rid)
                edges.append({"src": c.id, "dst": rid,
                              "kind": f"arm:{arm}"})
        if c.claim_id:
            edges.append({"src": c.id, "dst": c.claim_id,
                          "kind": "mints"})
        for s in store.list_campaign_spawns(c.id):
            node(s.programme_id, "programme", s.programme_id)
            edges.append({"src": c.id, "dst": s.programme_id,
                          "kind": f"spawn:{s.arm.value}"})

    investigations, _ = store.list_investigations(limit=200)
    for inv in investigations:
        node(inv.id, "investigation", inv.question,
             inv.status.value, f"/investigation/{inv.id}",
             ts=inv.created_at)

    for r in roster:
        if r.parent_id:
            node(r.parent_id, "roster_entry", r.parent_id)
            edges.append({"src": r.parent_id, "dst": r.id,
                          "kind": "parent_of"})

    return {
        "server": "ml-zetesis-mcp",
        "loop": "loop1",
        "generated_at": _now(),
        "truncated": len(nodes) >= _NODE_CAP,
        "nodes": nodes,
        "edges": edges,
    }


def register(mcp, store) -> None:
    """Register the entity-graph resource."""

    @mcp.resource("search://graph")
    def get_graph() -> str:
        """Loop-1 entity graph — campaigns, spawns, roster,
        investigations as nodes + edges. Read-only projection for
        the agora hub."""
        return json.dumps(build_graph(store), indent=2)
