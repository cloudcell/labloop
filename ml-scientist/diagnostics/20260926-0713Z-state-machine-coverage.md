You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you. The freshness gate will interrupt you — re-read
the status resource when it tells you to, and carry on.

This session exercises EVERY state machine in the ecosystem against one
synthetic workload. The reference is docs/c-handbook/c-09-state-machines.md
— §6's transition table is your coverage matrix. Every row must be
exercised or explicitly dispositioned. Coverage, not throughput, is the
goal — and every refusal is a result, not an obstacle.

THE SYNTHETIC TASK: a fake champion-vs-challenger evaluation. All compute
is trivial — trial code that prints a metrics dict is enough; the value
is controlled by a config field so YOU decide who wins. Name every
entity you create with a "diag-" marker (hypothesis text, programme
titles, proposal summaries) so no synthetic record can be mistaken for
real science. Report everything verbatim — errors are data.

PART A — Loop 0 core FSM (episteme: programme, hypothesis, trial)

Drive one programme through completion while touching every transition:

1. create_programme → active.
2. NEGATIVE: formulate_hypothesis WITHOUT failure_criterion → refused
   (falsifiability gate). Report verbatim.
3. formulate_hypothesis diag-H1 (with failure_criterion) → proposed.
4. design_experiment on H1 → hypothesis under_test + trial T1 designed.
5. NEGATIVE: run_trial before capture_bundle → refused. Then
   capture_bundle → run_trial → running → completed (wait_trial or
   get_trial_status to observe the terminal landing).
6. NEGATIVE: conclude_hypothesis on H1 with no evidence → refused.
   Then record_observation → conclude_hypothesis(accepted) → accepted.
   Confirm a claim was minted to anamnesis.
7. More hypotheses for the sweep + verdict paths: diag-H2 stays
   proposed (never designed). diag-H3: design a trial (T2) so it's
   under_test, run it so the result falsifies H3, then
   conclude_hypothesis(rejected) → confirm the falsification claim is
   minted. diag-H4: under_test via a designed trial, then
   conclude_hypothesis(inconclusive) → confirm NO claim is minted
   (inconclusive mints nothing — verify via anamnesis).
8. Trial paths: T3 designed → running → cancel_trial → failed.
   T4 designed → running → mark_retryable → retryable. T5 designed,
   never run → abandoned by close sweep.
9. NEGATIVE: close_programme(status=completed) while H3 is under_test
   → refused (unconcluded hypothesis). NEGATIVE: leave one trial
   running and try close_programme → refused; then cancel it.
10. correct_trial_status on T1 (completed→completed is fine; the point
    is the corrections log) → confirm the record carries the appended
    correction, not a silent rewrite.
11. close_programme(status=completed) → completed → confirm the
    archiver moved it to archived (archive list / get). Confirm H2 and
    T5 were swept to abandoned via auto_marked.
11b. Second programme: create → propose a hypothesis →
    close_programme(status=abandoned) → abandoned; confirm the
    proposed hypothesis was swept to abandoned too.

PART B — Loop 0 promotion machinery (episteme; seeds zetesis' truth)

12. register_candidate (diag candidate) → create_evaluation_contract →
    record_promotion_decision with a promote verdict → confirm
    get_incumbent now reports an incumbent. This is the upstream truth
    zetesis' roster derives from.

PART C — anamnesis bi-temporal claims (no FSM — live/superseded/expired)

13. assert_claim (diag claim) → live. NEGATIVE: assert a claim at
    confidence > 0.3 with NO evidence edges → confirm the cap fires
    (unevidenced claims cannot be asserted strongly).
14. relate: claim→claim edge (verified ref) and claim→external ref
    (e.g. the cand-/trial- ids from Parts A–B — opaque, trusted).
    NEGATIVE: relate to a claim id that does not exist → refused
    (claim-typed refs are verified).
15. assert a second claim with supersedes_id → confirm the first shows
    superseded and the auto supersedes edge exists.
16. assert a claim with a past valid_until → confirm it reads expired
    and is hidden from the default view.

PART D — Loop 1 (zetesis: investigation, finding, campaign, spawn, roster)

17. refresh_roster → the Part-B candidate appears (candidate →
    challenger via register_challenger; champion status derives from
    the upstream promote verdict — refresh_roster again and confirm
    derived_status moved). For the rolled_back edge: record a rollback
    promotion_decision upstream on episteme, refresh_roster, confirm
    the entry reads rolled_back.
18. open_investigation → open. pull_evidence against episteme (the
    grounding trail — ≥1 evidence ref). record_finding F1 → provisional.
    record_finding F2 → provisional. drop_finding F2 → dropped.
    conclude_investigation → concluded; confirm F1 was asserted as a
    claim in anamnesis with its evidence trail.
19. open_investigation → abandon_investigation → abandoned (second
    investigation, no findings needed).
20. Campaign (the orchestration path): open_campaign on the incumbent
    contract → open. spawn_campaign_programme → spawned. NEGATIVE:
    close_campaign before both arms have results → refused.
    record_campaign_result for each arm (the spawn's programme must
    actually run on episteme — drive it through Part A's sequence).
    NEGATIVE: record_campaign_result for a programme whose
    candidate_version_id doesn't match the arm → refused.
    close_campaign → closed; confirm promotion_score is frozen on the
    record. record_promotion_verdict → claim minted.

PART E — Loop 2 (arete: proposal gate, tournament, decision, policy, canary)

21. propose_meta_change three times to hit all three admission
    outcomes: one touching a class-1 immutable surface → rejected at
    the gate (rejection_reason recorded); one touching an
    unknown/class-3 component → conditional; one class-2 → admitted.
22. open_tournament T-A. NEGATIVE: close_tournament on it unpaired →
    refused (both arms need ≥1 result). Instead void_tournament with
    rationale + decided_by=human:<you> → voided. NEGATIVE:
    record_tournament_result on the voided record → refused (the
    message must say "voided", not "closed").
23. Open T-B for real: open_tournament(parent champion vs a registered
    candidate improver). record_tournament_result on BOTH arms (seed
    the metrics so candidate wins or loses deliberately — you choose).
    close_tournament → closed; confirm recursive_gain computed and
    sealed. NEGATIVE: void_tournament on the now-closed record →
    refused. NEGATIVE: void_tournament on a paired-but-open record →
    refused (close is the honest exit for computable evidence).
24. record_meta_decision: NEGATIVE first — a decision citing the VOIDED
    T-A → refused (a voided record is not a comparison). Then a real
    decision on T-B (verdict=hold is fine if you don't want to promote
    — it still discharges the debt; verdict=promote if you continue to
    step 25).
25. register_improver a child of the champion naming the admitted
    proposal (proposal_id + parent_id). If you promoted at 24:
    promote_policy → minted→active, champion pointer moves, displaced
    champion recorded on the policy row. Then rollback → rolled_back,
    prior champion restored. NEGATIVE (do this before the real promote):
    promote_policy on an improver whose latest decision isn't promote →
    refused.
26. record_canary → open; close_canary → passed. If you can motivate
    one honestly, a second canary → regressed (it should motivate a
    rollback decision — note whether it does).
27. For the conditional proposal from 21: NEGATIVE — attempt the
    promote path WITHOUT decided_by=human:<name> → refused (the human
    gate). Then record_meta_decision on it WITH human authority.

PART F — cross-cutting

28. Agora: read lab://status + lab://topology — confirm your synthetic
    entities show up in the aggregates. NEGATIVE: enumerate agora's
    tool list — confirm it exposes no mutating tools (read-only by
    construction).
29. check_invariants on episteme, zetesis, arete, anamnesis — all four
    should be clean. If your synthetic run legitimately created a
    violation (e.g. an ack you chose not to remediate), acknowledge it
    with an honest disposition and report it — do not leave silent debt.
30. COVERAGE MATRIX: for every row of c-09 §6's transition table, mark
    PASS / FAIL / SKIP with the tool call or evidence id that proves it.
    A transition you could not exercise must be SKIP with a reason —
    never silently absent.

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT read /srv/lab/mcp-state or any state database directly —
  everything goes through MCP tools.
- Trial code is real execution — keep it trivial (print a metrics
  dict). Do not spin real training.
- Report every refusal you hit verbatim, expected or not.
- Sequencing is yours to optimize (e.g. reuse one programme's trials
  for campaign arms where the tools permit), but every NEGATIVE must
  actually be attempted — a refusal you didn't trigger is an unrun test.

DELIVERABLE — produce an exportable artifact. From the repo root:
1. Write your findings into diagnostics/out/state-machine-coverage/ —
   report.md (narrative per part, every refusal verbatim) and
   matrix.md (the §6 coverage matrix — transition, state seen,
   evidence id, PASS/FAIL/SKIP+reason) plus evidence files (JSON
   snapshots of terminal records: the completed programme, the closed
   + voided tournaments, the policy row, the canary, the claims).
2. Run: ./labloop export state-machine-coverage
3. Report the printed tarball path, byte count and sha256 verbatim.
   The tarball is retrieved off the VM by a script — the run is not
   complete until the export succeeds.
