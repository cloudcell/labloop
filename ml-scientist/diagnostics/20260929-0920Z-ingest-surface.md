You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session covers a surface no battery prompt has ever touched:
the **episteme artifact-ingest API on :38082** — a token-gated HTTP
write path (`POST /artifacts`, `HEAD`/`GET /artifacts/{hash}`,
`GET /health`) that exists so remote executors can push bytes into
the store. The coverage audit found it has never been exercised —
not even the fail-closed 401.

Marker tag: `diag-ingest`. Report every refusal verbatim — errors are
data. You have NO configured ingest token — the lab runs with the
default. That makes the fail-closed checks the whole point of this
session: a write path with no provisioned credential must refuse
everything, not fail open.

PART A — fail-closed probes (the load-bearing part)

1. `curl -i http://127.0.0.1:38082/health` → report verbatim
   (expected 200 — health is unauthenticated by design).
2. `curl -i -X POST http://127.0.0.1:38082/artifacts
   -d 'diag-ingest-bytes'` → expect **401**. A 2xx is a critical
   FAIL — the write path accepted bytes with no credential.
3. `curl -i -X POST http://127.0.0.1:38082/artifacts -H
   'Authorization: Bearer wrong-token-value' -d 'x'` → 401. Report
   verbatim.
4. `curl -i http://127.0.0.1:38082/artifacts/<64-hex-of-your-choice>`
   → 401 (GET is gated too). Same for `curl -I` (HEAD). Report all
   verbatim.
5. `curl -i http://127.0.0.1:38082/nonexistent` → report the
   verbatim status (404 or 401 — record which; do not assume).

PART B — reachability honesty

6. If :38082 is not listening at all (connection refused): that is
   also a designed state — the ingest surface is off unless
   `--ingest-port` was given. Report `connection refused` verbatim
   and mark Parts A's probes UNREACHABLE, not passed — a refused
   connection is not a tested 401. State clearly which outcome you
   observed.
7. The template DOES provision a token — at
   /srv/lab/mcp-state/ingest.env (mcp-side, mode 600, env-file for
   the quadlet). It is deliberately outside your zone: do NOT try to
   read mcp-state. The success path needs the OPERATOR to hand you
   the token value through the exchange — ask for it in your
   report's "blocked" section if absent. With a token: repeat Part
   A's POST → 200 + the sha256 content address; then GET and HEAD
   that hash → the bytes round-trip. Report verbatim. Without one
   this step is BLOCKED (token exists but lives in the mcp zone —
   provisioning reachability is the gap, not the endpoint) — say
   exactly that.

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools and the ingest API.
- Do NOT edit server configuration mid-run, and do NOT try to
  provision a token yourself — the point is the default posture.
- Report every refusal verbatim, and what resolved it.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /srv/lab/exchange/diagnostics-out/ingest-surface/
   — report.md (every curl response verbatim — status line + body,
   the explicit UNREACHABLE-vs-tested-401 statement, the token
   disposition) plus evidence files.
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/ingest-surface
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
