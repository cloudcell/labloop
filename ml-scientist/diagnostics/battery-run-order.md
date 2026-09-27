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
| anamnesis | 4 | — |
| arete | 12 | — |
| episteme | **12** | 11 = pre-rc-7b |
| zetesis | **13** | 12 = pre-rc-7 |

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

## Phase 2 — rc-7b regression prompts

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

## Phase 3 — the standing battery (22)

Order within the standing set matters less; group by blast radius:

**Read-mostly / low blast radius** (safe in any order):
- `20260927-1445Z-channel-current-fields`
- `20260927-1450Z-integrity-report-honesty`
- `20260926-0713Z-state-machine-coverage` — step 16 now exercises
  `valid_until` for real (was SKIP on the pre-R6 build)
- `20260927-0330Z-boundary-class-registry`
- `20260926-0624Z-blob-retrieval-and-input-digests`
- `20260927-1500Z-stderr-elision-and-schema-docs`

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
- `20260927-0006Z-campaign-orchestration`
- `20260927-0310Z-campaign-metric-write-gate`
- `20260927-1430Z-campaign-value-gate-abandon`
- `20260927-0315Z-roster-rollback-derivation`
- `20260927-1420Z-claim-mint-loop2-reftypes`
- `20260926-0552Z-recurrent-protocol-smoke`

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
  seal-deny conditional negatives skip. Configure in the VM image if
  that path matters.
- **P16 positive control**: still needs a seeded violation or a
  configurable `observation_grace_seconds`; the 24 h default blocks
  `integrity-report-honesty`'s positive control. Open item.
- Each prompt stages its own `labloop-export` deliverable under
  `/srv/lab/exchange/diagnostics-out/<slug>/` — confirm the export
  lands before starting the next prompt, or the extraction misses it.
