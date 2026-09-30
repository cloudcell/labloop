You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session verifies the **grounded-constants registry** (e-plan
20260929-1514Z): every decision constant now carries a provenance
record, every status digest reports it, and an undefined promotion
score must REFUSE — never write a fabricated number. The audit found
`close_campaign` writing `promotion_score=0.0` when the champion mean
was zero (camp-532d7b60 froze a challenger win at score 0);
Gleser–Hwang (1987) is why that value cannot exist.

Marker tag: `diag-constants`. Report every refusal verbatim — errors
are data.

PART A — the constants block in status digests

1. read_resource on protocol://status, search://status,
   improver://status, claims://status → each payload must carry a
   `constants` object with `registered`, `scoring_path`,
   `transitional`, `scoring_violations`. Record each verbatim.
   Assert `scoring_violations` is an empty list — a non-empty list
   is a FAIL, verbatim.
2. lab://status → confirm each upstream digest's `constants` block
   survives aggregation (per-server `registered` counts must match
   across all four — they mirror one registry).

PART B — zero-divisor refusal on close_campaign

3. zetesis open_campaign → record the campaign_id. (Setup needs a
   powered contract + incumbent + challenger — README §Contract
   recipe; open with seeds=[1] for the declared-n gate.) record a
   campaign_result for arm=champion with the campaign's
   primary_metric = 0, and one for arm=challenger with a positive
   value (e.g. hits 0 vs hits 5).
4. close_campaign on it → must FAIL with an error naming the
   undefined score and pointing at abandon_campaign. Record the
   error verbatim. A closed campaign with promotion_score 0.0 is
   a FAIL — that is the fabricated-score bug (camp-532d7b60).
5. get_campaign → assert status is still `open` and
   promotion_score is null — refusal leaves no frozen record.
6. abandon_campaign with a rationale recording the observed means
   → succeeds; get_campaign → status `abandoned`, rationale stored.
   This is the honest terminal path the refusal names.

PART C — caller-originated numbers are refused; the registry computes

7. anamnesis assert_claim with a `confidence` argument (any value)
   → refused as an undeclared argument (numeric provenance
   invariant, plan-20260930-0240Z). A bare mint — no computation —
   stores confidence NULL with basis ungrounded. Record verbatim.
8. assert_claim with confidence_computation {"procedure":
   "posterior_from_2lnbf", "inputs": {"prior": 0.3, "bf_2ln":
   2.7055}} AND an evidence-bearing edge → mints confidence ≈0.624 —
   the NAP D-1 row recomputed by the registry, basis grounded. The
   number is the registry's output, never the caller's.

PART D — the disclosure surface

9. Locate the checked-in disclosure table
   (constants/disclosure.md in the deployed tree, or read it
   through whatever surface exposes repo files). Verify each
   scoring row (Scoring=yes) carries a Grounding column that names
   a corpus file or marks `transitional` with a named replacement —
   no scoring row may read plain `UNGROUNDED`.
10. Report the registry totals you observed (registered /
    scoring_path / transitional) against what PART A reported —
    they must agree.

Export: tarball your findings into
/srv/lab/exchange/diagnostics-out/grounded-constants/ including the
verbatim digests, the close refusal, and the disclosure table.
