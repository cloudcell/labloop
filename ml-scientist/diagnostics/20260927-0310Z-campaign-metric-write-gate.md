You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session verifies the rc-5 CAMPAIGN write-gate fix — rc-5 found that
`record_campaign_result` accepted a result whose metrics object did not
contain the campaign contract's primary metric, persisting the bogus row
into the insert-only record (verified in search.db). The write must now
be refused up front — the record is insert-only, so a bad row that lands
can never be removed. Prove the gate holds and that legitimate writes
still pass. Marker tag: `diag-metric`. Report every refusal verbatim —
errors are data.

Read tool schemas before calling. The campaign surface is zetesis; the
contract and candidates live upstream on episteme.

PART A — setup

1. Episteme: create_evaluation_contract with primary metric
   "rec_gain" (any distinct metric name — record it) + register two
   candidates (champion + challenger).
2. Promote the champion upstream: record_promotion_decision
   (verdict "promote", evidence_refs ≥1, non-empty rationale and
   decided_by). Establishing an incumbent is required for a campaign.
3. Zetesis: refresh_roster → both candidates tracked, the champion
   reconciled as champion. Then open_campaign naming the contract and
   the challenger → campaign id, armed/open.

PART B — NEGATIVE: wrong metric refused at write

4. Run a real upstream trial under a spawned or caller-driven programme
   attributed to the champion, then call record_campaign_result with
   metrics {"wrong_metric": 1} — i.e. NO "rec_gain" key. Expect a
   refusal that NAMES the required metric and the supplied keys. A
   silent accept is a FAIL — the row is insert-only and would persist
   forever.
5. get_campaign / list results → verify NO campaign_results row exists
   for that attempt. The record must be unchanged — check the result
   list is still empty (or only holds the rows you legitimately wrote).
   The gate refused BEFORE insert: nothing may be persisted.

PART C — happy path: correct metrics accepted

6. record_campaign_result for the champion arm with metrics
   {"rec_gain": 0.6, "latency": 3.1} — primary metric present,
   secondary metrics alongside → accepted. Verify the result row is
   listed by get_campaign and contains BOTH metrics (secondary metrics
   must coexist, not be stripped).
7. Same for the challenger arm: a real attributed trial, then
   record_campaign_result {"rec_gain": 0.7} → accepted.
8. close_campaign → promotion_score computed and reported. Before the
   fix, a wrong-metric row reached this point and failed with "no
   rec_gain results" — a confusing close-time error. Now the failure
   happens at write, where the data is. Verify close succeeds cleanly
   on the legitimate rows.

PART D — NEGATIVE: edge cases

9. Reopen a second campaign (fresh contract or same pair) and test the
   boundary: metrics {} (empty) → refused; metrics {"REC_GAIN": 1}
   (case mismatch) → refused unless the gate is documented
   case-insensitive — report which. metrics {"rec_gain": null} → report
   verbatim whether null counts as present. Each outcome is data;
   record exactly what the tool returned.
10. Attempt record_campaign_result on the CLOSED campaign → refused on
    campaign state, not metric shape. Order of checks is data — report
    verbatim.

PART E — honest bookkeeping

11. Report verbatim: every record_campaign_result call and response
    (accepted AND refused), every get_campaign payload showing the
    results list, the close payload with promotion_score.
12. If any step could not be reached — say which and why; a skipped
    step must be louder than a passing one. If the orchestration
    channel is not wired on this VM, run the caller-driven campaign
    path (the metric gate does not depend on spawn rows) and say so.
13. check_invariants on episteme + zetesis at the end — report
    payloads; acknowledge any violation you caused with an honest
    disposition.

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools.
- Do NOT edit server configuration mid-run.
- Report every refusal verbatim, and what resolved it.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /srv/lab/exchange/diagnostics-out/campaign-metric-write-gate/
   — report.md (the contract's primary metric, every write attempt
   verbatim, the results list before/after, the close payload) plus
   evidence files (tool result JSON per step).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/campaign-metric-write-gate
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
