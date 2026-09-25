# Lab VM — quick reference

A security-isolated ML experimentation lab. Three zones:

| Zone    | Who/what        | Where it lives |
|---------|-----------------|----------------|
| DRIVER  | you + the agent | desktop, `~/workspace`, VSCodium, opencode |
| TRUSTED | MCP services    | container `lab-cnt-mcp` — rootless under the `mcp` service account, loopback only; `lab` cannot read its state or admin it |
| HOSTILE | experiment code | container `lab-cnt-exp` (no net to MCP/host, read-only rootfs, 512 PID / 16 GiB caps) |

## Using the MCPs

Five servers run inside `lab-cnt-mcp`, published on loopback only:

| port | server | role |
|------|--------|------|
| 38050 | agora     | status hub (`lab://status`, GUI on :38051 — see Lab-Dashboard icon) |
| 38060 | arete     | optimizer (`ask`/`tell` studies) |
| 38070 | zetesis   | research/search |
| 38080 | episteme  | experiment executor |
| 38090 | anamnesis | claims memory |

opencode is pre-wired to them (`~/.config/opencode/opencode.json`). A good
first prompt: *"call lab://status and summarize what's healthy."*

Checks:

```bash
bash ~/Desktop/check-lab-ready.sh          # full check + security battery
curl -s 127.0.0.1:38050/health             # single-service ping
sudo -iu mcp                               # trusted-zone admin (password — human only)
systemctl --user status lab-cnt-mcp        #   … then inside that shell
sudo -u exp env HOME=/home/exp XDG_RUNTIME_DIR=/run/user/$(id -u exp) \
    systemctl --user status lab-cnt-exp    # hostile container
```

The trusted zone deliberately runs under the `mcp` service account:
`/srv/lab/mcp-state` (the scientific record + ingest token) is mode 700
`mcp:mcp` — unreadable from `lab`, and `lab`'s podman cannot see or stop
`lab-cnt-mcp`. Agents reach the servers only through the MCP endpoints.

## Running experiments

Draft under `~/workspace/experiments/<name>/`, promote to
`/srv/lab/experiments/`, execute through the exec channel:

```bash
sudo -u exp /usr/local/sbin/labloop-exec python3 /experiments/<name>/run.py
```

Results come back via `/srv/lab/exchange/`. Never `su exp` — it's a
locked service account.

## Upgrading

```bash
# opencode (global npm install — needs sudo; never RUN it as sudo)
sudo npm install -g @opencode/cli@<version>
opencode --version

# OS packages
sudo apt update && sudo apt upgrade

# uv (installed per-user, no sudo)
uv self update

# python tools
uv tool upgrade <tool>          # e.g. ruff
```

MCP servers are baked into the container images (`lab-img-mcp` /
`lab-img-exp`). Upgrade path: get a newer lab image from your provider,
or rebuild in the source repo (`ml-labloop/containers/`). Restarting the
containers picks up new quadlet/image content after a rebuild.

Rules of thumb:

- Upgrades that touch system paths (`/usr/local`, apt) need sudo.
- Upgrades under `~/` (`uv`, pipx, npm `--prefix ~/.local`) don't.
- Never run the agent/editor as root — it breaks file ownership in
  `~/workspace` and defeats the zone model.
