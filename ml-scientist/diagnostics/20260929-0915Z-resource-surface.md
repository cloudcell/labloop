You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session sweeps the **resource surface** — the `read_resource`
URIs the coverage audit found no prompt has ever read. Prior bugs in
this surface (graph node/edge field names, escaped metadata) shipped
and went unnoticed for a whole battery precisely because nothing read
them. Assert field SHAPES, not just that a payload returns.

Marker tag: `diag-resources`. Report every refusal verbatim — errors
are data.

PART A — session + graph resources on all five servers

1. read_resource on each of: protocol://session, protocol://graph,
   search://session, search://graph, improver://session,
   improver://graph, claims://session, claims://graph,
   lab://status, lab://topology. Record each payload verbatim.
   For every *://graph: assert the node list and edge list field
   names — edges must carry `kind`, `src`, `dst` (the agora
   projection bug was `e.type`/`e.kind` drift); report the exact
   key sets you see.
2. claims://by-relation/{relation} — read with a relation that
   exists in the claim graph (e.g. 'tested_by' or 'supports' —
   list_claims first to find one) AND with a relation that has no
   edges → report both payloads verbatim (empty is a legal answer,
   an error is not).

PART B — episteme programme resources

3. programme://{id}, programme://{id}/belief,
   programme://{id}/hypotheses, programme://{id}/trials,
   programme://{id}/budget on a live programme id (list_programmes
   first) → each payload verbatim; assert each is well-formed JSON
   with the documented fields.
4. trial://{trial_id}/artifacts on a trial that has captured
   artifacts → the manifest verbatim. On a trial with none → report
   what the resource returns for an empty set.
5. artifact://{content_hash} — read a real artifact hash (from step
   4's manifest) → verbatim.
6. code://{code_hash} — read the sha256 code hash from a prior
   capture_bundle response → verbatim.
7. dataref://{data_ref_id} — read a DataRef id from prepare_data →
   verbatim.
8. executor://contract → verbatim; assert it names the sandbox
   contract fields the run_trial schema references.

PART C — the negative surface

9. read_resource a URI with a valid scheme but a nonexistent id —
   e.g. programme://prog-deadbeef → report verbatim how the server
   reports a miss (empty payload, error, or structured 'not found'
   — any is acceptable, but record WHICH, verbatim).
10. read_resource a URI for a scheme the server does not own —
    e.g. claims://status on episteme, or protocol://status on
    anamnesis → report verbatim.
11. agora's read_resource acts as the router for peer URIs — read
    claims://status and protocol://status THROUGH agora → report
    whether the hub routes them or refuses (either is a designed
    answer; record which).

PART D — honest bookkeeping

12. check_invariants on every server at the end — report payloads;
    a pure-read session should produce zero violations of your own.

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools.
- Do NOT edit server configuration mid-run.
- Report every refusal verbatim, and what resolved it.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /srv/lab/exchange/diagnostics-out/resource-surface/
   — report.md (per-URI: payload or refusal verbatim, the exact
   top-level key set, any field-name anomalies) plus evidence files
   (one JSON per resource read).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/resource-surface
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
