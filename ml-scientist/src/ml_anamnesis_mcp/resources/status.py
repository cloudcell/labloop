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
from pathlib import Path
from datetime import datetime, timezone

from ..enforcement.recurrence import TRACKER, open_violations
from ..integrity.checks import log_dir_for
from ..integrity.log import list_check_logs
from ..state.store import MemoryStore
from .. import _grounded_constants as _gc


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _violation_blockers(store: MemoryStore) -> list[dict]:
    """Open (unacknowledged) integrity findings — tier-1 blockers
    that also gate mutating tools when the protocol is enabled."""
    return [
        {
            "kind": "open_violation",
            "check": v["check"],
            "object_ref": v["object_ref"],
            "action": "acknowledge_violation",
            "tool": "acknowledge_violation",
            "blocks": ["*"],
            "detail": v["detail"],
        }
        for v in open_violations(store)
    ]


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


def _build_stamp() -> dict | None:
    """Read BUILD_REV beside the vendored source tree — written into
    the deployment image at build time so a status digest can prove
    which source revision it runs (rc-12: surface fingerprints cannot
    see gate internals). None when absent — a dev checkout is
    unpackaged source, honestly unprovable."""
    for parent in Path(__file__).resolve().parents:
        f = parent / "BUILD_REV"
        if f.is_file():
            try:
                d = json.loads(f.read_text())
            except (OSError, ValueError):
                return None
            if isinstance(d, dict) and isinstance(d.get("rev"), str):
                out = {"rev": d["rev"],
                       "dirty": bool(d.get("dirty", False))}
                if out["dirty"]:
                    if isinstance(d.get("dirty_files"), list):
                        out["dirty_files"] = d["dirty_files"]
                    if isinstance(d.get("diff_sha256"), str):
                        out["diff_sha256"] = d["diff_sha256"]
                return out
            return None
    return None

def status_digest(
    store: MemoryStore, tool_deadline_seconds: float | None = None,
) -> dict:
    """The claims-role status digest — the cross-server contract
    shape, emitted in capability posture."""
    _, total = store.list_claims(limit=1)
    _, superseded = store.list_claims(limit=1, only_superseded=True)
    _, expired = store.list_claims(limit=1, only_expired=True)
    digest = {
        "server": "ml-anamnesis-mcp",
        "role": "claims",
        "generated_at": _utc_now_iso(),
        # A capability server has no workflow of its own — null is
        # the honest answer, not a fabricated position.
        "workflow_position": None,
        "open_work": [],
        "blockers": _violation_blockers(store),
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
        "constants": _gc.constants_block(),
        # The per-call response deadline — invisible in every digest
        # before rc-15 F8.
        "tool_deadline_seconds": tool_deadline_seconds,
        "build": _build_stamp(),
        "claims": {
            "total": total,
            "superseded": superseded,
            "expired": expired,
        },
    }
    TRACKER.mark_status_read(digest)
    return digest


def register(
    mcp, store: MemoryStore,
    tool_deadline_seconds: float | None = None,
) -> None:
    """Register the status digest resource."""

    @mcp.resource("claims://status")
    def get_status() -> str:
        """Capability status digest — claim counts, integrity."""
        return json.dumps(
            status_digest(
                store, tool_deadline_seconds=tool_deadline_seconds
            ),
            indent=2,
        )

    @mcp.resource("claims://constants")
    def get_constants() -> str:
        """Live grounded-constants registry — disclosed from the
        running module so the audit surface is the code, not a file."""
        return json.dumps(_gc.disclosure(), indent=2)
