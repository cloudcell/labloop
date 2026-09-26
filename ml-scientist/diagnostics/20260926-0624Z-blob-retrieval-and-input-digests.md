You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session exercises two new episteme capabilities: blob read-back by
digest (`get_blob`) and input-data digests in the execution manifest
(`executed_code.json`). Report everything verbatim — errors are data.

PART A — blob retrieval
1. Find a completed trial with captured artifacts (get_trial /
   describe_blob on any digest it reports). Call
   get_blob(content_hash=<digest>) on it. Decode content_b64, recompute
   sha256 over the decoded bytes, and confirm it equals the requested
   digest. Report {digest, size_bytes, served_from, recomputed_digest}.
2. NEGATIVE: call get_blob on a well-formed but fabricated digest
   (e.g. sha256: followed by 64 zeros). Confirm it returns an error —
   not empty bytes, not success. Report the error verbatim.
3. Call get_blob with a small max_bytes (e.g. 16) against a larger blob.
   Confirm it returns too_large with metadata but NO content_b64 field.
   Report what came back.

PART B — input-data digests
4. Write a small data file into the trial workspace (e.g.
   /exchange/<something>/probe-input.json — use a path the executor can
   reach). Run one complete Loop-0 experiment whose trial code opens and
   reads that file: programme → hypothesis → prepare_data →
   design_experiment → capture_bundle → run_trial → record_observation
   → conclude_hypothesis.
5. After the trial completes, fetch the trial's executed_code.json (via
   get_trial / describe_blob → get_blob). Assert the manifest contains
   an entry for your input file with role=input_data and a non-null
   sha256. Recompute the file's digest yourself and compare. Report the
   manifest entry verbatim.
6. Inspect the same manifest: confirm the read-trace artifact
   (*_readtrace.strace) and executed_code.json itself are pinned in the
   bundle manifest with digests — not just served.
7. NEGATIVE: call check_invariants and report whether the
   input_data_undigested check exists and its status. If your trial's
   input file somehow lacks a digest, that check MUST fire — report the
   violation verbatim if so. A passing check that examined nothing is
   not a pass — report how many manifests it actually inspected.
8. CONDITIONAL NEGATIVE: if the executor's config defines
   sealed_path_patterns, run a trial whose code opens a matching path.
   The manifest must record it as role=sealed with sha256=null and a
   nonempty reason — and no digest of that path may appear anywhere.
   If no patterns are configured, say so and skip.

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools.
- Report every refusal you hit verbatim, and what resolved it.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /home/lab/workspace/diagnostics-out/blob-retrieval-and-input-digests/
   — report.md (every get_blob result verbatim: digest, size_bytes,
   served_from, your independently recomputed digest; the manifest
   entries verbatim; the check_invariants result) plus evidence files
   (the decoded blob bytes, the manifest JSON).
2. Stage it for host retrieval:
      labloop-export /home/lab/workspace/diagnostics-out/blob-retrieval-and-input-digests
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
