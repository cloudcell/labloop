"""Decision-debt gate — F-17 from the rc-3 state-machine sweep.

The sweep wedged arete live: closing a tournament created decision
debt that gated EVERY mutator — including pull_evidence /
pull_arm_evidence, the only tools that mint the evidence_refs a
meta_decision requires. Deadlock. And the blocking rule appeared in
none of the check_invariants checks — invisible to operators.

Fixed by: exempting the two evidence miners from the decision-debt
gate (a gate must never strand its own remedy's inputs), naming the
gate as a real check, and giving REMEDY_TOOLS["decision_debt"] the
full discharge path.
"""

import pytest

from ml_arete_mcp.enforcement.recurrence import (
    DEBT_DISCHARGE_TOOLS,
    REMEDY_TOOLS,
    check_no_decision_debt,
    undecided_tournaments,
)

from .conftest import call_tool, make_contract, register_imp


@pytest.fixture
def gated_server(improver_store, adaptors):
    """An arete server with the enforcement stack actually installed —
    the shipped-server shape (create_server only wires the gates when
    enforcement_config is forwarded). Freshness disabled (TTL 0); the
    debt gates stay live."""
    from ml_arete_mcp.server import create_server

    return create_server(
        improver_store,
        adaptors=adaptors,
        enforcement_config={"status_freshness_seconds": 0},
    )


async def _close_paired_tournament(mcp):
    parent = await register_imp(mcp)
    candidate = await register_imp(mcp, parent_id=parent)
    contract = await make_contract(mcp)
    t = await call_tool(mcp, "open_tournament", {
        "contract_id": contract,
        "parent_improver_id": parent,
        "candidate_improver_id": candidate,
        "budget": {"descendant_runs": 1}, "seeds": [1],
    })
    tourn = t["tournament_id"]
    for arm in ("parent", "candidate"):
        r = await call_tool(mcp, "record_tournament_result", {
            "tournament_id": tourn, "arm": arm,
            "descendant_spec": {"gen": 1}, "metrics": {"hits": 1},
        })
        assert "error" not in r, r
    c = await call_tool(mcp, "close_tournament",
                      {"tournament_id": tourn})
    assert "error" not in c, c
    return tourn, candidate


async def test_debt_blocks_mutators_but_not_evidence_miners(
        gated_server):
    """The F-17 repro: debt blocks writes — but never its own remedy's
    inputs."""
    tourn, candidate = await _close_paired_tournament(gated_server)

    # Mutators are gated, naming the debt.
    for tool, args in (
        ("register_improver", {
            "code_artifact_digest": "none", "model_ref": "m"}),
        ("propose_meta_change", {
            "spec_delta": {}, "class_map": {},
            "expected_benefit": "x", "falsification": "x",
            "rollback_plan": "x"}),
        ("open_tournament", {
            "contract_id": "mcontract-x",
            "parent_improver_id": "imp-x",
            "candidate_improver_id": "imp-y", "budget": {}}),
    ):
        r = await call_tool(gated_server, tool, args)
        assert "error" in r, (tool, r)
        assert "meta_decision" in r["error"], r["error"]

    # …but the evidence miners are NOT gated — they mint the
    # discharge currency. This is the line that deadlocked F-17.
    pull = await call_tool(gated_server, "pull_evidence", {
        "context_type": "tournament", "context_id": tourn,
        "source": "loop0", "tool": "list_trials",
        "args": {"programme_id": "prog-x"},
    })
    assert "error" not in pull, pull
    eref = pull["evidence_ref_id"]

    # Discharge: decide (with the fresh ref) → gate clears.
    d = await call_tool(gated_server, "record_meta_decision", {
        "candidate_improver_id": candidate,
        "verdict": "hold",
        "evidence_refs": [eref],
        "rationale": "arms indistinguishable — hold is a decision",
        "decided_by": "human:x",
        "tournament_id": tourn,
    })
    assert "error" not in d, d
    new_imp = await register_imp(gated_server)
    assert new_imp.startswith("imp-")


async def test_debt_discharge_set_covers_both_miners():
    assert DEBT_DISCHARGE_TOOLS == {
        "pull_evidence", "pull_arm_evidence"}
    # The gate function itself exempts them, regardless of caller.
    assert check_no_decision_debt(
        _NoDebt(), "pull_evidence") is None
    assert check_no_decision_debt(
        _NoDebt(), "pull_arm_evidence") is None


async def test_gate_exemption_is_debt_specific(improver_store):
    """Miners are exempt from the DEBT gate — the check function
    returns None for them even when undecided tournaments exist."""
    from ml_arete_mcp.state.models import (
        ImproverVersion, MetaContract, Tournament)

    store = improver_store
    store.create_improver(ImproverVersion(
        id="imp-p", code_artifact_digest="d", model_ref="m"))
    store.create_improver(ImproverVersion(
        id="imp-c", parent_id="imp-p",
        code_artifact_digest="d2", model_ref="m"))
    store.create_meta_contract(MetaContract(
        id="mcontract-x", version=1, metrics={"g": "maximize"},
        promotion_policy={}))
    store.create_tournament(Tournament(
        id="tourn-x", contract_id="mcontract-x",
        parent_improver_id="imp-p",
        candidate_improver_id="imp-c", budget={}))
    store.conn.execute(
        "UPDATE tournaments SET status='closed' WHERE id='tourn-x'")
    store.conn.commit()

    assert undecided_tournaments(store) == ["tourn-x"]
    for miner in DEBT_DISCHARGE_TOOLS:
        assert check_no_decision_debt(store, miner) is None
    # …while every other mutator still eats the debt message.
    msg = check_no_decision_debt(store, "register_improver")
    assert msg is not None and "tourn-x" in msg


async def test_decision_debt_is_a_named_check(
        arete_server, improver_store):
    """F-17's second half: the gate must be observable. Before, 11
    checks reported ok while every write was blocked."""
    from ml_arete_mcp.integrity.checks import run_checks

    tourn, candidate = await _close_paired_tournament(arete_server)

    res = await run_checks(improver_store, claims=None)
    chk = next(c for c in res["checks"] if c["name"] == "decision_debt")
    assert chk["ok"] is False
    assert any(
        v["tournament_id"] == tourn for v in chk["violations"]
    )
    assert res["status"] == "violations"

    # Discharge → the check goes clean.
    pull = await call_tool(arete_server, "pull_evidence", {
        "context_type": "tournament", "context_id": tourn,
        "source": "loop0", "tool": "list_trials",
        "args": {"programme_id": "prog-x"},
    })
    await call_tool(arete_server, "record_meta_decision", {
        "candidate_improver_id": candidate,
        "verdict": "hold",
        "evidence_refs": [pull["evidence_ref_id"]],
        "rationale": "adjudicated", "decided_by": "human:x",
        "tournament_id": tourn,
    })
    res = await run_checks(improver_store, claims=None)
    chk = next(c for c in res["checks"] if c["name"] == "decision_debt")
    assert chk["ok"] is True
    assert chk["violations"] == []


async def test_remedy_map_carries_full_discharge_path():
    """Once decision_debt is a logged check, the violations gate must
    also let the whole discharge path through."""
    remedies = REMEDY_TOOLS["decision_debt"]
    assert {
        "record_meta_decision", "rollback",
        "pull_evidence", "pull_arm_evidence",
    } <= remedies


class _NoDebt:
    """Minimal store stub — undecided_tournaments returns []"""

    class conn:
        @staticmethod
        def execute(*a, **k):
            class _R:
                def fetchall(self):
                    return []
            return _R()
