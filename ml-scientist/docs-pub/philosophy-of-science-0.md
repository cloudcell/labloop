# The Philosophy of Science Underneath ml-scientist

*Draft — public explainer. The governed internal version of these
ideas lives in `docs/a-standards/a-00-ecosystem-philosophy.md`.*

---

Machine learning is an empirical science, not a programming
discipline. Progress in ML comes from running experiments, observing
evidence, and revising beliefs — not from reasoning forward from
axioms. Every piece of software in this ecosystem exists because of
that single claim, and each of its mechanisms descends from a specific
philosopher of science. This document explains the lineage.

## The unit of work is the experiment

Before the philosophers: the structural commitment. If ML is empirical
science, then the unit of work is not a commit, a notebook, or a
checkpoint. It is an **experiment** — a hypothesis rendered as a
configuration, executed to produce evidence, and judged against a
prior belief:

```text
hypothesis → designed experiment → execution → observation
           → statistical analysis → verdict → next experiment
```

An activity that does not close this loop — observations untied to
hypotheses, hypotheses never risked against observation — is
engineering, anecdote, or rhetoric. Whatever else it is, it is not
science. The software's first job is to make the loop itself the
irreducible object that tools operate on.

## Peirce: abduction — where hypotheses come from

Charles Sanders Peirce distinguished three kinds of inference:
deduction (what must follow), induction (what probably follows), and
**abduction** — the inference to the best explanation, the leap that
proposes a hypothesis worth testing at all. Abduction is the only mode
that *generates* new content; deduction and induction merely evaluate
it.

This is where the language model sits in the architecture — and it is
the *only* place where free reasoning is appropriate. Formulating a
hypothesis is genuinely creative work: surveying a result, noticing an
anomaly, guessing a mechanism. The system treats the agent as the
abductive engine and then immediately constrains it: a hypothesis must
be registered with explicit, numeric, falsifiable accept/reject
criteria *before* any trial runs. Creativity is welcome; unfalsifiable
creativity is not admitted to the loop.

## Popper: falsifiability is the demarcation criterion

Karl Popper's contribution is routinely misread as "scientists try to
disprove themselves." His actual claim is sharper: a claim is
scientific only if there *exists* an observation that would force its
rejection. Falsifiability is the boundary between science and
everything else — not a virtue scientists happen to practice, but the
property that makes an assertion testable at all.

Translated to ML: "depth improves generalization" is scientific only
if we specify in advance the observation that would make us abandon
it. A hypothesis that survives every outcome — "depth helps, or the
run was unlucky, or the data was bad" — is not a hypothesis. It is an
excuse with unlimited epicycles.

This is enforced, not advised. `formulate_hypothesis` rejects a
hypothesis with no `failure_criterion`. `conclude_hypothesis` requires
evidence attached to a verdict. A pipeline bug is `inconclusive`, never
a dishonest `rejected` that falsifies the hypothesis unfairly. The
demarcation criterion is code.

## Duhem–Quine: experiments test bundles, not claims

Pierre Duhem and Willard Van Orman Quine observed that an experiment
never tests a hypothesis in isolation. It tests a **bundle**: the
hypothesis plus every auxiliary assumption — the data pipeline is
correct, the metric measures what we think, the baseline was fair, the
seeds were controlled. When the observation contradicts the
prediction, *any* member of the bundle may be at fault.

This is the single most violated principle in applied ML. A negative
result is blamed on "the architecture" when the true culprit is a
silent preprocessing bug or an undertuned baseline; a positive result
is credited to a method when the real cause is a confound in the
evaluation.

The system's answer is to seal the bundle *before* execution.
`capture_bundle` content-addresses the exact code, environment,
seeds, splits, and data references a trial will run under — hashed
and immutable before the first gradient step. When a result arrives,
"what actually ran" is a lookup, not a memory. And the enforcement
runs both ways: a result can only be *attributed* to the varied factor
if the auxiliaries genuinely did not move, which is why compared arms
must share the bundle's controlled parts.

## Lakatos: research programmes, not single shots

Imre Lakatos observed that real science does not progress by killing
one hypothesis and inventing another. It progresses through **research
programmes**: a hard core of protected assumptions surrounded by a
protective belt of adjustable auxiliaries. A programme is
*degenerating* when it survives only by patching the belt after every
result; it is *progressive* when it predicts novel facts that get
confirmed.

An ML research line is identical. "Scaling computes" is a hard core;
the architecture, dataset, and optimizer are the belt. The health
question — are we learning or merely patching? — is invisible in a
flat run log. It requires memory *above* the level of individual
experiments.

So the programme is a first-class entity, not a folder convention.
Hypotheses, trials, observations, beliefs, and verdicts all hang off a
`programme_id`; `assess_programme` reads the whole trajectory and
reports whether it is progressive or degenerating. Degeneration — the
endless post-hoc belt-patching that flat logs hide — becomes a
reportable condition.

## Bayesian epistemology: belief is a quantity, not a verdict

The Bayesian view reframes the loop as belief updating: a prior over
hypotheses is revised by evidence into a posterior, which becomes the
prior for the next experiment. The operational payoff is that "how
surprised should we be?" becomes a measurable question rather than a
feeling.

The consequence for the software: an experiment that cannot move a
belief is wasted compute. `update_belief` is a mandatory loop stage,
positioned between observation and conclusion — a trial that produces
numbers but changes nothing epistemically has not done science. Belief
state is recorded per programme, so what the system *thinks* it knows
is itself an auditable object with provenance.

## Reproducibility: the price of admission

ML has a chronic reproducibility problem — identical configurations
yield different results across seeds, hardware, and library versions,
and "the run" is often a single draw from a high-variance distribution
treated as a point estimate.

This is not a nuisance; it is a violation of Duhem–Quine. If the
bundle is not controlled, the outcome cannot be attributed to the
varied factor, and a result that cannot be attributed cannot falsify
anything. Reproducibility is therefore a *precondition for evidence*,
not a best practice: `record_observation` rejects single point
estimates with zero variance; captured artifacts are content-addressed
so "what ran" is verifiable; and integrity checks hunt for
reproducibility debt — missing bundles, unsealed executions —
continuously, not just at audit time.

## The recursive turn: methodology under test

The philosophers above govern the *object-level* loop: hypotheses
about the world. But the ecosystem runs the same structure one level
up. The improver — the policy that proposes, runs, and judges
experiments — is itself a versioned entity with lineage, subjected to
tournaments between a champion and a challenger under pre-registered
meta-contracts. `recursive_gain` measures whether a child improver's
descendants outperform its parent's — Lakatos's "progressive vs
degenerating" question asked of the *method itself*.

There is no unrestricted self-modification. Change enters as typed
proposals through an admission gate that classifies what each change
touches: immutable kernel (rejected), conditionally modifiable
(human-gated), or recursively modifiable (admitted). The methodology
improves the way science improves — by registered, falsifiable,
judged experiment — not by silent mutation.

## Honest records: the record cannot lie to itself

A final philosophical commitment, less famous but load-bearing:
*records are insert-only, and claims must be verifiable.* Observations
are never rewritten — a mistake is repaired by a new, attributed
record that supersedes it, preserving the trail. Artifact digests are
not free strings: a registered `sha256:` must resolve to bytes the
system actually holds, or be an explicit `none`. A well-formed hash of
nothing is a claim with no referent, and it is rejected — the system
does not take an agent's word for what it can check.

This is Popper's criterion applied to the record-keeping itself: a
claim stored in the system is scientific only insofar as it could, in
principle, be shown false by the evidence the system holds.

## The commitments, mechanically

| Philosopher | Thesis | Mechanism |
|---|---|---|
| Peirce | Abduction generates hypotheses | The agent as creative engine; hypothesis registration bounds the claim |
| Popper | Falsifiability demarcates science | `failure_criterion` required; evidence-backed verdicts only |
| Duhem–Quine | Tests are of bundles | `capture_bundle` seals code/env/seeds/splits before execution |
| Lakatos | Programmes, not shots | `programme` entity; progressive/degenerating assessment |
| Bayes | Belief is a quantity | `update_belief` as a mandatory stage, per-programme |
| — | Reproducibility preconditions evidence | Variance required; content-addressed artifacts; integrity audits |
| (recursive) | Method under the same test | Versioned improvers, meta-contracts, `recursive_gain` |
| — | Records must not lie | Insert-only trails; corrections as events; digests that must resolve |

The software is, in a precise sense, these theses compiled: not a
tool that *supports* the scientific method, but one that *refuses to
let the loop be skipped* — and, at the meta-level, refuses to exempt
its own methodology from the same discipline.
