You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session verifies two rc-6 executor fixes:

- **P2 — credential scrub.** rc-6 found `env = dict(os.environ)` in the
  local executor handed the server's whole environment — including
  `ML_EPISTEME_INGEST_TOKEN` and every credential-shaped variable — to
  sandboxed trial code. The fix is a BLACKLIST scrub (`ML_EPISTEME_*`
  + names matching TOKEN|SECRET|PASSWORD|CREDENTIAL|_KEY$), not a
  whitelist — runtime vars (PATH, CUDA_*, VIRTUAL_ENV,
  LD_LIBRARY_PATH) must survive or GPU trials break. Prove secrets are
  gone AND runtime vars survive.
- **P7 — seal fields always.** `seal_enforced`, `seal_staged`,
  `launch_refused` were only emitted on the launch-refusal path — a
  consumer couldn't tell "not enforced" from "old build". Every
  executor result must now carry all three.

Marker tag: `diag-env`. Report every refusal verbatim — errors are
data.

Read tool schemas before calling. You will run real trials on
episteme — set up programme + hypothesis first.

PART A — setup

1. Episteme: create_programme + formulate_hypothesis for tag `diag-env`.
   The programme must be status=active for run_trial — check
   get_programme / the run_trial schema description for the gate.

PART B — happy path + env scrub (the trial inspects itself)

2. run_trial whose code is literally:
      import json, os
      print(json.dumps(sorted(os.environ)))
   After completion, retrieve the trial output (get_trial_status →
   executor_output / stdout as surfaced). From the env dump, verify:
   - ABSENT: any key starting `ML_EPISTEME_` (the ingest token above
     all), any key matching *TOKEN*, *SECRET*, *PASSWORD*,
     *CREDENTIAL*, or ending *_KEY — report verbatim any that leak,
     with the key NAME only, never the value (a leaked value in your
     report is itself a spill).
   - PRESENT: PATH, ML_SCI_ARTIFACT_DIR — and if the host env carries
     CUDA_VISIBLE_DEVICES / LD_LIBRARY_PATH / VIRTUAL_ENV, those must
     survive too (report which were present).
   Report the full sorted key list (keys only, no values).

PART C — seal fields on every result shape

3. From the step-2 completed trial's executor output: confirm all of
   `seal_enforced`, `launch_refused`, `seal_staged` are present as
   keys. Report their values verbatim.
4. run_trial with code `import sys; sys.exit(3)` → failed result.
   Confirm the same three keys are present on the FAILED shape too.
   Absence on either shape is a FAIL.
5. If the VM runs a sandboxed executor (check protocol://status or
   the adaptors config): also capture a seal/refusal path if you can
   trigger one legitimately (e.g. a sealed-path write if patterns are
   configured) — but do not force it; report what the field values
   were on the paths you exercised.

PART D — honest bookkeeping

6. Report verbatim: every run_trial call + result, the env key list,
   the seal-field triples from each result shape.
7. If the executor output isn't fully surfaced through
   get_trial_status, say so — what fields you could and could not
   observe. Do NOT try to read the artifacts dir off-disk.
8. check_invariants on episteme at the end — report the payload;
   acknowledge any violation you caused with an honest disposition.

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools.
- Do NOT edit server configuration mid-run.
- Report every refusal verbatim, and what resolved it.
- NEVER report secret VALUES. Key names only.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /srv/lab/exchange/diagnostics-out/sandbox-env-scrub/
   — report.md (the observed env key list, leaked-key verdict, the
   three seal fields per result shape, every refusal) plus evidence
   files (tool result JSON per step).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/sandbox-env-scrub
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
