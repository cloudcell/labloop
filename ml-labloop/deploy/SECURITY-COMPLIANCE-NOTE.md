# SECURITY-COMPLIANCE-NOTE — labloop vs. industry security standards

Companion to `SECURITY-MANUAL.md` (the binding contract). This note
records *where the design stands against industry practice* — which
conventions it meets or exceeds, which it deliberately does not, and
what the documented escalation path is for each gap.

Reference baselines: **NIST SP 800-190** (container security),
**CIS Docker Benchmark**, **NIST SP 800-53** (AC-6 least privilege,
AU-6 audit), and the practices of production agent-sandbox systems
(microVM platforms, code-execution sandboxes, sandbox runtimes).

## 1. Controls that meet or exceed the standard

| Control | Convention | Labloop implementation |
| ------- | ---------- | ---------------------- |
| Execution boundary | microVM-class isolation (Firecracker/Kata tier); a plain container is *not* a sandbox | KVM guest per user clone — strongest tier |
| Immutable image | Baked images; runtime mutation banned | `ReadOnly=true` rootfs; growth only via lanes |
| Privilege | CIS 5.x: drop caps, `no-new-privileges` | `DropCapability=all` + `NoNewPrivileges` |
| Resource limits | CIS 5.14/5.28 cgroup caps | 6 CPU / 16 GB / 512 PIDs — fork bombs contained |
| Rootless runtime | Rootless containers preferred | podman under `exp`/`mcp`, slirp4netns, keep-id |
| Zone separation | Dedicated uid per trust domain | `lab` / `mcp` / `exp` — separate stores, nets |
| Least-privilege exec | NIST AC-6: narrow, pinned channel | sudoers-scoped `labloop-exec`, args can't inject flags |
| Audit | NIST AU-6: tamper-resistant log | `logger` → root-owned journal; callers can't wipe |
| Secrets isolation | Never inside the untrusted zone | `mcp-state` mode 700, `mcp`-owned; OTP host-side |
| Image provenance | Declarative, reviewable dependency path | Containerfile edits + `labloop-build` only |
| Network policy | Egress restrictions on untrusted zones | owner-uid nftables drops: loopback + all private space |
| Continuous verification | Benchmark checks re-run, not assumed | `security-battery.sh` (24) + `functional-battery.sh` (17) — hard gates inside the build |
| Password hardening | Lockout against brute force | faillock: 10 fails → 15 min lockout |

## 2. Documented gaps vs. the strictest practice

These are **accepted trade-offs**, not oversights. Each has a
recorded reason and an escalation path if the threat model worsens.

| Gap | Strictest practice | Current status + rationale |
| --- | ------------------ | -------------------------- |
| Agent shares the `lab` uid | Dedicated unprivileged agent identity — the agent's uid cannot sudo at all | Weakest link. The sudo password lives host-side (OTP sidecar), but X11 keystroke sniffing means a typed password is exposed during an agent session. Escalation: split `lab`/`agent` uids, or sibling VMs with MCP-only reachability |
| Open public egress | Filtered proxy with domain allowlist (pypi, github, CTAN) | Accepted: `uv`/`micromamba`/`tlmgr`/datasets need broad egress. Nothing secret ever enters the hostile zone, so exfiltration has nothing to take. Escalation: Squid on the gateway + `--internal` on lab-net-exp |
| Base image by tag | `@sha256:` digest pinning (NIST 800-190 supply-chain) | uv 0.11.26 and micromamba 2.3.2 pinned by version tag; `python:3.12-slim` base not digest-pinned. Listed improvement |
| Loopback MCP, no bearer auth | Token or mTLS on the tool surface | Intended: ports publish on `127.0.0.1` only; the agent IS the driver, so any driver-zone process may call tools |
| No filtering inside `/exchange` | Content scanning on artifact handoff | Artifacts are labeled untrusted by contract; inspection is the driver's job — documented in the manual |

## 3. The residual risks that remain structural

- **VM escape** — inherent to any KVM boundary; mitigated by current
  QEMU/libvirt/kernel plus default sVirt confinement. Low probability,
  high effort.
- **Typed credentials in a live session** — X11 allows any client to
  observe keystrokes; treat the sudo password as exposed while an
  agent session runs. The OTP sidecar is host-side and unaffected.
- **Exfiltration channel** — open egress means hostile output can
  leave; the design counters this by ensuring nothing inside the
  hostile zone is worth stealing.

## 4. What a real compromise looks like (recovery posture)

| Compromise | Blast radius | Recovery |
| ---------- | ------------ | -------- |
| Hostile code escapes container | Still inside `exp` uid + fenced net, no secrets there | rebuild image, prune `/experiments` |
| `exp` compromises its own podman | Same — exp owns nothing else | same |
| Agent obtains guest root | The whole VM: trusted zone opens (`mcp-state` forgeable), nft editable, hostile boundary open | **destroy the clone** — recreate from template; that's why clones are disposable |
| Guest root attacks host | KVM boundary still holds; residual = QEMU device attack surface | host unaffected in practice; sVirt + updates are the mitigation |

## 5. Verification record

The claims above are not aspirational — they are exercised:

- `security-battery.sh` — 24 checks, hard gate at template build:
  zone isolation, rootfs immutability, caps, resource caps, exec
  channel scoping, nft fencing (behavioral probes: refused/timeout
  distinction, no false-PASS on dead ports), state unreadability,
  exchange traversal, loopback-only publish
- `functional-battery.sh` — 17 checks, hard gate at template build:
  exec channel, write+run, writable HOME, baked stack, uv venv
  install, pdflatex/pandoc compile, exchange handoff, micromamba,
  `/opt/local`, build channel, tool presence, MCP health
- `check-lab-ready.sh` — desktop one-click that runs both

Last verified in `lab-vm-pub-6`: security **24 PASS / 0 FAIL**,
functional **17 PASS / 0 FAIL** (as `lab`, not root — the battery
warns if run as root since permission checks become meaningless).
