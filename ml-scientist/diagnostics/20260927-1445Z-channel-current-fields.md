You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session verifies three rc-6 connectivity-surface fixes:

- **P4 — in-flight fields.** `in_flight_operation`/`in_flight_since`
  were tracked on the live client but never emitted — a channel could
  be mid-call and report "idle". Every channel dict on every server's
  `connectivity_report` (surfaced via check_invariants `channels` and
  status_report) now carries both.
- **P6 — topology field completeness.** agora's `lab://topology`
  carried only 2 of the ~13 channel diagnostic fields — the full
  fault-attribution set must now passthrough.
- **P17 — `probe: "n/a"`.** Local channels reported `probe: null`,
  ambiguous between "never probed" and "nothing to probe". Local
  targets now report `"n/a"`; remote channels that haven't probed yet
  keep `null`.

Marker tag: `diag-chan`. Report every refusal verbatim — errors are
data.

Read tool schemas before calling. The observability surface is
check_invariants (`channels` array) + each server's status resource +
agora's lab://status and lab://topology.

PART A — in-flight fields on every server

1. check_invariants (or status_report → channels) on each of the four
   writable servers — episteme, zetesis, arete, anamnesis. For EVERY
   channel dict in each `channels` array: assert the keys
   `in_flight_operation` and `in_flight_since` are PRESENT (values
   may be null when idle). Missing keys = FAIL. Report each server's
   channel array verbatim and tally the keyset size.
2. If you can observe a channel mid-operation (e.g. during a
   refresh_roster pull or a slow upstream call), capture
   in_flight_operation non-null — if nothing is in flight when you
   look, null is correct; do not fabricate.

PART B — topology carries the full field set

3. Agora: lab://topology → each upstream server's channel entries must
   carry the full diagnostic field set — state, role, target, probe,
   last_* timings, failure attribution, in_flight_operation /
   in_flight_since — not a 2-field subset. Diff the topology entry
   against the same server's own channel dict (from step 1): any
   field the server emits that topology drops is a FAIL. Report both
   verbatim side by side.
4. lab://status on agora → the channels arrays inside each server's
   status passthrough carry the same fields (agora digests verbatim).
   Report the per-server keysets.

PART C — probe semantics

5. In every channel dict: channels whose `target` is a local/in-process
   peer must report `probe: "n/a"` — `null` on a local channel is a
   FAIL (ambiguous). Remote channels that haven't probed keep `null`;
   remote up channels carry a real probe result. Report the probe
   value per channel verbatim with its target.
6. `probe: "busy"` on any channel must read informational — it is NOT
   a down violation. If you observe busy, confirm it doesn't appear
   in violations; report what you saw.

PART D — honest bookkeeping

7. Report verbatim: every server's channels array, the topology
   entries, every probe/target pair. Any channel DOWN when you look —
   report its full dict including in_flight_* and last_error.
8. check_invariants on all five servers at the end — report payloads;
   acknowledge any violation you caused with an honest disposition.

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools.
- Do NOT edit server configuration mid-run.
- Report every refusal verbatim, and what resolved it.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /srv/lab/exchange/diagnostics-out/channel-current-fields/
   — report.md (per-server channel arrays verbatim with the two new
   keys confirmed, the topology-vs-server field diff, the probe
   value per channel with its target) plus evidence files (tool
   result JSON per step).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/channel-current-fields
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
