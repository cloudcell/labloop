# GENESIS-RESEARCH-PROMPT — warm-up for a fresh lab VM's driver agent

Paste this whole block to the driver (Zoo Code / opencode) of a newly
created VM. It runs ONE complete scientific cycle end-to-end — a smoke
test of the entire research apparatus, not just of connectivity. Read
`AGENT-LAB-GUIDE.md` first if you haven't; this prompt assumes the zone
model it describes.

---

## Warm-up task: run the full cycle once

You are the research driver of a fresh lab. Before doing real work,
prove the apparatus works by running one *small, well-bounded* research
question through the complete loop. Pick something that finishes in
minutes on CPU **and compares at least two candidates** — an A/B by
construction (two optimizers, two regularizers, depth-2 vs depth-4,
a method vs its ablation — your choice, but it must produce *numbers*,
not prose). The comparison is deliberate: it makes the tournament
stage real instead of decorative.

### The cycle (do every step — skipping any is failure)

0. **Check for notes from a previous run.** Search
   `/home/lab/workspace/` for `*-ONBOARDING.md` — a previous agent
   may have left a signed note on how to drive this system (see
   step 11). If one exists, read it first: it is agent-authored
   knowledge, not operator documentation, and may save you real
   mistakes. A note accelerates orientation — it does **not**
   replace this cycle; run it anyway, then update the note with
   what changed.
1. **Status first.** Invoke agora's `status_report` **prompt**, then
   episteme's — these are MCP *prompts*, not tools; if your client
   doesn't surface them, read the `lab://status` (agora) and
   `protocol://status` (episteme) **resources** instead. All four
   servers must be healthy before you start; if not, stop and report
   that — do not research on a half-dead lab.
2. **Programme** (episteme): create one. Goal, constraints (CPU-only,
   wall-time, scale), allowed variables, `budget_max_trials`,
   `metric_direction`. A programme is a Lakatosian container — every
   later step hangs off it.
3. **Hypothesis** (episteme): register ONE falsifiable hypothesis.
   Write explicit numeric accept/reject criteria *before* running
   anything, and bound the claim to the regime you will test
   ("at tiny CPU scale, d≤64, synthetic data" — never claim the method
   generally; a tiny-scale rejection is not a method-level refutation).
4. **Trial(s)** — two execution planes; pick the right one:
   - **Recorded trials = episteme's executor** (evidence):
     `design_experiment` → `capture_bundle(code_ref=...)` →
     `run_trial`. Episteme runs the *sealed* bundle in its own
     sandboxed executor — results without an executor record are
     refused, so this path is what makes a trial count as evidence.
     The executor interpreter carries numpy/pandas/scipy/sklearn/
     matplotlib. **Path rule:** `code_ref` is resolved *inside the
     mcp container*, which sees only `/state` and `/exchange` —
     stage your code file at `/exchange/<name>/executor.py` before
     capture_bundle. `/workspace`, `/experiments`, `/home/lab` do not
     exist for it.
   - **Iteration = `labloop-exec`** (not evidence): developing,
     debugging, and exploratory runs go through `labloop-exec` into
     the hostile zone — uv venvs, scratch data, `/experiments`.
     When the code is ready, copy the executor file into `/exchange`
     and take it through the recorded path above.
   - **Never** run experiment code as `lab` on the VM itself — that
     is the driver zone.
   - `/workspace` and `/incoming` are READ-ONLY to experiments;
     `/exchange` and `/experiments` are the only writable zones.
   - Use the seeds/budgets your hypothesis committed to. Control the
     bundle: same data, same eval, same seeds across compared arms.
     **Don't** fire `design_experiment` calls in parallel — create
     the trial rows sequentially (a concurrency wrinkle there is
     being investigated).
5. **Observation** (episteme): record what the trial produced — metrics
   with variance, not a point estimate.
6. **Belief** (episteme): update the belief state — the observation must
   move it; if it can't, the trial was wasted compute.
7. **Conclusion** (episteme): verdict + evidence_ref.
   - `accepted` / `rejected` need the numeric evidence attached.
   - `inconclusive` is a real verdict: a broken evaluator, a NaN
     metric, a crashed run = inconclusive. Never mark a pipeline bug
     "rejected" — that falsifies the hypothesis unfairly; never mark
     it "accepted" either.
8. **Claim** (anamnesis): distill the durable knowledge as a claim.
   `empirical` (what was measured) vs `methodological` (what worked as
   procedure) — with honest confidence, not 1.0.
9. **Tournament** (arete) — REQUIRED whenever the hypothesis compares
   ≥2 candidates (yours does — see the task). Register the candidate
   versions, run the tournament, record `tournament_results`. The
   tournament output *is* your comparative evidence — cite it in the
   conclusion's evidence_ref. A winner by gut-feel instead of a
   recorded tournament is not a verdict.
   Optional: a zetesis finding, if the result is worth indexing for
   search.
10. **Report back** with: programme id, hypothesis id, trial count,
    tournament id + winner, verdict + the evidence summary, and the
    claim id. Then stage your
    artifacts: `labloop-export /exchange/<your-result-files>` —
    staging only; the human pulls from the host. Never try to transmit.
11. **Leave yourself a note.** Write a markdown document to
    `/home/lab/workspace/` titled with your own name
    (`<name>-ONBOARDING.md`) answering: *"after this warmup run, how
    would I prompt myself to quickly start working with this system?"*
    — what worked, what bit you, the call order that turned out to
    matter, the zones, the gotchas. Sign it with your name so a later
    session knows it came from an agent, not the operator. If a note
    from a previous run already exists, update it or write your own
    alongside — don't silently overwrite another agent's knowledge.
    Keep it out
    of `/exchange` — that zone is for result artifacts the human
    pulls, and the hostile container reads it.

### Hard rules (violating any of these invalidates the warm-up)

- **Never** read/copy `/srv/lab/mcp-state` or touch the `mcp` account —
  it is the trusted zone; you reach it only through MCP tools.
- **Never** `su exp` or `sudo -iu exp` — `labloop-exec` is the only
  path into the hostile zone.
- **Never** run `sudo labloop-export --all` — it quiesces the whole
  lab; that is a human operation.
- Trial code runs through episteme's executor on a dedicated
  interpreter (`/opt/trial-env`) that carries numpy, pandas, scipy,
  scikit-learn, matplotlib — importing them is expected to work. If a
  needed dependency is missing, note it and ask the operator; **never
  downgrade the science to fit the interpreter** — a dependency-free
  rewrite is not the hypothesis you were asked to test.
- Secrets stay in `/home/lab` mode `600` — never in `/exchange` or
  `/experiments` (the hostile zone reads those).
- Falsifiability is not optional: if your hypothesis cannot name the
  observation that would kill it, rewrite it before running.
- One variable moves per comparison — Duhem-Quine: if two things
  changed, you learned nothing attributable.

### What "done" looks like

A programme, a falsifiable hypothesis, ≥2 executed trials via
labloop-exec (one per arm), observations, a moved belief, a recorded tournament,
an evidence-backed verdict, an honest claim, staged artifacts in
the exchange — and a signed `<name>-ONBOARDING.md` in
`/home/lab/workspace/` for the next session. That is the shape every
real programme in this lab follows.
