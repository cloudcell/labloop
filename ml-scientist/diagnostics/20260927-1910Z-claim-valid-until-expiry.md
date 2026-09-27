You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session verifies the rc-7b anamnesis fix (R6): `valid_until` —
the `live → expired` transition — existed in the claim model, the
store column, and the `include_expired` read filter, but NO
agent-reachable tool could set it (state-machine coverage marked the
transition "defined but unwritable"). `assert_claim` now takes an
optional `valid_until` (ISO-8601, timezone-aware UTC). The row is
never deleted — expiry hides from the default live view only.

Marker tag: `diag-expiry`. Report every refusal verbatim — errors are
data.

Read the assert_claim / get_claim / list_claims schemas before
calling. All work is on anamnesis (:38090).

PART A — minting with expiry

1. assert_claim: content "diag-expiry: future-dated claim",
   type "empirical", confidence 0.5, valid_until = an ISO-8601 UTC
   timestamp ~30 days out in `Z` form. Response must carry
   `claim_id` AND echo `valid_until` — and the echoed/stored value
   must be normalized to `+00:00` form (the expiry filter compares
   lexicographically; `Z` input must not be stored raw). get_claim →
   `valid_until` present. list_claims → claim IS in the live view.
2. assert_claim with NO valid_until → claim minted, valid_until
   `null`, live view. Baseline unchanged.

PART B — the transition is real

3. assert_claim with `valid_until` = now + ~3 seconds (still
   `Z`/offset form). list_claims → present. Wait 5 s → list_claims
   → ABSENT from the default view; list_claims with
   `include_expired: true` → PRESENT, row intact, `valid_until`
   recorded. get_claim → still fetches the full row — expiry is a
   view filter, never a delete.
4. assert_claim with `valid_until` already in the PAST → mints an
   already-expired claim: allowed (the record says so), hidden by
   default, visible with include_expired. Report the payload.

PART C — negatives

5. valid_until = `"2030-01-01T00:00:00"` (naive, no timezone) →
   REFUSED — report the error verbatim; it must say the timestamp
   needs a timezone.
6. valid_until = `"next tuesday"` (garbage) → REFUSED — report the
   error verbatim; it must name ISO-8601.
7. Dedup check: re-assert the step-1 claim verbatim (same content,
   same or no valid_until) → returns the existing claim_id
   (deduplicated) rather than minting a second row. Report the
   response.

PART D — honest bookkeeping

8. Report verbatim: every assert_claim/get_claim/list_claims
   payload, both refusal strings, the before/after-expiry list
   contents, the stored `valid_until` forms (Z in, +00:00 stored).
9. check_invariants on anamnesis at the end — report the payload;
   acknowledge anything you caused with an honest disposition.
10. If any step could not be reached — say which and why; a skipped
    step must be louder than a passing one.

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools.
- Do NOT edit server configuration mid-run.
- Report every refusal verbatim, and what resolved it.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /srv/lab/exchange/diagnostics-out/claim-valid-until-expiry/
   — report.md (every payload verbatim, the expiry transition
   timestamps, both refusals) plus evidence files (tool result JSON
   per step).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/claim-valid-until-expiry
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
