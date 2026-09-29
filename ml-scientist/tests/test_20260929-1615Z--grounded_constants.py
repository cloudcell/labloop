"""Grounded-constants registry pins (e-plan 20260929-1514Z).

Every value consumed by a scoring, confidence, threshold, or
promotion computation carries verifiable provenance into
docs-pub/r-references/val-grounding/. These tests pin: the registry
schema, corpus integrity, mirror identity, the no-second-definition
rule, disclosure drift, and for_decision() enforcement.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
CANONICAL = ROOT / "constants" / "grounded_constants.py"
CORPUS_DIR = ROOT / "docs-pub" / "r-references" / "val-grounding"
DISCLOSURE = ROOT / "constants" / "disclosure.md"
PACKAGES = [
    "ml_agora_mcp",
    "ml_anamnesis_mcp",
    "ml_arete_mcp",
    "ml_episteme_mcp",
    "ml_zetesis_mcp",
]


@pytest.fixture(scope="module")
def reg():
    return importlib.import_module("ml_episteme_mcp._grounded_constants")


# --- mirror identity ------------------------------------------------------


def test_mirrors_byte_identical_to_canonical():
    canonical = CANONICAL.read_bytes()
    for pkg in PACKAGES:
        mirror = ROOT / "src" / pkg / "_grounded_constants.py"
        assert mirror.exists(), f"{pkg} missing _grounded_constants.py"
        assert mirror.read_bytes() == canonical, (
            f"{pkg}/_grounded_constants.py has drifted from "
            "constants/grounded_constants.py — edit the canonical "
            "file and re-copy (mirror + pin convention)"
        )


def test_all_mirrors_import_cleanly():
    for pkg in PACKAGES:
        mod = importlib.import_module(f"{pkg}._grounded_constants")
        assert set(mod.REGISTRY)


# --- schema ---------------------------------------------------------------

_REQUIRED_FIELDS = (
    "name", "value", "cls", "status", "scoring_path", "citation",
    "locator", "source_key", "procedure", "rationale", "decision_load",
)


def test_every_entry_is_complete(reg):
    for c in reg.REGISTRY.values():
        for f in _REQUIRED_FIELDS:
            assert hasattr(c, f), f"{c.name} missing field {f}"
        assert c.rationale.strip(), f"{c.name} has no rationale"


def test_sourced_entries_resolve_corpus(reg):
    """SOURCED/IN-RANGE must carry citation + locator + a source_key
    whose SOURCES entry names a corpus file."""
    for c in reg.REGISTRY.values():
        if c.status in ("SOURCED", "IN-RANGE"):
            assert c.citation, f"{c.name}: {c.status} without citation"
            assert c.locator, f"{c.name}: {c.status} without locator"
            assert c.source_key in reg.SOURCES, (
                f"{c.name}: unknown source_key {c.source_key}"
            )
            assert reg.SOURCES[c.source_key].filename, (
                f"{c.name}: source {c.source_key} archives no file"
            )


def test_derived_and_theorem_ship_no_literal(reg):
    """DERIVED/THEOREM ground a procedure, not a number — a caller
    must not be able to import a value from them."""
    for c in reg.REGISTRY.values():
        if c.status in ("DERIVED", "THEOREM"):
            assert c.value is None, (
                f"{c.name}: {c.status} entry ships a literal value"
            )
            assert c.procedure, f"{c.name}: {c.status} without procedure"


def test_ungrounded_never_decision_load(reg):
    for c in reg.REGISTRY.values():
        if c.status == "UNGROUNDED":
            assert not c.decision_load, (
                f"{c.name}: UNGROUNDED but decision_load=True"
            )


def test_scoring_invariant(reg):
    """The operator rule: a scoring-path entry may not be UNGROUNDED
    or OPERATIONAL — unless it is explicitly transitional with a
    named replacement (the migration window, not the end state)."""
    for c in reg.REGISTRY.values():
        if c.scoring_path and c.status in ("UNGROUNDED", "OPERATIONAL"):
            assert c.transitional and c.pending, (
                f"{c.name}: {c.status} in a scoring path with no "
                "transitional replacement named — the grounding "
                "invariant forbids this end state"
            )


def test_no_scoring_violations(reg):
    assert reg.scoring_violations() == []


# --- corpus integrity -----------------------------------------------------


def test_source_artifacts_exist_and_hash(reg):
    for key, s in reg.SOURCES.items():
        if s.filename is None:
            continue
        path = CORPUS_DIR / s.filename
        assert path.exists(), f"source {key}: {s.filename} not in corpus"
        if s.sha256:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            assert digest == s.sha256, (
                f"source {key}: sha256 mismatch on {s.filename}"
            )


# --- single definition ----------------------------------------------------


def test_no_second_definition(reg):
    """A registered literal must be defined nowhere else under src/
    — the F6 duplication class (600×7, 3600×3, 24.0×4) dies here."""
    failures = []
    for c in reg.REGISTRY.values():
        if c.value is None or not isinstance(c.value, (int, float)):
            continue
        lit = re.escape(repr(c.value))
        # NAME = <literal> as a module-level or attribute assignment
        pat = re.compile(rf"^\s*{c.name}\s*=\s*{lit}\b")
        for py in (ROOT / "src").rglob("*.py"):
            if py.name == "_grounded_constants.py":
                continue
            for i, line in enumerate(py.read_text().splitlines(), 1):
                if pat.match(line):
                    failures.append(f"{c.name} redefined at {py}:{i}")
    assert not failures, "\n".join(failures)


_SEMANTIC_KEYS = {
    # config key / param name → registered constant. A line naming one
    # of these keys AND carrying its registered literal as a bare
    # number is a second definition.
    "status_freshness_seconds": "STATUS_FRESHNESS_SECONDS",
    "improvement_epoch_seconds": "IMPROVEMENT_EPOCH_SECONDS",
    "observation_grace_seconds": "OBSERVATION_GRACE_SECONDS",
    "check_interval_seconds": "CHECK_INTERVAL_SECONDS",
    "stalled_trial_seconds": "STALLED_MARGIN_SECONDS",
    "stalled_margin_seconds": "STALLED_MARGIN_SECONDS",
    "log_max_files": "LOG_MAX_FILES",
    "stale_investigation_seconds": "STALE_INVESTIGATION_SECONDS",
    "stale_campaign_seconds": "STALE_CAMPAIGN_SECONDS",
    "stale_tournament_seconds": "STALE_TOURNAMENT_SECONDS",
    "stale_programme_hours": "STALE_PROGRAMME_HOURS",
    "archive_seal_warn_hours": "ARCHIVE_SEAL_WARN_HOURS",
    "prior_confidence_max": "PRIOR_CONFIDENCE_MAX",
}


def test_no_bare_registered_literal_in_src(reg):
    """A semantic key must never take its registered literal inline —
    `enf.get("status_freshness_seconds", 600)` is a second definition.
    Values coincidentally equal to a registered one but semantically
    unrelated (query limits, layout sizes) are not matched."""
    failures = []
    for py in (ROOT / "src").rglob("*.py"):
        if py.name == "_grounded_constants.py":
            continue
        rel = py.relative_to(ROOT)
        for i, line in enumerate(py.read_text().splitlines(), 1):
            if "_gc." in line:
                continue
            for key, cname in _SEMANTIC_KEYS.items():
                if key not in line:
                    continue
                c = reg.REGISTRY[cname]
                lit = re.escape(repr(c.value))
                if re.search(rf"[=,:]\s*{lit}\b", line):
                    failures.append(
                        f"{cname}: bare literal at {rel}:{i} — {line.strip()}"
                    )
    assert not failures, "\n".join(sorted(set(failures)))


# --- disclosure drift -----------------------------------------------------


def test_disclosure_table_is_current():
    r = subprocess.run(
        ["uv", "run", "python", "scripts/gen_constants_disclosure.py",
         "--check"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert r.returncode == 0, (
        f"constants/disclosure.md is stale — regenerate with "
        f"scripts/gen_constants_disclosure.py\n{r.stderr}"
    )


# --- runtime enforcement ---------------------------------------------------


def test_for_decision_refuses_undecidable(reg):
    for c in reg.REGISTRY.values():
        if not c.decision_load:
            with pytest.raises(reg.UngroundedDecisionBasis):
                c.for_decision()


def test_for_decision_returns_value(reg):
    assert reg.PRIOR_CONFIDENCE_MAX.for_decision() == 0.3


def test_procedure_entries_carry_no_value(reg):
    with pytest.raises(reg.UngroundedDecisionBasis):
        reg.CAMPAIGN_SCORE_ZERO_DIVISOR.for_decision()
    assert reg.CAMPAIGN_SCORE_ZERO_DIVISOR.procedure == "refuse_zero_divisor"


def test_every_value_entry_is_consumed(reg):
    """No dead registry rows: a value entry must be imported by ≥1
    src/ call site. Procedure entries are exempt — the procedure
    lands with its follow-on plan."""
    spec = importlib.util.spec_from_file_location(
        "gen_constants_disclosure",
        ROOT / "scripts" / "gen_constants_disclosure.py",
    )
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    module_reg = gen._load_registry()
    sites = gen._call_sites(set(module_reg.REGISTRY))
    dead = [
        name for name, c in module_reg.REGISTRY.items()
        if c.procedure is None and not sites[name]
    ]
    assert not dead, f"registry rows imported nowhere: {dead}"


# --- terminal-state sentinels (plan-20260929-1643Z) -------------------------


def test_registry_has_no_ungrounded_entries(reg):
    """The grounding programme's terminal state: nothing UNGROUNDED
    remains. Re-introduction requires deleting this pin."""
    bad = [
        c.name for c in reg.REGISTRY.values() if c.status == "UNGROUNDED"
    ]
    assert not bad, f"UNGROUNDED entries re-introduced: {bad}"


def test_registry_has_no_transitional_entries(reg):
    """No entry may be 'live but scheduled for replacement' — the
    transition completed with the field-deletion plan."""
    bad = [c.name for c in reg.REGISTRY.values() if c.transitional]
    assert not bad, f"transitional entries re-introduced: {bad}"
