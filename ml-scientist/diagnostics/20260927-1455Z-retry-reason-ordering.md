You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session verifies three rc-6 trial-lifecycle fixes:

- **P14 — durable retry_reason.** `mark_retryable` took a mandatory
  `reason` and discarded it — echoed in the response, never persisted.
  A `retry_reason` column now records it, and every status read surface
  (get_trial_status, list_trials) exposes it.
- **P18 — terminal-before-cancel ordering.** `mark_retryable`
  cancelled the executor BEFORE writing the terminal row — a probe
  mid-cancel saw a transient `running` row that
  `orphaned_running_trials` could gate on. The row now lands terminal
  first.
- **P14b — correct_trial_status parity.** A correction TO `retryable`
  must land its reason on the same column — same durable mechanism,
  not just the corrections trail.

Marker tag: `diag-retry`. Report every refusal verbatim — errors are
data.

Read tool schemas before calling. You will run real trials on
episteme — programme must be status=active for run_trial.

PART A — setup

1. Episteme: create_programme + create_hypothesis (tag `diag-retry`),
   programme active. register_candidate not needed — this is pure
   Loop-0.

PART B — happy path: mark_retryable persists the reason

2. run_trial with code that runs long enough to cancel mid-flight
   (e.g. `import time; time.sleep(300)`) → trial goes to running.
3. While running: mark_retryable with reason "diag-retry: executor
   wedged, retry on fresh node" → response status "retryable".
4. get_trial_status → `status: "retryable"` AND `retry_reason` equals
   the reason string VERBATIM. list_trials → same trial row shows
   retry_reason. A retryable trial with null/missing retry_reason is
   a FAIL (pre-fix: the reason was discarded). Report both surfaces.

PART C — ordering: no transient running row

5. Immediately after step 3's mark_retryable returns, check_invariants
   on episteme → `orphaned_running_trials` must NOT flag the
   just-retried trial (a transient running row post-cancel was the
   bug). Report the check entry verbatim. Then run_trial a fresh
   trial → the write isn't gated by a stale orphan violation.

PART D — happy path: correct_trial_status → retryable persists reason

6. run_trial a short trial → let it reach a terminal state
   (completed/failed). Then correct_trial_status to "retryable" with
   reason "diag-retry: output truncated — rerun on bigger budget".
7. get_trial_status → status "retryable" and `retry_reason` equals
   that reason verbatim. If correct_trial_status refuses retryable or
   drops the reason — report verbatim; the column must be the same
   durable mechanism as mark_retryable.

PART E — NEGATIVE + edge cases

8. mark_retryable on an already-terminal trial → refused (terminal
   precedence — rc-5 semantics unchanged). Report verbatim.
9. get_trial_status on a never-retried trial → `retry_reason` is
   null/absent — the field must not read stale. Report verbatim.
10. If the VM executor refuses the sleep-trial (timeout, sandbox):
    mark BLOCKED and substitute — run a trivial trial, let it finish,
    then exercise Part D's correct_trial_status path as the primary
    retry_reason evidence and say which path ran.
11. check_invariants on episteme at the end — report the payload;
    acknowledge any violation you caused with an honest disposition.

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools.
- Do NOT edit server configuration mid-run.
- Report every refusal verbatim, and what resolved it.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /srv/lab/exchange/diagnostics-out/retry-reason-ordering/
   — report.md (every trial lifecycle call verbatim, both
   retry_reason read surfaces, the orphaned_running_trials check
   entry, the correct_trial_status reason, every refusal) plus
   evidence files (tool result JSON per step).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/retry-reason-ordering
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
