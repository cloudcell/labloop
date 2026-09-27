You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session verifies three rc-6 campaign fixes:

- **P12 — metric VALUE gate.** rc-5's gate checked only that the
  primary-metric KEY existed; rc-6 wedged a real campaign
  (camp-c0abb53e) by writing {"rec_gain": null} — the row persisted
  and `close_campaign` died on a raw TypeError, leaving the campaign
  permanently uncloseable while invariants stayed green. The gate
  must now reject non-finite/non-numeric VALUES at write, name the
  key AND the bad value, and `close_campaign` must defensively refuse
  already-wedged rows with a named error pointing at
  `abandon_campaign`.
- **P12 — wedge detection.** A new `unscoreable_campaigns` integrity
  check reports open campaigns that can never score at close.
- **P9 — abandon_campaign.** The missing exit: terminal, attributed,
  recorded — for campaigns that can never legitimately close.

Marker tag: `diag-gate`. Report every refusal verbatim — errors are
data.

Read tool schemas before calling. The campaign surface is zetesis; the
contract and candidates live upstream on episteme.

PART A — setup (same shape as the rc-5 metric diagnostic)

1. Episteme: create_evaluation_contract (primary metric "rec_gain") +
   register two candidates; promote one to incumbent via
   record_promotion_decision (evidence_refs ≥1 — P3's gate is live).
2. Zetesis: refresh_roster → open_campaign naming the contract +
   challenger.

PART B — NEGATIVE: bad metric VALUES refused at write

3. For a programme attributed to the champion arm, attempt
   record_campaign_result with metrics {"rec_gain": null} → refused.
   The error must NAME the key and the offending value — report
   verbatim.
4. Repeat with: {"rec_gain": "high"}, {"rec_gain": true},
   {"rec_gain": "NaN"} / a NaN float if the transport admits it.
   Every one must be refused before insert. After each refusal,
   get_campaign → the results list must be unchanged. An accepted
   bogus row is a FAIL — it would be insert-only debt.
5. Happy path within the same campaign: {"rec_gain": 0.42} →
   accepted. Secondary metrics alongside ({"rec_gain": 0.42,
   "latency": 3.1}) → accepted, both preserved.

PART C — wedge detection + defensive close

6. If you can still reach an open campaign carrying a bad row (a
   pre-fix wedge like camp-c0abb53e may still exist on this lab —
   list_campaigns and check): run check_invariants on zetesis →
   `unscoreable_campaigns` must report the wedged campaign by id in
   violations, with a detail naming `abandon_campaign` as the exit.
   If no wedged campaign exists, report the check's clean payload
   verbatim and say so — do not fabricate a wedge.
7. On a wedged campaign (only if one exists): attempt close_campaign →
   must refuse with a NAMED error (not a raw TypeError/traceback)
   pointing at abandon_campaign. Report verbatim.

PART D — happy path: abandon_campaign terminal + attributed

8. On the wedged-or-a-fresh open campaign: abandon_campaign with
   rationale "diag-gate: metric gate verification" and
   decided_by "agent:diag". Response: status "abandoned" +
   abandoned_by. Report verbatim.
9. Immutability — all of these on the abandoned campaign must refuse
   identically to closed, report each verbatim:
   - record_campaign_result (any metrics)
   - spawn_campaign_programme
   - pull_campaign_evidence (if present)
   - close_campaign
   - a SECOND abandon_campaign (terminal twice = refused)
10. list_campaigns → the abandoned campaign appears under
    status filter "abandoned" and does NOT appear under "open".
    get_campaign → abandon_rationale and abandoned_by persisted on
    the record.
11. Happy-path counterpoint: open a fresh campaign, write valid
    results for BOTH arms, close_campaign → promotion_score computed,
    status closed. A second close → refused. Record the score
    verbatim.

PART E — honest bookkeeping

12. Report verbatim: every write attempt + response, every
    get_campaign payload, the check_invariants checks array (the
    unscoreable_campaigns entry in full), every abandon/close
    response.
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
   /srv/lab/exchange/diagnostics-out/campaign-value-gate-abandon/
   — report.md (every write attempt verbatim, refusals, the wedge
   check entry, the abandon lifecycle verbatim, the clean close)
   plus evidence files (tool result JSON per step).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/campaign-value-gate-abandon
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
