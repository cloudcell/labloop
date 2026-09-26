"""Invariant checks for state.db — the Loop-0 integrity audit.

Enforcement prevents violations at write time; these checks detect
what enforcement cannot cover: crashes mid-transaction, restarts that
orphan in-memory executor tasks, races, bit-rot, and hand-edits of the
database. They are the detection half of defense-in-depth — the audit
counterpart of Commitment 11's recorded-controls corollary.

Report-only: no check mutates state. Repair stays an explicit,
recorded act (the reap_orphaned_trials precedent).
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Margin past a running trial's enforced deadline before it counts as
# stalled. Trials legitimately run for hours; the executor kills them
# at its own timeout, so only outliving the deadline means wedged.
DEFAULT_STALLED_MARGIN_SECONDS = 300
# completed → record_observation is the normal loop order, but the
# analysis phase can lag execution by a whole session. Grace is a
# day: anything older is clearly abandoned debt, not in-flight work.
DEFAULT_OBSERVATION_GRACE_SECONDS = 86400
DEFAULT_LOG_MAX_FILES = 100
DEFAULT_CHECK_INTERVAL_SECONDS = 300


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _res(name: str, violations: list, detail: str) -> dict:
    return {
        "name": name,
        "ok": not violations,
        "violations": violations,
        "detail": detail,
    }


def _skipped(name: str, reason: str) -> dict:
    return {"name": name, "ok": True, "violations": [],
            "detail": f"skipped — {reason}"}


def _check_orphaned_running_trials(store, executor) -> dict:
    """'running' trials with no live executor task — the orphan class
    the startup reaper catches at boot, caught here mid-session."""
    rows = store._fetchall(
        "SELECT id, artifact_path FROM trials WHERE status = 'running'"
    )
    if executor is None:
        if rows:
            return _res(
                "orphaned_running_trials",
                [r["id"] for r in rows],
                f"{len(rows)} running trial(s); no executor to consult "
                "— all are orphaned by definition",
            )
        return _skipped("orphaned_running_trials", "no executor wired")
    task_map = getattr(executor, "_running_tasks", {})
    violations = [r["id"] for r in rows if r["id"] not in task_map]
    return _res(
        "orphaned_running_trials", violations,
        f"{len(rows)} running; {len(violations)} without a live "
        "executor task",
    )


def _check_completed_without_observation(
    store, grace_seconds: int
) -> dict:
    """Completed trials lacking an observation — but only once the
    grace window has passed. completed → record_observation is the
    normal loop order and the analysis phase can lag execution by a
    whole session, so flagging mid-gap is transient noise, not debt.
    Grace defaults to a day; conclude_hypothesis is the hard
    enforcement point, this check only surfaces abandoned debt."""
    rows = store._fetchall(
        """SELECT t.id, COALESCE(t.finished_at, t.created_at) AS done_at
           FROM trials t
           LEFT JOIN observations o ON o.trial_id = t.id
           WHERE t.status = 'completed' AND o.id IS NULL"""
    )
    violations = [
        r["id"] for r in rows
        if r["done_at"] is None
        or _iso_age_seconds(r["done_at"]) > grace_seconds
    ]
    return _res(
        "completed_without_observation", violations,
        f"{len(violations)} completed trial(s) lack an observation "
        f"past the {grace_seconds}s grace — conclude_hypothesis "
        "will reject",
    )


def _iso_age_seconds(ts: str) -> float:
    """Age in seconds of an ISO timestamp ('+00:00' suffix)."""
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return float("inf")  # unparseable → treat as stale, flag it
    return (datetime.now(timezone.utc) - dt).total_seconds()


def _check_unsealed_execution(store) -> dict:
    """Trials that executed with seal_enforced=false under a sandbox
    mode that should have applied the seal — a bypassed boundary."""
    rows = store._fetchall(
        """SELECT id, executor_output_json FROM trials
           WHERE executor_output_json IS NOT NULL"""
    )
    violations = []
    for r in rows:
        try:
            out = json.loads(r["executor_output_json"])
        except (ValueError, TypeError):
            continue
        if (
            out.get("seal_enforced") is False
            and out.get("sandbox") in ("minimal", "full")
        ):
            violations.append(r["id"])
    return _res(
        "unsealed_execution", violations,
        f"{len(violations)} trial(s) ran unsealed under an enforcing "
        "sandbox mode",
    )


def _executed_code_manifest(store, trial_id: str) -> dict | None:
    """Read a trial's executed_code.json from the blob store.

    The staging artifact_dir is deleted after capture
    (capture_artifacts_from_dir cleanup), so the manifest must come
    from artifact_files via trial_artifacts — never from
    trial.artifact_path.
    """
    import gzip as _gzip

    row = store._fetchone(
        """SELECT af.content FROM trial_artifacts ta
           JOIN artifact_files af ON af.content_hash = ta.content_hash
           WHERE ta.trial_id = ? AND ta.filename = 'executed_code.json'""",
        (trial_id,),
    )
    if row is None:
        return None
    try:
        return json.loads(_gzip.decompress(row["content"]))
    except (OSError, ValueError):
        return None


# Interpreter/environment code — sitecustomize, stdlib, spawn
# machinery. env_ref covers the platform; these can never be bundle
# members, so comparing them against the sealed set is a guaranteed
# false positive. Same classification capture_executed_code applies.
_ENV_CODE_RE = re.compile(r"/(?:etc|lib|lib64)/python\d+\.\d+/")


def _check_strace_divergence(store) -> dict:
    """Executed .py files absent from the sealed bundle — code that ran
    outside what capture_bundle sealed. Interpreter/environment files
    (sitecustomize, stdlib) are excluded: they are env, not experiment
    code, and can never be sealed into a bundle."""
    rows = store._fetchall(
        """SELECT t.id, b.code_hash, b.code_hash_extra_json
           FROM trials t
           LEFT JOIN bundles b ON b.trial_id = t.id
           WHERE EXISTS (
               SELECT 1 FROM trial_artifacts ta
               WHERE ta.trial_id = t.id
                 AND ta.filename = 'executed_code.json'
           )"""
    )
    violations = []
    for r in rows:
        manifest = _executed_code_manifest(store, r["id"])
        if manifest is None:
            continue
        bundle_set = {r["code_hash"]} if r["code_hash"] else set()
        try:
            bundle_set.update(json.loads(r["code_hash_extra_json"] or "[]"))
        except ValueError:
            pass
        escaped = [
            f.get("original_path") or f.get("path")
            for f in manifest.get("files", [])
            if f.get("code_hash") and f["code_hash"] not in bundle_set
            and not _ENV_CODE_RE.search(
                f.get("original_path") or f.get("path") or ""
            )
        ]
        if escaped:
            violations.append({"trial_id": r["id"], "paths": escaped})
    return _res(
        "strace_divergence", violations,
        f"{len(violations)} trial(s) executed code outside the sealed "
        "bundle",
    )


def _check_input_data_undigested(store) -> dict:
    """Completed trials whose executed_code.json records an
    input_data file with no digest — the bundle cannot say what data
    it ran on. A recorded policy exclusion (role='sealed') is
    compliant; a null sha256 on input_data is not — the digest was
    required and is missing. schema_version<2 manifests predate the
    input-digest projection: they are 'unrecorded' provenance gaps
    (reported in detail, never flagged — a digest not taken cannot
    be reconstructed)."""
    rows = store._fetchall(
        """SELECT DISTINCT t.id FROM trials t
           JOIN trial_artifacts ta ON ta.trial_id = t.id
           WHERE t.status = 'completed'
             AND ta.filename = 'executed_code.json'"""
    )
    violations = []
    unrecorded = 0
    for r in rows:
        manifest = _executed_code_manifest(store, r["id"])
        if manifest is None:
            continue
        if manifest.get("schema_version", 1) < 2:
            unrecorded += 1
            continue
        undigested = [
            f.get("path")
            for f in manifest.get("files", [])
            if f.get("role") == "input_data" and not f.get("sha256")
        ]
        if undigested:
            violations.append(
                {"trial_id": r["id"], "paths": undigested}
            )
    detail = (
        f"{len(violations)} completed trial(s) have input_data files "
        "with no recorded digest"
    )
    if unrecorded:
        detail += (
            f"; {unrecorded} trial(s) carry pre-v2 manifests "
            "(unrecorded — digests never taken, not reconstructable)"
        )
    return _res("input_data_undigested", violations, detail)


def _check_budget_exceeded(store) -> dict:
    rows = store._fetchall(
        """SELECT p.id, p.budget_max_trials, p.budget_max_wall_time_hours,
                  COUNT(t.id) AS n_trials,
                  COALESCE(SUM(t.duration_seconds), 0) AS wall_s
           FROM programmes p
           LEFT JOIN trials t ON t.programme_id = p.id
           WHERE p.status = 'active'
           GROUP BY p.id"""
    )
    violations = []
    for r in rows:
        over = []
        if r["n_trials"] > r["budget_max_trials"]:
            over.append(f"trials {r['n_trials']}>{r['budget_max_trials']}")
        # wall_time_hours <= 0 means no wall budget was declared —
        # unbounded, not "zero allowed" (the schema's unset default).
        if (
            r["budget_max_wall_time_hours"] > 0
            and r["wall_s"] / 3600 > r["budget_max_wall_time_hours"]
        ):
            over.append(
                f"wall {r['wall_s'] / 3600:.2f}h>"
                f"{r['budget_max_wall_time_hours']}h"
            )
        if over:
            violations.append({"programme_id": r["id"], "exceeded": over})
    return _res(
        "budget_exceeded", violations,
        f"{len(violations)} active programme(s) over budget",
    )


def _check_stuck_hypotheses(store) -> dict:
    rows = store._fetchall(
        """SELECT h.id FROM hypotheses h
           LEFT JOIN trials t ON t.hypothesis_id = h.id
           WHERE h.status = 'under_test' AND t.id IS NULL"""
    )
    violations = [r["id"] for r in rows]
    return _res(
        "stuck_hypotheses", violations,
        f"{len(violations)} under_test hypothes(es) with zero trials — "
        "can never conclude",
    )


def _last_json_line(text: str) -> dict | None:
    """Last JSON dict in possibly multi-line stdout — the result is
    printed last; noise may precede it."""
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except ValueError:
        pass
    for line in reversed(text.splitlines()):
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                return data
        except ValueError:
            continue
    return None


def _check_mislabeled_outcome(store) -> dict:
    """'completed' trials whose record cannot support the label: either
    nothing evidences the run at all (no executor record AND no
    observation), or the recorded output documents a failure — the
    outer wrapper exited 0 while the inner payload says error. A
    completed trial that has an observation but no executor record is
    corroborated by its result, not mislabeled — that provenance gap is
    reported in detail, not flagged. Correct via the
    correct_trial_status tool, never by hand-editing the DB."""
    from ..state.store import _has_executor_record

    rows = store._fetchall(
        """SELECT t.id, t.executor_output_json,
                  (SELECT COUNT(*) FROM observations o
                   WHERE o.trial_id = t.id) AS n_obs
           FROM trials t WHERE t.status = 'completed'"""
    )
    violations = []
    corroborated_gaps = 0
    for r in rows:
        if not _has_executor_record(r["executor_output_json"]):
            if r["n_obs"]:
                # The result exists — the label is corroborated. The
                # missing executor record is a bounded historical
                # provenance gap, not a mislabel.
                corroborated_gaps += 1
                continue
            violations.append(
                {"trial_id": r["id"], "signals": ["no executor record, "
                                                  "no observation"]}
            )
            continue
        out = json.loads(r["executor_output_json"])
        # Skip records already corrected — the correction is recorded.
        if out.get("corrections"):
            continue
        failed_signals = []
        if out.get("status") not in (None, "completed"):
            failed_signals.append(f"outer status={out.get('status')!r}")
        if out.get("exit_code", 0):
            failed_signals.append(f"exit_code={out['exit_code']}")
        # The workspace's run_training payload — outer exit 0 while the
        # inner job crashed leaves "status": "error" inside stdout.
        stdout = out.get("stdout")
        if isinstance(stdout, str):
            inner = _last_json_line(stdout)
            if isinstance(inner, dict):
                if inner.get("status") == "error" or inner.get("error"):
                    failed_signals.append(
                        f"inner status={inner.get('status')!r}"
                    )
                inner_rc = inner.get("_exit_code") or inner.get("exit_code")
                if inner_rc:
                    failed_signals.append(f"inner exit_code={inner_rc}")
        if failed_signals:
            violations.append(
                {"trial_id": r["id"], "signals": failed_signals}
            )
    detail = (
        f"{len(violations)} completed trial(s) whose record cannot "
        "support the label — correct via correct_trial_status"
    )
    if corroborated_gaps:
        detail += (
            f"; {corroborated_gaps} further completed trial(s) have an "
            "observation but no executor record — historical "
            "provenance gap, corroborated not mislabeled"
        )
    return _res("mislabeled_outcome", violations, detail)


def _check_stalled_running_trials(
    store, executor, margin_seconds: int
) -> dict:
    """Running trials that outlived their own enforced deadline — the
    executor kills a trial at its timeout, so a trial still 'running'
    past timeout + margin means the kill/finalize wedged. Also flags
    a finished executor task that never finalized. A multi-hour trial
    within its deadline is healthy work, not a stall — so the check
    is deadline-relative, never a flat wall-clock guess."""
    rows = store._fetchall(
        "SELECT id, started_at FROM trials WHERE status = 'running'"
    )
    if not rows:
        return _res("stalled_running_trials", [], "no running trials")
    if executor is None:
        return _skipped("stalled_running_trials", "no executor wired")
    task_map = getattr(executor, "_running_tasks", {})
    timeout = getattr(executor, "_timeout", None)
    now = time.time()
    violations = []
    for r in rows:
        task = task_map.get(r["id"])
        if task is None:
            continue  # orphaned_running_trials reports this class
        done = getattr(task, "done", None)
        if callable(done) and done():
            violations.append({
                "trial_id": r["id"],
                "detail": "executor task finished but the trial was "
                          "never finalized — wedged finalize path",
            })
            continue
        if timeout is None or not r["started_at"]:
            continue
        try:
            started = datetime.fromisoformat(r["started_at"]).timestamp()
        except ValueError:
            continue
        overdue = now - started - timeout
        if overdue > margin_seconds:
            violations.append({
                "trial_id": r["id"],
                "seconds_past_deadline": int(overdue),
                "deadline_seconds": timeout,
            })
    return _res(
        "stalled_running_trials", violations,
        f"{len(violations)} running trial(s) wedged past their "
        f"enforced deadline (+{margin_seconds}s margin)",
    )


def _check_upstream_connectivity(connectivity) -> dict:
    """Who is connected in what role — the launch-order contract made
    auditable. A configured channel that is down is a violation; an
    unconfigured channel is simply absent (supported standalone mode),
    not a violation. Local role fillers report as local targets."""
    if connectivity is None:
        return _skipped(
            "upstream_connectivity", "no adaptor container wired"
        )
    violations = [
        {
            "channel": c.get("channel"),
            "role": c.get("role"),
            "target": c.get("target"),
            "last_error": c.get("last_error"),
        }
        for c in connectivity
        if c.get("state") != "up"
    ]
    return _res(
        "upstream_connectivity",
        violations,
        f"{len(connectivity)} channel(s) configured; "
        + (f"{len(violations)} down" if violations else "all up"),
    )


def run_checks(
    store,
    *,
    executor=None,
    connectivity=None,
    stalled_trial_seconds: int = DEFAULT_STALLED_MARGIN_SECONDS,
    observation_grace_seconds: int = DEFAULT_OBSERVATION_GRACE_SECONDS,
) -> dict:
    """Run the full Loop-0 invariant suite; return the shared payload."""
    started = time.monotonic()
    checks = [
        _check_upstream_connectivity(connectivity),
        _check_orphaned_running_trials(store, executor),
        _check_completed_without_observation(
            store, observation_grace_seconds
        ),
        _check_unsealed_execution(store),
        _check_strace_divergence(store),
        _check_input_data_undigested(store),
        _check_budget_exceeded(store),
        _check_stuck_hypotheses(store),
        _check_mislabeled_outcome(store),
        _check_stalled_running_trials(
            store, executor, stalled_trial_seconds
        ),
    ]
    return {
        "server": "ml-episteme-mcp",
        "status": (
            "ok" if all(c["ok"] for c in checks) else "violations"
        ),
        "checked_at": _utc_now(),
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
        "checks": checks,
    }


def log_dir_for(store) -> Path:
    """The audit-log dir — <db_dir>/logs/ follows the store, never a
    hardcoded home."""
    return Path(store.path).parent / "logs"


def run_and_log(
    store,
    *,
    executor=None,
    connectivity=None,
    config: dict | None = None,
    trigger: str = "tool",
) -> dict:
    """Run the suite, write the audit log, return the payload.

    The single entry point for the check_invariants tool, the
    /health/deep route, and the monitor — same checks, same logging
    discipline. `trigger` records what invoked the run ("startup",
    "interval", "tool", "route") so the audit trail is
    self-describing.
    """
    from .log import write_check_log

    cfg = config or {}
    payload = run_checks(
        store,
        executor=executor,
        connectivity=connectivity,
        stalled_trial_seconds=cfg.get(
            "stalled_trial_seconds", DEFAULT_STALLED_MARGIN_SECONDS
        ),
        observation_grace_seconds=cfg.get(
            "observation_grace_seconds",
            DEFAULT_OBSERVATION_GRACE_SECONDS,
        ),
    )
    payload["trigger"] = trigger
    log_path = write_check_log(
        log_dir_for(store),
        payload,
        cfg.get("log_max_files", DEFAULT_LOG_MAX_FILES),
    )
    if log_path is not None:
        payload["log_file"] = str(log_path)
    return payload


def start_integrity_monitor(
    store, *, executor=None, connectivity=None, config=None
):
    """Startup + periodic invariant sweeps — the audit trail is on by
    default, not on request.

    Runs one check immediately (trigger="startup") so the day's log
    file exists as evidence that logging began, then schedules a sweep
    every `check_interval_seconds` (trigger="interval", default 300;
    0 disables the periodic sweep — the startup record still lands).
    Must be called inside a running event loop. Returns the periodic
    task, or None when the sweep is disabled.

    This is not a process watchdog — it audits store state; if the
    server hangs, the log simply stops, which an external supervisor
    can notice.
    """
    import asyncio

    cfg = config or {}
    # executor may be a callable returning the current executor — the
    # adaptor attribute can be rebound on reconnect; resolve per sweep.
    def _exec():
        return executor() if callable(executor) else executor

    # connectivity may be a callable returning the current report —
    # channel state changes on reconnect; resolve per sweep.
    def _connectivity():
        return (
            connectivity() if callable(connectivity) else connectivity
        )

    run_and_log(
        store,
        executor=_exec(),
        connectivity=_connectivity(),
        config=cfg,
        trigger="startup",
    )
    interval = cfg.get(
        "check_interval_seconds", DEFAULT_CHECK_INTERVAL_SECONDS
    )
    if not interval or interval <= 0:
        return None

    async def _loop():
        while True:
            await asyncio.sleep(interval)
            try:
                await asyncio.to_thread(
                    run_and_log,
                    store,
                    executor=_exec(),
                    connectivity=_connectivity(),
                    config=cfg,
                    trigger="interval",
                )
            except Exception:
                pass  # the monitor must never take down the server

    return asyncio.create_task(_loop())
