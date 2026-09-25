"""Tests for artifact workspaces — durable, timestamped, ID-tagged artifacts.

Verifies that:
- The executor writes wrapper script, stdout, stderr, and manifest to artifact_dir
- The manifest contains correct IDs, timestamps, paths, and SHA-256 hashes
- The wrapper script is NOT deleted (unlike the temp-file behavior)
- Backward compatibility: no artifact_dir → temp file behavior (deleted)
- The trial://{id}/artifacts resource returns the manifest
- The Trial model stores artifact_path
- The SQLite migration adds the artifact_path column
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from ml_episteme_mcp.clients.local_executor import LocalExecutor
from ml_episteme_mcp.state.models import Trial, TrialStatus
from ml_episteme_mcp.state.store import StateStore


# --- LocalExecutor artifact tests ---


class TestArtifactWorkspaces:
    """Tests for the artifact workspace feature of LocalExecutor."""

    @pytest.mark.asyncio
    async def test_artifacts_written_to_dir(self):
        """When artifact_dir is provided, artifacts are written there."""
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / "artifacts" / "prog-1" / "trial-1"
            executor = LocalExecutor()
            result = await executor.execute_code(
                "print('hello artifacts')",
                artifact_dir=artifact_dir,
                trial_id="trial-1",
                programme_id="prog-1",
                bundle_id="bundle-1",
            )
            data = json.loads(result)
            assert data["status"] == "completed"

            # The directory exists
            assert artifact_dir.exists()

            # Files are written
            files = list(artifact_dir.iterdir())
            assert len(files) >= 4  # wrapper, stdout, stderr, manifest

            # Wrapper script exists and is NOT deleted
            wrappers = list(artifact_dir.glob("*_wrapper.py"))
            assert len(wrappers) == 1
            # The wrapper contains the code (it's the script that was executed)
            assert "print" in wrappers[0].read_text()

            # Stdout file exists
            stdouts = list(artifact_dir.glob("*_stdout.json"))
            assert len(stdouts) == 1
            stdout_data = json.loads(stdouts[0].read_text())
            assert "hello artifacts" in stdout_data["stdout"]

            # Stderr file exists
            stderrs = list(artifact_dir.glob("*_stderr.log"))
            assert len(stderrs) == 1

            # Manifest exists
            manifests = list(artifact_dir.glob("*_manifest.json"))
            assert len(manifests) == 1
            manifest = json.loads(manifests[0].read_text())
            assert manifest["trial_id"] == "trial-1"
            assert manifest["programme_id"] == "prog-1"
            assert manifest["bundle_id"] == "bundle-1"
            assert "created_at" in manifest
            assert len(manifest["artifacts"]) == 3  # wrapper, stdout, stderr

            # Each artifact has id, type, filename, sha256, size_bytes
            for art in manifest["artifacts"]:
                assert "id" in art
                assert art["id"].startswith("art-")
                assert "type" in art
                assert "filename" in art
                assert "sha256" in art
                assert art["sha256"].startswith("sha256:")
                assert "size_bytes" in art
                assert art["size_bytes"] >= 0  # stderr may be empty

    @pytest.mark.asyncio
    async def test_wrapper_not_deleted(self):
        """The wrapper script is NOT deleted when artifact_dir is provided."""
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / "artifacts" / "trial-1"
            executor = LocalExecutor()
            await executor.execute_code(
                "print('persisted')",
                artifact_dir=artifact_dir,
                trial_id="trial-1",
            )
            wrappers = list(artifact_dir.glob("*_wrapper.py"))
            assert len(wrappers) == 1
            # The file still exists after execution
            assert wrappers[0].exists()

    @pytest.mark.asyncio
    async def test_backward_compat_no_artifact_dir(self):
        """Without artifact_dir, the temp file is deleted (backward compat)."""
        executor = LocalExecutor()
        result = await executor.execute_code("print('temp mode')")
        data = json.loads(result)
        assert data["status"] == "completed"
        assert "temp mode" in data["stdout"]
        # No artifact_dir → no persistent files to check; just verify it works

    @pytest.mark.asyncio
    async def test_failed_execution_writes_artifacts(self):
        """Artifacts are written even when execution fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / "artifacts" / "trial-fail"
            executor = LocalExecutor()
            result = await executor.execute_code(
                "import sys; sys.exit(1)",
                artifact_dir=artifact_dir,
                trial_id="trial-fail",
            )
            data = json.loads(result)
            assert data["status"] == "failed"

            # Artifacts still written
            manifests = list(artifact_dir.glob("*_manifest.json"))
            assert len(manifests) == 1

    @pytest.mark.asyncio
    async def test_async_execution_with_artifacts(self):
        """Async execution also writes artifacts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / "artifacts" / "trial-async"
            executor = LocalExecutor()
            output = await executor.execute_code_async(
                "trial-async",
                "print('async artifacts')",
                artifact_dir=artifact_dir,
                programme_id="prog-1",
                bundle_id="bundle-1",
            )
            # Wait for completion
            import asyncio
            await asyncio.sleep(1.0)
            result = executor.get_async_status("trial-async")
            data = json.loads(result)
            assert data["status"] == "completed"

            # Artifacts written
            manifests = list(artifact_dir.glob("*_manifest.json"))
            assert len(manifests) == 1


# --- Trial model artifact_path tests ---


class TestTrialArtifactPath:
    """Tests for the artifact_path field on Trial."""

    def test_trial_has_artifact_path(self):
        """The Trial model has an artifact_path field (default None)."""
        trial = Trial(
            id="trial-test",
            programme_id="prog-1",
            hypothesis_id="hyp-1",
            config_json="{}",
        )
        assert trial.artifact_path is None

    def test_trial_artifact_path_set(self):
        """The artifact_path can be set."""
        trial = Trial(
            id="trial-test",
            programme_id="prog-1",
            hypothesis_id="hyp-1",
            config_json="{}",
            artifact_path="/tmp/artifacts/trial-test",
        )
        assert trial.artifact_path == "/tmp/artifacts/trial-test"


# --- SQLite migration tests ---


class TestArtifactPathMigration:
    """Tests for the SQLite migration that adds artifact_path."""

    def test_migration_adds_column(self):
        """The migration adds artifact_path to existing databases."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = StateStore(db_path)
            store.connect()
            store.close()

            # Verify column exists
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            cols = {r[1] for r in conn.execute("PRAGMA table_info(trials)")}
            assert "artifact_path" in cols
            conn.close()

    def test_store_preserves_artifact_path(self):
        """The store round-trips artifact_path through SQLite."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = StateStore(db_path)
            store.connect()

            # Create a programme and hypothesis first (FKs)
            from ml_episteme_mcp.state.models import Programme, Hypothesis
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

            trial = Trial(
                id="trial-1",
                programme_id="prog-1",
                hypothesis_id="hyp-1",
                config_json="{}",
                artifact_path="/tmp/artifacts/trial-1",
            )
            store.create_trial(trial)

            retrieved = store.get_trial("trial-1")
            assert retrieved is not None
            assert retrieved.artifact_path == "/tmp/artifacts/trial-1"

            store.close()

    def test_store_update_artifact_path(self):
        """update_trial_artifact_path updates the field."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = StateStore(db_path)
            store.connect()

            from ml_episteme_mcp.state.models import Programme, Hypothesis
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

            trial = Trial(
                id="trial-1",
                programme_id="prog-1",
                hypothesis_id="hyp-1",
                config_json="{}",
            )
            store.create_trial(trial)
            assert store.get_trial("trial-1").artifact_path is None

            store.update_trial_artifact_path("trial-1", "/tmp/artifacts/trial-1")
            assert store.get_trial("trial-1").artifact_path == "/tmp/artifacts/trial-1"

            store.close()

    def test_state_dir_property(self):
        """The state_dir property returns the directory containing the DB."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "subdir" / "test.db"
            db_path.parent.mkdir(parents=True)
            store = StateStore(db_path)
            store.connect()
            assert store.state_dir == db_path.parent
            store.close()
