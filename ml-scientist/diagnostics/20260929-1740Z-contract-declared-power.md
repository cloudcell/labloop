You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session verifies **contract-declared power** (e-plan
20260929-1641Z): promotion_policy now carries the preregistration
inputs — sesoi_d (a declared smallest effect, never pilot-estimated),
target_power, min_evidence_rung (Kass–Raftery 2 ln BF ladder) — and
they gate creation, open, and verdict. The era of the inert policy
field is over; a contract without the power clause opens no new work.

Marker tag: `diag-power`. Report every refusal verbatim — errors are
data.

PART A — contract creation refuses an unpowered policy

1. episteme create_evaluation_contract against a real programme with
   promotion_policy {"min_gain": 1.0} → must FAIL, error naming all
   three missing keys (sesoi_d, target_power, min_evidence_rung).
   Record verbatim.
2. Same call with malformed values — sesoi_d=-1, target_power=1.5,
   min_evidence_rung="bogus" → must FAIL. Record verbatim.
3. Retry with a full policy: sesoi_d=0.5, target_power=0.8,
   min_evidence_rung="positive", alpha unset → succeeds; record the
   contract_id. (required_n at these values is 63/arm under the
   normal approximation — keep it in mind for PART B.)
4. arete create_meta_contract with promotion_policy {"min_gain":
   1.05} → must FAIL the same way. Retry powered (sesoi_d=4.0,
   target_power=0.8, min_evidence_rung="not_worth" → required_n=1)
   → succeeds; record the contract_id.

PART B — open-time gates on a campaign (zetesis)

5. zetesis open_campaign under the powered episteme contract with
   budget {} and NO seeds → must FAIL with "declares no n" — an
   empty budget plus absent seeds cannot silently mean zero or
   unknown n under a power contract. Record verbatim.
6. open_campaign with seeds=[1] under the sesoi_d=0.5 contract →
   must FAIL "under-powered": n_requested=1 < n_required=63.
   Record the error and the n_required/n_requested it echoes.
7. Retry the same call with allow_underpowered=true → must OPEN.
   Assert the response carries power_acknowledged=true,
   n_required=63, n_requested=1. The shortfall is declared and
   recorded, not hidden.
8. Record one result per arm (primary metric from the contract) and
   close_campaign → response must carry n_achieved=1,
   underpowered=true, and non-null type_s_risk/type_m_ratio
   (Gelman & Carlin retrodesign at the achieved n). get_campaign →
   the same fields persisted on the row; power_acknowledged=true
   survived the freeze.

PART C — the same gates on a tournament (arete)

9. arete open_tournament under the powered meta-contract with a
   budget but NO seeds → must FAIL "declares no n". (The tournament
   derives its n from the seed set — the budget alone is spend, not
   replicates.)
10. open_tournament with seeds=[1] under a sesoi_d=0.5 meta-contract
    → refused; retry with allow_underpowered=true → opens with
    power_acknowledged=true. close_tournament after one result per
    arm → n_achieved, underpowered, type_s/type_m on the response
    AND the frozen row.

PART D — the rung gate on decisions

11. Under a contract declaring min_evidence_rung="strong":
    record_promotion_verdict verdict=promote with claimed_rung=
    "positive" → must FAIL "below the contract minimum", and NO
    upstream decision may be written (verify via episteme
    list_promotion_decisions — the refused verdict leaves no row).
12. Retry with claimed_rung="strong" or "very_strong" → proceeds;
    the decision row and the verdict response both carry
    declared_rung + claimed_rung.
13. record_meta_decision under a min_evidence_rung meta-contract:
    promote with no claimed_rung → refused; promote with a rung
    below declared → refused; at-or-above → records. A "hold"
    verdict needs no rung — the gate binds promotions, not every
    verdict.
14. Honesty probe: the rung check is declared-vs-claimed. Nothing
    yet computes a Bayes factor — the statistic is the posterior
    plan's job. A claimed rung is a claim, not a measurement;
    note this explicitly in your findings.

PART E — legacy contracts

15. If the deployment carries pre-gate contracts (promotion_policy
    without the three keys): open_campaign/open_tournament under
    one → must FAIL with a message directing the caller to mint a
    new contract version. The legacy row stays readable
    (get_evaluation_contract returns it) — readable, never
    openable.

Export: tarball your findings into
/srv/lab/exchange/diagnostics-out/contract-declared-power/ including
every refusal verbatim, the acked open's recorded fields, and the
closed rows' power accounting.
