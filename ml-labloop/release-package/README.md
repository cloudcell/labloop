# labloop lab VM — release package

A self-contained, security-isolated ML experimentation lab as a KVM
virtual appliance. You download one qcow2 image, run one script, and
get a desktop VM with an AI coding agent, trusted MCP services, and a
hostile-zone sandbox for generated/aggressive experiment code.

## What's inside the image

- Linux Mint / XFCE desktop, timezone UTC, QXL+SPICE console with
  auto-resize (spice-vdagent).
- **Driver zone** (`lab` user): VSCodium, opencode `2.0.14`, git, uv,
  Midnight Commander, all pointed at the lab workspace.
- **Trusted zone**: `lab-cnt-mcp` — five MCP servers (agora status hub,
  arete optimizer, zetesis research, episteme executor, anamnesis
  claims memory) on loopback ports 38050–38091.
- **Hostile zone**: `lab-cnt-exp` — rootless podman container for
  untrusted experiment code: read-only rootfs, no capabilities,
  NoNewPrivs, 512-PID and 16 GiB cgroup caps, catatonit init, nftables
  rules denying it the trusted zone, the VM, and private address space.
- Password policy: minimum length 3, anything else allowed; faillock
  locks the account for 15 min after 10 bad tries.

## Requirements (host)

- Linux with KVM (`/dev/kvm`), libvirt `qemu:///system`, python3.
- `virt-manager` recommended for the console window.
- ~16 GiB RAM for the guest, ~20 GiB disk.

## Usage

```bash
# 1. download the image from the bucket (needs a HF account with
#    read access to the LabLoopCommunity/lab-trials bucket)
#    — pick the newest lab-template-v1-<timestamp>.qcow2
pip install -U huggingface_hub   # or use a venv
hf auth login
IMG=lab-template-v1-20260922T0908Z.qcow2   # check `hf buckets ls` for latest
hf buckets cp \
  "hf://buckets/LabLoopCommunity/lab-trials/$IMG" ~/Downloads/

# 2. verify integrity
cd ~/Downloads
hf buckets cp \
  "hf://buckets/LabLoopCommunity/lab-trials/$IMG.sha256" .
sha256sum -c "$IMG.sha256"

# 3. run the installer
chmod +x import-lab-vm.sh
./import-lab-vm.sh "$HOME/Downloads/$IMG" mylab
```

The image is a **compressed qcow2** (~6 GiB download, 80 GiB
virtual) — the installer uses it directly, no decompression step.

The script copies the image to `/var/lib/libvirt/images/lab-vm-mylab.qcow2`,
defines and boots the domain, sets credentials, waits for the zone
containers, and runs the readiness check + security battery inside.
Expect: `21 PASS` and `18 PASS, 0 WARN, 0 FAIL`.

## First login

Open the console in virt-manager and log in as `lab` / `lab`. A
terminal pops up asking you to set a new password — it reappears at
every login until you do (type `lab` once as the current password).
The one-time password is also stored host-side at
`/var/lib/libvirt/images/lab-vm-mylab.otp` (mode 400).

Tip: enable **View → Scale Display → Auto resize VM with window** in
virt-manager for auto-fit resolution. Clipboard is deliberately
disabled — move files through `/srv/lab/exchange` (inside the VM) or git.

## Layout inside the VM

```text
/home/lab/workspace                 opencode launches here (AGENTS.md
                                    auto-loads the lab guide)
/home/lab/workspace/experiments     draft/review: papers, notes, code
/srv/lab/experiments                promote here -> hostile zone runs it
/srv/lab/exchange                   artifact airlock back to driver
/srv/lab/mcp-state                  trusted MCP state (mode 700)
~/Desktop/check-lab-ready.sh        double-click: full health check
~/Desktop/Lab-Dashboard             agora status hub GUI (:38051)
~/Desktop/opencode                  AI agent launcher
~/AGENT-LAB-GUIDE.md                orientation for you and the agent
```

## Lifecycle

Draft and review under `~/workspace/experiments/<name>/`, promote to
`/srv/lab/experiments`, run via the `labloop-exec` channel (never `su
exp` directly), collect artifacts through `/srv/lab/exchange`.

## Publishing the image (maintainer)

The sealed template qcow2 carries internal `snp-*` snapshots — flatten
and compress before upload so the download is just the current sealed
state (~6 GiB instead of ~25):

```bash
qemu-img convert -c -O qcow2 \
    /var/lib/libvirt/images/lab-template-v1.qcow2 lab-template-v1.qcow2.out
sha256sum lab-template-v1.qcow2.out > lab-template-v1.qcow2.sha256
hf buckets cp lab-template-v1.qcow2.out \
  hf://buckets/LabLoopCommunity/lab-trials/lab-template-v1.qcow2
hf buckets cp lab-template-v1.qcow2.sha256 \
  hf://buckets/LabLoopCommunity/lab-trials/lab-template-v1.qcow2.sha256
```

sha256 of the published image `lab-template-v1-20260922T0908Z.qcow2`:
`68f5583478edcb6c4dd20cd3f88dfb37004a9114b202c6174b04601abc46ee24`

Maintainer one-liner (seal + compress + timestamp + upload):
`SKIP_SEAL=1 ../10-upload-template-to-hf.sh` — drop SKIP_SEAL to seal first.

Recipients run `import-lab-vm.sh` against the downloaded file.

## Notes

- `exp` is a locked service account — hostile code runs *as* it, never
  *as you*.
- If the readiness battery reports `hostile-to-host-gw` failures on a
  custom network setup, the in-guest `labloop.nft` table is what
  enforces private-space denial — it's preinstalled; don't flush it.
- To rebuild or re-derive the image itself, see the parent repo
  (`create-lab-template`, `01-prepare-template-for-cloning.sh`,
  `02-create-vm-from-template.sh`).
