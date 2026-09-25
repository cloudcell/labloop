"""Integrity-layer tests — the invariant suite over improver.db."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ml_arete_mcp.integrity.checks import (
    DEFAULT_LOG_MAX_FILES,
    run_checks,
    run_and_log,
)
from ml_arete_mcp.state.models import (
    DecisionVerdict,
    EvidenceContext,
    EvidenceRef,
    EvidenceSource,
    ImproverVersion,
    MetaChangeProposal,
    MetaContract,
    MetaDecision,
    PolicyStatus,
    PolicyVersion,
    ProposalStatus,
    Tournament,
)


def _imp(i, parent=None, champion=False, proposal_id=None):
    return ImproverVersion(
        id=i, parent_id=parent, proposal_id=proposal_id,
        code_artifact_digest="d", model_ref="m",
        capability_profile={}, is_champion=champion,
    )


def _proposal(pid, proposer, status=ProposalStatus.admitted):
    return MetaChangeProposal(
        id=pid, proposer_improver_id=proposer,
        spec_delta={}, class_map={"x": "m"},
        expected_benefit="b", falsification="f",
        rollback_plan="r", status=status,
    )


async def test_clean_store_is_ok(improver_store):
    payload = await run_checks(improver_store)
    assert payload["status"] == "ok"
    names = {c["name"] for c in payload["checks"]}
    assert "single_champion" in names
    assert "lineage_integrity" in names
    assert "minted_claims_resolve" in names


async def test_minted_claims_skipped_without_channel(improver_store):
    payload = await run_checks(improver_store, claims=None)
    check = next(
        c for c in payload["checks"]
        if c["name"] == "minted_claims_resolve"
    )
    assert check["ok"] is True
    assert check["detail"].startswith("skipped")


async def test_multiple_champions_flagged(improver_store):
    improver_store.create_improver(_imp("imp-a", champion=True))
    improver_store.create_improver(_imp("imp-b", champion=True))
    payload = await run_checks(improver_store)
    check = next(
        c for c in payload["checks"] if c["name"] == "single_champion"
    )
    assert check["ok"] is False
    assert len(check["violations"]) == 1


async def test_dangling_parent_flagged(improver_store):
    """FK blocks this through the tool path — corruption must be seeded
    with constraints off (e.g. a manual repair or bulk import)."""
    improver_store.conn.execute("PRAGMA foreign_keys = OFF")
    improver_store.conn.execute(
        """INSERT INTO improver_versions
           (id, parent_id, proposal_id, code_artifact_digest,
            harness_artifact_digest, model_ref, search_policy_ref,
            capability_profile_json, is_champion, created_at)
           VALUES ('imp-a', 'imp-ghost', NULL, 'd', NULL, 'm',
                   NULL, '{}', 0, '2026-01-01T00:00:00+00:00')"""
    )
    improver_store.conn.execute("PRAGMA foreign_keys = ON")
    improver_store.conn.commit()
    payload = await run_checks(improver_store)
    check = next(
        c for c in payload["checks"]
        if c["name"] == "lineage_integrity"
    )
    assert check["ok"] is False


async def test_stale_tournament_flagged(improver_store):
    store = improver_store
    store.create_improver(_imp("imp-a", champion=True))
    store.create_improver(_imp("imp-b", parent="imp-a"))
    store.create_meta_contract(MetaContract(
        id="mc-1", version=1,
        metrics={"primary_metric": "hits"}, promotion_policy={},
    ))
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    t = Tournament(
        id="tourn-1", contract_id="mc-1",
        parent_improver_id="imp-a", candidate_improver_id="imp-b",
        budget={}, created_at=old,
    )
    store.create_tournament(t)
    payload = await run_checks(
        store, stale_tournament_seconds=3600
    )
    check = next(
        c for c in payload["checks"]
        if c["name"] == "stale_open_tournaments"
    )
    assert check["ok"] is False
    assert check["violations"][0]["tournament_id"] == "tourn-1"


async def test_rejected_without_reason_flagged(improver_store):
    store = improver_store
    store.create_improver(_imp("imp-a", champion=True))
    p = _proposal("mcp-1", "imp-a", ProposalStatus.rejected)
    store.create_proposal(p)  # rejection_reason None
    payload = await run_checks(store)
    check = next(
        c for c in payload["checks"]
        if c["name"] == "rejected_have_reasons"
    )
    assert check["ok"] is False


async def test_conditional_human_gate_audit(improver_store):
    """Active policy tracing to a conditional proposal whose promote
    decision lacked 'human:' decided_by is flagged — even though the
    enforcement layer should have blocked it."""
    store = improver_store
    store.create_improver(_imp("imp-a", champion=True))
    store.create_proposal(
        _proposal("mcp-1", "imp-a", ProposalStatus.conditional)
    )
    store.create_improver(
        _imp("imp-b", parent="imp-a", proposal_id="mcp-1")
    )
    store.create_meta_decision(MetaDecision(
        id="mdec-1", candidate_improver_id="imp-b",
        verdict=DecisionVerdict.promote,
        evidence_refs=["e"], rationale="r",
        decided_by="arete:protocol",
    ))
    store.create_policy_version(PolicyVersion(
        id="pol-1", improver_version_id="imp-b",
        decision_id="mdec-1", policy={}, status=PolicyStatus.active,
    ))
    payload = await run_checks(store)
    check = next(
        c for c in payload["checks"]
        if c["name"] == "conditional_human_gate"
    )
    assert check["ok"] is False

    # Human-signed → clean.
    store._execute(
        "UPDATE meta_decisions SET decided_by = 'human:bob' "
        "WHERE id = 'mdec-1'"
    )
    store.conn.commit()
    payload = await run_checks(store)
    check = next(
        c for c in payload["checks"]
        if c["name"] == "conditional_human_gate"
    )
    assert check["ok"] is True


async def test_minted_claims_resolve(improver_store):
    """claim_ids on decisions must resolve through the claims channel."""
    store = improver_store
    store.create_improver(_imp("imp-a", champion=True))
    store.create_meta_decision(MetaDecision(
        id="mdec-1", candidate_improver_id="imp-a",
        verdict=DecisionVerdict.hold, evidence_refs=["e"],
        rationale="r", decided_by="human:a", claim_id="claim-x",
    ))

    class FakeClaims:
        async def pull(self, tool, args):
            return json.dumps({"claim_id": args["claim_id"]})

    payload = await run_checks(store, claims=FakeClaims())
    check = next(
        c for c in payload["checks"]
        if c["name"] == "minted_claims_resolve"
    )
    assert check["ok"] is True

    class BrokenClaims:
        async def pull(self, tool, args):
            return json.dumps({"error": "not found"})

    payload = await run_checks(store, claims=BrokenClaims())
    check = next(
        c for c in payload["checks"]
        if c["name"] == "minted_claims_resolve"
    )
    assert check["ok"] is False


async def test_run_and_log_writes_audit_file(improver_store, tmp_path):
    payload = await run_and_log(
        improver_store, config={"log_max_files": 10}, trigger="tool"
    )
    assert "log_file" in payload
    log_file = Path(payload["log_file"])
    assert log_file.exists()
    lines = log_file.read_text().strip().splitlines()
    assert len(lines) == 1
    recorded = json.loads(lines[0])
    assert recorded["trigger"] == "tool"
    assert recorded["server"] == "ml-arete-mcp"


async def test_check_invariants_tool(arete_server):
    from .conftest import call_tool

    payload = json.loads(
        (await arete_server.call_tool("check_invariants", {}))
        .content[0].text
    )
    assert payload["status"] == "ok"
    assert payload["trigger"] == "tool"
