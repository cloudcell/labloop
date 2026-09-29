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


# --- rc-6 P1: Loop-2 ref types must mint, not fail --------------------


async def test_meta_decision_mints_with_loop2_ref_types(
    arete_server, adaptors, improver_store
):
    """A meta-decision citing archive-/tourn- evidence mints a claim
    with typed edges — previously every Loop-2 ref_type anamnesis's
    enum rejected, so claim_status silently degraded to 'failed'."""
    from ml_arete_mcp.state.models import EvidenceRef

    # A tournament involving the candidate scopes the evidence refs —
    # decisions may only cite contexts related to the candidate.
    parent = await register_imp(arete_server)
    candidate = await register_imp(arete_server, parent_id=parent)
    contract = await make_contract(arete_server)
    p = await propose(arete_server, candidate)
    assert "error" not in p, p
    t = await call_tool(arete_server, "open_tournament", {
        "contract_id": contract,
        "parent_improver_id": parent,
        "candidate_improver_id": candidate,
        "budget": {"descendant_runs": 2},
        "seeds": [1],
    })
    tourn = t["tournament_id"]

    improver_store.create_evidence_ref(EvidenceRef(
        id="eref-arch", context_type="tournament",
        context_id=tourn, source="loop0",
        tool="get_archive", args={},
        ref_ids=["archive-abc123", "tourn-xyz789"],
    ))
    improver_store.create_evidence_ref(EvidenceRef(
        id="eref-probe", context_type="tournament",
        context_id=tourn, source="loop0",
        tool="get_trial_status", args={},
        ref_ids=["trial-q1"],
    ))
    dec = await call_tool(arete_server, "record_meta_decision", {
        "candidate_improver_id": candidate,
        "verdict": "hold",
        "evidence_refs": ["eref-arch", "eref-probe"],
        "rationale": "inconclusive margin",
        "decided_by": "human:pi",
        "tournament_id": tourn,
    })
    assert "error" not in dec, dec
    assert dec["claim_status"] == "minted"
    assert dec["claim_id"].startswith("claim-")

    # Edges carry real typed ref_types, not blanket 'external'.
    edges = adaptors.claims.edges
    by_ref = {e["to_ref"]: e["ref_type"] for e in edges}
    assert by_ref["archive-abc123"] == "archive"
    assert by_ref["tourn-xyz789"] == "tournament"
    assert by_ref["trial-q1"] == "trial"


def test_ref_type_map_stays_inside_anamnesis_vocabulary():
    """Subset guard: every ref_type the _REF_TYPE_BY_PREFIX table can
    emit must be a legal anamnesis RefType — drift here re-breaks
    claim minting."""
    from ml_anamnesis_mcp.state.models import RefType
    from ml_arete_mcp.tools.decisions import (
        _CLAIM_REF_TYPES,
        _REF_TYPE_BY_PREFIX,
        _ref_type_for,
    )

    legal = {t.value for t in RefType}
    assert set(_REF_TYPE_BY_PREFIX.values()) <= legal, (
        set(_REF_TYPE_BY_PREFIX.values()) - legal
    )
    # The defensive mirror equals the real enum exactly.
    assert _CLAIM_REF_TYPES == legal
    # An unknown future prefix degrades to external, never an
    # unmintable type.
    assert _ref_type_for("zzz-future-1") == "external"
    assert _ref_type_for("archive-x") == "archive"


def test_anamnesis_relate_accepts_loop2_types():
    """The claims tool Literal covers the extended vocabulary — a
    relate() with a Loop-2 ref_type must not be a schema error."""
    from ml_anamnesis_mcp.state.models import RefType

    for rt in (
        "investigation", "finding", "archive", "improver",
        "tournament", "tournament_result", "proposal",
        "meta_contract", "meta_decision", "policy_version",
        "canary_deployment", "candidate", "contract", "decision",
    ):
        assert RefType(rt).value == rt
