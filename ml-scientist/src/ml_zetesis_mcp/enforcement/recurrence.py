"""Recurrent self-improvement protocol — plan-20260926-0438Z.

Consultation duty as a rejection point: mutating tools refuse until
the server's status digest has been read within a freshness TTL
(``[enforcement] status_freshness_seconds``; ``0`` disables). The
digest read also populates a cache whose top recommendation is
injected into every successful tool result as ``next``.

Governance debt gates writes on top of the freshness gate: open,
unacknowledged integrity violations refuse all mutating tools except
``acknowledge_violation`` and the remediation tools that could clear
the flagged record (per-check map below — a gate must never strand
its own remedy).

Known v1 limitation (documented in the plan): the watermark is
server-global, not keyed to the calling session — one client's
consult satisfies another's duty, and aggregator polls of
``search://status`` (e.g. agora's upstream fetches) stamp it as
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

STATUS_RESOURCE = "search://status"
STATUS_SESSION = "search://session"

READ_PREFIXES = (
    "get_", "list_", "check_", "verify_", "assess_", "describe_",
    "wait_",
)

ALWAYS_EXEMPT = {
    "check_invariants",
    "acknowledge_violation",
}

# Integrity-check name → tools whose successful call can clear the
# flagged record. Checks with no in-store remedy (empty set) are
# resolved by acknowledge_violation only.
REMEDY_TOOLS = {
    "stale_open_investigations": {
        "conclude_investigation", "abandon_investigation",
    },
    "asserted_without_claim": set(),
    "concluded_unminted": set(),
    "minted_claims_resolve": set(),
    "closed_campaigns_scored": set(),
    "campaign_results_have_campaign": set(),
    "campaign_spawns_have_campaign": set(),
    "results_spawn_scoped": set(),
    "single_roster_champion": {"refresh_roster"},
    "spawn_budget_violations": set(),
    "upstream_connectivity": set(),
}


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
