"""Invariant checks for memory.db — the anamnesis integrity audit.

Write-time enforcement (server-computed confidence, supersession
targets, closed vocabularies) prevents violations; these checks
detect what
enforcement cannot cover: hand-edits, races, and future bugs. The
audit counterpart of the evidence rule — asserted, then audited.

Report-only: no check mutates state.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import json

from ..state.models import EVIDENCE_RELATIONS, INTERNAL_REF_PREFIXES

from .. import _grounded_constants as _gc

DEFAULT_LOG_MAX_FILES = _gc.LOG_MAX_FILES.value
DEFAULT_CHECK_INTERVAL_SECONDS = _gc.CHECK_INTERVAL_SECONDS.value


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


def _check_unverifiable_confidence(store) -> dict:
    """Every numeric confidence must recompute through its stored
    derivation (numeric provenance invariant,
    plan-20260930-0240Z). NULL confidence is honest — absence of a
    computation is absence of a number. A live claim violates when:

    - confidence is non-NULL but confidence_computation is NULL
      (no derivation was recorded — legacy rows flag ``legacy``),
    - the stored computation fails to recompute or recomputes to a
      different value (the record does not reproduce the number), or
    - a computed confidence lacks any evidence-bearing edge (a
      posterior floating free of the graph)."""
    ev = tuple(sorted(EVIDENCE_RELATIONS))
    placeholders = ",".join("?" for _ in ev)
    rows = store._fetchall(
        f"""SELECT c.id, c.confidence, c.confidence_basis,
                  c.confidence_computation FROM claims c
            WHERE c.confidence IS NOT NULL
              AND NOT {store._SUPERSEDED_CLAUSE}
              AND (c.valid_until IS NULL OR c.valid_until >= ?)""",
        (_utc_now(),),
    )
    violations = []
    for r in rows:
        legacy = r["confidence_basis"] is None
        comp_raw = r["confidence_computation"]
        if comp_raw is None:
            violations.append({
                "claim_id": r["id"],
                "confidence": r["confidence"],
                "basis": r["confidence_basis"],
                "legacy": legacy,
                "problem": "numeric confidence without a stored "
                           "derivation",
            })
            continue
        try:
            recomputed = _gc.compute_confidence(json.loads(comp_raw))
        except Exception as e:
            violations.append({
                "claim_id": r["id"],
                "confidence": r["confidence"],
                "basis": r["confidence_basis"],
                "legacy": legacy,
                "problem": f"derivation does not recompute: {e}",
            })
            continue
        if abs(recomputed - r["confidence"]) > 1e-9:
            violations.append({
                "claim_id": r["id"],
                "confidence": r["confidence"],
                "basis": r["confidence_basis"],
                "legacy": legacy,
                "problem": f"stored confidence does not recompute "
                           f"(derivation yields {recomputed})",
            })
            continue
        has_evidence = store._fetchone(
            f"""SELECT 1 FROM claim_edges
                WHERE from_claim = ? AND relation IN ({placeholders})
                LIMIT 1""",
            (r["id"], *ev),
        )
        if has_evidence is None:
            violations.append({
                "claim_id": r["id"],
                "confidence": r["confidence"],
                "basis": r["confidence_basis"],
                "legacy": legacy,
                "problem": "computed confidence without an "
                           "evidence-bearing edge",
            })
    return _res(
        "unverifiable_confidence", violations,
        f"{len(violations)} live claim(s) with confidence that does "
        "not verify against a stored derivation",
    )


def _check_dangling_claim_refs(store) -> dict:
    """claim_edges with ref_type='claim' pointing at nonexistent claims —
    refs of that type are verified at write time, so a dangling one is
    corruption."""
    rows = store._fetchall(
        """SELECT e.id, e.from_claim, e.to_ref FROM claim_edges e
           LEFT JOIN claims c ON c.id = e.to_ref
           WHERE e.ref_type = 'claim' AND c.id IS NULL"""
    )
    violations = [
        {"edge_id": r["id"], "from_claim": r["from_claim"],
         "to_ref": r["to_ref"]}
        for r in rows
    ]
    return _res(
        "dangling_claim_refs", violations,
        f"{len(violations)} edge(s) reference nonexistent claims",
    )


def _check_broken_supersession(store) -> dict:
    """supersedes_id pointing at a nonexistent claim, and supersedes
    chains that cycle — a chain must terminate to be a lineage."""
    violations = []
    rows = store._fetchall(
        """SELECT c.id, c.supersedes_id FROM claims c
           LEFT JOIN claims p ON p.id = c.supersedes_id
           WHERE c.supersedes_id IS NOT NULL AND p.id IS NULL"""
    )
    for r in rows:
        violations.append({
            "claim_id": r["id"],
            "supersedes_id": r["supersedes_id"],
            "problem": "target missing",
        })

    # Cycle detection over the supersedes graph
    edges = dict(
        (r["id"], r["supersedes_id"])
        for r in store._fetchall(
            "SELECT id, supersedes_id FROM claims "
            "WHERE supersedes_id IS NOT NULL"
        )
    )
    for start in edges:
        seen = set()
        node = start
        while node in edges:
            if node in seen:
                violations.append({
                    "claim_id": start, "problem": "cycle in chain",
                })
                break
            seen.add(node)
            node = edges[node]
    return _res(
        "broken_supersession", violations,
        f"{len(violations)} supersession defect(s) — missing targets "
        "or cycles",
    )


def _check_misfiled_external_refs(store) -> dict:
    """Edges typed 'external' whose to_ref carries a known internal
    prefix — 'external' means unresolvable elsewhere, so an internal
    id under it is a mislabel that pollutes every query against the
    bucket. The write-time guard and the init-time retype keep this
    at zero; a nonzero count means the vocabulary drifted again."""
    rows = store._fetchall(
        "SELECT id, from_claim, to_ref FROM claim_edges "
        "WHERE ref_type = 'external'"
    )
    violations = [
        {"edge_id": r["id"], "from_claim": r["from_claim"],
         "to_ref": r["to_ref"],
         "problem": "internal id filed as external"}
        for r in rows
        if any(r["to_ref"].startswith(p) for p in INTERNAL_REF_PREFIXES)
    ]
    return _res(
        "misfiled_external_refs", violations,
        f"{len(violations)} 'external' edge(s) point at internal ids",
    )


def run_checks(store) -> dict:
    """Run the full anamnesis invariant suite; return the payload."""
    started = time.monotonic()
    checks = [
        # Anamnesis is the semantic-memory endpoint — it consumes no
        # upstream channels, so connectivity is recorded as an explicit
        # skip rather than omitted from the payload.
        _skipped(
            "upstream_connectivity",
            "semantic-memory endpoint — no upstream channels",
        ),
        _check_unverifiable_confidence(store),
        _check_dangling_claim_refs(store),
        _check_broken_supersession(store),
        _check_misfiled_external_refs(store),
    ]
    return {
        "server": "ml-anamnesis-mcp",
        "status": (
            "ok" if all(c["ok"] for c in checks) else "violations"
        ),
        "checked_at": _utc_now(),
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
        "checks": checks,
    }


def log_dir_for(store) -> Path:
    return Path(store.db_path).parent / "logs"


def run_and_log(
    store, *, config: dict | None = None, trigger: str = "tool"
) -> dict:
    """Run the suite, write the audit log, return the payload.

    `trigger` records what invoked the run ("startup", "interval",
    "tool", "route") so the audit trail is self-describing.
    """
    from .log import write_check_log

    cfg = config or {}
    payload = run_checks(store)
    payload["trigger"] = trigger
    log_path = write_check_log(
        log_dir_for(store),
        payload,
        cfg.get("log_max_files", DEFAULT_LOG_MAX_FILES),
    )
    if log_path is not None:
        payload["log_file"] = str(log_path)
    return payload


def start_integrity_monitor(store, *, config=None):
    """Startup + periodic invariant sweeps — the audit trail is on by
    default, not on request. Runs one check immediately
    (trigger="startup"), then sweeps every `check_interval_seconds`
    (default 300; 0 disables the periodic sweep — the startup record
    still lands). Call inside a running event loop; returns the
    periodic task or None."""
    import asyncio

    cfg = config or {}
    run_and_log(store, config=cfg, trigger="startup")
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
                    run_and_log, store, config=cfg, trigger="interval"
                )
            except Exception:
                pass  # the monitor must never take down the server

    return asyncio.create_task(_loop())
