"""Status digest — improver://status.

The machine-readable slice of the session protocol: where the loop
stands, what is open, what is blocked, and the recommended next
action. Computed live on every read. The same digest is embedded in
``improver://session`` and rendered by the ``status_report`` prompt —
one source of truth, three surfaces.

The digest is the cross-server contract — every product in the lab
emits the same shape.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from ..enforcement import recurrence
from ..enforcement.recurrence import (
    TRACKER,
    improvement_due,
    open_violations,
    undecided_tournaments,
)
from ..integrity.checks import log_dir_for
from ..integrity.log import list_check_logs
from ..state.store import ImproverStore

# Which tools a down channel blocks.
_BLOCKED_TOOLS = {
    "loop0-read": ["pull_evidence"],
    "loop1-read": ["pull_evidence"],
    "loop1-orchestration": [
        "open_arm_campaign",
        "spawn_arm_programme",
        "pull_arm_evidence",
        "record_arm_result",
        "close_arm_campaign",
        "record_arm_verdict",
    ],
    "claims": ["record_meta_decision"],
}

_STEP_OF_TOOL = {
    "register_improver": (1, "register_improver"),
    "create_meta_contract": (2, "create_meta_contract"),
    "propose_meta_change": (3, "propose_meta_change"),
    "open_tournament": (4, "open_tournament"),
    "record_tournament_result": (5, "record_tournament_result"),
    "close_tournament": (6, "close_tournament"),
    "record_meta_decision": (7, "record_meta_decision"),
    "promote_policy": (8, "promote_policy"),
}
_LOOP_STEPS = 8


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


def _integrity_summary(store: ImproverStore) -> dict:
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


def _collect_open_work(
    store: ImproverStore, stale_seconds: int
) -> tuple[list[dict], dict]:
    open_work: list[dict] = []
    facts = {
        "no_champion": store.get_champion() is None,
        "conditional": [], "admitted": [],
        "tournaments_ready": [], "tournaments_waiting": [],
    }

    conditional, _ = store.list_proposals(status="conditional", limit=50)
    admitted, _ = store.list_proposals(status="admitted", limit=50)
    for p in conditional:
        open_work.append({
            "kind": "proposal", "id": p.id, "state": "conditional",
            "proposer": p.proposer_improver_id,
            "created_at": p.created_at,
        })
        facts["conditional"].append(p)
    for p in admitted:
        open_work.append({
            "kind": "proposal", "id": p.id, "state": "admitted",
            "proposer": p.proposer_improver_id,
            "created_at": p.created_at,
        })
        facts["admitted"].append(p)

    now = datetime.now(timezone.utc)
    tournaments, _ = store.list_tournaments(status="open", limit=50)
    for t in tournaments:
        results = store.list_tournament_results(t.id)
        parent_n = sum(1 for r in results if r.arm == "parent")
        cand_n = sum(1 for r in results if r.arm == "candidate")
        # Same last-activity basis as stale_open_tournaments:
        # max(created_at, last result, last tournament evidence).
        stamps = [t.created_at]
        stamps += [r.created_at for r in results]
        stamps += [
            e.created_at
            for e in store.list_evidence_refs("tournament", t.id)
        ]
        last_ts = t.created_at
        try:
            last = max(
                datetime.fromisoformat(s) for s in stamps if s
            )
            idle_s = (now - last).total_seconds()
            last_ts = last.isoformat()
        except ValueError:
            idle_s = 0.0
        open_work.append({
            "kind": "tournament", "id": t.id, "state": "open",
            "parent_results": parent_n,
            "candidate_results": cand_n,
            "idle_hours": round(idle_s / 3600, 1),
            "stale": idle_s > stale_seconds,
            "last_activity_at": last_ts,
        })
        if parent_n >= 1 and cand_n >= 1:
            facts["tournaments_ready"].append(t)
        else:
            facts["tournaments_waiting"].append(
                (t, parent_n, cand_n, idle_s)
            )
    # Most recent first — consumers render the head of this list
    # (agora's overview shows the first 5), so recency must lead.
    open_work.sort(
        key=lambda w: w.get("last_activity_at")
        or w.get("created_at") or "",
        reverse=True,
    )
    return open_work, facts


def _recommend(
    facts: dict, blockers: list[dict], stale_seconds: int
) -> list[dict]:
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

    for t in facts["tournaments_ready"]:
        add(
            "close_tournament", [t.id],
            "both arms have results — close to compute "
            "recursive_gain",
        )
    for t, parent_n, cand_n, idle_s in facts["tournaments_waiting"]:
        missing = "parent" if parent_n == 0 else "candidate"
        if parent_n == 0 and cand_n == 0:
            missing = "either arm"
        add(
            "record_tournament_result", [t.id],
            f"tournament is open — no result yet for {missing}; "
            "run the arm's evaluation then record it",
        )
        if idle_s > stale_seconds:
            # Dead record — offer the honest exit alongside revival.
            add(
                "void_tournament", [t.id],
                f"tournament idle >{stale_seconds}s with unpaired "
                "arms — void_tournament if the run is dead "
                "(rationale required)",
            )
    for p in facts["conditional"]:
        add(
            "record_meta_decision", [p.id],
            "proposal is conditional — the human gate decides "
            "(decided_by='human:<name>')",
        )
    for p in facts["admitted"]:
        add(
            "open_tournament", [p.id],
            "admitted proposal is the candidate — pair it against "
            "the champion under a contract",
        )
    if facts["no_champion"]:
        add(
            "register_improver", [],
            "no improver registered — genesis bootstraps the "
            "champion pointer",
        )
    if not recs:
        add(
            "propose_meta_change", [],
            "no open work — propose a meta-change or review the "
            "policy ledger",
        )
    return recs


def _workflow_position(recs: list[dict]) -> str:
    if not recs:
        return "idle"
    tool = recs[0].get("tool")
    step = _STEP_OF_TOOL.get(tool)
    if step:
        return f"{step[1]} (step {step[0]} of {_LOOP_STEPS})"
    return tool or "meta lifecycle"


def _violation_blockers(store: ImproverStore) -> list[dict]:
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


def _decision_debt_blockers(store: ImproverStore) -> list[dict]:
    """Closed tournaments lacking a meta_decision — blocking debt;
    the gate exempts the decision tools that resolve it."""
    return [
        {
            "kind": "decision_debt",
            "tournament_id": tid,
            "action": "record_meta_decision",
            "tool": "record_meta_decision",
            "blocks": ["*"],
            "detail": (
                "closed tournament has no meta_decision — adjudicate "
                "or record a 'hold' with rationale"
            ),
        }
        for tid in undecided_tournaments(store)
    ]


def _improvement_due_rec(store: ImproverStore) -> dict | None:
    """Advisory recurrence recommendation when the epoch elapses."""
    if not improvement_due(store):
        return None
    return {
        "action": "propose_meta_change",
        "tool": "propose_meta_change",
        "entity_refs": [],
        "reason": (
            f"improvement epoch elapsed "
            f"({recurrence.EPOCH_SECONDS:.0f}s with no proposal, "
            "decision, or "
            "tournament activity) — propose a meta-change or record "
            "a decision/hold on pending work"
        ),
    }


def status_digest(
    store: ImproverStore, adaptors=None, stale_seconds: int | None = None
) -> dict:
    """The Loop-2 status digest — the cross-server contract shape."""
    if stale_seconds is None:
        from ..integrity.checks import DEFAULT_STALE_TOURNAMENT_SECONDS
        stale_seconds = DEFAULT_STALE_TOURNAMENT_SECONDS
    open_work, facts = _collect_open_work(store, stale_seconds)
    blockers = (
        _blockers(adaptors)
        + _violation_blockers(store)
        + _decision_debt_blockers(store)
    )
    recs = _recommend(facts, blockers, stale_seconds)
    due_rec = _improvement_due_rec(store)
    if due_rec is not None:
        recs.append(due_rec)
    for i, rec in enumerate(recs):
        rec["rank"] = i + 1
    digest = {
        "server": "ml-arete-mcp",
        "role": "loop2",
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


def register(
    mcp, store: ImproverStore, adaptors=None,
    stale_seconds: int | None = None,
) -> None:
    """Register the status digest resource."""

    @mcp.resource("improver://status")
    def get_status() -> str:
        """Compact status digest — open work, blockers, next action."""
        return json.dumps(
            status_digest(store, adaptors, stale_seconds), indent=2
        )
