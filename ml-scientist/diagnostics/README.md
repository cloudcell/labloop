# diagnostics/

Operational prompt-inbox for lab VM diagnostic runs. Not governed
documentation — these are *inputs* to the VM agent, not outputs of the
lab. Filenames: `<UTC-timestamp>-<slug>.md` (same convention as docs/).

## The contract

Every diagnostic prompt ends with a DELIVERABLE section requiring the
agent to:

1. Write its findings into `/srv/lab/exchange/diagnostics-out/<slug>/`
   — `report.md` plus evidence files (verbatim tool results, JSON
   snapshots, computed counts). The slug is the filename minus
   timestamp and `.md`. **`labloop-export` only accepts paths under
   `/srv/lab/exchange/`** (path mode is pinned there) — stage the out
   dir directly under the exchange root, not the workspace — rc-5
   found every prompt staging under the agent's home workspace, which
   the export refuses; the path is pinned for that reason, not by
   convention.
2. Stage the deliverable for host retrieval:

   ```bash
   labloop-export /srv/lab/exchange/diagnostics-out/<slug>
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

## Inventory

| Prompt | Verifies |
| --- | --- |
| `20260926-0552Z-recurrent-protocol-smoke` | End-to-end Loop-0/Loop-1 cycle smoke test |
| `20260926-0624Z-blob-retrieval-and-input-digests` | Blob retrieval, input-data digesting |
| `20260926-0713Z-state-machine-coverage` | Lifecycle state-machine coverage across servers |
| `20260926-2056Z-sealed-path-runtime-deny` | Sealed-path runtime deny-list semantics |
| `20260927-0004Z-write-boundary-gates` | Write-boundary gates (rc-4 fixes) |
| `20260927-0005Z-connectivity-fault-observability` | Connectivity fault observability (rc-4) |
| `20260927-0006Z-campaign-orchestration` | Orchestrated campaign/spawn lifecycle (rc-4) |
| `20260927-0305Z-sealed-provenance-honesty` | rc-5: sealed-manifest hash honesty, `host_sha256`, deleted-target launch refusal, directory role, `input_data_undigested` exemption + ack annotation, vacuous-check `skipped` |
| `20260927-0310Z-campaign-metric-write-gate` | rc-5: `record_campaign_result` primary-metric write gate |
| `20260927-0315Z-roster-rollback-derivation` | rc-5: `rolled_back` derivation, dry-run preview, latest-verdict-wins un-roll |
| `20260927-0320Z-connectivity-live-gating` | rc-5: `channels` observability array ×5, live-over-logged gating, `channel:` ack refs |
| `20260927-0325Z-trial-terminal-precedence` | rc-5: persisted-terminal status precedence, `mark_retryable` cancellation, `prepare_data` stderr surfacing |
| `20260927-0330Z-boundary-class-registry` | rc-5: boundary-class registry discoverability, kernel authoritative classification, `rejected` reachability |

The `20260927-03xxZ` set is the rc-5 verification battery — each
targets one rc-5 fix and exercises both the valid path and the
negative/refused path the fix introduces.
