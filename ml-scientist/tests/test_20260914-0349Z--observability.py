"""Tests for the observability web GUI."""

import pytest
from starlette.testclient import TestClient

from ml_episteme_mcp.state.store import StateStore
from ml_episteme_mcp.state.models import (
    Programme,
    Hypothesis,
    Trial,
    Observation,
    Belief,
    Conclusion,
    Verdict,
    EvaluationContract,
    ProgrammeStatus,
    HypothesisStatus,
    TrialStatus,
)
from ml_episteme_mcp.observability.server import create_observability_app
from ml_episteme_mcp.observability.sparkline import sparkline


@pytest.fixture
def store(tmp_path):
    """Create a store with test data."""
    s = StateStore(str(tmp_path / "test.db"))
    s.connect()

    # Create a programme
    s.create_programme(Programme(
        id="prog-test1",
        goal="minimize val perplexity",
        constraints={"gpu_memory_gb": 8.0},
        allowed_variables=["learning_rate", "depth"],
        budget_max_trials=10,
        budget_max_wall_time_hours=5.0,
        metric_direction="minimize",
    ))

    # Create a hypothesis
    s.create_hypothesis(Hypothesis(
        id="hyp-test1",
        programme_id="prog-test1",
        statement="depth improves generalization",
        failure_criterion="val perplexity does not decrease by 0.05",
        variables_involved=["depth"],
    ))

    # Create a trial
    s.create_trial(Trial(
        id="trial-test1",
        programme_id="prog-test1",
        hypothesis_id="hyp-test1",
        config_json='{"learning_rate": 0.001, "depth": 4}',
    ))

    # Record an observation
    s.create_observation(Observation(
        id="obs-test1",
        trial_id="trial-test1",
        metrics_json='{"val_perplexity": 11.03}',
        variance_json='{"val_perplexity": 0.07}',
        spatiotemporal_region="gpu0-2026-09-14",
    ))

    # Update belief
    s.create_belief(Belief(
        id="belief-test1",
        programme_id="prog-test1",
        state_json='{"best_trials": [{"result": {"val_perplexity": 11.03}}]}',
    ))

    # Close with a conclusion
    s.create_conclusion(Conclusion(
        id="conc-test1",
        hypothesis_id="hyp-test1",
        programme_id="prog-test1",
        verdict=Verdict.accepted,
        evidence_ref="bootstrap CI excludes zero",
        evidence_summary="val perplexity decreased by 0.77 (CI [0.5, 1.0])",
    ))
    s.update_hypothesis_status("hyp-test1", "under_test")
    s.update_hypothesis_status("hyp-test1", "accepted")

    # An evaluation contract under the programme
    s.create_evaluation_contract(EvaluationContract(
        id="contract-test1",
        programme_id="prog-test1",
        version=1,
        metrics={"val_perplexity": "minimize"},
        promotion_policy={"min_gain": 1.05},
    ))

    yield s
    s.close()


@pytest.fixture
def client(store):
    """Create a test client for the observability app."""
    app = create_observability_app(store, refresh_interval=0)
    return TestClient(app)


class TestProgrammeList:
    def test_returns_200(self, client):
        """GET / returns 200."""
        resp = client.get("/")
        assert resp.status_code == 200

    def test_lists_programmes(self, client):
        """GET / lists programmes."""
        resp = client.get("/")
        assert "minimize val perplexity" in resp.text
        assert "prog-test1" in resp.text

    def test_shows_direction(self, client):
        """GET / shows the metric direction."""
        resp = client.get("/")
        assert "minimize" in resp.text

    def test_shows_budget_bar(self, client):
        """GET / shows the budget bar."""
        resp = client.get("/")
        assert "budget" in resp.text


class TestProgrammeDetail:
    def test_returns_200(self, client):
        """GET /programme/{id} returns 200."""
        resp = client.get("/programme/prog-test1")
        assert resp.status_code == 200

    def test_shows_goal(self, client):
        """GET /programme/{id} shows the programme goal."""
        resp = client.get("/programme/prog-test1")
        assert "minimize val perplexity" in resp.text

    def test_shows_hypotheses(self, client):
        """GET /programme/{id} shows hypotheses."""
        resp = client.get("/programme/prog-test1")
        assert "depth improves generalization" in resp.text
        assert "hyp-test1" in resp.text

    def test_shows_trials(self, client):
        """GET /programme/{id} shows trials."""
        resp = client.get("/programme/prog-test1")
        assert "trial-test1" in resp.text

    def test_shows_conclusions(self, client):
        """GET /programme/{id} shows conclusions."""
        resp = client.get("/programme/prog-test1")
        assert "conc-test1" in resp.text
        assert "accepted" in resp.text

    def test_shows_constraints(self, client):
        """GET /programme/{id} shows constraints."""
        resp = client.get("/programme/prog-test1")
        assert "gpu_memory_gb" in resp.text

    def test_shows_direction(self, client):
        """GET /programme/{id} shows metric direction."""
        resp = client.get("/programme/prog-test1")
        assert "minimize" in resp.text

    def test_missing_programme_returns_404(self, client):
        """GET /programme/nonexistent returns 404."""
        resp = client.get("/programme/nonexistent")
        assert resp.status_code == 404


class TestTrialDetail:
    def test_returns_200(self, client):
        """GET /programme/{id}/trial/{tid} returns 200."""
        resp = client.get("/programme/prog-test1/trial/trial-test1")
        assert resp.status_code == 200

    def test_shows_config(self, client):
        """GET trial detail shows the config."""
        resp = client.get("/programme/prog-test1/trial/trial-test1")
        assert "learning_rate" in resp.text
        assert "0.001" in resp.text

    def test_shows_observations(self, client):
        """GET trial detail shows observations."""
        resp = client.get("/programme/prog-test1/trial/trial-test1")
        assert "obs-test1" in resp.text
        assert "11.03" in resp.text

    def test_shows_variance(self, client):
        """GET trial detail shows variance."""
        resp = client.get("/programme/prog-test1/trial/trial-test1")
        assert "0.07" in resp.text

    def test_missing_trial_returns_404(self, client):
        """GET non-existent trial returns 404."""
        resp = client.get("/programme/prog-test1/trial/nonexistent")
        assert resp.status_code == 404


class TestBeliefDetail:
    def test_returns_200(self, client):
        """GET /programme/{id}/belief returns 200."""
        resp = client.get("/programme/prog-test1/belief")
        assert resp.status_code == 200

    def test_shows_direction(self, client):
        """GET belief detail shows direction."""
        resp = client.get("/programme/prog-test1/belief")
        assert "minimize" in resp.text

    def test_shows_belief_state(self, client):
        """GET belief detail shows the belief state."""
        resp = client.get("/programme/prog-test1/belief")
        assert "best_trials" in resp.text or "val_perplexity" in resp.text


class TestConclusionView:
    def test_returns_200(self, client):
        """GET /programme/{id}/conclusions returns 200."""
        resp = client.get("/programme/prog-test1/conclusions")
        assert resp.status_code == 200

    def test_shows_verdict(self, client):
        """GET conclusions shows the verdict."""
        resp = client.get("/programme/prog-test1/conclusions")
        assert "accepted" in resp.text

    def test_shows_evidence(self, client):
        """GET conclusions shows the evidence summary."""
        resp = client.get("/programme/prog-test1/conclusions")
        assert "bootstrap CI" in resp.text or "perplexity" in resp.text


class TestSparkline:
    def test_empty_values(self):
        """sparkline([]) returns empty string."""
        assert sparkline([]) == ""

    def test_single_value(self):
        """sparkline with one value returns an SVG with a circle."""
        svg = sparkline([1.0])
        assert "<svg" in svg
        assert "circle" in svg

    def test_multiple_values(self):
        """sparkline with multiple values returns an SVG with a polyline."""
        svg = sparkline([1.0, 2.0, 3.0, 2.5, 1.5])
        assert "<svg" in svg
        assert "polyline" in svg
        assert "points=" in svg

    def test_valid_svg(self):
        """sparkline returns valid SVG with xmlns."""
        svg = sparkline([1.0, 2.0, 3.0])
        assert "http://www.w3.org/2000/svg" in svg


class TestReadOnly:
    def test_post_not_allowed(self, client):
        """POST to any route returns 405."""
        resp = client.post("/")
        assert resp.status_code == 405

    def test_put_not_allowed(self, client):
        """PUT to any route returns 405."""
        resp = client.put("/")
        assert resp.status_code == 405

    def test_delete_not_allowed(self, client):
        """DELETE to any route returns 405."""
        resp = client.delete("/")
        assert resp.status_code == 405


class TestTrialsPartial:
    def test_returns_200(self, client):
        """GET /programme/{id}/trials returns 200."""
        resp = client.get("/programme/prog-test1/trials")
        assert resp.status_code == 200

    def test_shows_trial_rows(self, client):
        """GET trials partial shows trial rows."""
        resp = client.get("/programme/prog-test1/trials")
        assert "trial-test1" in resp.text


class TestContractDetail:
    def test_returns_200(self, client):
        """GET /contract/{id} returns 200."""
        resp = client.get("/contract/contract-test1")
        assert resp.status_code == 200

    def test_shows_metrics_and_policy(self, client):
        """Contract page renders metrics + promotion policy."""
        resp = client.get("/contract/contract-test1")
        assert "contract-test1" in resp.text
        assert "val_perplexity" in resp.text
        assert "min_gain" in resp.text

    def test_links_to_programme(self, client):
        """Contract page links back to its owning programme."""
        resp = client.get("/contract/contract-test1")
        assert 'href="/programme/prog-test1"' in resp.text

    def test_missing_contract_returns_404(self, client):
        """GET /contract/{id} returns 404 for unknown ids."""
        resp = client.get("/contract/contract-ghost")
        assert resp.status_code == 404
