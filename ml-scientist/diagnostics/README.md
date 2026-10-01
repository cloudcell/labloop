# diagnostics/

Operational prompt-inbox for lab VM diagnostic runs. Not governed
documentation — these are *inputs* to the VM agent, not outputs of the
lab. Filenames: `<UTC-timestamp>-<slug>.md` (same convention as docs/).

## The contract

Every diagnostic prompt ends with a DELIVERABLE section requiring the
agent to:

1. Write its findings into `/srv/lab/exchange/diagnostics-out/<slug>/`
   — `report.md` plus evidence files (verbatim tool results, JSON
   snapshots, computed counts). The slug is the filename minus
   timestamp and `.md`. **`labloop-export` only accepts paths under
   `/srv/lab/exchange/`** (path mode is pinned there) — stage the out
   dir directly under the exchange root, not the workspace — rc-5
   found every prompt staging under the agent's home workspace, which
   the export refuses; the path is pinned for that reason, not by
   convention.
2. Stage the deliverable for host retrieval:

   ```bash
   labloop-export /srv/lab/exchange/diagnostics-out/<slug>
   ```

3. Report the staged export path, byte count and sha256 verbatim.
4. **Staging discipline.** Every scratch/fixture dir the run creates
   lives under `diagnostics-out/<slug>/` (or a namespaced
   `diagnostics-out/<slug>/work/`) — never loose `diag-*` dirs at the
   exchange root. `--all` exports carry `/srv/lab/exchange` wholesale,
   so root-level staging pollutes every subsequent extraction (rc-15
   shipped ~15 stale `diag-*` dirs this way). If a run needs scratch
   outside its slug dir, it must remove it before the export.

`labloop-export` (installed at `/usr/local/sbin/labloop-export`,
nopasswd-free for `lab`) freezes the staged set into a manifest +
`export.tar.gz` under `/var/lib/labloop-export/user/`. A run is not
complete until the export succeeds.

**There is no `./labloop` in the VM.** `labloop` is the host-side repo
launcher (manages `~/.ml-*` server processes on the operator's
machine; its `export` subcommand packages host-side `diagnostics/out/`
+ server state into `sxport/`). In the guest the servers live in the
`lab-cnt-mcp` container and the only sanctioned egress is
`labloop-export` — do not look for `./labloop`, and do not treat its
absence as a blocker worth escalating.

**Fallback**: only if `labloop-export` itself is missing (VM predates
it) does the agent emulate it rather than stalling — tar.gz the out
dir into `/srv/lab/exchange/`, `sha256sum` it, and report path +
bytes + digest labelled *substitute for `labloop-export`*.

## Retrieval

From the host:

```bash
./90-extract-lab-data.sh <vm> <dest-dir>
# lands <dest>/<vm>-extraction-<UTC>.tar.gz — sha256-verified.
# Unpack deliberately: the payload is agent-produced, treat as untrusted.
```

`diagnostics-out/` is a run artifact — gitignored.

## Contract recipe (post-1641Z/1642Z/1643Z)

Prompts written before `plan-20260929-1641Z` assume a looser schema.
The current contract surface:

- **`promotion_policy` is required** at `create_evaluation_contract`/
  `create_meta_contract` and must carry `sesoi_d`, `target_power`,
  `min_evidence_rung` (optional `alpha`, `prior`). A policy missing
  them is refused at creation; contracts minted pre-gate can be read
  but open no new work — mint a new version.
- **Minimal recipe** for any prompt that needs a contract:
  `{"sesoi_d": 4.0, "target_power": 0.8, "min_evidence_rung":
  "not_worth"}` → `required_n = 1` per arm, so `seeds=[1]` (or a
  `budget.programmes_per_arm ≥ 1`) satisfies the declared-n gate at
  `open_campaign`/`open_tournament`/`open_arm_campaign`.
- **Promotes need `claimed_rung`** when the contract declares
  `min_evidence_rung`, and may not claim above the closed
  campaign's/tournament's `computed_rung` — `claimed_rung:
  "not_worth"` always satisfies `declared ≤ claimed ≤ computed`.
- `close_campaign`/`close_tournament` now return `p_value`,
  `bf_2ln`, `computed_rung` alongside the Type S/M fields; minted
  claims carry `confidence_basis`.
- `assert_claim` has no `importance` and `relate` has no `weight`
  param — callers sending them are refused (1643Z). A `weight` key
  inside an evidence dict is ignored, like any unrecognized key.

## Inventory

| Prompt | Verifies |
| --- | --- |
| `20260926-0552Z-recurrent-protocol-smoke` | End-to-end Loop-0/Loop-1 cycle smoke test |
| `20260926-0624Z-blob-retrieval-and-input-digests` | Blob retrieval, input-data digesting |
| `20260926-0713Z-state-machine-coverage` | Lifecycle state-machine coverage across servers |
| `20260926-2056Z-sealed-path-runtime-deny` | Sealed-path runtime deny-list semantics |
| `20260927-0004Z-write-boundary-gates` | Write-boundary gates (rc-4 fixes) |
| `20260927-0005Z-connectivity-fault-observability` | Connectivity fault observability (rc-4) |
| `20260927-0006Z-campaign-orchestration` | Orchestrated campaign/spawn lifecycle (rc-4) |
| `20260927-0305Z-sealed-provenance-honesty` | rc-5: sealed-manifest hash honesty, `host_sha256`, deleted-target launch refusal, directory role, `input_data_undigested` exemption + ack annotation, vacuous-check `skipped` |
| `20260927-0310Z-campaign-metric-write-gate` | rc-5: `record_campaign_result` primary-metric write gate |
| `20260927-0315Z-roster-rollback-derivation` | rc-5: `rolled_back` derivation, dry-run preview, latest-verdict-wins un-roll |
| `20260927-0320Z-connectivity-live-gating` | rc-5: `channels` observability array ×5, live-over-logged gating, `channel:` ack refs |
| `20260927-0325Z-trial-terminal-precedence` | rc-5: persisted-terminal status precedence, `mark_retryable` cancellation, `prepare_data` stderr surfacing |
| `20260927-0330Z-boundary-class-registry` | rc-5: boundary-class registry discoverability, kernel authoritative classification, `rejected` reachability |
| `20260927-1420Z-claim-mint-loop2-reftypes` | rc-6: Loop-2 `RefType` claim minting (P1), `evidence_refs ≥1` verdict gate (P3) |
| `20260927-1425Z-sandbox-env-scrub` | rc-6: credential/`ML_EPISTEME_*` env blacklist vs runtime-var survival (P2), seal fields on every result shape (P7) |
| `20260927-1430Z-campaign-value-gate-abandon` | rc-6: metric *value* write gate + `unscoreable_campaigns` wedge check (P12), `abandon_campaign` terminal path (P9) |
| `20260927-1435Z-spawn-guard-ordering` | rc-6: override-legality-before-cap, spawn-scope-before-attribution ordering, `open_arm_campaign` budget/seeds (P8) |
| `20260927-1440Z-orphan-rollback-derivation` | rc-6: orphan-rollback (no prior promote) must not mark `rolled_back` (P13) |
| `20260927-1445Z-channel-current-fields` | rc-6: `in_flight_operation`/`in_flight_since` on all channel dicts (P4), `lab://topology` full field set (P6), `probe: "n/a"` on local channels (P17) |
| `20260927-1450Z-integrity-report-honesty` | rc-6: phantom-ack `no_matching_violation` ×4 (P16), `input_data_undigested` denominators/`skipped` (P5), closed-programme exemption (P10), `improver://classes` (P11) |
| `20260927-1455Z-retry-reason-ordering` | rc-6: durable `retry_reason` on mark_retryable + correct_trial_status (P14), terminal-before-cancel ordering (P18) |
| `20260927-1500Z-stderr-elision-and-schema-docs` | rc-6: line-aligned head+tail stderr elision (P15), schema-doc truthfulness nits (P11) |
| `20260927-1900Z-cancel-spawn-window-kill` | rc-7b: shielded spawn — cancel inside `create_subprocess_exec` kills the child (R1), late finalize keeps the full terminal row (R2), `terminal_with_live_executor` residue check (R3) |
| `20260927-1905Z-channel-lasterror-heals` | rc-7b: `last_error` clears on connect/call/ping success across all four upstream clients; `last_failed_*` stay sticky (R4) |
| `20260927-1910Z-claim-valid-until-expiry` | rc-7b: `assert_claim(valid_until=…)` — live→expired write path, normalization, naive/garbage refusals, row never deleted (R6) |
| `20260927-1915Z-rc7-surface-verification` | rc-7 deferred verification — the last VM run predated the deploy: `read_resource` ×5 + agora routing, 15-check zetesis incl. `unrunnable_campaigns`, `abandoned_at`, `list_trials.retry_reason`, generator stdout error, artifact typing |
| `20260928-1608Z-rc9-findings` | rc-9: `incomplete_campaigns` check (15th; `camp-4f94d190` residue is the fixture), `rollback` claim minting, Loop-0 `RefType` members (`candidate`/`contract`/`decision`), episteme `source_id`=conclusion, dedup `valid_until` echo, `open_arm_campaign` docstring mechanism |
| `20260929-0859Z-rc11-extraction-findings` | rc-11: dedup `edges_added` (R41), `bundle`/`dataref`/`reference` ref types + `cites` + `external`-prefix guard (R42), run-artifact completion gate + shared predicate (R44) |
| `20260929-0900Z-arm-campaign-verbs` | Coverage (R49): arete arm-campaign verbs — `record_arm_result`, `pull_arm_evidence`, `close_arm_campaign`, `record_arm_verdict` end-to-end |
| `20260929-0905Z-canary-and-correction` | Coverage (R49): `record_canary`/`close_canary` lifecycle, `correct_tournament_result` audit trail, sealed-record refusal |
| `20260929-0910Z-read-paths-and-registries` | Coverage (R49): uncalled read paths + first writes to empty registries (`register_search_policy`, `record_finding`, `verify_archive`, `wait_trial`, `capture_bundle_from_code_hash`, `create_meta_contract`, …) |
| `20260929-0915Z-resource-surface` | Coverage (R49): every `read_resource` URI family ×5 servers; field-shape assertions (`kind`/`src`/`dst`), negative-URI probes, agora routing |
| `20260929-0920Z-ingest-surface` | Coverage (R49): `:38082` ingest API fail-closed probes — 401 without/with wrong token; UNREACHABLE ≠ pass |
| `20260929-0925Z-deadline-exceeded` | Coverage (R49, post-rc-11): tool deadline fires at ~120 s AND shielded trial completes server-side; loop stays responsive |
| `20260929-1615Z-grounded-constants` | Grounded-constants registry (e-plan 20260929-1514Z): `constants` block in every status digest, `close_campaign` zero-divisor refusal (camp-532d7b60), prior-ceiling enforcement, disclosure table |
| `20260929-1740Z-contract-declared-power` | Contract-declared power (e-plan 20260929-1641Z): policy power keys gate creation, declared-n + underpowered gates at open (ack path records `power_acknowledged`), close-time n/Type-S/M freeze, rung gate on all three decision writers, legacy contracts open no new work |
| `20260929-1830Z-verdict-posterior` | Verdict posterior (e-plan 20260929-1642Z): `p_value`/`bf_2ln`/`computed_rung` persisted at close, claimed ≤ computed rung gate, NAP D-1 posterior bound minted as `confidence`, `confidence_basis` on every claim, indicted confidence literals deleted |

The `20260927-03xxZ` set is the rc-5 verification battery — each
targets one rc-5 fix and exercises both the valid path and the
negative/refused path the fix introduces. The `20260927-14xxZ` set is
the rc-6 battery — one diagnostic per coherent fix cluster in
`docs/e-plans/plan-20260927-1315Z--rc6-diagnostic-findings.md` (P1–P18;
P0's roster fix is re-verified by `orphan-rollback-derivation`), again
exercising both valid and refused paths. The `20260927-19xxZ` set is
the rc-7b battery — regressions for the rc-7 VM run's findings
(`docs/e-plans/plan-20260927-1746Z--rc7b-diagnostic-findings.md`)
plus the rc-7 surface verification the stale VM build could not
perform.
