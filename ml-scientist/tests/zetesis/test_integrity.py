"""Integrity-layer tests for zetesis — the search-store audit.

Covers: each check's violation and clean paths (including the async
cross-server claim-resolution check and its skipped-when-absent
semantics), audit-log behaviour, the check_invariants tool, the
/health/deep route, the /integrity GUI view, and non-mutation.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from ml_zetesis_mcp.integrity.checks import run_and_log, run_checks
from ml_zetesis_mcp.integrity.log import list_check_logs
from ml_zetesis_mcp.state.models import (
    EvidenceRef,
    EvidenceSource,
    Finding,
    Investigation,
    InvestigationVerdict,
)
from .conftest import FakeClaimsAdaptor, call_tool


def _check(payload, name):
    return next(c for c in payload["checks"] if c["name"] == name)


def _inv(iid="inv-t1", created_at=None):
    return Investigation(
        id=iid,
        question="does the check work?",
        scope={"programme_ids": ["prog-aaaa1111"]},
        created_at=created_at or datetime.now(timezone.utc).isoformat(),
    )


def _finding(iid, fid="find-t1", **kw):
    return Finding(
        id=fid,
        investigation_id=iid,
        content="test finding",
        confidence=0.5,
        **kw,
    )


# --- Individual checks ---


async def test_clean_store_is_ok(search_store, adaptors):
    payload = await run_checks(search_store, claims=adaptors.claims)
    assert payload["status"] == "ok"
    assert len(payload["checks"]) == 11


async def test_stale_open_investigation(search_store):
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    search_store.create_investigation(_inv(created_at=old))
    c = _check(
        await run_checks(search_store), "stale_open_investigations"
    )
    assert not c["ok"]
    assert c["violations"][0]["investigation_id"] == "inv-t1"

    # A recent evidence pull refreshes staleness
    search_store.create_evidence_ref(EvidenceRef(
        id="eref-t1", investigation_id="inv-t1",
        source=EvidenceSource.loop0, tool="list_trials", args={},
    ))
    c = _check(
        await run_checks(search_store), "stale_open_investigations"
    )
    assert c["ok"]


async def test_asserted_without_claim(search_store):
    from ml_zetesis_mcp.state.models import FindingStatus

    search_store.create_investigation(_inv())
    search_store.create_finding(_finding("inv-t1"))
    search_store._execute(
        "UPDATE findings SET status = 'asserted' WHERE id = 'find-t1'"
    )
    c = _check(
        await run_checks(search_store), "asserted_without_claim"
    )
    assert not c["ok"]
    assert c["violations"][0]["finding_id"] == "find-t1"


async def test_concluded_unminted(search_store):
    search_store.create_investigation(_inv())
    search_store.create_finding(_finding("inv-t1"))
    search_store.conclude_investigation(
        "inv-t1", InvestigationVerdict.findings, "summary", None
    )
    c = _check(await run_checks(search_store), "concluded_unminted")
    assert not c["ok"]
    assert c["violations"][0]["finding_id"] == "find-t1"

    # null_result verdict → clean
    search_store.create_investigation(_inv("inv-t2"))
    search_store.conclude_investigation(
        "inv-t2", InvestigationVerdict.null_result, "nothing", None
    )
    c = _check(await run_checks(search_store), "concluded_unminted")
    assert len(c["violations"]) == 1  # only inv-t1's finding


async def test_minted_claims_resolve(search_store):
    search_store.create_investigation(_inv())
    search_store.create_finding(
        _finding("inv-t1", claim_id="claim-real")
    )
    search_store.create_finding(
        _finding("inv-t1", "find-t2", claim_id="claim-ghost")
    )

    claims = FakeClaimsAdaptor(payloads={
        "get_claim": json.dumps({"claim_id": "claim-real"}),
    })
    # FakeClaimsAdaptor returns the same payload for every get_claim —
    # make resolution depend on the arg by overriding pull
    async def pull(tool, args):
        cid = args["claim_id"]
        if cid == "claim-real":
            return json.dumps({"claim_id": cid})
        return json.dumps({"error": f"Claim not found: {cid}"})
    claims.pull = pull

    c = _check(
        await run_checks(search_store, claims=claims),
        "minted_claims_resolve",
    )
    assert not c["ok"]
    assert len(c["violations"]) == 1
    assert c["violations"][0]["claim_id"] == "claim-ghost"


async def test_minted_claims_skipped_without_channel(search_store):
    search_store.create_investigation(_inv())
    search_store.create_finding(
        _finding("inv-t1", claim_id="claim-any")
    )
    c = _check(
        await run_checks(search_store, claims=None),
        "minted_claims_resolve",
    )
    assert c["ok"]  # skipped is not a violation
    assert c["detail"].startswith("skipped")


async def test_non_mutation(search_store):
    search_store.create_investigation(_inv())
    search_store.create_finding(_finding("inv-t1"))
    before = [dict(r) for r in search_store._fetchall("SELECT * FROM findings")]
    await run_checks(search_store)
    after = [dict(r) for r in search_store._fetchall("SELECT * FROM findings")]
    assert before == after


# --- Audit log ---


async def test_log_written_and_disabled(search_store):
    payload = await run_and_log(search_store, config={})
    assert Path(payload["log_file"]).exists()

    payload = await run_and_log(
        search_store, config={"log_max_files": 0}
    )
    assert "log_file" not in payload


async def test_integrity_monitor(search_store, adaptors):
    """Audit on by default: startup record lands immediately; periodic
    sweeps append interval records. claims is resolved lazily per
    sweep."""
    import asyncio
    from ml_zetesis_mcp.integrity.checks import start_integrity_monitor

    log_dir = Path(search_store.db_path).parent / "logs"

    task = await start_integrity_monitor(
        search_store,
        claims=lambda: adaptors.claims,
        config={"check_interval_seconds": 0},
    )
    assert task is None
    lines = list(log_dir.glob("check-*.jsonl"))[0].read_text().splitlines()
    assert json.loads(lines[0])["trigger"] == "startup"

    task = await start_integrity_monitor(
        search_store,
        claims=lambda: adaptors.claims,
        config={"check_interval_seconds": 0.05},
    )
    assert task is not None
    await asyncio.sleep(0.15)
    task.cancel()
    lines = list(log_dir.glob("check-*.jsonl"))[0].read_text().splitlines()
    assert len(lines) >= 3
    assert json.loads(lines[-1])["trigger"] == "interval"


# --- Tool + route + GUI ---


async def test_check_invariants_tool(search_store, adaptors):
    from ml_zetesis_mcp.server import create_server

    mcp = create_server(
        search_store, adaptors=adaptors,
        integrity_config={"log_max_files": 5},
    )
    result = await call_tool(mcp, "check_invariants", {})
    assert result["server"] == "ml-zetesis-mcp"
    assert result["status"] == "ok"
    assert Path(result["log_file"]).exists()


async def test_health_deep_route(search_store, adaptors):
    from ml_zetesis_mcp.server import create_server

    mcp = create_server(
        search_store, adaptors=adaptors,
        integrity_config={"log_max_files": 5},
    )
    app = mcp.streamable_http_app()
    with TestClient(app) as client:
        r = client.get("/health/deep")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert len(body["checks"]) == 11


def test_integrity_gui(search_store):
    import asyncio

    from ml_zetesis_mcp.observability.server import (
        create_observability_app,
    )

    app = create_observability_app(search_store)
    client = TestClient(app)

    r = client.get("/integrity")
    assert r.status_code == 200
    assert "No integrity checks" in r.text

    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    search_store.create_investigation(_inv(created_at=old))
    asyncio.run(run_and_log(search_store, config={}))
    r = client.get("/integrity")
    assert r.status_code == 200
    assert "stale_open_investigations" in r.text

    run = list_check_logs(Path(search_store.db_path).parent / "logs")[0]
    r = client.get(f"/integrity/run/{run['file']}/{run['index']}")
    assert r.status_code == 200
    assert "inv-t1" in r.text
    r = client.get(f"/integrity/log/{run['file']}")
    assert r.status_code == 200
    assert "stale_open_investigations" in r.text


def test_integrity_help(search_store):
    """/integrity/help explains every emitted check name and is linked
    from the main page — static reference, not a log route."""
    from ml_zetesis_mcp.observability.server import (
        create_observability_app,
    )

    app = create_observability_app(search_store)
    client = TestClient(app)

    r = client.get("/integrity")
    assert r.status_code == 200
    assert '/integrity/help' in r.text

    r = client.get("/integrity/help")
    assert r.status_code == 200
    for name in (
        "upstream_connectivity", "stale_open_investigations",
        "asserted_without_claim", "concluded_unminted",
        "closed_campaigns_scored", "campaign_results_have_campaign",
        "single_roster_champion", "minted_claims_resolve",
    ):
        assert name in r.text
