"""Tests for the Loop-0 status surfaces — protocol://status, the
status key embedded in protocol://session, and the status_report
prompt.

The digest is the cross-server contract (server, role,
workflow_position, open_work, blockers, recommended_next,
upstream_summary, integrity_summary) — every product emits the same
shape, computed live on every read.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from ml_episteme_mcp.resources.status import status_digest
from ml_episteme_mcp.server import create_server
from ml_episteme_mcp.state.models import (
    Hypothesis,
    Programme,
    Trial,
)
from ml_episteme_mcp.state.store import StateStore


# --- Fixtures ---


@pytest.fixture
def store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    s = StateStore(path)
    s.connect()
    yield s
    s.close()
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def client(store):
    from mcp.client import Client

    return Client(create_server(store))


async def read_resource(client, uri: str) -> dict:
    result = await client.read_resource(uri)
    return json.loads(result.contents[0].text)


def _programme(pid: str = "prog-1") -> Programme:
    return Programme(
        id=pid,
        goal="maximize validation accuracy",
        constraints={},
        allowed_variables=["lr"],
        budget_max_trials=10,
        budget_max_wall_time_hours=5.0,
    )


def _hypothesis(pid: str, hid: str = "hyp-1") -> Hypothesis:
    return Hypothesis(
        id=hid,
        programme_id=pid,
        statement="lr affects validation accuracy",
        failure_criterion="no effect at p<0.05",
        variables_involved=["lr"],
    )


def _trial(pid: str, hid: str, tid: str = "trial-1") -> Trial:
    return Trial(
        id=tid,
        programme_id=pid,
        hypothesis_id=hid,
        config_json=json.dumps({"lr": 0.01}),
    )


# --- Contract shape ---


class TestDigestShape:
    def test_empty_store_emits_full_contract(self, store):
        d = status_digest(store)
        for key in (
            "server", "role", "generated_at", "workflow_position",
            "open_work", "blockers", "recommended_next",
            "upstream_summary", "integrity_summary",
        ):
            assert key in d, f"missing contract key {key}"
        assert d["server"] == "ml-episteme-mcp"
        assert d["role"] == "loop0"
        assert d["open_work"] == []
        assert d["blockers"] == []
        # Empty lab → the only recommendation is to open a programme.
        assert d["recommended_next"][0]["tool"] == "create_programme"

    def test_upstream_summary_standalone(self, store):
        """No adaptor wired → configured 0, verdict ok (absent means
        absent — standalone is a supported posture)."""
        d = status_digest(store)
        assert d["upstream_summary"]["configured"] == 0
        assert d["upstream_summary"]["verdict"] == "ok"


# --- Recommendation heuristics ---


class TestRecommendations:
    def test_active_programme_without_hypothesis(self, store):
        store.create_programme(_programme())
        d = status_digest(store)
        tools = [r["tool"] for r in d["recommended_next"]]
        assert "formulate_hypothesis" in tools
        kinds = [w["kind"] for w in d["open_work"]]
        assert "programme" in kinds

    def test_hypothesis_without_trial_recommends_design(self, store):
        store.create_programme(_programme())
        h = _hypothesis("prog-1")
        store.create_hypothesis(h)
        # under_test is the working state — set directly (the
        # transition tools gate on evidence that doesn't exist yet).
        store.update_hypothesis_status(h.id, "under_test")
        d = status_digest(store)
        tools = [r["tool"] for r in d["recommended_next"]]
        assert "design_experiment" in tools

    def test_open_work_sorted_newest_first(self, store):
        """Consumers render the head of open_work (agora shows the
        first 5) — recency must lead. An older programme with no
        recent activity must not displace a newer one; child items
        sort by their own stamps."""
        store.create_programme(
            _programme("prog-old").model_copy(
                update={"created_at": "2026-01-01T00:00:00+00:00"}))
        store.create_programme(
            _programme("prog-new"))
        d = status_digest(store)
        progs = [
            w["id"] for w in d["open_work"] if w["kind"] == "programme"
        ]
        assert progs[0] == "prog-new"

    def test_unattributed_programme_flagged_as_debt(self, store):
        """Layer C: an active programme without candidate_version_id
        shows as attribution debt — flagged in open_work and surfaced
        as a recommended action (advisory, never enforced here)."""
        store.create_programme(_programme())
        d = status_digest(store)
        prog_items = [w for w in d["open_work"] if w["kind"] == "programme"]
        assert prog_items[0].get("unattributed") is True
        actions = [r["action"] for r in d["recommended_next"]]
        assert "attribute_programme" in actions

    def test_attributed_programme_no_debt(self, store):
        store.create_programme(
            _programme().model_copy(
                update={"candidate_version_id": "cand-x"}))
        d = status_digest(store)
        prog_items = [w for w in d["open_work"] if w["kind"] == "programme"]
        assert "unattributed" not in prog_items[0]
        actions = [r["action"] for r in d["recommended_next"]]
        assert "attribute_programme" not in actions

    def test_designed_trial_recommends_run(self, store):
        store.create_programme(_programme())
        h = _hypothesis("prog-1")
        store.create_hypothesis(h)
        store.update_hypothesis_status(h.id, "under_test")
        store.create_trial(_trial("prog-1", h.id))
        d = status_digest(store)
        tools = [r["tool"] for r in d["recommended_next"]]
        assert "run_trial" in tools

    def test_completed_trial_without_observation(self, store):
        store.create_programme(_programme())
        h = _hypothesis("prog-1")
        store.create_hypothesis(h)
        store.update_hypothesis_status(h.id, "under_test")
        t = _trial("prog-1", h.id)
        store.create_trial(t)
        store.update_trial_status(t.id, "running")
        store.update_trial_executor_output(
            t.id, json.dumps({"exit_code": 0, "stdout": "done"})
        )
        store.update_trial_status(t.id, "completed")
        d = status_digest(store)
        tools = [r["tool"] for r in d["recommended_next"]]
        assert "record_observation" in tools
        flagged = [
            w for w in d["open_work"]
            if w.get("flag") == "missing_observation"
        ]
        assert len(flagged) == 1

    def test_workflow_position_names_step(self, store):
        store.create_programme(_programme())
        d = status_digest(store)
        assert "step" in d["workflow_position"]


# --- Resource + session embedding ---


class TestSurfaces:
    @pytest.mark.asyncio
    async def test_status_resource_serves_digest(self, client):
        async with client:
            d = await read_resource(client, "protocol://status")
            assert d["server"] == "ml-episteme-mcp"
            assert d["role"] == "loop0"
            assert "recommended_next" in d

    @pytest.mark.asyncio
    async def test_session_embeds_status(self, client):
        async with client:
            session = await read_resource(client, "protocol://session")
            assert "status" in session
            assert session["status"]["role"] == "loop0"
            # Same contract keys as protocol://status — one source.
            for key in (
                "workflow_position", "open_work",
                "recommended_next", "integrity_summary",
            ):
                assert key in session["status"]

    @pytest.mark.asyncio
    async def test_status_report_prompt(self, client):
        async with client:
            result = await client.get_prompt("status_report", {})
            text = result.messages[0].content.text
            assert "status" in text.lower()
            # The digest is rendered — not a bare restatement.
            assert "ml-episteme" in text or "loop" in text.lower()
