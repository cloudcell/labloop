You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session verifies the **numeric provenance invariant** (e-plan
20260930-0240Z): an LLM never originates a numeric confidence.
`assert_claim` has no `confidence`/`confidence_basis` parameters;
`confidence_computation` {procedure, inputs} names a registered
derivation and anamnesis recomputes and stores the result. No
computation → `confidence` is NULL, not a ceiling literal.
`record_finding` in zetesis dropped its caller-declared confidence on
the same argument — a finding is a proto-claim, not a measurement.

Requires the numeric-provenance build — a pre-invariant server still
accepts `confidence`/`confidence_basis` on `assert_claim` and
`confidence` on `record_finding`, mints prior-ceiling literals, and
runs the `unsupported_high_confidence` audit (the check is now
`unverifiable_confidence`).

Marker tag: `diag-numprop`. Report every refusal verbatim — errors are
data.

PART A — the call surface no longer accepts a number

1. anamnesis assert_claim: content "diag-numprop caller number",
   type empirical, confidence 0.97 → REFUSED on the gated dispatch
   naming `confidence` as an undeclared argument (rc-12 W6). A silent
   mint storing 0.97 — or storing anything numeric — is THE finding
   this prompt exists to catch.
2. assert_claim: same content (vary it), confidence_basis "grounded"
   → REFUSED naming `confidence_basis`. Callers cannot self-label.
3. assert_claim: "diag-numprop bare claim", type empirical, no other
   args → succeeds; the response MUST show `confidence: null` and
   `confidence_basis: "ungrounded"`. get_claim → the stored row
   echoes both, and `confidence_computation` is null. Any numeric
   confidence here is a fabrication at the protocol surface.

PART B — the computation path is the only path to a number

4. assert_claim: "diag-numprop computed claim", type empirical,
   confidence_computation {"procedure": "posterior_from_2lnbf",
   "inputs": {"prior": 0.3, "bf_2ln": 2.7055}},
   evidence [{to_ref: "trial-diag1", ref_type: "trial", relation:
   "derived_from"}] → succeeds; the response's `confidence` must be
   ≈0.624 (the NAP D-1 row recomputed — the exact float, not 0.9)
   and `confidence_basis` "grounded". get_claim →
   `confidence_computation` echoes verbatim — inputs included.
5. assert_claim with confidence_computation {"procedure":
   "not_a_procedure", "inputs": {}} plus an evidence edge → REFUSED
   naming the unknown procedure — the registry is closed.
6. assert_claim with a well-formed computation but NO evidence edge
   → REFUSED. A posterior floating free of the graph is not allowed
   to exist.
7. Dedup probe: re-assert the step-4 content verbatim WITH a different
   computation ({"procedure": "posterior_from_2lnbf", "inputs":
   {"prior": 0.5, "bf_2ln": 2.7055}}) → returns the existing
   claim_id with `deduplicated: true`; get_claim → the stored
   confidence is still ≈0.624 — re-assertion cannot launder a new
   derivation onto an old row. (Correction path is supersede, not
   silent upgrade.)

PART C — zetesis mirrors the rule

8. zetesis record_finding on an open investigation with confidence
   0.9 → REFUSED naming `confidence` (undeclared). Without it →
   succeeds; get_investigation → the finding's `confidence` is null.
9. conclude_investigation verdict="findings" → claim mints; on
   anamnesis get_claim → `confidence: null`; `confidence_basis` is
   "weakly_grounded" iff the finding carried an evidence ref,
   "ungrounded" otherwise. No number may appear.
10. record_promotion_verdict on a closed campaign WITH a statistic
    (≥2 results/arm, disjoint ranges) → the minted claim carries
    `confidence_computation` {procedure posterior_from_2lnbf (promote)
    or h0_bound_from_2lnbf (retain/rollback)} and a recomputed
    `confidence` — zetesis names the derivation, anamnesis computes.

PART D — the audit reads derivations, not thresholds

11. anamnesis check_invariants → the check list contains
    `unverifiable_confidence`, NOT `unsupported_high_confidence` —
    the old name anywhere on a live surface is a finding.
12. The claims GUI (http://localhost:38091 or the lab's claims GUI
    port): a claim row shows the basis label and "—" (or equivalent
    explicit null rendering) for NULL confidence — never a 0%-bar or
    a fabricated fraction. Same for the zetesis findings table.

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools.
- Do NOT edit server configuration mid-run.
- Report every refusal verbatim, and what resolved it.
- A skipped step must be louder than a passing one.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /srv/lab/exchange/diagnostics-out/numeric-provenance/
   — report.md (every refusal verbatim; every minted confidence value
   quoted; explicitly answer: can any caller-supplied number reach a
   claim row?) plus evidence files (tool result JSON per step).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/numeric-provenance
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
