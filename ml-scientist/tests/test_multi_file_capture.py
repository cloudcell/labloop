"""Tests for multi-file code capture (recursive local import capture).

Tests that:
- capture_code_with_imports captures the primary file AND local imports
- extra hashes are stored in code_snippets
- extra hashes are returned alongside the primary hash
- stdlib and third-party imports are skipped
- recursive imports (A imports B imports C) are captured
- bundles store code_hash_extra_json
- the execution wrapper inlines all code files
"""

import json
import tempfile
from pathlib import Path

import pytest

from ml_episteme_mcp.state.models import (
    Bundle,
    CodeSnippet,
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
def multi_file_project(tmp_path):
    """A project with executor.py importing from local modules.

    structure:
        executor.py   → imports model.py and data.py
        model.py      → imports utils.py
        data.py       → self-contained
        utils.py      → self-contained
        third_party.py → NOT imported (should not be captured)
    """
    (tmp_path / "executor.py").write_text("""
from model import build_model
from data import load_data
import json  # stdlib — should be skipped

def run_training(config):
    model = build_model(config)
    data = load_data(config)
    return {"metrics": {"acc": 0.9}, "variance": {"acc": 0.01}}
""")
    (tmp_path / "model.py").write_text("""
from utils import helper

def build_model(config):
    helper()
    return {"layers": config.get("depth", 3)}
""")
    (tmp_path / "data.py").write_text("""
def load_data(config):
    return {"x": [1, 2, 3]}
""")
    (tmp_path / "utils.py").write_text("""
def helper():
    return 42
""")
    (tmp_path / "third_party.py").write_text("""
# This file is NOT imported by anything — should not be captured.
def unused():
    pass
""")
    return tmp_path


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


class TestCaptureCodeWithImports:
    """Test the capture_code_with_imports method."""

    def test_captures_primary_file(self, store, multi_file_project):
        """The primary file (executor.py) is captured."""
        primary_hash, extra_hashes = store.capture_code_with_imports(
            str(multi_file_project / "executor.py")
        )
        assert primary_hash.startswith("sha256:")
        snippet = store.get_code_snippet(primary_hash)
        assert snippet is not None
        assert "run_training" in snippet.code_text

    def test_captures_local_imports(self, store, multi_file_project):
        """Local imports (model.py, data.py) are captured as extra hashes."""
        primary_hash, extra_hashes = store.capture_code_with_imports(
            str(multi_file_project / "executor.py")
        )
        # Should have captured model.py and data.py (direct imports)
        # and utils.py (recursive import from model.py)
        assert len(extra_hashes) >= 3, (
            f"Expected at least 3 extra hashes (model, data, utils), "
            f"got {len(extra_hashes)}: {extra_hashes}"
        )

        # All extra hashes should be in code_snippets
        for h in extra_hashes:
            snippet = store.get_code_snippet(h)
            assert snippet is not None, f"Code snippet not found for hash {h}"

    def test_recursive_imports_captured(self, store, multi_file_project):
        """Recursive imports (model.py imports utils.py) are captured."""
        primary_hash, extra_hashes = store.capture_code_with_imports(
            str(multi_file_project / "executor.py")
        )
        # Find the snippet that contains 'def helper'
        all_snippets = [store.get_code_snippet(h) for h in extra_hashes]
        has_utils = any(
            s is not None and "def helper" in s.code_text
            for s in all_snippets
        )
        assert has_utils, "utils.py (recursive import) was not captured"

    def test_stdlib_imports_skipped(self, store, multi_file_project):
        """Stdlib imports (json) are not captured as code files."""
        primary_hash, extra_hashes = store.capture_code_with_imports(
            str(multi_file_project / "executor.py")
        )
        # json is stdlib — there should be no snippet for it
        import hashlib
        json_hash = "sha256:" + hashlib.sha256(b"import json").hexdigest()
        assert json_hash not in extra_hashes

    def test_unimported_files_not_captured(self, store, multi_file_project):
        """Files that exist but are not imported are not captured."""
        primary_hash, extra_hashes = store.capture_code_with_imports(
            str(multi_file_project / "executor.py")
        )
        # third_party.py is not imported — should not be in extra_hashes
        all_snippets = [store.get_code_snippet(h) for h in extra_hashes]
        has_third_party = any(
            s is not None and "def unused" in s.code_text
            for s in all_snippets
        )
        assert not has_third_party, "third_party.py was captured but not imported"

    def test_single_file_no_imports(self, store, tmp_path):
        """A self-contained file with no local imports returns no extras."""
        p = tmp_path / "train.py"
        p.write_text("def run_training(config): return {'metrics': {}}")
        primary_hash, extra_hashes = store.capture_code_with_imports(str(p))
        assert primary_hash.startswith("sha256:")
        assert extra_hashes == []

    def test_dedup_across_recursive_captures(self, store, tmp_path):
        """If A imports B and C, and B also imports C, C is captured once."""
        (tmp_path / "a.py").write_text("""
from b import f_b
from c import f_c
def run_training(config):
    return {"metrics": {"acc": f_b() + f_c()}}
""")
        (tmp_path / "b.py").write_text("""
from c import f_c
def f_b():
    return f_c() + 1
""")
        (tmp_path / "c.py").write_text("""
def f_c():
    return 42
""")
        primary_hash, extra_hashes = store.capture_code_with_imports(
            str(tmp_path / "a.py")
        )
        # Should have exactly 2 extra hashes (b.py and c.py), not 3
        assert len(extra_hashes) == 2, (
            f"Expected 2 extra hashes (b and c), got {len(extra_hashes)}: "
            f"{extra_hashes}"
        )


class TestBundleCodeHashExtra:
    """Test that bundles store code_hash_extra_json."""

    def test_bundle_has_code_hash_extra_json(self, store, multi_file_project):
        """A bundle created with a multi-file code_ref gets code_hash_extra_json."""
        _create_trial(store)
        code_ref = str(multi_file_project / "executor.py")
        primary_hash, extra_hashes = store.capture_code_with_imports(code_ref)

        bundle = Bundle(
            id="bundle-test1",
            trial_id="trial-test1",
            code_ref=code_ref,
            code_hash=primary_hash,
            code_hash_extra_json=json.dumps(extra_hashes),
            env_ref="python:3.12",
            seeds_json="[42]",
            splits_json='{"train": 0.8}',
        )
        store.create_bundle(bundle)

        retrieved = store.get_bundle("bundle-test1")
        assert retrieved is not None
        assert retrieved.code_hash == primary_hash
        assert retrieved.code_hash_extra_json is not None
        stored_extras = json.loads(retrieved.code_hash_extra_json)
        assert len(stored_extras) >= 3
        assert set(stored_extras) == set(extra_hashes)

    def test_old_bundle_without_code_hash_extra(self, store):
        """Backward compat: old bundles have code_hash_extra_json=None."""
        _create_trial(store)
        bundle = Bundle(
            id="bundle-old",
            trial_id="trial-test1",
            code_ref="/old/path/train.py",
            code_hash=None,
            code_hash_extra_json=None,
            env_ref="python:3.12",
            seeds_json="[42]",
            splits_json='{"train": 0.8}',
        )
        store.create_bundle(bundle)

        retrieved = store.get_bundle("bundle-old")
        assert retrieved is not None
        assert retrieved.code_hash_extra_json is None


class TestExecutionWrapperMultiFile:
    """Test that _generate_execution_wrapper materializes dep files."""

    def test_wrapper_materializes_dep_code(self):
        from ml_episteme_mcp.tools.trial import _generate_execution_wrapper

        primary_code = "def run_training(config): return {'metrics': model()}"
        extra_code_1 = "def model(): return {'acc': 0.9}"
        extra_code_2 = "def helper(): return 42"

        wrapper = _generate_execution_wrapper(
            "trial-test",
            {"lr": 0.01},
            "code://sha256:abc",
            code_text=primary_code,
            dep_snippets=[
                ("/proj/model.py", extra_code_1),
                ("/proj/helper.py", extra_code_2),
            ],
        )

        # Deps are materialized as real files under _deps/, not inlined
        assert "def run_training" in wrapper
        assert "_deps_files" in wrapper
        assert "model.py" in wrapper
        assert "helper.py" in wrapper
        assert "sys.path.insert" in wrapper

    def test_wrapper_without_extras(self):
        from ml_episteme_mcp.tools.trial import _generate_execution_wrapper

        primary_code = "def run_training(config): return {'metrics': {}}"

        wrapper = _generate_execution_wrapper(
            "trial-test",
            {"lr": 0.01},
            "code://sha256:abc",
            code_text=primary_code,
            dep_snippets=None,
        )

        assert "def run_training" in wrapper
        assert "_deps_files" not in wrapper

    def test_wrapper_dotted_import_resolves(self):
        """Regression: `from pkg.mod import x` inside inlined primary must
        resolve against materialized dep files — inlining dep text could
        never satisfy the import machinery."""
        import subprocess

        from ml_episteme_mcp.tools.trial import _generate_execution_wrapper

        primary = (
            "from src.trainer import compute\n"
            "def run_training(config):\n"
            "    return {'metrics': {'acc': compute()}}\n"
        )
        dep = "def compute():\n    return 0.9\n"

        wrapper = _generate_execution_wrapper(
            "trial-test", {}, "code://sha256:abc",
            code_text=primary,
            dep_snippets=[("/w/src/trainer.py", dep)],
        )
        result = subprocess.run(
            ["python3", "-c", wrapper],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, result.stderr
        import json as _json
        output = _json.loads(result.stdout)
        assert output["metrics"]["acc"] == 0.9

    def test_materialize_plan_poisons_ambiguous_names(self):
        from ml_episteme_mcp.tools.trial import _materialize_plan

        plan = _materialize_plan([
            ("/a/mol/ga.py", "x = 1"),
            ("/b/mol/ga.py", "x = 2"),  # same relpaths, different content
        ])
        # mol/ga.py and ga.py are ambiguous — dropped; no silent wrong binding
        assert "ga.py" not in plan
        assert "mol/ga.py" not in plan
        # deeper qualified names still survive (different parents)
        assert plan.get("a/mol/ga.py") == "x = 1"
        assert plan.get("b/mol/ga.py") == "x = 2"


@pytest.fixture
def subprocess_project(tmp_path):
    """Executor that spawns a runner via subprocess — invisible to
    import-following (the prog-160a52ef gap: only executor.py captured).

    structure:
        entry.py         → os.path.join(ROOT,'scripts','runner.py') +
                           subprocess.run([PY, RUNNER])
        scripts/runner.py → from pkg.mod import go (transitive)
        pkg/mod.py       → self-contained
    """
    (tmp_path / "entry.py").write_text("""
import os, subprocess
ROOT = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(ROOT, 'scripts', 'runner.py')
MISSING = os.path.join(ROOT, 'scripts', 'nope.py')

def run_training(config):
    subprocess.run([os.sys.executable, RUNNER])
    return {"metrics": {"x": 1.0}, "variance": {}}
""")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "runner.py").write_text("""
from pkg.mod import go
def main(): go()
""")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("def go(): return 1\n")
    return tmp_path


class TestSpawnedScriptCapture:
    """Subprocess-spawned .py files are captured — commitment 5 requires
    the bundle to contain the code that actually runs the experiment."""

    def test_spawned_runner_captured(self, store, subprocess_project):
        _, extras = store.capture_code_with_imports(
            str(subprocess_project / "entry.py")
        )
        assert len(extras) == 2
        snippets = {
            s.original_path for e in extras
            for s in [store.get_code_snippet(e)]
        }
        assert str(subprocess_project / "scripts" / "runner.py") in snippets
        assert str(subprocess_project / "pkg" / "mod.py") in snippets

    def test_missing_literal_not_captured(self, store, subprocess_project):
        """'scripts/nope.py' is referenced but doesn't exist — skipped."""
        _, extras = store.capture_code_with_imports(
            str(subprocess_project / "entry.py")
        )
        paths = [store.get_code_snippet(e).original_path for e in extras]
        assert not any("nope.py" in p for p in paths)

    def test_ambiguous_basename_not_captured(self, store, subprocess_project):
        """A literal basename matching multiple files is skipped —
        statically unresolvable, and capturing all would poison
        same-stem materialization on code:// rerun."""
        (subprocess_project / "a").mkdir()
        (subprocess_project / "b").mkdir()
        (subprocess_project / "a" / "dup.py").write_text("x = 1\n")
        (subprocess_project / "b" / "dup.py").write_text("x = 2\n")
        (subprocess_project / "user.py").write_text(
            'P = "dup.py"\n'
        )
        _, extras = store.capture_code_with_imports(
            str(subprocess_project / "user.py")
        )
        assert extras == []

    def test_package_init_captured(self, store, subprocess_project):
        """from pkg.mod import go executes pkg/__init__.py at runtime —
        it is executed code and must be in the sealed bundle."""
        (subprocess_project / "pkg" / "__init__.py").write_text("V = 1\n")
        _, extras = store.capture_code_with_imports(
            str(subprocess_project / "entry.py")
        )
        paths = [store.get_code_snippet(e).original_path for e in extras]
        assert str(subprocess_project / "pkg" / "__init__.py") in paths

    def test_sys_path_insert_resolves_vendored_import(self, store, subprocess_project):
        """sys.path.insert(0, join(ROOT,'contrib')) + 'import sascorer'
        — the inserted dir is an import root for that file."""
        (subprocess_project / "contrib").mkdir()
        (subprocess_project / "contrib" / "sascorer.py").write_text("X = 1\n")
        (subprocess_project / "pkg" / "mod.py").write_text(
            "import os, sys\n"
            "_R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
            "sys.path.insert(0, os.path.join(_R, 'contrib'))\n"
            "import sascorer\n"
        )
        _, extras = store.capture_code_with_imports(
            str(subprocess_project / "entry.py")
        )
        paths = [store.get_code_snippet(e).original_path for e in extras]
        assert str(subprocess_project / "contrib" / "sascorer.py") in paths

    def test_from_pkg_import_submodule(self, store, tmp_path):
        """from pkg import mod captures pkg/mod.py, not just
        pkg/__init__.py — the alias may be a submodule."""
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "__init__.py").write_text("")
        (tmp_path / "pkg" / "mod.py").write_text("def go(): return 1\n")
        (tmp_path / "u.py").write_text("from pkg import mod\n")
        _, extras = store.capture_code_with_imports(str(tmp_path / "u.py"))
        paths = [store.get_code_snippet(e).original_path for e in extras]
        assert str(tmp_path / "pkg" / "mod.py") in paths
        assert str(tmp_path / "pkg" / "__init__.py") in paths
