"""Entity graph resource — protocol://graph.

A pure, read-only projection of the loop-0 store as nodes + edges so
the agora hub can stitch a cross-server graph without any tool calls.
Nodes carry ``gui_path`` — the path on this server's own
observability GUI — so the hub can cross-link without knowing our
routes.

Shape: ``{server, generated_at, truncated, nodes: [...], edges:
[...]}``. Capped at ``_NODE_CAP`` nodes (most-recent programmes
first) to bound the payload — matches the paginated-list convention.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

_NODE_CAP = 500
_PROGRAMME_CAP = 150


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_graph(store) -> dict:
    """Project programmes, hypotheses, and trials into a graph."""
    nodes: list[dict] = []
    edges: list[dict] = []
    truncated = False

    programmes = store.list_programmes_paginated(
        page=1, per_page=_PROGRAMME_CAP
    )
    for p in programmes:
        pid = p["id"]
        nodes.append({
            "id": pid,
            "kind": "programme",
            "label": (p.get("goal") or pid)[:60],
            "status": p.get("status"),
            "ts": p.get("created_at"),
            "gui_path": f"/programme/{pid}",
        })
        if p.get("candidate_version_id"):
            edges.append({
                "src": p["candidate_version_id"],
                "dst": pid,
                "kind": "spawned",
            })

        for h in store.list_hypotheses(pid):
            if len(nodes) >= _NODE_CAP:
                truncated = True
                break
            nodes.append({
                "id": h.id,
                "kind": "hypothesis",
                "label": h.statement[:60],
                "status": h.status.value,
                "ts": h.created_at,
            })
            edges.append({"src": pid, "dst": h.id, "kind": "tests"})

        for t in store.list_trials(pid):
            if len(nodes) >= _NODE_CAP:
                truncated = True
                break
            nodes.append({
                "id": t.id,
                "kind": "trial",
                "label": t.id,
                "status": t.status.value,
                "ts": t.created_at,
                "gui_path": f"/trial/{t.id}",
            })
            edges.append({"src": pid, "dst": t.id, "kind": "runs"})
            edges.append({
                "src": t.hypothesis_id, "dst": t.id, "kind": "tested_by",
            })
        if truncated:
            break

    return {
        "server": "ml-episteme-mcp",
        "loop": "loop0",
        "generated_at": _now(),
        "truncated": truncated,
        "nodes": nodes,
        "edges": edges,
    }


def register(mcp, store) -> None:
    """Register the entity-graph resource."""

    @mcp.resource("protocol://graph")
    def get_graph() -> str:
        """Loop-0 entity graph — programmes, hypotheses, trials as
        nodes + edges. Read-only projection for the agora hub."""
        return json.dumps(build_graph(store), indent=2)
