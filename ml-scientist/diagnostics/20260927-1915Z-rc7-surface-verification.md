You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session verifies the rc-7 surface — the prior extraction ran a
PRE-rc-7 build (zetesis reported 12 checks, `read_resource` absent),
so none of rc-7's fixes have ever been exercised on this lab. If the
tool surface still lacks `read_resource` or zetesis runs fewer than
15 checks, say so loudly at the top of the report — the build is
stale and everything below is N/A, not failed.

Marker tag: `diag-rc7`. Report every refusal verbatim — errors are
data.

Read tool schemas before calling.

PART A — read_resource (rc-7 Q6): the missing verb

1. List tools on each of the five servers — `read_resource` must be
   present on ALL FIVE. Report each server's tool count.
2. episteme: read_resource("protocol://status") → JSON content.
   read_resource("protocol://nonexistent") → named error, verbatim.
3. agora: read_resource("lab://topology") → the channel map. Then
   read upstream resources THROUGH agora's routing:
   read_resource("claims://status") → anamnesis digest;
   read_resource("improver://classes") → arete's ADR-0003 class map
   (immutable ↔ first class, modifiable ↔ second, conditional ↔
   third — report the mapping verbatim). A scheme agora can't route
   must return a named error, not a hang.

PART B — unrunnable_campaigns (rc-7 Q3) + abandoned_at (Q5)

4. zetesis check_invariants → report the FULL checks array: expect
   15 checks including `unrunnable_campaigns`,
   `campaigns_awaiting_verdict`, and `incomplete_campaigns` by name.
5. Construct the wedge it detects: open_campaign with a budget that
   cannot carry a spawn (read the open_campaign schema and craft the
   minimal non-carryable budget — e.g. zero trials/seeds capacity).
   Leave it open with zero spawns and zero results →
   check_invariants → `unrunnable_campaigns` flags it by id, and the
   detail names `abandon_campaign` as the exit. A campaign with NO
   budget (caller-driven) must NOT flag — open one bare and confirm
   it stays clean.
6. close_campaign on the wedged campaign → refusal must name the
   structural wedge and `abandon_campaign` as the exit — verbatim.
   An ordinary pending campaign's close refusal does NOT claim a
   wedge.
7. abandon_campaign → get_campaign → `abandoned_at` set AND
   `closed_at` null (rc-5's bug stamped closed_at; the split fields
   keep the record honest). Report both fields verbatim.

PART C — read surfaces (Q1, Q2, Q9, Q11)

8. Loop-0 quick chain on episteme (tag `diag-rc7`): programme →
   hypothesis → design → run a tiny trial; then a second trial you
   mark_retryable mid-run with reason "diag-rc7: queue parity".
   list_trials → the retryable row carries `retry_reason` verbatim
   (the queue surface — get_trial_status already had it; the list
   did not). Report the row.
9. prepare_data with a generator whose `generate_data` prints a
   single-line `{"error": "diag-rc7 SENTINEL"}` JSON to stdout and
   exits 1 → the failure payload must surface that structured error
   (pre-rc-7 the stdout payload was invisible — a bare exit code).
   Report the error verbatim.
10. arete record_meta_decision → report whether the response schema
    carries `claim_error` (mint failures now record the exception;
    null on success). A fabricated-evidence refusal should still be
    a clean refusal — report verbatim either way.
11. Trial artifacts: a trial that writes a file named
    `notes_stderr.py` (a code file, not a log) → captured artifact
    type must NOT be "stderr" — classification keys on role suffixes
    (`_stderr.log`, `_wrapper.py`) + extension, not filename
    substrings. Report the artifact manifest entry.
12. pull_evidence / pull_campaign_evidence tool schemas → the `tool`
    enum must equal the per-source whitelist union; zetesis's own
    loop1 tools (get_campaign, list_campaigns, …) must NOT appear in
    an enum that can't reach them. Report the enums verbatim.

PART D — honest bookkeeping

13. Report verbatim: every resource read payload, the checks arrays,
    campaign open/close/abandon exchanges, trial list rows, generator
    error, artifact manifest, tool enums.
14. check_invariants on each touched server at the end; acknowledge
    violations you caused with honest dispositions — record what the
    checks say rather than clearing your way out.
15. If any step could not be reached — say which and why; a skipped
    step must be louder than a passing one. A stale build (missing
    read_resource, <15 checks) voids the whole battery — say so.

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools.
- Do NOT edit server configuration mid-run.
- Report every refusal verbatim, and what resolved it.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /srv/lab/exchange/diagnostics-out/rc7-surface-verification/
   — report.md (build verdict first — rc-7 or stale — then every
   surface payload verbatim) plus evidence files (tool result JSON
   per step).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/rc7-surface-verification
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
