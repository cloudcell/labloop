You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session verifies two rc-6 "the surface carries enough to debug"
fixes:

- **P15 — stderr elision.** rc-6 found generator/trial stderr was
  tail-truncated at ~500 chars MID-LINE — the `Traceback` header and
  exception line were both destroyed, leaving unreadable garbage.
  `_elide_stderr` now keeps head (1500) + tail (500), cuts only on
  newlines, and inserts an elision marker; the 500-char tail read at
  finalization is newline-aligned too.
- **P11 — schema doc nits.** Tool descriptions were silently wrong or
  incomplete: `run_trial`'s `programme_id` didn't name the
  `status=active` requirement; `record_campaign_result`'s `metrics`
  didn't document case-sensitivity; `close_campaign` /
  `record_promotion_verdict` didn't document pull-before-close
  ordering; `refresh_roster` didn't define `unrolled`.

Marker tag: `diag-stderr`. Report every refusal verbatim — errors are
data.

PART A — happy path: long stderr keeps BOTH ends

1. Episteme: run_trial a trial that writes a LONG stderr then fails —
   e.g. code that prints "Traceback (most recent call last):", then
   ~3000 chars of filler lines, then "RuntimeError: diag-stderr
   sentinel-fault", then exits nonzero. The stderr must exceed 2000
   chars so the elision triggers.
2. Retrieve the surfaced stderr (get_trial_status / the error field /
   executor output as surfaced). Verify ALL of:
   - the "Traceback (most recent call last):" header survived at the
     HEAD — not tail-truncated away;
   - the "RuntimeError: diag-stderr sentinel-fault" exception line
     survived at the TAIL;
   - an elision marker ("bytes elided" or equivalent) is present;
   - the tail does NOT begin mid-line — the first character after the
     head cut is the start of a whole line, not a fragment.
   Any of these failing = FAIL. Report the surfaced stderr verbatim.
3. Control: a trial with SHORT stderr (a few lines) → surfaced
   verbatim, no marker, no truncation. Report it.

PART B — generator path (if reachable)

4. If the VM exercises a data-source/generator path that surfaces
   stderr (prepare_data or equivalent), trigger a long-stderr failure
   there too → same head+tail+marker guarantees. If the path isn't
   reachable through the tools, mark BLOCKED and say why.

PART C — schema docs tell the truth (P11)

5. Read the tool schemas (tools/list or equivalent) and report the
   relevant description lines verbatim:
   - run_trial → `programme_id` must name the `status=active`
     requirement.
   - record_campaign_result → `metrics` must document that the
     primary-metric key is case-sensitive.
   - close_campaign AND record_promotion_verdict → the description
     must document pull-before-close ordering (roster refresh /
     verdict before close, as written).
   - refresh_roster → must define `unrolled` (a rolled-back candidate
     restored by a later promote) — not just rolled_back.
   - propose_meta_change → the docstring must name the modifiable
     component set and the kernel class vocabulary (class-1/2/3).
   A docstring still missing the semantics is a FAIL — the schema is
   the only contract a caller sees. Report each excerpt verbatim.

PART D — honest bookkeeping

6. Report verbatim: the long-stderr trial output (or its surfaced
   truncation), the short-stderr control, each schema description
   excerpt with PASS/FAIL per item.
7. check_invariants on episteme at the end — report the payload;
   acknowledge any violation you caused with an honest disposition.

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools.
- Do NOT edit server configuration mid-run.
- Report every refusal verbatim, and what resolved it.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /srv/lab/exchange/diagnostics-out/stderr-elision-and-schema-docs/
   — report.md (the elided stderr verbatim showing head+marker+tail,
   the short-stderr control, each schema doc excerpt with its
   verdict) plus evidence files (tool result JSON per step).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/stderr-elision-and-schema-docs
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
