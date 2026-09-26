# diagnostics/

Operational prompt-inbox for lab VM diagnostic runs. Not governed
documentation — these are *inputs* to the VM agent, not outputs of the
lab. Filenames: `<UTC-timestamp>-<slug>.md` (same convention as docs/).

## The contract

Every diagnostic prompt ends with a DELIVERABLE section requiring the
agent to:

1. Write its findings into `/home/lab/workspace/diagnostics-out/<slug>/`
   — `report.md` plus evidence files (verbatim tool results, JSON
   snapshots, computed counts). The slug is the filename minus
   timestamp and `.md`.
2. Stage the deliverable for host retrieval:

   ```bash
   labloop-export /home/lab/workspace/diagnostics-out/<slug>
   ```

3. Report the staged export path, byte count and sha256 verbatim.

`labloop-export` (installed at `/usr/local/sbin/labloop-export`,
nopasswd-free for `lab`) freezes the staged set into a manifest +
`export.tar.gz` under `/var/lib/labloop-export/user/`. A run is not
complete until the export succeeds.

**There is no `./labloop` in the VM.** `labloop` is the host-side repo
launcher (manages `~/.ml-*` server processes on the operator's
machine; its `export` subcommand packages host-side `diagnostics/out/`
+ server state into `sxport/`). In the guest the servers live in the
`lab-cnt-mcp` container and the only sanctioned egress is
`labloop-export` — do not look for `./labloop`, and do not treat its
absence as a blocker worth escalating.

**Fallback**: only if `labloop-export` itself is missing (VM predates
it) does the agent emulate it rather than stalling — tar.gz the out
dir into `/srv/lab/exchange/`, `sha256sum` it, and report path +
bytes + digest labelled *substitute for `labloop-export`*.

## Retrieval

From the host:

```bash
./90-extract-lab-data.sh <vm> <dest-dir>
# lands <dest>/<vm>-extraction-<UTC>.tar.gz — sha256-verified.
# Unpack deliberately: the payload is agent-produced, treat as untrusted.
```

`diagnostics-out/` is a run artifact — gitignored.
