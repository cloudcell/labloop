"""Store-level tests for improver.db — CRUD, lineage, pointer moves."""

from __future__ import annotations

import pytest

from ml_arete_mcp.state.models import (
    EvidenceContext,
    EvidenceRef,
    EvidenceSource,
    ImproverVersion,
    MetaChangeProposal,
    MetaContract,
    MetaDecision,
    DecisionVerdict,
    PolicyStatus,
    PolicyVersion,
    ProposalStatus,
    Tournament,
    TournamentResult,
)


def _imp(i: str, parent=None, champion=False) -> ImproverVersion:
    return ImproverVersion(
        id=i, parent_id=parent,
        code_artifact_digest="sha256:x", model_ref="m",
        capability_profile={}, is_champion=champion,
    )


def _proposal(pid: str, proposer: str) -> MetaChangeProposal:
    return MetaChangeProposal(
        id=pid, proposer_improver_id=proposer,
        spec_delta={"a": 1}, class_map={"planner": "modifiable"},
        expected_benefit="b", falsification="f",
        rollback_plan="r",
    )


def _contract(cid: str, version: int = 1) -> MetaContract:
    return MetaContract(
        id=cid, version=version,
        metrics={"primary_metric": "hits", "direction": "max"},
        promotion_policy={"min_gain": 1.0},
    )


def _tournament(tid, cid, parent, cand) -> Tournament:
    return Tournament(
        id=tid, contract_id=cid,
        parent_improver_id=parent, candidate_improver_id=cand,
        budget={"runs": 3},
    )


def test_improver_crud_and_lineage(improver_store):
    store = improver_store
    store.create_improver(_imp("imp-aaaa", champion=True))
    store.create_improver(_imp("imp-bbbb", parent="imp-aaaa"))
    store.create_improver(_imp("imp-cccc", parent="imp-bbbb"))

    chain = store.lineage("imp-cccc")
    assert [i.id for i in chain] == ["imp-aaaa", "imp-bbbb", "imp-cccc"]
    assert store.is_descendant("imp-cccc", "imp-aaaa")
    assert not store.is_descendant("imp-aaaa", "imp-cccc")
    assert not store.is_descendant("imp-aaaa", "imp-aaaa")


def test_lineage_dangling_parent_terminates(improver_store):
    store = improver_store
    # FK blocks this through create_improver; a dangling parent can
    # still exist in practice (bulk import, FK off) — lineage must
    # terminate rather than loop or raise.
    store.conn.execute("PRAGMA foreign_keys = OFF")
    store.conn.execute(
        """INSERT INTO improver_versions
           (id, parent_id, proposal_id, code_artifact_digest,
            harness_artifact_digest, model_ref, search_policy_ref,
            capability_profile_json, is_champion, created_at)
           VALUES ('imp-orphan', 'imp-ghost', NULL, 'd', NULL, 'm',
                   NULL, '{}', 0, '2026-01-01T00:00:00+00:00')"""
    )
    store.conn.execute("PRAGMA foreign_keys = ON")
    store.conn.commit()
    chain = store.lineage("imp-orphan")
    assert [i.id for i in chain] == ["imp-orphan"]


def test_champion_pointer(improver_store):
    store = improver_store
    store.create_improver(_imp("imp-aaaa", champion=True))
    assert store.get_champion().id == "imp-aaaa"
    assert store.count_champions() == 1

    store.set_champion_flag("imp-aaaa", False)
    store.create_improver(_imp("imp-bbbb"))
    store.set_champion_flag("imp-bbbb", True)
    assert store.get_champion().id == "imp-bbbb"
    assert store.count_champions() == 1


def test_contract_versioning_and_freeze(improver_store):
    store = improver_store
    assert store.next_contract_version() == 1
    store.create_meta_contract(_contract("mcontract-1", 1))
    assert store.next_contract_version() == 2
    store.create_meta_contract(_contract("mcontract-2", 2))

    c = store.get_meta_contract("mcontract-1")
    assert not c.frozen
    store.freeze_meta_contract("mcontract-1")
    assert store.get_meta_contract("mcontract-1").frozen


def test_proposal_status_transitions(improver_store):
    store = improver_store
    store.create_improver(_imp("imp-aaaa", champion=True))
    store.create_proposal(_proposal("mcp-1", "imp-aaaa"))
    p = store.get_proposal("mcp-1")
    assert p.status == ProposalStatus.admitted

    store.set_proposal_status(
        "mcp-1", ProposalStatus.rejected, "class-1 touch"
    )
    p = store.get_proposal("mcp-1")
    assert p.status == ProposalStatus.rejected
    assert p.rejection_reason == "class-1 touch"


def test_tournament_results_insert_only(improver_store):
    store = improver_store
    store.create_improver(_imp("imp-aaaa", champion=True))
    store.create_improver(_imp("imp-bbbb", parent="imp-aaaa"))
    store.create_meta_contract(_contract("mcontract-1"))
    store.create_tournament(
        _tournament("tourn-1", "mcontract-1", "imp-aaaa", "imp-bbbb")
    )
    for i in range(3):
        store.create_tournament_result(TournamentResult(
            id=f"tres-p{i}", tournament_id="tourn-1", arm="parent",
            descendant_spec={"seed": i}, metrics={"hits": 10 + i},
        ))
    results = store.list_tournament_results("tourn-1", arm="parent")
    assert len(results) == 3
    # No update path exists — append-only by construction.

    store.close_tournament("tourn-1", 1.5)
    t = store.get_tournament("tourn-1")
    assert t.status.value == "closed"
    assert t.recursive_gain == 1.5
    assert t.closed_at is not None


def test_meta_decisions_insert_only_and_latest(improver_store):
    store = improver_store
    store.create_improver(_imp("imp-aaaa", champion=True))
    store.create_meta_decision(MetaDecision(
        id="mdec-1", candidate_improver_id="imp-aaaa",
        verdict=DecisionVerdict.hold,
        evidence_refs=["eref-1"], rationale="r1", decided_by="human:a",
    ))
    store.create_meta_decision(MetaDecision(
        id="mdec-2", candidate_improver_id="imp-aaaa",
        verdict=DecisionVerdict.promote,
        evidence_refs=["eref-2"], rationale="r2", decided_by="human:a",
    ))
    latest = store.latest_decision("imp-aaaa")
    assert latest.id == "mdec-2"
    assert len(store.list_meta_decisions("imp-aaaa")) == 2


def test_policy_version_and_active_lookup(improver_store):
    store = improver_store
    store.create_improver(_imp("imp-aaaa", champion=True))
    store.create_meta_decision(MetaDecision(
        id="mdec-1", candidate_improver_id="imp-aaaa",
        verdict=DecisionVerdict.promote,
        evidence_refs=["eref-1"], rationale="r", decided_by="human:a",
    ))
    store.create_policy_version(PolicyVersion(
        id="pol-1", improver_version_id="imp-aaaa",
        decision_id="mdec-1", policy={"lr": 0.1},
        status=PolicyStatus.active,
    ))
    active = store.get_active_policy("imp-aaaa")
    assert active.id == "pol-1"
    store.set_policy_status("pol-1", PolicyStatus.rolled_back)
    assert store.get_active_policy("imp-aaaa") is None


def test_evidence_refs_scoped_by_context(improver_store):
    store = improver_store
    store.create_improver(_imp("imp-aaaa", champion=True))
    store.create_proposal(_proposal("mcp-1", "imp-aaaa"))
    for ctx, cid in (("proposal", "mcp-1"), ("proposal", "mcp-2")):
        store.create_evidence_ref(EvidenceRef(
            id=f"eref-{cid}", context_type=EvidenceContext(ctx),
            context_id=cid, source=EvidenceSource.loop0,
            tool="list_trials", args={}, ref_ids=["trial-x"],
        ))
    refs = store.list_evidence_refs("proposal", "mcp-1")
    assert len(refs) == 1
    assert refs[0].id == "eref-mcp-1"


def test_stats_shape(improver_store):
    stats = improver_store.stats()
    for key in (
        "improvers", "champion_id", "proposals", "tournaments",
        "tournament_results", "decisions", "policy_versions",
        "evidence_refs",
    ):
        assert key in stats
