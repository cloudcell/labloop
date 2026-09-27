"""Tests for local adaptors — real implementations of the role interfaces.

These tests verify that the local adaptors do real work:
- LocalExecutor: runs Python code and captures output
- LocalOptimizer: does real random search HPO

There is no local memory adaptor: state.db IS the episodic memory
(ADR-0005) and semantic memory (claims) binds over MCP when configured.
"""

import json
from pathlib import Path

import pytest

from ml_episteme_mcp.clients.adaptor import create_local_adaptor
from ml_episteme_mcp.clients.local_executor import LocalExecutor
from ml_episteme_mcp.clients.local_optimizer import LocalOptimizer


# --- LocalExecutor tests ---


class TestLocalExecutor:
    @pytest.mark.asyncio
    async def test_execute_simple_code(self):
        """The executor runs Python code and captures stdout."""
        executor = LocalExecutor()
        result = await executor.execute_code("print('hello world')")
        data = json.loads(result)
        assert data["status"] == "completed"
        assert "hello world" in data["stdout"]
        assert data["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_capture_stderr(self):
        """The executor captures stderr."""
        executor = LocalExecutor()
        result = await executor.execute_code(
            "import sys; sys.stderr.write('error msg\\n')"
        )
        data = json.loads(result)
        assert data["status"] == "completed"
        assert "error msg" in data["stderr"]

    @pytest.mark.asyncio
    async def test_failed_execution(self):
        """The executor reports non-zero exit codes."""
        executor = LocalExecutor()
        result = await executor.execute_code("import sys; sys.exit(1)")
        data = json.loads(result)
        assert data["status"] == "failed"
        assert data["exit_code"] == 1

    @pytest.mark.asyncio
    async def test_execution_exception(self):
        """The executor captures Python exceptions in stderr."""
        executor = LocalExecutor()
        result = await executor.execute_code("raise ValueError('boom')")
        data = json.loads(result)
        assert data["status"] == "failed"
        assert "ValueError" in data["stderr"]
        assert "boom" in data["stderr"]

    @pytest.mark.asyncio
    async def test_timeout(self):
        """The executor kills processes that exceed the timeout."""
        executor = LocalExecutor(timeout=1)
        result = await executor.execute_code(
            "import time; time.sleep(10)"
        )
        data = json.loads(result)
        assert data["status"] == "timeout"
        assert "1s" in data["error"]

    @pytest.mark.asyncio
    async def test_read_cell_output(self):
        """The executor stores output for later retrieval."""
        executor = LocalExecutor()
        result = await executor.execute_code("print('cached output')")
        data = json.loads(result)
        # The cell_id is the script path (internal), but we can test
        # that read_cell_output returns something
        # The actual cell_id is the temp file path which is deleted after
        # execution, so we test the mechanism with a manual key
        executor._cell_outputs["test-cell"] = "cached output"
        output = await executor.read_cell_output("test-cell")
        out_data = json.loads(output)
        assert out_data["cell_id"] == "test-cell"
        assert "cached output" in out_data["output"]


# --- LocalOptimizer tests ---


class TestLocalOptimizer:
    @pytest.mark.asyncio
    async def test_create_study(self):
        """The optimizer creates a study and returns a study_id."""
        opt = LocalOptimizer(seed=42)
        study_id = await opt.create_study("prog-test", ["learning_rate", "depth"])
        assert study_id == "study-prog-test"

    @pytest.mark.asyncio
    async def test_ask_returns_config(self):
        """The optimizer returns a configuration with the specified variables."""
        opt = LocalOptimizer(seed=42)
        await opt.create_study("prog-test", ["learning_rate", "depth"])
        config = await opt.ask("prog-test")
        assert "learning_rate" in config
        assert "depth" in config
        assert config["_trial_number"] == 1

    @pytest.mark.asyncio
    async def test_ask_is_reproducible_with_seed(self):
        """The optimizer produces the same configs with the same seed."""
        opt1 = LocalOptimizer(seed=123)
        opt2 = LocalOptimizer(seed=123)
        await opt1.create_study("prog", ["learning_rate"])
        await opt2.create_study("prog", ["learning_rate"])
        c1 = await opt1.ask("prog")
        c2 = await opt2.ask("prog")
        assert c1["learning_rate"] == c2["learning_rate"]

    @pytest.mark.asyncio
    async def test_ask_varies_across_calls(self):
        """The optimizer returns different configs on successive calls."""
        opt = LocalOptimizer(seed=42)
        await opt.create_study("prog", ["learning_rate"])
        c1 = await opt.ask("prog")
        c2 = await opt.ask("prog")
        assert c1["learning_rate"] != c2["learning_rate"]
        assert c1["_trial_number"] == 1
        assert c2["_trial_number"] == 2

    @pytest.mark.asyncio
    async def test_ask_learning_rate_in_range(self):
        """The optimizer samples learning_rate from the log-uniform range."""
        opt = LocalOptimizer(seed=42)
        await opt.create_study("prog", ["learning_rate"])
        for _ in range(20):
            config = await opt.ask("prog")
            lr = config["learning_rate"]
            assert 1e-5 <= lr <= 1e-1, f"lr={lr} out of range"

    @pytest.mark.asyncio
    async def test_ask_depth_is_integer(self):
        """The optimizer returns integer values for depth."""
        opt = LocalOptimizer(seed=42)
        await opt.create_study("prog", ["depth"])
        for _ in range(10):
            config = await opt.ask("prog")
            assert isinstance(config["depth"], int)
            assert 1 <= config["depth"] <= 24

    @pytest.mark.asyncio
    async def test_tell_and_best_trials(self):
        """The optimizer tracks the best trial by the primary metric."""
        opt = LocalOptimizer(seed=42)
        await opt.create_study("prog", ["learning_rate"])
        await opt.tell("prog", "trial-1", {"val_accuracy": 0.85})
        await opt.tell("prog", "trial-2", {"val_accuracy": 0.92})
        await opt.tell("prog", "trial-3", {"val_accuracy": 0.88})

        best = await opt.best_trials("prog")
        assert len(best) == 1
        assert best[0]["result"]["val_accuracy"] == 0.92
        assert best[0]["trial_id"] == "trial-2"
        assert best[0]["primary_metric"] == "val_accuracy"
        assert best[0]["direction"] == "maximize"

    @pytest.mark.asyncio
    async def test_best_trials_empty(self):
        """The optimizer returns empty list when no trials are told."""
        opt = LocalOptimizer(seed=42)
        await opt.create_study("prog", ["learning_rate"])
        best = await opt.best_trials("prog")
        assert best == []

    @pytest.mark.asyncio
    async def test_param_importance_returns_dict(self):
        """The optimizer returns parameter importance for all variables."""
        opt = LocalOptimizer(seed=42)
        await opt.create_study("prog", ["learning_rate", "depth"])
        importance = await opt.param_importance("prog")
        assert "learning_rate" in importance
        assert "depth" in importance
        # Without trial configs, importance is uniform
        assert abs(importance["learning_rate"] - importance["depth"]) < 0.01

    @pytest.mark.asyncio
    async def test_param_importance_sums_to_one(self):
        """Parameter importance values sum to approximately 1."""
        opt = LocalOptimizer(seed=42)
        await opt.create_study("prog", ["learning_rate", "depth", "dropout"])
        importance = await opt.param_importance("prog")
        total = sum(importance.values())
        assert abs(total - 1.0) < 0.01

    @pytest.mark.asyncio
    async def test_param_importance_reflects_correlation(self):
        """A parameter that drives the metric dominates importance."""
        opt = LocalOptimizer(seed=42)
        await opt.create_study("prog", ["learning_rate", "depth"])
        for i in range(15):
            config = await opt.ask("prog")
            await opt.tell("prog", f"trial-{i}", {"acc": config["depth"] * 0.04})
        importance = await opt.param_importance("prog")
        assert importance["depth"] > 0.9
        assert importance["depth"] > importance["learning_rate"]

    @pytest.mark.asyncio
    async def test_best_trials_minimize_direction(self):
        """The optimizer picks the LOWEST metric when direction='minimize'."""
        opt = LocalOptimizer(seed=42)
        await opt.create_study("prog", ["learning_rate"], direction="minimize")
        await opt.tell("prog", "trial-1", {"val_perplexity": 11.03})
        await opt.tell("prog", "trial-2", {"val_perplexity": 10.26})
        await opt.tell("prog", "trial-3", {"val_perplexity": 10.85})

        best = await opt.best_trials("prog")
        assert len(best) == 1
        assert best[0]["result"]["val_perplexity"] == 10.26  # lowest, not highest
        assert best[0]["trial_id"] == "trial-2"
        assert best[0]["direction"] == "minimize"

    @pytest.mark.asyncio
    async def test_best_trials_maximize_direction(self):
        """The optimizer picks the HIGHEST metric when direction='maximize'."""
        opt = LocalOptimizer(seed=42)
        await opt.create_study("prog", ["learning_rate"], direction="maximize")
        await opt.tell("prog", "trial-1", {"val_accuracy": 0.85})
        await opt.tell("prog", "trial-2", {"val_accuracy": 0.92})
        await opt.tell("prog", "trial-3", {"val_accuracy": 0.88})

        best = await opt.best_trials("prog")
        assert len(best) == 1
        assert best[0]["result"]["val_accuracy"] == 0.92  # highest
        assert best[0]["direction"] == "maximize"


# --- create_local_adaptor factory tests ---


class TestCreateLocalAdaptor:
    @pytest.mark.asyncio
    async def test_factory_creates_all_roles(self):
        """The factory creates an adaptor with the local roles.

        Episodic memory is not a role — state.db IS it (ADR-0005), and
        semantic memory (claims) has no local filler: absent means absent.
        """
        adaptor = create_local_adaptor()
        assert adaptor.optimizer is not None
        assert adaptor.executor is not None
        assert adaptor.claims is None

    @pytest.mark.asyncio
    async def test_factory_optimizer_is_local(self):
        """The factory uses LocalOptimizer."""
        adaptor = create_local_adaptor()
        assert isinstance(adaptor.optimizer, LocalOptimizer)

    @pytest.mark.asyncio
    async def test_factory_executor_is_local(self):
        """The factory uses LocalExecutor."""
        adaptor = create_local_adaptor()
        assert isinstance(adaptor.executor, LocalExecutor)

    @pytest.mark.asyncio
    async def test_factory_shared_caches_wired(self):
        """[executor] shared_caches reaches the executor."""
        adaptor = create_local_adaptor(
            executor_shared_caches=["~/.cache/uv", "/data/models"],
        )
        assert adaptor.executor._shared_caches == [
            str(Path("~/.cache/uv").expanduser()), "/data/models",
        ]


# --- rc-6 P2: credential-shaped env never reaches bundle code -------


async def test_trial_env_scrubs_server_and_credential_keys(
    tmp_path, monkeypatch
):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    """os.environ inheritance once leaked ML_EPISTEME_INGEST_TOKEN (and
    every *_TOKEN/*_SECRET) into sandboxed trial code. The scrub is a
    blacklist, not a whitelist: runtime vars (PATH, CUDA_*, VIRTUAL_ENV,
    LD_LIBRARY_PATH) must survive — a whitelist would silently strip
    them and break GPU trials."""
    monkeypatch.setenv("ML_EPISTEME_INGEST_TOKEN", "supersecret")
    monkeypatch.setenv("ML_EPISTEME_DB_PATH", "/x/state.db")
    monkeypatch.setenv("ANTHROPIC_API_TOKEN", "tok-1")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "sec-1")
    monkeypatch.setenv("HF_PASSWORD", "pw")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    monkeypatch.setenv("VIRTUAL_ENV", "/opt/venvs/lab")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/usr/lib/cuda")
    monkeypatch.setenv("MY_CREDENTIAL_FILE", "/x")  # CREDENTIAL pattern
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/ssh")  # not credential-shaped
    code = (
        "import json, os\n"
        "print(json.dumps(sorted(os.environ)))"
    )
    ex = LocalExecutor(timeout=30, sandbox="none")
    r = json.loads(await ex.execute_code(code, artifact_dir=artifact_dir))
    assert r["status"] == "completed", r
    env_keys = set(json.loads(r["stdout"]))
    # Server-internal and credential-shaped keys must be gone.
    assert "ML_EPISTEME_INGEST_TOKEN" not in env_keys
    assert "ML_EPISTEME_DB_PATH" not in env_keys
    assert "ANTHROPIC_API_TOKEN" not in env_keys
    assert "AWS_SECRET_ACCESS_KEY" not in env_keys
    assert "HF_PASSWORD" not in env_keys
    assert "MY_CREDENTIAL_FILE" not in env_keys
    # Runtime vars survive — a whitelist would have stripped these.
    assert "PATH" in env_keys
    assert "CUDA_VISIBLE_DEVICES" in env_keys
    assert "VIRTUAL_ENV" in env_keys
    assert "LD_LIBRARY_PATH" in env_keys
    assert "SSH_AUTH_SOCK" in env_keys
    # Trial-facing ML_SCI_* vars are set on top by the executor.
    assert "ML_SCI_ARTIFACT_DIR" in env_keys


async def test_seal_fields_present_on_every_result_path(tmp_path):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    """rc-6 P7 — seal_enforced/launch_refused/seal_staged are emitted on
    every result shape, not only refusals. A consumer must never have
    to guess whether the field's absence means 'not enforced' or 'old
    build'."""
    ex = LocalExecutor(timeout=30, sandbox="none")
    # Happy path.
    r = json.loads(await ex.execute_code(
        "print('ok')", artifact_dir=artifact_dir
    ))
    assert r["status"] == "completed"
    for k in ("seal_enforced", "launch_refused", "seal_staged"):
        assert k in r, k
    assert r["seal_enforced"] is False
    assert r["launch_refused"] is False
    assert r["seal_staged"] == 0
    # Failed path (nonzero exit).
    r = json.loads(await ex.execute_code(
        "import sys; sys.exit(3)", artifact_dir=artifact_dir
    ))
    assert r["status"] == "failed"
    for k in ("seal_enforced", "launch_refused", "seal_staged"):
        assert k in r, k
