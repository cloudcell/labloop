"""Invariant checks for search.db — the zetesis integrity audit.

Async: the minted_claims_resolve check crosses the protocol boundary
through the claims adaptor. Channel absent → that check reports
`skipped`, never silently ok — absent means absent.

Report-only: no check mutates state.
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from .. import _grounded_constants as _gc

DEFAULT_STALE_INVESTIGATION_SECONDS = _gc.STALE_INVESTIGATION_SECONDS.value
DEFAULT_STALE_CAMPAIGN_SECONDS = _gc.STALE_CAMPAIGN_SECONDS.value
DEFAULT_LOG_MAX_FILES = _gc.LOG_MAX_FILES.value


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


def _check_stale_open_investigations(store, stale_seconds: int) -> dict:
    """Open investigations with no evidence pull in
    stale_investigation_seconds — the loop's visible debt."""
    rows = store._fetchall(
        """SELECT i.id, i.created_at,
                  MAX(e.created_at) AS last_pull
           FROM investigations i
           LEFT JOIN evidence_refs e ON e.investigation_id = i.id
           WHERE i.status = 'open'
           GROUP BY i.id"""
    )
    now = datetime.now(timezone.utc)
    violations = []
    for r in rows:
        last = r["last_pull"] or r["created_at"]
        try:
            age = (now - datetime.fromisoformat(last)).total_seconds()
        except ValueError:
            continue
        if age > stale_seconds:
            violations.append({
                "investigation_id": r["id"],
                "stale_seconds": int(age),
                "last_activity": last,
            })
    return _res(
        "stale_open_investigations", violations,
        f"{len(violations)} open investigation(s) idle for "
        f">{stale_seconds}s",
    )


def _check_asserted_without_claim(store) -> dict:
    """Asserted findings with null claim_id — an inconsistent row."""
    rows = store._fetchall(
        "SELECT id, investigation_id FROM findings "
        "WHERE status = 'asserted' AND claim_id IS NULL"
    )
    violations = [
        {"finding_id": r["id"], "investigation_id": r["investigation_id"]}
        for r in rows
    ]
    return _res(
        "asserted_without_claim", violations,
        f"{len(violations)} asserted finding(s) carry no claim_id",
    )


def _check_concluded_unminted(store) -> dict:
    """Investigations concluded 'findings' while provisional findings
    remain — conclusion claimed minting that did not happen (whatever
    the reason: the claim did not reach memory)."""
    rows = store._fetchall(
        """SELECT i.id AS inv_id, f.id AS finding_id
           FROM investigations i
           JOIN findings f ON f.investigation_id = i.id
           WHERE i.status = 'concluded' AND i.verdict = 'findings'
             AND f.status = 'provisional'"""
    )
    violations = [
        {"investigation_id": r["inv_id"], "finding_id": r["finding_id"]}
        for r in rows
    ]
    return _res(
        "concluded_unminted", violations,
        f"{len(violations)} provisional finding(s) in concluded "
        "'findings' investigations",
    )


async def _check_minted_claims_resolve(store, claims) -> dict:
    """Cross-server: every stored finding.claim_id must resolve through
    the claims adaptor — minted memory that no longer exists is a
    broken provenance chain."""
    if claims is None:
        return _skipped(
            "minted_claims_resolve", "no claims channel wired"
        )
    rows = store._fetchall(
        "SELECT id, claim_id FROM findings WHERE claim_id IS NOT NULL"
    )
    violations = []
    for r in rows:
        try:
            raw = await claims.pull(
                "get_claim", {"claim_id": r["claim_id"]}
            )
            data = json.loads(raw) if isinstance(raw, str) else raw
            if not data or data.get("error"):
                violations.append({
                    "finding_id": r["id"],
                    "claim_id": r["claim_id"],
                    "problem": "unresolvable in anamnesis",
                })
        except Exception as e:
            violations.append({
                "finding_id": r["id"],
                "claim_id": r["claim_id"],
                "problem": f"lookup failed: {e}",
            })
    return _res(
        "minted_claims_resolve", violations,
        f"{len(rows)} minted claim(s) checked; "
        f"{len(violations)} unresolvable",
    )


def _check_closed_campaigns_scored(store) -> dict:
    """Closed campaigns with null promotion_score — the freeze forgot
    the number the verdict interprets."""
    rows = store._fetchall(
        "SELECT id FROM promotion_campaigns "
        "WHERE status = 'closed' AND promotion_score IS NULL"
    )
    violations = [{"campaign_id": r["id"]} for r in rows]
    return _res(
        "closed_campaigns_scored", violations,
        f"{len(violations)} closed campaign(s) carry no "
        "promotion_score",
    )


def _check_campaigns_awaiting_verdict(store) -> dict:
    """Closed campaigns with no recorded verdict — the frozen score
    exists but no decision interprets it. The debt is dischargeable:
    post-close pull_campaign_evidence is legal while decision_id is
    unset, so a campaign closed before its evidence pull can still
    acquire the eref record_promotion_verdict requires (arete's
    decision_debt is the analogue for tournaments)."""
    rows = store._fetchall(
        "SELECT id, promotion_score FROM promotion_campaigns "
        "WHERE status = 'closed' AND decision_id IS NULL"
    )
    violations = [
        {
            "campaign_id": r["id"],
            "promotion_score": r["promotion_score"],
            "detail": "closed with a frozen score but no verdict — "
                      "pull_campaign_evidence post-close + "
                      "record_promotion_verdict discharges it",
        }
        for r in rows
    ]
    return _res(
        "campaigns_awaiting_verdict", violations,
        f"{len(violations)} closed campaign(s) await a verdict — "
        "pull_campaign_evidence post-close + record_promotion_"
        "verdict discharges each",
    )


def _check_unscoreable_campaigns(store) -> dict:
    """Open campaigns whose results carry an unscorable primary_metric
    (null/non-numeric/non-finite — rows predating the write gate, or
    written before it existed). Such a campaign can never score at
    close and cannot be repaired — results are insert-only;
    abandon_campaign is the exit. Reported, not auto-gating."""
    rows = store._fetchall(
        """SELECT r.id AS result_id, r.campaign_id, r.metrics_json,
                  c.primary_metric
           FROM campaign_results r
           JOIN promotion_campaigns c ON c.id = r.campaign_id
           WHERE c.status = 'open'"""
    )
    violations = []
    for r in rows:
        try:
            metrics = json.loads(r["metrics_json"])
        except (ValueError, TypeError):
            metrics = {}
        v = metrics.get(r["primary_metric"])
        if (
            not isinstance(v, (int, float))
            or isinstance(v, bool)
            or not math.isfinite(v)
        ):
            violations.append({
                "campaign_id": r["campaign_id"],
                "result_id": r["result_id"],
                "metric": r["primary_metric"],
                "value": v,
            })
    return _res(
        "unscoreable_campaigns", violations,
        f"{len(violations)} open campaign result(s) carry an "
        "unscorable primary_metric (close would wedge; "
        "abandon_campaign is the exit)",
    )


def _check_unrunnable_campaigns(store) -> dict:
    """Open campaigns that can never produce results: they carry an
    orchestration budget that fails carryability (missing/incorrect
    programmes_per_arm or trials_per_programme), have no spawn
    records, and no campaign results. unscoreable_campaigns only sees
    rows that exist — this check sees the campaign with no way to get
    any. Budget-free campaigns are caller-driven and don't flag.
    Reported, not auto-gating; abandon_campaign is the exit."""
    from ml_zetesis_mcp.enforcement.checks import (
        check_campaign_budget_carryable,
    )

    rows = store._fetchall(
        """SELECT c.id, c.budget_json
           FROM promotion_campaigns c
           WHERE c.status = 'open'
             AND c.budget_json IS NOT NULL
             AND NOT EXISTS (
                 SELECT 1 FROM campaign_spawns s
                 WHERE s.campaign_id = c.id)
             AND NOT EXISTS (
                 SELECT 1 FROM campaign_results r
                 WHERE r.campaign_id = c.id)"""
    )
    violations = []
    for r in rows:
        try:
            budget = json.loads(r["budget_json"])
        except (ValueError, TypeError):
            budget = {}
        # An empty budget is caller-driven, not orchestration-bound —
        # the wedge signature is a *declared* budget that can't spawn.
        if not budget:
            continue
        reason = check_campaign_budget_carryable(budget)
        if reason is not None:
            violations.append({
                "campaign_id": r["id"],
                "budget": budget,
                "reason": reason,
                "exit": "abandon_campaign",
            })
    return _res(
        "unrunnable_campaigns", violations,
        f"{len(violations)} open campaign(s) carry a budget that "
        "cannot spawn programmes and have produced nothing "
        "(structurally wedged; abandon_campaign is the exit)",
    )


def _check_incomplete_campaigns(store, stale_seconds: int) -> dict:
    """Open campaigns that have started but cannot close as they
    stand: ≥1 spawn or result row, campaign_results covering fewer
    than both arms, idle past stale_campaign_seconds. close_campaign
    needs results for BOTH arms — a one-armed campaign left mid-
    orchestration is invisible to campaigns_awaiting_verdict (closed
    only), unrunnable_campaigns (no spawns AND no results), and
    unscoreable_campaigns (bad metrics on existing rows). Reported,
    not auto-gating: a campaign waiting on a running programme is
    legitimately one-armed — the idle gate is what makes it debt."""
    rows = store._fetchall(
        """SELECT c.id, c.created_at
           FROM promotion_campaigns c
           WHERE c.status = 'open'
             AND (EXISTS (SELECT 1 FROM campaign_spawns s
                          WHERE s.campaign_id = c.id)
                  OR EXISTS (SELECT 1 FROM campaign_results r
                             WHERE r.campaign_id = c.id))"""
    )
    now = datetime.now(timezone.utc)
    violations = []
    for r in rows:
        cid = r["id"]
        res_rows = store._fetchall(
            "SELECT arm, created_at FROM campaign_results "
            "WHERE campaign_id = ?", (cid,)
        )
        covered = {row["arm"] for row in res_rows}
        if covered >= {"champion", "challenger"}:
            continue
        spawn_rows = store._fetchall(
            "SELECT arm, status, created_at FROM campaign_spawns "
            "WHERE campaign_id = ?", (cid,)
        )
        last = max(
            [r["created_at"], *(x["created_at"] for x in res_rows),
             *(x["created_at"] for x in spawn_rows)]
        )
        try:
            idle = (now - datetime.fromisoformat(last)).total_seconds()
        except ValueError:
            continue
        if idle <= stale_seconds:
            continue
        violations.append({
            "campaign_id": cid,
            "arms_with_results": sorted(covered),
            "missing_arms": sorted({"champion", "challenger"} - covered),
            "pending_spawns": sum(
                1 for x in spawn_rows if x["status"] == "spawned"
            ),
            "idle_seconds": int(idle),
            "last_activity": last,
            "exit": "populate the missing arm "
                    "(spawn_arm_programme campaign_arm=… / "
                    "spawn_campaign_programme arm=…) or "
                    "abandon_campaign",
        })
    return _res(
        "incomplete_campaigns", violations,
        f"{len(violations)} open campaign(s) started but one-armed "
        f"and idle >{stale_seconds}s — close needs results on both "
        "arms; fill the missing side via spawn_arm_programme "
        "campaign_arm=… / spawn_campaign_programme arm=…, or "
        "abandon_campaign is the exit (violations name the missing "
        "arms)",
    )


def _check_campaign_results_have_campaign(store) -> dict:
    """Campaign results referencing a nonexistent campaign — an
    orphaned result is provenance pointing nowhere."""
    rows = store._fetchall(
        """SELECT r.id, r.campaign_id
           FROM campaign_results r
           LEFT JOIN promotion_campaigns c ON c.id = r.campaign_id
           WHERE c.id IS NULL"""
    )
    violations = [
        {"result_id": r["id"], "campaign_id": r["campaign_id"]}
        for r in rows
    ]
    return _res(
        "campaign_results_have_campaign", violations,
        f"{len(violations)} campaign result(s) reference a missing "
        "campaign",
    )


def _check_single_roster_champion(store) -> dict:
    """At most one roster entry may carry derived_status 'champion' —
    the local mirror of the upstream single-incumbent rule."""
    rows = store._fetchall(
        "SELECT id FROM roster_entries WHERE derived_status = 'champion'"
    )
    violations = (
        [{"candidate_ids": [r["id"] for r in rows]}]
        if len(rows) > 1 else []
    )
    return _res(
        "single_roster_champion", violations,
        f"{len(rows)} roster entrant(s) marked champion",
    )


def _check_spawns_have_campaign(store) -> dict:
    """Spawn rows referencing a nonexistent campaign — the durable
    'this programme exists because this campaign ran' statement must
    point somewhere."""
    rows = store._fetchall(
        """SELECT s.id, s.campaign_id
           FROM campaign_spawns s
           LEFT JOIN promotion_campaigns c ON c.id = s.campaign_id
           WHERE c.id IS NULL"""
    )
    violations = [
        {"spawn_id": r["id"], "campaign_id": r["campaign_id"]}
        for r in rows
    ]
    return _res(
        "campaign_spawns_have_campaign", violations,
        f"{len(violations)} spawn(s) reference a missing campaign",
    )


def _check_spawn_budget(store) -> dict:
    """Per (campaign, arm) spawn counts must not exceed the carried
    programmes_per_arm — enforced at spawn_campaign_programme; a
    violation here means the cap was bypassed."""
    rows = store._fetchall(
        """SELECT s.campaign_id, s.arm, COUNT(*) AS n,
                  c.budget_json
           FROM campaign_spawns s
           JOIN promotion_campaigns c ON c.id = s.campaign_id
           GROUP BY s.campaign_id, s.arm"""
    )
    violations = []
    for r in rows:
        budget = json.loads(r["budget_json"] or "{}")
        cap = budget.get("programmes_per_arm")
        if cap is not None and r["n"] > int(cap):
            violations.append({
                "campaign_id": r["campaign_id"],
                "arm": r["arm"],
                "spawned": r["n"],
                "programmes_per_arm": cap,
            })
    return _res(
        "spawn_budget_violations", violations,
        f"{len(violations)} campaign arm(s) exceed the carried "
        "spawn cap",
    )


def _check_results_spawned(store) -> dict:
    """On orchestrated campaigns (those with any spawn rows), every
    recorded result must name a programme spawned under the same
    campaign and arm — spawn-scoped attribution audited after the
    fact."""
    # programme_id is UNIQUE in campaign_spawns — one programme can be
    # bound to at most one campaign. The invariant is: a result's
    # programme must be spawned under THIS campaign (arm matches), or —
    # for campaigns with no spawns at all — never spawned anywhere.
    # rc-12: the spawn lookup used to ignore campaign_id, so a
    # programme bound to another campaign passed as an alibi, and
    # spawn-less campaigns were never examined at all.
    rows = store._fetchall(
        """SELECT r.id, r.campaign_id, r.arm, r.programme_id
           FROM campaign_results r"""
    )
    violations = []
    for r in rows:
        spawn = store._fetchone(
            "SELECT campaign_id, arm FROM campaign_spawns "
            "WHERE programme_id = ?",
            (r["programme_id"],),
        )
        if spawn is not None and spawn["campaign_id"] != r["campaign_id"]:
            violations.append({
                "result_id": r["id"],
                "campaign_id": r["campaign_id"],
                "programme_id": r["programme_id"],
                "problem": f"programme is spawn-bound to campaign "
                           f"{spawn['campaign_id']}, not this campaign",
            })
            continue
        if spawn is None:
            camp_has_spawns = store._fetchone(
                "SELECT 1 FROM campaign_spawns WHERE campaign_id = ? "
                "LIMIT 1",
                (r["campaign_id"],),
            )
            if camp_has_spawns is not None:
                violations.append({
                    "result_id": r["id"],
                    "campaign_id": r["campaign_id"],
                    "programme_id": r["programme_id"],
                    "problem": "programme not spawned under this campaign",
                })
            continue
        if spawn["arm"] != r["arm"]:
            violations.append({
                "result_id": r["id"],
                "campaign_id": r["campaign_id"],
                "programme_id": r["programme_id"],
                "problem": f"spawned for arm '{spawn['arm']}', "
                           f"result claims '{r['arm']}'",
            })
    return _res(
        "results_spawn_scoped", violations,
        f"{len(violations)} result(s) breach spawn scope",
    )


def _check_upstream_connectivity(connectivity) -> dict:
    """Who is connected in what role — the launch-order contract made
    auditable. A configured channel that is down is a violation; an
    unconfigured channel is simply absent (supported standalone mode),
    not a violation."""
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
            "last_operation": c.get("last_operation"),
            "last_failed_operation": c.get("last_failed_operation"),
            "last_failed_at": c.get("last_failed_at"),
        }
        for c in connectivity
        if c.get("state") != "up"
    ]
    # Busy-but-up is not a violation (the channel may recover — only
    # a down channel gates), but it must surface in the summary:
    # "all up" next to a probe-busy channel is the self-contradicting
    # payload rc-15 flagged.
    busy = sum(
        1 for c in connectivity
        if c.get("state") == "up" and c.get("probe") == "busy"
    )
    parts = []
    if violations:
        parts.append(f"{len(violations)} down")
    if busy:
        parts.append(f"{busy} busy")
    res = _res(
        "upstream_connectivity",
        violations,
        f"{len(connectivity)} channel(s) configured; "
        + (", ".join(parts) if parts else "all up"),
    )
    # Full per-channel state (probe, busy, last_op, error attribution)
    # rides the payload — reachable from check_invariants, /health/deep,
    # and the check log without a resources/read.
    res["channels"] = connectivity
    return res


async def run_checks(
    store,
    *,
    claims=None,
    connectivity=None,
    stale_investigation_seconds: int = DEFAULT_STALE_INVESTIGATION_SECONDS,
    stale_campaign_seconds: int = DEFAULT_STALE_CAMPAIGN_SECONDS,
) -> dict:
    """Run the full zetesis invariant suite; return the payload."""
    started = time.monotonic()
    checks = [
        _check_upstream_connectivity(connectivity),
        _check_stale_open_investigations(
            store, stale_investigation_seconds
        ),
        _check_asserted_without_claim(store),
        _check_concluded_unminted(store),
        _check_closed_campaigns_scored(store),
        _check_campaigns_awaiting_verdict(store),
        _check_unscoreable_campaigns(store),
        _check_unrunnable_campaigns(store),
        _check_incomplete_campaigns(store, stale_campaign_seconds),
        _check_campaign_results_have_campaign(store),
        _check_spawns_have_campaign(store),
        _check_spawn_budget(store),
        _check_results_spawned(store),
        _check_single_roster_champion(store),
        await _check_minted_claims_resolve(store, claims),
    ]
    return {
        "server": "ml-zetesis-mcp",
        "status": (
            "ok" if all(c["ok"] for c in checks) else "violations"
        ),
        "checked_at": _utc_now(),
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
        "checks": checks,
    }


def log_dir_for(store) -> Path:
    return Path(store.db_path).parent / "logs"


async def run_and_log(
    store,
    *,
    claims=None,
    connectivity=None,
    config: dict | None = None,
    trigger: str = "tool",
) -> dict:
    """Run the suite, write the audit log, return the payload.

    `trigger` records what invoked the run ("startup", "interval",
    "tool", "route") so the audit trail is self-describing.
    """
    from .log import write_check_log

    cfg = config or {}
    payload = await run_checks(
        store,
        claims=claims,
        connectivity=connectivity,
        stale_investigation_seconds=cfg.get(
            "stale_investigation_seconds",
            DEFAULT_STALE_INVESTIGATION_SECONDS,
        ),
        stale_campaign_seconds=cfg.get(
            "stale_campaign_seconds",
            DEFAULT_STALE_CAMPAIGN_SECONDS,
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


async def start_integrity_monitor(
    store, *, claims=None, connectivity=None, config=None
):
    """Startup + periodic invariant sweeps — the audit trail is on by
    default, not on request. Runs one check immediately
    (trigger="startup"), then sweeps every `check_interval_seconds`
    (default 300; 0 disables the periodic sweep — the startup record
    still lands). Call inside a running event loop; returns the
    periodic task or None."""
    import asyncio

    cfg = config or {}
    # claims may be a callable returning the current channel — the
    # adaptor attribute can be rebound on disconnect; resolve per sweep.
    def _claims():
        return claims() if callable(claims) else claims

    # connectivity may be a callable returning the current report —
    # channel state changes on reconnect; resolve per sweep.
    def _connectivity():
        return (
            connectivity() if callable(connectivity) else connectivity
        )

    await run_and_log(
        store,
        claims=_claims(),
        connectivity=_connectivity(),
        config=cfg,
        trigger="startup",
    )
    interval = cfg.get("check_interval_seconds", _gc.CHECK_INTERVAL_SECONDS.value)
    if not interval or interval <= 0:
        return None

    async def _loop():
        while True:
            await asyncio.sleep(interval)
            try:
                await run_and_log(
                    store,
                    claims=_claims(),
                    connectivity=_connectivity(),
                    config=cfg,
                    trigger="interval",
                )
            except Exception:
                pass  # the monitor must never take down the server

    return asyncio.create_task(_loop())
