You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session verifies two rc-5 fixes on episteme: (a) TRIAL TERMINAL
PRECEDENCE — rc-5 found that `get_trial_status` preferred the
executor's live "running" report over the store's terminal `retryable`,
so a marked-retryable trial read as still running with live telemetry;
and `mark_retryable` flagged the row but left the subprocess running to
completion. The persisted terminal state must now win, and the executor
task must be cancelled. (b) PREPARE_DATA STDERR — rc-5 found a failing
data generator surfaced only "Process exited with code 1" with its
stderr discarded; the stderr tail must now reach the caller. Marker
tag: `diag-term`. Report every refusal verbatim — errors are data.

Read tool schemas before calling. Loop-0 chain: hypothesis → design →
capture_bundle → run_trial → record_observation.

PART A — happy path: terminal precedence

1. Write a QUICK stub /tmp/diag-term/quick.py that prints
   {"accuracy": 0.9} and exits. Loop-0 → run_trial → it completes.
   get_trial_status → status "completed" with real telemetry and
   executor_output. Call it again → same record (stable, not
   re-finalized).

PART B — the masking bug: mark_retryable on a live trial

2. Write a SLOW stub /tmp/diag-term/slow.py: import time;
   time.sleep(120); print({"accuracy": 0.1}). Loop-0 → run_trial.
3. Confirm it is running: get_trial_status → "running".
4. While running: mark_retryable(trial_id, reason="diag-term:
   infrastructure test"). Response must confirm status "retryable"
   and record your reason.
5. IMMEDIATELY get_trial_status → MUST return "retryable" — not
   "running". Before the fix this read the executor's stale async
   state and masked the retryable flag with live telemetry. If the
   status reads running with telemetry fields, FAIL.
6. Poll get_trial_status every ~5 s for ~30 s (and once more later,
   after the 120 s sleep would have finished). The status must STAY
   "retryable" — the cancelled subprocess's late completion must not
   resurrect it to "completed" or "failed". The persisted terminal
   record is authoritative.
7. Inspect the record: failure_reason carries your reason; the
   cancelled executor's output may appear under executor_output but
   a synthetic {"error": "No running task"} must not overwrite a real
   payload. Report the full record verbatim.
8. NEGATIVE: mark_retryable on the COMPLETED trial from step 1 →
   refused (a terminal trial cannot be re-marked). And
   mark_retryable on a trial that already is retryable → refused.
   Report both verbatim.

PART C — happy path + NEGATIVE: prepare_data stderr

9. Write a FAILING generator /tmp/diag-term/gen_fail.py:
   import sys; sys.stderr.write("diag-term SENTINEL: index column missing\n");
   sys.exit(1). prepare_data(split="train", regime="generated",
   generator_code_ref=<path>, generator_seed=7) → must FAIL, and the
   error payload must contain the stderr tail — the string
   "diag-term SENTINEL" must appear verbatim. If the error is still a
   bare "Process exited with code 1", the fix regressed — FAIL.
10. Write a WORKING generator /tmp/diag-term/gen_ok.py that writes a
    valid dataset (whatever shape prepare_data expects — read the
    schema/docstring; typically rows on stdout). prepare_data →
    accepted, returns a data_ref_id. Report it.

PART D — honest bookkeeping

11. Report verbatim: every get_trial_status poll (timestamp each),
    the mark_retryable exchange and refusals, both prepare_data calls
    and full error payloads, the completed trial's record.
12. check_invariants on episteme at the end — report the payload;
    acknowledge any violation you caused with an honest disposition.
    A retried trial is a legitimate infrastructure outcome, not a
    violation — do not ack-check your way around it; record what the
    checks say.
13. If any step could not be reached — say which and why; a skipped
    step must be louder than a passing one.

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools.
- Do NOT edit server configuration mid-run.
- Report every refusal verbatim, and what resolved it.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /srv/lab/exchange/diagnostics-out/trial-terminal-precedence/
   — report.md (the status poll series verbatim with timestamps, the
   mark_retryable exchange + refusals, both prepare_data payloads,
   the final check payload) plus evidence files (tool result JSON
   per step).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/trial-terminal-precedence
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
