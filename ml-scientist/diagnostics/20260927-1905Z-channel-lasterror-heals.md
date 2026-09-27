You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session verifies the rc-7b channel-fields fix (R4): the VM run
saw zetesis's `evidence` channel report `state: "up"` + `probe: "ok"`
+ `last_error: "ConnectError: All connection attempts failed"` — a
healed channel carrying a stale error. `last_error` is *current*
state: it must clear when the channel proves healthy (successful
connect, transport-ok call, or ping ok), while `last_failed_*` stay
sticky for attribution. The fix lives in FOUR separate client
implementations (episteme, zetesis, arete, agora) — a heal on one
proves nothing about the others.

Marker tag: `diag-heal`. Report every refusal verbatim — errors are
data.

PART A — baseline field semantics (no fault needed)

1. Pull each server's status digest (the `X://status` resource via
   read_resource, the status_report prompt, or agora's lab://topology
   — whichever surfaces carry the `channels` array). For EVERY
   channel record: `state`, `probe`, `last_error`, `last_operation`,
   `last_failed_operation`, `last_failed_at`, `connected_at`,
   `attempts`.
2. ASSERT per channel: a channel showing `state: "up"` with
   `probe` in (`"ok"`, `"n/a"`, or a healthy idle) must have
   `last_error: null`. A stale error string on an up+ok channel is
   the exact defect — FAIL, and name the channel + server.
   `last_failed_operation`/`last_failed_at` MAY be populated — that
   history is correct and must NOT be cleared; flag only if they
   claim a failure that never happened.
3. `in_flight_operation`/`in_flight_since` on an idle channel must
   be null — check that too while you're here.

PART B — induced fault → heal (the load-bearing part)

4. Induce a fault on the anamnesis server process inside the
   lab-cnt-mcp container (docker exec / kill / suspend — whatever
   mechanism the VM exposes). If you have no fault-injection path,
   mark Parts B–C SKIP and say why — baseline Part A still runs.
5. Poll a dependent's claims/evidence channel until `state: "down"`;
   record `last_error` (leaf cause — NOT a TaskGroup repr),
   `last_failed_operation`, `last_failed_at` verbatim.
6. Restart anamnesis. Poll until the channel shows `state: "up"` +
   `probe: "ok"` — then STRICT: `last_error` MUST be `null`. "Cleared
   or a fresh cause" is not good enough — after a heal it is null,
   while `last_failed_operation`/`last_failed_at` still name the
   fault that killed it. Report both field families verbatim.
7. Repeat per reachable channel — the clients differ per server:
   - episteme's claims channel (mcp_adaptor.py)
   - zetesis's evidence/upstream channels (mcp_client.py)
   - arete's upstream channels (mcp_client.py)
   - agora's upstream channels (mcp_client.py — its op surface is
     read_resource, not call_tool)
   Killing anamnesis exercises the dependents that route to it;
   killing a different upstream exercises a different set. Heal each
   channel you break before moving on.

PART C — busy is not an error record (best-effort)

8. If you can make a server unresponsive WITHOUT killing it
   (SIGSTOP or saturation): a ping timeout leaves the channel up
   with `probe: "busy"` — and must NOT record a `last_error` (busy
   isn't a fault). Resume it and confirm `probe` returns to `"ok"`
   with `last_error` still null. If you cannot induce busy, say so —
   do not fake it.

PART D — honest bookkeeping

9. Report verbatim: every channel object seen at each phase
   (baseline / faulted / healed), detection and heal latencies, and
   every field absent or opaque.
10. A stale `last_error` on any channel after heal is an automatic
    FAIL — that is the regression this diagnostic exists to catch.
11. If any step was skipped, say which and why — a skipped step must
    be louder than a passing one.

While you work:
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools and health surfaces.
- Do NOT edit server configuration mid-run.
- Restart anything you killed before finishing — leave the lab up.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /srv/lab/exchange/diagnostics-out/channel-lasterror-heals/
   — report.md (per-channel verdicts; fault/heal sequences verbatim;
   the before/during/after field triples) plus evidence files
   (status digests per phase, per server).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/channel-lasterror-heals
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
