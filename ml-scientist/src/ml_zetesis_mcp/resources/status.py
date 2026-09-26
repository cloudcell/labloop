"""Status digest — search://status.

The machine-readable slice of the session protocol: where the loop
stands, what is open, what is blocked, and the recommended next
action. Computed live on every read. The same digest is embedded in
``search://session`` and rendered by the ``status_report`` prompt —
one source of truth, three surfaces.

The digest is the cross-server contract — every product in the lab
emits the same shape.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from ..enforcement.recurrence import TRACKER, open_violations
from ..integrity.checks import log_dir_for
from ..integrity.log import list_check_logs
from ..state.store import SearchStore

# Which tools a down channel blocks.
_BLOCKED_TOOLS = {
    "loop0-read": ["pull_evidence"],
    "loop0-write": [
        "register_candidate", "create_evaluation_contract",
        "record_promotion_decision",
    ],
    "claims": ["conclude_investigation", "record_promotion_verdict"],
}

_STEP_OF_TOOL = {
    "open_investigation": (1, "open_investigation"),
    "pull_evidence": (2, "pull_evidence"),
    "record_finding": (3, "record_finding"),
    "conclude_investigation": (4, "conclude_investigation"),
}
_LOOP_STEPS = 4


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _upstream_summary(adaptors) -> dict:
    report = (
        adaptors.connectivity_report()
        if adaptors is not None
        and hasattr(adaptors, "connectivity_report")
        else []
    )
    up = sum(1 for e in report if e.get("state") == "up")
    down = sum(1 for e in report if e.get("state") == "down")
    return {
        "configured": len(report),
        "up": up,
        "down": down,
        "verdict": "ok" if down == 0 else "violations",
        "channels": report,
    }


def _blockers(adaptors) -> list[dict]:
    report = (
        adaptors.connectivity_report()
        if adaptors is not None
        and hasattr(adaptors, "connectivity_report")
        else []
    )
    out = []
    for e in report:
        if e.get("state") == "down":
            role = e.get("role") or e.get("channel")
            out.append({
                "kind": "upstream_down",
                "channel": e.get("channel"),
                "role": role,
                "blocks": _BLOCKED_TOOLS.get(role, []),
                "detail": e.get("last_error") or "channel unreachable",
            })
    return out


def _violation_blockers(store: SearchStore) -> list[dict]:
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


def _integrity_summary(store: SearchStore) -> dict:
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


def _collect_open_work(store: SearchStore) -> tuple[list[dict], dict]:
    open_work: list[dict] = []
    facts = {
        "campaigns": [], "concludable_invs": [],
        "working_invs": [], "fresh_invs": [],
    }

    campaigns, _ = store.list_campaigns(status="open", limit=50)
    for c in campaigns:
        results = store.list_campaign_results(c.id)
        open_work.append({
            "kind": "campaign", "id": c.id, "state": "open",
            "results": len(results),
        })
        facts["campaigns"].append((c, results))

    invs, _ = store.list_investigations(status="open", limit=50)
    for inv in invs:
        refs = store.list_evidence_refs(investigation_id=inv.id)
        findings = store.list_findings(inv.id, status="provisional")
        open_work.append({
            "kind": "investigation", "id": inv.id, "state": "open",
            "question": inv.question,
            "evidence_refs": len(refs),
            "findings": len(findings),
        })
        if findings:
            facts["concludable_invs"].append((inv, refs, findings))
        elif refs:
            facts["working_invs"].append((inv, refs))
        else:
            facts["fresh_invs"].append(inv)
    return open_work, facts


def _recommend(facts: dict, blockers: list[dict]) -> list[dict]:
    blocked_tools = {
        t for b in blockers for t in b.get("blocks", [])
    }
    recs: list[dict] = []

    def add(tool, refs, reason):
        entry = {
            "rank": len(recs) + 1,
            "action": tool,
            "tool": tool,
            "entity_refs": refs,
            "reason": reason,
        }
        if tool in blocked_tools:
            entry["blocked"] = True
        recs.append(entry)

    for c, results in facts["campaigns"]:
        if results:
            add(
                "close_campaign", [c.id],
                "campaign has recorded results — close it to compute "
                "the promotion score",
            )
        else:
            add(
                "record_campaign_result", [c.id],
                "campaign is open with no results — record the arm's "
                "outcome or pull campaign evidence",
            )
    for inv, refs, findings in facts["concludable_invs"]:
        add(
            "conclude_investigation", [inv.id],
            f"investigation has {len(findings)} provisional finding(s) "
            "grounded in pulls — conclude to mint claims",
        )
    for inv, refs in facts["working_invs"]:
        add(
            "record_finding", [inv.id],
            f"{len(refs)} evidence pull(s) made but no finding "
            "recorded — distill the evidence",
        )
    for inv in facts["fresh_invs"]:
        add(
            "pull_evidence", [inv.id],
            "investigation is open with no evidence consulted — "
            "pull from loop0 or anamnesis",
        )
    if not recs:
        add(
            "open_investigation", [],
            "no open work — open a bounded inquiry or pick up a "
            "roster/campaign task",
        )
    return recs


def _workflow_position(recs: list[dict]) -> str:
    if not recs:
        return "idle"
    tool = recs[0].get("tool")
    step = _STEP_OF_TOOL.get(tool)
    if step:
        return f"{step[1]} (step {step[0]} of {_LOOP_STEPS})"
    return tool or "campaign lifecycle"


def status_digest(store: SearchStore, adaptors=None) -> dict:
    """The Loop-1 status digest — the cross-server contract shape."""
    open_work, facts = _collect_open_work(store)
    blockers = _blockers(adaptors) + _violation_blockers(store)
    recs = _recommend(facts, blockers)
    digest = {
        "server": "ml-zetesis-mcp",
        "role": "loop1",
        "generated_at": _utc_now_iso(),
        "workflow_position": _workflow_position(recs),
        "open_work": open_work,
        "blockers": blockers,
        "recommended_next": recs,
        "upstream_summary": _upstream_summary(adaptors),
        "integrity_summary": _integrity_summary(store),
    }
    TRACKER.mark_status_read(digest)
    return digest


def register(mcp, store: SearchStore, adaptors=None) -> None:
    """Register the status digest resource."""

    @mcp.resource("search://status")
    def get_status() -> str:
        """Compact status digest — open work, blockers, next action."""
        return json.dumps(status_digest(store, adaptors), indent=2)
