"""HTTP battery — real arete server + stub upstreams, end-to-end.

Launches tests/downstream_mcp_server.py stubs for loop0/loop1/claims
and a real `python -m ml_arete_mcp` subprocess over Streamable HTTP,
then walks the plan's gate: register → contract → propose →
tournament → results → close → decision (claim minted through the
real adaptor path) → promote → rollback.
"""

from __future__ import annotations

import httpx

from .conftest import call_tool_http


async def test_health_reports_channels(arete_http_server):
    arete_url, gui_url, *_ = arete_http_server
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{arete_url}/health")
        assert r.status_code == 200
        upstream = r.json()["upstream"]
        assert upstream == {
            "loop0": True, "loop1": True, "claims": True,
            # this fixture doesn't wire the orchestration channel —
            # absent means absent, reported honestly.
            "loop1_orchestration": False,
        }

        r = await client.get(f"{arete_url}/health/deep")
        assert r.status_code == 200
        assert r.json()["server"] == "ml-arete-mcp"

        r = await client.get(f"{gui_url}/health")
        assert r.status_code == 200


async def test_gate_walk_over_http(arete_http_server):
    arete_url, gui_url, *_ = arete_http_server

    parent = (await call_tool_http(arete_url, "register_improver", {
        "code_artifact_digest": "none", "model_ref": "m0",
        "capability_profile": {"v": 1},
    }))["improver_id"]

    # Admission: class-1 touch rejected; class-2 admitted.
    bad = await call_tool_http(arete_url, "propose_meta_change", {
        "proposer_improver_id": parent,
        "spec_delta": {"x": 1},
        "class_map": {"rollback_mechanism": "m"},
        "expected_benefit": "b", "falsification": "f",
        "rollback_plan": "r",
    })
    assert bad["status"] == "rejected"

    prop = await call_tool_http(arete_url, "propose_meta_change", {
        "proposer_improver_id": parent,
        "spec_delta": {"planner": "beam"},
        "class_map": {"planner": "m"},
        "expected_benefit": "b", "falsification": "f",
        "rollback_plan": "r",
    })
    assert prop["status"] == "admitted"

    candidate = (await call_tool_http(arete_url, "register_improver", {
        "code_artifact_digest": "none", "model_ref": "m1",
        "capability_profile": {"v": 2},
        "parent_id": parent, "proposal_id": prop["proposal_id"],
    }))["improver_id"]

    contract = (await call_tool_http(arete_url, "create_meta_contract", {
        "metrics": {"primary_metric": "hits", "direction": "max"},
        "promotion_policy": {"min_gain": 1.0},
    }))["contract_id"]

    tourn = (await call_tool_http(arete_url, "open_tournament", {
        "contract_id": contract,
        "parent_improver_id": parent,
        "candidate_improver_id": candidate,
        "budget": {"descendant_runs": 2},
        "seeds": [7, 31],
    }))["tournament_id"]

    # Evidence pull through the real loop0 adaptor — logged.
    pull = await call_tool_http(arete_url, "pull_evidence", {
        "context_type": "tournament", "context_id": tourn,
        "source": "loop0", "tool": "list_active_programmes",
    })
    assert "error" not in pull, pull
    eref = pull["evidence_ref_id"]
    assert pull["ref_ids"]  # stub returns prog/trial ids

    for arm, hits in (("parent", 80), ("candidate", 120)):
        r = await call_tool_http(
            arete_url, "record_tournament_result", {
                "tournament_id": tourn, "arm": arm,
                "descendant_spec": {"g": 1},
                "metrics": {"hits": hits},
            }
        )
        assert "error" not in r, r

    closed = await call_tool_http(
        arete_url, "close_tournament", {"tournament_id": tourn}
    )
    assert closed["recursive_gain"] == 120 / 80

    dec = await call_tool_http(arete_url, "record_meta_decision", {
        "candidate_improver_id": candidate, "verdict": "promote",
        "evidence_refs": [eref],
        "rationale": "gain 1.5 over the paired parent arm",
        "decided_by": "human:operator",
        "tournament_id": tourn, "contract_id": contract,
    })
    assert dec["claim_status"] == "minted", dec
    assert dec["claim_id"].startswith("claim-")

    promo = await call_tool_http(arete_url, "promote_policy", {
        "candidate_improver_id": candidate,
        "policy": {"search": "beam", "width": 4},
    })
    assert promo["champion"] == candidate

    rb = await call_tool_http(arete_url, "rollback", {
        "candidate_improver_id": candidate,
        "rationale": "post-promotion regression observed",
        "decided_by": "human:operator",
        "evidence_refs": [eref],
    })
    assert rb["restored_champion"] == parent

    # The GUI reflects the record — read-only.
    async with httpx.AsyncClient() as client:
        r = await client.get(gui_url)
        assert r.status_code == 200
        assert parent in r.text and candidate in r.text
        r = await client.get(f"{gui_url}/tournament/{tourn}")
        assert r.status_code == 200
        assert "1.500" in r.text
        r = await client.get(f"{gui_url}/improver/{candidate}")
        assert r.status_code == 200
        assert "rollback" in r.text


async def test_degraded_absent_adaptors(tmp_path):
    """Server starts with no upstreams; pulls fail honestly."""
    import subprocess
    import sys
    from .conftest import find_free_port, wait_for_port

    port = find_free_port(28900)
    db = str(tmp_path / "improver.db")
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "ml_arete_mcp",
            "--transport", "http", "--port", str(port),
            "--stateless", "--db-path", db,
            "--config", "/dev/null",
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
    )
    try:
        wait_for_port(port, timeout=20.0)
        url = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{url}/health")
            assert r.json()["upstream"] == {
                "loop0": False, "loop1": False, "claims": False,
                "loop1_orchestration": False,
            }
        imp = (await call_tool_http(url, "register_improver", {
            "code_artifact_digest": "none", "model_ref": "m",
            "capability_profile": {},
        }))["improver_id"]
        prop = await call_tool_http(url, "propose_meta_change", {
            "proposer_improver_id": imp, "spec_delta": {},
            "class_map": {"planner": "m"},
            "expected_benefit": "b", "falsification": "f",
            "rollback_plan": "r",
        })
        pull = await call_tool_http(url, "pull_evidence", {
            "context_type": "proposal",
            "context_id": prop["proposal_id"],
            "source": "loop0", "tool": "list_trials",
        })
        assert "error" in pull and "adaptor" in pull["error"]
    finally:
        proc.terminate()
        proc.wait(timeout=10)
