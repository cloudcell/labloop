"""Status digest — protocol://status.

The machine-readable slice of the session protocol: where the loop
stands, what is open, what is blocked, and the recommended next
action. Computed live on every read. The same digest is embedded in
``protocol://session`` and rendered by the ``status_report`` prompt,
so the three surfaces can never drift.

The digest is the cross-server contract — every product in the lab
emits the same shape (server, role, workflow_position, open_work,
blockers, recommended_next, upstream_summary, integrity_summary).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..enforcement.recurrence import TRACKER, open_violations
from ..integrity.checks import log_dir_for
from ..integrity.log import list_check_logs
from ..state.store import StateStore

# Which tools a down channel blocks — the honest answer to "why can't
# I do the next step". Local roles (optimizer/executor/data_source)
# report "unwired" when absent, never "down"; only a configured-but-
# dead channel produces a blocker.
_BLOCKED_TOOLS = {
    "optimizer": ["get_next_experiment"],
    "executor": ["run_trial"],
    "data_source": ["prepare_data"],
    "claims": ["conclude_hypothesis"],
}

# Loop-step index for the recommended tools — used to render
# workflow_position.
_STEP_OF_TOOL = {
    "create_programme": (1, "create_programme"),
    "formulate_hypothesis": (2, "formulate_hypothesis"),
    "prepare_data": (3, "prepare_data"),
    "design_experiment": (4, "design_experiment"),
    "capture_bundle": (5, "capture_bundle"),
    "run_trial": (6, "run_trial"),
    "record_observation": (7, "record_observation"),
    "update_belief": (8, "update_belief"),
    "conclude_hypothesis": (9, "conclude_hypothesis"),
    "close_programme": (10, "close_programme"),
}
_LOOP_STEPS = 10


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _upstream_summary(adaptor) -> dict:
    report = (
        adaptor.connectivity_report()
        if adaptor is not None
        and hasattr(adaptor, "connectivity_report")
        else []
    )
    up = sum(1 for e in report if e.get("state") == "up")
    down = sum(1 for e in report if e.get("state") == "down")
    wired = [e for e in report if e.get("state") != "unwired"]
    return {
        "configured": len(wired),
        "up": up,
        "down": down,
        "verdict": "ok" if down == 0 else "violations",
        "channels": report,
    }


def _blockers(adaptor) -> list[dict]:
    report = (
        adaptor.connectivity_report()
        if adaptor is not None
        and hasattr(adaptor, "connectivity_report")
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


def _violation_blockers(store: StateStore) -> list[dict]:
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


def _integrity_summary(store: StateStore) -> dict:
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
    store: StateStore, stale_programme_hours: float = 24.0
) -> tuple[list[dict], dict]:
    """Open entities plus the raw facts the recommender needs."""
    open_work: list[dict] = []
    facts = {
        "running": [], "designed": [], "needs_obs": [],
        "concludable": [], "needs_design": [], "empty_programmes": [],
        "stale": [], "unattributed": [],
    }
    rows = store.conn.execute(
        "SELECT id, goal, created_at, candidate_version_id "
        "FROM programmes WHERE status = 'active'"
    ).fetchall()
    for row in rows:
        prog_id = row["id"]
        trials = store.list_trials(prog_id)
        hypotheses = store.list_hypotheses(prog_id)
        running = [t for t in trials if t.status.value == "running"]
        designed = [t for t in trials if t.status.value == "designed"]
        completed = [t for t in trials if t.status.value == "completed"]
        needs_obs = [
            t for t in completed
            if not store.list_observations(t.id)
        ]
        under_test = [
            h for h in hypotheses if h.status.value == "under_test"
        ]
        # A hypothesis is concludable when at least one of its trials
        # produced an observation.
        concludable = []
        needs_design = []
        for h in under_test:
            h_trials = [t for t in trials if t.hypothesis_id == h.id]
            has_obs = any(
                store.list_observations(t.id)
                for t in h_trials
                if t.status.value == "completed"
            )
            has_runnable = any(
                t.status.value in ("designed", "running")
                for t in h_trials
            )
            if has_obs:
                concludable.append(h)
            elif not has_runnable:
                needs_design.append(h)

        prog_item = {
            "kind": "programme",
            "id": prog_id,
            "state": "active",
            "goal": row["goal"],
            "created_at": row["created_at"],
        }
        if not row["candidate_version_id"]:
            prog_item["unattributed"] = True
            facts["unattributed"].append(prog_id)
        # Staleness mirrors the session resource's policy — same
        # idle-hours computation, surfaced not enforced.
        last_row = store.conn.execute(
            """
            SELECT MAX(ts) AS ts FROM (
                SELECT created_at AS ts FROM programmes WHERE id = ?
                UNION ALL SELECT created_at FROM hypotheses
                    WHERE programme_id = ?
                UNION ALL SELECT created_at FROM trials
                    WHERE programme_id = ?
                UNION ALL SELECT started_at FROM trials
                    WHERE programme_id = ?
                UNION ALL SELECT finished_at FROM trials
                    WHERE programme_id = ?
                UNION ALL SELECT o.created_at FROM observations o
                    JOIN trials t ON t.id = o.trial_id
                    WHERE t.programme_id = ?
            ) WHERE ts IS NOT NULL
            """,
            (prog_id,) * 6,
        ).fetchone()
        if last_row and last_row["ts"]:
            last_dt = datetime.fromisoformat(last_row["ts"])
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            idle_h = round(
                (datetime.now(timezone.utc) - last_dt).total_seconds()
                / 3600, 1
            )
            prog_item["idle_hours"] = idle_h
            prog_item["last_activity_at"] = last_row["ts"]
            if idle_h > stale_programme_hours:
                prog_item["stale"] = True
                facts["stale"].append(prog_id)

        open_work.append(prog_item)
        for h in under_test:
            open_work.append({
                "kind": "hypothesis", "id": h.id,
                "state": "under_test", "programme_id": prog_id,
                "created_at": h.created_at,
            })
        for t in running + designed:
            open_work.append({
                "kind": "trial", "id": t.id,
                "state": t.status.value,
                "programme_id": prog_id,
                "hypothesis_id": t.hypothesis_id,
                "created_at": t.started_at or t.created_at,
            })
        for t in needs_obs:
            open_work.append({
                "kind": "trial", "id": t.id,
                "state": "completed",
                "flag": "missing_observation",
                "programme_id": prog_id,
                "hypothesis_id": t.hypothesis_id,
                "created_at": t.finished_at or t.created_at,
            })

        facts["running"].extend(running)
        facts["designed"].extend(designed)
        facts["needs_obs"].extend(needs_obs)
        facts["concludable"].extend(
            (h, prog_id) for h in concludable
        )
        facts["needs_design"].extend(
            (h, prog_id) for h in needs_design
        )
        if not hypotheses:
            facts["empty_programmes"].append(prog_id)
    # Most recent first — consumers render the head of this list
    # (agora's overview shows the first 5), so recency must lead.
    open_work.sort(
        key=lambda w: w.get("last_activity_at")
        or w.get("created_at") or "",
        reverse=True,
    )
    return open_work, facts


def _recommend(facts: dict, blockers: list[dict]) -> list[dict]:
    """Ranked next actions — completion-debt first (most in-flight
    work finishes before new work starts)."""
    blocked_tools = {
        t for b in blockers for t in b.get("blocks", [])
    }
    recs: list[dict] = []

    def add(tool, refs, reason, prompt=None):
        entry = {
            "rank": len(recs) + 1,
            "action": tool,
            "tool": tool,
            "entity_refs": refs,
            "reason": reason,
        }
        if prompt:
            entry["prompt"] = prompt
        if tool in blocked_tools:
            entry["blocked"] = True
        recs.append(entry)

    for t in facts["running"]:
        add(
            None, [t.id],
            "trial is running — monitor and record the observation "
            "when it completes",
            prompt="monitor_running_trial",
        )
        recs[-1]["action"] = "monitor_trial"
    for t in facts["needs_obs"]:
        add(
            "record_observation", [t.id],
            "completed trial has no observation — the evidence step "
            "is missing",
        )
    for t in facts["designed"]:
        add(
            "run_trial", [t.id],
            "designed trial is ready to execute",
        )
    for h, prog_id in facts["concludable"]:
        add(
            "conclude_hypothesis", [h.id, prog_id],
            "hypothesis has observations — weigh the evidence and "
            "record a verdict",
        )
    for h, prog_id in facts["needs_design"]:
        add(
            "design_experiment", [h.id, prog_id],
            "hypothesis is under test with no runnable trial — "
            "design the experiment",
        )
    for prog_id in facts["empty_programmes"]:
        add(
            "formulate_hypothesis", [prog_id],
            "active programme has no hypothesis — state the "
            "falsifiable claim",
        )
    for prog_id in facts["unattributed"]:
        add(
            None, [prog_id],
            "active programme carries no candidate attribution — "
            "attribution is first-class provenance (see "
            "protocol://session candidate_attribution); future "
            "programmes should pass candidate_version_id to "
            "create_programme",
        )
        recs[-1]["action"] = "attribute_programme"
    for prog_id in facts["stale"]:
        add(
            None, [prog_id],
            "programme is stale — resume the loop or "
            "close_programme(status='abandoned')",
        )
        recs[-1]["action"] = "review_stale_programme"
    if not recs:
        add(
            "create_programme", [],
            "no open work — open a programme or pick up a stale one",
        )
    return recs


def _workflow_position(recs: list[dict]) -> str:
    if not recs:
        return "idle"
    top = recs[0]
    tool = top.get("tool")
    if tool is None:
        return "run_trial in progress (step 6 of 10)"
    step = _STEP_OF_TOOL.get(tool)
    if step:
        return f"{step[1]} (step {step[0]} of {_LOOP_STEPS})"
    return tool


def _unsealed_archives(
    store: StateStore, warn_hours: float
) -> list[dict]:
    """Archive-registry rows that were created but never sealed past
    the staleness window — an advisory, never a blocker: an unsealed
    archive is provenance debt, not a fault (field report F10)."""
    try:
        rows = store.conn.execute(
            "SELECT archive_id, created_at FROM archive_registry "
            "WHERE sealed = 0"
        ).fetchall()
    except Exception:
        return []
    now = datetime.now(timezone.utc)
    out = []
    for r in rows:
        try:
            created = datetime.fromisoformat(
                r["created_at"].replace("Z", "+00:00")
            )
        except (ValueError, TypeError):
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_h = (now - created).total_seconds() / 3600
        if age_h >= warn_hours:
            out.append({"archive_id": r["archive_id"], "age_hours": age_h})
    return out


def status_digest(
    store: StateStore, adaptor=None, stale_programme_hours: float = 24.0,
    archive_seal_warn_hours: float = 72.0,
) -> dict:
    """The Loop-0 status digest — the cross-server contract shape."""
    open_work, facts = _collect_open_work(
        store, stale_programme_hours
    )
    blockers = _blockers(adaptor) + _violation_blockers(store)
    recs = _recommend(facts, blockers)
    for a in _unsealed_archives(store, archive_seal_warn_hours):
        recs.append({
            "action": "seal_archive",
            "tool": None,
            "entity_refs": [a["archive_id"]],
            "reason": (
                f"archive {a['archive_id']} has been unsealed for "
                f"{a['age_hours']:.0f}h — a sealed archive is the "
                "provenance terminus; verify and seal it"
            ),
        })
    digest = {
        "server": "ml-episteme-mcp",
        "role": "loop0",
        "generated_at": _utc_now_iso(),
        "workflow_position": _workflow_position(recs),
        "open_work": open_work,
        "blockers": blockers,
        "recommended_next": recs,
        "upstream_summary": _upstream_summary(adaptor),
        "integrity_summary": _integrity_summary(store),
    }
    # Consultation-duty watermark — every digest consumer (status
    # resource, session resource, status_report prompt) stamps it.
    TRACKER.mark_status_read(digest)
    return digest


def register(
    mcp, store: StateStore, adaptor=None,
    stale_programme_hours: float = 24.0,
    archive_seal_warn_hours: float = 72.0,
) -> None:
    """Register the status digest resource."""

    @mcp.resource("protocol://status")
    def get_status() -> str:
        """Compact status digest — open work, blockers, next action."""
        return json.dumps(
            status_digest(
                store, adaptor, stale_programme_hours,
                archive_seal_warn_hours,
            ),
            indent=2,
        )
