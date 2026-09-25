# SECURITY-MANUAL — operating rules for the labloop VM

This manual is the **binding contract** for working inside a labloop
VM. `AGENT-LAB-GUIDE.md` orients you; this document tells you what is
permitted, what is forbidden, and *why*. Read it before installing
anything, running anything, or touching any account but your own.

The governing principle is **security above all** (a-00 §5.11): when
convenience and isolation conflict, isolation wins — every time.

## 1. The threat model in one paragraph

The lab exists so that **generated, unreviewed, potentially hostile
code can run**. Assume every artifact produced inside the hostile zone
is adversarial: it may attempt to read secrets, persist itself, attack
the network, or corrupt scientific state. The design does not try to
make hostile code *well-behaved*; it makes hostile code *irrelevant* —
it can burn down its own container and nothing else is harmed.

```text
KVM boundary          the VM itself is disposable; the host is outside it
  └─ zone boundaries  lab / mcp / exp are separate uid domains
       └─ container   lab-cnt-exp: read-only rootfs, no caps, fenced net
            └─ exec   labloop-exec: the ONE door in, audited
```

## 2. The three zones and their rules

| Zone    | User  | Container     | Trust level | May do                                          | Must NEVER do                                |
| ------- | ----- | ------------- | ----------- | ----------------------------------------------- | -------------------------------------------- |
| driver  | `lab` | host procs    | high        | author code, call MCP tools, drive labloop-exec | read `/srv/lab/mcp-state`, sudo into `mcp`   |
| trusted | `mcp` | `lab-cnt-mcp` | highest     | serve MCP, hold scientific state                | be administered by anyone but root/install   |
| hostile | `exp` | `lab-cnt-exp` | none        | run arbitrary code, install via the lanes (§5)  | reach trusted zone, gateway, private nets    |

Rules that follow from the table — violations are **defects to report**,
not inconveniences to work around:

1. **The trusted zone is reachable only through MCP tools** over
   loopback (`execute` → `tools.<server>.<tool>()`). Never `sqlite3`
   the state DBs, never `sudo -u mcp`, never read `ingest.env`.
   `mcp-state` is mode 700 owned by `mcp` — if you can read it,
   something is broken; report it.
2. **The hostile zone is entered only through `labloop-exec`** —
   never `su exp`, `sudo -iu exp`, or direct `podman` as `exp`.
3. **Artifacts crossing `/exchange` stay untrusted.** A PDF, dataset,
   or script produced by hostile code is hostile output — inspect it
   before you run, open, or feed it to anything trusted.
4. **Secrets live in `/home/lab` mode 600 only.** Never in the
   workspace payload (it is mounted read-only into the hostile zone),
   never inside `/experiments` or `/exchange`, never in git.

## 3. What actually contains the hostile zone

Knowing the real boundary tells you what you may and may not rely on:

| Control                      | Mechanism                                                    |
| ---------------------------- | ------------------------------------------------------------ |
| Immutable rootfs             | `ReadOnly=true` — writes to `/` fail by design               |
| Privilege                    | `DropCapability=all`, `NoNewPrivileges` — no escalation path |
| Execution surface            | `/tmp` tmpfs `noexec`; `HOME=/home/exp` writable tmpfs       |
| Resources                    | 6 CPUs, 16 GB RAM, 512 PIDs — a fork bomb dies in its cage   |
| Network                      | uid-owner nftables rules: no loopback, no private space,     |
|                              | no host gateway (except DNS); public egress only             |
| Persistence                  | `/experiments` + `/opt/local` + `/exchange` — the ONLY       |
|                              | writable persistent state, all exp-owned                     |
| Audit                        | every `labloop-exec`/`labloop-build` call → journald         |

**What is NOT a boundary:** the uv-only policy, the pip shim, package
choice. The hostile zone has public egress — determined code could
download a binary by hand. These are *policy* for reproducibility,
not walls. Do not mistake policy for containment, and do not try to
"fix" the real walls.

## 4. Audit — you are being logged, on purpose

```bash
journalctl -t labloop-exec     # every command driven into hostile zone
journalctl -t labloop-build    # every image rebuild
```

The journal is root-owned — neither `lab` nor `exp` can erase it. Every
exec and every rebuild is recorded with the invoking context. This is
provenance, not surveillance: the scientific record requires knowing
*what ran*.

## 5. Installing software — the four lanes

The hostile rootfs is read-only **forever** — that is the whole point.
"Installing" always means one of these lanes, chosen by what you need:

### Lane 1 — Python packages → `uv` (per-experiment venv)

`pip`, `pip3`, `ensurepip` are **removed** from the image; `pip` is a
shim that prints these instructions and fails. `uv` is the only paved
road — one tool, one persistent cache, reproducible venvs:

```bash
sudo -u exp /usr/local/sbin/labloop-exec sh -c \
  'cd /experiments/<name> &&
   uv venv --system-site-packages .venv &&
   uv pip install --python .venv/bin/python <pkg...> &&
   .venv/bin/python <script>'
```

- `--system-site-packages` exposes the baked stack (numpy, pandas,
  scipy, scikit-learn, matplotlib, reportlab) — do not reinstall them.
- The uv cache persists at `/experiments/.uv-cache`; venvs live inside
  the experiment dir, so each experiment pins its own deps.
- `uv` is pinned to 0.11.26 and `UV_PYTHON_PREFERENCE=only-system` —
  it will not download interpreters; the image Python is the runtime.

### Lane 2 — conda-forge / binary deps → `micromamba` or `/opt/local`

For non-Python dependencies (native libs, compilers, bio/physics
tools) without root:

```bash
# conda-forge packages — lands in the exp-owned prefix, no root:
labloop-exec sh -c \
  'micromamba create -y -p /experiments/<name>/env -c conda-forge <pkg>'

# compiled tools / static binaries — persistent, already on PATH:
labloop-exec sh -c 'tar -xzf tool.tgz -C /opt/local'
labloop-exec /opt/local/bin/<tool> --version
```

`MAMBA_ROOT_PREFIX=/experiments/.micromamba` — environments persist
across container restarts. `/opt/local` is a dedicated persistent
volume (`/opt/local/bin` is on `PATH` ahead of system dirs).

### Lane 3 — system/apt packages → `labloop-build` (image rebuild)

`apt`/`dpkg` cannot run inside the hostile container — read-only
rootfs, no capabilities. A system-level dependency (e.g.
`libhdf5-dev`, a new runtime) is added **declaratively**:

```bash
# 1. edit the image source (lab can edit the workspace):
$EDITOR /srv/lab/workspace/ml-labloop/containers/experiment/Containerfile
#    add your apt-get install line to the existing apt layer

# 2. rebuild + restart through the sanctioned channel:
sudo -u exp /usr/local/sbin/labloop-build
```

This is the sanctioned growth path: **the Containerfile is the
provenance** — every system dep is recorded in the image source,
reviewable, and reproducible on the next template build. The rebuild
runs in exp's own rootless podman; it cannot alter the quadlet, the
mounts, or any sandboxing — those live outside the image.

### Lane 4 — extra CTAN packages → `tlmgr --usermode`

The image carries the lean TeX Live set (`pdflatex`, `latexmk`,
recommended styles). A missing CTAN package installs into the
persistent user tree `$TEXMFHOME=/experiments/.texmf`:

```bash
labloop-exec sh -c 'tlmgr init-usertree'   # once per container
labloop-exec sh -c 'tlmgr --usermode \
  --repository https://ftp.math.utah.edu/pub/texlive/historic/systems/texlive/2025/tlnet-final \
  install <pkg>'
```

The `--repository` pin is **required**: CTAN's live repo is a newer
TeX Live release and tlmgr refuses cross-release. Never run `tlmgr`
in system mode — it cannot write the read-only rootfs anyway.

### Decision rule

```text
need a python pkg?         → lane 1 (uv venv)
need a binary/lib/tool?    → lane 2 (micromamba or /opt/local)
need an apt/system dep?    → lane 3 (edit Containerfile + labloop-build)
need a CTAN package?       → lane 4 (tlmgr --usermode)
tempted to bypass a lane?  → STOP: that IS the security violation
```

## 6. What is forbidden, and what happens if you try

| Attempt                                   | Result                                              |
| ----------------------------------------- | --------------------------------------------------- |
| `pip install` inside container            | shim fails with redirect to `uv`                    |
| `apt`/`dpkg`/`tlmgr` system mode          | read-only rootfs — impossible, not merely denied    |
| write to `/workspace` in container        | read-only mount — impossible                        |
| reach `172.31.0.2` or any private IP      | nftables owner-drop — connection times out          |
| reach `127.0.0.1:38xxx` from container    | loopback drop + loopback-only publish — unreachable |
| `sudo -u mcp …` / read `mcp-state`        | no sudoers rule; mode 700 — denied                  |
| `su exp` / `sudo -iu exp` / podman as exp | password locked; use `labloop-exec`                 |
| fork bomb / memory exhaustion             | pids.max 512 / 16 GB cap — contained, logged        |
| edit quadlet via `labloop-build`          | impossible — sandbox lives outside the image        |

Every one of these is **verified by `security-battery.sh`** — run
`~/Desktop/check-lab-ready.sh` to re-prove all of them at any time.

## 7. Getting data out — and in — the transfer channels

All movement is **host-initiated**: the guest can stage and receive,
never initiate. There is no sshd, no virtiofs, no guest→host socket —
everything crosses the qemu-guest-agent channel the host controls.

- **Stage (guest):** `labloop-export <paths>` as `lab` — every path
  must `realpath` inside `/srv/lab/exchange`; symlink escapes and
  out-of-zone paths are refused. For a full lab-work export:
  `sudo labloop-export --all` — sudoers-arg-pinned to exactly `--all`
  (no path arguments can reach root's read set); it quiesces both
  containers, then collects exchange, experiments, `mcp-state`, mcp/exp
  podman volumes, the full journal + rendered logs, workspace, and
  agent state. Staging lives in `/var/lib/labloop-export/{user,root}/`
  (tmpfiles-provisioned, `0700` per owner — the hostile zone cannot
  pre-seed it). `--all` is rate-limited to once per 300 s: it is
  NOPASSWD-invocable by `lab` but quiesces the whole lab, so it must
  not be loopable; all exports are flock-serialized per mode.
- **Pull (host):** `./90-extract-lab-data.sh <vm> [dest]` lands
  `<vm>-extraction-<UTCts>.tar.gz` + a `sha256sum -c`-compatible
  `.sha256`, both `0600`, next to a `_UNTRUSTED-SENSITIVE.txt` marker.
  The tarball is **never unpacked on the host** — it is simultaneously
  untrusted (hostile bytes) and sensitive (agent state contains
  credentials). Verify with `sha256sum -c`, extract deliberately.
- **Ingest (host pushes in):** `./80-ingest-lab-data.sh <vm> <src>
  [name]` packs the source into a deterministic tar, pushes it over
  the same agent channel, and lands it at
  `/srv/lab/incoming/<name>-<UTCts>/` — lab-owned, mounted read-only
  into the hostile zone at `/incoming`. Batches are immutable: a
  timestamped dir is created per ingest, never overwritten, so input
  provenance is preserved (the record of *which* dataset version ran).
  Because `/incoming` is hostile-readable, the script refuses
  likely-credential filenames (`*.pem`, `*.key`, `id_rsa*`, `.env`,
  `*.kdbx`, …) unless `--force` is passed — check what you push.
- The egress channel is one-directional by construction: a
  hostile-crafted manifest can only ever point the puller at the two
  fixed staging paths — it cannot redirect a read anywhere else.

## 8. Root inside the VM — who holds it, and what it costs

`lab` is a standard Mint admin account: member of `sudo`, full
`(ALL : ALL) ALL`. That is a deliberate choice — a human at the
console must be able to administer the box — but it is worth being
precise about what it does and does not mean.

**The pinned wrappers are lanes, not walls.** `labloop-exec`,
`labloop-build`, `labloop-export --all`, and `labloop-update-opencode`
exist so that *routine* operations never need broad root — each pins a
capability (exec as exp / rebuild the image / export the fixed read
set / install a semver-pinned package) instead of opening a shell. They
also shape the agent's NOPASSWD surface: what `lab` can do without a
password is confined to pinned, bounded commands.

**A human with the password already has everything.** `sudo npm
install -g …`, `sudo -iu mcp`, read of `/var/lib/labloop-export/root` —
all reachable. `labloop-update-opencode` does not forbid `sudo npm`; it
provides the version-validated path so nobody has to reach for the
unrestricted one. The control is *habit and audit*, not impossibility.

**The password is the only human-vs-agent boundary inside the VM.**
The OTP lives on the host (`$VM.otp`, mode 400), but the agent shares
the desktop session the human types into — a fake sudo prompt or
keystroke sniffing could capture it. Treat any password typed during an
active agent session as potentially exposed. This is a *human-check*,
not a hard boundary: it proves "a person was here," not "only persons
can ever reach root."

**Why that is acceptable here:** the VM is the hard boundary. Worst
case, a captured password buys root *inside* a disposable clone —
host, sibling VMs, and other users are still behind KVM. The trusted
zone's real protection is the same: `mcp` assets sit behind a password
the agent does not have, inside an image that gets destroyed on
retirement.

**If a harder in-VM split is ever needed:** remove `lab` from the
`sudo` group (keep only the pinned `labloop-*` rules, with
`--all`/update made password-required), and give humans a separate
admin credential — a distinct account or console root — so the
driver-zone credential can never reach root at all. The cost is real:
every admin action needs the second credential, and recovery paths get
thinner. Adopt it when clones start holding value that outlives the
lab, or when agents get autonomous enough to run convincing phishing
against the operator.

## 9. Honest residual risks (what this design does NOT claim)

- **The `lab` account is a full sudoer** — the sudo password is the
  only human-vs-agent boundary inside the VM, and the desktop session
  can be phished. See §8 for what that buys and how to harden it.
- **Public egress is open** — anything *inside* the hostile zone can be
  exfiltrated. That is why nothing secret ever enters it.
- **Bearer MCP auth** — loopback-only; any driver-zone process can
  call the tools. The agent is the driver; this is intended.
- **VM escape** is the residual risk of any KVM boundary — mitigated by
  keeping QEMU/libvirt/kernel current and default sVirt confinement.
  If stronger separation is needed, the escalation path is sibling
  VMs: trusted services in a second VM reachable only via MCP ports.

If you find a way around any control in §6, **that is a security
defect** — report it; do not use it.

---
*How this posture maps to industry standards (NIST SP 800-190, CIS
Docker Benchmark, production agent-sandbox practice) — including the
deliberate gaps and their escalation paths — is recorded in
`SECURITY-COMPLIANCE-NOTE.md`, same directory.*
