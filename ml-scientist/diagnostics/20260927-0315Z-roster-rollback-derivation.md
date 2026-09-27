You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session verifies the rc-5 ROLLBACK-derivation fix — rc-5 found that
`DerivedStatus.rolled_back` existed in zetesis's model and
`list_candidates(status=...)` accepted it, but `refresh_roster` never
wrote it: it reconciled only the champion via `get_incumbent`, ignoring
the upstream decision log. A `rollback` verdict left the candidate
`challenger` forever. The roster must now derive rollback state from the
latest upstream verdict. Prove it, including the dry-run preview and the
un-roll on a later promote. Marker tag: `diag-rollback`. Report every
refusal verbatim — errors are data.

Read tool schemas before calling. Upstream promotion decisions live on
episteme (record_promotion_decision, insert-only, verdict in
promote|reject|hold|rollback); the roster lives on zetesis
(refresh_roster, list_candidates).

PART A — happy path: derive rolled_back from the decision trail

1. Episteme: register_candidate ×2 (A and B — record both cand- ids).
2. record_promotion_decision: A ← verdict "promote" (rationale,
   decided_by "agent:diagnostics", evidence_refs ≥1).
3. Zetesis: refresh_roster(dry_run=false) → A adopted and reconciled
   champion (upstream incumbent). list_candidates → A present.
4. Episteme: record_promotion_decision on A ← verdict "rollback"
   (rationale "diag-rollback: revocation test", decided_by
   "agent:diagnostics" — episteme does not gate rollback on human:,
   zetesis does). A rollback does NOT delete the promote — both rows
   stay in the insert-only log. Verify via a decisions read if one
   exists.
5. Zetesis: refresh_roster(dry_run=false) → the response's `reconciled`
   must name A in its rolled_back list. list_candidates(status=
   "rolled_back") → A listed. list_candidates() unfiltered → A's
   status is rolled_back, not challenger.

PART B — NEGATIVE: dry-run writes nothing

6. Episteme: register_candidate C → promote C → rollback C (three
   rows, insert-only). Do NOT refresh yet.
7. Zetesis: refresh_roster(dry_run=true) → the response must preview
   BOTH effects without writing: C appears in `would_adopt` AND in
   `would_reconcile.rolled_back`. Before the fix's dry-run coverage
   this preview silently omitted candidates that had no roster row
   yet — if C is missing from would_reconcile, that is a FAIL.
8. list_candidates → C must NOT appear — the dry run wrote nothing.
   Verify no roster row exists for C.
9. refresh_roster(dry_run=false) → C adopted AND rolled_back; the
   previewed and actual outcomes must match.

PART C — latest verdict wins

10. Episteme: record_promotion_decision on A ← verdict "promote" again
    (later than the rollback). refresh_roster → A un-rolls: latest
    verdict governs, so A is champion again (rolled_back is a derived
    shadow of the trail, not a sticky flag). Report A's status.
11. NEGATIVE — non-rollback verdicts: register D upstream, record a
    "hold" and a "reject" on D (never promoted). refresh_roster → D is
    adopted but NOT rolled_back — only a rollback verdict derives that
    state, and only on a candidate with a prior promote. Report D's
    status.
12. NEGATIVE — orphaned rollback: register E, record "rollback" on E
    with NO prior promote. refresh_roster → E must not become
    rolled_back (upstream skips orphaned rollbacks when deriving the
    incumbent; zetesis must mirror). Report what status E gets.

PART D — honest bookkeeping

13. Report verbatim: every record_promotion_decision response, every
    refresh_roster response (dry_run and real — `adopted`,
    `would_adopt`, `reconciled`, `would_reconcile` in full), every
    list_candidates payload.
14. check_invariants on episteme + zetesis at the end — report
    payloads; acknowledge any violation you caused with an honest
    disposition.
15. If any step could not be reached — say which and why; a skipped
    step must be louder than a passing one.

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools.
- Do NOT edit server configuration mid-run.
- Report every refusal verbatim, and what resolved it.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /srv/lab/exchange/diagnostics-out/roster-rollback-derivation/
   — report.md (the decision trail verbatim, every roster refresh
   response, every list_candidates payload, per-candidate status at
   each stage) plus evidence files (tool result JSON per step).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/roster-rollback-derivation
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
