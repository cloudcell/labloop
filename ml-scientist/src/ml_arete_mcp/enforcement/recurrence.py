"""Recurrent self-improvement protocol — plan-20260926-0438Z.

Consultation duty as a rejection point: mutating tools refuse until
the server's status digest has been read within a freshness TTL
(``[enforcement] status_freshness_seconds``; ``0`` disables). The
digest read also populates a cache whose top recommendation is
injected into every successful tool result as ``next``.

Governance debt gates writes on top of the freshness gate:

- **Open violations** — unacknowledged integrity findings refuse all
  mutating tools except ``acknowledge_violation`` and the
  remediation tools for the flagged checks.
- **Decision debt** — closed tournaments with no linked
  ``meta_decision`` refuse all mutating tools except
  ``record_meta_decision`` and ``rollback`` (the tools that resolve
  it). A ``hold`` verdict is itself a decision — the built-in
  escape hatch for a genuinely undecidable tournament.

``improvement_due`` is advisory only (tier-2 recommended action, not
a hard blocker): when the most recent improvement activity — a
decision, proposal, or tournament event — is older than
``[enforcement] improvement_epoch_seconds`` (default 86400), the
digest recommends re-entering the loop. A fully idle loop is itself
the signal; no "open work" qualifier.

Known v1 limitation (documented in the plan): the watermark is
server-global, not keyed to the calling session — one client's
consult satisfies another's duty, and aggregator polls of
``improver://status`` (e.g. agora's upstream fetches) stamp it as
well. Accepted under the single-writer lab assumption; session-keyed
watermarks are v2.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from mcp.types import CallToolResult, TextContent

from ..integrity.checks import log_dir_for
from ..integrity.log import list_check_logs, read_run

STATUS_RESOURCE = "improver://status"
STATUS_SESSION = "improver://session"

READ_PREFIXES = (
    "get_", "list_", "check_", "verify_", "assess_", "describe_",
    "wait_",
)

# The debt-resolution pair plus the shared audit/remediation set —
# exempt from every gate so a gate can never strand its own remedy.
ALWAYS_EXEMPT = {
    "check_invariants",
    "acknowledge_violation",
    "correct_tournament_result",
    "record_meta_decision",
    "rollback",
}

REMEDY_TOOLS = {
    "lineage_integrity": set(),
    "single_champion": {"promote_policy", "rollback"},
    # Both exits are reachable: paired → close_tournament,
    # unpaired → void_tournament.
    "stale_open_tournaments": {"close_tournament", "void_tournament"},
    "rejected_have_reasons": {"record_meta_decision"},
    "closed_have_gain": set(),
    "conditional_human_gate": {"record_meta_decision"},
    "decisions_reference_candidates": {"record_meta_decision"},
    "minted_claims_resolve": set(),
    "dangling_campaign_links": {
        "open_tournament", "close_tournament",
        "record_tournament_result",
    },
    # The full discharge path: decide (hold counts) or rollback, plus
    # the two evidence miners that mint the evidence_refs a decision
    # requires. Missing any one re-strands the gate (F-17).
    "decision_debt": {
        "record_meta_decision", "rollback",
        "pull_evidence", "pull_arm_evidence",
    },
    "orphaned_arm_results": set(),
    "upstream_connectivity": set(),
}

# Tools that mint the discharge currency for decision debt —
# record_meta_decision requires ≥1 evidence_ref and only these two
# produce them. Exempt from the *decision-debt* gate only: a gate
# must never strand its own remedy's inputs. They remain subject to
# the violations gate — a separate debt.
DEBT_DISCHARGE_TOOLS = {"pull_evidence", "pull_arm_evidence"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _refusal(msg: str) -> CallToolResult:
    """Tool-error refusal — same wire shape as tools/schemas.fail()."""
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps({"error": msg}))],
        is_error=True,
    )


def canonical_ref(item: Any) -> str:
    """Stable object reference for a violation entry."""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for k in sorted(item):
            if k == "id" or k.endswith("_id"):
                return str(item[k])
        return json.dumps(item, sort_keys=True)
    return str(item)


def open_violations(store) -> list[dict]:
    """Unacknowledged violations from the latest logged check run."""
    log_dir = log_dir_for(store)
    runs = list_check_logs(log_dir, limit=1)
    if not runs:
        return []
    run = read_run(log_dir, runs[0]["file"], runs[0]["index"])
    if not run:
        return []
    acked = store.violation_ack_keys()
    out = []
    for c in run.get("checks", []):
        for v in c.get("violations", []):
            ref = canonical_ref(v)
            if (c.get("name"), ref) not in acked:
                out.append({
                    "check": c.get("name"),
                    "object_ref": ref,
                    "detail": c.get("detail", ""),
                })
    return out


def check_no_open_violations(store, tool_name: str) -> str | None:
    """Violation gate: refuse mutating tools while unacknowledged
    findings exist — except the tools that remediate them."""
    openv = open_violations(store)
    if not openv:
        return None
    remedies = set()
    for v in openv:
        remedies |= REMEDY_TOOLS.get(v["check"], set())
    if tool_name in remedies:
        return None
    kinds = sorted({v["check"] for v in openv})
    return (
        f"{len(openv)} open integrity violation(s) "
        f"({', '.join(kinds)}) — acknowledge_violation or remediate "
        "the flagged record(s) before mutating."
    )


def undecided_tournaments(store) -> list[str]:
    """Closed tournaments with no meta_decision linked via
    tournament_id — the decision-debt set. Live SQL, not the check
    log: it clears the moment a decision is recorded (including a
    ``hold``)."""
    rows = store.conn.execute(
        """SELECT t.id FROM tournaments t
           WHERE t.status = 'closed' AND NOT EXISTS (
               SELECT 1 FROM meta_decisions d
               WHERE d.tournament_id = t.id
           )"""
    ).fetchall()
    return [r["id"] if isinstance(r, dict) or hasattr(r, "keys") else r[0]
            for r in rows]


def check_no_decision_debt(store, tool_name: str) -> str | None:
    """Decision-debt gate: undecided closed tournaments refuse all
    mutating tools. ``record_meta_decision`` and ``rollback`` are in
    ALWAYS_EXEMPT upstream — they never reach this gate. The two
    evidence miners are exempt here: a decision needs ≥1
    evidence_ref and only they mint it — gating them deadlocks the
    debt it enforces (F-17)."""
    if tool_name in DEBT_DISCHARGE_TOOLS:
        return None
    undecided = undecided_tournaments(store)
    if not undecided:
        return None
    return (
        f"{len(undecided)} closed tournament(s) lack a meta_decision "
        f"({', '.join(undecided[:5])}) — adjudicate with "
        "record_meta_decision (a 'hold' verdict clears legitimately "
        "when genuinely undecidable) or rollback."
    )


# Improvement epoch — configured from [enforcement]
# improvement_epoch_seconds at server creation; the digest reads the
# module value so session/status surfaces share one setting.
EPOCH_SECONDS = 86400.0


def configure_epoch(seconds: float | int | None) -> None:
    global EPOCH_SECONDS
    EPOCH_SECONDS = float(seconds or 86400.0)


def improvement_due(store, epoch_seconds: float | None = None) -> bool:
    """True when the most recent improvement activity is older than
    the epoch — activity-based and unconditional: proposals,
    decisions, and tournament events all count."""
    if epoch_seconds is None:
        epoch_seconds = EPOCH_SECONDS
    row = store.conn.execute(
        """SELECT MAX(ts) AS ts FROM (
               SELECT created_at AS ts FROM meta_decisions
               UNION ALL SELECT created_at FROM meta_change_proposals
               UNION ALL SELECT created_at FROM tournaments
               UNION ALL SELECT closed_at FROM tournaments
                   WHERE closed_at IS NOT NULL
           ) WHERE ts IS NOT NULL"""
    ).fetchone()
    ts = None if row is None else (
        row["ts"] if hasattr(row, "keys") else row[0]
    )
    if not ts:
        # No improvement activity ever recorded — the idle loop is
        # itself the signal; the epoch is unconditionally elapsed.
        return True
    last = datetime.fromisoformat(ts)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (
        datetime.now(timezone.utc) - last
    ).total_seconds() > float(epoch_seconds)


class RecurrenceTracker:
    """Consultation-duty watermark + digest cache (per server process)."""

    def __init__(self) -> None:
        self.ttl_seconds = 0.0
        self.status_read_at: float | None = None
        self.digest: dict | None = None
        self.digest_computed_at: float | None = None

    def configure(self, freshness_seconds: float | int | None) -> None:
        self.ttl_seconds = float(freshness_seconds or 0.0)

    @property
    def enabled(self) -> bool:
        return self.ttl_seconds > 0

    def reset(self) -> None:
        self.status_read_at = None
        self.digest = None
        self.digest_computed_at = None

    def mark_status_read(self, digest: dict) -> None:
        now = time.time()
        self.status_read_at = now
        self.digest = digest
        self.digest_computed_at = now

    def is_fresh(self) -> bool:
        return (
            self.status_read_at is not None
            and (time.time() - self.status_read_at) <= self.ttl_seconds
        )

    def check_fresh(self) -> str | None:
        if not self.enabled or self.is_fresh():
            return None
        return (
            f"Stale session — read {STATUS_RESOURCE} (or "
            f"{STATUS_SESSION}) before mutating. Consultation duty: "
            f"status must be re-read every {self.ttl_seconds:.0f}s."
        )

    def next_hint(self) -> dict | None:
        if not self.enabled or not self.is_fresh() or not self.digest:
            return None
        recs = self.digest.get("recommended_next") or []
        top = recs[0] if recs else {}
        return {
            "action": top.get("action"),
            "tool": top.get("tool"),
            "reason": top.get("reason"),
            "blockers": len(self.digest.get("blockers") or []),
        }

    def inject(self, result: CallToolResult) -> CallToolResult:
        """Attach ``next`` to a successful result — advisory, never
        breaks the result on failure."""
        hint = self.next_hint()
        if hint is None:
            return result
        is_err = getattr(
            result, "is_error", getattr(result, "isError", False)
        )
        if is_err:
            return result
        try:
            if result.content and hasattr(result.content[0], "text"):
                payload = json.loads(result.content[0].text)
                if isinstance(payload, dict):
                    payload["next"] = hint
                    result.content[0].text = json.dumps(payload)
            sc = getattr(result, "structured_content", None)
            if sc is None:
                sc = getattr(result, "structuredContent", None)
            if isinstance(sc, dict):
                sc["next"] = hint
        except Exception:
            pass
        return result


TRACKER = RecurrenceTracker()


def install(
    mcp,
    store,
    tracker: RecurrenceTracker,
    *,
    extra_exempt: set[str] | frozenset[str] = frozenset(),
    extra_gates: tuple[Callable[[str], str | None], ...] = (),
) -> None:
    """Wrap ``mcp.call_tool`` with the freshness + governance-debt
    gates and post-injection."""
    exempt = set(ALWAYS_EXEMPT) | set(extra_exempt)

    def _is_exempt(name: str) -> bool:
        return name in exempt or name.startswith(READ_PREFIXES)

    def _gates(tool_name: str) -> str | None:
        err = check_no_open_violations(store, tool_name)
        if err:
            return err
        if err := check_no_decision_debt(store, tool_name):
            return err
        for g in extra_gates:
            if err := g(tool_name):
                return err
        return None

    original = mcp.call_tool

    async def _guarded_call_tool(name, arguments, context=None):
        if not _is_exempt(name):
            if err := tracker.check_fresh():
                return _refusal(err)
            if err := _gates(name):
                return _refusal(err)
        result = await original(name, arguments, context)
        return tracker.inject(result)

    mcp.call_tool = _guarded_call_tool


def violation_ack(
    store, check_name: str, object_ref: str,
    disposition: str, decided_by: str,
) -> dict:
    """Record an acknowledgment and report the residual open set —
    insert-only governance event, never deletes the finding."""
    open_before = {
        (v["check"], v["object_ref"]) for v in open_violations(store)
    }
    ack_id = f"vack-{uuid.uuid4().hex[:8]}"
    store.record_violation_ack(
        ack_id=ack_id,
        check_name=check_name,
        object_ref=object_ref,
        disposition=disposition,
        decided_by=decided_by,
        created_at=_utc_now_iso(),
    )
    remaining = open_violations(store)
    return {
        "ack_id": ack_id,
        "status": "acknowledged",
        "matched_open_violation": (check_name, object_ref) in open_before,
        "open_violations": len(remaining),
    }
