"""Declared inputs earn a provenance row even without a traced open —
rc-15 F7.

sealed-provenance-honesty: a file opened and deleted inside one trial
left no manifest row and no strace entry — an ephemeral input was
invisible to the audit trail. Declared inputs (data_paths /
data_ref_paths resolved at run time) are now merged into the
provenance pass: present at finalize → digested; gone → sha256: null
+ a missing-file reason, never silently omitted.
"""

import json

import pytest

from ml_episteme_mcp.state.models import (
    Hypothesis,
    Programme,
    ProgrammeStatus,
    Trial,
    TrialStatus,
)
from ml_episteme_mcp.state.store import StateStore


@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "state.db")
    s.connect()
    yield s
    s.close()


def _seed_trial(store, config: dict, trial_id="trial-t1"):
    store.create_programme(Programme(
        id="prog-t", goal="g", constraints={}, allowed_variables=["x"],
        budget_max_trials=10, budget_max_wall_time_hours=1.0,
        status=ProgrammeStatus.active,
    ))
    store.create_hypothesis(Hypothesis(
        id="hyp-t", programme_id="prog-t",
        statement="s", failure_criterion="f", variables_involved=["x"],
    ))
    store.create_trial(Trial(
        id=trial_id, programme_id="prog-t", hypothesis_id="hyp-t",
        config_json=json.dumps(config), status=TrialStatus.designed,
    ))
    return trial_id


def _trace_without_open(artifact, opened: str):
    """A strace log that traced an unrelated open but NOT the
    declared input — the untraced-access shape from rc-15."""
    (artifact / "t_2026_readtrace.strace").write_text(
        f'100 openat(AT_FDCWD, "{opened}", O_RDONLY|O_CLOEXEC) = 3\n'
    )


class TestDeclaredInputProvenance:
    def test_deleted_declared_input_gets_null_row(self, store, tmp_path):
        """The rc-15 repro: declared, deleted before finalize, never
        traced — must still produce a manifest row with a reason."""
        artifact = tmp_path / "art"
        artifact.mkdir()
        opened = tmp_path / "other.py"
        opened.write_text("x = 1\n")
        data = tmp_path / "ephemeral.csv"
        data.write_text("a,b\n")
        data_path = str(data.resolve())
        trial_id = _seed_trial(store, {
            "data_paths": {"train": data_path},
        })
        data.unlink()  # deleted mid-run — gone at finalize

        _trace_without_open(artifact, str(opened.resolve()))
        result = store.capture_executed_code(trial_id, artifact)
        assert result["traced"] is True

        rows = [
            c for c in result["captured"] if c["path"] == data_path
        ]
        assert len(rows) == 1
        row = rows[0]
        assert row["role"] == "input_data"
        assert row["declared"] is True
        assert row["sha256"] is None
        assert "missing at finalize" in row["reason"]

    def test_present_declared_input_is_digested(self, store, tmp_path):
        """A declared input that survives the run is digested — and
        flagged declared even when no open was traced."""
        artifact = tmp_path / "art"
        artifact.mkdir()
        opened = tmp_path / "other.py"
        opened.write_text("x = 1\n")
        data = tmp_path / "train.csv"
        data.write_text("a,b\n")
        data_path = str(data.resolve())
        trial_id = _seed_trial(store, {
            "data_ref_paths": {"data-ref-x": data_path},
        })

        _trace_without_open(artifact, str(opened.resolve()))
        result = store.capture_executed_code(trial_id, artifact)

        row = next(
            c for c in result["captured"] if c["path"] == data_path
        )
        assert row["role"] == "input_data"
        assert row["declared"] is True
        assert row["sha256"].startswith("sha256:")

    def test_undeclared_untraced_file_stays_absent(self, store, tmp_path):
        """An undeclared, untraced file still earns no row — the fix
        covers declared inputs, not omniscience."""
        artifact = tmp_path / "art"
        artifact.mkdir()
        opened = tmp_path / "other.py"
        opened.write_text("x = 1\n")
        trial_id = _seed_trial(store, {})

        _trace_without_open(artifact, str(opened.resolve()))
        result = store.capture_executed_code(trial_id, artifact)
        paths = [c["path"] for c in result["captured"]]
        assert not any("ephemeral" in p for p in paths)
