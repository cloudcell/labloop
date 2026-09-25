"""Loop-2 orchestration unit tests — the six governed verbs.

In-process: real ImproverStore, a duck-typed fake for the
loop1_orchestration channel. What is under test is this server's own
enforcement — arm validation, budget carry, link idempotency — not
the upstream's behaviour (the HTTP battery covers real upstreams).
"""

from __future__ import annotations

import json

import pytest

from .conftest import (
    call_tool,
    make_contract,
    propose,
    register_imp,
)


class FakeOrchestrationAdaptor:
    """Duck-typed loop1_orchestration channel — records pushes and
    answers the campaign verbs with canned upstream payloads."""

    def __init__(self, payloads: dict | None = None):
        self.payloads = payloads or {}
        self.pushed: list[tuple[str, dict]] = []
        self._camp_n = 0

    async def push(self, tool: str, args: dict):
        self.pushed.append((tool, args))
        if tool == "open_campaign":
            self._camp_n += 1
            return json.dumps({
                "campaign_id": f"camp-up{self._camp_n}",
                "status": "open",
            })
        if tool == "pull_campaign_evidence":
            return json.dumps({
                "evidence_ref_id": "eref-up1",
                "ref_ids": ["camp-up1"],
                "result": {"ok": True},
            })
        if tool == "close_campaign":
            return json.dumps({
                "campaign_id": args["campaign_id"],
                "status": "closed",
                "promotion_score": 1.25,
            })
        if tool == "record_promotion_verdict":
            return json.dumps({
                "decision_id": "decision-up1",
                "claim_id": "claim-up1",
                "claim_status": "minted",
            })
        return self.payloads.get(
            tool, json.dumps({"ok": True, "tool": tool})
        )


@pytest.fixture
def orchestration(adaptors):
    """Wire the fake orchestration channel onto the adaptors fixture."""
    adaptors.loop1_orchestration = FakeOrchestrationAdaptor()
    return adaptors.loop1_orchestration


BUDGET = {"programmes_per_arm": 2, "trials_per_programme": 3}


async def _open_tournament(mcp, budget=None):
    """Genesis + child improver, contract, proposal, tournament."""
    parent = await register_imp(mcp)
    child = await register_imp(mcp, parent_id=parent)
    contract = await make_contract(mcp)
    p = await propose(mcp, child)
    assert "error" not in p, p
    r = await call_tool(mcp, "open_tournament", {
        "contract_id": contract,
        "parent_improver_id": parent,
        "candidate_improver_id": child,
        "budget": budget or BUDGET,
        "seeds": [1, 2],
    })
    assert "error" not in r, r
    return r["tournament_id"]


class TestOpenArmCampaign:
    async def test_opens_and_links(
        self, arete_server, orchestration, improver_store
    ):
        tourn = await _open_tournament(arete_server)
        r = await call_tool(arete_server, "open_arm_campaign", {
            "tournament_id": tourn, "arm": "candidate",
            "upstream_contract_id": "contract-up1",
            "challenger_id": "cand-desc1",
        })
        assert "error" not in r, r
        assert r["campaign_id"] == "camp-up1"
        # Budget carried verbatim from the tournament
        tool, args = orchestration.pushed[-1]
        assert tool == "open_campaign"
        assert args["budget"] == BUDGET
        assert args["contract_id"] == "contract-up1"
        # Link row recorded
        link = improver_store.get_tournament_campaign(tourn, "candidate")
        assert link is not None
        assert link.campaign_id == "camp-up1"

    async def test_retry_is_idempotent(
        self, arete_server, orchestration
    ):
        tourn = await _open_tournament(arete_server)
        r1 = await call_tool(arete_server, "open_arm_campaign", {
            "tournament_id": tourn, "arm": "parent",
            "upstream_contract_id": "c1", "challenger_id": "cand-1",
        })
        r2 = await call_tool(arete_server, "open_arm_campaign", {
            "tournament_id": tourn, "arm": "parent",
            "upstream_contract_id": "c1", "challenger_id": "cand-1",
        })
        assert r2["status"] == "already_linked"
        assert r2["campaign_id"] == r1["campaign_id"]
        # Only one upstream open happened
        opens = [t for t, _ in orchestration.pushed if t == "open_campaign"]
        assert len(opens) == 1

    async def test_missing_budget_keys_rejected(
        self, arete_server, orchestration
    ):
        tourn = await _open_tournament(
            arete_server, budget={"max_trials": 5}
        )
        r = await call_tool(arete_server, "open_arm_campaign", {
            "tournament_id": tourn, "arm": "candidate",
            "upstream_contract_id": "c1", "challenger_id": "cand-1",
        })
        assert "error" in r
        assert "programmes_per_arm" in r["error"]
        assert not orchestration.pushed

    async def test_closed_tournament_rejected(
        self, arete_server, orchestration
    ):
        r = await call_tool(arete_server, "open_arm_campaign", {
            "tournament_id": "tourn-nope", "arm": "candidate",
            "upstream_contract_id": "c1", "challenger_id": "cand-1",
        })
        assert "error" in r
        assert "not found" in r["error"].lower()

    async def test_bad_arm_rejected(self, arete_server, orchestration):
        tourn = await _open_tournament(arete_server)
        r = await call_tool(arete_server, "open_arm_campaign", {
            "tournament_id": tourn, "arm": "sideways",
            "upstream_contract_id": "c1", "challenger_id": "cand-1",
        })
        assert "error" in r and "arm" in r["error"]

    async def test_unwired_channel_clear_error(self, arete_server):
        """No orchestration adaptor → a clear error, not a crash."""
        tourn = await _open_tournament(arete_server)
        r = await call_tool(arete_server, "open_arm_campaign", {
            "tournament_id": tourn, "arm": "candidate",
            "upstream_contract_id": "c1", "challenger_id": "cand-1",
        })
        assert "error" in r
        assert "loop1_orchestration" in r["error"]


class TestDownstreamVerbs:
    async def _linked(self, mcp, arm="candidate"):
        tourn = await _open_tournament(mcp)
        await call_tool(mcp, "open_arm_campaign", {
            "tournament_id": tourn, "arm": arm,
            "upstream_contract_id": "c1", "challenger_id": "cand-1",
        })
        return tourn

    async def test_spawn_routes_through_link(
        self, arete_server, orchestration
    ):
        tourn = await self._linked(arete_server)
        r = await call_tool(arete_server, "spawn_arm_programme", {
            "tournament_id": tourn, "arm": "candidate",
            "goal": "improve the thing",
            "constraints": {}, "allowed_variables": ["lr"],
        })
        assert "error" not in r, r
        tool, args = orchestration.pushed[-1]
        assert tool == "spawn_campaign_programme"
        assert args["campaign_id"] == "camp-up1"
        assert args["arm"] == "challenger"

    async def test_spawn_requires_link(
        self, arete_server, orchestration
    ):
        tourn = await _open_tournament(arete_server)
        r = await call_tool(arete_server, "spawn_arm_programme", {
            "tournament_id": tourn, "arm": "parent",
            "goal": "g", "constraints": {}, "allowed_variables": ["lr"],
        })
        assert "error" in r and "open_arm_campaign" in r["error"]

    async def test_pull_arm_evidence_logs_local_ref(
        self, arete_server, orchestration, improver_store
    ):
        tourn = await self._linked(arete_server)
        r = await call_tool(arete_server, "pull_arm_evidence", {
            "tournament_id": tourn, "arm": "candidate",
            "source": "loop0", "tool": "list_trials",
            "args": {"programme_id": "prog-x"},
        })
        assert "error" not in r, r
        assert r["upstream_evidence_ref_id"] == "eref-up1"
        # Local tournament-scoped eref recorded
        refs = improver_store.list_evidence_refs("tournament", tourn)
        assert len(refs) == 1
        assert refs[0].tool == "pull_campaign_evidence"
        assert "eref-up1" in refs[0].ref_ids

    async def test_record_result_and_close(
        self, arete_server, orchestration
    ):
        tourn = await self._linked(arete_server)
        r = await call_tool(arete_server, "record_arm_result", {
            "tournament_id": tourn, "arm": "candidate",
            "programme_id": "prog-1",
            "metrics": {"hits": 3.0},
        })
        assert "error" not in r, r
        tool, args = orchestration.pushed[-1]
        assert tool == "record_campaign_result"
        assert args["arm"] == "challenger"

        r = await call_tool(arete_server, "close_arm_campaign", {
            "tournament_id": tourn, "arm": "candidate",
        })
        assert "error" not in r, r
        assert r["result"]["promotion_score"] == 1.25

    async def test_record_arm_verdict(
        self, arete_server, orchestration
    ):
        tourn = await self._linked(arete_server)
        r = await call_tool(arete_server, "record_arm_verdict", {
            "tournament_id": tourn, "arm": "candidate",
            "verdict": "promote", "decided_by": "arete:tourn",
            "evidence_ref_ids": ["eref-up1"],
        })
        assert "error" not in r, r
        tool, args = orchestration.pushed[-1]
        assert tool == "record_promotion_verdict"
        assert args["campaign_id"] == "camp-up1"
        assert r["result"]["decision_id"] == "decision-up1"


class TestOrchestrationWhitelist:
    def test_whitelist_membership(self):
        from ml_arete_mcp.enforcement.checks import (
            LOOP1_ORCHESTRATION_TOOLS,
            check_orchestration_tool_whitelisted,
        )
        for t in (
            "open_campaign", "spawn_campaign_programme",
            "pull_campaign_evidence", "record_campaign_result",
            "close_campaign", "record_promotion_verdict",
        ):
            assert check_orchestration_tool_whitelisted(t) is None
        # run_trial and other non-campaign verbs are rejected
        assert check_orchestration_tool_whitelisted(
            "run_trial") is not None
        assert check_orchestration_tool_whitelisted(
            "register_challenger") is not None
        assert "run_trial" not in LOOP1_ORCHESTRATION_TOOLS

    def test_loop1_read_channel_unchanged(self):
        """The read whitelist did not grow write verbs."""
        from ml_arete_mcp.enforcement.checks import (
            EVIDENCE_READ_TOOLS,
        )
        loop1_reads = EVIDENCE_READ_TOOLS["loop1"]
        for write_verb in (
            "open_campaign", "spawn_campaign_programme",
            "record_campaign_result", "close_campaign",
            "record_promotion_verdict", "run_trial",
        ):
            assert write_verb not in loop1_reads


class TestTournamentResultCorrection:
    """The seed-shape incident class: aggregate rows with dict/list
    'seed' must be refused at write time, and rows written before the
    rule existed must be repairable via a recorded correction — never
    a silent rewrite, never a DB hand-edit."""

    async def test_record_rejects_dict_seed(self, arete_server):
        tourn = await _open_tournament(arete_server)
        r = await call_tool(arete_server, "record_tournament_result", {
            "tournament_id": tourn, "arm": "parent",
            "descendant_spec": {"desc": "d"},
            "metrics": {"hits": 38.7, "seed": {"7": 38.7, "8": 39.0}},
        })
        assert "error" in r
        assert "seed" in r["error"] and "scalar" in r["error"]

    async def test_record_rejects_list_seed(self, arete_server):
        tourn = await _open_tournament(arete_server)
        r = await call_tool(arete_server, "record_tournament_result", {
            "tournament_id": tourn, "arm": "parent",
            "descendant_spec": {"desc": "d"},
            "metrics": {"hits": 38.7, "seed": [38.7, 39.0]},
        })
        assert "error" in r and "scalar" in r["error"]

    async def test_correct_rewrites_and_audits(
        self, arete_server, improver_store
    ):
        tourn = await _open_tournament(arete_server)
        r = await call_tool(arete_server, "record_tournament_result", {
            "tournament_id": tourn, "arm": "parent",
            "descendant_spec": {"desc": "d"},
            "metrics": {"hits": 10, "seed": 7},
        })
        rid = r["result_id"]

        # Correction requires a reason and a replacement.
        r = await call_tool(arete_server, "correct_tournament_result", {
            "result_id": rid, "reason": "",
        })
        assert "error" in r and "reason" in r["error"]
        r = await call_tool(arete_server, "correct_tournament_result", {
            "result_id": rid, "reason": "reshape",
        })
        assert "error" in r and "nothing to correct" in r["error"]

        # A non-scalar corrected seed is rejected the same way.
        r = await call_tool(arete_server, "correct_tournament_result", {
            "result_id": rid, "reason": "reshape",
            "metrics": {"hits": 10, "seed": {"7": 10}},
        })
        assert "error" in r and "scalar" in r["error"]

        r = await call_tool(arete_server, "correct_tournament_result", {
            "result_id": rid, "reason": "normalize seed label",
            "metrics": {"hits": 10, "seed": "s7"},
        })
        assert "error" not in r, r
        assert r["corrections_count"] == 1

        row = improver_store._fetchone(
            "SELECT * FROM tournament_results WHERE id = ?", (rid,)
        )
        assert json.loads(row["metrics_json"])["seed"] == "s7"
        corrections = json.loads(row["corrections_json"])
        assert corrections[0]["metrics_from"]["seed"] == 7
        assert corrections[0]["reason"] == "normalize seed label"

    async def test_correct_unknown_and_sealed(
        self, arete_server, improver_store
    ):
        r = await call_tool(arete_server, "correct_tournament_result", {
            "result_id": "tres-ghost", "reason": "x",
            "metrics": {"hits": 1},
        })
        assert "error" in r and "not found" in r["error"].lower()

        # Sealed records stay sealed: close the tournament, then the
        # correction must refuse.
        tourn = await _open_tournament(arete_server)
        for arm in ("parent", "candidate"):
            await call_tool(arete_server, "record_tournament_result", {
                "tournament_id": tourn, "arm": arm,
                "descendant_spec": {"desc": "d"},
                "metrics": {"hits": 10, "seed": 7},
            })
        rid = improver_store.list_tournament_results(tourn)[0].id
        r = await call_tool(arete_server, "close_tournament", {
            "tournament_id": tourn,
        })
        assert "error" not in r, r
        r = await call_tool(arete_server, "correct_tournament_result", {
            "result_id": rid, "reason": "too late",
            "metrics": {"hits": 11, "seed": 7},
        })
        assert "error" in r and "sealed" in r["error"].lower()

    async def test_close_refuses_malformed_legacy_row(
        self, arete_server, improver_store
    ):
        """Rows predating the seed-shape rule (written directly, as on
        the affected VM) must fail close loudly, naming the row —
        not crash with unhashable-type."""
        from ml_arete_mcp.state.models import (
            TournamentArm, TournamentResult,
        )

        tourn = await _open_tournament(arete_server)
        improver_store.create_tournament_result(TournamentResult(
            id="tres-bad", tournament_id=tourn,
            arm=TournamentArm.parent,
            descendant_spec={"desc": "d"},
            metrics={"hits": 38.7, "seed": {"7": 38.7}},
        ))
        await call_tool(arete_server, "record_tournament_result", {
            "tournament_id": tourn, "arm": "candidate",
            "descendant_spec": {"desc": "d"},
            "metrics": {"hits": 10, "seed": 7},
        })
        r = await call_tool(arete_server, "close_tournament", {
            "tournament_id": tourn,
        })
        assert "error" in r
        assert "tres-bad" in r["error"] and "non-scalar" in r["error"]

        # The recorded correction unblocks the close.
        r = await call_tool(arete_server, "correct_tournament_result", {
            "result_id": "tres-bad", "reason": "split aggregate row",
            "metrics": {"hits": 38.7, "seed": 7},
        })
        assert "error" not in r, r
        r = await call_tool(arete_server, "close_tournament", {
            "tournament_id": tourn,
        })
        assert "error" not in r, r
        assert r["recursive_gain"] is not None
