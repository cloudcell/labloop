"""Phase 3 tests: MCP server surface — tools, resources, prompts.

Tests use the in-memory MCP client (Client(mcp)) to verify the server
end-to-end without any transport layer. Downstream role calls are stubbed.
"""

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


# --- Helper: call a tool and parse the JSON result ---


async def call_tool(client, name: str, args: dict) -> dict:
    result = await client.call_tool(name, args)
    text = result.content[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Schema-validation failures (bad Literal etc.) arrive as
        # is_error results with non-JSON text — surface as error.
        if result.is_error:
            return {"error": text}
        raise


async def call_tool_error(client, name: str, args: dict) -> dict:
    """Call a tool expected to fail; returns the error payload."""
    result = await client.call_tool(name, args)
    text = result.content[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"error": text}


# --- Tool discovery ---


class TestToolDiscovery:
    @pytest.mark.asyncio
    async def test_all_tools_listed(self, client):
        async with client:
            result = await client.list_tools()
            tool_names = [t.name for t in result.tools]
            expected = [
                "create_programme",
                "formulate_hypothesis",
                "design_experiment",
                "run_trial",
                "capture_bundle",
                "record_observation",
                "update_belief",
                "assess_programme",
                "get_next_experiment",
                "conclude_hypothesis",
                "conclude_hypothesis",
                "get_trial_status",
                "cancel_trial",
                "update_metric_direction",
            ]
            for tool in expected:
                assert tool in tool_names, f"Missing tool: {tool}"


# --- create_programme ---


class TestErrorChannel:
    """fail() returns is_error=True on the wire (W1)."""

    @pytest.mark.asyncio
    async def test_not_found_is_error_result(self, client):
        async with client:
            r = await client.call_tool(
                "get_trial_status",
                {"programme_id": "nope", "trial_id": "nope"})
        assert r.is_error
        payload = json.loads(r.content[0].text)
        assert payload["error"]

    @pytest.mark.asyncio
    async def test_success_is_not_error(self, client):
        async with client:
            r = await client.call_tool(
                "create_programme",
                {"goal": "g", "constraints": {},
                 "allowed_variables": ["lr"],
                 "budget": {"max_trials": 1}})
        assert not r.is_error
        assert "programme_id" in json.loads(r.content[0].text)


class TestCreateProgramme:
    @pytest.mark.asyncio
    async def test_create_programme(self, client):
        async with client:
            result = await call_tool(client, "create_programme", {
                "goal": "maximize validation accuracy",
                "constraints": {"gpu_memory_gb": 40, "max_training_hours_per_trial": 4},
                "allowed_variables": ["learning_rate", "depth"],
                "budget": {"max_trials": 50, "max_wall_time_hours": 200.0},
            })
            assert "programme_id" in result
            assert result["status"] == "created"

    @pytest.mark.asyncio
    async def test_create_programme_stores_state(self, client, store):
        async with client:
            result = await call_tool(client, "create_programme", {
                "goal": "test goal",
                "constraints": {"gpu_memory_gb": 10},
                "allowed_variables": ["lr"],
                "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
            })
            pid = result["programme_id"]
            p = store.get_programme(pid)
            assert p is not None
            assert p.goal == "test goal"
            assert p.constraints["gpu_memory_gb"] == 10

    @pytest.mark.asyncio
    async def test_create_programme_budget_extras_admitted(
        self, client, store
    ):
        """Unknown budget keys are recorded under budget_extras and
        admitted as not-enforced — never silently dropped (the
        max_walltime_s vs max_wall_time_hours trap)."""
        async with client:
            result = await call_tool(client, "create_programme", {
                "goal": "test budget extras",
                "constraints": {},
                "allowed_variables": ["lr"],
                "budget": {
                    "max_trials": 4,
                    "max_walltime_s": 3600,
                    "min_seeds_per_config": 3,
                },
            })
            assert result["budget_extras_not_enforced"] == [
                "max_walltime_s", "min_seeds_per_config",
            ]
            p = store.get_programme(result["programme_id"])
            assert p.constraints["budget_extras"] == {
                "max_walltime_s": 3600, "min_seeds_per_config": 3,
            }
            assert p.budget_max_wall_time_hours == 0.0


# --- formulate_hypothesis ---


class TestFormulateHypothesis:
    @pytest.mark.asyncio
    async def test_formulate_hypothesis(self, client):
        async with client:
            prog = await call_tool(client, "create_programme", {
                "goal": "test",
                "constraints": {},
                "allowed_variables": ["depth"],
                "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
            })
            result = await call_tool(client, "formulate_hypothesis", {
                "programme_id": prog["programme_id"],
                "statement": "depth improves generalization",
                "failure_criterion": "val_accuracy(depth=12) <= val_accuracy(depth=6)",
                "variables_involved": ["depth"],
            })
            assert "hypothesis_id" in result
            assert result["status"] == "created"

    @pytest.mark.asyncio
    async def test_rejects_empty_failure_criterion(self, client):
        """Commitment 3: Falsifiability is required for admission."""
        async with client:
            prog = await call_tool(client, "create_programme", {
                "goal": "test",
                "constraints": {},
                "allowed_variables": ["depth"],
                "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
            })
            result = await call_tool_error(client, "formulate_hypothesis", {
                "programme_id": prog["programme_id"],
                "statement": "depth improves generalization",
                "failure_criterion": "",
                "variables_involved": ["depth"],
            })
            assert "error" in result
            assert "Falsifiability" in result["error"]

    @pytest.mark.asyncio
    async def test_rejects_tautological_failure_criterion(self, client):
        """Commitment 3: rejects 'the run was unlucky'."""
        async with client:
            prog = await call_tool(client, "create_programme", {
                "goal": "test",
                "constraints": {},
                "allowed_variables": ["depth"],
                "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
            })
            result = await call_tool_error(client, "formulate_hypothesis", {
                "programme_id": prog["programme_id"],
                "statement": "depth improves generalization",
                "failure_criterion": "the run was unlucky",
                "variables_involved": ["depth"],
            })
            assert "error" in result
            assert "tautological" in result["error"]


# --- design_experiment + run_trial ---


class TestRunTrial:
    @pytest.mark.asyncio
    async def test_run_trial_requires_bundle(self, client):
        """Commitment 6: The bundle must be controlled."""
        async with client:
            prog = await call_tool(client, "create_programme", {
                "goal": "test",
                "constraints": {},
                "allowed_variables": ["lr"],
                "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
            })
            pid = prog["programme_id"]
            hyp = await call_tool(client, "formulate_hypothesis", {
                "programme_id": pid,
                "statement": "lr=0.001 is better",
                "failure_criterion": "val_accuracy(lr=0.001) <= val_accuracy(lr=0.01)",
                "variables_involved": ["lr"],
            })
            trial = await call_tool(client, "design_experiment", {
                "programme_id": pid,
                "hypothesis_id": hyp["hypothesis_id"],
                "config": {"lr": 0.001},
            })
            # No bundle captured yet — should reject
            result = await call_tool_error(client, "run_trial", {
                "programme_id": pid,
                "trial_id": trial["trial_id"],
            })
            assert "error" in result
            assert "bundle must be controlled" in result["error"]

    @pytest.mark.asyncio
    async def test_run_trial_with_bundle(self, client):
        async with client:
            prog = await call_tool(client, "create_programme", {
                "goal": "test",
                "constraints": {},
                "allowed_variables": ["lr"],
                "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
            })
            pid = prog["programme_id"]
            hyp = await call_tool(client, "formulate_hypothesis", {
                "programme_id": pid,
                "statement": "lr=0.001 is better",
                "failure_criterion": "val_accuracy(lr=0.001) <= val_accuracy(lr=0.01)",
                "variables_involved": ["lr"],
            })
            trial = await call_tool(client, "design_experiment", {
                "programme_id": pid,
                "hypothesis_id": hyp["hypothesis_id"],
                "config": {"lr": 0.001},
            })
            tid = trial["trial_id"]
            await call_tool(client, "capture_bundle", {
                "trial_id": tid,
                "code_ref": "tests/fixtures/train_stub.py",
                "env_ref": "conda:env1",
                "seeds": [42, 43],
                "splits": {"train": 0.8, "val": 0.1, "test": 0.1},
            })
            result = await call_tool(client, "run_trial", {
                "programme_id": pid,
                "trial_id": tid,
            })
            assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_rejects_orphan_trial(self, client):
        """Commitment 1: The loop is the unit — orphan runs forbidden."""
        async with client:
            prog = await call_tool(client, "create_programme", {
                "goal": "test",
                "constraints": {},
                "allowed_variables": ["lr"],
                "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
            })
            prog2 = await call_tool(client, "create_programme", {
                "goal": "test2",
                "constraints": {},
                "allowed_variables": ["lr"],
                "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
            })
            hyp = await call_tool(client, "formulate_hypothesis", {
                "programme_id": prog["programme_id"],
                "statement": "test",
                "failure_criterion": "val < 0.5",
                "variables_involved": ["lr"],
            })
            trial = await call_tool(client, "design_experiment", {
                "programme_id": prog["programme_id"],
                "hypothesis_id": hyp["hypothesis_id"],
                "config": {"lr": 0.001},
            })
            # Try to run under a different programme
            result = await call_tool_error(client, "run_trial", {
                "programme_id": prog2["programme_id"],
                "trial_id": trial["trial_id"],
            })
            assert "error" in result
            assert "loop is the unit" in result["error"]


# --- record_observation ---


class TestCaptureBundle:
    @pytest.mark.asyncio
    async def test_rejects_post_design_capture(self, client, store):
        """The bundle is sealed BEFORE execution (pre-registration of
        auxiliaries). A trial that has left 'designed' must not accept a
        capture — today unreachable via run_trial (bundle required), but
        the guard makes the ordering structural rather than incidental."""
        async with client:
            prog = await call_tool(client, "create_programme", {
                "goal": "test",
                "constraints": {},
                "allowed_variables": ["lr"],
                "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
            })
            hyp = await call_tool(client, "formulate_hypothesis", {
                "programme_id": prog["programme_id"],
                "statement": "lr matters",
                "failure_criterion": "no effect",
                "variables_involved": ["lr"],
            })
            trial = await call_tool(client, "design_experiment", {
                "programme_id": prog["programme_id"],
                "hypothesis_id": hyp["hypothesis_id"],
                "config": {"lr": 0.001},
            })
            tid = trial["trial_id"]
            store.update_trial_status(tid, "abandoned")
            result = await call_tool_error(client, "capture_bundle", {
                "trial_id": tid,
                "code_ref": "tests/fixtures/train_stub.py",
                "env_ref": "conda:env1",
                "seeds": [42],
                "splits": {"train": 1.0},
            })
            assert "error" in result
            assert "designed" in result["error"]


class TestRecordObservation:
    @pytest.mark.asyncio
    async def test_record_observation(self, client, store):
        async with client:
            prog = await call_tool(client, "create_programme", {
                "goal": "test",
                "constraints": {},
                "allowed_variables": ["lr"],
                "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
            })
            pid = prog["programme_id"]
            hyp = await call_tool(client, "formulate_hypothesis", {
                "programme_id": pid,
                "statement": "test",
                "failure_criterion": "val < 0.5",
                "variables_involved": ["lr"],
            })
            trial = await call_tool(client, "design_experiment", {
                "programme_id": pid,
                "hypothesis_id": hyp["hypothesis_id"],
                "config": {"lr": 0.001},
            })
            tid = trial["trial_id"]
            # Capture bundle (commitment 6) and complete trial (commitment 1)
            await call_tool(client, "capture_bundle", {
                "trial_id": tid,
                "code_ref": "tests/fixtures/train_stub.py",
                "env_ref": "conda:env1",
                "seeds": [42],
                "splits": {"train": 0.8},
            })
            store.update_trial_status(tid, "running")
            store.update_trial_executor_output(tid, '{"status": "completed", "exit_code": 0}')
            store.update_trial_status(tid, "completed")
            result = await call_tool(client, "record_observation", {
                "trial_id": tid,
                "metrics": {"val_accuracy": 0.92},
                "variance": {"val_accuracy": 0.003},
                "spatiotemporal_region": "gpu-0@2026-09-14",
            })
            assert "observation_id" in result

    @pytest.mark.asyncio
    async def test_rejects_no_variance(self, client, store):
        """Commitment 7: Reproducibility is the price of admission."""
        async with client:
            prog = await call_tool(client, "create_programme", {
                "goal": "test",
                "constraints": {},
                "allowed_variables": ["lr"],
                "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
            })
            pid = prog["programme_id"]
            hyp = await call_tool(client, "formulate_hypothesis", {
                "programme_id": pid,
                "statement": "test",
                "failure_criterion": "val < 0.5",
                "variables_involved": ["lr"],
            })
            trial = await call_tool(client, "design_experiment", {
                "programme_id": pid,
                "hypothesis_id": hyp["hypothesis_id"],
                "config": {"lr": 0.001},
            })
            tid = trial["trial_id"]
            # Capture bundle + complete trial so we reach the variance check
            await call_tool(client, "capture_bundle", {
                "trial_id": tid,
                "code_ref": "tests/fixtures/train_stub.py",
                "env_ref": "conda:env1",
                "seeds": [42],
                "splits": {"train": 0.8},
            })
            store.update_trial_status(tid, "running")
            store.update_trial_executor_output(tid, '{"status": "completed", "exit_code": 0}')
            store.update_trial_status(tid, "completed")
            result = await call_tool_error(client, "record_observation", {
                "trial_id": tid,
                "metrics": {"val_accuracy": 0.92},
                "variance": {},
                "spatiotemporal_region": "gpu-0",
            })
            assert "error" in result
            assert "variance" in result["error"].lower() or "reproducibility" in result["error"].lower()


# --- update_belief ---


class TestUpdateBelief:
    @pytest.mark.asyncio
    async def test_update_belief(self, client, store):
        async with client:
            prog = await call_tool(client, "create_programme", {
                "goal": "test",
                "constraints": {},
                "allowed_variables": ["lr"],
                "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
            })
            pid = prog["programme_id"]
            hyp = await call_tool(client, "formulate_hypothesis", {
                "programme_id": pid,
                "statement": "test",
                "failure_criterion": "val < 0.5",
                "variables_involved": ["lr"],
            })
            trial = await call_tool(client, "design_experiment", {
                "programme_id": pid,
                "hypothesis_id": hyp["hypothesis_id"],
                "config": {"lr": 0.001},
            })
            tid = trial["trial_id"]
            # Capture bundle + complete trial (required by record_observation)
            await call_tool(client, "capture_bundle", {
                "trial_id": tid,
                "code_ref": "tests/fixtures/train_stub.py",
                "env_ref": "conda:env1",
                "seeds": [42],
                "splits": {"train": 0.8},
            })
            store.update_trial_status(tid, "running")
            store.update_trial_executor_output(tid, '{"status": "completed", "exit_code": 0}')
            store.update_trial_status(tid, "completed")
            obs = await call_tool(client, "record_observation", {
                "trial_id": tid,
                "metrics": {"val_accuracy": 0.92},
                "variance": {"val_accuracy": 0.003},
                "spatiotemporal_region": "gpu-0",
            })
            result = await call_tool(client, "update_belief", {
                "programme_id": pid,
                "trial_id": tid,
                "observation_id": obs["observation_id"],
            })
            assert "belief_id" in result


# --- assess_programme ---


class TestAssessProgramme:
    @pytest.mark.asyncio
    async def test_assess_insufficient_data(self, client):
        async with client:
            prog = await call_tool(client, "create_programme", {
                "goal": "test",
                "constraints": {},
                "allowed_variables": ["lr"],
                "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
            })
            result = await call_tool(client, "assess_programme", {
                "programme_id": prog["programme_id"],
            })
            assert result["health"] == "insufficient-data"


# --- get_next_experiment ---


class TestGetNextExperiment:
    @pytest.mark.asyncio
    async def test_reports_remaining_budget(self, client):
        """Commitment 5: Budget is an epistemic resource."""
        async with client:
            prog = await call_tool(client, "create_programme", {
                "goal": "test",
                "constraints": {},
                "allowed_variables": ["lr"],
                "budget": {"max_trials": 50, "max_wall_time_hours": 200.0},
            })
            result = await call_tool(client, "get_next_experiment", {
                "programme_id": prog["programme_id"],
            })
            assert result["remaining_budget"]["trials"] == 50


# --- close_programme ---


class TestCloseProgramme:
    @pytest.mark.asyncio
    async def test_close_programme(self, client, store):
        async with client:
            prog = await call_tool(client, "create_programme", {
                "goal": "test",
                "constraints": {},
                "allowed_variables": ["lr"],
                "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
            })
            pid = prog["programme_id"]
            hyp = await call_tool(client, "formulate_hypothesis", {
                "programme_id": pid,
                "statement": "test",
                "failure_criterion": "val < 0.5",
                "variables_involved": ["lr"],
            })
            hid = hyp["hypothesis_id"]
            # Create evidence: a completed trial with a bundle + observation
            trial = await call_tool(client, "design_experiment", {
                "programme_id": pid,
                "hypothesis_id": hid,
                "config": {"lr": 0.001},
            })
            tid = trial["trial_id"]
            # Capture bundle (required by record_observation)
            await call_tool(client, "capture_bundle", {
                "trial_id": tid,
                "code_ref": "tests/fixtures/train_stub.py",
                "env_ref": "conda:env1",
                "seeds": [42],
                "splits": {"train": 0.8},
            })
            store.update_trial_status(tid, "running")
            store.update_trial_executor_output(tid, '{"status": "completed", "exit_code": 0}')
            store.update_trial_status(tid, "completed")
            await call_tool(client, "record_observation", {
                "trial_id": tid,
                "metrics": {"val_accuracy": 0.92},
                "variance": {"val_accuracy": 0.003},
                "spatiotemporal_region": "gpu-0",
            })
            # Create a belief update (required by conclude_hypothesis)
            from ml_episteme_mcp.state.models import Belief
            import uuid as _uuid
            store.create_belief(Belief(
                id=f"belief-{_uuid.uuid4().hex[:8]}",
                programme_id=pid,
                state_json='{"best_trials": []}',
            ))
            result = await call_tool(client, "conclude_hypothesis", {
                "programme_id": pid,
                "hypothesis_id": hid,
                "verdict": "accepted",
                "evidence_summary": "val_accuracy improved significantly",
            })
            assert result["verdict"] == "accepted"
            assert result["status"] == "closed"


# --- Resources ---


class TestResources:
    @pytest.mark.asyncio
    async def test_programme_resource(self, client):
        async with client:
            prog = await call_tool(client, "create_programme", {
                "goal": "maximize accuracy",
                "constraints": {"gpu_memory_gb": 40},
                "allowed_variables": ["lr"],
                "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
            })
            pid = prog["programme_id"]
            result = await client.read_resource(f"programme://{pid}")
            content = result.contents[0].text
            data = json.loads(content)
            assert data["goal"] == "maximize accuracy"
            assert data["constraints"]["gpu_memory_gb"] == 40
            assert data["ontological_category"] == "information content entity"

    @pytest.mark.asyncio
    async def test_hypotheses_resource(self, client):
        async with client:
            prog = await call_tool(client, "create_programme", {
                "goal": "test",
                "constraints": {},
                "allowed_variables": ["lr"],
                "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
            })
            pid = prog["programme_id"]
            await call_tool(client, "formulate_hypothesis", {
                "programme_id": pid,
                "statement": "test claim",
                "failure_criterion": "val < 0.5",
                "variables_involved": ["lr"],
            })
            result = await client.read_resource(f"programme://{pid}/hypotheses")
            data = json.loads(result.contents[0].text)
            assert len(data) == 1
            assert data[0]["statement"] == "test claim"

    @pytest.mark.asyncio
    async def test_trials_resource(self, client):
        async with client:
            prog = await call_tool(client, "create_programme", {
                "goal": "test",
                "constraints": {},
                "allowed_variables": ["lr"],
                "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
            })
            pid = prog["programme_id"]
            hyp = await call_tool(client, "formulate_hypothesis", {
                "programme_id": pid,
                "statement": "test",
                "failure_criterion": "val < 0.5",
                "variables_involved": ["lr"],
            })
            await call_tool(client, "design_experiment", {
                "programme_id": pid,
                "hypothesis_id": hyp["hypothesis_id"],
                "config": {"lr": 0.001},
            })
            result = await client.read_resource(f"programme://{pid}/trials")
            data = json.loads(result.contents[0].text)
            assert len(data) == 1
            assert data[0]["config"]["lr"] == 0.001

    @pytest.mark.asyncio
    async def test_belief_resource(self, client, store):
        async with client:
            prog = await call_tool(client, "create_programme", {
                "goal": "test",
                "constraints": {},
                "allowed_variables": ["lr"],
                "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
            })
            pid = prog["programme_id"]
            hyp = await call_tool(client, "formulate_hypothesis", {
                "programme_id": pid,
                "statement": "test",
                "failure_criterion": "val < 0.5",
                "variables_involved": ["lr"],
            })
            trial = await call_tool(client, "design_experiment", {
                "programme_id": pid,
                "hypothesis_id": hyp["hypothesis_id"],
                "config": {"lr": 0.001},
            })
            tid = trial["trial_id"]
            # Capture bundle + complete trial (required by record_observation)
            await call_tool(client, "capture_bundle", {
                "trial_id": tid,
                "code_ref": "tests/fixtures/train_stub.py",
                "env_ref": "conda:env1",
                "seeds": [42],
                "splits": {"train": 0.8},
            })
            store.update_trial_status(tid, "running")
            store.update_trial_executor_output(tid, '{"status": "completed", "exit_code": 0}')
            store.update_trial_status(tid, "completed")
            obs = await call_tool(client, "record_observation", {
                "trial_id": tid,
                "metrics": {"val_accuracy": 0.92},
                "variance": {"val_accuracy": 0.003},
                "spatiotemporal_region": "gpu-0",
            })
            await call_tool(client, "update_belief", {
                "programme_id": pid,
                "trial_id": tid,
                "observation_id": obs["observation_id"],
            })
            result = await client.read_resource(f"programme://{pid}/belief")
            data = json.loads(result.contents[0].text)
            assert "state" in data

    @pytest.mark.asyncio
    async def test_budget_resource(self, client):
        async with client:
            prog = await call_tool(client, "create_programme", {
                "goal": "test",
                "constraints": {},
                "allowed_variables": ["lr"],
                "budget": {"max_trials": 50, "max_wall_time_hours": 200.0},
            })
            result = await client.read_resource(f"programme://{prog['programme_id']}/budget")
            data = json.loads(result.contents[0].text)
            assert data["max_trials"] == 50
            assert data["remaining_trials"] == 50


# --- Prompts ---


class TestPrompts:
    @pytest.mark.asyncio
    async def test_list_prompts(self, client):
        async with client:
            result = await client.list_prompts()
            names = [p.name for p in result.prompts]
            assert "start_research_programme" in names
            assert "design_falsifiable_hypothesis" in names
            assert "review_programme_health" in names
            assert "monitor_running_trial" in names

    @pytest.mark.asyncio
    async def test_start_research_programme_prompt(self, client):
        async with client:
            result = await client.get_prompt(
                "start_research_programme",
                {
                    "goal": "maximize accuracy",
                    "constraints": '{"gpu_memory_gb": 40}',
                    "variables": "['learning_rate']",
                    "budget_trials": "50",
                },
            )
            text = result.messages[0].content.text
            assert "maximize accuracy" in text
            assert "create_programme" in text

    @pytest.mark.asyncio
    async def test_design_falsifiable_hypothesis_prompt(self, client):
        async with client:
            result = await client.get_prompt(
                "design_falsifiable_hypothesis",
                {"claim": "depth improves generalization"},
            )
            text = result.messages[0].content.text
            assert "failure_criterion" in text
            assert "Falsifiability" in text

    @pytest.mark.asyncio
    async def test_review_programme_health_prompt(self, client):
        async with client:
            result = await client.get_prompt(
                "review_programme_health",
                {"programme_id": "prog-123"},
            )
            text = result.messages[0].content.text
            assert "prog-123" in text
            assert "assess_programme" in text

    @pytest.mark.asyncio
    async def test_monitor_running_trial_prompt(self, client):
        async with client:
            result = await client.get_prompt(
                "monitor_running_trial",
                {"programme_id": "prog-1", "trial_id": "trial-1"},
            )
            text = result.messages[0].content.text
            assert "trial-1" in text
            assert "get_trial_status" in text
            # adaptive polling driven by reported ETA — not a fixed sleep
            assert "eta" in text.lower()


# --- Full lifecycle through MCP ---


class TestFullMcpLifecycle:
    @pytest.mark.asyncio
    async def test_end_to_end_through_mcp(self, client):
        """Full lifecycle: create → hypothesize → design → bundle → run →
        observe → update belief → assess → close."""
        async with client:
            # 1. Create programme
            prog = await call_tool(client, "create_programme", {
                "goal": "maximize validation accuracy",
                "constraints": {"gpu_memory_gb": 40, "max_training_hours_per_trial": 4},
                "allowed_variables": ["learning_rate", "depth"],
                "budget": {"max_trials": 50, "max_wall_time_hours": 200.0},
            })
            pid = prog["programme_id"]

            # 2. Formulate hypothesis
            hyp = await call_tool(client, "formulate_hypothesis", {
                "programme_id": pid,
                "statement": "depth=12 beats depth=6 under 40GB budget",
                "failure_criterion": "val_accuracy(depth=12) <= val_accuracy(depth=6)",
                "variables_involved": ["depth"],
            })
            hid = hyp["hypothesis_id"]

            # 3. Design experiment
            trial = await call_tool(client, "design_experiment", {
                "programme_id": pid,
                "hypothesis_id": hid,
                "config": {"depth": 12, "learning_rate": 0.001},
            })
            tid = trial["trial_id"]

            # 4. Capture bundle
            await call_tool(client, "capture_bundle", {
                "trial_id": tid,
                "code_ref": "tests/fixtures/train_stub.py",
                "env_ref": "conda:env-v1",
                "seeds": [42, 43, 44],
                "splits": {"train": 0.8, "val": 0.1, "test": 0.1},
            })

            # 5. Run trial
            run = await call_tool(client, "run_trial", {
                "programme_id": pid,
                "trial_id": tid,
            })
            assert run["status"] == "completed"

            # 6. Record observation
            obs = await call_tool(client, "record_observation", {
                "trial_id": tid,
                "metrics": {"val_accuracy": 0.94, "train_loss": 0.12},
                "variance": {"val_accuracy": 0.002, "train_loss": 0.005},
                "spatiotemporal_region": "gpu-0@2026-09-14T03:00Z",
            })

            # 7. Update belief
            belief = await call_tool(client, "update_belief", {
                "programme_id": pid,
                "trial_id": tid,
                "observation_id": obs["observation_id"],
            })
            assert "belief_id" in belief

            # 8. Assess programme
            assess = await call_tool(client, "assess_programme", {
                "programme_id": pid,
            })
            assert "health" in assess

            # 9. Close programme
            close = await call_tool(client, "conclude_hypothesis", {
                "programme_id": pid,
                "hypothesis_id": hid,
                "verdict": "accepted",
                "evidence_summary": "val_accuracy(0.94) > baseline with p<0.01",
            })
            assert close["verdict"] == "accepted"

            # 10. Verify state via resources
            belief_res = await client.read_resource(f"programme://{pid}/belief")
            belief_data = json.loads(belief_res.contents[0].text)
            assert "state" in belief_data


# --- Candidate-attribution gate (warmup protocol) ---


class TestCandidateAttributionGate:
    """require_candidate_attribution (the [session] flag): when on,
    create_programme refuses unattributed programmes; when off
    (default) attribution stays optional-but-recommended."""

    _PROG = {
        "goal": "gated programme",
        "constraints": {},
        "allowed_variables": ["lr"],
        "budget": {"max_trials": 5, "max_wall_time_hours": 1.0},
    }

    @pytest.fixture
    def gated_client(self, store):
        from mcp.client import Client

        return Client(create_server(
            store,
            session_config={"require_candidate_attribution": True},
        ))

    @pytest.mark.asyncio
    async def test_gate_off_allows_unattributed(self, client):
        async with client:
            r = await call_tool(client, "create_programme", dict(self._PROG))
        assert "programme_id" in r

    @pytest.mark.asyncio
    async def test_gate_on_rejects_unattributed(self, gated_client):
        async with gated_client:
            r = await call_tool(
                gated_client, "create_programme", dict(self._PROG))
        assert "error" in r
        assert "candidate_version_id" in r["error"]
        assert "required" in r["error"]

    @pytest.mark.asyncio
    async def test_gate_on_accepts_attributed(self, gated_client):
        async with gated_client:
            cand = await call_tool(gated_client, "register_candidate", {
                "code_artifact_digest": "none",
                "model_ref": "test-model",
                "capability_profile": {"tools": ["t1"]},
            })
            args = dict(self._PROG)
            args["candidate_version_id"] = cand["candidate_id"]
            r = await call_tool(gated_client, "create_programme", args)
        assert "error" not in r
        assert "programme_id" in r
