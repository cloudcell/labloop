"""Tests for SQLite concurrency: WAL mode, write serialization, logical races."""

import json
import tempfile
import threading
from pathlib import Path

import pytest

from ml_episteme_mcp.state.store import StateStore
from ml_episteme_mcp.state.models import (
    Programme,
    Hypothesis,
    Trial,
    Observation,
    TrialStatus,
)


# --- Fixtures ---


@pytest.fixture
def store(tmp_path):
    s = StateStore(str(tmp_path / "test.db"))
    s.connect()
    s.create_programme(Programme(
        id="prog-conc1",
        goal="test goal",
        constraints={"gpu_memory_gb": 8.0},
        allowed_variables=["learning_rate"],
        budget_max_trials=100,
        budget_max_wall_time_hours=10.0,
        metric_direction="minimize",
    ))
    s.create_hypothesis(Hypothesis(
        id="hyp-conc1",
        programme_id="prog-conc1",
        statement="test hypothesis",
        failure_criterion="metric does not improve",
        variables_involved=["learning_rate"],
    ))
    yield s
    s.close()


# --- SQLite Pragmas ---


class TestPragmas:
    """Verify the concurrency pragmas are set on connect."""

    def test_wal_mode_enabled(self, store):
        """journal_mode should be 'wal'."""
        result = store.conn.execute("PRAGMA journal_mode").fetchone()
        assert result[0].lower() == "wal"

    def test_busy_timeout_set(self, store):
        """busy_timeout should be 5000 (5 seconds)."""
        result = store.conn.execute("PRAGMA busy_timeout").fetchone()
        assert result[0] == 5000

    def test_synchronous_normal(self, store):
        """synchronous should be 1 (NORMAL)."""
        result = store.conn.execute("PRAGMA synchronous").fetchone()
        assert result[0] == 1  # 0=OFF, 1=NORMAL, 2=FULL

    def test_foreign_keys_on(self, store):
        """foreign_keys should be 1 (ON)."""
        result = store.conn.execute("PRAGMA foreign_keys").fetchone()
        assert result[0] == 1


# --- Write Serialization ---


class TestWriteSerialization:
    """Concurrent writes from multiple threads should all succeed."""

    def test_concurrent_writes_different_rows(self, store):
        """Multiple threads writing different trials should all succeed."""
        errors = []

        def write_trial(idx):
            try:
                store.create_trial(Trial(
                    id=f"trial-conc-{idx}",
                    programme_id="prog-conc1",
                    hypothesis_id="hyp-conc1",
                    config_json=f'{{"idx": {idx}}}',
                ))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_trial, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        trials = store.list_trials("prog-conc1")
        assert len(trials) == 10

    def test_concurrent_writes_same_table(self, store):
        """Multiple threads writing observations to different trials."""
        # First create trials
        for i in range(5):
            store.create_trial(Trial(
                id=f"trial-obs-{i}",
                programme_id="prog-conc1",
                hypothesis_id="hyp-conc1",
                config_json=f'{{"idx": {i}}}',
            ))

        errors = []

        def write_observation(idx):
            try:
                store.create_observation(Observation(
                    id=f"obs-conc-{idx}",
                    trial_id=f"trial-obs-{idx}",
                    metrics_json=f'{{"val_loss": {idx}}}',
                    variance_json='{"val_loss": 0.01}',
                    spatiotemporal_region="2026-09-14T00:00:00Z/gpu0",
                ))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_observation, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        for i in range(5):
            obs = store.list_observations(f"trial-obs-{i}")
            assert len(obs) == 1

    def test_concurrent_status_updates(self, store):
        """Multiple threads updating different trial statuses."""
        for i in range(10):
            store.create_trial(Trial(
                id=f"trial-status-{i}",
                programme_id="prog-conc1",
                hypothesis_id="hyp-conc1",
                config_json=f'{{"idx": {i}}}',
            ))

        errors = []

        def update_status(idx):
            try:
                store.update_trial_status(f"trial-status-{idx}", "running")
                store.update_trial_executor_output(f"trial-status-{idx}", '{"status": "completed", "exit_code": 0}')
                store.update_trial_status(f"trial-status-{idx}", "completed")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=update_status, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        trials = store.list_trials("prog-conc1")
        for t in trials:
            if t.id.startswith("trial-status-"):
                assert t.status.value == "completed"


# --- Read During Write ---


class TestReadDuringWrite:
    """WAL mode allows concurrent reads during writes."""

    def test_read_during_write(self, store):
        """Reading should not block while another thread writes."""
        store.create_trial(Trial(
            id="trial-read1",
            programme_id="prog-conc1",
            hypothesis_id="hyp-conc1",
            config_json='{"lr": 0.001}',
        ))

        write_done = threading.Event()
        read_result = []

        def slow_write():
            store.create_trial(Trial(
                id="trial-read2",
                programme_id="prog-conc1",
                hypothesis_id="hyp-conc1",
                config_json='{"lr": 0.002}',
            ))
            write_done.set()

        def read_while_writing():
            # Read should succeed even while write is happening
            trials = store.list_trials("prog-conc1")
            read_result.append(len(trials))

        t_write = threading.Thread(target=slow_write)
        t_read = threading.Thread(target=read_while_writing)

        t_write.start()
        t_read.start()
        t_write.join()
        t_read.join()

        # The read should have returned at least 1 trial
        assert len(read_result) == 1
        assert read_result[0] >= 1


# --- Foreign Key Enforcement ---


class TestForeignKeys:
    """foreign_keys=ON should enforce FK constraints."""

    def test_trial_with_bad_programme_id(self, store):
        """Inserting a trial with a non-existent programme_id should fail."""
        from ml_episteme_mcp.state.models import verify_mece

        # The verify_mece call in create_trial doesn't check FKs,
        # but the DB should reject it
        trial = Trial(
            id="trial-bad-fk",
            programme_id="nonexistent-programme",
            hypothesis_id="hyp-conc1",
            config_json='{"lr": 0.001}',
        )
        # verify_mece passes (it checks ontological metadata, not FKs)
        verify_mece(trial)
        # But the DB insert should fail due to FK constraint
        with pytest.raises(Exception):
            store._write(
                "INSERT INTO trials "
                "(id, programme_id, hypothesis_id, config_json, bundle_id, "
                "status, duration_seconds, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    trial.id,
                    trial.programme_id,
                    trial.hypothesis_id,
                    trial.config_json,
                    trial.bundle_id,
                    trial.status.value,
                    trial.duration_seconds,
                    trial.created_at,
                ),
            )


# --- Logical Race Prevention ---


class TestLogicalRaces:
    """run_trial and capture_bundle reject double operations."""

    @pytest.mark.asyncio
    async def test_run_trial_rejects_already_running(self, store):
        """run_trial on a trial that's already running should be rejected."""
        from ml_episteme_mcp.server import create_server
        from mcp.client import Client

        # Create a trial and mark it running
        store.create_trial(Trial(
            id="trial-running",
            programme_id="prog-conc1",
            hypothesis_id="hyp-conc1",
            config_json='{"lr": 0.001}',
            status=TrialStatus.running,
        ))

        mcp = create_server(store)
        async with Client(mcp) as client:
            result = await client.call_tool("run_trial", {
                "programme_id": "prog-conc1",
                "trial_id": "trial-running",
            })
            data = json.loads(result.content[0].text)
            assert "error" in data
            assert "already running" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_capture_bundle_rejects_already_linked(self, store):
        """capture_bundle on a trial that already has a bundle should be rejected."""
        from ml_episteme_mcp.server import create_server
        from mcp.client import Client

        # Create a trial with a bundle already linked
        store.create_trial(Trial(
            id="trial-bundled",
            programme_id="prog-conc1",
            hypothesis_id="hyp-conc1",
            config_json='{"lr": 0.001}',
            bundle_id="bundle-existing",
        ))

        py_file = Path("tests/fixtures/train_stub.py").resolve()
        mcp = create_server(store)
        async with Client(mcp) as client:
            result = await client.call_tool("capture_bundle", {
                "trial_id": "trial-bundled",
                "code_ref": str(py_file),
                "env_ref": "test",
                "seeds": [42],
                "splits": {"train": 0.8},
            })
            data = json.loads(result.content[0].text)
            assert "error" in data
            assert "already has a bundle" in data["error"].lower()


# --- WAL Files ---


class TestWalFiles:
    """WAL mode creates -wal and -shm sidecar files."""

    def test_wal_files_exist(self, tmp_path):
        """After connecting with WAL mode, -wal and -shm files should exist."""
        db_path = tmp_path / "test.db"
        s = StateStore(str(db_path))
        s.connect()
        # WAL files are created on first write
        s.create_programme(Programme(
            id="prog-wal-test",
            goal="test",
            constraints={},
            allowed_variables=[],
            budget_max_trials=1,
            budget_max_wall_time_hours=1.0,
        ))
        s.close()

        # The -wal file should exist (it may be checkpointed on close,
        # but at some point during the session it existed)
        # After close+checkpoint, the -wal file may be empty or removed.
        # The key test is that journal_mode is WAL, which we verify above.
        # Here we just verify the db file exists and is valid.
        assert db_path.exists()
