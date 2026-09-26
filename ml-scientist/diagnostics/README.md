# diagnostics/

Operational prompt-inbox for lab VM diagnostic runs. Not governed
documentation — these are *inputs* to the VM agent, not outputs of the
lab. Filenames: `<UTC-timestamp>-<slug>.md` (same convention as docs/).

## The contract

Every diagnostic prompt ends with a DELIVERABLE section requiring the
agent to:

1. Write its findings into `diagnostics/out/<slug>/` — `report.md`
   plus evidence files (verbatim tool results, JSON snapshots,
   computed counts). The slug is the filename minus timestamp and
   `.md`.
2. Run `./labloop export <slug>` from the repo root.
3. Report the printed tarball path, byte count and sha256 verbatim.

`labloop export` packages `diagnostics/out/<slug>/` (the payload) plus
auto-collected server state — per-server `/health` + `/health/deep`
snapshots, integrity `check-*.jsonl` trails, server-log tails, a
MANIFEST — into `sxport/<UTC>-<slug>.tar.gz`. A run is not complete
until the export succeeds.

## Retrieval

From the host:

```bash
scp vm:ml-scientist/sxport/<UTC>-<slug>.tar.gz .
tar -tzf ...   # payload/ is the agent's report; the rest is server state
```

`diagnostics/out/` and `sxport/` are run artifacts — gitignored.
