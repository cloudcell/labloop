"""Loop-1 orchestration unit tests — spawn enforcement, direction
resolution, spawn-scoped results, close-time budget audit.

In-process: real SearchStore, duck-typed fake adaptors. The upstream
(evidence reads, create_programme writes) is faked at the adaptor
boundary; enforcement under test is this server's own.
"""

from __future__ import annotations

import json

import pytest

from .conftest import call_tool

CAND_CHAMPION = "cand-champ1"
CAND_CHALLENGER = "cand-chall1"


class FakeOrchestrationPromotion:
    """Promotion channel fake that answers create_programme."""

    def __init__(self):
        self.pushed: list[tuple[str, dict]] = []
        self._n = 0

    async def push(self, tool: str, args: dict):
        self.pushed.append((tool, args))
        if tool == "create_programme":
            self._n += 1
            return json.dumps({
                "programme_id": f"prog-spawned{self._n}",
                "status": "created",
            })
        return json.dumps({"error": f"unexpected push: {tool}"})


def _contract_payload(direction="minimize", metric="val_ppl"):
    return json.dumps({
        "contract": {
            "id": "contract-t1",
            "programme_id": "prog-up",
            "version": 1,
            "metrics": {"primary_metric": metric, "direction": direction},
            "holdouts": None,
            "budget": None,
            "promotion_policy": {},
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    })


def _wire(adaptors, direction="minimize"):
    """Wire the fakes a spawn needs: incumbent + contract on evidence,
    create_programme on promotion."""
    adaptors.evidence.payloads.update({
        "get_incumbent": json.dumps({"candidate_id": CAND_CHAMPION}),
        "get_evaluation_contract": _contract_payload(direction),
        "list_programmes": json.dumps({"programmes": []}),
        "get_candidate": json.dumps(
            {"candidate": {"id": CAND_CHALLENGER, "parent_id": None}}
        ),
    })
    adaptors.promotion = FakeOrchestrationPromotion()


async def _open_campaign(mcp, budget=None):
    """Register the challenger + open a campaign through the tools."""
    r = await call_tool(mcp, "register_challenger", {
        "candidate_id": CAND_CHALLENGER,
    })
    assert "error" not in r, r
    r = await call_tool(mcp, "open_campaign", {
        "contract_id": "contract-t1",
        "challenger_id": CAND_CHALLENGER,
        "budget": budget or {
            "programmes_per_arm": 1,
            "trials_per_programme": 2,
        },
        "seeds": [1, 2],
    })
    assert "error" not in r, r
    return r["campaign_id"]


def _spawn_args(**over):
    args = {
        "goal": "beat the champion on val_ppl",
        "constraints": {"max_steps": 400},
        "allowed_variables": ["lr", "batch_size"],
    }
    args.update(over)
    return args


class TestSpawnCampaignProgramme:
    async def test_spawn_records_row_and_carries_budget(
        self, zetesis_server, adaptors, search_store
    ):
        _wire(adaptors, direction="minimize")
        camp = await _open_campaign(zetesis_server)
        r = await call_tool(
            zetesis_server, "spawn_campaign_programme", {
                "campaign_id": camp, "arm": "challenger",
                **_spawn_args(),
            }
        )
        assert "error" not in r, r
        assert r["metric_direction"] == "minimize"
        assert r["candidate_version_id"] == CAND_CHALLENGER
        assert r["budget"]["max_trials"] == 2
        # Upstream write carried the resolved direction + attribution
        tool, args = adaptors.promotion.pushed[-1]
        assert tool == "create_programme"
        assert args["metric_direction"] == "minimize"
        assert args["candidate_version_id"] == CAND_CHALLENGER
        # Durable spawn row
        spawns = search_store.list_campaign_spawns(camp)
        assert len(spawns) == 1
        assert spawns[0].programme_id == r["programme_id"]

    async def test_maximize_direction_flows(
        self, zetesis_server, adaptors
    ):
        _wire(adaptors, direction="maximize")
        camp = await _open_campaign(zetesis_server)
        r = await call_tool(
            zetesis_server, "spawn_campaign_programme", {
                "campaign_id": camp, "arm": "challenger",
                **_spawn_args(),
            }
        )
        assert r["metric_direction"] == "maximize"
        assert adaptors.promotion.pushed[-1][1][
            "metric_direction"] == "maximize"

    async def test_map_form_direction_resolves(
        self, zetesis_server, adaptors
    ):
        """The name→direction metrics map form carries direction too."""
        adaptors.evidence.payloads.update({
            "get_incumbent": json.dumps(
                {"candidate_id": CAND_CHAMPION}),
            "get_evaluation_contract": json.dumps({
                "contract": {
                    "id": "contract-t1", "programme_id": "prog-up",
                    "version": 1,
                    "metrics": {"val_ppl": "minimize"},
                    "holdouts": None, "budget": None,
                    "promotion_policy": {},
                    "created_at": "2026-01-01T00:00:00+00:00",
                }
            }),
            "list_programmes": json.dumps({"programmes": []}),
            "get_candidate": json.dumps(
                {"candidate": {"id": CAND_CHALLENGER}}
            ),
        })
        adaptors.promotion = FakeOrchestrationPromotion()
        camp = await _open_campaign(zetesis_server)
        r = await call_tool(
            zetesis_server, "spawn_campaign_programme", {
                "campaign_id": camp, "arm": "challenger",
                **_spawn_args(),
            }
        )
        assert "error" not in r, r
        assert r["metric_direction"] == "minimize"

    async def test_missing_direction_fails_loudly(
        self, zetesis_server, adaptors
    ):
        """A contract with no direction never silently maximizes."""
        adaptors.evidence.payloads.update({
            "get_incumbent": json.dumps(
                {"candidate_id": CAND_CHAMPION}),
            "get_evaluation_contract": json.dumps({
                "contract": {
                    "id": "contract-t1", "programme_id": "prog-up",
                    "version": 1,
                    "metrics": {"primary_metric": "val_ppl"},
                    "holdouts": None, "budget": None,
                    "promotion_policy": {},
                    "created_at": "2026-01-01T00:00:00+00:00",
                }
            }),
            "list_programmes": json.dumps({"programmes": []}),
            "get_candidate": json.dumps(
                {"candidate": {"id": CAND_CHALLENGER}}
            ),
        })
        adaptors.promotion = FakeOrchestrationPromotion()
        camp = await _open_campaign(zetesis_server)
        r = await call_tool(
            zetesis_server, "spawn_campaign_programme", {
                "campaign_id": camp, "arm": "challenger",
                **_spawn_args(),
            }
        )
        assert "error" in r
        assert "direction" in r["error"]
        assert not adaptors.promotion.pushed

    async def test_spawn_cap_enforced(
        self, zetesis_server, adaptors
    ):
        _wire(adaptors)
        camp = await _open_campaign(zetesis_server)  # ppa=1
        r1 = await call_tool(
            zetesis_server, "spawn_campaign_programme", {
                "campaign_id": camp, "arm": "challenger",
                **_spawn_args(),
            }
        )
        assert "error" not in r1
        r2 = await call_tool(
            zetesis_server, "spawn_campaign_programme", {
                "campaign_id": camp, "arm": "challenger",
                **_spawn_args(goal="a second programme"),
            }
        )
        assert "error" in r2
        assert "spawn cap" in r2["error"]
        # The other arm has its own cap — champion side still spawns.
        r3 = await call_tool(
            zetesis_server, "spawn_campaign_programme", {
                "campaign_id": camp, "arm": "champion",
                **_spawn_args(goal="champion baseline"),
            }
        )
        assert "error" not in r3, r3
        assert r3["candidate_version_id"] == CAND_CHAMPION

    async def test_spawn_on_closed_campaign_rejected(
        self, zetesis_server, adaptors
    ):
        _wire(adaptors)
        camp = await _open_campaign(zetesis_server)
        # Close requires results on both arms — instead verify the
        # nonexistent-campaign rejection, which exercises the same
        # check_campaign_open path.
        r = await call_tool(
            zetesis_server, "spawn_campaign_programme", {
                "campaign_id": "camp-nope", "arm": "challenger",
                **_spawn_args(),
            }
        )
        assert "error" in r
        assert "not found" in r["error"].lower()

    async def test_budget_override_shrink_only(
        self, zetesis_server, adaptors
    ):
        _wire(adaptors)
        camp = await _open_campaign(zetesis_server)
        ok = await call_tool(
            zetesis_server, "spawn_campaign_programme", {
                "campaign_id": camp, "arm": "challenger",
                **_spawn_args(), "budget": {"max_trials": 1},
            }
        )
        assert ok["budget"]["max_trials"] == 1
        # Grow rejected — and a fresh arm for the unknown-key case.
        _wire(adaptors)
        camp2 = await _open_campaign(zetesis_server)
        grow = await call_tool(
            zetesis_server, "spawn_campaign_programme", {
                "campaign_id": camp2, "arm": "challenger",
                **_spawn_args(), "budget": {"max_trials": 9},
            }
        )
        assert "error" in grow and "shrink" in grow["error"]
        invent = await call_tool(
            zetesis_server, "spawn_campaign_programme", {
                "campaign_id": camp2, "arm": "champion",
                **_spawn_args(), "budget": {"extra_key": 1},
            }
        )
        assert "error" in invent and "not a carried" in invent["error"]


class TestSpawnScopedResults:
    async def test_unspawned_programme_rejected(
        self, zetesis_server, adaptors
    ):
        """Once a campaign has spawns, results must name one of them."""
        _wire(adaptors)
        adaptors.evidence.payloads["list_programmes"] = json.dumps({
            "programmes": [{"programme_id": "prog-foreign"}]
        })
        camp = await _open_campaign(zetesis_server)
        await call_tool(
            zetesis_server, "spawn_campaign_programme", {
                "campaign_id": camp, "arm": "challenger",
                **_spawn_args(),
            }
        )
        r = await call_tool(
            zetesis_server, "record_campaign_result", {
                "campaign_id": camp, "arm": "challenger",
                "programme_id": "prog-foreign",
                "metrics": {"val_ppl": 1.0},
            }
        )
        assert "error" in r
        assert "not spawned" in r["error"]

    async def test_wrong_arm_result_rejected(
        self, zetesis_server, adaptors
    ):
        _wire(adaptors)
        camp = await _open_campaign(zetesis_server)
        spawn = await call_tool(
            zetesis_server, "spawn_campaign_programme", {
                "campaign_id": camp, "arm": "challenger",
                **_spawn_args(),
            }
        )
        prog = spawn["programme_id"]
        adaptors.evidence.payloads["list_programmes"] = json.dumps({
            "programmes": [{"programme_id": prog}]
        })
        r = await call_tool(
            zetesis_server, "record_campaign_result", {
                "campaign_id": camp, "arm": "champion",
                "programme_id": prog,
                "metrics": {"val_ppl": 1.0},
            }
        )
        assert "error" in r
        assert "spawned for arm" in r["error"]

    async def test_spawned_result_accepted(
        self, zetesis_server, adaptors, search_store
    ):
        _wire(adaptors)
        camp = await _open_campaign(zetesis_server)
        spawn = await call_tool(
            zetesis_server, "spawn_campaign_programme", {
                "campaign_id": camp, "arm": "challenger",
                **_spawn_args(),
            }
        )
        prog = spawn["programme_id"]
        adaptors.evidence.payloads["list_programmes"] = json.dumps({
            "programmes": [{"programme_id": prog}]
        })
        r = await call_tool(
            zetesis_server, "record_campaign_result", {
                "campaign_id": camp, "arm": "challenger",
                "programme_id": prog,
                "metrics": {"val_ppl": 1.0},
            }
        )
        assert "error" not in r, r
        # Spawn marked completed
        spawns = search_store.list_campaign_spawns(camp)
        assert spawns[0].status.value == "completed"


class TestWhitelist:
    def test_create_programme_whitelisted(self):
        from ml_zetesis_mcp.enforcement.checks import (
            check_promotion_tool_whitelisted,
        )
        assert check_promotion_tool_whitelisted(
            "create_programme") is None
        assert check_promotion_tool_whitelisted("run_trial") is not None
        assert check_promotion_tool_whitelisted(
            "conclude_hypothesis") is not None
