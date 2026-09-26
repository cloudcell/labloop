You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

Task: run one complete Loop-0 experiment on a trivial problem — e.g.
"does batch size 32 vs 64 change accuracy on a synthetic linear dataset?"
— programme → hypothesis → prepare_data → design_experiment →
capture_bundle → run_trial → record_observation → conclude_hypothesis.
Then close the programme.

While you work:
- Do NOT read any status resource before your first mutating call —
  I want to see what happens.
- When a tool call refuses, read the error and do exactly what it says.
- Report every refusal you hit verbatim, and what resolved it.

DELIVERABLE — produce an exportable artifact. From the repo root:
1. Write your findings into diagnostics/out/recurrent-protocol-smoke/ —
   report.md (narrative: what you did, every refusal verbatim, what
   resolved it) plus any evidence files (JSON snapshots, counts).
2. Run: ./labloop export recurrent-protocol-smoke
3. Report the printed tarball path, byte count and sha256 verbatim.
   The tarball is retrieved off the VM by a script — the run is not
   complete until the export succeeds.
