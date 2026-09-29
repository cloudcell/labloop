"""Zero-divisor refusal on close_campaign (e-plan 20260929-1514Z W5).

Gleser–Hwang (1987): no always-bounded score on a ratio whose
denominator can be zero has coverage — so `else 0.0` was not a
fallback, it was a fabricated score (camp-532d7b60 froze score 0 on
a challenger win). close_campaign now refuses like arete's
close_tournament; abandon_campaign is the honest terminal record.
"""

from __future__ import annotations

import pytest

from .conftest import call_tool
from ml_zetesis_mcp.state.models import (
    CampaignArm,
    CampaignResult,
    CampaignStatus,
    PromotionCampaign,
)


def _campaign(cid: str) -> PromotionCampaign:
    return PromotionCampaign(
        id=cid,
        contract_id="contract-c1",
        champion_id="cand-alpha",
        challenger_id="cand-beta",
        primary_metric="hits",
        budget={},
    )


def _result(rid: str, cid: str, arm: CampaignArm, hits: float):
    return CampaignResult(
        id=rid, campaign_id=cid, arm=arm,
        programme_id=f"prog-{rid}", metrics={"hits": hits},
    )


@pytest.mark.anyio
class TestZeroDivisorRefusal:
    async def test_zero_champion_mean_refuses(
        self, zetesis_server, search_store
    ):
        """Challenger wins on a zero-scoring champion — the ratio is
        undefined (Gleser–Hwang), never written as 0.0."""
        search_store.create_campaign(_campaign("camp-zero"))
        search_store.create_campaign_result(
            _result("r-c", "camp-zero", CampaignArm.champion, 0.0)
        )
        search_store.create_campaign_result(
            _result("r-x", "camp-zero", CampaignArm.challenger, 5.0)
        )
        r = await call_tool(zetesis_server, "close_campaign", {
            "campaign_id": "camp-zero",
        })
        assert "error" in r
        assert "undefined" in r["error"]
        assert "abandon_campaign" in r["error"]

    async def test_refusal_writes_no_fabricated_score(
        self, zetesis_server, search_store
    ):
        """The campaign stays open — no closed row, no score. The
        observed means ride the error payload for the record."""
        search_store.create_campaign(_campaign("camp-zero2"))
        search_store.create_campaign_result(
            _result("r-c", "camp-zero2", CampaignArm.champion, 0.0)
        )
        search_store.create_campaign_result(
            _result("r-x", "camp-zero2", CampaignArm.challenger, 5.0)
        )
        r = await call_tool(zetesis_server, "close_campaign", {
            "campaign_id": "camp-zero2",
        })
        assert r["champion_mean"] == 0.0
        assert r["challenger_mean"] == 5.0
        got = search_store.get_campaign("camp-zero2")
        assert got.status == CampaignStatus.open
        assert got.promotion_score is None

    async def test_nonzero_divisor_still_closes(
        self, zetesis_server, search_store
    ):
        """Sanity: the refusal is scoped to the undefined case."""
        search_store.create_campaign(_campaign("camp-ok"))
        search_store.create_campaign_result(
            _result("r-c", "camp-ok", CampaignArm.champion, 4.0)
        )
        search_store.create_campaign_result(
            _result("r-x", "camp-ok", CampaignArm.challenger, 5.0)
        )
        r = await call_tool(zetesis_server, "close_campaign", {
            "campaign_id": "camp-ok",
        })
        assert r["status"] == "closed"
        assert r["promotion_score"] == 1.25
