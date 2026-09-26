"""Loop-2 orchestration HTTP battery — the real-server gate.

Real ml_episteme + real ml_zetesis (evidence+promotion wired to
loop0) + real ml_arete with the loop1_orchestration channel. The
gate from plan-20260918-1448Z exercised end to end: open → spawn
(cap + direction + attribution enforced upstream) → evidence →
results (spawn-scoped) → close (budget audit) → verdict →
tournament result → close → invariants clean on both servers.
"""

from __future__ import annotations

import pytest

from .conftest import call_tool_http


async def _mk_improver(arete, parent_id=None):
    args = {
        "code_artifact_digest": "none",
        "model_ref": "orch-battery",
        "capability_profile": {"can_propose": True},
    }
    if parent_id:
        args["parent_id"] = parent_id
    r = await call_tool_http(arete, "register_improver", args)
    assert "error" not in r, r
    return r["improver_id"]


async def _mk_candidate(loop0):
    r = await call_tool_http(loop0, "register_candidate", {
        "code_artifact_digest": "none",
        "model_ref": "orch-model",
        "capability_profile": {"tools": ["t1"]},
    })
    assert "error" not in r, r
    return r["candidate_id"]


@pytest.fixture(scope="module")
def battery(arete_orchestration_server):
    """The shared gate setup: tournament + upstream lineage +
    contract + rostered challenger. Yields a context dict.

    Sync wrapper — module-scoped async fixtures need a module-scoped
    event loop the suite doesn't configure; asyncio.run does the
    whole setup in one shot instead."""
    import asyncio
    return asyncio.run(_battery_setup(arete_orchestration_server))


async def _battery_setup(arete_orchestration_server):
    arete, _agui, zet, _zgui, loop0, _claims = (
        arete_orchestration_server
    )

    # Loop-0: an anchor programme + evaluation contract (minimize)
    prog = await call_tool_http(loop0, "create_programme", {
        "goal": "orchestration battery anchor",
        "constraints": {},
        "allowed_variables": ["lr"],
        "budget": {"max_trials": 2},
        "metric_direction": "minimize",
    })
    assert "error" not in prog, prog
    contract = await call_tool_http(
        loop0, "create_evaluation_contract", {
            "programme_id": prog["programme_id"],
            "metrics": {
                "primary_metric": "val_ppl",
                "direction": "minimize",
            },
            "promotion_policy": {"min_gain": 1.0},
        })
    assert "error" not in contract, contract
    contract_id = contract["contract_id"]

    # Loop-0: champion lineage — incumbent + descendant challenger
    champion = await _mk_candidate(loop0)
    d = await call_tool_http(loop0, "record_promotion_decision", {
        "candidate_id": champion, "verdict": "promote",
        "evidence_refs": ["trial-seed"],
        "rationale": "battery incumbent",
        "decided_by": "human:battery",
    })
    assert "error" not in d, d
    desc_parent = await _mk_candidate(loop0)
    desc_cand = await _mk_candidate(loop0)
    # roster them on zetesis: champion via refresh, challengers direct
    r = await call_tool_http(zet, "refresh_roster", {"dry_run": False})
    assert "error" not in r, r

    # Arete: improver lineage + contract + proposal + tournament
    parent = await _mk_improver(arete)
    child = await _mk_improver(arete, parent_id=parent)
    mc = await call_tool_http(arete, "create_meta_contract", {
        "metrics": {"primary_metric": "hits", "direction": "max"},
        "promotion_policy": {"min_gain": 1.0},
    })
    assert "error" not in mc, mc
    p = await call_tool_http(arete, "propose_meta_change", {
        "proposer_improver_id": child,
        "spec_delta": {"change": "planner", "to": "beam"},
        "class_map": {"planner": "modifiable"},
        "expected_benefit": "better descendants",
        "falsification": "gain <= 1",
        "rollback_plan": "restore",
    })
    assert "error" not in p, p
    tourn = await call_tool_http(arete, "open_tournament", {
        "contract_id": mc["contract_id"],
        "parent_improver_id": parent,
        "candidate_improver_id": child,
        "budget": {"programmes_per_arm": 1, "trials_per_programme": 2},
        "seeds": [1],
    })
    assert "error" not in tourn, tourn

    return {
        "arete": arete, "zet": zet, "loop0": loop0,
        "tournament_id": tourn["tournament_id"],
        "candidate_improver_id": child,
        "contract_id": contract_id,
        "champion": champion,
        "desc_parent": desc_parent,
        "desc_cand": desc_cand,
        "campaigns": {},
    }


class TestOrchestrationGate:
    """The plan's gate, in order — each step builds on the last."""

    async def test_01_open_arm_campaigns(self, battery):
        arete, zet = battery["arete"], battery["zet"]
        tourn = battery["tournament_id"]
        for arm in ("parent", "candidate"):
            challenger = (
                battery["desc_parent"] if arm == "parent"
                else battery["desc_cand"]
            )
            r = await call_tool_http(zet, "register_challenger", {
                "candidate_id": challenger,
            })
            assert "error" not in r, r
            r = await call_tool_http(arete, "open_arm_campaign", {
                "tournament_id": tourn, "arm": arm,
                "upstream_contract_id": battery["contract_id"],
                "challenger_id": challenger,
            })
            assert "error" not in r, r
            battery["campaigns"][arm] = r["campaign_id"]
            # Upstream campaign exists and carries the tournament
            # budget verbatim
            c = await call_tool_http(
                zet, "get_campaign",
                {"campaign_id": r["campaign_id"]})
            assert "error" not in c, c
            assert c["campaign"]["budget"] == {
                "programmes_per_arm": 1, "trials_per_programme": 2,
            }
            # Retry is idempotent — same campaign, no duplicate open
            r2 = await call_tool_http(arete, "open_arm_campaign", {
                "tournament_id": tourn, "arm": arm,
                "upstream_contract_id": battery["contract_id"],
                "challenger_id": challenger,
            })
            assert r2["status"] == "already_linked"
            assert r2["campaign_id"] == r["campaign_id"]

    async def test_02_spawn_carries_direction_and_cap(
        self, battery,
    ):
        arete, zet, loop0 = (
            battery["arete"], battery["zet"], battery["loop0"]
        )
        tourn = battery["tournament_id"]
        for arm in ("parent", "candidate"):
            camp = battery["campaigns"][arm]
            challenger = (
                battery["desc_parent"] if arm == "parent"
                else battery["desc_cand"]
            )
            r = await call_tool_http(arete, "spawn_arm_programme", {
                "tournament_id": tourn, "arm": arm,
                "goal": f"{arm} descendant programme",
                "constraints": {"max_steps": 100},
                "allowed_variables": ["lr"],
            })
            assert "error" not in r, r
            spawn = r["spawn"]
            assert spawn["metric_direction"] == "minimize"
            assert spawn["candidate_version_id"] == challenger
            assert spawn["budget"]["max_trials"] == 2
            battery[f"prog_{arm}"] = spawn["programme_id"]

            # Upstream programme exists with the right attribution
            progs = await call_tool_http(loop0, "list_programmes", {
                "candidate_version_id": challenger,
            })
            ids = [p["programme_id"] for p in progs["programmes"]]
            assert spawn["programme_id"] in ids
            row = next(
                p for p in progs["programmes"]
                if p["programme_id"] == spawn["programme_id"]
            )
            assert row["metric_direction"] == "minimize"

            # programmes_per_arm=1 — a second challenger spawn fails
            r2 = await call_tool_http(arete, "spawn_arm_programme", {
                "tournament_id": tourn, "arm": arm,
                "goal": "over-cap spawn",
                "constraints": {}, "allowed_variables": ["lr"],
            })
            assert "error" in r2, r2
            assert "spawn cap" in r2["error"]

            # Champion side of the campaign has its own cap slot
            r3 = await call_tool_http(arete, "spawn_arm_programme", {
                "tournament_id": tourn, "arm": arm,
                "goal": f"{arm} champion baseline",
                "constraints": {}, "allowed_variables": ["lr"],
                "campaign_arm": "champion",
            })
            assert "error" not in r3, r3
            battery[f"prog_{arm}_champ"] = r3["spawn"]["programme_id"]

            # Spawn rows visible upstream
            c = await call_tool_http(
                zet, "get_campaign", {"campaign_id": camp})
            assert len(c["spawns"]) == 2

    async def test_03_pull_arm_evidence(self, battery):
        arete = battery["arete"]
        tourn = battery["tournament_id"]
        r = await call_tool_http(arete, "pull_arm_evidence", {
            "tournament_id": tourn, "arm": "candidate",
            "source": "loop0", "tool": "get_incumbent", "args": {},
        })
        assert "error" not in r, r
        assert r["evidence_ref_id"]
        assert r["upstream_evidence_ref_id"]
        battery["eref"] = r["upstream_evidence_ref_id"]

    async def test_04_results_spawn_scoped(self, battery):
        arete, loop0 = battery["arete"], battery["loop0"]
        tourn = battery["tournament_id"]

        # A programme created directly upstream — correctly attributed
        # but NEVER spawned under the campaign — is rejected.
        foreign = await call_tool_http(loop0, "create_programme", {
            "goal": "unspawned foreign programme",
            "constraints": {}, "allowed_variables": ["lr"],
            "budget": {"max_trials": 1},
            "candidate_version_id": battery["desc_cand"],
        })
        assert "error" not in foreign, foreign
        r = await call_tool_http(arete, "record_arm_result", {
            "tournament_id": tourn, "arm": "candidate",
            "programme_id": foreign["programme_id"],
            "metrics": {"val_ppl": 1.0},
        })
        assert "error" in r
        assert "not spawned" in r["error"]

        # Spawned programmes report fine — both campaign arms.
        for arm in ("parent", "candidate"):
            r = await call_tool_http(arete, "record_arm_result", {
                "tournament_id": tourn, "arm": arm,
                "programme_id": battery[f"prog_{arm}"],
                "metrics": {"val_ppl": 4.0 if arm == "parent" else 2.0},
            })
            assert "error" not in r, r
            r = await call_tool_http(arete, "record_arm_result", {
                "tournament_id": tourn, "arm": arm,
                "programme_id": battery[f"prog_{arm}_champ"],
                "metrics": {"val_ppl": 5.0},
                "campaign_arm": "champion",
            })
            assert "error" not in r, r

    async def test_05_close_and_verdict(self, battery):
        arete, zet = battery["arete"], battery["zet"]
        tourn = battery["tournament_id"]
        for arm in ("parent", "candidate"):
            r = await call_tool_http(arete, "close_arm_campaign", {
                "tournament_id": tourn, "arm": arm,
            })
            assert "error" not in r, r
            assert r["result"]["status"] == "closed"
            score = r["result"]["promotion_score"]
            # challenger 4.0|2.0 vs champion 5.0 → 0.8 | 0.4
            expected = 0.8 if arm == "parent" else 0.4
            assert abs(score - expected) < 1e-6

        # Verdict on the candidate arm — needs a campaign-scoped eref
        r = await call_tool_http(arete, "record_arm_verdict", {
            "tournament_id": tourn, "arm": "candidate",
            "verdict": "retain", "decided_by": "arete:tournament",
            "evidence_ref_ids": [battery["eref"]],
            "rationale": "descendant did not beat the incumbent",
        })
        assert "error" not in r, r
        assert r["result"]["decision_id"]

    async def test_06_tournament_result_and_close(self, battery):
        arete = battery["arete"]
        tourn = battery["tournament_id"]
        for arm, hits in (("parent", 3.0), ("candidate", 4.0)):
            r = await call_tool_http(arete, "record_tournament_result", {
                "tournament_id": tourn, "arm": arm,
                "descendant_spec": {
                    "campaign_id": battery["campaigns"][arm],
                    "programme_id": battery[f"prog_{arm}"],
                },
                "metrics": {"hits": hits, "seed": 1},
            })
            assert "error" not in r, r
        r = await call_tool_http(arete, "close_tournament", {
            "tournament_id": tourn,
        })
        assert "error" not in r, r
        # direction=max: candidate E[best]=4 / parent=3 → 4/3
        assert abs(r["recursive_gain"] - 4.0 / 3.0) < 1e-6

    async def test_07_invariants_clean(self, battery):
        arete, zet = battery["arete"], battery["zet"]
        tourn = battery["tournament_id"]

        # F-17: the closed-but-undecided tournament is decision debt —
        # a named check now, so invariants report it before discharge.
        r = await call_tool_http(arete, "check_invariants", {})
        assert r["status"] == "violations", r
        chk = next(
            c for c in r["checks"] if c["name"] == "decision_debt"
        )
        assert chk["ok"] is False
        assert any(
            v["tournament_id"] == tourn for v in chk["violations"]
        )

        # Discharge through the live gates: pull evidence on the
        # closed tournament (legal by design, and reachable again —
        # this is the step that was deadlocked), then decide.
        pull = await call_tool_http(arete, "pull_evidence", {
            "context_type": "tournament", "context_id": tourn,
            "source": "loop0", "tool": "list_trials",
            "args": {"programme_id": battery["prog_candidate"]},
        })
        assert "error" not in pull, pull
        d = await call_tool_http(arete, "record_meta_decision", {
            "candidate_improver_id": battery["candidate_improver_id"],
            "verdict": "hold",
            "evidence_refs": [pull["evidence_ref_id"]],
            "rationale": "candidate did not beat the incumbent — "
            "hold is a decision",
            "decided_by": "human:battery",
            "tournament_id": tourn,
        })
        assert "error" not in d, d

        # Re-run refreshes the logged violations view — clean now.
        r = await call_tool_http(arete, "check_invariants", {})
        assert r["status"] == "ok", r
        r = await call_tool_http(zet, "check_invariants", {})
        assert r["status"] == "ok", r

    async def test_08_health_reports_orchestration(
        self, battery, arete_orchestration_server
    ):
        import httpx
        arete_url = arete_orchestration_server[0]
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{arete_url}/health")
        assert r.status_code == 200
        assert "loop1_orchestration" in r.text
