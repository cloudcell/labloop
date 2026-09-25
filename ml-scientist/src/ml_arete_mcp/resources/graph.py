"""Entity graph resource — improver://graph.

A pure, read-only projection of the loop-2 store as nodes + edges so
the agora hub can stitch a cross-server graph without tool calls.
Nodes carry ``gui_path`` — the path on this server's own
observability GUI — so the hub can cross-link without knowing our
routes.

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
    """Project improvers, proposals, tournaments, decisions."""
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

    improvers, _ = store.list_improvers(limit=200)
    # Nodes first, edges second — a parent referenced before its own
    # row must not lose its full record (e.g. champion status).
    for imp in improvers:
        node(imp.id, "improver", imp.model_ref,
             "champion" if imp.is_champion else None,
             f"/improver/{imp.id}", ts=imp.created_at)
    for imp in improvers:
        if imp.parent_id:
            edges.append({"src": imp.parent_id, "dst": imp.id,
                          "kind": "parent_of"})
        if imp.proposal_id:
            edges.append({"src": imp.proposal_id, "dst": imp.id,
                          "kind": "admits"})

    proposals, _ = store.list_proposals(limit=200)
    for pr in proposals:
        node(pr.id, "proposal", pr.expected_benefit or pr.id,
             pr.status.value, f"/proposal/{pr.id}", ts=pr.created_at)
        edges.append({"src": pr.proposer_improver_id, "dst": pr.id,
                      "kind": "proposed"})

    tournaments, _ = store.list_tournaments(limit=200)
    for t in tournaments:
        node(t.id, "tournament", t.id, t.status.value,
             f"/tournament/{t.id}", ts=t.created_at)
        edges.append({"src": t.id, "dst": t.parent_improver_id,
                      "kind": "arm:parent"})
        edges.append({"src": t.id, "dst": t.candidate_improver_id,
                      "kind": "arm:candidate"})
        for link in store.list_tournament_campaigns(t.id):
            node(link.campaign_id, "campaign", link.campaign_id)
            edges.append({"src": t.id, "dst": link.campaign_id,
                          "kind": f"arm:{link.arm.value}"})

    decisions = store.list_meta_decisions()[:200]
    for d in decisions:
        node(d.id, "meta_decision", d.rationale[:60],
             d.verdict.value, ts=d.created_at)
        edges.append({"src": d.id, "dst": d.candidate_improver_id,
                      "kind": "decides"})
        if d.tournament_id:
            edges.append({"src": d.id, "dst": d.tournament_id,
                          "kind": "on"})
        if d.claim_id:
            edges.append({"src": d.id, "dst": d.claim_id,
                          "kind": "mints"})

    return {
        "server": "ml-arete-mcp",
        "loop": "loop2",
        "generated_at": _now(),
        "truncated": len(nodes) >= _NODE_CAP,
        "nodes": nodes,
        "edges": edges,
    }


def register(mcp, store) -> None:
    """Register the entity-graph resource."""

    @mcp.resource("improver://graph")
    def get_graph() -> str:
        """Loop-2 entity graph — improvers, proposals, tournaments,
        meta-decisions as nodes + edges. Read-only projection for
        the agora hub."""
        return json.dumps(build_graph(store), indent=2)
