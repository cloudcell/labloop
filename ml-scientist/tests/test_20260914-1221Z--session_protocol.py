"""Tests for the session protocol resource — protocol://session.

Verifies:
- The protocol resource returns the loop steps, tool catalog, state machine
- The dynamic state shows active programmes, hypotheses, trials
- The protocol includes the new data tools (prepare_data, verify_data)
- The protocol includes the new resources (dataref://, trial://artifacts)
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from ml_episteme_mcp.server import create_server
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
def mcp(store):
    return create_server(store)


@pytest.fixture
def client(mcp):
    from mcp.client import Client
    return Client(mcp)


# --- Helper: read a resource and parse the JSON result ---


async def read_resource(client, uri: str) -> dict:
    result = await client.read_resource(uri)
    return json.loads(result.contents[0].text)


# --- Tests ---


class TestSessionProtocol:
    """Tests for the protocol://session resource."""

    @pytest.mark.asyncio
    async def test_protocol_returns_loop_steps(self, client):
        """The protocol resource returns the loop steps."""
        async with client:
            protocol = await read_resource(client, "protocol://session")

            assert "loop_steps" in protocol
            assert len(protocol["loop_steps"]) == 10
            assert protocol["loop_steps"][0]["name"] == "create_programme"
            assert protocol["loop_steps"][-1]["name"] == "close_programme"

    @pytest.mark.asyncio
    async def test_protocol_includes_prepare_data(self, client):
        """The protocol includes the prepare_data step."""
        async with client:
            protocol = await read_resource(client, "protocol://session")

            step_names = [s["name"] for s in protocol["loop_steps"]]
            assert "prepare_data" in step_names

    @pytest.mark.asyncio
    async def test_protocol_includes_tool_catalog(self, client):
        """The protocol includes the tool catalog."""
        async with client:
            protocol = await read_resource(client, "protocol://session")

            assert "tool_catalog" in protocol
            tool_names = [t["name"] for t in protocol["tool_catalog"]]
            assert "prepare_data" in tool_names
            assert "verify_data" in tool_names
            assert "create_programme" in tool_names
            assert "run_trial" in tool_names

    @pytest.mark.asyncio
    async def test_protocol_includes_state_machine(self, client):
        """The protocol includes the state machine."""
        async with client:
            protocol = await read_resource(client, "protocol://session")

            assert "state_machine" in protocol
            assert "programme" in protocol["state_machine"]
            assert "hypothesis" in protocol["state_machine"]
            assert "trial" in protocol["state_machine"]

    @pytest.mark.asyncio
    async def test_protocol_includes_resources(self, client):
        """The protocol includes the resource list."""
        async with client:
            protocol = await read_resource(client, "protocol://session")

            assert "resources" in protocol
            uris = [r["uri"] for r in protocol["resources"]]
            assert "protocol://session" in uris
            assert "dataref://{id}" in uris
            assert "trial://{id}/artifacts" in uris

    @pytest.mark.asyncio
    async def test_protocol_includes_dynamic_state(self, client):
        """The protocol includes the dynamic current state."""
        async with client:
            protocol = await read_resource(client, "protocol://session")

            assert "current_state" in protocol
            assert "active_programmes" in protocol["current_state"]
            assert "timestamp" in protocol["current_state"]

    @pytest.mark.asyncio
    async def test_protocol_dynamic_state_shows_active_programmes(self, client, store):
        """The dynamic state shows active programmes."""
        from ml_episteme_mcp.state.models import Programme
        store.create_programme(Programme(
            id="prog-1",
            goal="test goal",
            constraints={},
            allowed_variables=["lr"],
            budget_max_trials=10,
            budget_max_wall_time_hours=1.0,
        ))

        async with client:
            protocol = await read_resource(client, "protocol://session")

            programmes = protocol["current_state"]["active_programmes"]
            assert len(programmes) == 1
            assert programmes[0]["id"] == "prog-1"
            assert programmes[0]["goal"] == "test goal"
            assert programmes[0]["status"] == "active"

    @pytest.mark.asyncio
    async def test_protocol_dynamic_state_shows_trial_counts(self, client, store):
        """The dynamic state shows trial counts per programme."""
        from ml_episteme_mcp.state.models import Programme, Hypothesis, Trial
        store.create_programme(Programme(
            id="prog-1",
            goal="test",
            constraints={},
            allowed_variables=["lr"],
            budget_max_trials=10,
            budget_max_wall_time_hours=1.0,
        ))
        store.create_hypothesis(Hypothesis(
            id="hyp-1",
            programme_id="prog-1",
            statement="test",
            failure_criterion="test",
            variables_involved=["lr"],
        ))
        store.create_trial(Trial(
            id="trial-1",
            programme_id="prog-1",
            hypothesis_id="hyp-1",
            config_json="{}",
        ))
        store.create_trial(Trial(
            id="trial-2",
            programme_id="prog-1",
            hypothesis_id="hyp-1",
            config_json="{}",
        ))

        async with client:
            protocol = await read_resource(client, "protocol://session")

            prog = protocol["current_state"]["active_programmes"][0]
            assert prog["trials"]["total"] == 2

    @pytest.mark.asyncio
    async def test_stale_programme_surfaced_not_closed(self, client, store):
        """An active programme idle >24h is flagged stale with a hint —
        surfaced for the agent to decide, never auto-closed."""
        from ml_episteme_mcp.state.models import Programme
        store.create_programme(Programme(
            id="prog-stale",
            goal="test",
            constraints={},
            allowed_variables=["lr"],
            budget_max_trials=10,
            budget_max_wall_time_hours=1.0,
        ))
        store._write(
            "UPDATE programmes SET created_at = ? WHERE id = ?",
            ("2020-01-01T00:00:00+00:00", "prog-stale"),
        )

        async with client:
            protocol = await read_resource(client, "protocol://session")

            prog = protocol["current_state"]["active_programmes"][0]
            assert prog["status"] == "active"  # surfaced, not closed
            assert prog["stale"] is True
            assert prog["idle_hours"] > 24
            assert "last_activity_at" in prog
            assert "abandoned" in prog["hint"]

    @pytest.mark.asyncio
    async def test_fresh_programme_not_stale(self, client, store):
        """A programme with recent activity carries activity fields
        but no stale flag."""
        from ml_episteme_mcp.state.models import Programme
        store.create_programme(Programme(
            id="prog-fresh",
            goal="test",
            constraints={},
            allowed_variables=["lr"],
            budget_max_trials=10,
            budget_max_wall_time_hours=1.0,
        ))

        async with client:
            protocol = await read_resource(client, "protocol://session")

            prog = protocol["current_state"]["active_programmes"][0]
            assert "stale" not in prog
            assert prog["idle_hours"] < 1

    @pytest.mark.asyncio
    async def test_protocol_declares_warmup_obligation(self, client):
        """The warmup protocol section: doc obligation + the honest
        scoping that file presence is workspace policy while
        attribution is the server-side residue."""
        async with client:
            protocol = await read_resource(client, "protocol://session")

            warmup = protocol["warmup"]
            assert "AGENTICSCIENCE.md" in warmup["obligation"]
            assert any(
                "protocol://session" in s for s in warmup["sequence"])
            assert "require_candidate_attribution" in \
                warmup["enforcement"]

    @pytest.mark.asyncio
    async def test_attribution_advisory_by_default(self, client):
        async with client:
            protocol = await read_resource(client, "protocol://session")

            attr = protocol["candidate_attribution"]
            assert attr["required"] is False
            assert "Optional" in attr["purpose"]

    @pytest.mark.asyncio
    async def test_attribution_required_when_flagged(self, store):
        """With require_candidate_attribution the resource must say
        so — the surface tells the truth about the tool gate."""
        from mcp.client import Client
        gated = Client(create_server(
            store,
            session_config={"require_candidate_attribution": True},
        ))
        async with gated:
            protocol = await read_resource(gated, "protocol://session")

            attr = protocol["candidate_attribution"]
            assert attr["required"] is True
            assert "REQUIRED" in attr["purpose"]
