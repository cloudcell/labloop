# ml-labloop

Isolation harness for the ml-scientist lab loops: one KVM VM per user,
and inside it three zones — driver (VSCodium/Cline), trusted (the five
ml-* MCP servers, one Podman container), hostile (arbitrary experiment
code, one container, no route back).

## Using it

The numbered scripts are the workflow — run them in order on the host:

```text
00-build-lab-template.sh            Mint ISO -> unattended install -> template
01-prepare-template-for-cloning.sh  seal: password lock (publish) + sysprep + snapshot
02-create-vm-from-template.sh <u>   clone -> lab-vm-<u>, readiness + security battery
10-upload-template-to-hf.sh         [y/N] confirm -> seal -> compress -> HF bucket
30-ensure-lab-tools.sh <vm>|all     deploy current lab tooling into a running VM
80-ingest-lab-data.sh <vm> <src>    push data in -> /srv/lab/incoming/<batch-UTCts>
90-extract-lab-data.sh <vm> [dest]  pull staged lab data out -> <vm>-extraction-<ts>
```

Defaults: `00` creates `lab-template-<UTC-ts>`; `01`/`02`/`10` pick the
newest dated template automatically (override with an argument or
`LABLOOP_TEMPLATE=`).

Day to day:

```bash
./02-create-vm-from-template.sh alice     # new user VM -> lab-vm-alice
```

Default login inside every VM: user `lab`, password `lab` (forced to
change at first login). The clone boots, runs `check-lab-ready.sh` +
the security battery inside (expect `22 PASS, 0 FAIL` /
`18 PASS, 0 WARN, 0 FAIL`), and writes the current password to
`/var/lib/libvirt/images/lab-vm-alice.otp` (mode 400) — `lab` for
normal templates, a generated one for publish-locked images.
(`exp` is a locked service account, not a login.)

First run inside a new VM: paste `deploy/GENESIS-RESEARCH-PROMPT.md`
to the driver agent — it walks one complete scientific cycle
(programme → falsifiable hypothesis → `labloop-exec` trial →
observation → belief → evidence-backed verdict → anamnesis claim →
staged export) and exercises every MCP service and zone rule before
real work.

> **The first boot of a fresh clone is slow — a few minutes, not
> seconds.** Sysprep scrubbed its identity (machine-id, SSH host keys,
> network config), which the guest regenerates on first power-on; the
> rootless Podman quadlets also start for the first time (both zone
> containers + lingering user services). The QEMU guest agent can take
> ~60–90 s to answer, and the readiness check only passes once the
> zone containers are up. A black/login-less screen during that window
> is normal — subsequent boots are fast.

Template releases:

```bash
./00-build-lab-template.sh                # rebuild when the image changes
./10-upload-template-to-hf.sh             # publish -> hf://buckets/... bucket
```

`10` asks `[y/N]` (default: no), locks `lab`+`exp` passwords inside the
image before upload, and pushes `<name>-<UTC-ts>.qcow2` + `.sha256`.
Recipients fetch it and run `release-package/import-lab-vm.sh`:

```bash
hf buckets cp hf://buckets/LabLoopCommunity/lab-trials/<file>.qcow2 .
sha256sum -c <file>.qcow2.sha256
release-package/import-lab-vm.sh <file>.qcow2 <name>
```

Design and governance live in `docs/`:

- `sa-standards/` — security documentation convention (the s-prefix)
- `sb-architecture/sb-01-secure-encapsulation.md` — the zone design
- `se-plans/` — execution plan + two-zone escalation variant
- `si-reviews/` — review-gate verdicts
- `sg-research/` — external/reference material
- `a-standards` … `r-references` — symlinked from `../ml-scientist/docs/`

Layout:

```text
containers/mcp/         trusted-zone image + 5-server entrypoint
containers/experiment/  hostile-zone image
deploy/labloop.sudoers  driver -> hostile exec channel (VM sudoers.d)
deploy/labloop.nft      zone firewall (inside the VM)
deploy/quadlets/        rootless systemd units, per user
create-lab-user         template -> per-user VM clone (host side)
```

Build context note: `containers/mcp/Containerfile` expects a staged
context containing a `ml-scientist/` checkout — see its header comment.
