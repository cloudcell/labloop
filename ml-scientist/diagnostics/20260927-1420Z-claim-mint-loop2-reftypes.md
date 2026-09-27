You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session verifies two rc-6 fixes:

- **P1 — Loop-2 claim minting.** rc-6 found that `record_meta_decision`
  on arete silently failed to mint anamnesis claims for every decision:
  `_ref_type_for` mapped `archive-`, `tourn-`, `inv-`, `find-`, `imp-`,
  `tres-`, `mcp-`, `mcontract-`, `mdec-`, `pol-`, `canary-` prefixes to
  ref_types that anamnesis's closed `RefType` enum rejected — 11/11
  meta_decisions on the lab had `claim_id` NULL. The enum now covers
  the Loop-2 vocabulary and arete degrades unknown maps to `external`.
  Prove meta-decisions mint claims with typed edges again.
- **P3 — grounded verdicts.** `record_promotion_decision` on episteme
  accepted `evidence_refs: []` despite the "≥1 required" contract —
  decision-752651da persisted ungrounded. The write must now refuse.

Marker tag: `diag-claims`. Report every refusal verbatim — errors are
data.

Read tool schemas before calling. Meta-decisions need a live context
(proposal or tournament) and cite evidence_ref ids minted by
`pull_evidence` — read `improver://classes` and the tool descriptions
first.

PART A — setup: Loop-2 context + evidence pulls

1. Arete: propose_meta_change (any boundary-class-eligible target —
   read `improver://classes` resource first to pick a modifiable
   component) → proposal id. This is your pull context.
2. pull_evidence with context_type "proposal", source "loop0",
   tool "list_archives" — the payload references archive-* ids, which
   become the evidence_ref's ref_ids. Also pull "list_campaigns" from
   "loop1" if campaigns exist (camp-*, cand-*, contract-*, prog-*
   ids). Record every evidence_ref id verbatim.
3. If no archives exist yet, pull any loop0 surface that yields ids —
   list_candidates, list_active_programmes — and note which prefixes
   the refs carry.

PART B — happy path: meta-decision mints a typed claim

4. record_meta_decision citing the evidence_refs from step 2. Expect
   `claim_status: "minted"` (or equivalent success field) and a
   claim_id — NOT null, NOT an error about ref_type. Report verbatim.
5. Anamnesis: get_claim / list_claims → find the minted claim. Report
   its edges verbatim: each edge's ref_type must be the typed Loop-2
   value (`archive`, `tournament`, `investigation`, `finding`,
   `improver`, `tournament_result`, `proposal`, `meta_contract`,
   `meta_decision`, `policy_version`, `canary_deployment`) or a valid
   Loop-0/Loop-1 type — never a type outside the enum. `external` is
   acceptable only for ids matching no known prefix; flag if a
   known-prefix id mapped to `external`.

PART C — NEGATIVE: ungrounded promotion decision refused (episteme)

6. Episteme: register_candidate → candidate id. Then
   record_promotion_decision with evidence_refs: [] (empty list,
   non-empty rationale + decided_by). Expect a refusal naming the
   evidence requirement. A silent accept is a FAIL — the row is
   insert-only.
7. Repeat with evidence_refs: ["trial-<any-plausible-id>"] → accepted.
   Report both responses verbatim side by side.

PART D — NEGATIVE + edge cases

8. record_meta_decision citing a nonexistent evidence_ref id →
   refused; report verbatim. Citing evidence pulled under a DIFFERENT
   context (proposal vs tournament) → report verbatim whether refused.
9. If the lab already has meta_decisions with claim_id NULL from
   before the fix: report the count via list surfaces if observable
   (this is historical debt, not a new failure — note it).
10. check_invariants on arete + anamnesis + episteme at the end —
    report payloads; acknowledge any violation you caused with an
    honest disposition.

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools.
- Do NOT edit server configuration mid-run.
- Report every refusal verbatim, and what resolved it.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /srv/lab/exchange/diagnostics-out/claim-mint-loop2-reftypes/
   — report.md (proposal id, evidence_ref ids + their ref prefixes,
   the meta_decision response verbatim, the minted claim's edges
   verbatim, both promotion-decision responses, every refusal) plus
   evidence files (tool result JSON per step).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/claim-mint-loop2-reftypes
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
