You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session covers two arete surfaces never exercised end-to-end on
the lab:

- **The canary lifecycle** — `record_canary` / `close_canary` on a
  minted policy version.
- **`correct_tournament_result`** — the recorded-repair act on a
  tournament result row. Correction paths are where the rc-10 R35
  laundering bug lived; they deserve deliberate coverage.

Marker tag: `diag-canary`. Report every refusal verbatim — errors are
data.

PART A — canary lifecycle

1. Arete: you need a policy version (`pol-*`). If none exists, run the
   Loop-2 path: propose_meta_change → record_meta_decision (with
   evidence_ref ids — pull_evidence first) → promote_policy → the
   policy_version id. Record it verbatim.
2. record_canary(policy_version_id, scope={…}) → canary_id, status
   'open'. Report verbatim.
3. NEGATIVE: record_canary with a nonexistent policy_version_id →
   refused, naming the missing row. Report verbatim.
4. close_canary(canary_id, status='passed') → closed. Report
   verbatim. Then close_canary again → refused (already closed).
5. Record a second canary on the same policy version and close it
   'regressed' → the record marks the regression; the governed
   response would be a rollback decision — report whether anything
   forces or suggests one.

PART B — correct_tournament_result

6. You need an OPEN tournament with at least one result row
   (`tres-*`). open_tournament + record_tournament_result if none
   exists — record the result id from list surfaces or the write
   response.
7. NEGATIVE — empty correction: correct_tournament_result(result_id,
   reason='', …) → refused (reason required). With reason but no
   metrics and no descendant_spec → refused ("nothing to correct").
   Report both verbatim.
8. POSITIVE: correct_tournament_result(result_id,
   reason='diag-canary: reshaping metrics', metrics={'seed': 0,
   'score': <new>}) → accepted; the row's corrections audit trail
   gains an entry with previous values + reason. get_tournament →
   show the corrected row's audit trail verbatim.
9. NEGATIVE — sealed record: close_tournament the tournament, then
   correct_tournament_result on the same result → refused (a sealed
   record stays sealed). Report verbatim.
10. Check list_decisions / the tournament record — confirm the
    correction is an append, not a rewrite: the audit trail must
    contain the PREVIOUS values.

PART C — honest bookkeeping

11. check_invariants on arete at the end — report the payload;
    acknowledge any violation you caused with an honest disposition.

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools.
- Do NOT edit server configuration mid-run.
- Report every refusal verbatim, and what resolved it.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /srv/lab/exchange/diagnostics-out/canary-and-correction/
   — report.md (policy_version id, both canary ids + close responses,
   every correct_tournament_result response verbatim, the audit-trail
   entry) plus evidence files (tool result JSON per step).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/canary-and-correction
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
