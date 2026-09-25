"""GUI tests — the observability views are read-only projections."""

from __future__ import annotations

import httpx


async def test_gui_serves_readonly_views(improver_store):
    from ml_arete_mcp.observability.server import (
        create_observability_app,
    )
    from ml_arete_mcp.state.models import ImproverVersion

    improver_store.create_improver(ImproverVersion(
        id="imp-a", code_artifact_digest="d", model_ref="m",
        capability_profile={}, is_champion=True,
    ))
    app = create_observability_app(improver_store)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        r = await client.get("/")
        assert r.status_code == 200
        assert "Arete" in r.text
        assert "imp-a" in r.text
        assert "champion" in r.text.lower()

        r = await client.get("/improver/imp-a")
        assert r.status_code == 200
        assert "imp-a" in r.text
        assert "Lineage" in r.text

        r = await client.get("/improver/imp-ghost")
        assert r.status_code == 404

        r = await client.get("/tournament/tourn-ghost")
        assert r.status_code == 404

        r = await client.get("/proposal/mcp-ghost")
        assert r.status_code == 404

        r = await client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] in {"ok", "down"}

        r = await client.get("/integrity")
        assert r.status_code == 200
        assert "Integrity" in r.text
        assert "/integrity/help" in r.text

        r = await client.get("/integrity/help")
        assert r.status_code == 200
        for name in (
            "upstream_connectivity", "lineage_integrity",
            "single_champion", "stale_open_tournaments",
            "rejected_have_reasons", "closed_have_gain",
            "conditional_human_gate", "decisions_reference_candidates",
            "minted_claims_resolve",
        ):
            assert name in r.text

        # Read-only: no mutating routes exist.
        r = await client.post("/", json={"x": 1})
        assert r.status_code in {404, 405}


def _seed_tournament_with_campaign(store):
    """A tournament plus one orchestrated-campaign link row."""
    from ml_arete_mcp.state.models import (
        ImproverVersion, MetaContract, Tournament, TournamentArm,
        TournamentCampaign,
    )

    store.create_improver(ImproverVersion(
        id="imp-p", code_artifact_digest="d", model_ref="m",
        capability_profile={}, is_champion=True,
    ))
    store.create_improver(ImproverVersion(
        id="imp-c", code_artifact_digest="d2", model_ref="m2",
        capability_profile={},
    ))
    store.create_meta_contract(MetaContract(
        id="mcontract-1", version=1,
        metrics={"primary_metric": "hits", "direction": "max"},
        promotion_policy={},
    ))
    store.create_tournament(Tournament(
        id="tourn-t", contract_id="mcontract-1",
        parent_improver_id="imp-p", candidate_improver_id="imp-c",
        budget={"runs": 3},
    ))
    store.create_tournament_campaign(TournamentCampaign(
        id="tcamp-x", tournament_id="tourn-t",
        arm=TournamentArm.candidate, campaign_id="camp-z",
        upstream_contract_id="contract-u", challenger_id="cand-z",
    ))


async def test_tournament_page_links_entities(improver_store):
    """Entity ids on the tournament page link: camp-/cand- to the
    zetesis GUI, contract- to the episteme GUI, tcamp- and mcontract-
    to arete's own detail pages."""
    from ml_arete_mcp.observability.server import (
        create_observability_app,
    )

    _seed_tournament_with_campaign(improver_store)
    loop0_gui = "http://epi.example:38081"
    loop1_gui = "http://zet.example:38071"
    app = create_observability_app(
        improver_store,
        upstream_gui_bases={
            "loop0": loop0_gui, "loop1": loop1_gui,
        },
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        r = await client.get("/tournament/tourn-t")
        assert r.status_code == 200
        # Cross-server: zetesis campaign + roster anchor,
        # episteme evaluation contract.
        assert f'href="{loop1_gui}/campaign/camp-z"' in r.text
        assert f'href="{loop1_gui}/candidate/cand-z"' in r.text
        assert f'href="{loop0_gui}/contract/contract-u"' in r.text
        # Local: the join record and the meta-contract get real pages.
        assert 'href="/campaign-link/tcamp-x"' in r.text
        assert 'href="/contract/mcontract-1"' in r.text

        # The tcamp- detail page renders the join record, linking
        # back to the tournament and out to upstream entities.
        r = await client.get("/campaign-link/tcamp-x")
        assert r.status_code == 200
        assert 'href="/tournament/tourn-t"' in r.text
        assert f'href="{loop1_gui}/campaign/camp-z"' in r.text
        assert f'href="{loop0_gui}/contract/contract-u"' in r.text
        assert f'href="{loop1_gui}/candidate/cand-z"' in r.text

        r = await client.get("/campaign-link/tcamp-ghost")
        assert r.status_code == 404

        # The meta-contract page renders the policy + its tournaments.
        r = await client.get("/contract/mcontract-1")
        assert r.status_code == 200
        assert "primary_metric" in r.text
        assert 'href="/tournament/tourn-t"' in r.text

        r = await client.get("/contract/mcontract-ghost")
        assert r.status_code == 404


async def test_tournament_page_no_cross_links_without_gui_bases(
    improver_store,
):
    """Without configured upstream GUI bases the cross-server ids
    render as plain text — degraded-but-honest, never a dead link.
    Arete-local ids (tcamp-, mcontract-) still link to local pages."""
    from ml_arete_mcp.observability.server import (
        create_observability_app,
    )

    _seed_tournament_with_campaign(improver_store)
    app = create_observability_app(improver_store)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        r = await client.get("/tournament/tourn-t")
        assert r.status_code == 200
        assert "camp-z" in r.text and "cand-z" in r.text
        assert "campaign/camp-z" not in r.text
        assert "promotion#cand-z" not in r.text
        assert "/candidate/cand-z" not in r.text
        assert "contract-u" in r.text
        assert "/contract/contract-u" not in r.text
        # Local pages don't depend on upstream wiring.
        assert 'href="/campaign-link/tcamp-x"' in r.text
        assert 'href="/contract/mcontract-1"' in r.text
