"""Grounded decision constants — provenance-labelled registry.

Canonical source. Per ADR-0001/0002 (no cross-package imports) this file is
mirrored byte-identically into each package as
``src/ml_<name>_mcp/_grounded_constants.py``; the test suite pins the mirrors
to this file. Edit here, then re-copy.

Every constant carries its grounding: a source status from the lab audit
(GROUNDING-PROPOSAL.md §2), a full citation into the corpus at
``docs-pub/r-references/val-grounding/``, and a ``decision_load`` flag —
a ``UNGROUNDED`` value may not ground a promotion decision (protocol R2).

Sources (key — full citation — corpus file — sha256 prefix):

  02  Franz (2007) Ratios: confidence limits and proper use. JLU Giessen TR,
      arXiv:0710.2024 — 02-ratios-fieller-guide-arxiv0710.2024.pdf — 1a6c716d
  04  Morey, Hoekstra, Rouder, Lee & Wagenmakers (2016) The fallacy of placing
      confidence in confidence intervals. Psychonomic Bulletin & Review 23(1)
      (author preprint) — 04-morey-et-al-2016-author-preprint.pdf — b5cba209
  05  Gelman & Carlin (2014) Beyond power calculations: Type S and Type M
      errors. Perspectives on Psychological Science 9(6):641-651,
      doi:10.1177/1745691614551642 — 05-gelman-carlin2014-*.pdf — 068b792d
  06  Lakens et al. (2026) Rethinking Type S and M errors. OSF preprint 2phzb
      — 06-lakens-et-al-2026-*.pdf — 4c96fe72
  07  Albers & Lakens (2018) Power analyses from pilot data are biased.
      J. Experimental Social Psychology — 07-albers2018-*.pdf — 8a68117e
  08  Kass & Raftery (1995) Bayes factors. JASA 90(430):773-795
      — 08-kass-raftery1995-bayes-factors.pdf — 3da446b1
  09  Gneiting & Raftery (2007) Strictly proper scoring rules. JASA 102(477):
      359-378, doi:10.1198/016214506000001437 — 09-gneiting-raftery2007-*.pdf
      — d31a0c5f
  11  Bakker et al. (2020) PLOS ONE 15(7):e0236079,
      doi:10.1371/journal.pone.0236079 — 11-bakker2020-*.pdf — e7a64fb6
  12  Lakatos (1970) Falsification and the MSRP. Schick (ed.) Readings in the
      Philosophy of Science Vol. 1 — 12-lakatos1970-*.pdf — 11045de6
  13  Lupidi et al. (2026) AIRS-Bench. arXiv:2602.06855
      — 13-airsbench-*.pdf — edceffbd
  14  von Luxburg & Franz (2004) Confidence sets for ratios: geometric Fieller.
      MPI TR-133 — 14-vonluxburg-franz2004-*.pdf — 0dc610b3
  NAP National Academies (2019) Reproducibility and Replicability in Science,
      App. D Table D-1 — nap-table-d1-transcription.md — 290b5326
      (transcription; original read at publisher, not archivable)
  GH  Gleser & Hwang (1987) — gleser-hwang-transcription.md — 5608269a
      (second-hand via Axioms 13(3):37 survey; citation venue disputed —
      audit manifest says Biometrika, canonical is Annals of Statistics
      15(4):1351-1362)
  FI  Fieller (1954) JRSS B 16:174-185 — not archived; content reproduced
      in 02 and 14
  AX  Axioms 13(3):37 (2025) ratio-interval survey — not archived
      (publisher asset-path collision); quoting source for GH
  SE  Lakens, Scheel & Isager (2018) Equivalence testing tutorial.
      AMPPS 1(2):259-269 — not archived (paywalled; read at publisher)
  AK  van den Akker et al. (2023) Preregistration in practice. Behavior
      Research Methods — not archived (paywalled; read at publisher)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Literal

_CORPUS = "docs-pub/r-references/val-grounding"

Status = Literal[
    "SOURCED",      # a source names this value/procedure, with reason
    "DERIVED",      # a cited method yields the value from declared inputs
    "IN-RANGE",     # source tabulates a family including ours, unrecommended
    "UNGROUNDED",   # no source — see `transitional`/`pending`
    "OPERATIONAL",  # deployment preference, reclassified — needs a reason
    "THEOREM",      # settled by proof, not a number (value is None)
]

ConstantClass = Literal["C1", "C2", "C3", "C4"]


@dataclass(frozen=True)
class CorpusSource:
    key: str               # registry shorthand ("02", "NAP", ...)
    citation: str          # full citation — resolvable without our archive
    filename: str | None   # file under the corpus dir, None if unarchived
    sha256: str | None     # recorded digest; None when nothing is archived


@dataclass(frozen=True)
class GroundedConstant:
    name: str
    value: int | float | str | None   # None for DERIVED/THEOREM entries
    cls: ConstantClass                # audit taxonomy: C1 calibration,
                                      # C2 discriminative, C3 governance,
                                      # C4 operational
    status: Status
    scoring_path: bool                # consumed by a scoring/confidence/
                                      # threshold/promotion computation
    citation: str | None              # full citation, None for OPERATIONAL
    locator: str | None               # table/section inside the source
    source_key: str | None            # key into SOURCES
    procedure: str | None             # DERIVED/THEOREM: the grounded
                                      # computation's name
    rationale: str                    # why this value / why reclassified
    decision_load: bool               # may ground a promotion decision
                                      # (UNGROUNDED implies False)
    transitional: bool = False        # still live but scheduled for
                                      # replacement/deletion by a named
                                      # follow-on plan
    pending: str | None = None        # what replaces it / "delete" / "rename"

    def for_decision(self) -> float | int | str:
        """Value for a decision-consuming context. Refuses UNGROUNDED."""
        if not self.decision_load:
            raise UngroundedDecisionBasis(
                f"{self.name} (status={self.status}) may not ground a "
                f"decision — {'replacement: ' + self.pending if self.pending else 'no grounding'}"
            )
        if self.value is None:
            raise UngroundedDecisionBasis(
                f"{self.name} is a {self.status} procedure entry "
                f"({self.procedure}); call the procedure, not the value"
            )
        return self.value


class UngroundedDecisionBasis(ValueError):
    """Raised when an ungrounded/procedure constant grounds a decision."""


SOURCES: dict[str, CorpusSource] = {
    "02": CorpusSource("02", "Franz, V. H. (2007). Ratios: A short guide to confidence limits and proper use. Technical report, Justus-Liebig-Universität Giessen. arXiv:0710.2024", "02-ratios-fieller-guide-arxiv0710.2024.pdf", "1a6c716d499e36fd5d8387e7dc94a2cad23e897ed58b85c7fa8666c2df9f5fd3"),
    "04": CorpusSource("04", "Morey, R. D., Hoekstra, R., Rouder, J. N., Lee, M. D., & Wagenmakers, E.-J. (2016). The fallacy of placing confidence in confidence intervals. Psychonomic Bulletin & Review 23(1):103-123 (author preprint)", "04-morey-et-al-2016-author-preprint.pdf", "b5cba20978852b59b87aeffb33909e82d40a7da702e97f99028888d909365ad2"),
    "05": CorpusSource("05", "Gelman, A., & Carlin, J. B. (2014). Beyond power calculations: assessing Type S (sign) and Type M (magnitude) errors. Perspectives on Psychological Science 9(6):641-651. doi:10.1177/1745691614551642", "05-gelman-carlin2014-type-s-type-m-errors.pdf", "068b792d2b98b23f24ef8ea03afd55b49732290fbfbd00c2181b10cf04facb7d"),
    "06": CorpusSource("06", "Lakens, D., Mesquida, C., Xavier-Quintais, G., Rasti, S., Toffalini, E., & Altoè, G. (2026). Rethinking Type S and M errors. OSF preprint 2phzb", "06-lakens-et-al-2026-rethinking-type-s-m.pdf", "4c96fe72142536be546e5b003a87df7d4d7ce329e7473d7f21073c549893aa45"),
    "07": CorpusSource("07", "Albers, C. J., & Lakens, D. (2018). When power analyses based on pilot data are biased: inaccurate effect size estimators and follow-up bias. Journal of Experimental Social Psychology (version of record, author's copy)", "07-albers2018-power-from-pilot-data-biased.pdf", "8a68117ee316f0232c2ddc93f06bb57a74fd9efdd9227a63cb362767330824ac"),
    "08": CorpusSource("08", "Kass, R. E., & Raftery, A. E. (1995). Bayes factors. Journal of the American Statistical Association 90(430):773-795", "08-kass-raftery1995-bayes-factors.pdf", "3da446b1aea8e64801dbe17c0b727a71e7acbbc304d4879532066c7139e50d86"),
    "09": CorpusSource("09", "Gneiting, T., & Raftery, A. E. (2007). Strictly proper scoring rules, prediction, and estimation. Journal of the American Statistical Association 102(477):359-378. doi:10.1198/016214506000001437", "09-gneiting-raftery2007-strictly-proper-scoring.pdf", "d31a0c5f0ae8fec1a0a6544db5d056645b2d7296d71b44a2e8efb293c7d87ba2"),
    "11": CorpusSource("11", "Bakker, M., Veldkamp, C. L. S., van den Akker, O. R., van Assen, M. A. L. M., Crompvoets, E., Ong, H. H., & Wicherts, J. M. (2020). PLOS ONE 15(7):e0236079. doi:10.1371/journal.pone.0236079", "11-bakker2020-prereg-power-analyses.pdf", "e7a64fb6c157d3f3ea0d04db39bc09bf7e44f8a9e22dc3ddc685361c4dfd4876"),
    "12": CorpusSource("12", "Lakatos, I. (1970). Falsification and the methodology of scientific research programmes. In Schick (ed.), Readings in the Philosophy of Science Vol. 1 (reprint; pagination differs from Lakatos & Musgrave)", "12-lakatos1970-falsification-msrp.pdf", "11045de669ce0da99f9d004336bbe46dbd0d5695d3664a6163d13e2038ec5998"),
    "13": CorpusSource("13", "Lupidi, A., et al. (2026). AIRS-Bench: a suite of tasks for frontier AI research science agents. arXiv:2602.06855", "13-airsbench-arxiv2602.06855.pdf", "edceffbd86caea2b824b5c710ed08f3a53b30cfb9a6d4505ce03d978cd9a2ad6"),
    "14": CorpusSource("14", "von Luxburg, U., & Franz, V. H. (2004). Confidence sets for ratios: a purely geometric approach to Fieller's theorem. Max-Planck-Institut für biologische Kybernetik, Technical Report TR-133", "14-vonluxburg-franz2004-fieller-geometric.pdf", "0dc610b3ab9ac590a4c293539a0aeaa68ba3e34297a256eca6f5c7c732ee0c45"),
    "NAP": CorpusSource("NAP", "National Academies of Sciences, Engineering, and Medicine (2019). Reproducibility and Replicability in Science. Washington, DC: National Academies Press. Appendix D, Table D-1 (transcription; original read at publisher, not archivable)", "nap-table-d1-transcription.md", "290b532627833cff82a4ed2fe8bf527714b2dbbade80b564e4511d347732bc70"),
    "GH": CorpusSource("GH", "Gleser, L. J., & Hwang, J. T. (1987). Impossibility result for bounded confidence sets on ratios — audit manifest cites 'The application of Fieller's theorem for confidence intervals for a ratio', Biometrika; canonical record is Annals of Statistics 15(4):1351-1362 (unverified venue — see transcription)", "gleser-hwang-transcription.md", "5608269ac8d3a72c680e50f7a681c861289003636e1f774b509b3ae7a739b8db"),
    "FI": CorpusSource("FI", "Fieller, E. C. (1954). Some problems in interval estimation. Journal of the Royal Statistical Society, Series B 16:174-185 (not archived; content reproduced in sources 02 and 14)", None, None),
    "AX": CorpusSource("AX", "Re-examining confidence intervals for ratios of parameters. Axioms 13(3):37, 2025 (not archived — publisher asset-path collision; read on publisher's HTML page; quoting source for the GH statement)", None, None),
    "SE": CorpusSource("SE", "Lakens, D., Scheel, A. M., & Isager, P. M. (2018). Equivalence testing for psychological research: a tutorial. Advances in Methods and Practices in Psychological Science 1(2):259-269 (not archived — paywalled; read at publisher)", None, None),
    "AK": CorpusSource("AK", "van den Akker, R., et al. (2023). Preregistration in practice. Behavior Research Methods (not archived — paywalled; abstract and results read at publisher)", None, None),
}


# --------------------------------------------------------------------------
# Scoring-path constants — the grounding invariant applies: each carries a
# corpus citation (or a grounded procedure), and none may be UNGROUNDED
# without a `transitional` marker naming its replacement.
# --------------------------------------------------------------------------

PRIOR_CONFIDENCE_MAX = GroundedConstant(
    name="PRIOR_CONFIDENCE_MAX",
    value=0.3,
    cls="C1",
    status="IN-RANGE",
    scoring_path=True,
    citation="National Academies (2019), Reproducibility and Replicability in Science, App. D Table D-1",
    locator="Table D-1, row prior=0.30 (one-tailed p=0.05 → posterior 0.624)",
    source_key="NAP",
    procedure=None,
    rationale=(
        "Ceiling on a claim with no evidence-bearing edge. NAP Table D-1 "
        "tabulates prior 0.30 as 'about 1 in 3' — semantically defensible, "
        "but one row of a family, not a recommendation (IN-RANGE, not "
        "SOURCED). The assumed prior is deployable config."
    ),
    decision_load=True,
)

VERDICT_POSTERIOR = GroundedConstant(
    name="VERDICT_POSTERIOR",
    value=None,
    cls="C1",
    status="DERIVED",
    scoring_path=True,
    citation=(
        "National Academies (2019), Reproducibility and Replicability in "
        "Science, App. D Table D-1; Morey et al. (2016) PBR 23(1); "
        "Kass & Raftery (1995) JASA 90(430):773-795"
    ),
    locator=(
        "NAP Table D-1 (the prior+p → posterior mapping); 08 §2.2 "
        "(the ladder the BF reads onto)"
    ),
    source_key="NAP",
    procedure="verdict_posterior",
    rationale=(
        "Verdict confidence is a posterior bound computed from the "
        "contract's declared prior and the measured p — the oracle LR "
        "bound BF=exp(max(z,0)²/2), honestly labelled an upper bound "
        "(04 governs the semantics: the number is a bound, named as "
        "such). Replaces the indicted flat 0.85 — which NAP D-1 shows "
        "unreachable — and the fixture-derived 0.6."
    ),
    decision_load=False,   # callers invoke verdict_posterior
)

CAMPAIGN_SCORE_ZERO_DIVISOR = GroundedConstant(
    name="CAMPAIGN_SCORE_ZERO_DIVISOR",
    value=None,
    cls="C2",
    status="THEOREM",
    scoring_path=True,
    citation=(
        "Gleser & Hwang (1987) via Axioms 13(3):37 survey; Fieller (1954) "
        "via Franz (2007) and von Luxburg & Franz (2004)"
    ),
    locator="GH transcription (theorem statement); 02/14 (unboundedness)",
    source_key="GH",
    procedure="refuse_zero_divisor",
    rationale=(
        "Any always-bounded score for a ratio with a possibly-zero "
        "denominator has zero coverage — a theorem, not a convention. "
        "close_campaign refuses; camp-532d7b60 froze score 0 for a "
        "challenger win, the observed failure."
    ),
    decision_load=False,   # the procedure decides; no value exists
)

SEED_COUNT_DEFAULT = GroundedConstant(
    name="SEED_COUNT_DEFAULT",
    value=None,
    cls="C2",
    status="DERIVED",
    scoring_path=True,
    citation=(
        "Gelman & Carlin (2014); Lakens et al. (2026); Albers & Lakens "
        "(2018); Bakker et al. (2020)"
    ),
    locator="05 (Type S/M), 06 (rethinking), 07 (pilot bias), 11 (prereg power)",
    source_key="05",
    procedure="required_n",
    rationale=(
        "n follows from a contract-declared SESOI and target power, not a "
        "default. At n=1 the sign of a real small effect is wrong 37.8% of "
        "the time (lab's own Monte-Carlo, prog-e8615c09); the closed form "
        "gives ≈9/arm at 80% directional accuracy under the audit "
        "benchmark's parameters."
    ),
    decision_load=False,   # callers must invoke required_n(contract)
)

PROMOTION_THRESHOLD = GroundedConstant(
    name="PROMOTION_THRESHOLD",
    value=None,
    cls="C2",
    status="DERIVED",
    scoring_path=True,
    citation="Kass & Raftery (1995), JASA 90(430):773-795",
    locator="08 §2.2 Table — 2 ln BF evidence ladder",
    source_key="08",
    procedure="min_evidence_rung",
    rationale=(
        "The promotion bar is a minimum-evidence rung on the Bayes-factor "
        "ladder, declared by the contract — not a literal '>1'."
    ),
    decision_load=False,
)

# --------------------------------------------------------------------------
# Operational constants — reclassified, deduplicated, reasoned. Never in a
# scoring path; the invariant does not apply to them.
# --------------------------------------------------------------------------

STALE_INVESTIGATION_SECONDS = GroundedConstant(
    name="STALE_INVESTIGATION_SECONDS",
    value=3600,
    cls="C3", status="OPERATIONAL", scoring_path=False,
    citation=None, locator=None, source_key=None, procedure=None,
    rationale="Cost-asymmetry choice: an investigation open an hour without a heartbeat is treated as abandoned. Deployment cadence, not science.",
    decision_load=False,
)

STALE_CAMPAIGN_SECONDS = GroundedConstant(
    name="STALE_CAMPAIGN_SECONDS",
    value=3600,
    cls="C3", status="OPERATIONAL", scoring_path=False,
    citation=None, locator=None, source_key=None, procedure=None,
    rationale="Same cost asymmetry applied to open campaigns.",
    decision_load=False,
)

STALE_TOURNAMENT_SECONDS = GroundedConstant(
    name="STALE_TOURNAMENT_SECONDS",
    value=3600,
    cls="C3", status="OPERATIONAL", scoring_path=False,
    citation=None, locator=None, source_key=None, procedure=None,
    rationale="Same cost asymmetry applied to open tournaments.",
    decision_load=False,
)

STALE_PROGRAMME_HOURS = GroundedConstant(
    name="STALE_PROGRAMME_HOURS",
    value=24.0,
    cls="C3", status="OPERATIONAL", scoring_path=False,
    citation=None, locator=None, source_key=None, procedure=None,
    rationale="Session-surface staleness: a programme idle a day is called stale. Was duplicated (server fallback + session constant — the F6 '24.0' class).",
    decision_load=False,
)

ARCHIVE_SEAL_WARN_HOURS = GroundedConstant(
    name="ARCHIVE_SEAL_WARN_HOURS",
    value=72.0,
    cls="C3", status="OPERATIONAL", scoring_path=False,
    citation=None, locator=None, source_key=None, procedure=None,
    rationale="Warn when an archive has sat unsealed for 3 days. Was duplicated across server fallback and status surface (the F6 '24.0'-class duplication pattern).",
    decision_load=False,
)

STATUS_FRESHNESS_SECONDS = GroundedConstant(
    name="STATUS_FRESHNESS_SECONDS",
    value=600,
    cls="C3", status="OPERATIONAL", scoring_path=False,
    citation=None, locator=None, source_key=None, procedure=None,
    rationale=(
        "Status digest TTL — how stale a peer digest may be before the "
        "digest reports 'degraded'. Was written seven times across four "
        "packages (F6); now defined once here."
    ),
    decision_load=False,
)

IMPROVEMENT_EPOCH_SECONDS = GroundedConstant(
    name="IMPROVEMENT_EPOCH_SECONDS",
    value=86400,
    cls="C3", status="OPERATIONAL", scoring_path=False,
    citation=None, locator=None, source_key=None, procedure=None,
    rationale="Cadence at which improvement duty recurs for the improver loop. Deployment preference.",
    decision_load=False,
)

OBSERVATION_GRACE_SECONDS = GroundedConstant(
    name="OBSERVATION_GRACE_SECONDS",
    value=86400,
    cls="C3", status="OPERATIONAL", scoring_path=False,
    citation=None, locator=None, source_key=None, procedure=None,
    rationale="Grace between executor-terminal and observation rows before a completed trial may conclude. Config knob ([integrity]).",
    decision_load=False,
)

CHECK_INTERVAL_SECONDS = GroundedConstant(
    name="CHECK_INTERVAL_SECONDS",
    value=300,
    cls="C3", status="OPERATIONAL", scoring_path=False,
    citation=None, locator=None, source_key=None, procedure=None,
    rationale="Integrity-check cadence. Deployment preference; was duplicated across three packages.",
    decision_load=False,
)

STALLED_MARGIN_SECONDS = GroundedConstant(
    name="STALLED_MARGIN_SECONDS",
    value=300,
    cls="C3", status="OPERATIONAL", scoring_path=False,
    citation=None, locator=None, source_key=None, procedure=None,
    rationale="Margin before a running trial is flagged stalled. Deployment preference.",
    decision_load=False,
)

TERMINAL_RESIDUE_MARGIN_SECONDS = GroundedConstant(
    name="TERMINAL_RESIDUE_MARGIN_SECONDS",
    value=1.0,
    cls="C4", status="OPERATIONAL", scoring_path=False,
    citation=None, locator=None, source_key=None, procedure=None,
    rationale="Absorbs timestamp rounding between terminal mark and executor reap — a fact about the clock, not science.",
    decision_load=False,
)

LOG_MAX_FILES = GroundedConstant(
    name="LOG_MAX_FILES",
    value=100,
    cls="C4", status="OPERATIONAL", scoring_path=False,
    citation=None, locator=None, source_key=None, procedure=None,
    rationale=(
        "Audit-log retention. Was 100 in four packages and 30 in agora — "
        "the audit trail gating all writes must not have divergent "
        "retention in one server; unified at 100 as audit policy."
    ),
    decision_load=False,
)


REGISTRY: dict[str, GroundedConstant] = {
    c.name: c for c in (
        PRIOR_CONFIDENCE_MAX,
        VERDICT_POSTERIOR,
        CAMPAIGN_SCORE_ZERO_DIVISOR,
        SEED_COUNT_DEFAULT,
        PROMOTION_THRESHOLD,
        STALE_INVESTIGATION_SECONDS,
        STALE_CAMPAIGN_SECONDS,
        STALE_TOURNAMENT_SECONDS,
        STALE_PROGRAMME_HOURS,
        ARCHIVE_SEAL_WARN_HOURS,
        STATUS_FRESHNESS_SECONDS,
        IMPROVEMENT_EPOCH_SECONDS,
        OBSERVATION_GRACE_SECONDS,
        CHECK_INTERVAL_SECONDS,
        STALLED_MARGIN_SECONDS,
        TERMINAL_RESIDUE_MARGIN_SECONDS,
        LOG_MAX_FILES,
    )
}


def constants_block() -> dict:
    """Status-digest surface: the registry summary every server embeds
    so a status read reports its own grounding state."""
    return {
        "registered": len(REGISTRY),
        "scoring_path": sum(1 for c in REGISTRY.values() if c.scoring_path),
        "transitional": sum(1 for c in REGISTRY.values() if c.transitional),
        "scoring_violations": scoring_violations(),
    }


def scoring_violations() -> list[str]:
    """Registry rows that violate the grounding invariant (operator rule):
    a scoring_path entry must not be UNGROUNDED or OPERATIONAL unless it is
    `transitional` (scheduled for replacement/deletion by a named plan)."""
    bad = []
    for c in REGISTRY.values():
        if c.scoring_path and c.status in ("UNGROUNDED", "OPERATIONAL"):
            if not (c.transitional and c.pending):
                bad.append(c.name)
    return bad


# --------------------------------------------------------------------------
# Grounded procedures — the DERIVED entries' computations, live. A caller
# does not read SEED_COUNT_DEFAULT.value or PROMOTION_THRESHOLD.value (both
# are None); it invokes these functions on the contract's declared inputs.
# --------------------------------------------------------------------------

# Kass & Raftery (1995) §2.2 table — the 2 ln BF evidence ladder. Rung
# names are the contract-facing enum; values are the minimum 2 ln BF a
# decision must reach to claim that rung. (BF equivalents ≈ 1, 3, 20, 150.)
EVIDENCE_RUNGS: dict[str, float] = {
    "not_worth": 0.0,
    "positive": 2.0,
    "strong": 6.0,
    "very_strong": 10.0,
}

_ALPHA_DEFAULT = 0.05


def min_evidence_rung(name: str) -> float:
    """Rung name → minimum 2 ln BF the contract's decisions must reach."""
    try:
        return EVIDENCE_RUNGS[name]
    except KeyError:
        raise UngroundedDecisionBasis(
            f"unknown evidence rung {name!r} — the Kass–Raftery ladder "
            f"is {'|'.join(EVIDENCE_RUNGS)}"
        )


def rung_at_least(claimed: str, required: str) -> bool:
    """Ordering on the ladder — claimed must reach the required rung."""
    return min_evidence_rung(claimed) >= min_evidence_rung(required)


# Confidence provenance — what the minted float rests on. Recorded as
# `confidence_basis` on the claim row; the float is a bound or a
# ceiling, never a bare label (04 Morey: a number that is not a
# posterior must not wear the clothes of one).
CONFIDENCE_BASIS: tuple[str, ...] = (
    "ungrounded",        # no evidence consulted (prior-ceiling mints)
    "weakly_grounded",   # evidence consulted, no comparative likelihood
    "grounded",          # a posterior bound was computed
)


def rung_for_2lnbf(x: float) -> str:
    """The highest Kass–Raftery rung a measured 2 ln BF reaches —
    the inverse of min_evidence_rung."""
    reached = "not_worth"
    for name, threshold in EVIDENCE_RUNGS.items():
        if x >= threshold:
            reached = name
    return reached


def arm_evidence(
    champion_vals: list[float],
    challenger_vals: list[float],
) -> dict | None:
    """Two-sample normal-theory statistic over per-arm results.

    ``z = (m_chal − m_champ) / (sd_pooled·√(1/n₁ + 1/n₂))``; the
    one-sided p reads in the challenger-favored direction (the
    score>1 semantics the promotion machinery assumes). The
    likelihood-ratio bound is directional: under H₁ = challenger
    ahead, ``bf_2ln = max(z,0)²`` — a losing z leaves the best point
    in H₁'s support at μ → 0, i.e. LR = 1.

    Returns None when either arm has n < 2 or zero pooled variance —
    the statistic honestly does not exist; the verdict records null,
    not a fabricated rung. Same Gaussian model required_n records on
    its return, so no new assumption enters. Grounding: 05/06 (the
    retrodesign's machinery), 08 (the BF the rungs measure).
    """
    nc, nl = len(champion_vals), len(challenger_vals)
    if nc < 2 or nl < 2:
        return None
    vals = list(champion_vals) + list(challenger_vals)
    if any(
        not isinstance(v, (int, float))
        or isinstance(v, bool)
        or not math.isfinite(v)
        for v in vals
    ):
        raise UngroundedDecisionBasis(
            "arm_evidence requires finite numeric samples — unscorable "
            "values are refused at record time; a surviving one is a "
            "corrupt record, not an input to paper over"
        )
    m_c = sum(champion_vals) / nc
    m_l = sum(challenger_vals) / nl
    var_c = sum((v - m_c) ** 2 for v in champion_vals) / (nc - 1)
    var_l = sum((v - m_l) ** 2 for v in challenger_vals) / (nl - 1)
    sd_pooled = math.sqrt(
        ((nc - 1) * var_c + (nl - 1) * var_l) / (nc + nl - 2)
    )
    if sd_pooled == 0:
        return None
    z = (m_l - m_c) / (sd_pooled * math.sqrt(1 / nc + 1 / nl))
    bf_2ln = max(z, 0.0) ** 2
    std = NormalDist()
    return {
        "z": z,
        "p": 1.0 - std.cdf(z),
        "bf_2ln": bf_2ln,
        "computed_rung": rung_for_2lnbf(bf_2ln),
        "n_champion": nc,
        "n_challenger": nl,
        "champion_mean": m_c,
        "challenger_mean": m_l,
        "assumptions": (
            "normal noise, pooled variance, one-sided challenger-favored "
            "test; the bound is the most generous reading of the data"
        ),
    }


def verdict_posterior(prior: float, p: float) -> float:
    """NAP Table D-1 posterior bound: P[H₁|data] from stated prior
    and one-sided p via the oracle LR bound BF = exp(max(z,0)²/2),
    z = Φ⁻¹(1−p).

    This is an UPPER bound — the most favorable reading of the
    evidence (the alternative sits at the observed effect). Minted
    confidence reads "the evidence cannot support more than this";
    it is not a calibrated posterior (04 Morey; 09 Gneiting–Raftery
    governs the honest labelling). p > 0.5 → z < 0 → BF = 1 →
    posterior = prior — evidence against H₁ cannot mint support
    for it. Reproduces the transcribed table: prior 0.3, p 0.05 →
    0.624; prior 0.5 → 0.795.
    """
    if (
        not isinstance(prior, (int, float))
        or isinstance(prior, bool)
        or not 0 < prior < 1
    ):
        raise UngroundedDecisionBasis(
            f"prior must be in (0, 1), got {prior!r}"
        )
    if (
        not isinstance(p, (int, float))
        or isinstance(p, bool)
        or not 0 <= p <= 1
    ):
        raise UngroundedDecisionBasis(
            f"p must be in [0, 1], got {p!r}"
        )
    # p = 0 is float underflow on 1 − Φ(z) at extreme z — the oracle
    # bound saturates: the evidence permits up to certainty.
    if p == 0:
        return 1.0
    z = NormalDist().inv_cdf(1 - p) if p < 1 else 0.0
    return posterior_from_2lnbf(prior, max(z, 0.0) ** 2)


def posterior_from_2lnbf(prior: float, bf_2ln: float) -> float:
    """Same NAP D-1 bound, computed from the persisted 2 ln BF
    directly — avoids the p → z → BF round-trip losing the
    statistic when p underflows to 0.0 at extreme z."""
    if (
        not isinstance(prior, (int, float))
        or isinstance(prior, bool)
        or not 0 < prior < 1
    ):
        raise UngroundedDecisionBasis(
            f"prior must be in (0, 1), got {prior!r}"
        )
    if (
        not isinstance(bf_2ln, (int, float))
        or isinstance(bf_2ln, bool)
        or bf_2ln < 0
        or math.isnan(bf_2ln)
    ):
        raise UngroundedDecisionBasis(
            f"bf_2ln must be a non-negative number, got {bf_2ln!r}"
        )
    try:
        bf = math.exp(bf_2ln / 2)
    except OverflowError:
        return 1.0
    odds = prior / (1 - prior) * bf
    if math.isinf(odds):
        return 1.0
    return odds / (1 + odds)


def required_n(
    sesoi_d: float,
    target_power: float,
    alpha: float = _ALPHA_DEFAULT,
    source: str = "declared",
) -> dict:
    """Per-arm sample size implied by a contract-declared SESOI.

    Two-sample closed form, normal approximation, equal arms:
    ``n = ceil(2 * (z_{1-alpha/2} + z_{power})^2 / sesoi_d^2)``.

    The SESOI is the smallest effect worth detecting — a declaration
    (Bakker et al. 2020), not an estimate. ``source="pilot"`` refuses:
    pilot-derived effect sizes are the biased input Albers & Lakens
    (2018) indicted. Grounding: 05/06 (why power is not enough),
    07 (the pilot prohibition), 11 (declare it before data exists).
    """
    if source == "pilot":
        raise UngroundedDecisionBasis(
            "pilot-estimated SESOI is the biased input Albers & Lakens "
            "(2018) indicted — the contract declares the smallest "
            "effect worth detecting; it does not inherit a pilot's."
        )
    if (
        not isinstance(sesoi_d, (int, float))
        or isinstance(sesoi_d, bool)
        or not math.isfinite(sesoi_d)
        or sesoi_d <= 0
    ):
        raise UngroundedDecisionBasis(
            f"sesoi_d must be a positive finite number, got {sesoi_d!r}"
        )
    if not 0 < target_power < 1:
        raise UngroundedDecisionBasis(
            f"target_power must be in (0, 1), got {target_power!r}"
        )
    if not 0 < alpha < 1:
        raise UngroundedDecisionBasis(
            f"alpha must be in (0, 1), got {alpha!r}"
        )
    std = NormalDist()
    z_a = std.inv_cdf(1 - alpha / 2)
    z_p = std.inv_cdf(target_power)
    n = math.ceil(2 * (z_a + z_p) ** 2 / sesoi_d**2)
    return {
        "n_per_arm": n,
        "sesoi_d": sesoi_d,
        "target_power": target_power,
        "alpha": alpha,
        "assumptions": (
            "normal noise, equal arms, two-sided test "
            "(closed form — the heavy-tailed correction is a "
            "research item, not a constant)"
        ),
    }


def type_s_m(
    sesoi_d: float,
    n_per_arm: int,
    alpha: float = _ALPHA_DEFAULT,
) -> dict | None:
    """Type S (sign-error) risk and Type M (exaggeration) ratio at the
    achieved n under the declared SESOI — Gelman & Carlin (2014)'s
    retrodesign, conditioned on the assumption, not the observation
    (Lakens et al. 2026).

    Returns None when n_per_arm < 1 — a run that delivered nothing
    carries no error profile, and null is the honest record.
    """
    if n_per_arm < 1:
        return None
    if not isinstance(sesoi_d, (int, float)) or sesoi_d <= 0:
        raise UngroundedDecisionBasis(
            f"sesoi_d must be positive, got {sesoi_d!r}"
        )
    std = NormalDist()
    z = std.inv_cdf(1 - alpha / 2)
    se = math.sqrt(2 / n_per_arm)  # standardised d, equal arms
    lam = sesoi_d / se
    power = std.cdf(lam - z) + std.cdf(-lam - z)
    phi = lambda x: math.exp(-(x * x) / 2) / math.sqrt(2 * math.pi)
    # E[|X̂|] over the rejection region, X̂ ~ N(sesoi_d, se²)
    hi = sesoi_d * std.cdf(lam - z) + se * phi(z - lam)
    lo = sesoi_d * std.cdf(-z - lam) - se * phi(z + lam)
    return {
        "type_s_risk": std.cdf(-lam - z) / power if power else 1.0,
        "type_m_ratio": (
            (hi - lo) / (power * sesoi_d) if power else None
        ),
        "power": power,
        "assumptions": (
            "normal noise, equal arms, two-sided test; conditioned "
            "on the declared SESOI, not the observed effect"
        ),
    }
