"""Integrity-layer tests for anamnesis — the memory-store audit.

Covers: each check's violation and clean paths, audit-log
write/prune/config behaviour, the check_invariants tool, the
/health/deep route, the /integrity GUI view, and non-mutation.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from ml_anamnesis_mcp.integrity.checks import run_and_log, run_checks
from ml_anamnesis_mcp.integrity.log import list_check_logs, write_check_log
from ml_anamnesis_mcp.state.models import Claim, ClaimEdge, ClaimType
from .conftest import call_tool, make_claim


def _check(payload, name):
    return next(c for c in payload["checks"] if c["name"] == name)


def _claim(cid, confidence=0.8, **kw):
    return Claim(
        id=cid,
        content=kw.pop("content", f"content {cid}"),
        type=ClaimType.empirical,
        confidence=confidence,
        content_hash=f"hash-{cid}",
        **kw,
    )


# --- Individual checks ---


def test_clean_store_is_ok(mem_store):
    payload = run_checks(mem_store)
    assert payload["status"] == "ok"
    assert len(payload["checks"]) == 4


async def test_unsupported_high_confidence(mcp_server, mem_store):
    # Supported claim → clean
    await make_claim(mcp_server, confidence=0.9)
    assert _check(
        run_checks(mem_store), "unsupported_high_confidence"
    )["ok"]

    # Direct insert bypasses enforcement — the audit catches it
    mem_store.create_claim(_claim("claim-bare", confidence=0.9))
    c = _check(run_checks(mem_store), "unsupported_high_confidence")
    assert not c["ok"]
    assert c["violations"][0]["claim_id"] == "claim-bare"


def test_prior_confidence_max_from_config(mem_store):
    """[integrity] prior_confidence_max governs the audit — the same
    knob the write-time gate reads, so they can't diverge."""
    mem_store.create_claim(_claim("claim-mid", confidence=0.4))
    # Default ceiling 0.3 → flagged
    c = _check(run_checks(mem_store), "unsupported_high_confidence")
    assert not c["ok"]
    # Configured ceiling 0.5 → under the bar
    c = _check(
        run_checks(mem_store, prior_confidence_max=0.5),
        "unsupported_high_confidence",
    )
    assert c["ok"]
    # And through the config-table path the monitor/route use
    payload = run_and_log(
        mem_store,
        config={"prior_confidence_max": 0.5, "log_max_files": 0},
        trigger="tool",
    )
    assert _check(payload, "unsupported_high_confidence")["ok"]


async def test_dangling_claim_refs(mem_store):
    mem_store.create_claim(_claim("claim-a", confidence=0.2))
    mem_store.create_edge(ClaimEdge(
        id=str(uuid.uuid4()),
        from_claim="claim-a",
        to_ref="claim-ghost",
        ref_type="claim",
        relation="supports",
    ))
    c = _check(run_checks(mem_store), "dangling_claim_refs")
    assert not c["ok"]
    assert c["violations"][0]["to_ref"] == "claim-ghost"


async def test_broken_supersession(mem_store):
    # FKs block this at write — the audit exists for corruption that
    # bypassed enforcement (hand-edits, foreign_keys=off imports).
    mem_store.conn.execute("PRAGMA foreign_keys = OFF")
    mem_store.create_claim(_claim("claim-x", confidence=0.2))
    mem_store.create_claim(
        _claim("claim-y", confidence=0.2, supersedes_id="claim-missing")
    )
    mem_store.conn.execute("PRAGMA foreign_keys = ON")
    c = _check(run_checks(mem_store), "broken_supersession")
    assert not c["ok"]
    assert c["violations"][0]["problem"] == "target missing"


async def test_supersession_cycle(mem_store):
    mem_store.conn.execute("PRAGMA foreign_keys = OFF")
    mem_store.create_claim(
        _claim("claim-p", confidence=0.2, supersedes_id="claim-q")
    )
    mem_store.create_claim(
        _claim("claim-q", confidence=0.2, supersedes_id="claim-p")
    )
    mem_store.conn.execute("PRAGMA foreign_keys = ON")
    c = _check(run_checks(mem_store), "broken_supersession")
    assert not c["ok"]
    assert any(v["problem"] == "cycle in chain" for v in c["violations"])


async def test_non_mutation(mem_store):
    mem_store.create_claim(_claim("claim-bare", confidence=0.9))
    before = [dict(r) for r in mem_store._fetchall("SELECT * FROM claims")]
    run_checks(mem_store)
    after = [dict(r) for r in mem_store._fetchall("SELECT * FROM claims")]
    assert before == after


# --- Audit log ---


def test_log_written_and_pruned(mem_store):
    payload = run_and_log(mem_store, config={"log_max_files": 2})
    log_dir = Path(mem_store.db_path).parent / "logs"
    assert Path(payload["log_file"]).exists()
    assert payload["log_file"].endswith(".jsonl")  # one file per UTC day
    # Seed fake day files — pruning bounds files, not runs
    for i in range(3):
        (log_dir / f"check-2026-09-1{i}.jsonl").write_text(
            json.dumps({"status": "ok", "checks": []}) + "\n"
        )
    write_check_log(log_dir, {"status": "ok", "checks": []}, 2)
    assert len(list(log_dir.glob("check-*.jsonl"))) == 2


def test_log_disabled(mem_store):
    payload = run_and_log(mem_store, config={"log_max_files": 0})
    assert "log_file" not in payload


async def test_integrity_monitor(mem_store):
    """Audit on by default: startup record lands immediately; periodic
    sweeps append interval records."""
    import asyncio
    from ml_anamnesis_mcp.integrity.checks import start_integrity_monitor

    log_dir = Path(mem_store.db_path).parent / "logs"

    task = start_integrity_monitor(
        mem_store, config={"check_interval_seconds": 0}
    )
    assert task is None
    lines = list(log_dir.glob("check-*.jsonl"))[0].read_text().splitlines()
    assert json.loads(lines[0])["trigger"] == "startup"

    task = start_integrity_monitor(
        mem_store, config={"check_interval_seconds": 0.05}
    )
    assert task is not None
    await asyncio.sleep(0.15)
    task.cancel()
    lines = list(log_dir.glob("check-*.jsonl"))[0].read_text().splitlines()
    assert len(lines) >= 3
    assert json.loads(lines[-1])["trigger"] == "interval"


# --- Tool + route + GUI ---


async def test_check_invariants_tool(mem_store):
    from ml_anamnesis_mcp.server import create_server

    mcp = create_server(mem_store, integrity_config={"log_max_files": 5})
    result = await call_tool(mcp, "check_invariants", {})
    assert result["server"] == "ml-anamnesis-mcp"
    assert result["status"] == "ok"
    assert Path(result["log_file"]).exists()


async def test_health_deep_route(mem_store):
    from ml_anamnesis_mcp.server import create_server

    mcp = create_server(mem_store, integrity_config={"log_max_files": 5})
    app = mcp.streamable_http_app()
    with TestClient(app) as client:
        r = client.get("/health/deep")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert len(body["checks"]) == 4


def test_integrity_gui(mem_store):
    from ml_anamnesis_mcp.observability.server import (
        create_observability_app,
    )

    app = create_observability_app(mem_store)
    client = TestClient(app)

    r = client.get("/integrity")
    assert r.status_code == 200
    assert "No integrity checks" in r.text

    mem_store.create_claim(_claim("claim-bare", confidence=0.9))
    run_and_log(mem_store, config={})
    r = client.get("/integrity")
    assert r.status_code == 200
    assert "unsupported_high_confidence" in r.text

    run = list_check_logs(Path(mem_store.db_path).parent / "logs")[0]
    r = client.get(f"/integrity/run/{run['file']}/{run['index']}")
    assert r.status_code == 200
    assert "claim-bare" in r.text
    r = client.get(f"/integrity/log/{run['file']}")
    assert r.status_code == 200
    assert "unsupported_high_confidence" in r.text


def test_integrity_help(mem_store):
    """/integrity/help explains every emitted check name — including
    why upstream_connectivity is permanently skipped — and is linked
    from the main page."""
    from ml_anamnesis_mcp.observability.server import (
        create_observability_app,
    )

    app = create_observability_app(mem_store)
    client = TestClient(app)

    r = client.get("/integrity")
    assert r.status_code == 200
    assert "/integrity/help" in r.text

    r = client.get("/integrity/help")
    assert r.status_code == 200
    for name in (
        "upstream_connectivity", "unsupported_high_confidence",
        "dangling_claim_refs", "broken_supersession",
    ):
        assert name in r.text
    # The skip is explained, not just listed.
    assert "endpoint" in r.text
