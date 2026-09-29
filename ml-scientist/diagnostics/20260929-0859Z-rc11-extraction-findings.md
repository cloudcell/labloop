You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session verifies three rc-11 extraction fixes:

- **R41 — dedup must not drop evidence.** `assert_claim` used to
  early-return on a content-identical re-assertion BEFORE recording
  the caller's evidence edges — re-asserting with a new citation
  silently discarded it. Re-assertion must now attach the new edges
  to the existing claim and report `edges_added`.
- **R42 — honest reference vocabulary.** `RefType` gained `bundle`,
  `dataref`, `reference`; `Relation` gained `cites`. `external` is
  refused when `to_ref` carries a known internal prefix — the old
  enum gave callers no honest type for `bundle-*`/`data-ref-*` ids,
  so they polluted the `external` bucket.
- **R44 — 'completed' needs a run artifact, not a status word.** A
  bare `{"status": "completed"}` executor record must not satisfy
  the completion gate at `correct_trial_status`, and a completed
  trial carrying one must be flagged by `mislabeled_outcome`. The
  gate and the audit now share one predicate.

Marker tag: `diag-rc11`. Report every refusal verbatim — errors are
data.

PART A — R41: dedup attaches new evidence

1. Anamnesis: assert_claim with content "diag-rc11 dedup probe" and
   one evidence edge — e.g.
   `[{"to_ref": "trial-diag-a", "ref_type": "trial",
   "relation": "tested_by"}]` — plus confidence inside the evidence
   ceiling. Record `claim_id`, `edge_ids`, `deduplicated: false`.
2. Re-assert the IDENTICAL content with the SAME evidence → expect
   `deduplicated: true`, `edges_added: 0`, same claim_id. get_claim →
   exactly one edge. Report verbatim.
3. Re-assert the identical content with a NEW evidence edge —
   `[{"to_ref": "trial-diag-b", "ref_type": "trial",
   "relation": "supports"}]` → expect `deduplicated: true`,
   `edges_added: 1`, same claim_id. get_claim → the edge set now has
   BOTH edges. **The pre-fix behaviour dropped this edge while still
   returning success — a missing second edge is a FAIL.** Report
   verbatim.
4. Re-assert once more with BOTH prior edges → `edges_added: 0`,
   edge set still two — no duplicates. Report verbatim.
5. NEGATIVE: re-assert the identical content with a malformed edge
   (bad `ref_type`, or `ref_type: "claim"` pointing at a nonexistent
   claim-id) → named error, no edge written. Report verbatim.

PART B — R42: reference vocabulary + external guard

6. relate from your claim with `ref_type: "bundle"`,
   `to_ref: "bundle-<8 hex>"`, `relation: "cites"` → accepted;
   get_claim shows the typed edge. Report verbatim.
7. Same for `ref_type: "dataref"` / `to_ref: "data-ref-<id>"` and
   `ref_type: "reference"` / `to_ref: "data-ref-<id>"` → accepted.
8. NEGATIVE — the mislabel that polluted the bucket: `ref_type:
   "external"` with `to_ref: "bundle-44bd7102"` (or any `bundle-*` /
   `data-ref-*` / `claim-*` / `trial-*` id) → REFUSED, naming the
   honest type. A silent accept re-opens the R42 bug — FAIL.
9. POSITIVE CONTROL: `ref_type: "external"` with a genuinely opaque
   id (e.g. `doi:10.1234/example` or a raw URL string) → accepted —
   `external` still exists for what it means. Report verbatim.
10. `cites` is NOT evidence: assert_claim a fresh claim at confidence
    above the prior ceiling with only a `cites` edge → refused by
    the evidence requirement (cites is a citation, not support).
    Then assert the same content at/below the ceiling → minted;
    re-assert above the ceiling with a `tested_by` edge → attaches
    on the dedup path regardless (the ceiling is mint-time-only).

PART C — R44: status-word records cannot launder a completion

11. Episteme: run a trivial trial (create_programme →
    design_experiment → run_trial with a short script that exits 0)
    OR reuse any existing completed/failed trial. cancel_trial a
    RUNNING one if you started it — you want a trial whose executor
    record is a cancellation receipt (`{"status": "cancelled", …}`).
12. correct_trial_status that cancelled trial → "completed" →
    REFUSED (the executor record evidences non-completion). Report
    verbatim.
13. check_invariants on episteme → `mislabeled_outcome` must stay
    clean for what you did; report the payload. If the lab carries
    pre-fix laundered rows, the check may now FLAG them — a nonzero
    count here is the check working (late detection of historical
    debt), not a regression. List the flagged trial ids verbatim.
14. If any completed trial's executor record is a bare
    `{"status": "completed"}` status-word with no exit_code/stdout,
    it must appear in `mislabeled_outcome` — report whether any such
    rows exist.

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools.
- Do NOT edit server configuration mid-run.
- Report every refusal verbatim, and what resolved it.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /srv/lab/exchange/diagnostics-out/rc11-extraction-findings/
   — report.md (the claim_id, all four assert_claim responses
   verbatim, the edge set after each step, the external-guard
   refusal, the correct_trial_status refusal, check_invariants
   payloads) plus evidence files (tool result JSON per step).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/rc11-extraction-findings
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
