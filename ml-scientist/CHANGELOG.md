# Changelog — ml-scientist

All notable changes to the ml-* MCP ecosystem are recorded here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
entry timestamps are commit times in **UTC**.

## [Unreleased]

### Removed

- **`claims.importance` and `claim_edges.weight`** (e-plan
  20260929-1643Z) — **breaking MCP change**: `assert_claim` no
  longer takes `importance`; `relate` no longer takes `weight`;
  evidence-dict `weight` keys are ignored; both columns are dropped
  from `memory.db` by a guarded `DROP COLUMN` migration (the
  store's first destructive migration — non-default row counts are
  logged before each drop). Both fields were written-never-read —
  defaults that carried no information and invited a false reading
  (a weighted-looking graph that never ranked evidence). The
  registry entries `CLAIM_IMPORTANCE_DEFAULT` and
  `EDGE_WEIGHT_DEFAULT` are deleted with them; the registry now
  holds zero `UNGROUNDED` and zero `transitional` entries, pinned
  by new sentinel tests. If evidence ranking is ever wanted it
  returns grounded — computed, with a citation.
  *(2026-09-29 18:58Z)*

### Added

- **Verdict confidence → computed posterior** (e-plan 20260929-1642Z)
  — the indicted confidence literals are deleted:
  `VERDICT_CONFIDENCE_{ACCEPTED,REJECTED}` (0.85 — unreachable per
  NAP Table D-1), `METADECISION_CONFIDENCE` (0.6 — fixture-derived),
  and `min(1.0, abs(promotion_score))` — a mean ratio re-labelled as
  a probability (the Morey et al. error). Grounded procedures landed:
  `arm_evidence` (two-sample normal-theory z, directional
  `bf_2ln = max(z,0)²` — a losing challenger gets LR = 1, never
  BF > 1), `verdict_posterior`/`posterior_from_2lnbf` (the NAP D-1
  oracle bound, honestly labelled an upper bound), `rung_for_2lnbf`
  (the inverse ladder). Contracts may declare `prior` (P[H₁] ∈
  (0, 0.5]), snapshotted on campaign/tournament rows at open like
  `sesoi_d`. `close_campaign`/`close_tournament` compute and persist
  `p_value`/`bf_2ln` (null when the arms can't support it). The rung
  gate graduated to declared ≤ claimed ≤ computed — a promote may not
  assert past the evidence's own bound; `computed_rung`/`bf_2ln`
  forward upstream onto decision rows. Every minted claim carries
  `confidence_basis` (`ungrounded`/`weakly_grounded`/`grounded`), a
  new nullable claims column — a posterior bound mints `grounded`,
  ceiling mints under consulted evidence mint `weakly_grounded`, and
  `conclude_investigation` findings with no refs mint `ungrounded`.
  GUI renders the statistic on campaign/tournament pages and the
  basis on claims; diagnostic prompt
  `20260929-1830Z-verdict-posterior` covers the bounds end-to-end.
  *(2026-09-29 18:30Z)*

- **Contract-declared power** (e-plan 20260929-1641Z) —
  `promotion_policy` is no longer inert: `sesoi_d`, `target_power`,
  and `min_evidence_rung` (Kass–Raftery 2 ln BF ladder) are required
  at `create_evaluation_contract`/`create_meta_contract`, with
  optional `alpha` (0.05). Grounded procedures landed in
  `_grounded_constants`: `required_n` (closed-form two-sample, pilot
  SESOI refused per Albers & Lakens), `min_evidence_rung`/
  `rung_at_least`, `type_s_m` (Gelman & Carlin retrodesign).
  `open_tournament`/`open_campaign` require a declared n (seeds, else
  `budget.programmes_per_arm`), refuse underpowered opens unless
  `allow_underpowered` (recorded `power_acknowledged`), and refuse
  legacy contracts outright — mint a new version. Closed rows freeze
  `n_achieved`, `underpowered` (achieved < required), and Type S/M
  (null when not computable). `record_meta_decision`,
  `record_promotion_decision`, and `record_promotion_verdict` record
  `declared_rung`/`claimed_rung` and refuse a `promote` claiming a
  rung below the contract minimum — declared-vs-claimed; the
  BF-producing statistic is the posterior plan's. GUI renders the
  power design on contract/campaign/tournament pages; diagnostic
  prompt `20260929-1740Z-contract-declared-power` covers the gates
  end-to-end. *(2026-09-29 17:40Z)*

- **Sealed-path runtime deny** — `[executor] sealed_path_patterns` is
  now a physical boundary, not an audit tag: under `minimal`/`full`
  each resolved match is hidden inside the mount namespace before the
  trial starts (dirs → `--tmpfs`, children `ENOENT`; files → a
  mode-000 `--bind-data` mount, `open()` → `EACCES`), applied after
  every other bind so deny beats even the code-seal overlays. Mount
  denies cannot be unlinked by trial code (`EBUSY` even under
  `minimal`'s writable root). A shared fnmatch predicate arms and
  classifies identically (`*` crosses `/` — glob-arming would have
  silently under-armed nested matches); enumeration is bounded and
  critical-root patterns refuse launch. New `sealed_enforcement`
  config (`deny`|`audit`; default `deny`), fail-closed refusal under
  `sandbox="none"`, denied attempts recorded `role=sealed,
  denied=true` in `executed_code.json`, and a new
  `sealed_access_attempts` integrity check (ack-only remedy).
  *(2026-09-27 09:45Z)*
- **Blob retrieval + input-data digests** — `get_blob` /
  `describe_blob` close the evidence-retrievability gap (re-hashed
  bytes on serve), and `executed_code.json` schema v2 records every
  traced open with `role=code|input_data|sealed|other` — input files
  digest-pinned at finalize, sealed paths recorded-but-never-hashed.
  *(2026-09-26 18:29Z)*
- **Cross-server entity routes** — every id family in claim/reference
  surfaces now resolves to a real page: resolve-and-redirect routes
  for context-bound ids (`/hypothesis/`, `/observation/`,
  `/conclusion/`, `/belief/` → parent page + anchor), standalone
  detail pages for decisions/canaries/search-policies, archive-aware
  redirects, click-time `eref-` owner-probing, and 27-prefix peer
  linking in anamnesis claim edges.
  *(2026-09-26 19:04Z – 2026-09-27 05:43Z)*
- **Per-trial `timeout_seconds`** — `run_trial` accepts an
  agent-settable deadline bounded by `max_timeout_seconds`; the
  applied value is recorded in `executor_output`.
  *(2026-09-27 08:35Z)*
- **`labloop export`** — packages a diagnostic run's out-dir plus
  server state into a sha256-verified tarball for host retrieval.
  *(2026-09-26 18:37Z)*
- **Agora home links** in every observability GUI (env-derived with
  TOML override). *(2026-09-26 18:07Z)*
- **VM diagnostic prompts** — `diagnostics/` prompt-inbox:
  recurrent-protocol smoke, blob-retrieval + input digests,
  state-machine coverage (all five servers, embedded matrix), and
  sealed-path runtime deny. *(2026-09-26 17:53Z – 2026-09-27 09:58Z)*
- **Grounded-constants registry** — every decision constant now
  lives in `constants/grounded_constants.py` (byte-identical
  `_grounded_constants.py` mirrors per package, pinned by test),
  each carrying a grounding status, full citation into
  `docs-pub/r-references/val-grounding/`, corpus sha256, and a
  `decision_load` flag enforced by `for_decision()`. All audited
  literals (`PRIOR_CONFIDENCE_MAX` 0.3, verdict `0.85`, arete `0.6`,
  `importance`/`weight` defaults, `status_freshness_seconds` 600,
  `stale_*` 3600, `check_interval`/`stalled_margin` 300,
  `observation_grace`/`improvement_epoch` 86400, `stale_programme`
  24 h, `archive_seal_warn` 72 h, residue `1.0`, `log_max_files`
  100/30) now import from the registry — agora's divergent
  `log_max_files=30` unified at 100. A generated
  `constants/disclosure.md` (drift-pinned by test) and a
  `constants` block in every `X://status` digest expose the state.
  *(2026-09-29)*

### Fixed

- **`close_campaign` zero-divisor** — a zero champion-arm mean now
  refuses with the observed means and names `abandon_campaign`,
  instead of writing `promotion_score=0.0` (the camp-532d7b60 bug;
  Gleser–Hwang 1987 — no always-bounded ratio score covers a
  zero denominator). Mirrors `close_tournament`'s refusal.
  *(2026-09-29)*

- **rc-11 extraction findings** — the rc-11 VM battery surfaced three
  code defects, all fixed: `assert_claim` deduplication silently
  dropped the caller's evidence edges (re-assertion now attaches
  them to the existing claim and reports `edges_added`); the closed
  `RefType` vocabulary had no honest type for `bundle-*`/`data-ref-*`
  ids so callers misfiled them as `external` (new `bundle`/`dataref`/
  `reference` members + `cites` relation, a write-time guard
  refusing `external` for known internal prefixes, an init-time
  retype of legacy rows, and a `misfiled_external_refs` integrity
  check — 5th anamnesis check); and the completed-trial gate and the
  `mislabeled_outcome` audit diverged — a bare
  `{"status": "completed"}` status-word satisfied the gate while the
  audit never tested `error`/`timed_out` keys (both now share
  `_completion_failure_signals` + `_has_run_artifact`: a concrete
  run artifact — `exit_code` or `stdout` — is required in both
  directions). Diagnostics coverage closed: 7 new battery prompts,
  a `coverage-manifest.txt` sentinel test so a shipped tool without
  a diagnostic is a suite failure, and the `campaign-orchestration`
  step-16 expectation corrected to the documented post-close pull
  contract. *(2026-09-29 09:30Z)*
- **rc-11 sibling coverage — tool deadlines + bounded connect on all
  servers** — the episteme-freeze fixes extended to the siblings that
  shared the defect class: `tool_deadline_seconds` (shielded
  `wait_for` → named `deadline_exceeded`) now wraps `call_tool` in
  zetesis, arete, anamnesis, and agora (`server_config` plumbed
  through each `__main__`), and zetesis/arete's connectivity
  supervisors bound `connect()` the same way agora's does — a
  listening-but-silent upstream is marked `down` and retried instead
  of parking the supervisor forever. *(2026-09-29 07:29Z)*
- **Arete decision-debt deadlock (F-17, critical)** — closing a
  tournament created "closed tournament lacks meta_decision" debt,
  which gated *every* arete mutator including the evidence pulls
  needed to mint the discharge evidence — an unbreakable wedge, and
  invisible because the gate had no named check. Evidence miners are
  now debt-exempt (`DEBT_DISCHARGE_TOOLS`), `decision_debt` is a named
  `check_invariants` check, and `REMEDY_TOOLS` covers the full
  discharge path. *(bundled in 2026-09-27 06:36Z)*
- **`open_work` count/display mismatch + ordering** — agora printed
  the full open count but rendered only 5 items silently; each
  server's status digest now sorts `open_work` newest-first
  (programmes by `last_activity_at`, trials by `started_at`), and the
  card discloses truncation with a `+N more` row.
  *(2026-09-27 07:10Z)*
- **v2 manifest rows rendered blank** — the trial view read only
  `original_path`/`code_hash`, so `input_data`/`sealed`/`other` rows
  showed `—` and were mislabeled "not in sealed bundle"; the view now
  renders `path`/`sha256`/`role`/`reason` honestly.
  *(2026-09-27 08:42Z)*
- **strace-divergence false positive** from a uv-managed interpreter
  path change. *(2026-09-26 20:16Z)*
- **Diagnostic prompt defects** — `labloop-export` instructions (no
  `./labloop` in the VM), embedded transition matrix when the
  handbook is absent, and pull-evidence-before-close ordering so the
  F-17 path is executable. *(2026-09-26 – 2026-09-27)*


## [0.0.1] — 2026-09-25 — first public release

### Added

- **episteme (Loop 0)** — the experiment loop: role-based MCP server
  with local adaptors for optimizer / executor / memory, programme +
  hypothesis + trial state machines, and `conclude_hypothesis` /
  `close_programme` split with evidence enforcement.
  *(2026-09-13 20:33Z – 2026-09-14 04:50Z)*
- **Observability GUIs** — per-server web dashboards (programmes,
  hypotheses, trials, beliefs, claims) with search, filtering, and
  cross-server entity links. *(from 2026-09-14 12:56Z)*
- **Artifact capture + archives** — multi-file code capture,
  gzip-compressed trial artifacts in SQLite, automatic programme
  archiving with batched archives and integrity verification, and
  rerun-from-archive via `capture_bundle_from_code_hash`.
  *(2026-09-14 11:19Z–19:42Z)*
- **anamnesis (semantic memory)** — standalone claims server;
  `ClaimsRole` adaptor mints claims automatically at
  `conclude_hypothesis`, replacing the episodic memory role.
  *(2026-09-15 22:10Z; adaptor 2026-09-16 01:05Z)*
- **Sandboxed trial execution** — bubblewrap mount namespaces
  (`none|minimal|full|auto`; `auto` resolves to `full` and fails the
  run when bwrap is missing — fail closed, never degrade), in-sandbox
  strace executed-code capture, and rw-shared host caches under
  `full`. *(2026-09-16 05:40Z–15:06Z)*
- **zetesis (Loop 1)** — investigation-scaffold server:
  `open_investigation` → `pull_evidence` → `record_finding` →
  `conclude_investigation`. *(2026-09-16 17:21Z)*
- **Integrity layer** — `check_invariants`, `/health/deep`, and an
  `/integrity` GUI with check-run audit logs. *(2026-09-17 07:05Z)*
- **`labloop` lifecycle layer** — `start|stop|restart|status|logs`
  for the whole stack, upstream-first ordering with `/health` waits.
  *(2026-09-18 13:27Z)*
- **agora (status hub)** — read-only aggregation of all four
  channels (`lab://status`, `lab://topology`), the uniform
  `status_report` contract, and exploratory dashboards (overview,
  entity graph). *(2026-09-18 16:51Z; dashboards 2026-09-19 14:44Z)*
- **arete (Loop 2)** — the recursive loop: tournaments between
  improvers, meta-contracts, and campaign orchestration driving
  zetesis through a dedicated write channel. *(2026-09-17 20:04Z;
  orchestration 2026-09-19 13:14Z)*
- **Artifact ingest surface** — token-gated HEAD/GET blob surface
  plus artifact→snippet promotion; enabled only when
  `ML_EPISTEME_INGEST_TOKEN` is set. *(2026-09-21 04:02Z)*
- **Verifiable artifact digests** — resolution-or-none digest
  verification at registration (arete + episteme).
  *(2026-09-24 01:50Z)*
- **Executor interpreter override** — `ML_EPISTEME_EXECUTOR_PYTHON`
  selects the trial interpreter (used by the lab VM's
  `/opt/trial-env` scientific stack). *(2026-09-24 21:01Z)*

### Tool surface hardening *(2026-09-21)*

- Real MCP error channel — 301 error payloads become `is_error`
  results. *(07:27Z)*
- `Field(description=…)` on all 280 parameters; closed-vocab params
  became `Literal` (schema-visible enums). *(07:38Z–07:45Z)*
- Zero-arg mutation guards — destructive calls require `dry_run`
  acknowledgement. *(07:57Z)*
- Declared `outputSchema` on all 96 tools + `structured_content`.
  *(09:43Z–09:55Z)*

### Fixed

- **`design_experiment` parallel race** — concurrent calls both
  promoted the same hypothesis; the loser hit an illegal
  `under_test→under_test` transition after already creating its
  trial. Hypothesis promotion is now atomic and idempotent.
  *(2026-09-24 21:11Z)*
- **stdio transport corruption** — informational prints moved to
  stderr; they were corrupting JSON-RPC on stdout. *(2026-09-21 04:54Z)*
- **Ingest surface** — HEAD route reachable again (Starlette
  auto-adds HEAD to GET routes); stored blobs re-hashed on GET so
  corruption returns 500 instead of wrong bytes.
  *(2026-09-21 05:19Z–05:44Z)*
- **Integrity false positives** — observation grace, undeclared
  wall-budget, and deadline-relative stalled checks.
  *(2026-09-17 10:44Z–10:55Z)*
- **Tournament correctness** — seed-shape validation, honest close
  refusal, `correct_tournament_result`. *(2026-09-22 15:57Z)*

### Changed

- README rewritten as Lab Loop™ — commitments, architecture, and
  the per-loop workflow. *(2026-09-25 04:28Z)*
