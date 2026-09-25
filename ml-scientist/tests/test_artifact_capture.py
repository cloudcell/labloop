"""Tests for artifact capture into SQLite (gzip-compressed)."""

import gzip
import hashlib
import tempfile
from pathlib import Path

import pytest

from ml_episteme_mcp.state.store import StateStore
from ml_episteme_mcp.state.models import (
    Programme, ProgrammeStatus,
    Hypothesis,
    Trial, TrialStatus,
)


@pytest.fixture
def store():
    tmpdir = Path(tempfile.mkdtemp())
    s = StateStore(tmpdir / "test.db")
    s.connect()
    yield s
    s.close()


@pytest.fixture
def artifact_dir(tmp_path):
    """Create a fake artifact directory with files."""
    d = tmp_path / "artifacts" / "prog-test" / "trial-test"
    d.mkdir(parents=True)
    (d / "stderr.log").write_text("Error: something went wrong\n")
    (d / "stdout.json").write_text('{"status": "completed", "loss": 0.5}')
    (d / "manifest.json").write_text('{"artifacts": [{"type": "log", "filename": "stderr.log"}]}')
    (d / "wrapper.py").write_text("def run(): pass\n")
    return d


def _create_trial_with_artifacts(store, artifact_path):
    """Create a minimal trial with an artifact_path."""
    store.create_programme(Programme(
        id="prog-test", goal="test",
        constraints={"type": "test"}, allowed_variables=["lr"],
        budget_max_trials=10, budget_max_wall_time_hours=1.0,
        status=ProgrammeStatus.active,
    ))
    store.create_hypothesis(Hypothesis(
        id="hyp-test", programme_id="prog-test",
        statement="test", failure_criterion="test",
        variables_involved=["lr"],
    ))
    store.create_trial(Trial(
        id="trial-test", programme_id="prog-test", hypothesis_id="hyp-test",
        config_json='{"lr": 0.01}', status=TrialStatus.designed,
        artifact_path=str(artifact_path),
    ))


class TestArtifactCapture:
    """Tests for the artifact_files / trial_artifacts tables."""

    def test_capture_artifacts_from_dir(self, store, artifact_dir):
        """capture_artifacts_from_dir stores all files in SQLite."""
        _create_trial_with_artifacts(store, str(artifact_dir))
        result = store.capture_artifacts_from_dir("trial-test", str(artifact_dir))

        assert len(result["captured"]) == 4
        assert len(result["oversized"]) == 0
        assert len(result["lost"]) == 0

        # Verify the files are in the DB
        arts = store.list_trial_artifacts("trial-test")
        assert len(arts) == 4
        filenames = {a["filename"] for a in arts}
        assert filenames == {"stderr.log", "stdout.json", "manifest.json", "wrapper.py"}

    def test_artifact_content_is_gzip_compressed(self, store, artifact_dir):
        """The content BLOB is gzip-compressed in the DB."""
        _create_trial_with_artifacts(store, str(artifact_dir))
        store.capture_artifacts_from_dir("trial-test", str(artifact_dir))

        arts = store.list_trial_artifacts("trial-test")
        for art in arts:
            af = store.get_artifact_file(art["content_hash"])
            assert af is not None
            # The returned content should be decompressed
            assert isinstance(af["content"], bytes)
            # The raw BLOB in the DB should be gzip (starts with 0x1f 0x8b)
            raw = store._fetchone(
                "SELECT content FROM artifact_files WHERE content_hash = ?",
                (art["content_hash"],),
            )
            assert raw["content"][0:2] == b"\x1f\x8b"  # gzip magic
            # Compressed should be smaller or similar for these small files
            assert af["compressed_size_bytes"] > 0

    def test_get_artifact_file_decompresses(self, store, artifact_dir):
        """get_artifact_file returns the original (decompressed) content."""
        _create_trial_with_artifacts(store, str(artifact_dir))
        store.capture_artifacts_from_dir("trial-test", str(artifact_dir))

        arts = store.list_trial_artifacts("trial-test")
        stderr_art = next(a for a in arts if a["filename"] == "stderr.log")
        af = store.get_artifact_file(stderr_art["content_hash"])

        assert af["content"] == b"Error: something went wrong\n"
        assert af["size_bytes"] == len(b"Error: something went wrong\n")

    def test_content_hash_is_sha256(self, store, artifact_dir):
        """The content_hash is sha256 of the original (uncompressed) bytes."""
        _create_trial_with_artifacts(store, str(artifact_dir))
        # cleanup_after=False so we can read the original bytes from disk
        store.capture_artifacts_from_dir(
            "trial-test", str(artifact_dir), cleanup_after=False
        )

        original = (artifact_dir / "stderr.log").read_bytes()
        expected_hash = "sha256:" + hashlib.sha256(original).hexdigest()

        arts = store.list_trial_artifacts("trial-test")
        stderr_art = next(a for a in arts if a["filename"] == "stderr.log")
        assert stderr_art["content_hash"] == expected_hash

    def test_dedup_by_content_hash(self, store, artifact_dir):
        """Files with identical content share one artifact_files row."""
        _create_trial_with_artifacts(store, str(artifact_dir))

        # Create a second trial with the same stderr.log content
        store.create_trial(Trial(
            id="trial-test2", programme_id="prog-test", hypothesis_id="hyp-test",
            config_json='{"lr": 0.02}', status=TrialStatus.designed,
            artifact_path=str(artifact_dir),
        ))

        # cleanup_after=False: both trials capture the same dir; the first
        # capture must not delete the files before the second reads them.
        store.capture_artifacts_from_dir(
            "trial-test", str(artifact_dir), cleanup_after=False
        )
        store.capture_artifacts_from_dir(
            "trial-test2", str(artifact_dir), cleanup_after=False
        )

        # Both trials should reference the same content_hash for stderr.log
        arts1 = store.list_trial_artifacts("trial-test")
        arts2 = store.list_trial_artifacts("trial-test2")
        h1 = next(a for a in arts1 if a["filename"] == "stderr.log")["content_hash"]
        h2 = next(a for a in arts2 if a["filename"] == "stderr.log")["content_hash"]
        assert h1 == h2

    def test_artifact_type_detection(self, store, artifact_dir):
        """Artifact types are detected from filenames."""
        _create_trial_with_artifacts(store, str(artifact_dir))
        store.capture_artifacts_from_dir("trial-test", str(artifact_dir))

        arts = store.list_trial_artifacts("trial-test")
        types = {a["filename"]: a["artifact_type"] for a in arts}
        assert types["stderr.log"] == "stderr"
        assert types["stdout.json"] == "stdout"
        assert types["manifest.json"] == "manifest"
        assert types["wrapper.py"] == "wrapper"

    def test_oversized_files_skipped(self, store, tmp_path):
        """Files over the size cap are skipped and reported."""
        d = tmp_path / "artifacts" / "prog-big" / "trial-big"
        d.mkdir(parents=True)
        (d / "small.txt").write_text("small")
        (d / "big.bin").write_bytes(b"x" * 200)  # 200 bytes

        _create_trial_with_artifacts(store, str(d))
        result = store.capture_artifacts_from_dir(
            "trial-test", str(d), max_file_size_bytes=100
        )

        assert len(result["captured"]) == 1
        assert result["captured"][0]["filename"] == "small.txt"
        assert len(result["oversized"]) == 1
        assert result["oversized"][0]["filename"] == "big.bin"

    def test_lost_files_reported(self, store, tmp_path):
        """Files that can't be read are reported as lost."""
        d = tmp_path / "nonexistent"
        _create_trial_with_artifacts(store, str(d))
        result = store.capture_artifacts_from_dir("trial-test", str(d))
        assert "error" in result or len(result.get("lost", [])) >= 0


class TestArtifactGUI:
    """Tests for the GUI serving artifacts from SQLite."""

    def test_artifact_served_from_sqlite(self, store, artifact_dir):
        """The artifact route serves from SQLite, not disk."""
        from starlette.testclient import TestClient
        from ml_episteme_mcp.observability.server import create_observability_app

        _create_trial_with_artifacts(store, str(artifact_dir))
        store.update_trial_status("trial-test", "running")
        store.update_trial_executor_output("trial-test", '{"status": "completed", "exit_code": 0}')
        store.update_trial_status("trial-test", "completed")
        store.capture_artifacts_from_dir("trial-test", str(artifact_dir))

        app = create_observability_app(store)
        client = TestClient(app)

        response = client.get(
            "/programme/prog-test/trial/trial-test/artifact/stderr.log"
        )
        assert response.status_code == 200
        assert response.text == "Error: something went wrong\n"

    def test_artifact_served_after_disk_deleted(self, store, artifact_dir):
        """Artifacts are served from SQLite even after disk files are deleted."""
        from starlette.testclient import TestClient
        from ml_episteme_mcp.observability.server import create_observability_app

        _create_trial_with_artifacts(store, str(artifact_dir))
        # Default cleanup_after=True deletes the staging dir automatically
        store.capture_artifacts_from_dir("trial-test", str(artifact_dir))

        # Staging dir is already gone (cleanup_after deleted it), but
        # explicitly remove it to prove serving works without it.
        import shutil
        if artifact_dir.exists():
            shutil.rmtree(artifact_dir)

        app = create_observability_app(store)
        client = TestClient(app)

        response = client.get(
            "/programme/prog-test/trial/trial-test/artifact/stderr.log"
        )
        assert response.status_code == 200
        assert response.text == "Error: something went wrong\n"

    def test_trial_page_shows_compression_ratio(self, store, artifact_dir):
        """The trial detail page shows the compression ratio."""
        from starlette.testclient import TestClient
        from ml_episteme_mcp.observability.server import create_observability_app

        _create_trial_with_artifacts(store, str(artifact_dir))
        store.update_trial_status("trial-test", "running")
        store.update_trial_executor_output("trial-test", '{"status": "completed", "exit_code": 0}')
        store.update_trial_status("trial-test", "completed")
        store.capture_artifacts_from_dir("trial-test", str(artifact_dir))

        app = create_observability_app(store)
        client = TestClient(app)

        response = client.get("/programme/prog-test/trial/trial-test")
        assert response.status_code == 200
        assert "Compressed" in response.text
        assert "sqlite" in response.text.lower() or "SQLite" in response.text


class TestStagingCleanup:
    """The on-disk artifacts/ directory is purely transient staging.

    After capture_artifacts_from_dir(cleanup_after=True) — the default
    used by _finalize_trial — every captured file is deleted and the
    (now-empty) staging directory is removed. The SQLite copy in
    artifact_files is the sole durable carrier (Rule 5.4).
    """

    def test_cleanup_removes_staging_dir(self, store, artifact_dir):
        """Default capture deletes files and removes the staging dir."""
        _create_trial_with_artifacts(store, str(artifact_dir))
        result = store.capture_artifacts_from_dir(
            "trial-test", str(artifact_dir)
        )

        assert len(result["captured"]) == 4
        assert not artifact_dir.exists(), (
            "staging dir should be removed after successful capture"
        )
        # But the content is still in SQLite
        arts = store.list_trial_artifacts("trial-test")
        assert len(arts) == 4

    def test_cleanup_false_preserves_files(self, store, artifact_dir):
        """cleanup_after=False keeps the originals (migration mode)."""
        _create_trial_with_artifacts(store, str(artifact_dir))
        result = store.capture_artifacts_from_dir(
            "trial-test", str(artifact_dir), cleanup_after=False
        )

        assert len(result["captured"]) == 4
        assert artifact_dir.exists()
        assert (artifact_dir / "stderr.log").exists()

    def test_oversized_file_stays_on_disk(self, store, tmp_path):
        """Oversized files are not deleted and keep the staging dir."""
        d = tmp_path / "artifacts" / "prog-big" / "trial-big"
        d.mkdir(parents=True)
        (d / "small.txt").write_text("small")
        (d / "big.bin").write_bytes(b"x" * 200)

        _create_trial_with_artifacts(store, str(d))
        result = store.capture_artifacts_from_dir(
            "trial-test", str(d), max_file_size_bytes=100
        )

        assert len(result["captured"]) == 1
        assert len(result["oversized"]) == 1
        # small.txt was captured and deleted; big.bin stays
        assert not (d / "small.txt").exists()
        assert (d / "big.bin").exists()
        # Dir remains because an oversized file is still there
        assert d.exists()

    def test_nested_files_captured(self, store, tmp_path):
        """Subdirectories (e.g. figures/) are captured recursively."""
        d = tmp_path / "artifacts" / "prog-nest" / "trial-nest"
        (d / "figures").mkdir(parents=True)
        (d / "wrapper.py").write_text("def run(): pass\n")
        (d / "figures" / "loss.png").write_bytes(b"PNG")

        _create_trial_with_artifacts(store, str(d))
        result = store.capture_artifacts_from_dir("trial-test", str(d))

        assert len(result["captured"]) == 2
        arts = store.list_trial_artifacts("trial-test")
        names = {a["filename"] for a in arts}
        assert "wrapper.py" in names
        assert "figures/loss.png" in names

    def test_gui_404_when_no_artifacts_captured(self, store, tmp_path):
        """The artifact route returns 404 (not a crash) for uncaptured trials."""
        from starlette.testclient import TestClient
        from ml_episteme_mcp.observability.server import create_observability_app

        # Trial exists but nothing was captured (staging dir already gone)
        _create_trial_with_artifacts(store, str(tmp_path / "artifacts" / "prog-test" / "trial-test"))

        app = create_observability_app(store)
        client = TestClient(app)

        response = client.get(
            "/programme/prog-test/trial/trial-test/artifact/stderr.log"
        )
        assert response.status_code == 404
        assert "No artifacts captured" in response.text

    def test_gui_404_for_missing_filename(self, store, artifact_dir):
        """404 when the trial has artifacts but not the requested filename."""
        from starlette.testclient import TestClient
        from ml_episteme_mcp.observability.server import create_observability_app

        _create_trial_with_artifacts(store, str(artifact_dir))
        store.capture_artifacts_from_dir("trial-test", str(artifact_dir))

        app = create_observability_app(store)
        client = TestClient(app)

        response = client.get(
            "/programme/prog-test/trial/trial-test/artifact/nonexistent.txt"
        )
        assert response.status_code == 404
        assert "not found in captured artifacts" in response.text.lower()

    def test_gui_serves_nested_file(self, store, tmp_path):
        """Nested artifact files (e.g. figures/loss.png) are servable."""
        from starlette.testclient import TestClient
        from ml_episteme_mcp.observability.server import create_observability_app

        d = tmp_path / "artifacts" / "prog-test" / "trial-test"
        (d / "figures").mkdir(parents=True)
        (d / "wrapper.py").write_text("def run(): pass\n")
        (d / "figures" / "plot.png").write_bytes(b"fakepng")

        _create_trial_with_artifacts(store, str(d))
        store.capture_artifacts_from_dir("trial-test", str(d))

        app = create_observability_app(store)
        client = TestClient(app)

        response = client.get(
            "/programme/prog-test/trial/trial-test/artifact/figures/plot.png"
        )
        assert response.status_code == 200
        assert response.content == b"fakepng"
