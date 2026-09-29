# val-grounding — provenance corpus for decision constants

Reference corpus grounding the grounded-constants registry
(`constants/grounded_constants.py` + per-package mirrors). Every constant
consumed by a scoring, confidence, threshold, or promotion computation cites
a source here — see `docs/e-plans/plan-20260929-1514Z--grounded-decision-constants.md`.

## Provenance

Produced inside the lab VM by the driving agent (opencode), extracted
2026-09-29 (`lab-vm-rc-11-extraction-20260929T1505Z`), ingested here
byte-identical to the extraction's `ref-papers/` — sha256-verified.

The audit's own verification convention was stronger than filename trust:
every PDF's actual text was read before citation. That pass caught five
citation errors in the audit's drafts, including two files that were entirely
the wrong paper and XMP metadata that was wrong. Treat filenames as labels;
the sha256 table in `MANIFEST.md` is the integrity record.

## Contents

- `NN-*.pdf` — the 11 archived source papers (see `MANIFEST.md` for the
  per-file citation, version status, and verification method).
- `MANIFEST.md` — the audit's manifest plus the recorded sha256 block.
- `GROUNDING-PROPOSAL.md`, `grounding-proposal.pdf/.tex` — the audit of all
  15 decision constants (per-constant grounding status and proposed
  dispositions); the registry's primary spec.
- `paper.pdf/.tex`, `flow.pdf/.png`, `make_flow.py` — the companion audit
  paper (*Provisional Constants*) and its figure, kept for provenance.
- `nap-table-d1-transcription.md`,
  `gleser-hwang-transcription.md` — authored transcriptions of the two
  load-bearing sources the audit could not archive (NAP Table D-1;
  Gleser–Hwang via the Axioms survey). Each records its provenance chain and
  — for Gleser–Hwang — a citation discrepancy that needs resolving against
  the original.

## Rule of use

A citation here is only honest when the source *determines* the value or the
procedure — not merely contains the number somewhere. `IN-RANGE` (a tabulated
family that includes our value) is reported separately from `SOURCED` for
that reason; see the proposal's §2 for the status vocabulary and the plan for
the registry schema.
