You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session exercises the sealed-path runtime deny — [executor]
sealed_path_patterns is a deny-list that must now be PHYSICALLY
enforced inside the trial's mount namespace, not merely recorded after
the fact. Holdout data must be unreadable while the run happens, the
attempt must still be recorded, and an integrity check must surface it.
Report everything verbatim — errors are data.

PRECONDITION DETECTION — this run has three possible outcomes:
  A) sealed_path_patterns configured + sealed_enforcement="deny"
     → the full deny battery runs (Parts B–D).
  B) patterns configured + sealed_enforcement="audit"
     → trace-only mode: opens succeed, exclusions recorded (Part B
       reports which, and the runtime-deny steps are expected NOT to
       deny — say so explicitly).
  C) patterns NOT configured → sealed_enforcement reports "none".
     The deny machinery is dormant: skip Parts B–D, run only Part E
     (code-seal overlay) and Part F, and report the config gap as the
     finding — an unconfigured deny-list IS the result.

How to tell: Part A produces executor_output; it carries
sealed_enforcement ("deny"|"audit"|"none"), sealed_denies (armed
paths), and sealed_unmatched (patterns that armed nothing). Report
those three fields verbatim — they are the ground truth of which mode
is live.

PART A — setup + a control trial

1. Create a test layout under /exchange (a path the executor can
   reach — use /exchange/diag-seal/ as the root):
     /exchange/diag-seal/holdout.csv        contents: "HOLDOUT-DATA-A"
     /exchange/diag-seal/sub/holdout.csv    contents: "HOLDOUT-DATA-B"
     /exchange/diag-seal/train.csv          contents: "TRAIN-DATA"
     /exchange/diag-seal/link.csv           symlink → holdout.csv
   If /exchange is not writable, pick another writable location the
   executor can see and say where — but check FIRST whether the
   configured sealed_path_patterns actually cover that location
   (mismatched coverage = outcome C).
2. Drive one complete Loop-0 experiment whose trial code opens and
   prints TRAIN-DATA's contents: programme → hypothesis →
   prepare_data → design_experiment → capture_bundle → run_trial →
   record_observation → conclude_hypothesis. Name every entity with a
   "diag-seal" marker.
3. Fetch the trial record (get_trial) and report executor_output's
   sealed_enforcement, sealed_denies, sealed_unmatched, sandbox, and
   seal_enforced verbatim. This is the precondition verdict.
4. Fetch executed_code.json (get_trial → the artifact digest →
   get_blob): confirm train.csv is recorded role=input_data with a
   non-null sha256 — the deny-list is NOT an allow-list; permitted
   reads must still work and still be digested.

PART B — denied reads (runs only under outcome A)

5. Run a trial whose code does open("/exchange/diag-seal/holdout.csv")
   and prints what it gets. The open MUST fail — report the exception
   class verbatim (PermissionError/EACCES for a file, or
   FileNotFoundError/ENOENT under a dir-level deny). If the contents
   print, the deny failed — report the leaked bytes verbatim as the
   primary finding.
6. Repeat for the NESTED path /exchange/diag-seal/sub/holdout.csv —
   a single-level wildcard must still deny it (fnmatch '*' crosses
   '/'). A nested match that reads is a critical bypass — report it.
7. Repeat through the SYMLINK /exchange/diag-seal/link.csv — the
   alias must be denied exactly as its target is.
8. In the same or another trial, try to defeat the deny:
   os.remove() on the path, os.rename() it aside, and
   open(os.open(path, os.O_RDONLY)) — every variant must fail.
   Report each result verbatim.
9. After each denying trial, fetch its executed_code.json: the sealed
   path must appear as role=sealed, sha256=null, denied=true, reason
   naming the errno — the ATTEMPT is recorded even though nothing was
   read. Confirm no sha256 of the holdout bytes appears anywhere in
   the manifest or artifacts.

PART C — the integrity surface (runs under A or B)

10. Call check_invariants. The sealed_access_attempts check must exist;
    after Part B it must flag each denying trial (under B it flags the
    exclusion rows). Report the check's name, status and violation
    entries verbatim — including how many manifests were examined (a
    passing check that examined nothing is not a pass).
11. While the violation is open, attempt any mutating tool (e.g.
    design_experiment on a fresh hypothesis) — the violations gate
    should refuse until the finding is acknowledged. Report the
    refusal verbatim.
12. acknowledge_violation on sealed_access_attempts with disposition
    "diagnostic run — expected attempt" and decided_by
    "agent:diagnostics". Confirm the gate clears (the mutating call
    now succeeds).

PART D — audit-mode contrast (runs only under outcome B)

13. Same opens as Part B — under audit they SUCCEED. Report the read
    bytes verbatim, and confirm the manifest still records
    role=sealed with sha256=null + reason "excluded by policy" and NO
    denied flag. Then run Part C — the check fires on exclusions too.

PART E — code-seal overlay (runs under every outcome)

14. capture_bundle seals the trial's code by digest. Prove the
    overlay is physical: after capture_bundle, EDIT the live source
    file the bundle was built from (append a print("TAMPERED") or
    corrupt it outright), then run_trial. The trial must execute the
    SEALED bytes — its stdout must not contain TAMPERED. Report the
    executor_output.seal_enforced and sealed_overlays fields and the
    stdout verbatim.
15. Delete the live source file entirely and run again — sealed
    execution must still succeed (staged bytes are mounted over the
    missing path). If it fails, report the error verbatim — the seal
    claims to cover deletion.
16. check_invariants → strace_divergence must NOT fire for the
    tampered runs (the executed code WAS the sealed bundle). Report
    the check result.

PART F — honest bookkeeping

17. Report every executor_output field verbatim for each trial:
    sandbox, seal_enforced, sealed_overlays, sealed_enforcement,
    sealed_denies, sealed_unmatched, read_trace. Any field absent or
    misleading is a finding.
18. If any step was skipped because of outcome B or C, say which and
    why — a skipped step must be louder than a passing one.

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools.
- Do NOT edit server configuration mid-run — if patterns are missing
  or mismatched, that is the reportable outcome, not something to fix.
- Report every refusal verbatim, and what resolved it.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /home/lab/workspace/diagnostics-out/sealed-path-runtime-deny/
   — report.md (the precondition verdict; every open() result verbatim
   with its errno; the manifest entries verbatim; executor_output fields
   for every trial; the check_invariants payload; the ack discharge;
   every refusal) plus evidence files (the manifest JSON, the executor
   output JSON).
2. Stage it for host retrieval:
      labloop-export /home/lab/workspace/diagnostics-out/sealed-path-runtime-deny
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
