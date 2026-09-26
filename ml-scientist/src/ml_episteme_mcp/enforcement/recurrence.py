"""Recurrent self-improvement protocol — plan-20260926-0438Z.

Consultation duty as a rejection point: mutating tools refuse until
the server's status digest has been read within a freshness TTL
(``[enforcement] status_freshness_seconds``; ``0`` disables). The
digest read also populates a cache whose top recommendation is
injected into every successful tool result as ``next`` — guidance
rides responses for free instead of requiring a second round-trip.

Governance debt gates writes on top of the freshness gate: open,
unacknowledged integrity violations refuse all mutating tools except
``acknowledge_violation`` and the remediation tools that could clear
the flagged record (per-check map below — a gate must never strand
its own remedy).

Known v1 limitation (documented in the plan): the watermark is
server-global, not keyed to the calling session — one client's
consult satisfies another's duty, and aggregator polls of
``protocol://status`` (e.g. agora's upstream fetches) stamp it as
well. Accepted under the single-writer lab assumption; session-keyed
watermarks are v2.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from mcp.types import CallToolResult, TextContent

from ..integrity.checks import log_dir_for
from ..integrity.log import list_check_logs, read_run

STATUS_RESOURCE = "protocol://status"
STATUS_SESSION = "protocol://session"

# Read-shaped tool-name prefixes — the consultation surface is never
# gated. Everything else mutates and answers to the gates.
READ_PREFIXES = (
    "get_", "list_", "check_", "verify_", "assess_", "describe_",
    "wait_",
)

# Gate-free regardless of kind: the audit tool, the ack tool, and the
# record-level remediation writes a violation gate exists to force.
ALWAYS_EXEMPT = {
    "check_invariants",
    "acknowledge_violation",
    "correct_trial_status",
}

# Integrity-check name → tools whose successful call can clear the
# flagged record. The violation gate exempts the union of remedies
# for the currently-open checks; checks with no in-store remedy
# (empty set) are resolved by acknowledge_violation only.
REMEDY_TOOLS = {
    "orphaned_running_trials": {
        "correct_trial_status", "cancel_trial", "mark_retryable",
    },
    "stalled_running_trials": {
        "correct_trial_status", "cancel_trial", "mark_retryable",
    },
    "completed_without_observation": {
        "record_observation", "correct_trial_status",
    },
    "unsealed_execution": {"correct_trial_status"},
    "strace_divergence": {"correct_trial_status"},
    # No in-store remedy — a digest not taken cannot be
    # reconstructed; acknowledge_violation is the resolution path.
    "input_data_undigested": set(),
    "mislabeled_outcome": {"correct_trial_status"},
    "budget_exceeded": {"close_programme"},
    "stuck_hypotheses": {
        "design_experiment", "conclude_hypothesis", "close_programme",
    },
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
    """Stable object reference for a violation entry.

    Violation items are entity ids (strings) or small dicts like
    ``{"trial_id": ..., "paths": [...]}`` — canonicalize to the id
    value, or a deterministic serialization otherwise.
    """
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for k in sorted(item):
            if k == "id" or k.endswith("_id"):
                return str(item[k])
        return json.dumps(item, sort_keys=True)
    return str(item)


def open_violations(store) -> list[dict]:
    """Unacknowledged violations from the latest logged check run.

    Computed from the audit trail (``<db_dir>/logs/check-*.jsonl``)
    minus ``violation_acks`` rows — the log is append-only, so an ack
    is the only way a recorded finding leaves the open set.
    """
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
    """Consultation-duty watermark + digest cache (per server process).

    ``mark_status_read`` is called by ``status_digest`` — every
    consumer of the digest (status resource, session resource,
    status_report prompt) stamps the watermark and refreshes the
    cache with one code path.
    """

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
        """Freshness gate — the consultation-duty refusal."""
        if not self.enabled or self.is_fresh():
            return None
        return (
            f"Stale session — read {STATUS_RESOURCE} (or "
            f"{STATUS_SESSION}) before mutating. Consultation duty: "
            f"status must be re-read every {self.ttl_seconds:.0f}s."
        )

    def next_hint(self) -> dict | None:
        """Compact next-action for post-injection — top recommendation
        + blocker count from the cached digest, when the cache is
        fresh."""
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


# Module-level tracker — status_digest stamps it without signature
# changes; create_server configures it once per instance.
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
    gates and post-injection — the same monkey-patch seam the
    ``log_tool_args`` wrapper already uses, applied to every tool
    call including refused ones.
    """
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
