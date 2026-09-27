"""rc-7 regressions — zetesis.

Q3  unrunnable_campaigns flags open campaigns whose budget cannot
    orchestrate (missing programmes_per_arm/trials_per_programme)
    with no spawns and no results; close_campaign names
    abandon_campaign for the structural wedge.
Q5  abandon_campaign stamps abandoned_at, never closed_at; the
    migration backfills pre-abandoned_at rows.
Q6  read_resource reaches search:// resources and errors clearly on
    unknown URIs.
Q10 pull_evidence/pull_campaign_evidence tool enums equal the union
    of the per-source whitelists.
"""

from __future__ import annotations

import json

import pytest

from .conftest import call_tool
from ml_zetesis_mcp.integrity.checks import run_checks
from ml_zetesis_mcp.state.models import (
    CampaignArm,
    CampaignResult,
    CampaignSpawn,
    PromotionCampaign,
)


def _campaign(cid: str, budget=None, **kw) -> PromotionCampaign:
    return PromotionCampaign(
        id=cid,
        contract_id="contract-c1",
        champion_id="cand-alpha",
        challenger_id="cand-beta",
        primary_metric="hits",
        budget=budget or {},
        **kw,
    )


def _unrunnable(payload: dict) -> dict:
    return next(c for c in payload["checks"]
                if c["name"] == "unrunnable_campaigns")


class TestUnrunnableCampaigns:
    """rc-7 Q3 — structural wedges must be visible to the audit."""

    async def test_wedged_campaign_flagged(self, search_store):
        search_store.create_campaign(_campaign(
            "camp-wedge", budget={"seeds": 3},
        ))
        payload = await run_checks(search_store, connectivity=None)
        c = _unrunnable(payload)
        assert not c["ok"]
        v = c["violations"][0]
        assert v["campaign_id"] == "camp-wedge"
        assert "programmes_per_arm" in v["reason"]
        assert v["exit"] == "abandon_campaign"

    async def test_budget_free_campaign_not_flagged(self, search_store):
        """Caller-driven campaigns carry no budget — not a wedge."""
        search_store.create_campaign(_campaign("camp-free", budget={}))
        payload = await run_checks(search_store, connectivity=None)
        assert _unrunnable(payload)["ok"]

    async def test_carryable_budget_not_flagged(self, search_store):
        search_store.create_campaign(_campaign(
            "camp-ok",
            budget={"programmes_per_arm": 2, "trials_per_programme": 3},
        ))
        payload = await run_checks(search_store, connectivity=None)
        assert _unrunnable(payload)["ok"]

    async def test_campaign_with_spawn_not_flagged(self, search_store):
        search_store.create_campaign(_campaign(
            "camp-spawned", budget={"seeds": 3},
        ))
        search_store.create_campaign_spawn(CampaignSpawn(
            id="spawn-1", campaign_id="camp-spawned",
            arm=CampaignArm.champion, programme_id="prog-champ",
        ))
        payload = await run_checks(search_store, connectivity=None)
        assert _unrunnable(payload)["ok"]

    async def test_campaign_with_result_not_flagged(self, search_store):
        search_store.create_campaign(_campaign(
            "camp-res", budget={"seeds": 3},
        ))
        search_store.create_campaign_result(CampaignResult(
            id="res-1", campaign_id="camp-res",
            arm=CampaignArm.champion, programme_id="prog-champ",
            metrics={"hits": 10},
        ))
        payload = await run_checks(search_store, connectivity=None)
        assert _unrunnable(payload)["ok"]

    async def test_close_names_abandon_for_wedge(
        self, zetesis_server, search_store
    ):
        """A structurally wedged campaign's close refusal must name
        the real exit — 'both arms must run' alone misdiagnoses it."""
        search_store.create_campaign(_campaign(
            "camp-wedge", budget={"seeds": 3},
        ))
        r = await call_tool(zetesis_server, "close_campaign", {
            "campaign_id": "camp-wedge",
        })
        assert "error" in r
        assert "abandon_campaign" in r["error"]

    async def test_close_ordinary_campaign_message_unchanged(
        self, zetesis_server, search_store
    ):
        """A runnable campaign with no results keeps the plain
        'both arms must run' message — no false abandon hint."""
        search_store.create_campaign(_campaign(
            "camp-pending",
            budget={"programmes_per_arm": 2, "trials_per_programme": 1},
        ))
        r = await call_tool(zetesis_server, "close_campaign", {
            "campaign_id": "camp-pending",
        })
        assert "error" in r
        assert "both" in r["error"]
        assert "abandon_campaign" not in r["error"]


class TestAbandonedAt:
    """rc-7 Q5 — abandoned is a terminal state, not a close."""

    async def test_abandon_sets_abandoned_at_not_closed_at(
        self, zetesis_server, adaptors, search_store
    ):
        adaptors.evidence.calls = []
        # Open a campaign through the store — tool path needs a live
        # upstream; the column semantics are what matter here.
        search_store.create_campaign(_campaign("camp-ab"))
        r = await call_tool(zetesis_server, "abandon_campaign", {
            "campaign_id": "camp-ab",
            "rationale": "wedged",
            "decided_by": "human:op",
        })
        assert r["status"] == "abandoned"
        assert r["abandoned_at"] is not None
        c = search_store.get_campaign("camp-ab")
        assert c.abandoned_at is not None
        assert c.closed_at is None

    async def test_get_campaign_surfaces_abandoned_at(
        self, zetesis_server, search_store
    ):
        search_store.create_campaign(_campaign("camp-ab2"))
        await call_tool(zetesis_server, "abandon_campaign", {
            "campaign_id": "camp-ab2",
            "rationale": "wedged",
            "decided_by": "human:op",
        })
        r = await call_tool(zetesis_server, "get_campaign", {
            "campaign_id": "camp-ab2",
        })
        assert r["campaign"]["abandoned_at"] is not None
        assert r["campaign"]["closed_at"] is None

    def test_migration_backfills_pre_column_rows(self, tmp_path):
        """A row abandoned before abandoned_at existed carried the
        instant in closed_at — reconnect moves it, not destroys it."""
        from ml_zetesis_mcp.state.store import SearchStore

        db = tmp_path / "search.db"
        store = SearchStore(str(db))
        store.connect()
        store.create_campaign(_campaign("camp-old"))
        # Simulate the pre-rc-7 write: abandoned with closed_at set.
        store._execute(
            "UPDATE promotion_campaigns SET status='abandoned', "
            "closed_at='2026-09-27T00:00:00+00:00' WHERE id='camp-old'"
        )
        store.conn.commit()
        store.close()

        store2 = SearchStore(str(db))
        store2.connect()
        c = store2.get_campaign("camp-old")
        assert c.abandoned_at == "2026-09-27T00:00:00+00:00"
        assert c.closed_at is None
        store2.close()


class TestReadResource:
    """rc-7 Q6 — the tool surface can read resources."""

    async def test_reads_search_status(self, zetesis_server):
        r = await call_tool(zetesis_server, "read_resource", {
            "uri": "search://status",
        })
        assert "error" not in r, r
        body = json.loads(r["contents"][0]["content"])
        assert body["server"] == "ml-zetesis-mcp"

    async def test_unknown_uri_errors(self, zetesis_server):
        r = await call_tool(zetesis_server, "read_resource", {
            "uri": "search://nonexistent",
        })
        assert "error" in r

    async def test_foreign_scheme_errors(self, zetesis_server):
        r = await call_tool(zetesis_server, "read_resource", {
            "uri": "improver://status",
        })
        assert "error" in r


class TestPullEvidenceEnums:
    """rc-7 Q10 — the advertised tool enum equals the whitelist
    union; loop1's own tools are not advertised as upstream reads."""

    async def test_pull_evidence_enum_matches_whitelist(
        self, zetesis_server
    ):
        from ml_zetesis_mcp.enforcement.checks import (
            EVIDENCE_READ_TOOLS,
        )

        tools = await zetesis_server.list_tools()
        pull = next(t for t in tools if t.name == "pull_evidence")
        enum = set(pull.input_schema["properties"]["tool"]["enum"])
        assert enum == set().union(*EVIDENCE_READ_TOOLS.values())
        # Loop-1's own tools were over-advertised — unreachable
        # upstream, now out of the enum.
        assert "get_investigation" not in enum
        assert "list_campaigns" not in enum

    async def test_pull_campaign_evidence_enum_matches(
        self, zetesis_server
    ):
        from ml_zetesis_mcp.enforcement.checks import (
            EVIDENCE_READ_TOOLS,
        )

        tools = await zetesis_server.list_tools()
        pull = next(
            t for t in tools if t.name == "pull_campaign_evidence"
        )
        enum = set(pull.input_schema["properties"]["tool"]["enum"])
        assert enum == set().union(*EVIDENCE_READ_TOOLS.values())
