You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session exercises the ORCHESTRATED campaign path — the rc-4
diagnostic ran campaigns client-driven (the agent created its own
programmes upstream and reported results), which left the entire
spawn lifecycle untouched: no spawn_campaign_programme call, no
campaign_spawns rows, no spawn-scoping guard, no budget-cap
enforcement, no close-time budget audit. Run the path the way it was
designed: arete arms the campaign, zetesis spawns the programmes,
the results report back into the spawn rows. Marker tag: `diag-camp`.
Report every refusal verbatim — errors are data.

The intended chain (discover exact argument shapes from the tool
schemas — read them before calling):

  episteme: create_evaluation_contract (an upstream contract —
            carries the primary metric) + register_candidate for
            both arms (champion + challenger cand- ids)
  arete:    improver parent + candidate → open_tournament whose
            budget carries programmes_per_arm + trials_per_programme
            → open_arm_campaign(tournament, arm, upstream_contract_id,
            challenger_id) — one campaign per arm; budget is carried
            verbatim, the caller cannot redefine it
  zetesis:  spawn_campaign_programme → creates a REAL upstream
            programme and writes a campaign_spawns row
  episteme: run trials inside the spawned programmes
  zetesis:  record_campaign_result → spawn row flips to completed
            pull_campaign_evidence (while open) → erefs
            close_campaign → promotion_score
            record_promotion_verdict citing the erefs → claim

PART A — setup

1. Upstream contract + two registered candidates (the campaign's
   champion/challenger). Record the contract's primary metric name —
   your trial code must print it.
2. Arete: improver parent + candidate, a meta-contract, and an OPEN
   tournament with budget carrying BOTH programmes_per_arm and
   trials_per_programme (e.g. {"programmes_per_arm": 1,
   "trials_per_programme": 2}). If open_arm_campaign refuses for a
   missing carried key, fix the tournament budget, not the call.

PART B — the spawn lifecycle

3. open_arm_campaign for each arm → two campaign ids. A second
   open_arm_campaign on the same arm must return the EXISTING link
   (status already_linked), not a new campaign — verify.
4. spawn_campaign_programme on each campaign (arm: champion on the
   parent arm's campaign, challenger on the other — read the schema).
   Each returns a spawn_id + an upstream programme_id.
5. get_campaign → spawns must be NON-EMPTY and list each spawn with
   its status and programme_id. (rc-4 reported `spawns: []` on
   campaigns that never spawned — that was correct output for
   client-driven mode, not a read bug. Here rows must exist.)
6. Cap enforcement: with programmes_per_arm=1, a second spawn on the
   same arm → refused. Report verbatim.
7. Budget override: spawn with a budget override LARGER than the
   carried trials_per_programme → refused ("can only shrink"). An
   override within the carried values → accepted. Report both.
8. Spawn on a campaign after close → refused (run at the end; see
   Part D ordering).

PART C — attribution and scoping guards

9. In each spawned programme upstream: hypothesis → design → bundle →
   a trial that prints {"<primary_metric>": <value>} → observation.
   The spawned programme must carry the arm's candidate_version_id —
   verify via list_programmes or the programme resource.
10. NEGATIVE — spawn scoping: create a programme DIRECTLY on
    episteme (not spawned), attributed to the arm's candidate.
    record_campaign_result naming it → refused, message must say the
    programme was not spawned under the campaign. (This guard only
    exists once spawn rows exist — client-driven campaigns with zero
    spawns keep caller-driven behaviour by design.)
11. NEGATIVE — wrong arm: record_campaign_result citing the
    champion-spawned programme as arm "challenger" → refused
    ("spawned for arm ... not 'challenger'").
12. NEGATIVE — wrong attribution: record_campaign_result for a
    programme whose candidate_version_id is not the arm's candidate →
    refused upstream attribution. Report verbatim.
13. record_campaign_result for each spawned programme under its
    correct arm → accepted; get_campaign → the spawn status is now
    "completed" and results are listed.

PART D — close ordering and the frozen trail

14. pull_campaign_evidence for each campaign WHILE OPEN → erefs.
15. close_campaign on a campaign missing one arm's score result →
    refused ("both arms must run"). Then close each campaign after
    both results recorded → promotion_score reported.
16. After close: pull_campaign_evidence → ACCEPTED. Close freezes
    results and score, not consultation — pulls stay legal while the
    verdict is pending (the erefs are what the verdict cites). A
    refusal here contradicts the documented contract; a refusal only
    becomes correct after step 17's verdict lands. Report verbatim.
17. record_promotion_verdict WITHOUT a campaign-scoped eref →
    refused. With the erefs from step 14 → decision + minted claim.
    Report the claim_id.
18. spawn_campaign_programme on the now-closed campaign → refused.
18b. Post-verdict pull_campaign_evidence → refused (decision_id set —
    consultation ended with the verdict). Report verbatim.

PART E — honest bookkeeping

19. Report verbatim: every campaign JSON (get_campaign) after each
    stage — spawns, results, evidence_refs, promotion_score; every
    refusal; the verdict's claim.
20. If any step could not be reached — say which and why; a skipped
    step must be louder than a passing one. If the orchestration
    channel is not wired on this VM ([adaptors.loop1_orchestration]),
    that IS the reportable outcome — record the refusal verbatim and
    mark Parts B–D blocked, not skipped-by-choice.
21. check_invariants on every server at the end — report payloads;
    acknowledge any violation you caused with an honest disposition.

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools.
- Do NOT edit server configuration mid-run.
- Report every refusal verbatim, and what resolved it.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /srv/lab/exchange/diagnostics-out/campaign-orchestration/
   — report.md (the full chain verbatim: link records, spawn rows,
   per-stage get_campaign payloads, every guard refusal, the verdict
   and its claim) plus evidence files (tool result JSON per step).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/campaign-orchestration
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
