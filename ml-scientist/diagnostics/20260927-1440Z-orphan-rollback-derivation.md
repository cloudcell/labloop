You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session verifies the rc-6 roster derivation fix — **P13: orphan
rollbacks don't mark rolled_back.** rc-6 found that `refresh_roster`'s
rollback derivation fired on ANY rollback verdict in a candidate's
trail, even one with no prior promote — so a candidate that was never
promoted could read as `rolled_back`, disagreeing with the incumbent
derivation on the same trail. The derivation now requires a prior
promote in the candidate's own trail.

Marker tag: `diag-orphan`. Report every refusal verbatim — errors are
data.

Read tool schemas before calling. Roster = zetesis (refresh_roster,
list_roster); the verdict trail = upstream episteme
(record_promotion_decision — evidence_refs ≥1 is now required; a
"rollback" verdict needs decided_by "human:<name>").

PART A — happy path: orphan rollback does NOT mark rolled_back

1. Episteme: register_candidate → a fresh candidate with NO promotion
   history. Then record_promotion_decision verdict "rollback",
   evidence_refs ≥1, decided_by "human:diag" — a rollback with no
   prior promote is the orphan case. (If the verdict itself refuses,
   report verbatim — that's also data.)
2. Zetesis: refresh_roster → then get/list the roster entry for that
   candidate. `rolled_back` must be ABSENT / false — an orphan
   rollback carries no prior promote to roll back FROM. A
   `rolled_back: true` here is a FAIL (the pre-fix bug).
3. Report the roster entry verbatim — especially rolled_back,
   incumbent/champion flags, and any derived fields.

PART B — happy path: real promote→rollback DOES mark rolled_back

4. Fresh candidate: record_promotion_decision "promote" (evidence_refs
   ≥1, decided_by "human:diag") → refresh_roster → confirm it lands
   as incumbent or promoted in the roster. Then record a "rollback"
   verdict on the SAME candidate → refresh_roster again.
5. Now the roster entry MUST show rolled_back (a real promote exists
   to roll back) — report the entry verbatim. This is the non-orphan
   path; it must still work.

PART C — NEGATIVE + dry-run

6. refresh_roster with dry_run (if the schema exposes it) on the same
   state → the preview must match what the live refresh derived —
   an orphan rollback previews rolled_back=false exactly as it lands.
   Report the preview verbatim.
7. A promote AFTER the orphan rollback on the step-1 candidate →
   refresh_roster → the entry un-rolls/promotes normally (latest
   verdict wins — rc-5's semantics unchanged). Report verbatim.

PART D — incumbent derivation agreement

8. The incumbent/champion side of the roster must stay consistent:
   get_incumbent / list_roster after each refresh — a rolled-back
   candidate is never left as incumbent, and an orphan-rolled-back
   candidate is never marked as if it had been promoted. Report each
   roster read verbatim.
9. check_invariants on zetesis at the end — report the payload;
   acknowledge any violation you caused with an honest disposition.

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools.
- Do NOT edit server configuration mid-run.
- Report every refusal verbatim, and what resolved it.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /srv/lab/exchange/diagnostics-out/orphan-rollback-derivation/
   — report.md (every decision + refresh verbatim, each roster entry
   showing rolled_back presence/absence, the dry-run preview, the
   un-roll) plus evidence files (tool result JSON per step).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/orphan-rollback-derivation
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
