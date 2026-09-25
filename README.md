# Lab Loop™ — a research automation engine for empirical work

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

`create-lab-template` finds the sibling `../ml-scientist` checkout
automatically (override with `ML_SCIENTIST=`). See `ml-labloop/README.md`
for the full workflow including publishing VM images.
