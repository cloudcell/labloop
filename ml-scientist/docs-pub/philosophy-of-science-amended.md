# The Philosophy of Science Underneath ml-scientist

*Draft: public explainer. The governed internal version of these ideas lives in `docs/a-standards/a-00-ecosystem-philosophy.md`.*

---

Machine-learning research is ultimately answerable to empirical evidence. Mathematics, software engineering, and theory can generate mechanisms, predictions, and useful constraints, but claims about learned systems must eventually survive contact with experiment.

That commitment shapes the entire ml-scientist ecosystem.

The architecture described here is deliberately eclectic. It draws on several traditions in the philosophy of science, especially Peirce, Popper, Duhem–Quine, Lakatos, and Bayesian epistemology, without pretending that those traditions form one perfectly unified doctrine. The software does not "implement philosophy" in a literal sense. It takes selected methodological ideas and turns them into enforceable research mechanisms.

## The unit of work is the experiment

For empirical ML research, the natural unit of work is not a commit, a notebook, or a checkpoint. It is an **experiment**: a hypothesis rendered as an executable design, run under controlled conditions, observed, analyzed, and used to update what the system believes.

The canonical shape of that unit is a closed loop. A hypothesis is formulated; an experiment is designed to expose it; the trial executes; the outcome is observed and analyzed; belief is updated; a verdict is recorded, and the whole of it feeds the design of the next experiment.

Not every scientific activity has to begin with a preregistered hypothesis. Exploration, measurement, anomaly discovery, debugging, and instrument-building can all be legitimate parts of research.

But when ml-scientist makes a **confirmatory claim**, the loop must close. A hypothesis must be testable, the experiment must expose it to possible failure, the resulting evidence must be recorded, and the conclusion must be traceable to that evidence.

The software's first job is therefore to make the experiment loop a first-class object rather than a loose convention.

## Peirce: abduction, or where hypotheses come from

Charles Sanders Peirce distinguished three forms of inference:

- **abduction** proposes a plausible explanation for a surprising fact;
- **deduction** derives consequences that should follow if the hypothesis is true;
- **induction** evaluates those consequences against experience.

In this architecture, the language model primarily occupies the abductive role. It surveys results, notices anomalies, proposes mechanisms, and generates hypotheses worth testing.

That is the part of the system where open-ended reasoning is most useful.

But the system does not allow the abductive step to remain vague. Once a hypothesis enters the experimental loop, it must be registered in a form that can be tested. ml-scientist may impose stricter operational requirements than philosophy itself does (numeric thresholds when appropriate, for example) because software needs explicit decision rules.

The design principle is:

> creativity proposes; experiment constrains.

## Popper: falsifiability as an operational discipline

For Karl Popper, falsifiability was the key demarcation criterion for scientific claims: a claim is scientific only if some possible observation could count against it.

ml-scientist adopts this idea operationally.

"Depth improves generalization" is not yet a useful experimental claim. The system needs to know what observation would count as failure. A hypothesis that can survive every possible outcome by adding an after-the-fact excuse is not meaningfully exposed to test.

This is enforced rather than merely recommended.

`formulate_hypothesis` requires a `failure_criterion`. `conclude_hypothesis` requires evidence attached to the verdict. If a pipeline bug invalidates a trial, the result is `inconclusive`; the system does not turn an invalid execution into a false rejection of the hypothesis.

The distinction matters:

- **failed experiment**: the execution does not support a valid inference;
- **failed hypothesis**: a valid experiment produced evidence that crossed the preregistered rejection condition.

Popper supplies the discipline of exposure to failure. ml-scientist turns that discipline into an executable contract.

## Duhem–Quine: experiments test bundles, not isolated claims

Pierre Duhem and Willard Van Orman Quine emphasized a problem that is especially important in machine learning: experiments do not test hypotheses in isolation.

They test a **bundle** of assumptions:

- the implementation is correct;
- the data pipeline is correct;
- the metric measures what we think it measures;
- the baseline is fair;
- the seeds and splits are controlled;
- the environment is what we think it is.

When an observation contradicts a prediction, the failure may lie in the focal hypothesis or in one of these auxiliary assumptions.

This cannot be eliminated by software. It can, however, be made explicit.

`capture_bundle` content-addresses the exact code, environment, seeds, splits, and data references used by a trial. The bundle is sealed before execution so that "what actually ran" becomes a lookup rather than a reconstruction from memory.

That does not prove the auxiliaries are correct. It does something more modest and more useful: it makes them inspectable and keeps controlled components fixed across comparisons.

Attribution then becomes a matter of evidence rather than recollection.

A result may be attributed to the varied factor only when the relevant auxiliaries remained stable enough for that inference to be credible.

## Lakatos: research programmes, not isolated shots

Imre Lakatos argued that science develops through **research programmes**, not through isolated hypotheses that are accepted or killed one by one.

A research programme contains:

- a relatively stable **hard core** of commitments;
- a **protective belt** of adjustable auxiliary assumptions;
- a history of predictions, revisions, successes, and failures.

A programme is **progressive** when its development produces novel, successful predictions or explanations. It is **degenerating** when it survives mainly through post-hoc adjustments that protect the core without increasing explanatory or predictive power.

An ML research line can be modeled this way.

A broad commitment such as "scaling compute improves capability" might function as part of a hard core. Architecture choices, datasets, optimizer settings, and training procedures may form parts of the protective belt.

The important question is not merely whether one run succeeded. It is whether the research line is learning something or merely accumulating rescue explanations.

A flat experiment log cannot answer that question.

So the **programme** is a first-class entity. Hypotheses, trials, observations, belief states, and verdicts are linked through a `programme_id`. `assess_programme` reads the trajectory across experiments and asks whether the programme is producing genuine progress or merely patching itself after the fact.

This is not a claim that Lakatos directly specified software such as `programme_id`. It is an engineering operationalization of his central insight: scientific progress is visible only across a sequence of related tests.

## Bayesian epistemology: belief as a quantity

Popper gives the system hard rejection conditions. Bayesian reasoning adds something different: a way to represent **graded belief** between decisive tests.

The architecture deliberately uses both.

A prior belief about a hypothesis is revised in light of evidence to produce a posterior belief. That posterior becomes part of the starting state for the next experiment.

The practical question is not only:

> did this trial cross a rejection threshold?

It is also:

> how much should this evidence change what we believe?

`update_belief` records that change explicitly.

This does not mean every experiment must produce a large posterior shift. Replications, calibration studies, variance estimation, and negative results may be valuable even when belief moves only slightly.

The stronger principle is:

> every experiment should have a specified potential epistemic consequence.

If the system cannot say what evidence would change its view, the experiment has not been connected clearly enough to the research programme.

Belief state is recorded per programme so that what the system currently takes itself to know is an auditable object rather than an implicit impression.

## Reproducibility: a precondition for credible attribution

Machine-learning experiments often vary across seeds, hardware, library versions, nondeterministic kernels, and hidden environmental state.

A single run can therefore be a poor basis for a scientific claim.

This is not, by itself, a "violation" of Duhem–Quine. Rather, it makes the Duhem–Quine problem operationally acute: if the auxiliary conditions are not controlled or recorded, the observed outcome cannot be cleanly attributed to the factor under study.

Reproducibility is therefore treated as a precondition for **credible attribution**.

The system distinguishes several cases:

- one observation, where empirical variance is not estimable;
- repeated observations with nonzero variance;
- repeated deterministic observations with zero observed variance.

`record_observation` should not confuse "one sample" with "zero variance." The relevant question is whether the evidence is statistically adequate for the claim being made.

Captured artifacts are content-addressed so that "what ran" can be verified. Integrity checks look for reproducibility debt such as missing bundles, unsealed executions, incomplete provenance, or inadequate replication.

The point is not to make every experiment perfectly reproducible. The point is to make uncertainty and uncontrolled variation visible before they are mistaken for evidence.

## The recursive turn: methodology under test

The same experimental discipline can be applied one level up.

At the object level, the system tests hypotheses about the world.

At the meta level, it can test the **methodology that generates and judges those experiments**.

The improver, the policy that proposes, runs, and evaluates experiments, is therefore versioned and lineage-tracked. Challenger methodologies can be tested against a champion under preregistered meta-contracts. `recursive_gain` measures whether descendants of a modified improver outperform descendants of its parent under the chosen evaluation regime.

This extends the Lakatosian question to the methodology itself:

> is the research process becoming more productive, or merely becoming more elaborate?

There is no unrestricted self-modification.

Changes enter as typed proposals through an admission gate that classifies what they touch:

- immutable kernel: rejected;
- conditionally modifiable: human-gated;
- recursively modifiable: admitted to experimental evaluation.

The methodology changes in the same way the system wants scientific claims to change: through explicit proposals, controlled tests, recorded evidence, and judged outcomes.

## Honest records: the record must be auditable

The final commitment is not primarily Popperian. It is about provenance, corrigibility, and referential integrity.

Records are insert-only. Observations are not silently rewritten. A mistake is corrected by a new attributed record that supersedes the old one while preserving the trail.

Artifact digests are not treated as decorative strings. A registered `sha256:` identifier must resolve to bytes the system actually holds, or the absence must be represented explicitly.

A well-formed hash that points to nothing is not evidence.

The governing principle is simple:

> if the system can verify a claim about its own research record, it should not merely trust the agent that asserted it.

This makes the record itself inspectable. Claims about experiments can be traced to observations; observations can be traced to executions; executions can be traced to bundles; bundles can be traced to concrete artifacts.

Scientific disagreement then occurs over evidence and inference rather than over what supposedly happened.

## The commitments, mechanically

| Source idea | Idea adopted here | Mechanism |
|---|---|---|
| Peirce | Abduction proposes explanatory hypotheses | Agent as creative engine; hypothesis registration converts ideas into testable objects |
| Popper | Claims must be exposed to possible failure | `failure_criterion`; evidence-backed verdicts; invalid trials become `inconclusive` |
| Duhem–Quine | Experiments test bundles of assumptions | `capture_bundle` seals code, environment, seeds, splits, and data references |
| Lakatos | Progress is visible across research programmes | `programme` entity; trajectory-level progressive/degenerating assessment |
| Bayesian epistemology | Evidence changes graded belief | `update_belief`; per-programme belief state |
| Experimental methodology | Attribution requires adequate control and replication | repeated observations, explicit variance handling, content-addressed artifacts, integrity audits |
| Recursive extension | Methodology itself can be tested | versioned improvers, meta-contracts, `recursive_gain` |
| Provenance discipline | Research records must be auditable | insert-only trails, superseding corrections, resolvable artifact digests |

## What this means in practice

ml-scientist is not merely a tool that helps researchers run experiments.

Its deeper purpose is to make certain methodological shortcuts difficult:

- hypotheses cannot silently change after results arrive;
- failed executions cannot masquerade as falsified hypotheses;
- experiments cannot be detached from the code and environment that produced them;
- research programmes cannot hide endless post-hoc patching inside a flat run log;
- belief changes cannot remain entirely implicit;
- methodological self-improvement cannot bypass evaluation;
- research records cannot be silently rewritten.

The software is therefore best understood as a set of methodological commitments compiled into mechanisms.

It does not guarantee good science.

It tries to guarantee that the structure of the scientific argument (hypothesis, test, evidence, inference, revision, and provenance) remains visible, inspectable, and difficult to bypass.
