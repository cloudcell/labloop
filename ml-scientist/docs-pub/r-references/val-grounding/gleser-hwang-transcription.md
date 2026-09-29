# Transcription — Gleser & Hwang (1987), impossibility result for ratio intervals

**Status:** transcription with a recorded citation discrepancy. The theorem
statement below is **second-hand**: the lab audit quoted it from *Re-examining
Confidence Intervals for Ratios of Parameters* (Axioms 13(3):37, 2025), whose
publisher asset path resolved to a different article in the same volume — the
downloaded PDF was deleted by the audit rather than left mislabelled, and the
text was read on the publisher's HTML page. The Gleser–Hwang original was not
retrieved by the audit (not open access) and has not been retrieved here.

## Citation discrepancy — recorded, not resolved silently

- The audit's `MANIFEST.md` cites: *"Gleser, L. J. & Hwang, J. D. (1987).
  Biometrika."* — with the descriptive title "The application of Fieller's
  theorem for confidence intervals for a ratio."
- The canonical impossibility result this theorem statement comes from is
  normally cited as: Gleser, L. J., & Hwang, J. T. (1987). *The nonexistence
  of 100(1−α)% confidence sets of finite expected diameter in
  errors-in-variables and related models.* Annals of Statistics 15(4):
  1351–1362.

These may or may not be the same paper — the venues differ and the audit's
title does not match the canonical one. Until the original is retrieved, the
registry cites the result via this file with the chain recorded, and the
`citation` field reads the canonical form marked "unverified venue/title —
audit manifest cites Biometrika". This is the same error class the corpus's
own verification pass caught five times; it is flagged, not papered over.

## The theorem statement (as quoted by the Axioms survey)

> *"Any confidence interval which has finite length for almost every
> observation has minimum zero coverage probability while minimizing over
> (α₁, α₂)."*

Plain reading for `close_campaign`/`promotion_score`: any procedure that
always returns a finite score for a ratio whose denominator may be
compatible with zero is not a conservative estimate — it has zero coverage.
The correct output for a near-zero denominator is *undefined*; a refusal is
the honest encoding.

## What this grounds in the registry

- `refuse_zero_divisor` procedure — `close_campaign` refuses rather than
  freezing a fabricated `promotion_score`, mirroring `close_tournament`.
  The companion result (Fieller's theorem: the ratio CI is unbounded when
  the denominator's interval includes zero) is archived in `02` (Franz 2007)
  and `14` (von Luxburg & Franz 2004) — those are the *archived* legs of the
  grounding; this file supplies only the impossibility quote.
