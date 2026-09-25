# Lab Loop™: a research automation environment for empirical work

Hypotheses, designed experiments, evidence and conclusions as
durable state — built for researchers, applications and AI agents
across any empirical domain.

Two projects, side by side. Each folder is self-contained and usable
on its own.

```text
ml-scientist/   the five ml-* MCP servers (agora, arete, zetesis,
                episteme, anamnesis) — run them anywhere, no VM needed
ml-labloop/     the KVM isolation harness — builds a per-user VM where
                those services run in a trusted zone, sealed off from
                a hostile experiment zone
```

## ml-scientist (standalone)

No VM required. From `ml-scientist/`:

```bash
uv sync --frozen
./labloop start all        # or ./run-ml-episteme.sh for a single server
./labloop status
```

Ports are defined in `ports.env` (38050/38060/38070/38080/38090).

## ml-labloop (the lab VM)

Requires KVM/libvirt on a Linux host. From `ml-labloop/`:

```bash
./00-build-lab-template.sh              # Mint ISO -> unattended build
./01-prepare-template-for-cloning.sh    # seal
./02-create-vm-from-template.sh <user>  # clone -> lab-vm-<user>
```

A locally built VM boots with login `lab` / password `lab` — change it
after first login.

`create-lab-template` finds the sibling `../ml-scientist` checkout
automatically (override with `ML_SCIENTIST=`). See `ml-labloop/README.md`
for the full workflow including publishing VM images.

## Download a prebuilt image

Released VM images live in the public Hugging Face bucket:
[https://huggingface.co/buckets/cloudcell/LabLoop](https://huggingface.co/buckets/cloudcell/LabLoop)

Download an image plus its checksum, verify it, then import it with
the script in `ml-labloop/release-package/`:

```bash
hf buckets cp hf://buckets/cloudcell/LabLoop/<image>.qcow2 .
hf buckets cp hf://buckets/cloudcell/LabLoop/<image>.qcow2.sha256 .
sha256sum -c <image>.qcow2.sha256
./import-lab-vm.sh <image>.qcow2 <vm-name>
```

Published images are sealed — unlike a local build, the `lab`/`exp`
passwords are locked and the import generates a one-time password for
first login. See `ml-labloop/release-package/import-lab-vm.sh` for
details.

## Appendix A — operations manual

Beyond the build/clone pipeline (`00`/`01`/`02`), the numbered
scripts cover the day-to-day operations on a running lab VM. All
data movement is **host-initiated over the qemu guest agent** — no
SSH, no virtiofs, no guest→host sockets.

### `10-upload-template-to-hf.sh [template]`

Seals a template for publication (locks `lab`/`exp` passwords,
sysprep, snapshot), flattens + compresses the qcow2, checksums it,
and uploads the pair to the private trials bucket
(`hf://buckets/LabLoopCommunity/lab-trials`). Asks before publishing.

### `11-release-template.sh [image] [--keep]`

Promotes a qcow2 + `.sha256` from the trials bucket to the public
release bucket (`hf://buckets/cloudcell/LabLoop`). Shows a numbered
menu when no name is given; verifies both objects landed before
removing them from the source (`--keep` copies instead of moving).

### `30-ensure-lab-tools.sh <vm>|all`

Idempotent in-place upgrade for VMs built before a tooling change.
Pushes the current `deploy/` payload into the guest — wrappers
(`labloop-exec`, `labloop-export`, `labloop-build`,
`labloop-update-opencode`), sudoers, quadlets, opencode config,
readiness check, agent guides — and restarts quadlets only when
their definition changed. New templates bake all of this in; this
script is the upgrade path for clones that already exist.

### `80-ingest-lab-data.sh <vm> <src-path> [batch-name]`

The sanctioned *push* channel. Lands a file or directory on the
guest at `/srv/lab/incoming/<batch>-<UTC-ts>/`, mounted read-only
into the hostile zone at `/incoming`. Batches are immutable —
re-ingesting creates a new timestamped batch, never overwrites.
Convention: stage inputs under `./incoming/` on the host first.

### `90-extract-lab-data.sh <vm> [dest-dir]`

The sanctioned *pull* channel. Reads the manifest + tarball produced
in-guest by `labloop-export` (run `sudo labloop-export --all`, or a
path-scoped export, inside the VM first), verifies the tarball's
sha256 against the manifest, and lands
`<vm>-extraction-<UTC-ts>.tar.gz` (mode 0600) plus its checksum in
`./extracted/`. The tarball is **never opened on the host** — it is
untrusted, sensitive content; extract it deliberately yourself.

### Inside the VM

| Command | Role |
| --- | --- |
| `labloop-exec <cmd> …` | Run code in the hostile zone (as `exp`, confined) |
| `labloop-export <path>` / `--all` | Stage lab artifacts into `/var/lib/labloop-export/` for `90-*` to pull (`--all` quiesces the lab; operator action) |
| `labloop-build` | Rebuild the hostile-zone image after editing its Containerfile |
| `sudo labloop-update-opencode <semver>` | Update opencode to a pinned version (password-gated) |
| `~/Desktop/check-lab-ready.sh` | Readiness + security battery; warns on drift, fails on real defects |

## Appendix B — build provenance

Templates and tooling were developed and tested on:

| Component | Version |
| --- | --- |
| Host OS | Linux Mint 22.3 (Zena) |
| Host kernel | 6.14.0-37-generic |
| QEMU / qemu-img | 8.2.2 (Debian 1:8.2.2+ds-0ubuntu1.18) |
| libvirt / libvirtd | 10.0.0 (`qemu:///system`) |
| Guest OS | Linux Mint 22.3 Xfce (unattended ISO, `selfbuild/mk-auto-iso.sh`) |
| Guest container runtime | rootless Podman (quadlets) |
