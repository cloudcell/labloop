# Changelog — ml-scientist

All notable changes to the ml-* MCP ecosystem are recorded here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
entry timestamps are commit times in **UTC**.

## [Unreleased]

### Added

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

### Fixed

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
