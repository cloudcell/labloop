"""Tests for infrastructure fixes: failure handling, async execution,
bundle validation, trial existence check, wall-time budget."""

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

import pytest

from ml_episteme_mcp.state.store import StateStore
from ml_episteme_mcp.state.models import (
    Programme,
    Hypothesis,
    Trial,
    Observation,
    Belief,
    TrialStatus,
)
from ml_episteme_mcp.clients.local_executor import LocalExecutor
from ml_episteme_mcp.enforcement.commitments import check_wall_time_exhausted
from ml_episteme_mcp.tools.trial import _validate_code_ref, _parse_executor_output


class _FakeAdaptor:
    """Minimal adaptor with just an executor — for auto-finalize tests."""
    def __init__(self, executor):
        self.executor = executor


# --- Fixtures ---


@pytest.fixture
def store(tmp_path):
    s = StateStore(str(tmp_path / "test.db"))
    s.connect()
    s.create_programme(Programme(
        id="prog-infra1",
        goal="test goal",
        constraints={"gpu_memory_gb": 8.0},
        allowed_variables=["learning_rate"],
        budget_max_trials=10,
        budget_max_wall_time_hours=1.0,
        metric_direction="minimize",
    ))
    s.create_hypothesis(Hypothesis(
        id="hyp-infra1",
        programme_id="prog-infra1",
        statement="test hypothesis",
        failure_criterion="metric does not improve",
        variables_involved=["learning_rate"],
    ))
    s.create_trial(Trial(
        id="trial-infra1",
        programme_id="prog-infra1",
        hypothesis_id="hyp-infra1",
        config_json='{"lr": 0.001}',
    ))
    yield s
    s.close()


@pytest.fixture
async def executor():
    exe = LocalExecutor(timeout=10)
    yield exe
    # Cleanup: cancel any lingering tasks (subprocesses) and await
    # them so their cancellation handlers run while the loop is open.
    pending = [
        task for task in exe._running_tasks.values()
        if task is not None and not task.done()
    ]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def test_executor_timeout_from_config():
    """[executor] timeout_seconds flows from the toml into
    LocalExecutor — trials legitimately run for hours, so the
    deadline is an operator knob, not a code constant."""
    from ml_episteme_mcp.config import create_adaptor_from_config

    adaptor = create_adaptor_from_config(
        {"executor": {"timeout_seconds": 7200}}
    )
    assert adaptor.executor._timeout == 7200
    # Env override wins
    import os
    os.environ["ML_EPISTEME_EXECUTOR_TIMEOUT"] = "9000"
    try:
        from ml_episteme_mcp.config import load_config
        cfg = load_config("/nonexistent")
        assert cfg["executor"]["timeout_seconds"] == 9000
    finally:
        del os.environ["ML_EPISTEME_EXECUTOR_TIMEOUT"]


# --- Step 1: Failure handling ---


class TestFailureHandling:
    """run_trial must mark trials as 'failed' when the executor reports failure."""

    @pytest.mark.asyncio
    async def test_parse_executor_output_failed(self):
        """_parse_executor_output parses 'failed' status."""
        output = json.dumps({"status": "failed", "exit_code": 1, "error": "crash"})
        data = _parse_executor_output(output)
        assert data["status"] == "failed"
        assert data["exit_code"] == 1

    @pytest.mark.asyncio
    async def test_parse_executor_output_timeout(self):
        """_parse_executor_output parses 'timeout' status."""
        output = json.dumps({"status": "timeout", "exit_code": -1})
        data = _parse_executor_output(output)
        assert data["status"] == "timeout"

    @pytest.mark.asyncio
    async def test_parse_executor_output_completed(self):
        """_parse_executor_output parses 'completed' status."""
        output = json.dumps({"status": "completed", "exit_code": 0, "stdout": "ok"})
        data = _parse_executor_output(output)
        assert data["status"] == "completed"

    @pytest.mark.asyncio
    async def test_executor_returns_duration(self, executor):
        """LocalExecutor includes duration_seconds in output."""
        code = "print('hello')"
        output = await executor.execute_code(code)
        data = json.loads(output)
        assert "duration_seconds" in data
        assert data["duration_seconds"] >= 0

    @pytest.mark.asyncio
    async def test_executor_failed_includes_duration(self, executor):
        """Failed execution still includes duration_seconds."""
        code = "import sys; sys.exit(1)"
        output = await executor.execute_code(code)
        data = json.loads(output)
        assert data["status"] == "failed"
        assert data["exit_code"] == 1
        assert "duration_seconds" in data


# --- Step 2: Async execution ---


class TestAsyncExecution:
    """LocalExecutor async dispatch, status polling, and cancellation."""

    @pytest.mark.asyncio
    async def test_execute_code_async_returns_running(self, executor):
        """execute_code_async returns status 'running' immediately."""
        code = "import time; time.sleep(0.3); print('done')"
        output = await executor.execute_code_async("trial-async1", code)
        data = json.loads(output)
        assert data["status"] == "running"
        # Wait for completion to avoid hanging
        await asyncio.sleep(0.5)
        executor.clear_async("trial-async1")

    @pytest.mark.asyncio
    async def test_get_async_status_running(self, executor):
        """get_async_status returns 'running' while task is active."""
        code = "import time; time.sleep(0.5)"
        await executor.execute_code_async("trial-async2", code)
        status = executor.get_async_status("trial-async2")
        data = json.loads(status)
        assert data["status"] == "running"
        # Wait for completion
        await asyncio.sleep(0.7)
        executor.clear_async("trial-async2")

    @pytest.mark.asyncio
    async def test_get_async_status_completed(self, executor):
        """get_async_status returns result when task finishes."""
        code = "print('done')"
        await executor.execute_code_async("trial-async3", code)
        # Wait for completion
        await asyncio.sleep(0.5)
        status = executor.get_async_status("trial-async3")
        data = json.loads(status)
        assert data["status"] == "completed"
        executor.clear_async("trial-async3")

    @pytest.mark.asyncio
    async def test_get_async_status_unknown(self, executor):
        """get_async_status returns 'unknown' for non-existent trial."""
        status = executor.get_async_status("nonexistent")
        data = json.loads(status)
        assert data["status"] == "unknown"

    @pytest.mark.asyncio
    async def test_cancel_async_running(self, executor):
        """cancel_async kills a running task."""
        code = "import time; time.sleep(5)"
        await executor.execute_code_async("trial-cancel1", code)
        await asyncio.sleep(0.1)  # Let it start
        result = await executor.cancel_async("trial-cancel1")
        data = json.loads(result)
        assert data["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_async_not_running(self, executor):
        """cancel_async returns error for non-existent task."""
        result = await executor.cancel_async("nonexistent")
        data = json.loads(result)
        assert data["status"] == "failed"

    @pytest.mark.asyncio
    async def test_cancel_async_already_done(self, executor):
        """cancel_async returns error for already-finished task."""
        code = "print('done')"
        await executor.execute_code_async("trial-done", code)
        await asyncio.sleep(0.5)
        result = await executor.cancel_async("trial-done")
        data = json.loads(result)
        assert data["status"] == "failed"
        executor.clear_async("trial-done")

    @pytest.mark.asyncio
    async def test_clear_async(self, executor):
        """clear_async removes task state."""
        code = "print('done')"
        await executor.execute_code_async("trial-clear", code)
        await asyncio.sleep(0.3)
        executor.clear_async("trial-clear")
        status = executor.get_async_status("trial-clear")
        data = json.loads(status)
        assert data["status"] == "unknown"


# --- Step 2b: Auto-finalization ---


class TestAutoFinalization:
    """Async trials are auto-finalized by a background task — the client
    doesn't need to poll for the trial to reach 'completed'."""

    @pytest.mark.asyncio
    async def test_finalize_trial_idempotent(self, store, executor):
        """_finalize_trial on an already-terminal trial doesn't raise."""
        from ml_episteme_mcp.tools.trial import _finalize_trial
        store.update_trial_status("trial-infra1", "running")
        store.update_trial_executor_output("trial-infra1", '{"status": "completed", "exit_code": 0}')
        store.update_trial_status("trial-infra1", "completed")
        # Calling _finalize_trial again should not raise
        output = json.dumps({"status": "completed", "exit_code": 0})
        result = _finalize_trial(store, "trial-infra1", output)
        data = result.structured_content
        assert data["status"] == "completed"

    @pytest.mark.asyncio
    async def test_auto_finalize_marks_completed(self, store, executor):
        """_auto_finalize marks a trial completed when the executor finishes."""
        from ml_episteme_mcp.tools.trial import _auto_finalize

        # Set up a trial in 'running' state
        store.update_trial_status("trial-infra1", "running")
        # Dispatch a quick async job
        code = "print('done')"
        await executor.execute_code_async("trial-infra1", code)
        # Start the auto-finalizer
        task = asyncio.create_task(
            _auto_finalize("trial-infra1", store, _FakeAdaptor(executor))
        )
        # Wait for the auto-finalizer to poll and finalize
        await asyncio.sleep(7.0)
        # The trial should be completed now
        trial = store.get_trial("trial-infra1")
        assert trial.status.value == "completed"
        task.cancel()  # clean up the background task
        await asyncio.gather(task, return_exceptions=True)
        executor.clear_async("trial-infra1")

    @pytest.mark.asyncio
    async def test_auto_finalize_marks_failed(self, store, executor):
        """_auto_finalize marks a trial failed when the executor fails."""
        from ml_episteme_mcp.tools.trial import _auto_finalize

        store.update_trial_status("trial-infra1", "running")
        # Dispatch a failing async job
        code = "import sys; sys.exit(1)"
        await executor.execute_code_async("trial-infra1", code)
        task = asyncio.create_task(
            _auto_finalize("trial-infra1", store, _FakeAdaptor(executor))
        )
        await asyncio.sleep(7.0)
        trial = store.get_trial("trial-infra1")
        assert trial.status.value == "failed"
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        executor.clear_async("trial-infra1")

    @pytest.mark.asyncio
    async def test_run_trial_rejects_completed_trial(self, store, executor):
        """run_trial rejects re-running a completed trial (terminal state)."""
        from ml_episteme_mcp.tools.trial import _finalize_trial
        # Mark the trial completed
        store.update_trial_status("trial-infra1", "running")
        store.update_trial_executor_output("trial-infra1", '{"status": "completed", "exit_code": 0}')
        store.update_trial_status("trial-infra1", "completed")
        # Try to finalize again — should be idempotent, not raise
        output = json.dumps({"status": "completed", "exit_code": 0})
        result = _finalize_trial(store, "trial-infra1", output)
        data = result.structured_content
        assert data["status"] == "completed"


# --- Step 3: Bundle validation ---


class TestBundleValidation:
    """capture_bundle validates code_ref at capture time."""

    def test_validate_code_ref_valid(self, tmp_path):
        """_validate_code_ref accepts a real .py file."""
        py_file = tmp_path / "train.py"
        py_file.write_text("def run_training(c): return {}")
        assert _validate_code_ref(str(py_file)) is None

    def test_validate_code_ref_nonexistent(self):
        """_validate_code_ref rejects non-existent path."""
        err = _validate_code_ref("/nonexistent/path.py")
        assert err is not None
        assert "does not exist" in err

    def test_validate_code_ref_non_py(self, tmp_path):
        """_validate_code_ref rejects non-.py files."""
        txt_file = tmp_path / "train.txt"
        txt_file.write_text("not python")
        err = _validate_code_ref(str(txt_file))
        assert err is not None
        assert ".py" in err

    def test_validate_code_ref_empty(self):
        """_validate_code_ref rejects empty string."""
        err = _validate_code_ref("")
        assert err is not None
        assert "empty" in err


# --- Step 4: Trial existence check ---


class TestTrialExistenceCheck:
    """capture_bundle rejects non-existent trials."""

    @pytest.mark.asyncio
    async def test_capture_bundle_nonexistent_trial(self, store):
        """capture_bundle on a non-existent trial returns an error."""
        from ml_episteme_mcp.server import create_server
        from mcp.client import Client

        mcp = create_server(store)
        py_file = Path("tests/fixtures/train_stub.py").resolve()

        async with Client(mcp) as client:
            result = await client.call_tool(
                "capture_bundle",
                {
                    "trial_id": "nonexistent-trial",
                    "code_ref": str(py_file),
                    "env_ref": "test",
                    "seeds": [42],
                    "splits": {"train": 0.8},
                },
            )
            text = result.content[0].text
            data = json.loads(text)
            assert "error" in data
            assert "not found" in data["error"].lower()


# --- Step 5: Wall-time budget enforcement ---


class TestWallTimeBudget:
    """check_wall_time_exhausted enforces wall-time budget."""

    def test_no_budget_set(self):
        """No wall-time budget (0) → allowed."""
        programme = Programme(
            id="p1", goal="g", constraints={}, allowed_variables=[],
            budget_max_trials=10, budget_max_wall_time_hours=0.0,
        )
        assert check_wall_time_exhausted(programme, []) is None

    def test_no_completed_trials(self):
        """No completed trials → allowed (can't estimate)."""
        programme = Programme(
            id="p1", goal="g", constraints={}, allowed_variables=[],
            budget_max_trials=10, budget_max_wall_time_hours=1.0,
        )
        trials = [Trial(
            id="t1", programme_id="p1", hypothesis_id="h1",
            config_json="{}", status=TrialStatus.running,
        )]
        assert check_wall_time_exhausted(programme, trials) is None

    def test_no_duration_data(self):
        """Completed trials but no duration_seconds → allowed."""
        programme = Programme(
            id="p1", goal="g", constraints={}, allowed_variables=[],
            budget_max_trials=10, budget_max_wall_time_hours=1.0,
        )
        trials = [Trial(
            id="t1", programme_id="p1", hypothesis_id="h1",
            config_json="{}", status=TrialStatus.completed,
            duration_seconds=None,
        )]
        assert check_wall_time_exhausted(programme, trials) is None

    def test_under_budget(self):
        """Total duration under budget → allowed."""
        programme = Programme(
            id="p1", goal="g", constraints={}, allowed_variables=[],
            budget_max_trials=10, budget_max_wall_time_hours=1.0,  # 3600s
        )
        trials = [Trial(
            id="t1", programme_id="p1", hypothesis_id="h1",
            config_json="{}", status=TrialStatus.completed,
            duration_seconds=600.0,  # 10 min
        )]
        assert check_wall_time_exhausted(programme, trials) is None

    def test_over_budget(self):
        """Total duration over budget → rejected."""
        programme = Programme(
            id="p1", goal="g", constraints={}, allowed_variables=[],
            budget_max_trials=10, budget_max_wall_time_hours=0.5,  # 1800s
        )
        trials = [
            Trial(
                id="t1", programme_id="p1", hypothesis_id="h1",
                config_json="{}", status=TrialStatus.completed,
                duration_seconds=1000.0,
            ),
            Trial(
                id="t2", programme_id="p1", hypothesis_id="h1",
                config_json="{}", status=TrialStatus.completed,
                duration_seconds=1000.0,
            ),
        ]
        err = check_wall_time_exhausted(programme, trials)
        assert err is not None
        assert "wall-time" in err.lower()

    def test_exactly_at_budget(self):
        """Total duration exactly at budget → rejected."""
        programme = Programme(
            id="p1", goal="g", constraints={}, allowed_variables=[],
            budget_max_trials=10, budget_max_wall_time_hours=1.0,  # 3600s
        )
        trials = [Trial(
            id="t1", programme_id="p1", hypothesis_id="h1",
            config_json="{}", status=TrialStatus.completed,
            duration_seconds=3600.0,
        )]
        err = check_wall_time_exhausted(programme, trials)
        assert err is not None

    def test_partial_duration_data(self):
        """Some trials have duration, some don't → uses available data."""
        programme = Programme(
            id="p1", goal="g", constraints={}, allowed_variables=[],
            budget_max_trials=10, budget_max_wall_time_hours=0.5,  # 1800s
        )
        trials = [
            Trial(
                id="t1", programme_id="p1", hypothesis_id="h1",
                config_json="{}", status=TrialStatus.completed,
                duration_seconds=1000.0,
            ),
            Trial(
                id="t2", programme_id="p1", hypothesis_id="h1",
                config_json="{}", status=TrialStatus.completed,
                duration_seconds=None,
            ),
        ]
        err = check_wall_time_exhausted(programme, trials)
        assert err is None  # 1000s < 1800s

    def test_none_programme(self):
        """None programme → error."""
        err = check_wall_time_exhausted(None, [])
        assert err is not None
        assert "not found" in err.lower()


# --- Step 6: Store duration tracking ---


class TestStoreDuration:
    """StateStore tracks trial duration."""

    def test_update_trial_duration(self, store):
        """update_trial_duration persists duration."""
        store.update_trial_duration("trial-infra1", 42.5)
        trial = store.get_trial("trial-infra1")
        assert trial.duration_seconds == 42.5

    def test_get_trial_includes_duration(self, store):
        """get_trial returns duration_seconds."""
        store.update_trial_duration("trial-infra1", 100.0)
        trial = store.get_trial("trial-infra1")
        assert trial.duration_seconds == 100.0

    def test_list_trials_includes_duration(self, store):
        """list_trials returns duration_seconds."""
        store.update_trial_duration("trial-infra1", 50.0)
        trials = store.list_trials("prog-infra1")
        assert len(trials) == 1
        assert trials[0].duration_seconds == 50.0

    def test_trial_without_duration(self, store):
        """Trial with no duration set returns None."""
        trial = store.get_trial("trial-infra1")
        assert trial.duration_seconds is None


class TestResolveEnvPython:
    """env_ref → interpreter resolution rules (tools/trial.py)."""

    def test_venv_dir_resolves_bin_python(self, tmp_path):
        from ml_episteme_mcp.tools.trial import _resolve_env_python
        venv = tmp_path / ".venv"
        (venv / "bin").mkdir(parents=True)
        py = venv / "bin" / "python"
        py.touch()
        assert _resolve_env_python(str(venv)) == str(py)

    def test_executable_file_resolves_to_itself(self, tmp_path):
        import os
        import stat
        from ml_episteme_mcp.tools.trial import _resolve_env_python
        exe = tmp_path / "python3.11"
        exe.touch()
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
        assert _resolve_env_python(str(exe)) == str(exe)

    def test_tool_version_string_is_not_an_env(self):
        from ml_episteme_mcp.tools.trial import _resolve_env_python
        assert _resolve_env_python("uv@0.10") is None
        assert _resolve_env_python(None) is None

    def test_missing_path_and_dir_without_python(self, tmp_path):
        from ml_episteme_mcp.tools.trial import _resolve_env_python
        assert _resolve_env_python(str(tmp_path / "nonexistent")) is None
        d = tmp_path / "empty_env"
        d.mkdir()
        assert _resolve_env_python(str(d)) is None

    def test_non_executable_file_rejected(self, tmp_path):
        from ml_episteme_mcp.tools.trial import _resolve_env_python
        f = tmp_path / "not_python"
        f.write_text("#!/bin/sh\n")
        f.chmod(0o644)
        assert _resolve_env_python(str(f)) is None


class TestReapOrphanedTrials:
    """Startup reaper: a restart leaves 'running' trials with no live
    executor task — they can never finalize and would block close."""

    def test_running_trial_reaped_to_failed(self, store):
        from ml_episteme_mcp.__main__ import reap_orphaned_trials
        store.update_trial_status("trial-infra1", "running")
        n = reap_orphaned_trials(store)
        assert n == 1
        t = store.get_trial("trial-infra1")
        assert t.status.value == "failed"
        assert t.finished_at is not None
        import json
        out = json.loads(t.executor_output_json)
        assert out["interrupted"] is True
        assert out["status"] == "failed"

    def test_non_running_trials_untouched(self, store):
        from ml_episteme_mcp.__main__ import reap_orphaned_trials
        n = reap_orphaned_trials(store)
        assert n == 0
        assert store.get_trial("trial-infra1").status.value == "designed"

    def test_terminal_trial_not_reaped(self, store):
        from ml_episteme_mcp.__main__ import reap_orphaned_trials
        store.update_trial_status("trial-infra1", "running")
        store.update_trial_executor_output("trial-infra1", '{"status": "completed", "exit_code": 0}')
        store.update_trial_status("trial-infra1", "completed")
        assert reap_orphaned_trials(store) == 0
        assert store.get_trial("trial-infra1").status.value == "completed"
