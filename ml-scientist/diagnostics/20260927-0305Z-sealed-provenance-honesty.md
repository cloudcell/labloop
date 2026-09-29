You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session verifies the rc-5 PROVENANCE fixes on the sealed-code path —
the rc-5 diagnostic found that `executed_code.json` recorded the hash of
whatever bytes sat at the host path at finalize time, even when the code
that actually ran was served from a sealed overlay. It also found that
directory opens were digested as `input_data` (firing
`input_data_undigested` on every Python trial), that deleting a sealed
file produced an opaque sandbox error, and that `seal_enforced` reported
true on a trial that never ran. All four defects should now be fixed —
prove it, both ways. Marker tag: `diag-sealprov`. Report every refusal
verbatim — errors are data.

Read tool schemas before calling. The Loop-0 chain is: hypothesis →
design → capture_bundle(code_ref=<path>) → run_trial. run_trial's
programme_id parameter is required — first confirm its docstring says
which programme states qualify (rc-5 found it thinly documented).

PART A — happy path: sealed manifest honesty under host tampering

1. Write /tmp/diag-sealprov/train.py — a stub that prints a JSON metric
   line, e.g. {"accuracy": 0.5}. sha256sum it; record as ORIG.
2. Loop-0 chain → capture_bundle with code_ref=/tmp/diag-sealprov/train.py.
3. AFTER capture, edit train.py (append a comment — any byte change).
   sha256sum again; record as TAMPERED. ORIG != TAMPERED must hold.
4. run_trial → expect seal_enforced: true in the run payload. If it is
   false, the VM runs sandbox=none — skip Part A's hash assertions, say
   so loudly, and continue at Part B.
5. Read executed_code.json via trial://{id}/artifacts →
   artifact://{content_hash}. For the train.py entry verify:
   - role is "code" (or the code role the schema documents)
   - sha256 == ORIG (the sealed bytes that ran) — NOT TAMPERED
   - host_sha256 == TAMPERED (the divergence is retained as evidence)
   Before the fix this entry would read TAMPERED — the provenance lie.
   If host_sha256 is absent on a divergent file, that is a FAIL.

PART B — directory classification and undigested-input honesty

6. In the same manifest: verify at least one entry carries
   role: "directory" (the interpreter opens its cwd and stdlib).
   Directory entries must never carry a sha256.
7. check_invariants on episteme → input_data_undigested must NOT fire
   for directory rows or for rows whose reason is IsADirectoryError.
   If it fires only for directories, the fix regressed — FAIL.
8. NEGATIVE — real undigested input: create /tmp/diag-sealprov/input.json;
   write a second trial whose code opens it then deletes it
   (open(...).read() then os.remove). After finalize the manifest row
   must be role: input_data, sha256: null, with a reason naming the
   missing file. input_data_undigested must fire for THIS trial.
9. Acknowledge that violation (acknowledge_violation, check name
   input_data_undigested, ref = the trial id) → then re-run
   check_invariants. The SAME violation row must still appear in the
   payload, now annotated acknowledged: true — acknowledgement must be
   visible, not silent. Run a mutating call (e.g. record_observation
   on a third trial) to prove the gate cleared.

PART C — deleted sealed target: named refusal, not sandbox mystery

10. Write a third code file /tmp/diag-sealprov/gone.py, capture_bundle
    on it, then `rm gone.py` BEFORE run_trial. Launch must fail with a
    NAMED refusal — the payload must carry launch_refused: true and
    seal_enforced: false, and name the missing path. An opaque sandbox
    error ("Read-only file system", mount failure) is a FAIL.
11. check_invariants → unsealed_execution must NOT fire for the refused
    launch — a trial that never ran is not an unsealed execution.
12. NEGATIVE — honest seal flag: for the refused launch, the trial
    record must not claim seal_enforced: true. If the run payload or
    trial record says enforcement held on a never-run trial, FAIL.

PART D — non-regular input honesty (rc-11 freeze regression)

    rc-11: a trial opened /dev/urandom and the finalize-time digest
    streamed it synchronously on the event loop — every episteme port
    listened, nothing answered, ~55 min. Post-fix, non-regular paths a
    trial names must produce sha256: null + a naming reason, and the
    loop must stay live. The health probe after each arm IS the
    assertion — a silent/hung call is the pre-fix signature.

13. Character-device arm: write a trial whose code opens /dev/urandom,
    reads 16 bytes, prints a metric line, exits. run_trial → the call
    must return (pre-fix it never did). In executed_code.json the
    urandom row must read role: input_data, sha256: null, reason naming
    non-regular (e.g. "not a regular file"). Then call any episteme
    tool (e.g. list_programmes) — it must answer promptly. A hang or
    multi-minute silence is a FAIL, and proves the digest bound
    regressed.
14. FIFO arm: a trial whose code does os.mkfifo("/tmp/diag-sealprov/f")
    then os.open(path, os.O_RDONLY | os.O_NONBLOCK) (non-blocking so
    the trial itself doesn't hang waiting for a writer). Same
    assertions: call returns, manifest row is sha256: null + non-regular
    reason, follow-up call answers promptly.
15. input_data_undigested MUST fire for both rows — an undigestable
    consumed input is a real provenance gap; the reason names it. This
    is intended post-fix behavior, not noise.

PART E — vacuous-check honesty

16. check_invariants → sealed_access_attempts. If the VM has no
    sealed_path_patterns configured, its detail must say skipped (or
    carry skipped: true) — a bare green check that examined nothing is
    a FAIL. If patterns ARE configured, the check must fire only on
    real denied-access rows; report which case you observed.
17. If sealed patterns are configured: a trial that attempts to open a
    deny-listed path produces a manifest entry with denied: true —
    verify such an attempt is recorded, not hashed as input_data.

PART F — honest bookkeeping

18. Report verbatim: every manifest JSON, every check_invariants
    payload, every refusal. For each entry keep the full JSON — do not
    trim to excerpts.
19. If any step could not be reached — say which and why; a skipped
    step must be louder than a passing one.

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools.
- Do NOT edit server configuration mid-run.
- Report every refusal verbatim, and what resolved it.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /srv/lab/exchange/diagnostics-out/sealed-provenance-honesty/
   — report.md (the full evidence chain: ORIG/TAMPERED hashes, the
   manifest entries, the launch refusal, every check payload, every
   verdict) plus evidence files (tool result JSON per step, the
   sha256sum outputs).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/sealed-provenance-honesty
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
