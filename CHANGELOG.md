# Changelog

All notable changes to Lab Loop™ (ml-labloop + ml-scientist) are
recorded here. Format follows [Keep a
Changelog](https://keepachangelog.com/en/1.1.0/); this project uses
calendar-dated releases until a versioning scheme is adopted.
Entry timestamps are the commit time in **UTC**.

## [0.0.1] — 2026-09-25 — first public release

### Added

- **Lab VM pipeline** — unattended Linux Mint 22.3 build
  (`00-build-lab-template.sh`), publish-mode sealing
  (`01-prepare-template-for-cloning.sh`), and per-user cloning
  (`02-create-vm-from-template.sh`) against KVM/libvirt.
  *(2026-09-21 21:23Z – 2026-09-22 16:11Z)*
- **Three-zone isolation** — driver (`lab`), trusted (`mcp`, the five
  ml-* MCP servers under rootless Podman), hostile (`exp`, confined
  experiment container: read-only rootfs, PID/memory caps, no route
  to MCP or host networks). *(2026-09-20 18:42Z)*
- **ml-scientist** — the five MCP servers (agora, arete, zetesis,
  episteme, anamnesis) with the commitments enforced structurally:
  falsifiability at admission, sealed-code bundles, programme-level
  state, tracked belief, and sandboxed execution that fails closed.
  *(ecosystem since 2026-09-13 14:24Z)*
- **Sanctioned data channels** — host-initiated over the QEMU guest
  agent only (no SSH, no virtiofs, no guest→host sockets):
  `80-ingest-lab-data.sh` pushes timestamped, immutable batches to a
  read-only `/incoming`; `90-extract-lab-data.sh` pulls manifest +
  sha256-verified tarballs staged in-guest by `labloop-export`.
  *(2026-09-23 21:20Z – 21:29Z)*
- **`30-ensure-lab-tools.sh`** — idempotent in-place tooling upgrade
  for existing VMs (wrappers, sudoers, quadlets, opencode config,
  guides, readiness check). *(2026-09-23 22:00Z)*
- **`11-release-template.sh`** — menu-driven promotion of a qcow2 +
  checksum from the private trials bucket to the public release
  bucket (`hf://buckets/cloudcell/LabLoop`). *(2026-09-25 04:18Z)*
- **GENESIS warmup run** — a full scientific cycle as an apparatus
  smoke test, ending with the agent writing a signed
  `*-ONBOARDING.md` note for future sessions.
  *(2026-09-23 23:03Z; steps 0 + 11: 2026-09-24 06:05Z–06:27Z)*
- **Trial interpreter** — `/srv/lab/trial-env` volume carrying
  numpy / pandas / scipy / scikit-learn / matplotlib; episteme's
  executor uses it via `ML_EPISTEME_EXECUTOR_PYTHON`.
  *(2026-09-24 21:01Z)*
- **`labloop-update-opencode`** — password-gated, semver-validated
  update path for the driver-zone agent (`autoupdate: false` keeps
  the pinned install authoritative). *(2026-09-24 04:58Z)*
- **Readiness warnings** — `check-lab-ready.sh` gains a WARN tier:
  opencode PATH-shadowing and version drift are reported without
  failing the run. *(2026-09-24 05:24Z)*

### Fixed

- **Hostile-zone resource storm wedges the VM** — `lab-cnt-exp`
  was allowed `--cpus 6 --memory 16g` on an 8 vCPU / 16 GiB VM
  (no headroom for the desktop), and `labloop-exec` did not pin
  BLAS/OpenMP threading, so a single `import numpy` before
  `os.environ.setdefault("OMP_NUM_THREADS","1")` spawned 8 threads
  per worker — 86 runnable threads, load 58, display starved, hard
  freeze. Caps are now `--cpus 5 --cpu-shares 512 --memory 12g`
  (host tasks win under contention) and `labloop-exec` exports
  `OMP/OPENBLAS/MKL/NUMEXPR/VECLIB/BLIS_NUM_THREADS=1` into every
  exec. `30-ensure-lab-tools.sh` also now pushes `labloop-exec` +
  `labloop-build`, which it had silently never updated.
  *(2026-09-26 18:06Z)*
- **Display freezes** — QXL `vgamem` raised 32 → 128 MiB; at
  2560×1440 the starved framebuffer caused TTM thrashing until the
  DRM path wedged. *(2026-09-25 22:57Z)*
- **`design_experiment` race** — parallel calls both promoted the
  same hypothesis; the loser hit an illegal `under_test→under_test`
  transition after already creating its trial. Promotion is now
  atomic and idempotent. *(2026-09-24 20:57Z)*
- **Export staging on tmpfs** — `labloop-export` stages under
  `/var/lib` (survives reboots) instead of `/run`.
  *(2026-09-23 22:39Z)*
- **Guest-agent flakiness** — ingest/extract retry transient QGA
  transport failures and capture stderr. *(2026-09-23 22:20Z)*
- **Quadlet restarts** — `SuccessExitStatus` added so container
  exits don't wedge the services. *(2026-09-23 22:54Z)*

### Security

- Published images are **sealed**: `lab`/`exp` passwords locked; the
  importer mints a random one-time password for first login.
  *(sealing pipeline 2026-09-22 09:00Z; OTP import 2026-09-22 06:33Z)*
- `SECURITY-MANUAL.md` §8 documents the root-access model: bounded
  wrappers are lanes, not walls — the VM is the hard boundary.
  *(2026-09-24 05:17Z)*
