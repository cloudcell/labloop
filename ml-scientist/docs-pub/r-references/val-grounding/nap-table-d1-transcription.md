# Transcription — NAP (2019), Appendix D, Table D-1

**Status:** transcription, not an archived file. The source report is free to
read at nap.nationalacademies.org but the PDF requires a (free) account; it was
read in the publisher's online reader by the lab audit agent, and the rows
below were re-transcribed here from the audit's own rendering in
`GROUNDING-PROPOSAL.md` §3.1–3.2. This makes the evidence **second-hand**: the
values were not re-verified against the publisher page during this ingest, and
any use that needs more precision than these rows should re-read the source.

## Source

National Academies of Sciences, Engineering, and Medicine (2019).
*Reproducibility and Replicability in Science.* Washington, DC: National
Academies Press. Appendix D (Bayes formulation), Table D-1.

## What the table is

Posterior probability that a hypothesis is true, P[H₁ | data], as a function of
the stated prior probability P[H₁] and the observed p-value. The audit relied
on the one-tailed p = 0.05 column and the p = 0.01 column's range.

## Transcribed rows (one-tailed p = 0.05)

| prior P[H₁] | 0.01 | 0.05 | 0.10 | 0.20 | 0.25 | 0.30 | 0.40 | 0.50 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| posterior P[H₁\|data] | 0.038 | 0.169 | 0.301 | 0.492 | 0.563 | 0.624 | 0.721 | 0.795 |

At p = 0.01 the posterior range across priors is approximately 0.13 to 0.94
(audit's rendering; the full grid was not transcribed — see source).

## Sentences the audit relies on

The report discusses the 0.30 prior row explicitly as *"its pre-experimental
probability of being true is about 1 in 3."*

And the calibration principle the audit quotes against `VERDICT_CONFIDENCE`:

> *"It is clearly inappropriate to apply the same confidence to the results of
> a study with a highly unexpected and surprising result as in a study in which
> the results were a priori more plausible."*

## What this grounds in the registry

- `PRIOR_CONFIDENCE_MAX = 0.3` — IN-RANGE: the table contains the 0.30 row and
  names its meaning, but the table does not *recommend* a prior. The registry
  records this honestly as one row of a tabulated family.
- `verdict_posterior` procedure — the 0.624 figure for prior 0.3, p = 0.05,
  and the 0.795 upper bound at p = 0.05 which shows that a flat 0.85 cannot be
  reached by any evidence of the kind the system collects.
