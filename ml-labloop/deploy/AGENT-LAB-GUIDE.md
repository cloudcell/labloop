# AGENT-LAB-GUIDE — orientation for agents working inside a lab VM

You are running inside a **labloop VM** — an isolated KVM guest built
for ML experimentation. Untrusted/generated code is *expected* to run
here; the security model below is what keeps it safe. Read this before
touching anything.

> **REQUIRED READING:** `SECURITY-MANUAL.md` (same directory — also
> symlinked into the workspace). It is the binding contract: the threat
> model, the zone rules, and the **four install lanes** (uv / micromamba
> + `/opt/local` / `labloop-build` / `tlmgr --usermode`). If this guide
> and the manual ever disagree, the manual wins.
>
> **FIRST RUN?** `GENESIS-RESEARCH-PROMPT.md` (same directory) is the
> warm-up: one complete programme → hypothesis → trial → observation →
> belief → verdict → claim cycle that exercises every MCP service and
> the zone rules before real work starts.

## The three zones

| Zone      | User  | Container     | Network       | What lives here                        |
| --------- | ----- | ------------- | ------------- | -------------------------------------- |
| `driver`  | `lab` | (host procs)  | —             | you, VSCodium, opencode, authoring     |
| `trusted` | `mcp` | `lab-cnt-mcp` | `lab-net-mcp` | five MCP servers + scientific state    |
| `hostile` | `exp` | `lab-cnt-exp` | `lab-net-exp` | untrusted experiment code              |

The hostile zone is intentionally cut off: no route to the MCP
servers, the VM gateway, or the host. Never try to "fix" that — the
isolation is the product.

## Naming (sa-02)

```text
lab-img-mcp / lab-img-exp    podman images
lab-cnt-mcp / lab-cnt-exp    containers (= quadlet unit names)
lab-net-mcp / lab-net-exp    podman networks (172.31.0.0/24 · 172.30.0.0/24)
lab-vm-<owner>               user clones · lab-vm-tmp-N = throwaways
lab-template-v1              the golden image
snp-vN                       snapshots
lab-<role>-<identifier>      the grammar — volatile part is the suffix
```

## The filesystem that matters

```text
/srv/lab/workspace     runtime payload (deploy/, containers/) — rw for
                       lab, READ-ONLY inside the hostile container
/srv/lab/exchange      THE handoff: hostile writes artifacts, lab reads
/srv/lab/incoming      ingested datasets — UTC-timestamped batches,
                       lab-owned, READ-ONLY in the hostile container
                       at /incoming (visible there after next restart)
/srv/lab/experiments   exp-owned persistent experiment state
/srv/lab/mcp-state     trusted MCP state — owned by the `mcp` service
                       account, mode 700. NOT readable from `lab` —
                       this is deliberate: state changes go through the
                       MCP tools, never through direct file edits
/home/lab/workspace/experiments   YOUR authoring/review scratch space
/home/lab/Desktop/check-lab-ready.sh   one-click readiness check
```

## Working layout — where to put things

opencode launches with cwd `/home/lab/workspace`. All pre-execution
material lives there — one folder per investigation or experiment:

```text
/home/lab/workspace/
    experiments/<name>/    drafts, configs, notes, papers, code being
                           written — everything BEFORE it runs
```

Nothing in `/home/lab/workspace` is visible to the hostile container.
The lifecycle is:

1. **Draft** under `/home/lab/workspace/experiments/<name>/` — write
   code, collect papers, iterate freely.
2. **Promote** to `/srv/lab/experiments/<name>/` (container `/experiments`)
   or `/srv/lab/exchange` (container `/exchange`) when ready to run.
3. **Execute** via `labloop-exec`; collect outputs from `/exchange`,
   review them back in `/home/lab/workspace`.

Never edit or review files *inside* the hostile mounts expecting them
to stay private — once promoted, hostile code can see them.

## Driving the hostile zone

You cannot `podman exec` into `lab-cnt-exp` as `lab` — the only
sanctioned channel is:

```bash
sudo -u exp /usr/local/sbin/labloop-exec <cmd...>
```

Put experiment code where the container can see it (`/exchange` or
`/experiments` — they map to `/srv/lab/exchange` and
`/srv/lab/experiments` on the host side). `/tmp` inside the container
is tmpfs+noexec and dies with the run — anything that must survive
goes to `/exchange` or `/experiments` before the run ends.

Artifacts that come back through `/exchange` are still untrusted —
inspect before executing.

## MCP services (loopback only, driver-reachable)

| Server       | MCP                  | GUI   | Role               |
| ------------ | -------------------- | ----- | ------------------ |
| `agora`      | `127.0.0.1:38050/mcp`| 38051 | status hub / topology |
| `arete`      | `127.0.0.1:38060/mcp`| 38061 | optimizer          |
| `zetesis`    | `127.0.0.1:38070/mcp`| 38071 | research/search    |
| `episteme`   | `127.0.0.1:38080/mcp`| 38081 | executor + state   |
| `anamnesis`  | `127.0.0.1:38090/mcp`| 38091 | claims/memory      |

Episteme ingest surface: `127.0.0.1:38082` (token-gated; token lives in
`/srv/lab/mcp-state/ingest.env` — which you cannot read, by design).

Agent configs already seeded: `~/.config/opencode/opencode.json` (MCP
servers) and `opencode.jsonc` (local `vllm/*` provider — host vLLM via
the libvirt gateway; models auto-discover), plus Zoo Code settings under
`~/.config/VSCodium/User/globalStorage/zoocodeorganization.zoo-code/`.

### Calling MCP tools (IMPORTANT — read before first tool call)

opencode runs in **Code Mode**: MCP tools are NOT native function tools.
They are callable only inside the `execute` tool's code block, as
`tools.<server>.<tool>(...)`. There is also a `search` function inside
the sandbox for discovering tool names.

WRONG — emits a native tool call, fails with "No tool named":

    agora.check_invariants          # as a direct tool call — INVALID
    tools.agora.check_invariants    # as a direct tool call — INVALID

RIGHT — call `execute` with a code argument:

    execute {"code": "const r = await tools.agora.check_invariants(); console.log(JSON.stringify(r))"}

If unsure which tools exist, call `search` inside the same sandbox:

    execute {"code": "console.log(await search('claims'))"}

## Rules of the house

- **Never** weaken the boundary: no new mounts into `lab-cnt-exp`, no
  host networking, no caps.
- **Never** read, copy, or edit `/srv/lab/mcp-state` or the `mcp`
  account's podman — the trusted zone is fenced from `lab` on purpose.
  Scientific state changes go through MCP tools only
  (`execute` + `tools.<server>.<tool>()`). Do not try to `sudo` into
  `mcp`, do not `sqlite3` the state DBs, do not work around the fence.
- **Never** `su exp` / `sudo -iu exp` — use `labloop-exec`.
- Inside the hostile zone: `HOME` is a writable tmpfs, `/tmp` is 2G
  tmpfs, `/exchange` + `/experiments` are the only persistent rw
  mounts. Full TeX Live (`pdflatex`/`latexmk`, all styles + fonts),
  `pandoc`, `matplotlib` and `reportlab` are baked into the image —
  build PDFs there, not on the driver. `apt` cannot run inside (the
  rootfs is read-only). A genuinely missing CTAN package is rare;
  install it user-mode into `$TEXMFHOME` (persistent):

      tlmgr init-usertree   # once per container
      tlmgr --usermode --repository \
        https://ftp.math.utah.edu/pub/texlive/historic/systems/texlive/2025/tlnet-final \
        install <pkg>

  (the repository pin is required — CTAN's live repo is a newer TeX
  Live release and tlmgr refuses cross-release.) If that still fails,
  last resort is downloading the `.sty` files into `$TEXMFHOME/tex/latex/<pkg>/`
  and running `texhash $TEXMFHOME` — `kpsewhich` finds them. Never
  try `apt`/`tlmgr` system mode — it cannot work by design.
- **Installing things — three lanes, pick by need:**

  1. **Python packages → `uv`** (pip is shimmed off on purpose):

         sudo -u exp /usr/local/sbin/labloop-exec sh -c \
           'cd /experiments/<name> &&
            uv venv --system-site-packages .venv &&
            uv pip install --python .venv/bin/python <pkg...> &&
            .venv/bin/python <script>'

     `--system-site-packages` exposes the baked stack (numpy, pandas,
     scipy, scikit-learn, matplotlib, reportlab); the cache persists
     at `/experiments/.uv-cache`.

  2. **Non-Python runtime deps → `micromamba`** (conda-forge, no root)
     or compile/extract into **`/opt/local`** — a persistent
     agent-writable volume already on `PATH`:

         labloop-exec sh -c 'micromamba create -y -p /experiments/<name>/env -c conda-forge <pkg>'
         labloop-exec sh -c 'tar -xzf tool.tgz -C /opt/local'   # binaries on PATH

  3. **System/apt packages → `labloop-build`**: edit
     `/srv/lab/workspace/ml-labloop/containers/experiment/Containerfile`
     (add the apt/pip line), then `sudo -u exp /usr/local/sbin/labloop-build`
     rebuilds `lab-img-exp` and restarts the container. This is the
     sanctioned way to grow the toolchain — the image is the audit
     trail; never mutate the running rootfs (it's read-only anyway).

  Every `labloop-exec`/`labloop-build` call is logged to the journal
  (`journalctl -t labloop-exec`) — the audit trail is intentional.
- `/srv/lab/experiments` is `exp`-owned: write to it via
  `labloop-exec`, never directly as `lab`.
- **Getting data out → `labloop-export`** (the ONLY sanctioned egress —
  the VM has no scp/ssh out). Stage exchange artifacts for the human:

      labloop-export /srv/lab/exchange/results.tar.gz

  This validates the path (must resolve inside `/srv/lab/exchange`),
  freezes a copy, and builds `manifest.json` + `export.tar.gz` under
  `/var/lib/labloop-export/user/` — the human then pulls it from the host
  with `./90-extract-lab-data.sh`. You stage; you never transmit.
  `sudo labloop-export --all` (full lab-state export) exists but
  quiesces both containers — that is a human operation, not an agent
  one; do not run it. The full mechanism (zones, staging tree,
  verification, lifecycle) is documented in
  `deploy/DATA-MOVEMENT-MANUAL.md`.
- **Updating opencode is a human operation.** `autoupdate` is disabled;
  if a newer release is needed, ask the operator — the in-VM path is
  `sudo labloop-update-opencode <semver>`, which requires the lab login
  password. Do not attempt it yourself, and do not work around it with
  `npm install -g` (fails by design: the global install is root-owned).
- **Getting data IN → the human pushes it.** There is no guest-initiated
  channel; the host runs `./80-ingest-lab-data.sh <vm> <src>` which
  lands the batch at `/srv/lab/incoming/<name>-<UTCts>/` — lab-owned,
  read-only inside the hostile zone at `/incoming/<name>-<UTCts>/`.
  Read datasets from there; never write into `/incoming` (it's mounted
  read-only anyway).
- Secrets (API keys etc.) go in `/home/lab`, mode `600`. Never in the
  workspace payload (it is mounted read-only into the hostile zone)
  and never in committed files.
- Clipboard is disabled VM-wide by design; files move via `/exchange`
  or git pushes, not copy-paste.
- If something looks broken: run `~/Desktop/check-lab-ready.sh` first.
