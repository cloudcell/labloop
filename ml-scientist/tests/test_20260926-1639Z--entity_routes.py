"""Entity-id route tests — every minted id family must resolve to a
real detail surface on its owning GUI.

Context-bound records (findings, spawns, results, policies,
hypotheses, observations, conclusions, beliefs) get
resolve-and-redirect routes that land on the parent page's anchor;
standalone records (decisions, canaries, search policies) get their
own pages. These tests pin both sides: the redirect target and the
anchor actually existing.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient


# ------------------------------------------------------------------
# Episteme — /hypothesis, /observation, /conclusion, /belief,
#            /decision
# ------------------------------------------------------------------


@pytest.fixture
def episteme_client(tmp_path):
    from ml_episteme_mcp.observability.server import (
        create_observability_app,
    )
    from ml_episteme_mcp.state.models import (
        Belief,
        Bundle,
        CandidateVersion,
        Conclusion,
        DataRef,
        EvaluationContract,
        Hypothesis,
        Observation,
        Programme,
        PromotionDecision,
        PromotionVerdict,
        Trial,
        Verdict,
    )
    from ml_episteme_mcp.state.store import StateStore

    store = StateStore(str(tmp_path / "test.db"))
    store.connect()
    store.create_programme(Programme(
        id="prog-test1", goal="minimize val perplexity",
        constraints={}, allowed_variables=["depth"],
        budget_max_trials=10, budget_max_wall_time_hours=5.0,
        metric_direction="minimize",
        candidate_version_id="cand-test1"))
    store.create_hypothesis(Hypothesis(
        id="hyp-test1", programme_id="prog-test1",
        statement="depth improves generalization",
        failure_criterion="val perplexity does not decrease",
        variables_involved=["depth"]))
    store.create_trial(Trial(
        id="trial-test1", programme_id="prog-test1",
        hypothesis_id="hyp-test1", config_json='{"depth": 4}',
        bundle_id="bundle-test1"))
    store.create_bundle(Bundle(
        id="bundle-test1", trial_id="trial-test1",
        code_ref="/tmp/quick.py", env_ref="env:test",
        seeds_json="[1]", splits_json="{}",
        data_refs_json='["data-ref-test1"]',
        baseline_ref="data-ref-test1"))
    store.create_data_ref(DataRef(
        id="data-ref-test1", split="validation", regime="captured",
        source_uri="file:///tmp/val.jsonl",
        content_hash="sha256:testdata"))
    store.create_observation(Observation(
        id="obs-test1", trial_id="trial-test1",
        metrics_json='{"val_perplexity": 11.03}',
        variance_json='{}', spatiotemporal_region="gpu0"))
    store.create_belief(Belief(
        id="belief-test1", programme_id="prog-test1",
        state_json='{"best_trials": []}'))
    store.create_conclusion(Conclusion(
        id="conc-test1", hypothesis_id="hyp-test1",
        programme_id="prog-test1", verdict=Verdict.accepted,
        evidence_ref="CI excludes zero",
        evidence_summary="decreased by 0.77"))
    store.create_evaluation_contract(EvaluationContract(
        id="contract-test1", programme_id="prog-test1", version=1,
        metrics={"val_perplexity": "minimize"},
        promotion_policy={}))
    store.create_candidate_version(CandidateVersion(
        id="cand-test1", code_artifact_digest="sha256:test",
        model_ref="test-model", capability_profile={}))
    store.create_promotion_decision(PromotionDecision(
        id="decision-test1", candidate_id="cand-test1",
        contract_id="contract-test1",
        verdict=PromotionVerdict.promote,
        evidence_refs=["trial-test1", "obs-test1", "conc-test1"],
        rationale="candidate beat incumbent", decided_by="operator"))
    app = create_observability_app(store, refresh_interval=0)
    yield TestClient(app)
    store.close()


class TestEpistemeEntityRoutes:
    """Resolve-and-redirect routes for context-bound entity ids.

    obs-/conc-/belief- ids carry no context in the id itself — each
    route resolves the record, then redirects to the parent page's
    anchor so claim-graph references land somewhere real. hyp- is the
    exception: it renders a detail page (rc-12 W11), redirecting only
    when the owning programme is archived.
    """

    def test_hypothesis_renders_detail_page(self, episteme_client):
        """Live hypotheses render a detail page with the full
        statement (rc-12 W11) — no redirect to a truncated row."""
        r = episteme_client.get(
            "/hypothesis/hyp-test1", follow_redirects=False)
        assert r.status_code == 200
        assert "depth improves generalization" in r.text
        assert "val perplexity does not decrease" in r.text
        assert "/programme/prog-test1" in r.text

    def test_hypothesis_anchor_exists_on_programme(self, episteme_client):
        r = episteme_client.get("/programme/prog-test1")
        assert 'id="hyp-hyp-test1"' in r.text

    def test_observation_redirects_to_trial_anchor(self, episteme_client):
        r = episteme_client.get(
            "/observation/obs-test1", follow_redirects=False)
        assert r.status_code == 307
        assert (
            r.headers["location"]
            == "/programme/prog-test1/trial/trial-test1#obs-obs-test1")

    def test_observation_anchor_exists_on_trial(self, episteme_client):
        r = episteme_client.get("/programme/prog-test1/trial/trial-test1")
        assert 'id="obs-obs-test1"' in r.text

    def test_conclusion_redirects_to_conclusions_anchor(
        self, episteme_client
    ):
        r = episteme_client.get(
            "/conclusion/conc-test1", follow_redirects=False)
        assert r.status_code == 307
        assert (
            r.headers["location"]
            == "/programme/prog-test1/conclusions#conc-conc-test1")

    def test_conclusion_anchor_exists(self, episteme_client):
        r = episteme_client.get("/programme/prog-test1/conclusions")
        assert 'id="conc-conc-test1"' in r.text

    def test_belief_redirects_to_belief_page(self, episteme_client):
        r = episteme_client.get(
            "/belief/belief-test1", follow_redirects=False)
        assert r.status_code == 307
        assert (
            r.headers["location"] == "/programme/prog-test1/belief")

    def test_bundle_redirects_to_trial_anchor(self, episteme_client):
        r = episteme_client.get(
            "/bundle/bundle-test1", follow_redirects=False)
        assert r.status_code == 307
        assert (
            r.headers["location"]
            == "/programme/prog-test1/trial/trial-test1#bundle")

    def test_bundle_anchor_exists_on_trial(self, episteme_client):
        r = episteme_client.get("/programme/prog-test1/trial/trial-test1")
        assert 'id="bundle"' in r.text

    def test_shortcuts_404_on_unknown(self, episteme_client):
        for path in (
            "/hypothesis/hyp-ghost",
            "/observation/obs-ghost",
            "/conclusion/conc-ghost",
            "/belief/belief-ghost",
            "/decision/decision-ghost",
            "/bundle/bundle-ghost",
        ):
            assert episteme_client.get(path).status_code == 404, path

    def test_decision_page_renders(self, episteme_client):
        r = episteme_client.get("/decision/decision-test1")
        assert r.status_code == 200
        assert "decision-test1" in r.text
        assert "promote" in r.text
        assert "operator" in r.text

    def test_decision_links_local_evidence_refs(self, episteme_client):
        r = episteme_client.get("/decision/decision-test1")
        assert 'href="/trial/trial-test1"' in r.text
        assert 'href="/observation/obs-test1"' in r.text
        assert 'href="/conclusion/conc-test1"' in r.text


# ------------------------------------------------------------------
# Zetesis — /finding, /spawn, /campaign-result, /search-policy
# ------------------------------------------------------------------


@pytest.fixture
def zetesis_client(tmp_path):
    from ml_zetesis_mcp.observability.server import (
        create_observability_app,
    )
    from ml_zetesis_mcp.state.models import (
        CampaignArm,
        CampaignResult,
        CampaignSpawn,
        Finding,
        Investigation,
        PromotionCampaign,
        SearchPolicy,
    )
    from ml_zetesis_mcp.state.store import SearchStore

    store = SearchStore(str(tmp_path / "search.db"))
    store.connect()
    store.create_investigation(Investigation(
        id="inv-test1", question="does it link?"))
    store.create_finding(Finding(
        id="find-test1", investigation_id="inv-test1",
        content="finding content", confidence=0.5))
    store.create_campaign(PromotionCampaign(
        id="camp-test1", contract_id="contract-x",
        champion_id="cand-champ", challenger_id="cand-chal",
        primary_metric="val_perplexity"))
    store.create_campaign_result(CampaignResult(
        id="cres-test1", campaign_id="camp-test1",
        arm=CampaignArm.challenger, programme_id="prog-x",
        metrics={"val_perplexity": 9.1}))
    store.create_campaign_spawn(CampaignSpawn(
        id="spawn-test1", campaign_id="camp-test1",
        arm=CampaignArm.challenger, programme_id="prog-x",
        budget={}))
    store.create_search_policy(SearchPolicy(
        id="spol-test1", name="grid", version=1,
        policy={"step": 0.1}))
    app = create_observability_app(store)
    yield TestClient(app)
    store.close()


class TestZetesisEntityRoutes:
    def test_finding_redirects_to_investigation_anchor(
        self, zetesis_client
    ):
        r = zetesis_client.get(
            "/finding/find-test1", follow_redirects=False)
        assert r.status_code == 307
        assert (
            r.headers["location"]
            == "/investigation/inv-test1#find-find-test1")

    def test_finding_anchor_exists(self, zetesis_client):
        r = zetesis_client.get("/investigation/inv-test1")
        assert 'id="find-find-test1"' in r.text

    def test_spawn_redirects_to_campaign_anchor(self, zetesis_client):
        r = zetesis_client.get(
            "/spawn/spawn-test1", follow_redirects=False)
        assert r.status_code == 307
        assert (
            r.headers["location"]
            == "/campaign/camp-test1#spawn-spawn-test1")

    def test_result_redirects_to_campaign_anchor(self, zetesis_client):
        r = zetesis_client.get(
            "/campaign-result/cres-test1", follow_redirects=False)
        assert r.status_code == 307
        assert (
            r.headers["location"]
            == "/campaign/camp-test1#cres-cres-test1")

    def test_anchors_exist_on_campaign(self, zetesis_client):
        r = zetesis_client.get("/campaign/camp-test1")
        assert 'id="spawn-spawn-test1"' in r.text
        assert 'id="cres-cres-test1"' in r.text

    def test_search_policy_page(self, zetesis_client):
        r = zetesis_client.get("/search-policy/spol-test1")
        assert r.status_code == 200
        assert "spol-test1" in r.text
        assert "grid" in r.text

    def test_routes_404_on_unknown(self, zetesis_client):
        for path in (
            "/finding/find-ghost",
            "/spawn/spawn-ghost",
            "/campaign-result/cres-ghost",
            "/search-policy/spol-ghost",
        ):
            assert zetesis_client.get(path).status_code == 404, path


# ------------------------------------------------------------------
# Arete — /decision, /canary, /policy, /result
# ------------------------------------------------------------------


@pytest.fixture
def arete_client(tmp_path):
    from ml_arete_mcp.observability.server import (
        create_observability_app,
    )
    from ml_arete_mcp.state.models import (
        CanaryDeployment,
        DecisionVerdict,
        ImproverVersion,
        MetaContract,
        MetaDecision,
        PolicyVersion,
        Tournament,
        TournamentArm,
        TournamentResult,
    )
    from ml_arete_mcp.state.store import ImproverStore

    store = ImproverStore(str(tmp_path / "improver.db"))
    store.connect()
    store.create_improver(ImproverVersion(
        id="imp-parent", code_artifact_digest="d1",
        model_ref="m", is_champion=True))
    store.create_improver(ImproverVersion(
        id="imp-cand", parent_id="imp-parent",
        code_artifact_digest="d2", model_ref="m"))
    store.create_meta_contract(MetaContract(
        id="mcontract-test1", version=1,
        metrics={"recursive_gain": "maximize"},
        promotion_policy={}))
    store.create_tournament(Tournament(
        id="tourn-test1", contract_id="mcontract-test1",
        parent_improver_id="imp-parent",
        candidate_improver_id="imp-cand", budget={}))
    store.create_tournament_result(TournamentResult(
        id="tres-test1", tournament_id="tourn-test1",
        arm=TournamentArm.candidate, descendant_spec={},
        metrics={"recursive_gain": 1.1}))
    store.create_meta_decision(MetaDecision(
        id="mdec-test1", candidate_improver_id="imp-cand",
        contract_id="mcontract-test1", tournament_id="tourn-test1",
        verdict=DecisionVerdict.promote,
        evidence_refs=["tres-test1", "tourn-test1"],
        rationale="won", decided_by="operator"))
    store.create_policy_version(PolicyVersion(
        id="pol-test1", improver_version_id="imp-cand",
        decision_id="mdec-test1", policy={"k": "v"}))
    store.create_canary(CanaryDeployment(
        id="canary-test1", policy_version_id="pol-test1",
        scope={"slice": "10%"}))
    app = create_observability_app(store)
    yield TestClient(app)
    store.close()


class TestAreteEntityRoutes:
    def test_decision_page_renders(self, arete_client):
        r = arete_client.get("/decision/mdec-test1")
        assert r.status_code == 200
        assert "mdec-test1" in r.text
        assert "promote" in r.text
        assert "operator" in r.text

    def test_decision_links_context(self, arete_client):
        r = arete_client.get("/decision/mdec-test1")
        assert 'href="/improver/imp-cand"' in r.text
        assert 'href="/tournament/tourn-test1"' in r.text
        assert 'href="/contract/mcontract-test1"' in r.text
        assert 'href="/result/tres-test1"' in r.text

    def test_canary_page_renders(self, arete_client):
        r = arete_client.get("/canary/canary-test1")
        assert r.status_code == 200
        assert "canary-test1" in r.text
        assert "pol-test1" in r.text

    def test_policy_redirects_to_improver_anchor(self, arete_client):
        r = arete_client.get(
            "/policy/pol-test1", follow_redirects=False)
        assert r.status_code == 307
        assert (
            r.headers["location"] == "/improver/imp-cand#pol-pol-test1")

    def test_result_redirects_to_tournament_anchor(self, arete_client):
        r = arete_client.get(
            "/result/tres-test1", follow_redirects=False)
        assert r.status_code == 307
        assert (
            r.headers["location"]
            == "/tournament/tourn-test1#tres-tres-test1")

    def test_anchors_exist(self, arete_client):
        assert 'id="pol-pol-test1"' in arete_client.get(
            "/improver/imp-cand").text
        assert 'id="mdec-mdec-test1"' in arete_client.get(
            "/improver/imp-cand").text
        assert 'id="tres-tres-test1"' in arete_client.get(
            "/tournament/tourn-test1").text

    def test_routes_404_on_unknown(self, arete_client):
        for path in (
            "/decision/mdec-ghost",
            "/canary/canary-ghost",
            "/policy/pol-ghost",
            "/result/tres-ghost",
        ):
            assert arete_client.get(path).status_code == 404, path


# ------------------------------------------------------------------
# Anamnesis — _ref_link covers every routed prefix
# ------------------------------------------------------------------


class TestRefLinkCoverage:
    """Every prefix with a real detail surface must link cross-GUI."""

    @pytest.fixture(autouse=True)
    def _peer_urls(self, monkeypatch):
        # views.claims binds PEER_GUI_URLS at import time (from-import),
        # so patching the templates attribute is dead once the module
        # is cached — patch the name in the consuming module.
        from ml_anamnesis_mcp.observability.views import claims

        monkeypatch.setattr(
            claims, "PEER_GUI_URLS",
            {
                "episteme": "http://epi.gui",
                "zetesis": "http://zet.gui",
                "arete": "http://are.gui",
            },
        )

    @pytest.mark.parametrize(
        "ref_id, expected",
        [
            # arete
            ("mdec-x", "http://are.gui/decision/mdec-x"),
            ("pol-x", "http://are.gui/policy/pol-x"),
            ("canary-x", "http://are.gui/canary/canary-x"),
            ("tcamp-x", "http://are.gui/campaign-link/tcamp-x"),
            ("tres-x", "http://are.gui/result/tres-x"),
            ("mcontract-x", "http://are.gui/contract/mcontract-x"),
            ("tourn-x", "http://are.gui/tournament/tourn-x"),
            ("imp-x", "http://are.gui/improver/imp-x"),
            ("mcp-x", "http://are.gui/proposal/mcp-x"),
            # zetesis
            ("cand-x", "http://zet.gui/candidate/cand-x"),
            ("camp-x", "http://zet.gui/campaign/camp-x"),
            ("inv-x", "http://zet.gui/investigation/inv-x"),
            ("find-x", "http://zet.gui/finding/find-x"),
            ("spawn-x", "http://zet.gui/spawn/spawn-x"),
            ("cres-x", "http://zet.gui/campaign-result/cres-x"),
            ("spol-x", "http://zet.gui/search-policy/spol-x"),
            # episteme
            ("trial-x", "http://epi.gui/trial/trial-x"),
            ("prog-x", "http://epi.gui/programme/prog-x"),
            ("contract-x", "http://epi.gui/contract/contract-x"),
            ("archive-x", "http://epi.gui/archive/archive-x"),
            ("hyp-x", "http://epi.gui/hypothesis/hyp-x"),
            ("obs-x", "http://epi.gui/observation/obs-x"),
            ("conc-x", "http://epi.gui/conclusion/conc-x"),
            ("decision-x", "http://epi.gui/decision/decision-x"),
            ("belief-x", "http://epi.gui/belief/belief-x"),
            ("data-ref-x", "http://epi.gui/dataref/data-ref-x"),
            ("bundle-x", "http://epi.gui/bundle/bundle-x"),
        ],
    )
    def test_prefix_links(self, ref_id, expected):
        from ml_anamnesis_mcp.observability.views.claims import (
            _ref_link,
        )

        html = _ref_link(ref_id, "entity")
        assert f'href="{expected}"' in html, html

    def test_claim_refs_link_locally(self):
        from ml_anamnesis_mcp.observability.views.claims import (
            _ref_link,
        )

        html = _ref_link("claim-x", "claim")
        assert 'href="/claim/claim-x"' in html

    def test_edge_refs_link_locally(self):
        """edge-* ids are anamnesis-local rows — /edge/ resolves the
        owning claim at click time."""
        from ml_anamnesis_mcp.observability.views.claims import (
            _ref_link,
        )

        html = _ref_link("edge-x", "external")
        assert 'href="/edge/edge-x"' in html

    def test_eref_routes_through_resolver(self):
        from ml_anamnesis_mcp.observability.views.claims import (
            _ref_link,
        )

        html = _ref_link("eref-x", "entity")
        assert 'href="/ref/eref-x"' in html

    def test_unmapped_prefix_stays_opaque(self):
        from ml_anamnesis_mcp.observability.views.claims import (
            _ref_link,
        )

        html = _ref_link("vack-x", "entity")
        assert "<a " not in html
        assert "vack-x" in html


# ------------------------------------------------------------------
# Anamnesis — /edge resolver (rc-7b: edge-* refs were unlinked)
# ------------------------------------------------------------------


@pytest.fixture
def anamnesis_client(tmp_path):
    from ml_anamnesis_mcp.observability.server import (
        create_observability_app,
    )
    from ml_anamnesis_mcp.state.models import (
        Claim,
        ClaimEdge,
        ClaimType,
        RefType,
        Relation,
    )
    from ml_anamnesis_mcp.state.store import MemoryStore

    store = MemoryStore(str(tmp_path / "memory.db"))
    store.connect()
    store.create_claim(Claim(
        id="claim-test1", content="edge target claim",
        type=ClaimType.empirical, confidence=0.5,
        content_hash="h1"))
    store.create_claim(Claim(
        id="claim-test2", content="citing claim",
        type=ClaimType.empirical, confidence=0.5,
        content_hash="h2"))
    store.create_edge(ClaimEdge(
        id="edge-test1", from_claim="claim-test2",
        to_ref="claim-test1", ref_type=RefType.claim,
        relation=Relation.supports))
    app = create_observability_app(store)
    yield TestClient(app)
    store.close()


class TestAnamnesisEdgeRoute:
    def test_edge_redirects_to_claim_anchor(self, anamnesis_client):
        r = anamnesis_client.get(
            "/edge/edge-test1", follow_redirects=False)
        assert r.status_code == 307
        assert (
            r.headers["location"]
            == "/claim/claim-test2#edge-edge-test1")

    def test_edge_anchor_exists_on_claim(self, anamnesis_client):
        r = anamnesis_client.get("/claim/claim-test2")
        assert 'id="edge-edge-test1"' in r.text

    def test_edge_404_on_unknown(self, anamnesis_client):
        assert (
            anamnesis_client.get("/edge/edge-ghost").status_code == 404
        )


# ------------------------------------------------------------------
# Cross-server link maps — bundle-/edge- resolve on every consumer
# ------------------------------------------------------------------


class TestUpstreamLinkMaps:
    """bundle- (episteme) and edge- (anamnesis) must link from every
    server that renders foreign ids — each carries its own copy of the
    prefix map, so each needs its own assertion."""

    GUI_BASES = {
        "loop0": "http://epi.gui",
        "evidence": "http://epi.gui",
        "loop1": "http://zet.gui",
        "claims": "http://ana.gui",
    }

    @pytest.mark.parametrize(
        "entity_id, expected",
        [
            ("bundle-x", "http://epi.gui/bundle/bundle-x"),
            ("edge-x", "http://ana.gui/edge/edge-x"),
        ],
    )
    def test_zetesis_entity_url(self, entity_id, expected):
        from ml_zetesis_mcp.observability.links import entity_url

        assert entity_url(entity_id, self.GUI_BASES) == expected

    @pytest.mark.parametrize(
        "entity_id, expected",
        [
            ("bundle-x", "http://epi.gui/bundle/bundle-x"),
            ("edge-x", "http://ana.gui/edge/edge-x"),
        ],
    )
    def test_arete_entity_url(self, entity_id, expected):
        from ml_arete_mcp.observability.links import entity_url

        assert entity_url(entity_id, self.GUI_BASES) == expected

    @pytest.mark.parametrize(
        "entity_id, expected",
        [
            ("bundle-x", "http://epi.gui/bundle/bundle-x"),
            ("edge-x", "http://ana.gui/edge/edge-x"),
        ],
    )
    def test_agora_entity_url(self, entity_id, expected):
        from ml_agora_mcp.observability.links import entity_url

        assert entity_url(entity_id, self.GUI_BASES) == expected


# ------------------------------------------------------------------
# Hyperlinked ids — every rendered <name>-<id> is a link where a
# detail surface exists (the links.py convention, all five GUIs)
# ------------------------------------------------------------------


class TestEntityIdLinks:
    """Entity ids rendered in tables/cards link to their owning page
    — not just row titles or page headings."""

    # --- episteme: local ids link through its own shortcut routes ---

    def test_trial_page_links_observation(self, episteme_client):
        """The reported gap: obs-* cells on the trial page."""
        r = episteme_client.get(
            "/programme/prog-test1/trial/trial-test1")
        assert r.status_code == 200
        assert 'href="/observation/obs-test1"' in r.text

    def test_trial_page_links_bundle_and_hypothesis(
        self, episteme_client
    ):
        r = episteme_client.get(
            "/programme/prog-test1/trial/trial-test1")
        assert 'href="/bundle/bundle-test1"' in r.text
        assert 'href="/hypothesis/hyp-test1"' in r.text

    def test_trial_page_links_baseline_and_data_refs(
        self, episteme_client
    ):
        """baseline_ref and the data-provenance rows are data-ref-*
        ids — both must resolve through /dataref/."""
        r = episteme_client.get(
            "/programme/prog-test1/trial/trial-test1")
        assert r.text.count('href="/dataref/data-ref-test1"') >= 2

    def test_conclusions_page_links_conclusion_id(
        self, episteme_client
    ):
        r = episteme_client.get("/programme/prog-test1/conclusions")
        assert 'href="/conclusion/conc-test1"' in r.text

    def test_belief_page_links_belief_id(self, episteme_client):
        r = episteme_client.get("/programme/prog-test1/belief")
        assert 'href="/belief/belief-test1"' in r.text

    def test_contract_page_links_its_id(self, episteme_client):
        r = episteme_client.get("/contract/contract-test1")
        assert 'href="/contract/contract-test1"' in r.text

    def test_dataref_page_links_its_id(self, episteme_client):
        r = episteme_client.get("/dataref/data-ref-test1")
        assert 'href="/dataref/data-ref-test1"' in r.text

    def test_programme_page_links_its_id(self, episteme_client):
        r = episteme_client.get("/programme/prog-test1")
        assert 'href="/programme/prog-test1"' in r.text

    def test_candidate_page_renders(self, episteme_client):
        """cand-* ids are episteme-minted (register_candidate) — they
        get a local detail page, not a zetesis hop."""
        r = episteme_client.get("/candidate/cand-test1")
        assert r.status_code == 200
        assert "cand-test1" in r.text
        assert "test-model" in r.text
        assert 'href="/programme/prog-test1"' in r.text

    def test_decision_links_candidate_locally(self, episteme_client):
        r = episteme_client.get("/decision/decision-test1")
        assert 'href="/candidate/cand-test1"' in r.text

    def test_peer_id_links_when_gui_configured(self, monkeypatch):
        """Peer-minted ids (claims, zetesis rows) link only when the
        peer GUI is configured — never a guessed address."""
        from ml_episteme_mcp.observability import links

        monkeypatch.setattr(
            links, "PEER_GUI_URLS",
            {"anamnesis": "http://ana.gui", "zetesis": "http://zet.gui"},
        )
        assert (
            links.entity_url("claim-x") == "http://ana.gui/claim/claim-x")
        assert links.entity_url("camp-x") == "http://zet.gui/campaign/camp-x"

    def test_peer_id_unlinked_without_gui_config(self, monkeypatch):
        from ml_episteme_mcp.observability import links

        monkeypatch.setattr(links, "PEER_GUI_URLS", {})
        assert links.entity_url("claim-x") is None
        assert links.entity_url("camp-x") is None

    def test_entity_url_local_and_fallback(self):
        from ml_episteme_mcp.observability.links import entity_url

        assert entity_url("trial-x") == "/trial/trial-x"
        assert entity_url("data-ref-x") == "/dataref/data-ref-x"
        assert entity_url("cand-x") == "/candidate/cand-x"
        # Unmapped prefixes and non-ids render unlinked, never guessed.
        assert entity_url("vack-x") is None
        assert entity_url("sha256:abc") is None

    def test_linkify_links_ids_in_prose(self, monkeypatch):
        """Free-text fields citing ids (evidence summaries, rationales)
        get inline links — surrounding text stays escaped."""
        from ml_episteme_mcp.observability import links

        monkeypatch.setattr(links, "PEER_GUI_URLS", {})
        html = links.linkify(
            'trial-t1abc2 supported <injection> "attempt"')
        assert 'href="/trial/trial-t1abc2"' in html
        assert "&lt;injection&gt;" in html
        assert "&quot;attempt&quot;" in html

    # --- zetesis: context-bound ids link to their resolvers ---

    def test_campaign_page_links_result_and_spawn_ids(
        self, zetesis_client
    ):
        r = zetesis_client.get("/campaign/camp-test1")
        assert 'href="/campaign-result/cres-test1"' in r.text
        assert 'href="/spawn/spawn-test1"' in r.text

    # --- arete: lineage + context ids link ---

    def test_improver_page_links_parent_decision_policy(
        self, arete_client
    ):
        r = arete_client.get("/improver/imp-cand")
        assert 'href="/improver/imp-parent"' in r.text
        assert 'href="/decision/mdec-test1"' in r.text
        assert 'href="/policy/pol-test1"' in r.text

    def test_improver_list_links_parent(self, arete_client):
        r = arete_client.get("/")
        assert 'href="/improver/imp-parent"' in r.text

    def test_tournament_page_links_result_id(self, arete_client):
        r = arete_client.get("/tournament/tourn-test1")
        assert 'href="/result/tres-test1"' in r.text
