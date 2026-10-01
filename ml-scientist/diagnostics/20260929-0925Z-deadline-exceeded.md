You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session verifies the lab's **response-deadline surface** as it
actually exists on this build. There are two distinct knobs:

- `[server] tool_deadline_seconds` (default 120 s) — a per-call
  RESPONSE bound implemented as an `asyncio.wait_for` shielded wrapper
  around every tool. On expiry the client gets a named error
  (`deadline_exceeded`) while the handler keeps running server-side.
  NOTE: with `run_trial` dispatch-first-async (returns `running` in
  ~10 s), NO reachable tool call outlives 120 s on a healthy lab —
  this deadline is only observable under a wedge. Do not fake it.
- `wait_trial`'s server-side cap — `timeout_seconds` clamps to 60 s.
  This one IS reachable, and since rc-15 the response echoes
  `timeout_seconds_requested` / `timeout_seconds_applied` so a caller
  can tell "waited as asked" from "granted less".
- `run_trial` discloses `submit_wait_seconds` — the ~10 s settle
  window that used to exist only in the tool's description prose.
- Per-channel upstream deadline — each channel row in the digests
  now carries `call_timeout_seconds` (default 30 s).

Marker tag: `diag-deadline`. Report every refusal verbatim — errors
are data.

PART A — fingerprint + disclosure

1. Read `protocol://status` on episteme. Record
   `tool_deadline_seconds` verbatim — the configured per-call
   deadline is now a digest field (previously only a startup-banner
   line). Also record `upstream_summary.channels[].call_timeout_seconds`
   for every channel — the per-channel upstream deadline must be
   disclosed as a number, not absent. If either field is missing or
   null, the disclosure regressed — FAIL.
2. Cross-check `lab://status` on agora — its `tool_deadline_seconds`
   and each upstream digest's value should all appear. Report the
   per-server values verbatim.

PART B — the reachable deadline: wait_trial's clamp

3. Episteme: create_programme → formulate_hypothesis →
   design_experiment. Write a stub `/tmp/diag-deadline/slow.py`
   exposing `def run_training(config):` with
   `import time; time.sleep(90); return {"metrics": {"accuracy":
   0.5}, "variance": {}}`. capture_bundle → run_trial.
4. Record `run_trial`'s response verbatim — it must carry
   `submit_wait_seconds` as a field (the settle cost, previously
   prose-only; absence is a FAIL) and return `status: "running"`
   around that many seconds after the call.
5. While the trial runs: `wait_trial` with
   `timeout_seconds: 150` (above the 60 s cap) — the response must
   come back near ~60 s with `timed_out: true`, `status: "running"`,
   `timeout_seconds_requested: 150`,
   `timeout_seconds_applied: 60`. A silent clamp (response at 60 s
   but no requested/applied echo, or applied ≠ 60) is a FAIL —
   a polling client cannot tell "waited as asked" from "granted
   less" without the echo. Record the elapsed wall time.
6. Repeat `wait_trial` with `timeout_seconds: 5` — it must return
   ~5 s, `timed_out: true`, requested 5 / applied 5 (no clamp when
   under the cap). Then keep polling until the stub finishes (~95 s
   total) and confirm `status: "completed"` — the trial continued
   server-side past every response bound; nothing was aborted by a
   client-facing limit.

PART C — the unreachable deadline, classified honestly

7. `tool_deadline_seconds` (120 s) cannot be crossed by any reachable
   call on a healthy lab — `run_trial` is async, `wait_trial` clamps
   at 60 s. State that plainly: the wrapper's existence is disclosed
   by the digest field (step 1); its firing branch is unverifiable
   without wedging a tool — classify this clause UNREACHABLE-BY-
   DESIGN, not PASS, and note that the shield semantics are instead
   proven by part B (the trial completing past response bounds).

PART D — boundary honesty

8. If the lab's `tool_deadline_seconds` differs from 120 s (shorter —
   reachable; longer — even less reachable), adapt and report what
   you OBSERVED, not the default you assumed.

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools.
- Do NOT edit server configuration mid-run.
- Report every refusal verbatim, and what resolved it.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /srv/lab/exchange/diagnostics-out/deadline-exceeded/
   — report.md (the digest's deadline fields verbatim, both
   wait_trial payloads with elapsed times, run_trial's
   submit_wait_seconds, the trial's final terminal record, and the
   UNREACHABLE-BY-DESIGN classification for the 120 s wrapper)
   plus evidence files (tool result JSON per step).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/deadline-exceeded
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
