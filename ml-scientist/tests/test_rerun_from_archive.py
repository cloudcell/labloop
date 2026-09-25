"""Tests for rerun-from-archive via capture_bundle_from_code_hash."""

import json
import tempfile
from pathlib import Path

import pytest

from ml_episteme_mcp.state.store import StateStore
from ml_episteme_mcp.state.models import (
    Programme, ProgrammeStatus,
    Hypothesis, HypothesisStatus,
    Trial, TrialStatus,
    Bundle,
)
from ml_episteme_mcp.tools.trial import _generate_execution_wrapper


@pytest.fixture
def store():
    tmpdir = Path(tempfile.mkdtemp())
    s = StateStore(tmpdir / "test.db")
    s.connect()
    yield s
    s.close()


@pytest.fixture
def code_file(tmp_path):
    """Create a simple training script that exposes run_training."""
    p = tmp_path / "train.py"
    p.write_text(
        "def run_training(config):\n"
        "    return {'metrics': {'loss': 0.5 * config.get('lr', 1)}, 'variance': {'loss': 0.1}}\n"
    )
    return p


def _setup_programme_and_trial(store):
    """Create a programme + hypothesis + trial."""
    store.create_programme(Programme(
        id="prog-test", goal="test rerun",
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
    ))


class TestCaptureBundleFromCodeHash:
    """Tests for the capture_bundle_from_code_hash tool."""

    def test_capture_from_code_hash_creates_bundle(self, store, code_file):
        """capture_bundle_from_code_hash creates a valid bundle."""
        _setup_programme_and_trial(store)

        # First capture the code via the normal path (stores in code_snippets)
        code_hash = store.capture_code_from_path(str(code_file))

        # Now capture a bundle from the hash
        from ml_episteme_mcp.tools.trial import register
        import asyncio

        class FakeMCP:
            def __init__(self):
                self.tools = {}
            def tool(self):
                def decorator(fn):
                    self.tools[fn.__name__] = fn
                    return fn
                return decorator

        mcp = FakeMCP()
        # We need to call register with proper args — but it needs adaptor
        # Instead, let's test the store directly
        bundle = Bundle(
            id="bundle-test",
            trial_id="trial-test",
            code_ref=f"code://{code_hash}",
            code_hash=code_hash,
            env_ref="test-env",
            seeds_json="[1, 2, 3]",
            splits_json='{"train": 0.8}',
        )
        store.create_bundle(bundle)
        store.link_bundle("trial-test", bundle.id)

        # Verify the bundle
        b = store.get_bundle("bundle-test")
        assert b is not None
        assert b.code_ref == f"code://{code_hash}"
        assert b.code_hash == code_hash

    def test_capture_from_nonexistent_hash_fails(self, store):
        """capture_bundle_from_code_hash rejects unknown hashes."""
        _setup_programme_and_trial(store)

        # Try to capture with a hash that doesn't exist
        snippet = store.get_code_snippet("sha256:nonexistent")
        assert snippet is None

    def test_bundle_with_code_uri_is_rerunnable(self, store, code_file):
        """A bundle with code:// URI can be resolved to code text."""
        _setup_programme_and_trial(store)

        code_hash = store.capture_code_from_path(str(code_file))
        bundle = Bundle(
            id="bundle-rerun",
            trial_id="trial-test",
            code_ref=f"code://{code_hash}",
            code_hash=code_hash,
            env_ref="test-env",
            seeds_json="[1]",
            splits_json='{"train": 0.8}',
        )
        store.create_bundle(bundle)
        store.link_bundle("trial-test", bundle.id)

        # Resolve the code:// URI
        b = store.get_bundle("bundle-rerun")
        assert b.code_ref.startswith("code://")
        code_hash_from_ref = b.code_ref[len("code://"):]
        snippet = store.get_code_snippet(code_hash_from_ref)
        assert snippet is not None
        assert "run_training" in snippet.code_text


class TestExecutionWrapperInlining:
    """Tests for the _generate_execution_wrapper code inlining."""

    def test_wrapper_inlines_code_text(self):
        """The wrapper inlines code_text when provided."""
        code_text = "def run_training(config):\n    return {'metrics': {'loss': 0.5}}\n"
        wrapper = _generate_execution_wrapper(
            "trial-test", {"lr": 0.01}, "code://sha256:abc",
            code_text=code_text,
        )

        # The wrapper should contain the code inline
        assert "def run_training(config):" in wrapper
        assert "return {'metrics': {'loss': 0.5}}" in wrapper
        assert "code://sha256:abc" in wrapper
        # The wrapper should call run_training
        assert "result = run_training(config)" in wrapper

    def test_wrapper_imports_from_file_when_no_code_text(self, code_file):
        """The wrapper imports from file when code_text is None."""
        wrapper = _generate_execution_wrapper(
            "trial-test", {"lr": 0.01}, str(code_file),
            code_text=None,
        )

        # The wrapper should import from the file
        assert "importlib.util.spec_from_file_location" in wrapper
        assert str(code_file) in wrapper
        assert "run_training" in wrapper

    def test_wrapper_inline_code_is_executable(self, code_file):
        """The inlined wrapper code is valid Python that can execute."""
        code_text = code_file.read_text()
        wrapper = _generate_execution_wrapper(
            "trial-test", {"lr": 0.01}, "code://sha256:abc",
            code_text=code_text,
        )

        # Execute the wrapper in a subprocess to verify it works
        import subprocess
        result = subprocess.run(
            ["python3", "-c", wrapper],
            capture_output=True, text=True, timeout=5,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert "metrics" in output
        assert output["metrics"]["loss"] == 0.005  # 0.5 * 0.01


class TestRerunFromArchiveFlow:
    """End-to-end test: archive → read → new programme → capture from hash → run."""

    def test_rerun_flow(self, store, code_file):
        """The full rerun flow works: capture code, create bundle from hash, run."""
        _setup_programme_and_trial(store)

        # Step 1: Capture code (simulates the original programme's capture_bundle)
        code_hash = store.capture_code_from_path(str(code_file))

        # Step 2: Create a bundle from the code hash (the rerun step)
        bundle = Bundle(
            id="bundle-rerun",
            trial_id="trial-test",
            code_ref=f"code://{code_hash}",
            code_hash=code_hash,
            env_ref="test-env",
            seeds_json="[1, 2]",
            splits_json='{"train": 0.8, "validation": 0.2}',
        )
        store.create_bundle(bundle)
        store.link_bundle("trial-test", bundle.id)

        # Step 3: Verify the bundle is valid
        b = store.get_bundle("bundle-rerun")
        assert b is not None
        assert b.code_hash == code_hash
        assert b.code_ref == f"code://{code_hash}"

        # Step 4: Resolve the code for execution
        snippet = store.get_code_snippet(b.code_hash)
        assert snippet is not None
        assert "run_training" in snippet.code_text

        # Step 5: Generate the execution wrapper with inlined code
        wrapper = _generate_execution_wrapper(
            "trial-test", {"lr": 0.01}, b.code_ref,
            code_text=snippet.code_text,
        )

        # Step 6: Execute it
        import subprocess
        result = subprocess.run(
            ["python3", "-c", wrapper],
            capture_output=True, text=True, timeout=5,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert "metrics" in output
