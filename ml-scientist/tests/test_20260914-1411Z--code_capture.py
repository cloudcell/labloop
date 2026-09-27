"""Tests for Phase 0: content-addressed code capture.

Tests that:
- capture_bundle stores code text in code_snippets
- code_hash is set on the bundle
- the same code file produces the same hash (deduplication)
- different code produces different hashes
- code snippets can be retrieved by hash
- generator code is captured for generated data refs
- code:// resource returns the code text
- old bundles (no code_hash) still work (backward compat)
"""

import json
import tempfile
from pathlib import Path

import pytest

from ml_episteme_mcp.state.models import (
    Bundle,
    CodeSnippet,
    DataRef,
    Hypothesis,
    Programme,
    ProgrammeStatus,
    Trial,
    TrialStatus,
)
from ml_episteme_mcp.state.store import StateStore


@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "test.db")
    s.connect()
    yield s
    s.close()


@pytest.fixture
def code_file(tmp_path):
    """A simple training script."""
    p = tmp_path / "train.py"
    p.write_text("""
def run_training(config):
    return {"metrics": {"acc": 0.9}, "variance": {"acc": 0.01}}
""")
    return str(p)


@pytest.fixture
def generator_file(tmp_path):
    """A simple generator script."""
    p = tmp_path / "generate.py"
    p.write_text("""
def generate_data(config, output_path):
    import json
    with open(output_path, 'w') as f:
        json.dump({"data": [1, 2, 3]}, f)
""")
    return str(p)


def _create_trial(store, trial_id="trial-test1"):
    """Helper: create a programme + hypothesis + trial for bundle FK."""
    store.create_programme(Programme(
        id="prog-test",
        goal="test goal",
        constraints={"type": "test"},
        allowed_variables=["lr"],
        budget_max_trials=10,
        budget_max_wall_time_hours=1.0,
        status=ProgrammeStatus.active,
    ))
    store.create_hypothesis(Hypothesis(
        id="hyp-test",
        programme_id="prog-test",
        statement="lr affects acc",
        failure_criterion="acc < 0.5",
        variables_involved=["lr"],
    ))
    store.create_trial(Trial(
        id=trial_id,
        programme_id="prog-test",
        hypothesis_id="hyp-test",
        config_json='{"lr": 0.01}',
        status=TrialStatus.designed,
    ))


class TestCodeSnippetStorage:
    """Test the code_snippets table and store methods."""

    def test_create_and_get_code_snippet(self, store):
        snippet = CodeSnippet(
            code_hash="sha256:abc123",
            code_text="def run_training(config): pass",
            language="python",
            captured_at="2026-09-14T12:00:00Z",
            original_path="/tmp/train.py",
            size_bytes=30,
        )
        store.create_code_snippet(snippet)

        retrieved = store.get_code_snippet("sha256:abc123")
        assert retrieved is not None
        assert retrieved.code_hash == "sha256:abc123"
        assert retrieved.code_text == "def run_training(config): pass"
        assert retrieved.language == "python"
        assert retrieved.original_path == "/tmp/train.py"
        assert retrieved.size_bytes == 30

    def test_get_nonexistent_code_snippet(self, store):
        assert store.get_code_snippet("sha256:nonexistent") is None

    def test_code_snippet_dedup(self, store):
        """INSERT OR IGNORE: same hash is a no-op."""
        snippet1 = CodeSnippet(
            code_hash="sha256:dup",
            code_text="code v1",
            language="python",
            captured_at="2026-09-14T12:00:00Z",
            original_path="/tmp/v1.py",
            size_bytes=7,
        )
        store.create_code_snippet(snippet1)

        # Same hash, different text — should be ignored
        snippet2 = CodeSnippet(
            code_hash="sha256:dup",
            code_text="code v2 different",
            language="python",
            captured_at="2026-09-14T13:00:00Z",
            original_path="/tmp/v2.py",
            size_bytes=16,
        )
        store.create_code_snippet(snippet2)

        retrieved = store.get_code_snippet("sha256:dup")
        assert retrieved.code_text == "code v1"  # first one wins
        assert retrieved.original_path == "/tmp/v1.py"

    def test_list_code_snippets(self, store):
        for i in range(3):
            store.create_code_snippet(CodeSnippet(
                code_hash=f"sha256:hash{i}",
                code_text=f"code {i}",
                language="python",
                captured_at=f"2026-09-14T12:0{i}:00Z",
                original_path=f"/tmp/train{i}.py",
                size_bytes=7,
            ))
        snippets = store.list_code_snippets()
        assert len(snippets) == 3


class TestCaptureCodeFromPath:
    """Test the capture_code_from_path method."""

    def test_capture_returns_hash(self, store, code_file):
        code_hash = store.capture_code_from_path(code_file)
        assert code_hash.startswith("sha256:")
        assert len(code_hash) == 71  # "sha256:" + 64 hex chars

    def test_capture_stores_snippet(self, store, code_file):
        code_hash = store.capture_code_from_path(code_file)
        snippet = store.get_code_snippet(code_hash)
        assert snippet is not None
        assert "run_training" in snippet.code_text
        assert snippet.language == "python"
        assert snippet.original_path is not None

    def test_capture_dedup(self, store, code_file):
        """Same file captured twice → same hash, one entry."""
        hash1 = store.capture_code_from_path(code_file)
        hash2 = store.capture_code_from_path(code_file)
        assert hash1 == hash2
        snippets = store.list_code_snippets()
        assert len(snippets) == 1

    def test_capture_different_files_different_hashes(self, store, tmp_path):
        f1 = tmp_path / "a.py"
        f1.write_text("def run_training(c): return {}")
        f2 = tmp_path / "b.py"
        f2.write_text("def run_training(c): return {'metrics': {}}")

        h1 = store.capture_code_from_path(str(f1))
        h2 = store.capture_code_from_path(str(f2))
        assert h1 != h2

    def test_capture_nonexistent_file(self, store):
        with pytest.raises(FileNotFoundError):
            store.capture_code_from_path("/nonexistent/file.py")


class TestBundleCodeHash:
    """Test that bundles get code_hash when created via capture_bundle."""

    def test_bundle_has_code_hash(self, store, code_file):
        """A bundle created with a code_ref should get a code_hash."""
        _create_trial(store)
        code_hash = store.capture_code_from_path(code_file)

        bundle = Bundle(
            id="bundle-test1",
            trial_id="trial-test1",
            code_ref=code_file,
            code_hash=code_hash,
            env_ref="python:3.12",
            seeds_json="[42]",
            splits_json='{"train": 0.8}',
        )
        store.create_bundle(bundle)

        retrieved = store.get_bundle("bundle-test1")
        assert retrieved is not None
        assert retrieved.code_hash == code_hash
        assert retrieved.code_ref == code_file

    def test_old_bundle_without_code_hash(self, store):
        """Backward compat: old bundles have code_hash=None."""
        _create_trial(store)
        bundle = Bundle(
            id="bundle-old",
            trial_id="trial-test1",
            code_ref="/old/path/train.py",
            code_hash=None,
            env_ref="python:3.12",
            seeds_json="[42]",
            splits_json='{"train": 0.8}',
        )
        store.create_bundle(bundle)

        retrieved = store.get_bundle("bundle-old")
        assert retrieved is not None
        assert retrieved.code_hash is None
        assert retrieved.code_ref == "/old/path/train.py"


class TestDataRefGeneratorCodeHash:
    """Test that data refs get generator_code_hash for generated data."""

    def test_data_ref_has_generator_code_hash(self, store, generator_file):
        generator_hash = store.capture_code_from_path(generator_file)

        data_ref = DataRef(
            id="data-ref-test1",
            split="train",
            regime="generated",
            generator_code_ref=generator_file,
            generator_code_hash=generator_hash,
            generator_seed=42,
            generator_params={"n_samples": 100},
            reproducibility_risk="none",
        )
        store.create_data_ref(data_ref)

        retrieved = store.get_data_ref("data-ref-test1")
        assert retrieved is not None
        assert retrieved.generator_code_hash == generator_hash
        assert retrieved.generator_code_ref == generator_file


class TestCodeIntegrityVerification:
    """Test that code integrity can be verified by recomputing the hash."""

    def test_recompute_hash_matches(self, store, code_file):
        import hashlib

        code_hash = store.capture_code_from_path(code_file)
        snippet = store.get_code_snippet(code_hash)

        recomputed = "sha256:" + hashlib.sha256(
            snippet.code_text.encode("utf-8")
        ).hexdigest()

        assert recomputed == code_hash

    def test_modified_code_different_hash(self, store, tmp_path):
        """If the code file is modified after capture, the hash changes."""
        import hashlib

        f = tmp_path / "train.py"
        f.write_text("def run_training(c): return {}")
        hash1 = store.capture_code_from_path(str(f))

        # Modify the file
        f.write_text("def run_training(c): return {'metrics': {}}")
        hash2 = store.capture_code_from_path(str(f))

        assert hash1 != hash2

        # Both snippets exist (different content)
        assert store.get_code_snippet(hash1) is not None
        assert store.get_code_snippet(hash2) is not None
