"""Phase 4 tests: orchestration layer — verify tools call downstream roles
through the adaptor layer.

These tests use custom mock role implementations to verify that:
1. create_programme calls optimizer.create_study
2. run_trial calls executor.execute_code
3. record_observation persists to state.db (the owned episodic memory)
4. update_belief calls optimizer.tell and optimizer.best_trials
5. assess_programme calls optimizer.best_trials, reads state.db
6. get_next_experiment calls optimizer.ask
7. The full ask → run → tell cycle completes end-to-end

The server never names, imports, or depends on a specific product — only
the role interface.
"""

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from ml_episteme_mcp.clients.adaptor import MCPAdaptor
from ml_episteme_mcp.clients.roles import ClaimsRole, ExecutorRole, OptimizerRole
from ml_episteme_mcp.server import create_server
from ml_episteme_mcp.state.store import StateStore


# --- Mock role implementations that track calls ---


class MockOptimizer(OptimizerRole):
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.studies: dict[str, list[str]] = {}
        self.tells: list[tuple[str, str, dict]] = []
        self.ask_count = 0

    async def create_study(self, programme_id: str, variables: list[str], direction: str = "maximize") -> str:
        self.calls.append(("create_study", programme_id, {"variables": variables, "direction": direction}))
        self.studies[programme_id] = variables
        return f"study-{programme_id}"

    async def ask(self, programme_id: str) -> dict[str, Any]:
        self.ask_count += 1
        self.calls.append(("ask", programme_id, {"trial": self.ask_count}))
        variables = self.studies.get(programme_id, ["learning_rate"])
        return {v: 0.01 * self.ask_count for v in variables}

    async def tell(self, programme_id: str, trial_id: str, result: dict[str, float]) -> None:
        self.tells.append((programme_id, trial_id, result))
        self.calls.append(("tell", programme_id, {"trial_id": trial_id, "result": result}))

    async def best_trials(
        self, programme_id: str, direction: str | None = None
    ) -> list[dict[str, Any]]:
        self.calls.append(("best_trials", programme_id, {"direction": direction}))
        return [{"config": {"lr": 0.01}, "result": {"val_accuracy": 0.95}}]

    async def param_importance(self, programme_id: str) -> dict[str, float]:
        self.calls.append(("param_importance", programme_id, {}))
        return {"learning_rate": 0.8, "depth": 0.2}


class MockExecutor(ExecutorRole):
    def __init__(self):
        self.calls: list[str] = []

    async def execute_code(self, code: str, artifact_dir=None, trial_id=None, programme_id=None, bundle_id=None, extra_ro_paths=None, extra_rw_paths=None, python_exe=None, overlay_ro=None) -> str:
        self.calls.append(code)
        return json.dumps({"status": "completed", "output": "mock execution"})

    async def read_cell_output(self, cell_id: str) -> str:
        return json.dumps({"cell_id": cell_id, "output": "mock output"})


class MockClaims(ClaimsRole):
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.asserts: list[dict[str, Any]] = []

    async def assert_claim(
        self, content, type, confidence, evidence=None, source_id=None
    ):
        if self.fail:
            raise RuntimeError("claims server unreachable")
        self.asserts.append({
            "content": content, "type": type, "confidence": confidence,
            "evidence": evidence, "source_id": source_id,
        })
        return f"claim-{len(self.asserts)}"

    async def relate(self, from_claim, to_ref, ref_type, relation):
        return "edge-1"

    async def get_claim(self, claim_id):
        return {}

    async def list_claims(self, type=None, limit=50):
        return []


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
def mock_optimizer():
    return MockOptimizer()


@pytest.fixture
def mock_executor():
    return MockExecutor()


@pytest.fixture
def adaptor(mock_optimizer, mock_executor):
    a = MCPAdaptor({})
    a.set_optimizer(mock_optimizer)
    a.set_executor(mock_executor)
    return a


@pytest.fixture
def mcp(store, adaptor):
    return create_server(store, adaptor)


@pytest.fixture
def client(mcp):
    from mcp.client import Client
    return Client(mcp)


@pytest.fixture
def claims_client(store, mock_optimizer, mock_executor):
    """A client whose adaptor has a recording claims role wired."""
    a = MCPAdaptor({})
    a.set_optimizer(mock_optimizer)
    a.set_executor(mock_executor)
    claims = MockClaims()
    a.set_claims(claims)
    mcp = create_server(store, a)
    from mcp.client import Client
    return Client(mcp), claims


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


async def _drive_to_conclusion(client, store, verdict="accepted",
                               with_candidate=False):
    """Run a full loop and conclude; returns (result, pid, hid, tid)."""
    prog_args = {
        "goal": "test",
        "constraints": {},
        "allowed_variables": ["lr"],
        "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
    }
    if with_candidate:
        cand = await call_tool(client, "register_candidate", {
            "code_artifact_digest": "none",
            "model_ref": "test-model",
            "capability_profile": {"roles": ["optimizer"]},
        })
        prog_args["candidate_version_id"] = cand["candidate_id"]
    prog = await call_tool(client, "create_programme", prog_args)
    pid = prog["programme_id"]
    hyp = await call_tool(client, "formulate_hypothesis", {
        "programme_id": pid,
        "statement": "lr=0.01 beats lr=0.001",
        "failure_criterion": "val < 0.5",
        "variables_involved": ["lr"],
    })
    hid = hyp["hypothesis_id"]
    trial = await call_tool(client, "design_experiment", {
        "programme_id": pid,
        "hypothesis_id": hid,
        "config": {"lr": 0.01},
    })
    tid = trial["trial_id"]
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
        "metrics": {"val_accuracy": 0.9},
        "variance": {"val_accuracy": 0.01},
        "spatiotemporal_region": "gpu-0",
    })
    await call_tool(client, "update_belief", {
        "programme_id": pid,
        "trial_id": tid,
        "observation_id": obs["observation_id"],
    })
    result = await call_tool(client, "conclude_hypothesis", {
        "programme_id": pid,
        "hypothesis_id": hid,
        "verdict": verdict,
        "evidence_summary": "done",
    })
    return result, pid, hid, tid


# --- Orchestration tests ---


class TestCreateProgrammeOrchestration:
    @pytest.mark.asyncio
    async def test_calls_optimizer_create_study(self, client, mock_optimizer):
        """create_programme should call optimizer.create_study."""
        async with client:
            await call_tool(client, "create_programme", {
                "goal": "test",
                "constraints": {},
                "allowed_variables": ["learning_rate", "depth"],
                "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
            })
        # Verify optimizer.create_study was called
        study_calls = [c for c in mock_optimizer.calls if c[0] == "create_study"]
        assert len(study_calls) == 1
        assert study_calls[0][1] is not None  # programme_id
        assert "learning_rate" in study_calls[0][2]["variables"]

class TestRunTrialOrchestration:
    @pytest.mark.asyncio
    async def test_calls_executor_execute_code(self, client, mock_executor):
        """run_trial should call executor.execute_code."""
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
            await call_tool(client, "capture_bundle", {
                "trial_id": trial["trial_id"],
                "code_ref": "tests/fixtures/train_stub.py",
                "env_ref": "conda:env1",
                "seeds": [42],
                "splits": {"train": 0.8},
            })
            await call_tool(client, "run_trial", {
                "programme_id": pid,
                "trial_id": trial["trial_id"],
            })
        assert len(mock_executor.calls) == 1
        assert "run_training" in mock_executor.calls[0]


class TestRecordObservationOrchestration:
    @pytest.mark.asyncio
    async def test_persists_observation_in_state(self, client, store):
        """record_observation should persist the observation in state.db —
        state.db IS the episodic memory (ADR-0005)."""
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
        persisted = store.get_observation(obs["observation_id"])
        assert persisted is not None
        assert persisted.trial_id == tid


class TestUpdateBeliefOrchestration:
    @pytest.mark.asyncio
    async def test_calls_optimizer_tell(self, client, mock_optimizer, store):
        """update_belief should call optimizer.tell."""
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
        assert len(mock_optimizer.tells) == 1
        assert mock_optimizer.tells[0][2] == {"val_accuracy": 0.92}

    @pytest.mark.asyncio
    async def test_calls_optimizer_best_trials(self, client, mock_optimizer, store):
        """update_belief should call optimizer.best_trials."""
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
        best_calls = [c for c in mock_optimizer.calls if c[0] == "best_trials"]
        assert len(best_calls) == 1


class TestAssessProgrammeOrchestration:
    @pytest.mark.asyncio
    async def test_calls_optimizer(self, client, mock_optimizer):
        """assess_programme should call optimizer.best_trials."""
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
        best_calls = [c for c in mock_optimizer.calls if c[0] == "best_trials"]
        assert len(best_calls) == 1
        assert "total_observations" in result


class TestGetNextExperimentOrchestration:
    @pytest.mark.asyncio
    async def test_calls_optimizer_ask(self, client, mock_optimizer):
        """get_next_experiment should call optimizer.ask."""
        async with client:
            prog = await call_tool(client, "create_programme", {
                "goal": "test",
                "constraints": {},
                "allowed_variables": ["learning_rate"],
                "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
            })
            result = await call_tool(client, "get_next_experiment", {
                "programme_id": prog["programme_id"],
            })
        ask_calls = [c for c in mock_optimizer.calls if c[0] == "ask"]
        assert len(ask_calls) == 1
        assert "learning_rate" in result["next_config"]


class TestFullAskRunTellCycle:
    @pytest.mark.asyncio
    async def test_end_to_end_orchestration(
        self, client, mock_optimizer, mock_executor
    ):
        """Full ask → run → tell cycle through the downstream roles."""
        async with client:
            # 1. Create programme (wires optimizer + memory)
            prog = await call_tool(client, "create_programme", {
                "goal": "maximize val accuracy",
                "constraints": {"gpu_memory_gb": 40},
                "allowed_variables": ["learning_rate", "depth"],
                "budget": {"max_trials": 50, "max_wall_time_hours": 200.0},
            })
            pid = prog["programme_id"]

            # 2. Formulate hypothesis
            hyp = await call_tool(client, "formulate_hypothesis", {
                "programme_id": pid,
                "statement": "depth=12 beats depth=6",
                "failure_criterion": "val_acc(depth=12) <= val_acc(depth=6)",
                "variables_involved": ["depth"],
            })

            # 3. Get next experiment from optimizer (ask)
            next_exp = await call_tool(client, "get_next_experiment", {
                "programme_id": pid,
            })
            assert "learning_rate" in next_exp["next_config"]

            # 4. Design experiment with the optimizer's config
            trial = await call_tool(client, "design_experiment", {
                "programme_id": pid,
                "hypothesis_id": hyp["hypothesis_id"],
                "config": next_exp["next_config"],
            })
            tid = trial["trial_id"]

            # 5. Capture bundle
            await call_tool(client, "capture_bundle", {
                "trial_id": tid,
                "code_ref": "tests/fixtures/train_stub.py",
                "env_ref": "conda:env1",
                "seeds": [42, 43],
                "splits": {"train": 0.8, "val": 0.1, "test": 0.1},
            })

            # 6. Run trial (calls executor)
            run = await call_tool(client, "run_trial", {
                "programme_id": pid,
                "trial_id": tid,
            })
            assert run["status"] == "completed"

            # 7. Record observation (persisted to state.db)
            obs = await call_tool(client, "record_observation", {
                "trial_id": tid,
                "metrics": {"val_accuracy": 0.94},
                "variance": {"val_accuracy": 0.002},
                "spatiotemporal_region": "gpu-0@2026-09-14",
            })

            # 8. Update belief (calls optimizer tell + best_trials)
            belief = await call_tool(client, "update_belief", {
                "programme_id": pid,
                "trial_id": tid,
                "observation_id": obs["observation_id"],
            })
            assert "belief_id" in belief

            # 9. Assess programme (calls optimizer, reads state.db)
            assess = await call_tool(client, "assess_programme", {
                "programme_id": pid,
            })
            assert "health" in assess

            # 10. Close programme
            close = await call_tool(client, "conclude_hypothesis", {
                "programme_id": pid,
                "hypothesis_id": hyp["hypothesis_id"],
                "verdict": "accepted",
                "evidence_summary": "val_accuracy improved",
            })
            assert close["verdict"] == "accepted"

        # Verify the roles were called
        assert mock_optimizer.ask_count >= 1
        assert len(mock_optimizer.tells) == 1
        assert len(mock_executor.calls) == 1


class TestClaimMinting:
    """conclude_hypothesis mints a semantic claim via the claims role
    (Phase 3a) — a byproduct of the verdict, never a gate on it."""

    @pytest.mark.asyncio
    async def test_claim_content_is_scoped_finding(
        self, claims_client, store
    ):
        """The claim is the finding — hypothesis + regime + summary —
        not the bare conjecture."""
        client, claims = claims_client
        async with client:
            result, pid, hid, tid = await _drive_to_conclusion(client, store)
        assert result["claim_status"] == "minted"
        assert len(claims.asserts) == 1
        content = claims.asserts[0]["content"]
        assert "lr=0.01 beats lr=0.001" in content
        assert "holds under" in content
        assert "done" in content  # evidence_summary
        assert claims.asserts[0]["type"] == "empirical"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("verdict", ["accepted", "rejected"])
    async def test_verdict_confidence_is_symmetric(
        self, claims_client, store, verdict
    ):
        """Confidence tracks evidence strength, not the verdict — a clean
        falsification is positive knowledge (Popper)."""
        client, claims = claims_client
        async with client:
            result, pid, hid, tid = await _drive_to_conclusion(
                client, store, verdict=verdict
            )
        assert result["claim_status"] == "minted"
        assert claims.asserts[0]["confidence"] == 0.85

    @pytest.mark.asyncio
    async def test_rejected_mints_the_falsification(
        self, claims_client, store
    ):
        """A rejected verdict mints the negation — the knowledge is that
        the hypothesis does not hold."""
        client, claims = claims_client
        async with client:
            result, pid, hid, tid = await _drive_to_conclusion(
                client, store, verdict="rejected"
            )
        assert "does not hold" in claims.asserts[0]["content"]

    @pytest.mark.asyncio
    async def test_inconclusive_mints_nothing(self, claims_client, store):
        """An undecided test yields no empirical claim."""
        client, claims = claims_client
        async with client:
            result, pid, hid, tid = await _drive_to_conclusion(
                client, store, verdict="inconclusive"
            )
        assert result["verdict"] == "inconclusive"
        assert result["claim_status"] == "skipped"
        assert "claim_id" not in result
        assert claims.asserts == []

    @pytest.mark.asyncio
    async def test_evidence_edges(self, claims_client, store):
        """Provenance: tested_by->trials, derived_from->conclusion,
        supports->observations."""
        client, claims = claims_client
        async with client:
            result, pid, hid, tid = await _drive_to_conclusion(client, store)
        obs_ids = [o.id for o in store.list_observations(tid)]
        evidence = claims.asserts[0]["evidence"]
        assert {
            "to_ref": result["conclusion_id"],
            "ref_type": "conclusion",
            "relation": "derived_from",
        } in evidence
        assert {
            "to_ref": tid, "ref_type": "trial", "relation": "tested_by",
        } in evidence
        for oid in obs_ids:
            assert {
                "to_ref": oid,
                "ref_type": "observation",
                "relation": "supports",
            } in evidence

    @pytest.mark.asyncio
    async def test_source_id_falls_back_to_programme_id(
        self, claims_client, store
    ):
        """Without a candidate_version_id, source_id is the programme id."""
        client, claims = claims_client
        async with client:
            result, pid, hid, tid = await _drive_to_conclusion(client, store)
        assert claims.asserts[0]["source_id"] == pid

    @pytest.mark.asyncio
    async def test_source_id_prefers_candidate_version(
        self, claims_client, store
    ):
        """When the programme names a candidate, source_id is that id."""
        client, claims = claims_client
        async with client:
            result, pid, hid, tid = await _drive_to_conclusion(
                client, store, with_candidate=True
            )
        assert claims.asserts[0]["source_id"].startswith("cand-")

    @pytest.mark.asyncio
    async def test_claims_failure_does_not_block_conclusion(
        self, store, mock_optimizer, mock_executor
    ):
        """A claims outage must never block a verdict — reported, not raised."""
        a = MCPAdaptor({})
        a.set_optimizer(mock_optimizer)
        a.set_executor(mock_executor)
        a.set_claims(MockClaims(fail=True))
        mcp = create_server(store, a)
        from mcp.client import Client
        client = Client(mcp)
        async with client:
            result, pid, hid, tid = await _drive_to_conclusion(client, store)
        assert result["verdict"] == "accepted"
        assert result["claim_status"] == "failed"
        assert "claim_id" not in result
        assert "claim_error" in result

    @pytest.mark.asyncio
    async def test_no_claims_adaptor_no_claim_fields(self, client, store):
        """Absent claims role → conclusion succeeds with no claim fields."""
        async with client:
            result, pid, hid, tid = await _drive_to_conclusion(client, store)
        assert result["verdict"] == "accepted"
        assert "claim_status" not in result
        assert "claim_id" not in result


class TestRoleInterfaceNotProductDependent:
    @pytest.mark.asyncio
    async def test_server_works_with_any_optimizer(self, store):
        """The server works with any implementation of the optimizer role —
        it does not depend on a specific product."""
        # Create a completely different optimizer implementation
        class CustomOptimizer(OptimizerRole):
            async def create_study(self, programme_id, variables, direction="maximize"):
                return "custom-study"
            async def ask(self, programme_id):
                return {"custom_param": 0.5}
            async def tell(self, programme_id, trial_id, result):
                pass
            async def best_trials(self, programme_id, direction=None):
                return [{"custom": True}]
            async def param_importance(self, programme_id):
                return {"custom_param": 1.0}

        class CustomExecutor(ExecutorRole):
            async def execute_code(self, code, artifact_dir=None, trial_id=None, programme_id=None, bundle_id=None, extra_ro_paths=None, extra_rw_paths=None, python_exe=None, overlay_ro=None):
                return "custom output"
            async def read_cell_output(self, cell_id):
                return "custom"

        class CustomClaims(ClaimsRole):
            async def assert_claim(self, content, type, confidence, evidence=None, source_id=None):
                return "custom-claim"
            async def relate(self, from_claim, to_ref, ref_type, relation):
                return "custom-edge"
            async def get_claim(self, claim_id):
                return {}
            async def list_claims(self, type=None, limit=50):
                return []

        adaptor = MCPAdaptor({})
        adaptor.set_optimizer(CustomOptimizer())
        adaptor.set_executor(CustomExecutor())
        adaptor.set_claims(CustomClaims())

        mcp = create_server(store, adaptor)
        from mcp.client import Client
        client = Client(mcp)

        async with client:
            prog = await call_tool(client, "create_programme", {
                "goal": "test",
                "constraints": {},
                "allowed_variables": ["custom_param"],
                "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
            })
            next_exp = await call_tool(client, "get_next_experiment", {
                "programme_id": prog["programme_id"],
            })
            assert next_exp["next_config"]["custom_param"] == 0.5
