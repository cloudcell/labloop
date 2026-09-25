# DATA-MOVEMENT-MANUAL — how lab data crosses the VM boundary

> The only sanctioned ways to move files between host and lab VM:
> **in** via `80-ingest-lab-data.sh` (host push) and **out** via
> `labloop-export` (guest stage) + `90-extract-lab-data.sh` (host pull).
> No SSH, no virtiofs, no shared folders, no guest→host sockets —
> everything rides the qemu-guest-agent channel that already exists
> for VM management.

## 1. Why the mechanism looks like this

The VM runs three zones:

```text
DRIVER   lab — editor, agents, your interactive work
TRUSTED  mcp — scientific-state services and their stores
HOSTILE  exp — experiment code, unvetted and agent-generated
```

The hostile zone is allowed to produce anything, including hostile
bytes. The design goal: lab-generated data can leave the VM, but the
channel itself can never become an attack surface into the host or
the trusted zone. That drives every choice below:

- **Pull-only for egress.** The guest never initiates a connection to
  the host. The guest *stages*; the host *pulls*. A compromised or
  hostile guest cannot push anything at the host uninvited.
- **Fixed read paths.** The host only ever reads two filenames under
  `/var/lib/labloop-export/{user,root}/` — `manifest.json` and
  `export.tar.gz` — and rejects any other answer from the guest.
- **Verify on the host before trusting.** The manifest is validated
  host-side (format, hash shape, size cap) before the tarball is
  pulled; the tarball must match its declared SHA-256 before it earns
  a real filename.
- **Never auto-opened.** Extracted artifacts are both untrusted
  (hostile-zone contents) and sensitive (agent state carries
  credentials). They land packed, mode `0600`, with a warning marker.

## 2. The staging tree

```text
/var/lib/labloop-export          0711 root:root   traverse-only base
/var/lib/labloop-export/user     0700 lab:lab     path-mode staging
/var/lib/labloop-export/root     0700 root:root   full-export staging
```

- Persistent disk, not `/run` tmpfs — lab data can exceed RAM-bounded
  tmpfs (this failure was observed in the field).
- Separate user/root trees so a `lab`-level process can never read or
  tamper with a privileged `--all` export in flight.
- `labloop-export` re-creates the dirs, verifies ownership before
  staging (refuses on mismatch — fails closed), serializes concurrent
  runs with `flock`, and caps staged size.

## 3. Getting data OUT (extraction)

### Guest side — stage

```bash
# path mode — as lab, no sudo. Paths must live under /srv/lab/exchange.
labloop-export /srv/lab/exchange/results.tar.gz /srv/lab/exchange/fig1.pdf

# full mode — operator-grade snapshot. Root, hardcoded read set,
# quiesces lab-cnt-mcp + lab-cnt-exp briefly, 300s rate limit.
sudo labloop-export --all
```

Both write `manifest.json` + `export.tar.gz` into their staging dir.
`--all` covers `/srv/lab/{exchange,experiments,mcp-state}`, podman
volumes, journald + logs, workspace, and agent state — lab work, not
the OS image.

### Host side — pull

```bash
./90-extract-lab-data.sh <vm> [dest-dir]     # dest defaults ./extracted
```

What it does, in order:

1. **Wakes the VM if dormant** — paused→resume, off→start,
   pmsuspended→dompmwakeup, in-shutdown→wait+start. The found state is
   restored on exit (success, failure, or Ctrl-C) via an `EXIT` trap.
   A running VM is left running.
2. **Waits for the guest agent**, retrying transport-level hiccups
   (common right after boot) without retrying real guest failures.
3. **Self-heals old clones** — if `labloop-export` is missing it runs
   `30-ensure-lab-tools.sh`. If *it* woke the VM and nothing is staged,
   it runs `labloop-export --all` itself. On an already-running VM it
   refuses instead of quiescing your live lab — stage explicitly.
4. **Picks the newest staged manifest** — but only under
   `/var/lib/labloop-export/{root,user}/`. Any other path the guest
   reports is rejected outright.
5. **Validates the manifest on the host** — format version, exact
   tarball name, 64-hex SHA-256, size ≤ 8 GiB, sane timestamp.
6. **Pulls `export.tar.gz` to `*.partial`**, verifies the SHA-256,
   and only then renames it to:

   ```text
   <vm>-extraction-<YYYYMMDDTHHMMZ>.tar.gz         (0600)
   <vm>-extraction-<YYYYMMDDTHHMMZ>.tar.gz.sha256  (0600)
   _UNTRUSTED-SENSITIVE.txt                        (marker, once)
   extraction.log                                  (append, 0600)
   ```

7. **Restores the VM's original power state.**

Never overwrites an existing artifact — pick a new destination or
move the old one.

## 4. Getting data IN (ingestion)

```bash
./80-ingest-lab-data.sh <vm> <path> [path...]
```

- Same wake-and-restore lifecycle as `90-` — dormant VMs are woken,
   used, and put back.
- Files land at `/srv/lab/incoming/<batch>-<UTC timestamp>/` in the
   guest, owned by `lab`.
- The hostile container sees them read-only at `/incoming` — hostile
   code can read inputs but can never modify what was ingested or
   write back through that path.
- If the guest predates the ingest stack, `30-ensure-lab-tools.sh`
   runs automatically (it also creates `/srv/lab/incoming` and the
   `/incoming` bind mount).

## 5. Handling extracted artifacts

- Treat the tarball as **adversarial content**: don't execute, index,
  or casually open members; extract deliberately into a confined
  location and inspect names before unpacking.
- Treat it as **sensitive**: agent state may contain API keys,
  tokens, and prompts. Keep `0600`, out of synced/public dirs.
- `sha256sum -c <file>.sha256` proves transfer integrity — **not**
  authenticity of contents.

## 6. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `no export staged` | running VM, nothing prepared | run `labloop-export <paths>` or `sudo labloop-export --all` in the guest, re-run `90-` |
| `guest-agent not answering` | agent slow after wake | retry — post-boot window is transient |
| `guest returned unexpected staging path` | guest-side tampering | **investigate the guest** — never bypass |
| `refusing to overwrite` | artifact exists | new dest dir or move old file |
| `sha256 mismatch` | corrupt/tampered pull | rerun; if it repeats, investigate guest |
| `no such VM` | wrong name / not defined | `virsh -c qemu:///system list --all` |
| `lab-cnt-exp` fails `statfs /srv/lab/incoming` | old tooling, missing dir | `./30-ensure-lab-tools.sh <vm>` |

## 7. Files

| Path | Side | Role |
| --- | --- | --- |
| `deploy/labloop-export` | guest | staging tool — path mode + `--all` |
| `deploy/labloop-tmpfiles.conf` | guest | provisions `/var/lib/labloop-export` |
| `deploy/labloop.sudoers` | guest | arg-pinned sudo for `--all` |
| `90-extract-lab-data.sh` | host | pull staged artifacts |
| `80-ingest-lab-data.sh` | host | push files into `/srv/lab/incoming` |
| `30-ensure-lab-tools.sh` | host | deploy/repair guest tooling on any clone |
