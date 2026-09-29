# Diagnostic battery — run order

Operational note for sequencing the diagnostics/ prompts on the lab VM.
Not a governed doc — an ordering + gating recipe for whoever (or
whichever agent) drives the battery.

## Phase 0 — build fingerprint (before any prompt runs)

The rc-7 extraction ran a **pre-rc-7 build** — the battery measured
rc-6 while reporting as rc-7. Confirm the deploy before spending a
run:

| Server | `check_invariants` count (current main) | Stale-build signal |
| ------ | ------------------------------------- | ------------------ |
| agora | 2 | — |
| anamnesis | **5** | 4 = pre-rc-11 |
| arete | 12 | — |
| episteme | **12** | 11 = pre-rc-7b |
| zetesis | **15** | 14 = pre-rc-9; 13 = pre-rc-8 |

Plus: `read_resource` must appear in every server's tool list, and
`assert_claim` must accept `valid_until`. Any miss → wrong build;
stop, redeploy, restart.

## Phase 1 — build gate

1. **`20260927-1915Z-rc7-surface-verification`** — runs the Phase-0
   fingerprint as its own first assertion and then verifies every
   rc-7 surface that has never been exercised on the lab
   (`read_resource` ×5 + agora scheme routing, `unrunnable_campaigns`
   wedge + `abandoned_at`, `list_trials.retry_reason`, generator
   stdout error, artifact typing, `pull_evidence` enums). If its
   report opens with "stale build", the rest of the battery is
   re-measuring rc-6 — do not interpret downstream failures as new
   regressions.

## Phase 2 — regression prompts

1. **`20260928-1608Z-rc9-findings`** — verifies all six rc-9 fixes:
   the new `incomplete_campaigns` check (15th zetesis check —
   `camp-4f94d190` residue from the rc-9 run is the fixture),
   `rollback` claim minting, Loop-0 `RefType` members, episteme
   `source_id` provenance, dedup `valid_until` echo, and the
   `open_arm_campaign` docstring mechanism. ~15–20 min, no sleeps.
   Runs its own 15-check fingerprint first.
2. **`20260927-1910Z-claim-valid-until-expiry`** — pure anamnesis
   reads/writes, no other server involved, ~1 min of wall time
   (one 5 s expiry wait). Cheapest prompt; good smoke test that the
   harness is healthy.
3. **`20260927-1900Z-cancel-spawn-window-kill`** — episteme trials,
   three spawn-window cancels + one mid-run cancel. Contains two
   ~90 s sleep windows; expect ~6–8 min. Needs container-shell
   access (`docker exec lab-cnt-mcp pgrep`) for the liveness step —
   BLOCKED there is honest, the residue check still catches leaks.
4. **`20260927-1905Z-channel-lasterror-heals`** — needs fault
   injection (kill/restart anamnesis inside `lab-cnt-mcp`).
   Disruptive: it takes a channel down deliberately. Run it when no
   other diagnostic is mid-flight, and confirm the lab is fully
   healed afterward (Part A's baseline assertion doubles as the
   post-check).
5. **`20260929-0859Z-rc11-extraction-findings`** — verifies the rc-11
   extraction fixes: dedup `edges_added` semantics + the
   bundle/dataref/reference/cites vocabulary + the `external`-prefix
   guard + the R44 run-artifact gate. Pure MCP calls, no sleeps.
   Requires the rc-11 build — a pre-fix server fails Part A step 3
   and Part B step 8.

## Phase 3 — the standing battery (29)

Order within the standing set matters less; group by blast radius:

Order within the standing set matters less; group by blast radius:

**Read-mostly / low blast radius** (safe in any order):
- `20260927-1445Z-channel-current-fields`
- `20260927-1450Z-integrity-report-honesty`
- `20260926-0713Z-state-machine-coverage` — step 16 now exercises
  `valid_until` for real (was SKIP on the pre-R6 build)
- `20260927-0330Z-boundary-class-registry`
- `20260926-0624Z-blob-retrieval-and-input-digests`
- `20260927-1500Z-stderr-elision-and-schema-docs`
- `20260929-0910Z-read-paths-and-registries` — sweeps the uncalled
  read surface; first write to `list_search_policies` (registry has
  never been written — its `[]` is honest state, not a failure)
- `20260929-0915Z-resource-surface` — every `read_resource` URI
  family ×5 servers; asserts field shapes (`kind`/`src`/`dst`),
  not just payload presence
- `20260929-0920Z-ingest-surface` — `:38082` fail-closed probes;
  `connection refused` is a designed state → UNREACHABLE, not pass

**Loop-0 trial lifecycle** (run trials; leave residue rows — normal):
- `20260927-1455Z-retry-reason-ordering`
- `20260927-0325Z-trial-terminal-precedence`
- `20260927-1425Z-sandbox-env-scrub`
- `20260926-2056Z-sealed-path-runtime-deny` — step 15 now expects
  launch refusal on a deleted sealed source (corrected expectation)
- `20260927-0305Z-sealed-provenance-honesty`
- `20260927-1435Z-spawn-guard-ordering`
- `20260927-0004Z-write-boundary-gates`
- `20260927-1440Z-orphan-rollback-derivation`

**Loop-1/2 campaign machinery** (cross-server, longer):
- `20260927-0006Z-campaign-orchestration` — step 16 now expects the
  post-close pull to be ACCEPTED (verdict, not close, ends
  consultation); step 18b asserts the post-verdict refusal
- `20260927-0310Z-campaign-metric-write-gate`
- `20260927-1430Z-campaign-value-gate-abandon`
- `20260927-0315Z-roster-rollback-derivation`
- `20260927-1420Z-claim-mint-loop2-reftypes`
- `20260926-0552Z-recurrent-protocol-smoke`
- `20260929-0900Z-arm-campaign-verbs` — the arete-side campaign
  wrappers (`record_arm_result`, `pull_arm_evidence`,
  `close_arm_campaign`, `record_arm_verdict`) that
  `campaign-orchestration` bypassed
- `20260929-0905Z-canary-and-correction` — `record_canary`/
  `close_canary` lifecycle + `correct_tournament_result` (a
  correction path — post-R35 these get deliberate coverage)

**Long-running / deadline-dependent**:
- `20260929-0925Z-deadline-exceeded` — needs a ~150 s trial to cross
  the 120 s tool deadline; asserts the named `deadline_exceeded`
  error AND that the shielded trial completes server-side. Requires
  the rc-11 build — on a pre-deadline image the call just runs long
  (honest outcome: UNREACHABLE, not pass).

**Disruptive — run last** (kill/suspend server processes):
- `20260927-0320Z-connectivity-live-gating`
- `20260927-0005Z-connectivity-fault-observability` — step 9 now
  requires healed `last_error` == `null` (strict, rc-7b semantics)

## Environmental prerequisites

- **Fault injection**: `channel-lasterror-heals` Parts B–C and
  `connectivity-fault-observability` Parts B–D need `docker exec` /
  kill into `lab-cnt-mcp`. Without it they report honest SKIP —
  decide per-run whether that's acceptable.
- **`sealed_path_patterns`**: unconfigured on the lab → the
  seal-deny conditional negatives skip. Now a tracked rc-11 finding
  (R45 — ml-labloop se-plan); the skip stays honest until it arms.
- **`observation_grace_seconds`** (`[integrity]`): the knob exists
  — the open item is a VALUE in the VM config (or a seeded
  backdated fixture); the 24 h default blocks
  `integrity-report-honesty`'s positive control.
- **Coverage manifest**: `diagnostics/coverage-manifest.txt` maps
  every registered tool/resource to the prompt(s) that name it. A
  sentinel test fails the suite if a new surface ships unnamed —
  when adding a tool or prompt, update the manifest in the same
  commit.
- Each prompt stages its own `labloop-export` deliverable under
  `/srv/lab/exchange/diagnostics-out/<slug>/` — confirm the export
  lands before starting the next prompt, or the extraction misses it.
