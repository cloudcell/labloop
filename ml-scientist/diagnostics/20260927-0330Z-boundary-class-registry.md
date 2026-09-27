You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session verifies the rc-5 DISCOVERABILITY fix on arete — rc-5's
state-machine diagnostic reported "rejected is unreachable: the kernel
classifies from a registry no tool exposes, so no agent can name an
immutable component on purpose." The registry was real (18 immutable
names); what was missing was any way for an agent to learn the names.
The fix put the recognized class-1/2 names into `propose_meta_change`'s
docstring. Prove: the names are discoverable, the kernel classifies
authoritatively (a proposer LYING about a class still gets rejected),
and `rejected` is genuinely reachable. Marker tag: `diag-registry`.
Report every refusal verbatim — errors are data.

Read tool schemas before calling. Arete's proposal surface:
register an improver, then propose_meta_change(proposer_improver_id,
spec_delta, class_map={component: declared_class}, ...).

PART A — discoverability

1. Read the propose_meta_change schema/docstring. It must name the
   recognized immutable components (audit_log, provenance,
   enforcement_kernel, promotion_protocol, ...) and conditional ones
   (evaluator, scheduler, memory_schema, ...). If the description is
   still opaque about which names classify as what, FAIL — that was
   the defect.
2. Arete: register an improver parent (register_improver or
   equivalent — read the tool list) so you hold a valid
   proposer_improver_id.

PART B — NEGATIVE: rejected is reachable, and lying doesn't help

3. propose_meta_change with class_map {"audit_log": "modifiable"} —
   declare it modifiable on purpose. The kernel ignores the declared
   class; the component is registry-immutable → status MUST be
   "rejected" with a rejection_reason naming audit_log and the
   class-1 rule. Report verbatim. An "admitted" or "conditional"
   outcome here is a FAIL (the declared class would have been
   trusted — the exact spoof the kernel-classifies rule prevents).
4. List/read the proposal → the rejected row is durably stored with
   its reason. Rejection is a record, not just a refusal.
5. propose_meta_change with class_map {"enforcement_kernel":
   "modifiable"} → also rejected. Two rejections, both verbatim.

PART C — happy path: conditional and modifiable

6. class_map {"evaluator": "modifiable"} → evaluator is class-2
   conditional → status "conditional" (the declaration is again
   overridden — registry wins). Report verbatim.
7. class_map {"some_novel_widget": "modifiable"} → unknown name →
   status "conditional", NOT admitted — unknown defaults to
   conditional, never silently writable.
8. class_map {"prompts": "modifiable"} → modifiable registry member
   → status "admitted". The happy path must reach admitted.

PART D — NEGATIVE: shape refusals

9. propose_meta_change missing falsification or rollback_plan →
   refused at field check, verbatim.
10. propose_meta_change with a proposer_improver_id that doesn't exist
    → refused. Verbatim.

PART E — honest bookkeeping

11. Report verbatim: the schema text you read, every proposal request
    + response (both statuses and all rejection_reasons), the stored
    proposal records.
12. check_invariants on arete at the end — report the payload;
    acknowledge any violation you caused with an honest disposition.
13. If any step could not be reached — say which and why; a skipped
    step must be louder than a passing one.

While you work:
- When a tool call refuses, read the error and do exactly what it says.
- Do NOT attempt to read /srv/lab/mcp-state or the state databases
  directly — everything goes through MCP tools.
- Do NOT edit server configuration mid-run.
- Report every refusal verbatim, and what resolved it.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /srv/lab/exchange/diagnostics-out/boundary-class-registry/
   — report.md (the docstring evidence, every proposal's stored
   status + reason verbatim, per-step verdicts) plus evidence files
   (tool result JSON per step).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/boundary-class-registry
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
   The export is pulled off the VM by a host script — the run is not
   complete until the export succeeds.
