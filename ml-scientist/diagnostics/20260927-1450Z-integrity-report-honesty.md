You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session verifies four rc-6 "the report tells the truth" fixes:

- **P16 — phantom acknowledgements.** `acknowledge_violation` on all
  four writable servers minted an ack row with `status: "acknowledged"`
  even when the named violation didn't exist — a phantom ack read as
  real remediation (specimen: vack-9cda46b9). Unmatched acks must now
  return `status: "no_matching_violation"` — the row is still recorded
  (insert-only ledger) but its status can't masquerade.
- **P5 — undigested denominators.** `input_data_undigested` reported a
  bare numerator — a vacuous pass read identically to a real one. The
  detail must now carry "K of N entries across M manifest(s)", and an
  empty applicable population must report `skipped`.
- **P10 — closed-programme exemption.** `completed_without_observation`
  fired on completed trials of closed/archived programmes — debt
  nobody could remediate (the write path refuses dead programmes).
  Non-active programmes are exempted and counted in the detail.
- **P11 — improver://classes.** The boundary-class registry was only
  in a docstring; it's now a readable resource on arete.

Marker tag: `diag-honest`. Report every refusal verbatim — errors are
data.

PART A — NEGATIVE: phantom ack on every writable server

1. On EACH of episteme, zetesis, arete, anamnesis: call
   acknowledge_violation naming a check + object_ref that does NOT
   exist (e.g. check_name "nonexistent_check", object_ref
   "no-such-object"). The response must say
   `status: "no_matching_violation"` (or equivalent non-acknowledged
   status) — `matched_open_violation: false`. `status: "acknowledged"`
   on a phantom is a FAIL.
2. Positive control on the same server: ack a violation that DOES
   exist (create one legitimately or ack a pre-existing open
   violation from check_invariants) → `status: "acknowledged"` +
   `matched_open_violation: true`. Report both side by side — the
   asymmetry is the fix.
3. Report all eight responses (4 servers × phantom + real) verbatim.

PART B — undigested denominators

4. Episteme: check_invariants → find `input_data_undigested`. Report
   its detail verbatim. If violations exist, the detail must read
   "K of N input_data entries across M manifest(s)" — a bare "K"
   with no denominator is a FAIL. If the population is empty, the
   detail must START with "skipped" — a vacuous-looking "0" is a
   FAIL.
5. If you can legitimately create a manifest with an undigested
   input_data entry (a trial that records input_data without a
   digest), do so and re-run the check → denominator must reflect it.
   Otherwise report what the live population shows.

PART C — closed-programme exemption

6. Episteme: create a programme, run a trial to completed WITHOUT an
   observation, then close/abandon the programme. check_invariants →
   `completed_without_observation` must be ok:true for that trial
   AND the detail must name the exempt count ("…exempt…"). A flagged
   closed-programme trial is a FAIL — that debt is unremediable.
7. Positive control: a completed-without-observation trial on an
   ACTIVE programme (past the grace window if applicable — if your
   trial is too fresh to flag, note that and rely on step 6's
   exempt-count) — report what the check shows.

PART D — improver://classes resource

8. Arete: read the resource `improver://classes` → it must list the
   boundary classes with their tiers (immutable / conditional /
   modifiable). Report the payload verbatim. Then check
   propose_meta_change's schema docstring — it must name the
   modifiable set AND the kernel vocabulary (class-1/2/3 tiers).
   Report the docstring's relevant lines.

PART E — honest bookkeeping

9. Report verbatim: every acknowledge_violation response (8 total),
   both check details, the classes resource, the docstring excerpt.
10. check_invariants on all five servers at the end — report
    payloads; acknowledge any violation you caused with an honest
    disposition (and confirm the ack status is truthful per P16).

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools.
- Do NOT edit server configuration mid-run.
- Report every refusal verbatim, and what resolved it.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /srv/lab/exchange/diagnostics-out/integrity-report-honesty/
   — report.md (all 8 ack responses verbatim, the two check details,
   the classes resource payload, the docstring evidence) plus
   evidence files (tool result JSON per step).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/integrity-report-honesty
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
