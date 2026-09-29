"""rc-11 extraction findings — episteme completion predicate (R44) and
the diagnostics coverage sentinel (R49/W5b).

R44: 'completed' required a verdict-key executor record, not a run
artifact — a bare {"status": "completed"} receipt satisfied the gate,
and mislabeled_outcome reimplemented a subset of the gate's signals
(it never tested error/timed_out keys). Both now share
_completion_failure_signals + _has_run_artifact.

W5b: the coverage sentinel — every registered @mcp.tool name and
@mcp.resource URI must appear in diagnostics/coverage-manifest.txt,
so "new tool, no diagnostic" is a CI failure, not a production
discovery.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
MANIFEST = ROOT / "diagnostics" / "coverage-manifest.txt"
PACKAGES = [
    "ml_agora_mcp", "ml_anamnesis_mcp", "ml_arete_mcp",
    "ml_episteme_mcp", "ml_zetesis_mcp",
]


def _episteme_prog_hyp(store, pid="prog-1", hid="hyp-1"):
    from ml_episteme_mcp.state.models import (
        Hypothesis, Programme, ProgrammeStatus,
    )

    store.create_programme(Programme(
        id=pid, goal="g", constraints={},
        allowed_variables=["x"], budget_max_trials=10,
        budget_max_wall_time_hours=1.0,
        status=ProgrammeStatus.active,
    ))
    store.create_hypothesis(Hypothesis(
        id=hid, programme_id=pid,
        statement="s", failure_criterion="f",
        variables_involved=["x"],
    ))


def _trial(store, tid="trial-1", status="failed", record=None):
    from ml_episteme_mcp.state.models import Trial, TrialStatus

    store.create_trial(Trial(
        id=tid, programme_id="prog-1",
        hypothesis_id="hyp-1", config_json="{}",
        status=TrialStatus(status),
        executor_output_json=record,
    ))


class TestRunArtifactRequired:
    """A bare status-word record is not evidence a run happened."""

    @pytest.mark.parametrize("record", [
        '{"status": "completed"}',
        '{"status": "completed", "trial_id": "trial-1"}',
    ])
    def test_status_only_completed_refused(self, tmp_path, record):
        from ml_episteme_mcp.state.store import StateStore

        store = StateStore(str(tmp_path / "s.db"))
        store.connect()
        try:
            _episteme_prog_hyp(store)
            for source in ("failed", "retryable"):
                _trial(store, f"trial-{source}", status=source,
                       record=record)
                with pytest.raises(ValueError, match="evidence"):
                    store.correct_trial_status(
                        f"trial-{source}", "completed", "launder"
                    )
        finally:
            store.close()

    def test_status_only_flagged_by_audit(self, tmp_path):
        """A completed row carrying a bare status-word record is a
        violation — gate and audit agree, in both directions."""
        from ml_episteme_mcp.integrity.checks import (
            _check_mislabeled_outcome,
        )
        from ml_episteme_mcp.state.store import StateStore

        store = StateStore(str(tmp_path / "s.db"))
        store.connect()
        try:
            _episteme_prog_hyp(store)
            _trial(store, status="completed",
                   record='{"status": "completed"}')
            result = _check_mislabeled_outcome(store)
            assert not result["ok"]
            assert result["violations"][0]["trial_id"] == "trial-1"
            assert any(
                "run artifact" in s
                for s in result["violations"][0]["signals"]
            )
        finally:
            store.close()

    def test_real_completed_record_passes_both_ways(self, tmp_path):
        """A genuine executor record still corrects to completed AND
        stays clean under the audit."""
        from ml_episteme_mcp.integrity.checks import (
            _check_mislabeled_outcome,
        )
        from ml_episteme_mcp.state.store import StateStore

        record = (
            '{"status": "completed", "exit_code": 0, '
            '"stdout": "{\\"metric\\": 0.9}"}'
        )
        store = StateStore(str(tmp_path / "s.db"))
        store.connect()
        try:
            _episteme_prog_hyp(store)
            _trial(store, "trial-fix", status="failed", record=record)
            store.correct_trial_status(
                "trial-fix", "completed", "re-read: executor succeeded"
            )
            result = _check_mislabeled_outcome(store)
            assert result["ok"], result["violations"]
        finally:
            store.close()


class TestSharedSignalSet:
    """error/timed_out keys the audit used to miss now flag."""

    @pytest.mark.parametrize("record", [
        '{"status": "completed", "exit_code": 0, "error": "oom"}',
        '{"status": "completed", "exit_code": 0, "timed_out": true}',
    ])
    def test_error_keys_flagged(self, tmp_path, record):
        from ml_episteme_mcp.integrity.checks import (
            _check_mislabeled_outcome,
        )
        from ml_episteme_mcp.state.store import StateStore

        store = StateStore(str(tmp_path / "s.db"))
        store.connect()
        try:
            _episteme_prog_hyp(store)
            _trial(store, status="completed", record=record)
            result = _check_mislabeled_outcome(store)
            assert not result["ok"]
        finally:
            store.close()

    def test_error_key_also_refuses_at_gate(self, tmp_path):
        """Gate and audit are the same predicate — a record the check
        flags cannot have passed the gate."""
        from ml_episteme_mcp.state.store import StateStore

        store = StateStore(str(tmp_path / "s.db"))
        store.connect()
        try:
            _episteme_prog_hyp(store)
            _trial(store, status="retryable",
                   record='{"exit_code": 0, "timed_out": true}')
            with pytest.raises(ValueError, match="evidence"):
                store.correct_trial_status(
                    "trial-1", "completed", "launder"
                )
        finally:
            store.close()


def _registered_surface() -> set[str]:
    """Every @mcp.tool name and @mcp.resource URI in the five
    packages — the surface the battery must name."""
    names = set()
    for pkg in PACKAGES:
        for f in (ROOT / "src" / pkg).rglob("*.py"):
            text = f.read_text()
            names.update(
                re.findall(r"@mcp\.tool\(\)[\s\S]{0,400}?def (\w+)",
                           text)
            )
            names.update(
                re.findall(r"@mcp\.resource\([\"']([^\"']+)", text)
            )
    return names


def _manifest_names() -> set[str]:
    assert MANIFEST.exists(), (
        "diagnostics/coverage-manifest.txt is absent — the sentinel "
        "cannot tell uncovered surface from a missing manifest; "
        "restore it or regenerate it from the registered surface"
    )
    names = set()
    for line in MANIFEST.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or " -> " not in line:
            continue
        names.add(line.split(" -> ")[0].strip())
    return names


class TestCoverageSentinel:
    """W5b — every registered surface names a diagnostic prompt."""

    def test_every_surface_has_a_prompt(self):
        surface = _registered_surface()
        assert surface, "surface enumeration found nothing — wrong root?"
        uncovered = surface - _manifest_names()
        assert not uncovered, (
            f"{len(uncovered)} registered surface(s) absent from "
            f"coverage-manifest.txt: {sorted(uncovered)} — a tool or "
            "resource shipped without a diagnostic naming it"
        )

    def test_manifest_slugs_resolve(self):
        """Every slug the manifest cites must be a real prompt file —
        a stale slug is as dishonest as no coverage."""
        prompt_slugs = {
            f.name[:-3].split("-", 2)[-1]
            for f in (ROOT / "diagnostics").glob("*.md")
            if re.match(r"\d{8}-\d{4}Z-", f.name)
        }
        stale = set()
        for line in MANIFEST.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or " -> " not in line:
                continue
            for slug in line.split(" -> ")[1].split(","):
                slug = slug.strip()
                if slug and slug not in prompt_slugs:
                    stale.add(slug)
        assert not stale, (
            f"manifest cites nonexistent prompt(s): {sorted(stale)}"
        )
