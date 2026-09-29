You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session sweeps the **uncalled read paths and never-written
registries** — the surfaces the coverage audit found no prompt has
ever named. Most are cheap reads; a few are first writes to
registries that have always been empty on the lab.

Marker tag: `diag-readpaths`. Report every refusal verbatim — errors
are data. Where a tool needs an id, mint the object first (the point
is the read path, not the fixture).

PART A — zetesis: policy registry + investigation reads

1. list_search_policies → expect `[]` on a fresh lab — the registry
   has never been written. Report verbatim.
2. register_search_policy(name='diag-readpaths-policy',
   policy={…}) → first write to the registry. Then
   register_search_policy with the SAME name → a second VERSION row
   (versions are append-only under a name; the previous retires).
   list_search_policies → both visible, exactly one active. Report
   verbatim.
3. If investigations exist: get_investigation + list_investigations
   on one. If none: open_investigation → record_finding(content,
   confidence ≤ 0.3) → get_investigation shows the finding →
   conclude_investigation. Report each response.

PART B — episteme: read paths

4. list_hypotheses + list_promotion_decisions → report counts.
5. get_next_experiment on an open programme → the suggestion
   payload, verbatim.
6. update_metric_direction(programme_id, 'maximize') → report;
   update back if the programme was mid-flight.
7. wait_trial on a completed trial with a short timeout → returns
   the terminal state immediately. Report verbatim.
8. If archives exist: get_archive, get_archived_programme,
   verify_archive → each payload verbatim. If none:
   archive_pending_programmes(dry_run=true) → report what WOULD be
   archived; then dry_run=false if the preview is non-empty.
9. capture_pending_artifacts(dry_run=true) → report the pending
   list; dry_run=false if non-empty.
10. get_candidate, get_candidate_lineage, get_candidate_scorecard,
    get_incumbent on a registered candidate → verbatim.
11. get_evaluation_contract on a contract id → verbatim.
12. assess_programme on a programme → verbatim.
13. verify_data on a data_ref id minted by prepare_data (prepare one
    with a trivial inline dataset if none exists) → the verification
    payload verbatim.
14. capture_bundle_from_code_hash: needs a 'sha256:…' code_hash from
    a prior capture_bundle on the same trial — run a trivial trial,
    capture_bundle, then capture_bundle_from_code_hash with the same
    hash → a second bundle sharing the code blob. Report both
    bundle ids.
15. abandon_hypothesis on a hypothesis you formulate for this
    purpose → verbatim; list_hypotheses shows the terminal state.

PART C — arete: registry and lineage reads

16. get_improver + get_improver_lineage + list_improvers → verbatim
    (register_improver first if the registry is empty).
17. get_proposal + list_proposals → verbatim.
18. get_tournament + list_tournaments → verbatim.
19. create_meta_contract(metrics={'primary_metric': 'diag'},
    promotion_policy={"sesoi_d": 4.0, "target_power": 0.8,
    "min_evidence_rung": "not_worth"}) → the meta-contract id.
    promotion_policy is required and must be powered (README
    §Contract recipe) — a keyless policy is refused. Report
    verbatim.

PART D — anamnesis

20. get_claims (batch fetch — pass claim ids you mint or list) and
    list_claims → verbatim; note any difference between the two
    surfaces.

PART E — honest bookkeeping

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
   /srv/lab/exchange/diagnostics-out/read-paths-and-registries/
   — report.md (every response verbatim or summarized with the exact
   field set, every first-write to an empty registry flagged) plus
   evidence files (tool result JSON per step).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/read-paths-and-registries
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
