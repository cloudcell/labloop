"""Status digest — claims://status.

The machine-readable status slice for the semantic-memory server.
Anamnesis is a role-filler, not a loop (ADR-0004) — it has no
workflow position and no open work of its own; its status reports
capability posture honestly: claim counts, integrity, and an
explicit "consuming loops determine the next action" note.

The digest is the cross-server contract — every product in the lab
emits the same shape.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from ..integrity.checks import log_dir_for
from ..integrity.log import list_check_logs
from ..state.store import MemoryStore


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _integrity_summary(store: MemoryStore) -> dict:
    runs = list_check_logs(log_dir_for(store), limit=1)
    if not runs:
        return {
            "verdict": "no_runs",
            "violations": None,
            "last_run_at": None,
        }
    last = runs[0]
    return {
        "verdict": last.get("status"),
        "violations": last.get("violations"),
        "last_run_at": last.get("checked_at"),
        "trigger": last.get("trigger"),
    }


def status_digest(store: MemoryStore) -> dict:
    """The claims-role status digest — the cross-server contract
    shape, emitted in capability posture."""
    _, total = store.list_claims(limit=1)
    _, superseded = store.list_claims(limit=1, only_superseded=True)
    _, expired = store.list_claims(limit=1, only_expired=True)
    return {
        "server": "ml-anamnesis-mcp",
        "role": "claims",
        "generated_at": _utc_now_iso(),
        # A capability server has no workflow of its own — null is
        # the honest answer, not a fabricated position.
        "workflow_position": None,
        "open_work": [],
        "blockers": [],
        "recommended_next": [{
            "rank": 1,
            "action": "serve_consumers",
            "tool": None,
            "entity_refs": [],
            "reason": (
                "capability server — the next action is determined "
                "by the consuming loops (assert_claim, relate, "
                "list_claims, get_claim)"
            ),
        }],
        # Anamnesis consumes no upstreams (ADR-0004): the summary is
        # an explicit skip, mirroring upstream_connectivity.
        "upstream_summary": {
            "configured": 0,
            "up": 0,
            "down": 0,
            "verdict": "skipped",
            "detail": (
                "semantic-memory endpoint — no upstream channels"
            ),
            "channels": [],
        },
        "integrity_summary": _integrity_summary(store),
        "claims": {
            "total": total,
            "superseded": superseded,
            "expired": expired,
        },
    }


def register(mcp, store: MemoryStore) -> None:
    """Register the status digest resource."""

    @mcp.resource("claims://status")
    def get_status() -> str:
        """Capability status digest — claim counts, integrity."""
        return json.dumps(status_digest(store), indent=2)
