"""Invariant checks for improver.db — the arete integrity audit.

Async: the minted_claims_resolve check crosses the protocol boundary
through the claims adaptor. Channel absent → that check reports
`skipped`, never silently ok — absent means absent.

Report-only: no check mutates state.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_STALE_TOURNAMENT_SECONDS = 3600
DEFAULT_LOG_MAX_FILES = 100


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


def _check_lineage_integrity(store) -> dict:
    """Every non-genesis improver's parent_id must resolve; the parent
    chain must reach genesis without a cycle. A dangling or cyclic
    lineage silently breaks the ancestry check tournaments rely on."""
    rows = store._fetchall(
        "SELECT id, parent_id FROM improver_versions"
    )
    ids = {r["id"] for r in rows}
    violations = []
    for r in rows:
        if r["parent_id"] is not None and r["parent_id"] not in ids:
            violations.append({
                "improver_id": r["id"],
                "problem": f"parent {r['parent_id']} does not exist",
            })
            continue
        # Cycle check — walk to genesis with a seen-set.
        seen: set[str] = set()
        current = r["id"]
        while current is not None and current in ids:
            if current in seen:
                violations.append({
                    "improver_id": r["id"],
                    "problem": "cycle in parent chain",
                })
                break
            seen.add(current)
            parent = store._fetchone(
                "SELECT parent_id FROM improver_versions WHERE id = ?",
                (current,),
            )
            current = parent["parent_id"] if parent else None
    return _res(
        "lineage_integrity", violations,
        f"{len(rows)} improver(s) checked; "
        f"{len(violations)} dangling/cyclic",
    )


def _check_single_champion(store) -> dict:
    """At most one improver may hold the champion pointer — the
    pointer is the protocol's single source of truth."""
    rows = store._fetchall(
        "SELECT id FROM improver_versions WHERE is_champion = 1"
    )
    violations = [
        {"improver_id": r["id"], "problem": "multiple champions"}
        for r in rows[1:]
    ]
    return _res(
        "single_champion", violations,
        f"{len(rows)} champion(s); "
        + (
            "none yet (pre-promotion is normal)"
            if not rows else
            "exactly one incumbent" if len(rows) == 1
            else "MULTIPLE — pointer corruption"
        ),
    )


def _check_stale_open_tournaments(store, stale_seconds: int) -> dict:
    """Open tournaments with no result or pull in
    stale_tournament_seconds — the loop's visible debt."""
    rows = store._fetchall(
        """SELECT t.id, t.created_at,
                  MAX(
                    COALESCE(
                      (SELECT MAX(r.created_at) FROM tournament_results r
                       WHERE r.tournament_id = t.id),
                      (SELECT MAX(e.created_at) FROM evidence_refs e
                       WHERE e.context_type = 'tournament'
                         AND e.context_id = t.id),
                      t.created_at
                    )
                  ) AS last_activity
           FROM tournaments t
           WHERE t.status = 'open'
           GROUP BY t.id"""
    )
    now = datetime.now(timezone.utc)
    violations = []
    for r in rows:
        try:
            age = (
                now - datetime.fromisoformat(r["last_activity"])
            ).total_seconds()
        except ValueError:
            continue
        if age > stale_seconds:
            violations.append({
                "tournament_id": r["id"],
                "stale_seconds": int(age),
                "last_activity": r["last_activity"],
            })
    return _res(
        "stale_open_tournaments", violations,
        f"{len(violations)} open tournament(s) idle for "
        f">{stale_seconds}s",
    )


def _check_rejected_have_reasons(store) -> dict:
    """Rejected proposals must carry the rejection reason — an
    unexplained refusal is an accountability gap."""
    rows = store._fetchall(
        "SELECT id FROM meta_change_proposals "
        "WHERE status = 'rejected' AND "
        "(rejection_reason IS NULL OR rejection_reason = '')"
    )
    violations = [{"proposal_id": r["id"]} for r in rows]
    return _res(
        "rejected_have_reasons", violations,
        f"{len(violations)} rejected proposal(s) lack a reason",
    )


def _check_closed_have_gain(store) -> dict:
    """Closed tournaments should carry recursive_gain — the close
    path computes it, so a null means the seal happened another
    way."""
    rows = store._fetchall(
        "SELECT id FROM tournaments "
        "WHERE status = 'closed' AND recursive_gain IS NULL"
    )
    violations = [{"tournament_id": r["id"]} for r in rows]
    return _res(
        "closed_have_gain", violations,
        f"{len(violations)} closed tournament(s) with no "
        "recursive_gain",
    )


def _check_conditional_human_gate(store) -> dict:
    """Defense-in-depth audit: an active policy whose improver
    implements a conditional proposal must trace to a promote
    decision with a human decided_by. Enforcement blocks this at
    promote_policy — a violation here means the gate was bypassed."""
    rows = store._fetchall(
        """SELECT pv.id AS policy_id, pv.improver_version_id,
                  pv.decision_id, i.proposal_id, d.decided_by
           FROM policy_versions pv
           JOIN improver_versions i
             ON i.id = pv.improver_version_id
           JOIN meta_change_proposals p
             ON p.id = i.proposal_id AND p.status = 'conditional'
           JOIN meta_decisions d ON d.id = pv.decision_id
           WHERE pv.status = 'active'"""
    )
    violations = [
        {
            "policy_version_id": r["policy_id"],
            "improver_id": r["improver_version_id"],
            "proposal_id": r["proposal_id"],
            "decided_by": r["decided_by"],
            "problem": "conditional proposal promoted without a "
                       "human decided_by",
        }
        for r in rows
        if not (r["decided_by"] or "").startswith("human:")
    ]
    return _res(
        "conditional_human_gate", violations,
        f"{len(rows)} active conditional-policy row(s) audited; "
        f"{len(violations)} without human authority",
    )


def _check_decisions_reference_candidates(store) -> dict:
    """Every meta_decision's candidate (and contract/tournament, when
    given) must resolve — a decision about nothing is a broken
    record."""
    violations = []
    for r in store._fetchall(
        "SELECT id, candidate_improver_id, contract_id, "
        "tournament_id FROM meta_decisions"
    ):
        if store.get_improver(r["candidate_improver_id"]) is None:
            violations.append({
                "decision_id": r["id"],
                "problem": f"candidate "
                f"{r['candidate_improver_id']} does not exist",
            })
        if r["contract_id"] and (
            store.get_meta_contract(r["contract_id"]) is None
        ):
            violations.append({
                "decision_id": r["id"],
                "problem": f"contract {r['contract_id']} "
                "does not exist",
            })
        if r["tournament_id"] and (
            store.get_tournament(r["tournament_id"]) is None
        ):
            violations.append({
                "decision_id": r["id"],
                "problem": f"tournament {r['tournament_id']} "
                "does not exist",
            })
    return _res(
        "decisions_reference_candidates", violations,
        f"{len(violations)} dangling decision reference(s)",
    )


async def _check_minted_claims_resolve(store, claims) -> dict:
    """Cross-server: every stored decision.claim_id must resolve
    through the claims adaptor — minted memory that no longer exists
    is a broken provenance chain."""
    if claims is None:
        return _skipped(
            "minted_claims_resolve", "no claims channel wired"
        )
    rows = store._fetchall(
        "SELECT id, claim_id FROM meta_decisions "
        "WHERE claim_id IS NOT NULL"
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
                    "decision_id": r["id"],
                    "claim_id": r["claim_id"],
                    "problem": "unresolvable in anamnesis",
                })
        except Exception as e:
            violations.append({
                "decision_id": r["id"],
                "claim_id": r["claim_id"],
                "problem": f"lookup failed: {e}",
            })
    return _res(
        "minted_claims_resolve", violations,
        f"{len(rows)} minted claim(s) checked; "
        f"{len(violations)} unresolvable",
    )


async def _check_dangling_campaign_links(store, loop1) -> dict:
    """Cross-server: every tournament_campaigns link must resolve to a
    real upstream campaign — a link that names a campaign Loop 1 has
    never heard of is a broken orchestration trail. Report-only; the
    read goes through the loop1 evidence channel (get_campaign)."""
    rows = store._fetchall(
        "SELECT id, tournament_id, arm, campaign_id "
        "FROM tournament_campaigns"
    )
    if not rows:
        return _res(
            "dangling_campaign_links", [],
            "no tournament-campaign links recorded",
        )
    if loop1 is None:
        return _skipped(
            "dangling_campaign_links",
            f"{len(rows)} link(s) recorded but no loop1 channel "
            "wired — cannot resolve upstream",
        )
    violations = []
    for r in rows:
        try:
            raw = await loop1.pull(
                "get_campaign", {"campaign_id": r["campaign_id"]}
            )
            data = json.loads(raw) if isinstance(raw, str) else raw
            if not data or data.get("error"):
                violations.append({
                    "link_id": r["id"],
                    "tournament_id": r["tournament_id"],
                    "arm": r["arm"],
                    "campaign_id": r["campaign_id"],
                    "problem": "campaign unresolvable upstream",
                })
        except Exception as e:
            violations.append({
                "link_id": r["id"],
                "campaign_id": r["campaign_id"],
                "problem": f"lookup failed: {e}",
            })
    return _res(
        "dangling_campaign_links", violations,
        f"{len(rows)} link(s) checked; {len(violations)} dangling",
    )


def _check_orphaned_arm_results(store) -> dict:
    """A tournament_result whose descendant_spec names a campaign_id
    must have a matching tournament_campaigns row — otherwise the
    result claims an orchestrated provenance that was never linked."""
    rows = store._fetchall(
        "SELECT id, tournament_id, arm, descendant_spec_json "
        "FROM tournament_results"
    )
    violations = []
    for r in rows:
        try:
            spec = json.loads(r["descendant_spec_json"])
        except ValueError:
            continue
        campaign_id = spec.get("campaign_id")
        if not campaign_id:
            continue
        link = store._fetchone(
            "SELECT id FROM tournament_campaigns "
            "WHERE tournament_id = ? AND campaign_id = ?",
            (r["tournament_id"], campaign_id),
        )
        if link is None:
            violations.append({
                "result_id": r["id"],
                "tournament_id": r["tournament_id"],
                "campaign_id": campaign_id,
                "problem": "result names a campaign with no "
                           "tournament_campaigns link",
            })
    return _res(
        "orphaned_arm_results", violations,
        f"{len(violations)} result(s) name unlinked campaign(s)",
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


async def run_checks(
    store,
    *,
    claims=None,
    loop1=None,
    connectivity=None,
    stale_tournament_seconds: int = DEFAULT_STALE_TOURNAMENT_SECONDS,
) -> dict:
    """Run the full arete invariant suite; return the payload."""
    started = time.monotonic()
    checks = [
        _check_upstream_connectivity(connectivity),
        _check_lineage_integrity(store),
        _check_single_champion(store),
        _check_stale_open_tournaments(store, stale_tournament_seconds),
        _check_rejected_have_reasons(store),
        _check_closed_have_gain(store),
        _check_conditional_human_gate(store),
        _check_decisions_reference_candidates(store),
        await _check_minted_claims_resolve(store, claims),
        await _check_dangling_campaign_links(store, loop1),
        _check_orphaned_arm_results(store),
    ]
    return {
        "server": "ml-arete-mcp",
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
    loop1=None,
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
        loop1=loop1,
        connectivity=connectivity,
        stale_tournament_seconds=cfg.get(
            "stale_tournament_seconds",
            DEFAULT_STALE_TOURNAMENT_SECONDS,
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
    store, *, claims=None, loop1=None, connectivity=None, config=None
):
    """Startup + periodic invariant sweeps — the audit trail is on by
    default, not on request. Runs one check immediately
    (trigger="startup"), then sweeps every `check_interval_seconds`
    (default 300; 0 disables the periodic sweep — the startup record
    still lands). Call inside a running event loop; returns the
    periodic task or None."""
    import asyncio

    cfg = config or {}
    # claims/loop1 may be callables returning the current channel —
    # the adaptor attribute can be rebound on disconnect; resolve
    # per sweep.
    def _claims():
        return claims() if callable(claims) else claims

    def _loop1():
        return loop1() if callable(loop1) else loop1

    # connectivity may be a callable returning the current report —
    # channel state changes on reconnect; resolve per sweep.
    def _connectivity():
        return (
            connectivity() if callable(connectivity) else connectivity
        )

    await run_and_log(
        store,
        claims=_claims(),
        loop1=_loop1(),
        connectivity=_connectivity(),
        config=cfg,
        trigger="startup",
    )
    interval = cfg.get("check_interval_seconds", 300)
    if not interval or interval <= 0:
        return None

    async def _loop():
        while True:
            await asyncio.sleep(interval)
            try:
                await run_and_log(
                    store,
                    claims=_claims(),
                    loop1=_loop1(),
                    connectivity=_connectivity(),
                    config=cfg,
                    trigger="interval",
                )
            except Exception:
                pass  # the monitor must never take down the server

    return asyncio.create_task(_loop())
