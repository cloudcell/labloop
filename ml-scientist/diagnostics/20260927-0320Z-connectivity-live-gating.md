You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session verifies the rc-5 CONNECTIVITY fixes — rc-5 found two
defects: (a) the per-channel state (probe results, busy detection,
fault attribution) was invisible to tools-only agents — check_invariants
exposed only "N channel(s) configured; all up"; and (b) a STALE logged
connectivity violation kept gating writes after the channel healed —
the very boot-straggler problem the check exists to catch, inverted:
an upstream slow to start left a durable violation that blocked writes
until someone acknowledged it. The fix: full channel arrays in every
check payload, and live state substituted over logged state. Prove both.
Marker tag: `diag-conngate`. Report every refusal verbatim — errors are
data.

Read tool schemas before calling. Run step 1 IMMEDIATELY at session
start — boot stragglers are the live test.

PART A — happy path: channel observability

1. IMMEDIATELY: check_invariants on episteme, zetesis, arete,
   anamnesis. Each upstream_connectivity entry must now carry a
   `channels` array (a payload field, not only inside violations) —
   every configured channel listed with at least: name, status,
   probe result, last_probe_ms or an equivalent timing field. If any
   server still reports only "N channel(s) configured; all up", FAIL.
2. For every channel reporting status "up": probe must read "ok". A
   channel reporting "busy" (probe timed out, session kept) must NOT
   appear as a violation — busy is a live signal, not a fault.
3. Agora: check_invariants → the channels array must carry the
   operation-attribution fields too (current_op / current_op_since or
   equivalents) — agora's channel state has extra fields the other
   servers lack; parity means both directions.
4. If you have a resources/read capability: lab://status and
   lab://topology should agree with the channels arrays. If you lack
   resources/read, note it and move on — that limitation is not a
   defect in this fix.

PART B — NEGATIVE path via fault injection (best effort)

5. Take one upstream DOWN briefly via the sanctioned fault lever:
   `sudo -u mcp /usr/local/sbin/labloop-fault kill anamnesis`
   (zone-wide bounce — all five servers restart together; the quadlet
   heals it). For a per-server busy/wedged shape instead, use
   `labloop-fault freeze <srv>` / `labloop-fault thaw <srv>`. Check
   `labloop-fault status` during the fault. On a VM predating the
   lever the sudo call itself is refused — record the refusal
   verbatim and mark Part B "blocked — no fault injection", then
   continue at Part C. Never improvise: kill by pidfile and pkill -f
   on mcp-owned processes are EPERM by design and count as refusals.
6. If you got a channel down: check_invariants → the channel appears
   in upstream_connectivity.violations with a STABLE ref — a string
   like "channel:<name>" — plus fault attribution (last_error,
   last_failed_operation, last_failed_at). The ref must be
   channel-keyed, not a JSON blob — that is what makes it
   acknowledgeable.
7. While down: a mutating call on the affected server (e.g.
   record_promotion_decision or register_candidate on zetesis's
   upstream side — any write) → refused on open violations. Verbatim.
8. Restart the upstream. Re-run check_invariants → the channel is up,
   probe ok. THEN — the key assertion — retry the SAME mutating call
   IMMEDIATELY, WITHOUT calling acknowledge_violation. It must
   SUCCEED. Before the fix the stale logged violation kept gating
   until acked; live state must now supersede logged state. If the
   write still refuses citing the healed channel, FAIL.
9. If the violation still appears in the check payload post-heal, it
   must be under violations only while actually down — a healed
   channel must not persist as an open violation.

PART C — acknowledgement stability

10. If a connectivity violation exists (induced or logged): ack it with
    acknowledge_violation(check_name="upstream_connectivity",
    object_ref="channel:<name>") → accepted; open_violations clears.
    If acking requires the whole violation dict instead of the stable
    channel ref, the identity fix regressed — FAIL.
11. If no violation exists to ack: exercise acknowledge_violation on a
    DIFFERENT live violation if any exists (e.g. one caused by another
    diagnostic) or record "no violation available — SKIP with reason".
12. check_invariants once more on all four + agora — report the full
    payloads. Acknowledge anything you caused with an honest
    disposition (once you've confirmed writes proceed).

PART D — honest bookkeeping

13. Report verbatim: every check_invariants payload (all five servers,
    full channels arrays), every fault-injection attempt and refusal,
    the gated write and the un-gated retry, every ack exchange.
14. Distinguish in your report: violation-absent-because-healthy vs
    violation-absent-because-acked. They are different states.

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools.
- Do NOT edit server configuration mid-run.
- Report every refusal verbatim, and what resolved it.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /srv/lab/exchange/diagnostics-out/connectivity-live-gating/
   — report.md (channels arrays verbatim per server, fault-injection
   attempt log, the gate→heal→ungated sequence verbatim, ack
   exchanges) plus evidence files (tool result JSON per step).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/connectivity-live-gating
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
