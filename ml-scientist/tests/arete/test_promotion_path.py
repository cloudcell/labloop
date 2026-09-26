"""Promotion-path smoke test — plan-20260926-0438Z A4.

The one shot-though test for the whole Loop-2 promotion path:
register → contract → tournament → results → close → promote
decision → promote_policy → canary → rollback. Asserts the champion
pointer actually moves and restores — the field report found the
path had never been exercised end-to-end on the live lab.
"""

import json

from mcp.server.mcpserver.exceptions import ToolError

from .conftest import call_tool, make_contract, propose, register_imp


async def test_promotion_path_end_to_end(arete_server, adaptors):
    mcp = arete_server

    # Lineage: parent champion → candidate child.
    parent = await register_imp(mcp)
    candidate = await register_imp(mcp, parent_id=parent)
    contract = await make_contract(mcp)

    # Tournament — candidate arm wins.
    t = await call_tool(mcp, "open_tournament", {
        "contract_id": contract,
        "parent_improver_id": parent,
        "candidate_improver_id": candidate,
        "budget": {"descendant_runs": 2},
        "seeds": [7],
    })
    tourn = t["tournament_id"]
    for arm, hits in (("parent", 100), ("candidate", 150)):
        r = await call_tool(mcp, "record_tournament_result", {
            "tournament_id": tourn, "arm": arm,
            "descendant_spec": {"gen": 1}, "metrics": {"hits": hits},
        })
        assert "error" not in r, r
    closed = await call_tool(mcp, "close_tournament", {"tournament_id": tourn})
    assert closed["recursive_gain"] == 1.5

    # Evidence for the decision.
    pull = await call_tool(mcp, "pull_evidence", {
        "context_type": "tournament", "context_id": tourn,
        "source": "loop0", "tool": "list_trials",
        "args": {"programme_id": "prog-x"},
    })
    eref = pull["evidence_ref_id"]

    # Promote decision → promote_policy → champion pointer moves.
    dec = await call_tool(mcp, "record_meta_decision", {
        "candidate_improver_id": candidate,
        "verdict": "promote",
        "evidence_refs": [eref],
        "rationale": "gain 1.5 clears the contracted bar",
        "decided_by": "human:pi",
        "tournament_id": tourn,
    })
    assert "error" not in dec, dec
    promo = await call_tool(mcp, "promote_policy", {
        "candidate_improver_id": candidate,
        "policy": {"search": "beam"},
    })
    assert promo["champion"] == candidate

    # Canary on the new policy — clean pass.
    canary = await call_tool(mcp, "record_canary", {
        "policy_version_id": promo["policy_version_id"],
        "scope": {"traffic": 0.1},
    })
    closed_c = await call_tool(mcp, "close_canary", {
        "canary_id": canary["canary_id"], "status": "passed",
    })
    assert closed_c["status"] == "passed"

    # Rollback — pointer restores to the parent.
    rb = await call_tool(mcp, "rollback", {
        "candidate_improver_id": candidate,
        "rationale": "post-canary holdout regression found later",
        "decided_by": "human:reviewer",
        "evidence_refs": [eref],
    })
    assert rb["was_champion"] is True
    assert rb["restored_champion"] == parent
    champ = await call_tool(mcp, "list_improvers", {"champion_only": True})
    assert champ["improvers"][0]["id"] == parent
