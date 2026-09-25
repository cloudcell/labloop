"""Tool-level tests — the meta-change lifecycle end-to-end, in-process."""

from __future__ import annotations

from .conftest import call_tool, make_contract, propose, register_imp


async def test_register_improver_bootstrap_champion(arete_server):
    mcp = arete_server
    first = await register_imp(mcp)
    second = await register_imp(mcp, parent_id=first)
    imp1 = await call_tool(mcp, "get_improver", {"improver_id": first})
    imp2 = await call_tool(mcp, "get_improver", {"improver_id": second})
    assert imp1["improver"]["is_champion"] is True
    assert imp2["improver"]["is_champion"] is False


async def test_register_requires_existing_parent(arete_server):
    mcp = arete_server
    result = await call_tool(mcp, "register_improver", {
        "code_artifact_digest": "none", "model_ref": "m",
        "capability_profile": {}, "parent_id": "imp-ghost",
    })
    assert "error" in result and "not found" in result["error"]


async def test_malformed_digest_rejected(arete_server):
    mcp = arete_server
    for bad in ("sha256:d", "sha256:" + "0" * 63, ""):
        result = await call_tool(mcp, "register_improver", {
            "code_artifact_digest": bad, "model_ref": "m",
            "capability_profile": {},
        })
        assert "error" in result, bad
        assert "not a content digest" in result["error"], bad


async def test_unresolving_digest_rejected(arete_server, adaptors):
    """A well-formed hash that resolves to nothing upstream fails."""
    adaptors.loop0.payloads["describe_blob"] = (
        '{"exists": false, "resolved_in": []}'
    )
    result = await call_tool(arete_server, "register_improver", {
        "code_artifact_digest": "sha256:" + "0" * 64,
        "model_ref": "m", "capability_profile": {},
    })
    assert "error" in result
    assert "does not resolve" in result["error"]
    assert "'none'" in result["error"]  # teaches the honest out


async def test_digest_resolution_goes_through_loop0(
        arete_server, adaptors):
    """The digest is verified upstream — the channel call is recorded."""
    imp_id = await register_imp(arete_server)
    assert imp_id.startswith("imp-")
    blob_calls = [c for c in adaptors.loop0.calls
                  if c[0] == "describe_blob"]
    assert blob_calls, "register_improver must resolve via loop0"
    assert blob_calls[0][1]["content_hash"].startswith("sha256:")


async def test_absent_loop0_refuses_digest_accepts_none(
        improver_store):
    """Degraded-honest: no channel → no verification → digests
    refused outright; 'none' is the only honest answer."""
    from ml_arete_mcp.clients.adaptors import Adaptors
    from ml_arete_mcp.server import create_server

    bare = Adaptors()  # nothing wired — loop0 absent
    mcp = create_server(improver_store, adaptors=bare)
    result = await call_tool(mcp, "register_improver", {
        "code_artifact_digest": "sha256:" + "ab" * 32,
        "model_ref": "m", "capability_profile": {},
    })
    assert "error" in result
    assert "loop0" in result["error"]
    result = await call_tool(mcp, "register_improver", {
        "code_artifact_digest": "none", "model_ref": "m",
        "capability_profile": {},
    })
    assert "error" not in result, result
    assert result["improver_id"].startswith("imp-")


async def test_proposal_class1_rejected_at_admission(arete_server):
    mcp = arete_server
    imp = await register_imp(mcp)
    result = await propose(
        mcp, imp, class_map={"enforcement_kernel": "modifiable"}
    )
    assert result["status"] == "rejected"
    assert "enforcement_kernel" in result["rejection_reason"]
    # Durable: the rejection is stored, not silent.
    got = await call_tool(
        mcp, "get_proposal", {"proposal_id": result["proposal_id"]}
    )
    assert got["proposal"]["status"] == "rejected"


async def test_proposal_class3_conditional(arete_server):
    mcp = arete_server
    imp = await register_imp(mcp)
    result = await propose(
        mcp, imp, class_map={"scheduler": "modifiable"}
    )
    assert result["status"] == "conditional"


async def test_register_child_of_proposal_must_match_proposer(
    arete_server,
):
    mcp = arete_server
    parent = await register_imp(mcp)
    other = await register_imp(mcp)
    prop = await propose(mcp, parent)
    result = await call_tool(mcp, "register_improver", {
        "code_artifact_digest": "none", "model_ref": "m",
        "capability_profile": {}, "parent_id": other,
        "proposal_id": prop["proposal_id"],
    })
    assert "error" in result and "not by parent" in result["error"]


async def test_full_lifecycle_gate_walk(arete_server, adaptors):
    """The plan's gate: register → contract → propose → tournament →
    results → close (gain) → decision → promote → rollback."""
    mcp = arete_server

    # Lineage: parent champion + candidate child.
    parent = await register_imp(mcp)
    candidate = await register_imp(mcp, parent_id=parent)
    lineage = await call_tool(
        mcp, "get_improver_lineage", {"improver_id": candidate}
    )
    assert [i["id"] for i in lineage["lineage"]] == [parent, candidate]

    # Contract.
    contract = await make_contract(mcp)

    # Proposal by the parent; candidate implements it.
    prop = await propose(mcp, parent, class_map={"prompts": "m"})
    assert prop["status"] == "admitted"

    # Tournament — paired arms, one budget.
    result = await call_tool(mcp, "open_tournament", {
        "contract_id": contract,
        "parent_improver_id": parent,
        "candidate_improver_id": candidate,
        "budget": {"descendant_runs": 2, "calls": 1500},
        "seeds": [7, 31],
    })
    assert "error" not in result
    tourn = result["tournament_id"]
    assert result["contract_frozen"] is True

    # Non-descendant candidate refused.
    stranger = await register_imp(mcp)
    result = await call_tool(mcp, "open_tournament", {
        "contract_id": contract,
        "parent_improver_id": parent,
        "candidate_improver_id": stranger,
        "budget": {"runs": 1},
    })
    assert "error" in result and "descend" in result["error"]

    # Evidence pull inside the tournament context.
    pull = await call_tool(mcp, "pull_evidence", {
        "context_type": "tournament", "context_id": tourn,
        "source": "loop0", "tool": "list_trials",
        "args": {"programme_id": "prog-x"},
    })
    assert "error" not in pull
    eref = pull["evidence_ref_id"]

    # Results on both arms — candidate produces better descendants.
    for arm, hits in (("parent", 100), ("parent", 120),
                      ("candidate", 150), ("candidate", 160)):
        r = await call_tool(mcp, "record_tournament_result", {
            "tournament_id": tourn, "arm": arm,
            "descendant_spec": {"gen": 1},
            "metrics": {"hits": hits},
        })
        assert "error" not in r, r

    closed = await call_tool(
        mcp, "close_tournament", {"tournament_id": tourn}
    )
    assert closed["status"] == "closed"
    # E[best]: parent (100,120)→120… one group each → best per group.
    assert closed["recursive_gain"] == 160 / 120

    # Results on a closed tournament refused.
    r = await call_tool(mcp, "record_tournament_result", {
        "tournament_id": tourn, "arm": "parent",
        "descendant_spec": {}, "metrics": {"hits": 1},
    })
    assert "error" in r and "closed" in r["error"]

    # Promote without a decision → refused.
    r = await call_tool(mcp, "promote_policy", {
        "candidate_improver_id": candidate, "policy": {"k": 1},
    })
    assert "error" in r and "meta_decision" in r["error"]

    # Decision: promote.
    dec = await call_tool(mcp, "record_meta_decision", {
        "candidate_improver_id": candidate,
        "verdict": "promote",
        "evidence_refs": [eref],
        "rationale": "recursive_gain 1.33 exceeds contracted bar",
        "decided_by": "arete:protocol",
        "tournament_id": tourn,
        "contract_id": contract,
    })
    assert "error" not in dec, dec
    assert dec["claim_status"] == "minted"
    assert dec["claim_id"]
    # Claim + derived_from edge to the pulled trial entity.
    claims = adaptors.claims
    assert len(claims.minted) == 1
    assert claims.minted[0]["source_id"] == dec["decision_id"]

    # Promote — champion pointer moves.
    promo = await call_tool(mcp, "promote_policy", {
        "candidate_improver_id": candidate,
        "policy": {"search": "beam"},
    })
    assert "error" not in promo, promo
    assert promo["champion"] == candidate
    assert promo["displaced_champion"] == parent

    # Rollback — decision + pointer restore.
    rb = await call_tool(mcp, "rollback", {
        "candidate_improver_id": candidate,
        "rationale": "canary regression on holdout",
        "decided_by": "human:reviewer",
        "evidence_refs": [eref],
    })
    assert "error" not in rb, rb
    assert rb["was_champion"] is True
    assert rb["restored_champion"] == parent

    champ = await call_tool(
        mcp, "list_improvers", {"champion_only": True}
    )
    assert champ["improvers"][0]["id"] == parent


async def test_close_refuses_unpaired_tournament(arete_server):
    mcp = arete_server
    parent = await register_imp(mcp)
    candidate = await register_imp(mcp, parent_id=parent)
    contract = await make_contract(mcp)
    t = await call_tool(mcp, "open_tournament", {
        "contract_id": contract, "parent_improver_id": parent,
        "candidate_improver_id": candidate,
        "budget": {"runs": 1},
    })
    closed = await call_tool(
        mcp, "close_tournament", {"tournament_id": t["tournament_id"]}
    )
    assert "error" in closed and "parent" in closed["error"]
    # One arm populated, other empty → still refused.
    await call_tool(mcp, "record_tournament_result", {
        "tournament_id": t["tournament_id"], "arm": "parent",
        "descendant_spec": {}, "metrics": {"hits": 5},
    })
    closed = await call_tool(
        mcp, "close_tournament", {"tournament_id": t["tournament_id"]}
    )
    assert "error" in closed and "candidate" in closed["error"]


async def test_conditional_requires_human_gate(arete_server):
    mcp = arete_server
    parent = await register_imp(mcp)
    prop = await propose(
        mcp, parent, class_map={"metric_weighting": "m"}
    )
    assert prop["status"] == "conditional"
    candidate = await register_imp(
        mcp, parent_id=parent, proposal_id=prop["proposal_id"]
    )
    # Evidence pulled under the proposal context.
    pull = await call_tool(mcp, "pull_evidence", {
        "context_type": "proposal",
        "context_id": prop["proposal_id"],
        "source": "anamnesis", "tool": "list_claims",
    })
    eref = pull["evidence_ref_id"]
    # Non-human decided_by promotes → still blocked at promote_policy.
    dec = await call_tool(mcp, "record_meta_decision", {
        "candidate_improver_id": candidate, "verdict": "promote",
        "evidence_refs": [eref], "rationale": "looks good",
        "decided_by": "arete:protocol",
    })
    assert "error" not in dec
    promo = await call_tool(mcp, "promote_policy", {
        "candidate_improver_id": candidate, "policy": {"w": 1},
    })
    assert "error" in promo and "human" in promo["error"]
    # Human-signed promote decision → passes.
    dec = await call_tool(mcp, "record_meta_decision", {
        "candidate_improver_id": candidate, "verdict": "promote",
        "evidence_refs": [eref], "rationale": "reviewed",
        "decided_by": "human:alice",
    })
    promo = await call_tool(mcp, "promote_policy", {
        "candidate_improver_id": candidate, "policy": {"w": 1},
    })
    assert "error" not in promo


async def test_latest_decision_supersedes_promote(arete_server):
    mcp = arete_server
    parent = await register_imp(mcp)
    candidate = await register_imp(mcp, parent_id=parent)
    pull = await call_tool(mcp, "pull_evidence", {
        "context_type": "tournament",
        "context_id": (await call_tool(mcp, "open_tournament", {
            "contract_id": await make_contract(mcp),
            "parent_improver_id": parent,
            "candidate_improver_id": candidate,
            "budget": {"r": 1},
        }))["tournament_id"],
        "source": "loop0", "tool": "list_archives",
    })
    eref = pull["evidence_ref_id"]
    for verdict in ("promote", "reject"):
        await call_tool(mcp, "record_meta_decision", {
            "candidate_improver_id": candidate, "verdict": verdict,
            "evidence_refs": [eref], "rationale": "r",
            "decided_by": "human:a",
        })
    promo = await call_tool(mcp, "promote_policy", {
        "candidate_improver_id": candidate, "policy": {},
    })
    assert "error" in promo and "reject" in promo["error"]


async def test_decision_without_evidence_refused(arete_server):
    mcp = arete_server
    imp = await register_imp(mcp)
    dec = await call_tool(mcp, "record_meta_decision", {
        "candidate_improver_id": imp, "verdict": "hold",
        "evidence_refs": [], "rationale": "r",
        "decided_by": "human:a",
    })
    assert "error" in dec and "evidence" in dec["error"]


async def test_evidence_smuggling_refused(arete_server):
    """An eref from an unrelated context cannot ground a decision."""
    mcp = arete_server
    parent = await register_imp(mcp)
    candidate = await register_imp(mcp, parent_id=parent)
    stranger = await register_imp(mcp)
    prop = await propose(mcp, stranger)
    pull = await call_tool(mcp, "pull_evidence", {
        "context_type": "proposal", "context_id": prop["proposal_id"],
        "source": "loop0", "tool": "list_trials",
        "args": {"programme_id": "prog-x"},
    })
    dec = await call_tool(mcp, "record_meta_decision", {
        "candidate_improver_id": candidate, "verdict": "hold",
        "evidence_refs": [pull["evidence_ref_id"]],
        "rationale": "r", "decided_by": "human:a",
    })
    assert "error" in dec and "not" in dec["error"]


async def test_pull_requires_live_context(arete_server):
    mcp = arete_server
    pull = await call_tool(mcp, "pull_evidence", {
        "context_type": "proposal", "context_id": "mcp-ghost",
        "source": "loop0", "tool": "list_trials",
    })
    assert "error" in pull and "not found" in pull["error"].lower()


async def test_pull_rejected_context(arete_server):
    mcp = arete_server
    imp = await register_imp(mcp)
    prop = await propose(
        mcp, imp, class_map={"enforcement_kernel": "x"}
    )
    pull = await call_tool(mcp, "pull_evidence", {
        "context_type": "proposal", "context_id": prop["proposal_id"],
        "source": "loop0", "tool": "list_trials",
    })
    assert "error" in pull and "rejected" in pull["error"]


async def test_absent_adaptors_fail_loudly(improver_store):
    """No adaptors wired — pulls fail clearly, minting reports
    'disabled', and the server still serves its own state."""
    from ml_arete_mcp.clients.adaptors import Adaptors
    from ml_arete_mcp.server import create_server

    mcp = create_server(improver_store, adaptors=Adaptors())
    # 'none' — no loop0 to resolve a digest against.
    imp = await register_imp(mcp, code_artifact_digest="none")
    prop = await propose(mcp, imp)
    pull = await call_tool(mcp, "pull_evidence", {
        "context_type": "proposal", "context_id": prop["proposal_id"],
        "source": "loop1", "tool": "list_investigations",
    })
    assert "error" in pull and "adaptor" in pull["error"]

    health_payload = await call_tool(mcp, "list_improvers", {})
    assert health_payload["total"] == 1


async def test_canary_record_lifecycle(arete_server):
    mcp = arete_server
    parent = await register_imp(mcp)
    candidate = await register_imp(mcp, parent_id=parent)
    contract = await make_contract(mcp)
    t = (await call_tool(mcp, "open_tournament", {
        "contract_id": contract, "parent_improver_id": parent,
        "candidate_improver_id": candidate, "budget": {"r": 1},
    }))["tournament_id"]
    pull = await call_tool(mcp, "pull_evidence", {
        "context_type": "tournament", "context_id": t,
        "source": "loop0", "tool": "list_archives",
    })
    await call_tool(mcp, "record_meta_decision", {
        "candidate_improver_id": candidate, "verdict": "promote",
        "evidence_refs": [pull["evidence_ref_id"]],
        "rationale": "r", "decided_by": "human:a",
    })
    promo = await call_tool(mcp, "promote_policy", {
        "candidate_improver_id": candidate, "policy": {"p": 1},
    })
    canary = await call_tool(mcp, "record_canary", {
        "policy_version_id": promo["policy_version_id"],
        "scope": {"traffic": 0.1},
    })
    assert canary["status"] == "open"
    closed = await call_tool(mcp, "close_canary", {
        "canary_id": canary["canary_id"], "status": "regressed",
    })
    assert closed["status"] == "regressed"
    assert "next_step" in closed


async def test_get_and_list_views(arete_server):
    mcp = arete_server
    imp = await register_imp(mcp)
    await propose(mcp, imp)
    props = await call_tool(mcp, "list_proposals", {})
    assert props["total"] == 1
    tourns = await call_tool(mcp, "list_tournaments", {})
    assert tourns["total"] == 0
    decs = await call_tool(mcp, "list_decisions", {})
    assert decs["total"] == 0
