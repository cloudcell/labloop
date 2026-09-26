"""Tournament void transition — plan-20260926-0643Z.

open → voided: the dead-record exit for tournaments that were opened
but can never honestly close. Covers the FSM edge, the paired-arm
refusal, the status-aware guards (R1/A3), the voided decision-cite
refusal (R1/A1), listability (R1/A2), check/debt rescoping, and the
digest's conditional void recommendation.
"""

import json

from .conftest import call_tool, make_contract, register_imp


async def _open_tournament(mcp, parent=None, candidate=None):
    parent = parent or await register_imp(mcp)
    candidate = candidate or await register_imp(mcp, parent_id=parent)
    contract = await make_contract(mcp)
    t = await call_tool(mcp, "open_tournament", {
        "contract_id": contract,
        "parent_improver_id": parent,
        "candidate_improver_id": candidate,
        "budget": {"descendant_runs": 1},
        "seeds": [1],
    })
    assert "error" not in t, t
    return t["tournament_id"], parent, candidate


async def _record(mcp, tourn, arm, hits=1):
    r = await call_tool(mcp, "record_tournament_result", {
        "tournament_id": tourn, "arm": arm,
        "descendant_spec": {"gen": 1}, "metrics": {"hits": hits},
    })
    assert "error" not in r, r
    return r["result_id"]


async def test_void_empty_tournament(arete_server, improver_store):
    tourn, _, _ = await _open_tournament(arete_server)
    r = await call_tool(arete_server, "void_tournament", {
        "tournament_id": tourn,
        "rationale": "orchestration smoke — runs never completed",
        "decided_by": "human:x",
    })
    assert "error" not in r, r
    assert r["status"] == "voided"
    assert r["voided_at"]
    t = improver_store.get_tournament(tourn)
    assert t.status.value == "voided"
    assert t.recursive_gain is None
    assert t.closed_at
    # the attributed record is on the row
    assert t.void["rationale"].startswith("orchestration smoke")
    assert t.void["decided_by"] == "human:x"
    got = await call_tool(arete_server, "get_tournament",
                          {"tournament_id": tourn})
    assert got["tournament"]["status"] == "voided"
    assert got["tournament"]["void"]["decided_by"] == "human:x"


async def test_void_partial_arm_allowed(arete_server):
    tourn, _, _ = await _open_tournament(arete_server)
    await _record(arete_server, tourn, "parent")
    r = await call_tool(arete_server, "void_tournament", {
        "tournament_id": tourn, "rationale": "one arm never ran",
        "decided_by": "agent:test",
    })
    assert "error" not in r, r
    assert r["status"] == "voided"


async def test_void_paired_refused(arete_server):
    tourn, _, _ = await _open_tournament(arete_server)
    await _record(arete_server, tourn, "parent")
    await _record(arete_server, tourn, "candidate")
    r = await call_tool(arete_server, "void_tournament", {
        "tournament_id": tourn, "rationale": "should refuse",
        "decided_by": "agent:test",
    })
    assert "error" in r
    assert "paired" in r["error"] and "close_tournament" in r["error"]


async def test_void_terminal_and_unknown_refused(arete_server):
    tourn, _, _ = await _open_tournament(arete_server)
    await _record(arete_server, tourn, "parent")
    await _record(arete_server, tourn, "candidate")
    await call_tool(arete_server, "close_tournament",
                    {"tournament_id": tourn})
    for tid, expect in (
        (tourn, "closed"),
        ("tourn-nonexistent", "not found"),
    ):
        r = await call_tool(arete_server, "void_tournament", {
            "tournament_id": tid, "rationale": "x",
            "decided_by": "agent:test",
        })
        assert "error" in r and expect in r["error"].lower(), r
    # double-void refuses — terminal
    tourn2, _, _ = await _open_tournament(arete_server)
    await call_tool(arete_server, "void_tournament", {
        "tournament_id": tourn2, "rationale": "first",
        "decided_by": "agent:test",
    })
    r = await call_tool(arete_server, "void_tournament", {
        "tournament_id": tourn2, "rationale": "again",
        "decided_by": "agent:test",
    })
    assert "error" in r and "voided" in r["error"]


async def test_void_requires_rationale_and_attribution(arete_server):
    tourn, _, _ = await _open_tournament(arete_server)
    for args in (
        {"rationale": "", "decided_by": "agent:test"},
        {"rationale": "   ", "decided_by": "agent:test"},
        {"rationale": "dead", "decided_by": ""},
    ):
        r = await call_tool(arete_server, "void_tournament",
                            {"tournament_id": tourn, **args})
        assert "error" in r, r


async def test_voided_drops_stale_check(arete_server, improver_store):
    from ml_arete_mcp.integrity.checks import run_checks

    tourn, _, _ = await _open_tournament(arete_server)
    improver_store.conn.execute(
        "UPDATE tournaments SET created_at = "
        "'2020-01-01T00:00:00+00:00' "
        "WHERE id = ?", (tourn,))
    improver_store.conn.commit()

    async def _count():
        res = await run_checks(improver_store, claims=None)
        chk = next(c for c in res["checks"]
                   if c["name"] == "stale_open_tournaments")
        return sum(len(c["violations"]) for c in res["checks"]
                   if c["name"] == "stale_open_tournaments"), chk

    before, _ = await _count()
    assert before == 1
    await call_tool(arete_server, "void_tournament", {
        "tournament_id": tourn, "rationale": "stale smoke",
        "decided_by": "agent:test",
    })
    after, chk = await _count()
    assert after == 0
    assert chk["ok"] is True


async def test_voided_no_decision_debt_or_open_work(
        arete_server, improver_store):
    from ml_arete_mcp.resources.status import status_digest

    tourn, _, _ = await _open_tournament(arete_server)
    await call_tool(arete_server, "void_tournament", {
        "tournament_id": tourn, "rationale": "dead", 
        "decided_by": "agent:test",
    })
    digest = status_digest(improver_store)
    assert all(
        b.get("tournament_id") != tourn for b in digest["blockers"]
    )
    assert all(
        w.get("id") != tourn for w in digest["open_work"]
    )
    assert all(
        tourn not in r.get("entity_refs", [])
        for r in digest["recommended_next"]
    )


async def test_digest_offers_void_only_when_stale(
        arete_server, improver_store):
    from ml_arete_mcp.resources.status import status_digest

    tourn, _, _ = await _open_tournament(arete_server)
    # fresh — no void rec
    fresh = status_digest(improver_store, stale_seconds=10**9)
    assert not any(
        r["tool"] == "void_tournament"
        for r in fresh["recommended_next"]
    )
    # stale — void rec appears alongside record_tournament_result
    stale = status_digest(improver_store, stale_seconds=0)
    tools = [
        (r["tool"], tourn in r.get("entity_refs", []))
        for r in stale["recommended_next"]
    ]
    assert ("void_tournament", True) in tools
    assert ("record_tournament_result", True) in tools
    entry = next(
        w for w in stale["open_work"] if w.get("id") == tourn
    )
    assert entry["stale"] is True
    assert "idle_hours" in entry


async def test_decision_cite_voided_refused(arete_server):
    tourn, _, candidate = await _open_tournament(arete_server)
    pull = await call_tool(arete_server, "pull_evidence", {
        "context_type": "tournament", "context_id": tourn,
        "source": "loop0", "tool": "list_trials",
        "args": {"programme_id": "prog-x"},
    })
    eref = pull["evidence_ref_id"]
    await call_tool(arete_server, "void_tournament", {
        "tournament_id": tourn, "rationale": "dead comparison",
        "decided_by": "agent:test",
    })
    r = await call_tool(arete_server, "record_meta_decision", {
        "candidate_improver_id": candidate,
        "verdict": "promote",
        "evidence_refs": [eref],
        "rationale": "must refuse — cites a voided comparison",
        "decided_by": "human:x",
        "tournament_id": tourn,
    })
    assert "error" in r
    assert "voided" in r["error"]


async def test_list_tournaments_voided(arete_server):
    tourn, _, _ = await _open_tournament(arete_server)
    await call_tool(arete_server, "void_tournament", {
        "tournament_id": tourn, "rationale": "dead",
        "decided_by": "agent:test",
    })
    r = await call_tool(arete_server, "list_tournaments",
                        {"status": "voided"})
    assert "error" not in r, r
    assert any(t["id"] == tourn for t in r["tournaments"])
    open_l = await call_tool(arete_server, "list_tournaments",
                             {"status": "open"})
    assert all(t["id"] != tourn for t in open_l["tournaments"])


async def test_voided_guards_say_voided(arete_server):
    """A3: every non-open refusal names the actual state."""
    tourn, _, _ = await _open_tournament(arete_server)
    await _record(arete_server, tourn, "parent")
    rid = (
        await call_tool(arete_server, "record_tournament_result", {
            "tournament_id": tourn, "arm": "parent",
            "descendant_spec": {"gen": 2},
            "metrics": {"hits": 5},
        })
    )["result_id"]
    await call_tool(arete_server, "void_tournament", {
        "tournament_id": tourn, "rationale": "dead",
        "decided_by": "agent:test",
    })

    r1 = await call_tool(arete_server, "record_tournament_result", {
        "tournament_id": tourn, "arm": "candidate",
        "descendant_spec": {"gen": 1}, "metrics": {"hits": 1},
    })
    assert "error" in r1 and "voided" in r1["error"]
    assert "closed" not in r1["error"]

    r2 = await call_tool(arete_server, "close_tournament",
                         {"tournament_id": tourn})
    assert "error" in r2 and "voided" in r2["error"]
    assert "closed" not in r2["error"]

    r3 = await call_tool(arete_server, "correct_tournament_result", {
        "result_id": rid, "reason": "fix",
        "metrics": {"hits": 9},
    })
    assert "error" in r3 and "voided" in r3["error"]
    assert "closed" not in r3["error"]
