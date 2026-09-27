You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session exercises the WRITE-boundary gates — the rc-4 diagnostic
found three places where a downstream gate was absent or bypassed at
the moment the durable record was written. Verify each fix end-to-end.
Every entity you create must carry the marker `diag-wb` somewhere in
its goal/statement/rationale so the run is identifiable. Report every
refusal verbatim — errors are data.

The three rules under test:

  R1 (arete) — a `promote` meta_decision on a candidate implementing a
     `conditional` proposal is refused AT THE WRITE unless decided_by
     names `human:<name>`. Nothing may be persisted: no mdec row, no
     minted claim.
  R2 (episteme) — a refused `close_programme` mutates NOTHING. Every
     gate runs before every sweep.
  R3 (episteme) — `conclude_hypothesis` refuses while any trial on the
     SAME hypothesis is still `running`.

PART A — R1: the conditional-proposal human gate at decision-write

1. Register a parent improver, then propose_meta_change touching an
   UNRECOGNIZED component (e.g. class_map {"diag-wb-widget": "m"} —
   unknown components classify conditional). Confirm the proposal
   reads status "conditional" BEFORE proceeding — if it reads
   "admitted" or "rejected", the fixture is wrong; say so and stop.
2. register_improver under that proposal → candidate.
3. Run a tournament involving the candidate (open_tournament →
   record results both arms → close_tournament), then pull_evidence
   to mint an eref in a context involving the candidate.
4. THE TEST: record_meta_decision(candidate, verdict="promote",
   decided_by="agent:diagnostics", evidence_refs=[eref]) — it MUST
   be refused. Report the error verbatim; it must name the human
   requirement.
5. Verify NOTHING was written:
   a. list_decisions(candidate) → total must be 0.
   b. list_claims on anamnesis → no new methodological claim whose
      source_id is an mdec- id minted this session. If a claim
      exists, the gate minted before refusing — that is the bug;
      report the claim_id.
6. Same call with decided_by="human:operator" → must succeed.
   promote_policy on the candidate → must succeed.
7. Negative controls — all must succeed (gate is promote+conditional
   only):
   a. A SECOND conditional-proposal candidate; record_meta_decision
      with verdict="reject", decided_by="agent:diagnostics" → accepted.
   b. A candidate implementing an ADMITTED proposal (class_map
      {"planner": "m"} classifies modifiable → admitted);
      record_meta_decision promote + agent decider → accepted.
   c. An improver with NO proposal_id; record_meta_decision promote
      + agent decider → accepted (cite a tournament-scoped eref).

PART B — supersession as remediation (insert-only correction)

8. On a THIRD conditional-proposal candidate: record a human-signed
   promote decision (accepted), then record a human-signed `reject`
   decision on the same candidate with rationale noting it supersedes
   the promote. list_decisions must show BOTH rows, oldest first.
9. promote_policy on that candidate → must now refuse, and the error
   must say the LATEST decision is not promote. This is the
   non-destructive repair pattern: the bad row stays, truthfully
   superseded.

PART C — R2: refused close mutates nothing

10. Programme P1: hypothesis H1 with a designed trial (H1 goes
    under_test), hypothesis H2 left proposed.
    close_programme(P1, "completed") → refused (under_test). Then GET
    each entity: H1 still under_test, H2 still proposed, the trial
    still designed, P1 still active. Any change = the bug.
11. Programme P2: H3 with a RUNNING trial (run a trial whose code
    sleeps — `import time; time.sleep(90); print({"m": 1})` — so
    get_trial_status reads "running"), H4 under_test with a designed
    trial, H5 proposed.
    close_programme(P2, "abandoned") → refused (running trial). GET
    each entity: H3/H4 under_test, H5 proposed, the designed trial
    still designed, the running trial still running, P2 active.
    Then cancel_trial the runner.
12. Close P2 again ("abandoned") → succeeds. auto_marked must list
    BOTH swept hypotheses (the under_test ones AND the proposed one)
    and trials_auto_marked the designed trial. Any swept entity
    missing from the report is a finding.
13. close_programme(P2) a third time → refused, and the error must
    say the programme is closed/immutable — report it verbatim.
    (Pre-rc-4 this refusal fired only after no-op sweeps, with a
    different message.)

PART D — R3: conclude while evidence is in flight

14. Programme P3: hypothesis H6 with a completed+observed trial AND a
    second trial still running (sleep code again). update_belief for
    the programme.
    conclude_hypothesis(H6) → must refuse, naming the running trial
    verbatim.
15. cancel_trial the runner → conclude_hypothesis → must now succeed.
16. Programme P4: H7 (completed+observed trial, conclude target) and
    H8 (a running trial — DIFFERENT hypothesis). conclude_hypothesis
    on H7 must succeed — the gate is per-hypothesis. cancel the H8
    runner afterwards.

PART E — honest bookkeeping

17. Report verbatim: every refused payload, the list_decisions output
    for every candidate, the auto_marked/trials_auto_marked payloads,
    and every GET after a refused close.
18. If any step could not be reached (tool absent, prerequisite
    refused), say which and why — a skipped step must be louder than
    a passing one.
19. check_invariants on episteme and arete at the end — report the
    payload; any violation you caused must be acknowledged with an
    honest disposition, none left open.

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools.
- Do NOT edit server configuration mid-run.
- Report every refusal verbatim, and what resolved it.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /srv/lab/exchange/diagnostics-out/write-boundary-gates/
   — report.md (every refusal verbatim; the before/after entity states
   for each refused close; list_decisions payloads; the claims check;
   every gate verdict) plus evidence files (tool result JSON per step).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/write-boundary-gates
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
