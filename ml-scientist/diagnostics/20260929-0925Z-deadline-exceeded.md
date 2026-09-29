You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session verifies the rc-11 **per-tool deadline**
(`[server] tool_deadline_seconds`, default 120 s): a call that
outlives the deadline returns `deadline_exceeded` to the client —
but the deadline is a RESPONSE bound, not an abort. The handler is
shielded: the trial keeps running server-side and completes on its
own clock. Proving only the client-facing timeout would miss the
point — you must also prove the work finished.

Marker tag: `diag-deadline`. Report every refusal verbatim — errors
are data.

Requires a build carrying the rc-11 deadline wrapper — fingerprint
first.

PART A — fingerprint + a trial designed to outlive the deadline

1. Read the episteme tool list — run_trial and wait_trial present.
   Record the server start banner if visible (the deadline value is
   logged) — otherwise proceed on the 120 s default and time your
   calls.
2. create_programme → design_experiment → run_trial a script that
   sleeps ~150 s then prints a metric — long enough to cross 120 s,
   short enough to stay under the executor's own 300 s bound. Note
   the wall-clock time you issued the call.

PART B — the deadline fires, and the work still completes

3. The run_trial call returns `{"error": "deadline_exceeded — …"}`
   (or the transport-level timeout your client surfaces) around the
   120 s mark — record the elapsed time AND the verbatim error
   text. If the call instead hangs past ~150 s with no response,
   the deadline did not fire — FAIL, report observed behaviour.
4. Now prove the handler was shielded, not cancelled: poll
   wait_trial / get_trial_status until the trial reaches a terminal
   state. Expect `completed` with your metric observed — the trial
   finished server-side AFTER the client-facing deadline fired.
   Record the trial's status, finished_at, and executor record
   verbatim. A trial left `running` forever or marked failed-by-
   deadline contradicts the shield semantics — report it.
5. While the trial was still running past the deadline, other calls
   must still answer: issue list_programmes against episteme and
   claims://status on anamnesis DURING the wait window — report
   that the server stayed responsive (the wedged call did not
   block the loop).

PART C — boundary honesty

6. If the lab's deadline differs from 120 s (a shorter configured
   value fires early — your 150 s trial may never reach it if the
   executor kills first, or a longer one means your trial completes
   before the deadline): report what you OBSERVED — the configured
   deadline, the elapsed time at response, the final trial state —
   and classify the outcome honestly (PASS / BLOCKED / UNREACHABLE),
   not by assumption.

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools.
- Do NOT edit server configuration mid-run.
- Report every refusal verbatim, and what resolved it.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /srv/lab/exchange/diagnostics-out/deadline-exceeded/
   — report.md (call start/elapsed times, the verbatim deadline
   error, the trial's final record, the concurrent-call probes)
   plus evidence files (tool result JSON per step).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/deadline-exceeded
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
