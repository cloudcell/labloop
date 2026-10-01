You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session verifies three rc-7b episteme fixes that the earlier
`trial-terminal-precedence` run exposed on the VM:

- **R1 — executor-cancel spawn race.** `mark_retryable` landed +14 ms
  after `run_trial` — inside `create_subprocess_exec`, before the
  process handle was bound — and the 120 s subprocess ran to
  completion anyway. The spawn is now shielded so the handle is
  always bound and killed deterministically, and the pgrep sweep
  retries for ~1 s to catch a fork that completes post-cancel.
- **R2 — thin terminal payload.** When the leaked process's late
  result landed, `get_trial_status` returned only
  `{trial_id, status, executor_output}` — `finished_at`,
  `retry_reason`, `started_at`, `duration_seconds`, `artifact_path`
  vanished. The terminal early-return now projects the full row.
- **R3 — integrity blind spot.** `orphaned_running_trials` only saw
  `running` rows; a terminal row with a live executor task was
  invisible. A new `terminal_with_live_executor` check exists —
  live non-done task entries, plus the residue signature (recorded
  executor `duration_seconds` exceeding the row's
  started→finished window).

Marker tag: `diag-race`. Report every refusal verbatim — errors are
data.

Read tool schemas before calling. Loop-0 chain: create_programme →
formulate_hypothesis → design_experiment → capture_bundle → run_trial.
The programme must be status=active for run_trial.

PART A — setup

1. Episteme: create_programme + formulate_hypothesis (tag `diag-race`),
   programme active.

PART B — the spawn-window cancel (the bug's exact shape)

2. Write a SLOW stub /tmp/diag-race/slow.py that exposes the
   executor contract — `def run_training(config):` body
   `import time; time.sleep(90); return {"metrics": {"accuracy":
   0.5}, "variance": {}}`. (run_trial imports the file and calls
   run_training(config); a bare top-level print never runs.)
   Bundle it and design three trials (t1, t2, t3) — the race is
   probabilistic, so run the scenario more than once.
3. For EACH trial: run_trial → returns quickly with
   `status: "running"` (the call does NOT wait for the trial to
   finish — poll get_trial_status) → then IMMEDIATELY mark_retryable
   with reason "diag-race: spawn-window cancel" — no reads in
   between. Record the wall-clock latency between run_trial's return
   and mark_retryable's call for each (the VM hit the race at +14 ms;
   anything under ~1 s still exercises the boundary path).
   Response must confirm status "retryable".
4. IMMEDIATELY after each mark_retryable: get_trial_status →
   `status: "retryable"`, `retry_reason` verbatim, AND the full
   terminal field set present — `finished_at`, `started_at`,
   `duration_seconds`, `artifact_path`, `retry_reason` as KEYS in the
   payload (null values are honest; ABSENT keys are the R2 bug —
   FAIL).
5. Process liveness — the MCP-reachable probe (an agent as `lab`
   has no in-guest shell): ~10 s after each mark_retryable, call
   check_invariants on episteme and report `orphaned_running_trials`
   and `terminal_with_live_executor` verbatim — they consult the
   executor's live task table, which is the sanctioned liveness
   surface (a cancelled trial whose subprocess lives is exactly
   what they catch). A trial that stays clean here AND clean at
   step 6's duration check is the pass shape. If you DO have a
   container shell, `pgrep -f <trial_id>` is welcome corroboration —
   but never fabricate it.
6. Wait past the stub's natural end (~95 s after run_trial).
   get_trial_status each trial again → still "retryable", same full
   field set, same retry_reason. If `executor_output` is present, its
   `duration_seconds` must NOT be ~90 s — a full-runtime duration on
   a row marked <1 s in is the leaked-process signature (FAIL).
7. check_invariants on episteme → report the FULL `checks` array.
   Expect 12 checks including `terminal_with_live_executor` by name,
   reporting ok for your trials. If step 5/6 showed a leak, this
   check MUST flag the trial — a leaked process plus a clean
   `terminal_with_live_executor` is a double FAIL: the check exists
   precisely to see that residue.

PART C — mid-run cancel (the non-racy path must still work)

8. Fresh trial on the same stub. run_trial → wait ~3 s (spawn long
   done, `proc.communicate()` in flight) → cancel_trial → status
   `failed` with `cancelled: true`. `failed` here records the
   cancellation act, not a scientific failure — the docstring says so;
   report the payload verbatim.
9. Liveness again via check_invariants (same probe as step 5 —
   `terminal_with_live_executor` must not flag the cancelled id);
   if you have a container shell, `pgrep -f <trial_id>` → nothing
   alive. get_trial_status → `failed`, terminal fields intact.

PART D — honest bookkeeping

10. Report verbatim: every get_trial_status poll (timestamped), the
    mark_retryable / cancel_trial payloads and measured latencies,
    the step-5/9 check_invariants liveness probes (and pgrep output
    if a container shell was available), the full check_invariants
    array.
11. check_invariants at the end — acknowledge any violation you
    caused with an honest disposition. A retried trial is a
    legitimate infrastructure outcome; do not ack-check your way
    around a real finding.
12. If any step could not be reached — say which and why; a skipped
    step must be louder than a passing one.

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools.
- Do NOT edit server configuration mid-run.
- Report every refusal verbatim, and what resolved it.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /srv/lab/exchange/diagnostics-out/cancel-spawn-window-kill/
   — report.md (the per-trial cancel latencies, every status poll
   verbatim, the check_invariants liveness evidence, the full check
   array)
   plus evidence files (tool result JSON per step).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/cancel-spawn-window-kill
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
