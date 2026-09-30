You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session verifies **verdict confidence → computed posterior**
(e-plan 20260929-1642Z): the flat confidence literals (0.85 verdicts,
0.6 meta-decisions, `min(1, |score|)` re-labelled as a probability) are
gone. Minted confidence is now either the NAP D-1 oracle posterior
bound — computed from the contract's declared `prior` and the measured
`2 ln BF` — or the grounded prior ceiling, and every minted claim
carries `confidence_basis`. The rung gate graduated: a promote may
claim no more than the evidence's own upper bound.

Marker tag: `diag-posterior`. Report every refusal verbatim — errors
are data.

Prerequisite: the powered-contract machinery from
`20260929-1740Z-contract-declared-power` (a contract carrying
sesoi_d/target_power/min_evidence_rung, plus `prior` — e.g. 0.4).

PART A — the statistic exists at close

1. zetesis open_campaign under a powered contract (with prior=0.4),
   seeds ≥ 2 per arm. Record ≥2 results per arm with clearly separated
   primary-metric values (e.g. champion ~80, challenger ~120).
   close_campaign → response must carry `p_value`, `bf_2ln`,
   `computed_rung` alongside n_achieved/Type S-M. get_campaign → the
   same fields persisted on the row, plus `prior: 0.4` snapshotted.
2. Honesty probe: record only ONE result per arm and close →
   `p_value`/`bf_2ln`/`computed_rung` must be null — n<2 means the
   statistic honestly does not exist; null, not a fabricated rung.
3. Directional probe: a campaign where the challenger LOSES (champion
   ~120, challenger ~80) → `bf_2ln` must be 0.0 and `computed_rung`
   "not_worth". An unsigned statistic would have minted BF > 1 for a
   loser — confirm the bound is directional.

PART B — the computed-rung ceiling

4. On a closed campaign whose computed_rung is low (weak separation),
   record_promotion_verdict verdict=promote with claimed_rung ABOVE
   computed → must FAIL "exceeds the evidence", echoing computed_rung
   and bf_2ln. No upstream decision may be written — verify via
   episteme list_promotion_decisions.
5. Retry with claimed_rung at or below computed (and ≥ the contract's
   min_evidence_rung) → proceeds. The upstream decision row must carry
   computed_rung + bf_2ln — the measured evidence, not just the claim.
   Verify via episteme list_promotion_decisions / get_campaign.
6. Symmetric on arete: close_tournament with ≥2 seeds per arm freezes
   `p_value`/`bf_2ln` on the tournament row (per-seed bests are the
   samples); record_meta_decision promote claiming above the
   tournament's computed_rung → refused with the same bound message.

PART C — the minted confidence

7. After the passing promote (PART B step 5): anamnesis get_claim on
   the minted claim_id → `confidence_basis` = "grounded", `confidence`
   = the recomputed posterior bound (prior-odds × BF form — at
   bf_2ln ≈ 600 the bound saturates at 1.0), and
   `confidence_computation` naming the procedure and inputs
   (plan-20260930-0240Z: zetesis names the derivation, anamnesis
   computes it — no number crosses the wire).
8. A verdict on a campaign with no statistic (bf_2ln null) → the
   minted claim carries confidence NULL and `confidence_basis` =
   "weakly_grounded" — no computation, no number. Same on arete: a
   meta-decision citing no tournament mints NULL + weakly_grounded.
9. conclude_hypothesis (episteme) → the minted claim carries
   confidence NULL, `confidence_basis` = "weakly_grounded" —
   single-arm verdicts have no comparative likelihood; inconclusive
   still mints nothing.
10. conclude_investigation (zetesis): a finding recorded WITH
    evidence_ref_ids mints `weakly_grounded`; a finding with none
    mints `ungrounded`. Both carry confidence NULL — the label must
    appear beside an explicit null, never a number.

PART D — the audit surface

11. anamnesis assert_claim with a `confidence_basis` argument →
    refused as undeclared (callers cannot self-label; the basis is
    server-derived). get_claim/list_claims echo the stored basis;
    the observability claim page renders "—" (or equivalent explicit
    null) beside the label for NULL confidence — never a 0% bar or
    a fabricated fraction.
12. constants/disclosure.md — `VERDICT_POSTERIOR` is present as a
    DERIVED procedure; `VERDICT_CONFIDENCE_*` and
    `METADECISION_CONFIDENCE` are absent entirely (deleted, not
    marked transitional).
13. Honesty probe: the minted "grounded" confidence is an oracle
    UPPER bound — BF = exp(max(z,0)²/2) is the most generous possible
    likelihood ratio, not a calibrated posterior. The label
    `confidence_basis` is what makes that honest. Note explicitly in
    your findings whether any surface still presents a confidence
    float without its basis label beside it.

Export: tarball your findings into
/srv/lab/exchange/diagnostics-out/verdict-posterior/ including every
refusal verbatim, close payloads, the persisted campaign/tournament
rows, and the minted claims' basis fields.
