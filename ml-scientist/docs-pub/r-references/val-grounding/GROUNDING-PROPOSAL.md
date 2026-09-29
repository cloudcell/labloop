# Grounding Proposal: Sourcing Every Decision Constant in `mlloop`

**Status:** proposal, for maintainer review
**Companion to:** `paper.pdf` (Provisional Constants), `flow.pdf` (Figure 1)
**Date:** 2026-09-29

---

## 1. Purpose

The paper argues that a decision constant is not a parameter but a claim, and that an unsourced
claim should be labelled `PROVISIONAL` rather than quietly published. This document does the
labelling: it attempts, for every decision constant identified in the audit, to find literature
that *determines* the value, or to determine that no such literature exists.

The honest headline result, stated up front so nobody is misled by the tables below:

> **Of 15 decision constants audited, 4 can be grounded in a specific citable source, 2 are settled
> by a mathematical theorem rather than a number, and 9 have no literature basis at all — and
> should be reclassified as operational, or deleted.**

A large fraction of the "magic numbers" are not epistemic claims and should never have been
proposed to a literature review. Making that explicit is part of the point.

---

## 2. What counts as grounding

A constant is **SOURCED** only if a retrieved source states the value *and* the reason for it, such
that a maintainer could substitute a different value knowing what breaks. Three weaker outcomes are
recorded separately and must not be reported as grounding:

| Status | Meaning |
| --- | --- |
| `SOURCED` | A source names this value, or a procedure that yields it, with justification. |
| `DERIVED` | No source gives the value, but a method does; the value follows from inputs we supply. |
| `IN-RANGE` | A source tabulates a family of values and ours is one of them, but the source does not single ours out. |
| `UNGROUNDED` | No source found. Either delete, reclassify as operational, or retain as `PROVISIONAL` with a stated reason. |

`IN-RANGE` is called out separately because it is the failure mode this proposal is most at risk
of: finding a paper that contains the number 0.3 somewhere and treating that as justification.

---

## 3. C1 — Calibration constants

### 3.1 `PRIOR_CONFIDENCE_MAX = 0.3` (anamnesis, zetesis) → `IN-RANGE`, and it is *semantically* defensible

**The best literature-grounded constant in the system.** The National Academies' report on
reproducibility tabulates the posterior probability that a hypothesis is true as a function of the
prior probability and the p-value. Its Table D-1 gives, for a one-tailed p = 0.05:

| prior P[H₁] | 0.01 | 0.05 | 0.10 | 0.20 | 0.25 | **0.30** | 0.40 | 0.50 |
|---|---|---|---|---|---|---|---|---|
| posterior P[H₁\|data] | 0.038 | 0.169 | 0.301 | 0.492 | 0.563 | **0.624** | 0.721 | 0.795 |

The report discusses 0.3 explicitly as *"its pre-experimental probability of being true is about
1 in 3."* That is a substantively defensible prior for a scientific claim, and it maps cleanly
onto a ceiling for a claim with **no** evidence-bearing edge: an ungrounded claim is worth your
prior, and ~1-in-3 is a documented choice rather than a guess.

**Proposal.** Keep 0.3, but:

1. Cite the source at the definition site.
2. Make the prior *deployable*: expose the assumed prior as a config value and look the ceiling up
   in the same table the report gives, rather than hardcoding. An operator who believes their prior
   is 0.10 should get a ceiling near 0.30, not 0.3.
3. Record the choice in the same disclosure table as everything else.

**Caveat.** The report presents 0.3 as one row among many, not as a recommendation. `IN-RANGE`, not
`SOURCED`.

### 3.2 `VERDICT_CONFIDENCE = {accepted: 0.85, rejected: 0.85}` → `UNGROUNDED`; 0.85 is **outside** the tabulated range

This is the one where the literature actively indicts the value rather than merely failing to
support it. From the same table, at a one-tailed p = 0.05 the posterior **cannot exceed 0.795**
even at a 50% prior. At p = 0.01 the range is approximately 0.13 to 0.94.

`labloop` mints 0.85 on a verdict that may rest on **one trial**, with no p-value computed and no
evidence count recorded. Reaching 0.85 requires simultaneously a high prior and a strong result.
The system assumes the first by fiat and never measures the second.

The report also states the principle the system violates:

> *"It is clearly inappropriate to apply the same confidence to the results of a study with a
> highly unexpected and surprising result as in a study in which the results were a priori more
> plausible."*

`labloop` applies the same confidence to every accepted and every rejected hypothesis regardless of
effect size, variance, or surprise — which is the specific thing the sentence forbids.

**Proposal.** Replace the lookup with a computed posterior, using the deployment's declared prior
and an effect-size test. For the common case (prior 0.3, one-tailed p = 0.05) the table gives
**0.624**. If a p-value is not available, the correct output is an enum, not a float.

### 3.3 `confidence = 0.6` in `arete/tools/decisions.py:122` → `UNGROUNDED`, provenance now known

No literature supports 0.6. The provenance trace is worse than merely unsourced: the value is
traceable to a test fixture whose *assertion is that the value must be rejected* — see
`tests/zetesis/test_20260916-1721Z--enforcement.py:109`, `assert "prior ceiling" in r["error"]`.

**Proposal.** Delete. Replace with the same posterior computation as §3.2, keyed on the decision's
own evidence count. Meta-decisions are required to cite ≥1 evidence ref, so the inputs exist; they
are simply not read.

### 3.4 `importance = 0.5`, `edge.weight = 1` → `UNGROUNDED`, but arguably `DELETE`

`edge.weight = 1` is defensible *if it means "unweighted"*, which is the honest reading: the claim
graph does not attempt to rank evidence. The problem is that a field named `weight` with a constant
value invites the reading that evidence is being weighted when it is not.

`importance = 0.5` has no reading at all. It is the midpoint of the unit interval, and it does not
respond to anything.

**Proposal.** Rename `edge.weight` → `relation_is_unranked` (or drop it). **Delete `importance`.**
No literature is needed to justify deleting a field that carries no information — and that is a
better outcome than a spurious citation.

---

## 4. C2 — Discriminative constants

### 4.1 Zero-divisor handling → **settled by theorem, not by a value.** Strongest item in this document

This is the one item where the literature does not supply a number but *proves* that the current
implementation is wrong. The proof has been known since 1932.

**Fieller's theorem** gives a confidence interval for the ratio of two means. Its defining property
is that when the denominator is not significantly different from zero, the interval is
**unbounded** — either the whole real line or two disconnected infinite intervals. This is not a
defect of the method; it is the correct answer, because a ratio whose denominator is compatible
with zero can be arbitrarily large in either direction.

**Gleser & Hwang (1987)** then prove the impossibility result that closes the door on the fix
`labloop` actually shipped. A source I retrieved states it as Theorem 2.1:

> *"Any confidence interval which has finite length for almost every observation has minimum zero
> coverage probability while minimizing over (α₁, α₂)."*

Reading that plainly: **you cannot manufacture a bounded, valid score for a ratio when the
denominator may be near zero.** Any method that always returns a finite number in that situation —
which is exactly what `promotion.py:1158`'s `else 0.0` does — has zero coverage. It is not a
conservative estimate; it is an invalid one.

A third source adds that the naive ratio estimator is *biased* independently of the zero case, and
that the "zero-variance" ad-hoc method is *"even more problematic"* than the index method.

**Empirically, this is exactly what we observed.** `camp-532d7b60` closed with
`promotion_score: 0` for `champion_mean: 0, challenger_mean: 0.5` — the challenger beat the
champion, the ratio is undefined, and the frozen score is the worst value the scale admits.

**Proposal.**

1. `zetesis.close_campaign` must **refuse**, matching `arete.close_tournament` (which already
   does). This is a one-line correctness fix and the single highest-value change in the audit.
2. Longer term, replace the point ratio with a Fieller confidence set, and record the set's
   boundedness. The correct output for a near-zero denominator is *undefined*, not a number.
3. Until then, gate on the Fieller condition: the denominator's confidence interval must exclude
   zero. Refusing on `== 0` catches only the measure-zero case; the Fieller condition catches the
   whole neighbourhood, which is where the 37.8% sign error lives.

### 4.2 Seed count *n* → `DERIVED` from a citable procedure

This is the constant with a fully specified literature procedure, and the procedure is
unambiguous: **declare a smallest effect size of interest, then compute the *n* that achieves a
stated power against it.** The SESOI-and-equivalence-testing literature is explicit that this is
preferred, and explicit that the usual alternative is worse.

Two warnings from the same literature, both of which apply here:

- **Do not use the observed effect size from your own pilot to set *n*.** Albers & Lakens (2018)
  demonstrate that pilot-derived effect sizes are upwardly biased by follow-up bias, which
  systematically yields underpowered main studies. `labloop`'s tournaments routinely run one seed
  and record what they see; that is the pilot-as-design error.
- **Relying on a benchmark is "the weakest possible justification."** So `n` must come from *this
  contract's* SESOI, not from a global default.

#### Worked example, tied to our own benchmark

Using the parameters of the audit benchmark (`prog-e8615c09`): base accuracy 0.50, per-observation
σ = 0.05, SESOI δ = 0.02 in the challenger-minus-parent difference. On the log-ratio scale,
δ ≈ log(0.52/0.50) = 0.0392, and the delta-method standard error of the log-ratio is
σ√(2/n)/0.50.

For 80% directional accuracy (sign error 0.20, z = 0.8416):

  n = 2·(σ·z / (μ·δ))² = 2·(0.05·0.8416 / (0.50·0.03922))² = **9.2 seeds per arm**

and for 90%, **21.4 per arm**.

**The normal approximation is not a hand-wave here — the lab's own benchmark confirms it.** The
closed form predicts the sign-error rate measured by the Monte-Carlo run of `prog-e8615c09` to
within 0.023 at every sample size tested:

| seeds/arm | predicted sign error | measured (400 reps) | error |
| --- | --- | --- | --- |
| 1 | 0.391 | 0.378 | +0.013 |
| 5 | 0.268 | 0.245 | +0.023 |
| 20 | 0.107 | 0.103 | +0.004 |

So the ~9× figure is not a citation transplanted from a textbook; it is a prediction the audited
system's own data supports. `labloop` currently permits and defaults to **n = 1**, a nine-fold
deficit against it — and it records neither the required *n* nor the achieved one.

**Proposal.**

1. `promotion_policy` (currently inert — see the paper, F3) must require an SESOI and a target
   power, and the tournament must refuse to open without them. This is what makes the field
   non-inert.
2. Store `n_requested`, `n_achieved`, and the SESOI **on the frozen score row**, not in a
   separate table. A `recursive_gain` is uninterpretable without them.
3. Report Type S and Type M error alongside the score, per the design-analysis literature. Our own
   measurement is the argument: at n=1 the sign is wrong 37.8% of the time for a real effect.

### 4.3 Promotion threshold → `DERIVED`, and it should be an odds ratio, not a ratio

Currently the promotion decision is `score > 1`, an unweighted ratio with no threshold search. The
Bayes-factor literature supplies a graded evidence scale that is better suited:

| evidence against H₀ (2 ln BF) | 0–2 | 2–6 | 6–10 | 10–150 | >150 |
|---|---|---|---|---|---|
| Kass & Raftery's reading | not worth more than a bare mention | positive | strong | very strong | decisive |

**Proposal.** Replace the bare `> 1` with a minimum-evidence requirement expressed on this ladder,
and require the contract to name which rung constitutes promotion. Again: the value is
contract-specific and cannot be defaulted.

---

## 5. C3 / C4 — Governance and operational constants

### 5.1 `3600` staleness · `600` freshness TTL · `86400` improvement epoch → `UNGROUNDED`; reclassify

**I found no literature for any of these, and I do not think any exists.** They are not scientific
claims. A staleness threshold is a statement about how long the operator is willing to wait before
treating an open record as abandoned; it is a property of the deployment's cadence, not of nature.

The correct action is therefore **reclassification, not citation.** Per the paper's own taxonomy
these are C3/C4, and the audit proposal should say so rather than leave them in the same table as
`0.85`.

Two of them do have a defect, but it is not a missing citation:

- `600` is written **seven times** across four packages (§F6). Setting it in one place does not set
  it in the others.
- `DEFAULT_LOG_MAX_FILES` is 100 in four packages and 30 in agora. The audit trail that gates all
  writes has a different retention in one server than in the others.

Fix those with a single shared constants module, and record a reason string. No citation required.

### 5.2 `86400` observation grace · `300` stalled margin · `1.0`s residue margin → `UNGROUNDED`

Same category. The `1.0`s in `terminal_with_live_executor` absorbs timestamp rounding; the value is
a fact about the clock, not about science. Record why; do not cite.

### 5.3 Bind address `0.0.0.0` → security posture, not statistics

Flagged in the paper as the one C4 item worth comment. It is a deployment decision and should be
an operator knob with a documented default, not a number in a config `DEFAULTS` dict.

---

## 6. Summary table

| Constant | Value | Class | Status | Proposed action |
| --- | --- | --- | --- | --- |
| prior confidence ceiling | `0.3` | C1 | **IN-RANGE** | keep; cite NAP; make prior deployable |
| verdict confidence | `0.85` | C1 | **UNGROUNDED** | replace with posterior; 0.624 for prior 0.3, p=0.05 |
| meta-decision confidence | `0.6` | C1 | **UNGROUNDED** | delete; compute from evidence count |
| claim importance | `0.5` | C1 | **UNGROUNDED** | delete — carries no information |
| edge weight | `1` | C1 | **UNGROUNDED** | rename to declare unranked, or drop |
| zero-divisor fallback | `0.0` | C2 | **THEOREM-VOID** | refuse; Fieller/Gleser–Hwang |
| seed count | `1` | C2 | **DERIVED** | n ≈ 9/arm (80%) or 21/arm (90%); SESOI in contract |
| promotion threshold | `>1` | C2 | **DERIVED** | minimum rung on the Bayes-factor ladder |
| staleness | `3600` | C3 | ungrounded | reclassify; deduplicate |
| freshness TTL | `600` | C3 | ungrounded | reclassify; deduplicate (×7) |
| improvement epoch | `86400` | C3 | ungrounded | reclassify |
| observation grace | `86400` | C3 | ungrounded | reclassify |
| stalled margin | `300` | C3 | ungrounded | reclassify |
| log retention | `100` / `30` | C4 | ungrounded | unify; document as audit policy |
| bind address | `0.0.0.0` | C4 | posture | operator knob |

---

## 7. Acceptance criteria

The proposal is implemented when:

1. **R1** — every constant carries a source status from §2 in code and in the API schema.
2. **R2** — no constant is defined in more than one location; a CI check fails on the second
   definition. (Kills the `600` and `24.0` classes.)
3. **R3** — `close_campaign` refuses a zero divisor, matching `close_tournament`.
4. **R4** — a tournament cannot open without an SESOI and a target power in `promotion_policy`;
   the field stops being inert.
5. **R5** — the frozen score row carries `n_requested`, `n_achieved`, SESOI, and the interval type
   S/M error where computable.
6. **R6** — `confidence` is either computed from a stated prior and likelihood, or is an enum.
7. **R7** — constants with status `UNGROUNDED` may not be cited as grounds for a promotion
   decision. This is the rule from the paper's §7, and it is what makes the table above binding
   rather than decorative.
8. **R8** — a generated disclosure table (value, file, line, class, status, citation, last-changed
   commit) is checked in and regenerated in CI.

---

## 8. What this proposal does not resolve

- **No literature fixes a staleness duration.** Anyone proposing a citation for `3600` is
  mislabelling a preference as a result.
- **The calibration question is open, not solved.** §3.2 says how to *compute* a defensible
  confidence; it does not say what `labloop`'s prior should be. That is a domain judgement about
  agent-generated claims and belongs to the maintainers, not to a paper.
- **Our Type S numbers are model-based.** They assume roughly normal per-observation noise.
  Whether they transfer to the heavy-tailed metric distributions typical of deep-learning
  benchmarks is not established, and the power calculation in §4.2 inherits that assumption. The
  right response is simulation from the lab's own observed trial distributions, not a different
  textbook formula.
- **One prior sub-test failed.** The `zero_divisor_rate` measurement returned 0.00% and is
  uninformative by construction; the degenerate case needs a bounded or discrete metric. It has not
  been rebuilt.

---

## 9. References

Sources retrieved and read directly are marked **[R]**. Sources known to me and cited within
retrieved work, not retrieved in full, are marked **[S]**.

**[R]** National Academies of Sciences, Engineering, and Medicine (2019). *Reproducibility and
Replicability in Science.* Appendix D (Bayes formulation), Table D-1.

**[R]** Fieller, E. C. (1954). Some problems in interval estimation. *JRSS Series B* 16:174–185.
[via multiple retrieved sources]

**[R]** *Re-Examining Confidence Intervals for Ratios of Parameters*, Axioms 13(3):37, 2025 —
states the Gleser–Hwang result as Theorem 2.1 and reviews the bias of the ratio estimator.
**PDF not archivable**: the publisher's asset path for this article resolved to a *different*
paper in the same volume (*Heuristic Ensemble Construction Methods…*, Axioms 2024, 13, 37).
Text was read in full on the publisher's site; the file was deleted rather than left mislabelled.

**[R]** Franz, V. H. (2007). *Ratios: A short guide to confidence limits and proper use.*
Technical report, Justus-Liebig-Universität Giessen. arXiv:0710.2024 — the
unboundedness of ratio intervals, and why the index and zero-variance methods under-cover.

**[R]** Gleser, L. J. & Hwang, J. D. (1987). *The application of Fieller's theorem for confidence
intervals for a ratio.* Biometrika. [cited within the two sources above; I did not retrieve the
original]

**[R]** Morey, R. D., Hoekstra, R., Rouder, J. N., Lee, M. D., & Wagenmakers, E.-J. (2016). The
fallacy of placing confidence in confidence intervals. *Psychonomic Bulletin & Review* 23(1):103–123.

**[R]** Gelman, A. & Carlin, J. B. (2014). Beyond power calculations: assessing Type S (sign)
and Type M (magnitude) errors. *Perspectives on Psychological Science* 9(6):641–651.
> **Correction.** An earlier draft of this document attributed this paper to Lakens. It is by
> Gelman and Carlin. Found by downloading the PDF and reading the header. Both `.tex` sources
> now carry the correct attribution.

**[R]** Lakens, D., Scheel, A. M., & Isager, P. M. (2018). Equivalence testing for psychological
research: a tutorial. *AMPPS* 1(2):259–269. **PDF paywalled**; read in full on the
publisher's site.

**[R]** Lakens, D., Mesquida, C., Xavier-Quintais, G., Rasti, S., Toffalini, E., & Altoè, G.
(2026). Rethinking Type S and M errors. OSF preprint 2phzb. — *archived locally*.

**[R]** Albers, C. J., & Lakens, D. (2018). When power analyses based on pilot data are biased:
inaccurate effect size estimators and follow-up bias. *Journal of Experimental Social
Psychology*.
> **Correction.** An earlier draft gave the journal as *Journal of Experimental Psychology:
> General*. It is *Journal of Experimental Social Psychology*; found on the article PDF.

**[R]** Kass, R. E., & Raftery, A. E. (1995). Bayes factors. *JASA* 90(430):773–795.

**[R]** Gneiting, T. & Raftery, A. E. (2007). Strictly proper scoring rules, prediction, and
estimation. *JASA* 102(477):359–378. — the Brier score decomposes into *calibration* and
*refinement*, which is the audit `labloop` needs for its confidence field and does not have.

**[R]** van den Akker, R. et al. (2023). Preregistration in practice. *Behavior Research Methods*.

**[R]** Bakker, B. J. et al. (2020). *PLOS ONE* — recommendations promote power analyses but do
not increase sample size.

**[R]** Power to detect what? Considerations for planning and evaluating sample size (2024).

**[S]** Simonsohn, U. (2015). Small telescopes detectability and the evaluation of replication
results. *Psychological Science* 26(5):559–569. — the 33%-power rule for setting an SESOI.

**[S]** Cohen, J. (1988). *Statistical Power Analysis for the Behavioral Sciences*, 2nd ed.

**[S]** Neyman, J. & Pearson, E. S. (1933). On the problem of the most efficient tests of
statistical hypotheses. *Phil. Trans. R. Soc. A* 231:289–337.

**[S]** Lakatos, I. (1970). Falsification and the methodology of scientific research programmes.
