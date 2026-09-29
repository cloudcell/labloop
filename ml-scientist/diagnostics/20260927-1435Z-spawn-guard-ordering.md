You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session verifies the rc-6 spawn/result GUARD ORDERING fixes — three
cases where the wrong check ran first and the real error was shadowed:

- **P8a — override legality before the cap.** `spawn_campaign_programme`
  checked the programmes_per_arm cap before validating the caller's
  `budget` override — an illegal override on a capped arm reported
  "cap reached" instead of the override violation. Legality must be
  validated FIRST so the refusal names what the caller actually did
  wrong.
- **P8b — spawn-scope before the upstream pull.**
  `record_campaign_result` pulled upstream attribution before the local
  spawn-scope check, so a wrong-arm/wrong-campaign result on a spawned
  campaign got the generic "not attributed" refusal instead of the
  precise "was not spawned under campaign / wrong arm" scoping message.
  The local check now runs first.
- **P8c — `open_arm_campaign` (arete) returns the carried context.**
  The orchestrated open response omitted the `budget`/`seeds` it
  carried upstream — a caller couldn't confirm what was pushed. Both
  must be in the response.

Marker tag: `diag-order`. Report every refusal verbatim — the ORDER of
checks is itself data.

Read tool schemas before calling. The orchestrated path needs a
tournament on arete (open_arm_campaign → campaign on zetesis →
spawn_campaign_programme). If the orchestration channel isn't wired,
note it and drive the equivalent on zetesis directly where possible —
but the P8c response-shape check is arete-only; mark it BLOCKED rather
than skipping silently.

PART A — setup: an orchestrated campaign with a cap

1. Arete: open a tournament with a budget that carries
   trials_per_programme AND a small programmes_per_arm cap (e.g. 1) —
   read propose/open tool schemas; the budget must be carryable.
   The meta-contract must be powered (README §Contract recipe) and
   programmes_per_arm doubles as the declared n — with
   sesoi_d=4.0 a cap of 1 satisfies required_n.
2. open_arm_campaign for an arm → the response MUST include `budget`
   and `seeds` fields echoing what was carried (P8c). Report the
   response verbatim — missing fields = FAIL.
3. Record the campaign_id, the carried budget, and seeds verbatim.

PART B — NEGATIVE: illegal override on a capped arm names the override

4. Zetesis: spawn_campaign_programme on that campaign/arm once
   legitimately → succeeds and reaches the cap.
5. Now spawn AGAIN with budget {"max_trials": 999} — an override that
   EXCEEDS the carried value AND hits the already-reached cap. The
   refusal must name the OVERRIDE violation ("exceeds the carried" /
   "only shrink"), NOT "spawn cap reached". Cap-first reporting is a
   FAIL — the caller needs to know what they did wrong.
6. Repeat with an override key that isn't carried at all (e.g.
   budget {"gpu_hours": 5}) → refused naming the unknown key, still
   not the cap. And a legal shrink (e.g. max_trials below carried) on
   the capped arm → now the cap message IS correct. Report all three
   verbatim — the third proves ordering is right, not just error text.

PART C — NEGATIVE: spawn-scope message before upstream attribution

7. On the orchestrated campaign (which HAS spawn records now), call
   record_campaign_result naming a programme_id that was NOT spawned
   by the campaign (any other programme id or a fabricated
   "prog-notspawned"). Expect the SCOPING refusal ("was not spawned
   under campaign …"), not the generic attribution refusal. Report
   verbatim.
8. If you can spawn programmes for BOTH arms: report a challenger-arm
   programme against arm "champion" → the error must name the ARM
   mismatch ("spawned for arm 'challenger', not 'champion'"), not
   attribution. Report verbatim.

PART D — happy path

9. record_campaign_result on a programme genuinely spawned under the
   correct arm → accepted (valid metrics, per the P12 gate). Verify
   the row lands via get_campaign.

PART E — honest bookkeeping

10. Report verbatim: every spawn/result call + response in the order
    made, the open_arm_campaign response, every refusal.
11. If the orchestration channel (arete↔zetesis) isn't wired on this
    VM: mark PART A/C-derived steps BLOCKED and exercise B's ordering
    on a zetesis-opened campaign that carries a budget directly —
    say exactly which path you ran.
12. check_invariants on zetesis + arete at the end — report payloads;
    acknowledge any violation you caused with an honest disposition.

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools.
- Do NOT edit server configuration mid-run.
- Report every refusal verbatim, and what resolved it.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /srv/lab/exchange/diagnostics-out/spawn-guard-ordering/
   — report.md (the open_arm_campaign response verbatim, each of the
   three ordering probes verbatim showing WHICH message fired, the
   happy-path accept) plus evidence files (tool result JSON per step).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/spawn-guard-ordering
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
