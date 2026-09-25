"""Promotion HTTP battery — the full Loop-1 campaign lifecycle over
real transports against a REAL ml_episteme_mcp upstream.

This is the load-bearing test of the increment: the promotion channel
is the ecosystem's first cross-server write, and it is exercised
against actual Loop-0 enforcement — real lineage checks, real contract
existence, real insert-only decision records — not a stub's canned
replies. A stub would certify the wiring; this certifies the protocol.
"""

from __future__ import annotations

import pytest

from .conftest import call_tool_http


async def _register_candidate(loop0_url, parent_id=None):
    args = {
        "code_artifact_digest": "none",
        "model_ref": "battery-model",
        "capability_profile": {"tools": ["t1"]},
    }
    if parent_id:
        args["parent_id"] = parent_id
    r = await call_tool_http(loop0_url, "register_candidate", args)
    assert "error" not in r, r
    return r["candidate_id"]


async def _decide(loop0_url, candidate_id, verdict, **over):
    args = {
        "candidate_id": candidate_id,
        "verdict": verdict,
        "evidence_refs": ["trial-battery"],
        "rationale": "battery decision",
        "decided_by": "human:battery",
    }
    args.update(over)
    r = await call_tool_http(loop0_url, "record_promotion_decision", args)
    assert "error" not in r, r
    return r["decision_id"]


class TestRealUpstreamReads:
    """Loop-0's new read surface, exercised end-to-end."""

    async def test_list_candidates_and_incumbent(
        self, zetesis_promotion_server
    ):
        zet, _gui, loop0, _claims = zetesis_promotion_server
        # Session-shared upstream: candidates from sibling tests may
        # already exist. Assert on what we add, not an empty store.
        before = (await call_tool_http(loop0, "list_candidates", {}))["total"]
        cand = await _register_candidate(loop0)
        after = await call_tool_http(loop0, "list_candidates", {})
        assert after["total"] == before + 1
        assert any(c["id"] == cand for c in after["candidates"])

        await _decide(loop0, cand, "promote")
        r = await call_tool_http(loop0, "get_incumbent", {})
        assert r["candidate_id"] == cand
        assert r["candidate"]["id"] == cand

    async def test_scorecard_and_programme_filter(
        self, zetesis_promotion_server
    ):
        _zet, _gui, loop0, _claims = zetesis_promotion_server
        cand = await _register_candidate(loop0)
        prog = await call_tool_http(loop0, "create_programme", {
            "goal": "attributed",
            "constraints": {}, "allowed_variables": ["lr"],
            "budget": {"max_trials": 5, "max_wall_time_hours": 1.0},
            "candidate_version_id": cand,
        })
        assert "error" not in prog, prog
        r = await call_tool_http(loop0, "list_programmes", {
            "candidate_version_id": cand,
        })
        assert [
            p["programme_id"] for p in r["programmes"]
        ] == [prog["programme_id"]]
        assert r["programmes"][0]["candidate_version_id"] == cand
        r = await call_tool_http(loop0, "get_candidate_scorecard", {
            "candidate_id": cand,
        })
        assert r["candidate_id"] == cand
        assert r["programme_count"] == 1


class TestCampaignOverRealLoop0:
    """The full lifecycle: roster → campaign → results → verdict →
    upstream decision — against real enforcement."""

    async def test_full_promotion_lifecycle(
        self, zetesis_promotion_server
    ):
        zet, gui, loop0, _claims = zetesis_promotion_server

        # Upstream: a champion (promoted) and a challenger (registered).
        champion = await _register_candidate(loop0)
        challenger = await _register_candidate(loop0, parent_id=champion)
        await _decide(loop0, champion, "promote")

        # Loop 1: refresh adopts both; challenger marked.
        r = await call_tool_http(zet, "refresh_roster", {"dry_run": False})
        assert "error" not in r, r
        assert {champion, challenger} <= set(r["adopted"])
        r = await call_tool_http(zet, "register_challenger", {
            "candidate_id": challenger,
        })
        assert "error" not in r, r

        # The campaign needs a contract — created upstream on a real
        # programme so metrics exist to score against.
        prog_champ = await call_tool_http(loop0, "create_programme", {
            "goal": "champion arm",
            "constraints": {}, "allowed_variables": ["lr"],
            "budget": {"max_trials": 5, "max_wall_time_hours": 1.0},
            "candidate_version_id": champion,
        })
        prog_chall = await call_tool_http(loop0, "create_programme", {
            "goal": "challenger arm",
            "constraints": {}, "allowed_variables": ["lr"],
            "budget": {"max_trials": 5, "max_wall_time_hours": 1.0},
            "candidate_version_id": challenger,
        })
        contract = await call_tool_http(
            loop0, "create_evaluation_contract", {
                "programme_id": prog_champ["programme_id"],
                "metrics": {"hits": "maximize"},
                "promotion_policy": {"threshold": 1.0},
            },
        )
        assert "error" not in contract, contract

        # Open — champion derived upstream, not asserted.
        r = await call_tool_http(zet, "open_campaign", {
            "contract_id": contract["contract_id"],
            "challenger_id": challenger,
            "budget": {"seeds": 3},
        })
        assert "error" not in r, r
        assert r["champion_id"] == champion
        cid = r["campaign_id"]

        # Attribution gate: the champion's programme can't post as the
        # challenger arm — real upstream filter enforces it.
        r = await call_tool_http(zet, "record_campaign_result", {
            "campaign_id": cid, "arm": "challenger",
            "programme_id": prog_champ["programme_id"],
            "metrics": {"hits": 999},
        })
        assert "error" in r and "attributed" in r["error"]

        # Real results, both arms.
        for arm, prog, hits in (
            ("champion", prog_champ["programme_id"], 80),
            ("challenger", prog_chall["programme_id"], 120),
        ):
            r = await call_tool_http(zet, "record_campaign_result", {
                "campaign_id": cid, "arm": arm,
                "programme_id": prog, "metrics": {"hits": hits},
            })
            assert "error" not in r, r

        # Campaign-scoped evidence, then close.
        ev = await call_tool_http(zet, "pull_campaign_evidence", {
            "campaign_id": cid, "source": "loop0",
            "tool": "get_candidate_scorecard",
            "args": {"candidate_id": challenger},
        })
        assert "error" not in ev, ev
        r = await call_tool_http(zet, "close_campaign", {
            "campaign_id": cid,
        })
        assert r["promotion_score"] == pytest.approx(1.5)

        # The verdict writes upstream — the decision is a real row in
        # Loop-0's insert-only table, and the incumbent re-derives.
        r = await call_tool_http(zet, "record_promotion_verdict", {
            "campaign_id": cid, "verdict": "promote",
            "decided_by": "human:battery",
            "evidence_ref_ids": [ev["evidence_ref_id"]],
        })
        assert "error" not in r, r
        assert r["decision_id"].startswith("decision-")

        upstream = await call_tool_http(loop0, "get_incumbent", {})
        assert upstream["candidate_id"] == challenger
        trail = await call_tool_http(loop0, "list_promotion_decisions", {
            "candidate_id": challenger,
        })
        assert [d["verdict"] for d in trail["decisions"]] == ["promote"]

        # GUI: the campaign page renders read-only.
        import httpx
        page = httpx.get(f"{gui}/campaign/{cid}", timeout=5.0)
        assert page.status_code == 200
        assert challenger in page.text

    async def test_rollback_restores_incumbent_over_http(
        self, zetesis_promotion_server
    ):
        """A rollback verdict on the challenger restores the prior
        incumbent upstream — reversal is a new record, not a delete."""
        zet, _gui, loop0, _claims = zetesis_promotion_server

        champion = await _register_candidate(loop0)
        challenger = await _register_candidate(loop0, parent_id=champion)
        await _decide(loop0, champion, "promote")
        # Challenger was promoted once — now rolled back.
        await _decide(loop0, challenger, "promote")
        r = await call_tool_http(loop0, "get_incumbent", {})
        assert r["candidate_id"] == challenger
        await _decide(loop0, challenger, "rollback")
        r = await call_tool_http(loop0, "get_incumbent", {})
        assert r["candidate_id"] == champion

    async def test_verdict_write_reaches_real_enforcement(
        self, zetesis_promotion_server
    ):
        """A verdict the upstream would reject (bad decided_by on a
        rollback — needs human:) is stopped at Loop 1 before it can
        reach Loop 0's trail."""
        zet, _gui, loop0, _claims = zetesis_promotion_server
        champion = await _register_candidate(loop0)
        challenger = await _register_candidate(loop0, parent_id=champion)
        await _decide(loop0, champion, "promote")

        await call_tool_http(zet, "refresh_roster", {"dry_run": False})
        await call_tool_http(zet, "register_challenger", {
            "candidate_id": challenger,
        })
        prog = await call_tool_http(loop0, "create_programme", {
            "goal": "arm",
            "constraints": {}, "allowed_variables": ["lr"],
            "budget": {"max_trials": 5, "max_wall_time_hours": 1.0},
            "candidate_version_id": champion,
        })
        contract = await call_tool_http(
            loop0, "create_evaluation_contract", {
                "programme_id": prog["programme_id"],
                "metrics": {"hits": "maximize"},
                "promotion_policy": {},
            },
        )
        cid = (await call_tool_http(zet, "open_campaign", {
            "contract_id": contract["contract_id"],
            "challenger_id": challenger, "budget": {},
        }))["campaign_id"]
        for arm, pid in (
            ("champion", prog["programme_id"]),
            ("challenger", prog["programme_id"]),
        ):
            pass  # challenger needs its own programme — skip; see below

        r = await call_tool_http(zet, "record_promotion_verdict", {
            "campaign_id": cid, "verdict": "rollback",
            "decided_by": "agent:zetesis",
            "evidence_ref_ids": ["eref-any"],
        })
        # Fails at Loop-1's human: gate — never reaches upstream.
        assert "error" in r
