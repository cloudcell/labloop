You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session covers a surface no prior battery prompt has exercised:
the **arete-side arm-campaign verbs** — `record_arm_result`,
`pull_arm_evidence`, `close_arm_campaign`, `record_arm_verdict`. The
`campaign-orchestration` prompt drives the same lifecycle through the
zetesis-side names; these four arete wrappers have never been called
end-to-end on the lab.

Marker tag: `diag-arm-verbs`. Report every refusal verbatim — errors
are data.

Read tool schemas before calling. The arm verbs take a TOURNAMENT id
plus an arm ('parent' | 'candidate') — the campaign is the arm's
linked upstream campaign opened by `open_arm_campaign`.

PART A — setup: tournament + arm campaign

1. Arete: read improver://status for live context. Then
   propose_meta_change → proposal id → record_meta_decision citing at
   least one evidence_ref (pull_evidence first if none exist) →
   promote_policy if the decision warrants → open_tournament on the
   resulting policy/candidate context. A fresh open needs a powered
   meta-contract (README §Contract recipe: {"sesoi_d": 4.0,
   "target_power": 0.8, "min_evidence_rung": "not_worth"}) and
   seeds=[1]. Record every id verbatim.
2. Register or identify a challenger candidate for the tournament's
   evaluation contract (register_candidate on episteme;
   register_challenger on zetesis if the campaign needs a roster row).
3. open_arm_campaign(tournament_id, arm='candidate',
   upstream_contract_id=<the tournament's contract>, challenger_id)
   → the arm's linked campaign id. The upstream contract is pulled
   and power-gated — the carried budget's programmes_per_arm is the
   declared n; pre-1641Z contracts open no new work. Report
   verbatim.

PART B — the four verbs, in order

4. pull_arm_evidence(tournament_id, arm='candidate',
   source='loop0', tool='list_programmes') → erefs. Report the
   evidence_ref id and the payload's ref_ids.
5. spawn_arm_programme → spawn a descendant programme for the arm
   (needs the arm campaign from step 3). Then run the spawned
   programme to a real result on episteme (design_experiment →
   run_trial → record_observation as needed — read the tool
   descriptions; a one-line script that prints a metric is enough).
6. record_arm_result(tournament_id, arm='candidate',
   programme_id=<spawned>, metrics={primary_metric: <value>}) →
   accepted; the metrics must carry the contract's primary_metric
   name — read the contract first.
7. NEGATIVE — wrong arm: record_arm_result for the same programme
   under arm='parent' → refused (attribution). Report verbatim.
8. close_arm_campaign(tournament_id, arm='candidate') → the linked
   campaign closes only once both campaign arms have a recorded
   result. If the champion arm has no result, record one through
   record_arm_result with campaign_arm='champion' first, then close.
   Report the promotion_score verbatim.
9. record_arm_verdict(tournament_id, arm='candidate',
   verdict='promote'|'retain', decided_by='diag-arm-verbs',
   evidence_ref_ids=[<erefs from step 4>], rationale=…) → decision
   recorded. Report verbatim.

PART C — honest bookkeeping

10. get_tournament → the arm campaign's final state (score, verdict,
    decision_id). Report verbatim.
11. NEGATIVE — post-verdict pull: pull_arm_evidence on the verdicted
    arm → refused (consultation ended with the verdict). Report
    verbatim.
12. check_invariants on arete + zetesis + episteme at the end —
    report payloads; acknowledge any violation you caused with an
    honest disposition.

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools.
- Do NOT edit server configuration mid-run.
- Report every refusal verbatim, and what resolved it.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /srv/lab/exchange/diagnostics-out/arm-campaign-verbs/
   — report.md (tournament/campaign/programme ids, each verb's
   response verbatim, the negative-path refusals, final campaign
   state) plus evidence files (tool result JSON per step).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/arm-campaign-verbs
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
