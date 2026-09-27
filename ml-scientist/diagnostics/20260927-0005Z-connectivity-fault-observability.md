You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session exercises fault observability: when an upstream channel
fails, every surface must say WHICH operation was in flight or failing
— not an opaque "TaskGroup" error. A dead peer and a busy peer are
different conditions and must be reported differently. Marker tag:
`diag-conn`. Report every payload verbatim — errors are data.

What correct looks like:

  - A channel that cannot connect reports its cause as the LEAF error
    (e.g. "ConnectError: All connection attempts failed"), never
    "unhandled errors in a TaskGroup".
  - A channel whose session died mid-call names the operation that
    was running ("... during <tool>").
  - Channel state carries last_operation, in_flight_operation,
    last_error, last_failed_operation, last_failed_at, probe,
    last_probe_at, last_probe_ms.
  - The supervisor probes each live channel once per tick: an
    answered ping → probe "ok"; a timed-out ping → probe "busy"
    (alive but unresponsive — NOT a violation, channel stays up);
    a transport error → channel goes down ("ping failed: <leaf>").
  - `blocked:` blocker detail composes as "<cause> (failed op: <op>)".
  - A down channel's sticky last_failed_operation survives reconnect
    while last_error tracks the current state.

PART A — baseline

1. Read each server's X://status digest (protocol://status on
   episteme, search://status on zetesis, improver://status on arete,
   claims://status on anamnesis) — or the equivalent status surface
   available to you — and record upstream_summary verbatim: channels
   up/down/busy counts, and each channel's probe fields.
2. check_invariants on agora — report the upstream_connectivity and
   status_reachability payloads verbatim.
3. If lab://status / lab://topology are reachable through your MCP
   client (they are served as RESOURCES, not tools — use the
   resources/read interface if your harness exposes it), record both
   payloads. If your client cannot read resources at all, say so
   explicitly — that is the finding, and use check_invariants +
   per-server status instead.

PART B — the dead-peer case

4. Establish which servers depend on anamnesis (claims channels):
   episteme mints claims at conclude; zetesis resolves claims;
   arete mints methodological claims at decisions.
5. Induce a fault: stop or suspend the anamnesis server process
   inside the lab-cnt-mcp container (docker exec / kill / systemctl —
   whatever mechanism the VM exposes; if you have no way to touch it,
   mark Parts B–D SKIP and say why — an agent with no fault-injection
   capability cannot run this battery).
6. Within ~60 seconds, poll each dependent server's status until the
   claims channel shows down. Record: how long detection took, the
   channel's last_error (must be a leaf cause like
   "ping failed: ConnectError: ..." or a session-death message — NOT
   a TaskGroup repr), last_failed_operation, last_failed_at.
7. On agora (or the equivalent aggregate surface): the anamnesis card
   must read unreachable WITH the composed cause —
   "unreachable — ConnectError: ... (failed op: connect)" — and each
   dependent server must show "blocked: channel claims" with the op
   in the detail. Report verbatim.
8. check_invariants on each dependent — the upstream_connectivity
   violation must carry the channel's last_error /
   last_failed_operation / last_failed_at fields. Report verbatim.

PART C — recovery + sticky attribution

9. Restart anamnesis. Within a few supervisor ticks the channels must
   return up. Verify: last_error now reflects current state (cleared
   or a fresh cause), but last_failed_operation / last_failed_at still
   name the FAULT that killed the channel — the attribution is
   sticky, the error is transient. Report both fields verbatim.

PART D — the mid-flight case (best-effort)

10. If you can arrange a call to be in flight when its server dies —
    e.g. start a slow operation on an upstream (a run_trial whose
    code sleeps) and kill the server DURING it — the dependent's
    channel should record "session ended during <op>" and
    last_failed_operation naming the call. If you cannot arrange a
    reliable mid-flight kill, say so and mark this PARTIAL — do not
    fake it.

PART E — busy ≠ down (best-effort)

11. If you can make a server unresponsive WITHOUT killing it (pause
    the process with SIGSTOP, or saturate it), a supervisor ping
    that times out should leave the channel up with probe="busy" —
    NOT down, NOT a violation. Resume the process and confirm it
    returns to probe="ok". If SIGSTOP does not stall the MCP handler
    (the session may run in-process), report what you observed
    verbatim instead of a verdict.

PART F — honest bookkeeping

12. Report verbatim: every status digest, every channel state object,
    every check_invariants payload, the detection latencies you
    measured in Part B step 6, and every field that was absent or
    opaque.
13. An "unhandled errors in a TaskGroup" string ANYWHERE in any
    surfaced payload is an automatic FAIL — that is the exact defect
    this diagnostic exists to catch.
14. If any step was skipped, say which and why — a skipped step must
    be louder than a passing one.

While you work:
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools and the server's own
  health surfaces.
- Do NOT edit server configuration mid-run.
- Restart anything you killed before finishing — leave the lab up.
- Report every refusal verbatim, and what resolved it.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /home/lab/workspace/diagnostics-out/connectivity-fault-observability/
   — report.md (baseline probe fields; per-fault channel states
   verbatim; detection latencies; the agora card strings; recovery
   fields; any TaskGroup repr sighting) plus evidence files (the
   status/health JSON snapshots before, during, and after each fault).
2. Stage it for host retrieval:
      labloop-export /home/lab/workspace/diagnostics-out/connectivity-fault-observability
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
