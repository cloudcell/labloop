# Lab Loop™

*A research automation engine for empirical work.*

Hypotheses, designed experiments, evidence and conclusions as
durable state — built for researchers, applications and AI agents
across any empirical domain.

Lab Loop treats the empirical research loop as a first-class object
that survives across tools, teams and compute — independent of any
single notebook, dashboard or agent:

```
hypothesis → designed experiment → execution → observation
→ statistical analysis → belief update → next experiment
```

**01 — Falsifiable hypotheses.** Every claim declares, up front,
the observation that would force its rejection.

**02 — Controlled experiments.** Auxiliary assumptions are made
explicit and held fixed across the comparison a result claims to
make.

**03 — Reproducible evidence.** Code, environment, seeds and splits
are captured with every result, alongside reported variance.

**04 — Agent integration.** Agents and assistants drive the loop
through bounded operations without owning the evidence.

This repository implements Lab Loop as a set of MCP servers. Any
MCP-compatible LLM client can drive experiments through it; the
system records every step — hypotheses, trials, observations,
belief updates, and conclusions — as queryable, provenance-tracked
state.

## The commitments

Lab Loop is built around eleven load-bearing commitments. They are
rules the system enforces structurally, not best practices the
scientist is asked to remember:

1. **The loop is the unit.** A run not tied to a hypothesis, a
   design, and a conclusion is telemetry, not science. Isolated
   runs are refused.
2. **Memory precedes optimization.** Experimental memory is the
   substrate optimization and belief-updating operate on — durable
   and queryable, not a report bolted on after the fact.
3. **Falsifiability is required for admission.** A hypothesis
   enters the loop only with a pre-declared criterion of failure;
   experiments that cannot be falsified are rejected at design time.
4. **Constraints are first-class.** Compute, memory, time and
   parameter budgets are part of the hypothesis, not filters
   applied after the search.
5. **Budget is an epistemic resource.** Spending a trial is
   spending a unit of belief-reduction; the marginal value of the
   next trial is visible, so "stop" and "keep going" are explicit
   decisions.
6. **The bundle must be controlled.** Data, code, environment,
   seeds, splits and baselines are captured and held fixed across
   every comparison a result claims to make.
7. **Reproducibility is the price of admission.** Bundles are
   replayable by default, and results report variance — a single
   point estimate is a story, not a result.
8. **Programmes, not runs.** Experiments are grouped into research
   programmes with visible health — progressive or degenerating —
   instead of a flat run log that hides patching.
9. **Belief is a tracked quantity.** Each programme carries an
   explicit belief state; an experiment that cannot move it is not
   worth its budget.
10. **The agent is a scientist, not a scribe.** The agent's
    judgment goes to hypotheses, evidence and belief — the
    mechanical stages are made rigorous by the system so cognition
    is never spent on plumbing.
11. **Security is above all.** The loop runs code it did not write.
    Execution fails closed when a control is missing, only sealed
    bytes execute, and every applied control — or admitted absence —
    is part of the permanent record. This commitment outranks all
    the others.

## The five servers

| Server | Port | GUI | Role |
|---|---|---|---|
| `ml-agora-mcp` | 38050 | 38051 | Lab status hub — read-only aggregation of all four channels (`lab://status`, `lab://topology`) |
| `ml-arete-mcp` | 38060 | 38061 | Loop 2 — the recursive loop: tournaments between improvers, meta-decisions about the protocol itself |
| `ml-zetesis-mcp` | 38070 | 38071 | Loop 1 — the search loop: open investigations, pull evidence, record findings |
| `ml-episteme-mcp` | 38080 | 38081 | Loop 0 — the experiment loop: programmes, hypotheses, sandboxed trial execution, belief updates |
| `ml-anamnesis-mcp` | 38090 | 38091 | Semantic memory — the claims store; claims are minted automatically when a hypothesis concludes |

All ports live in `ports.env` — edit that file to move them, never
the scripts. Episteme also has an optional artifact-ingest listener
(38082) that is enabled iff `ML_EPISTEME_INGEST_TOKEN` is set in
`ingest.env`; without a token the surface is absent (fail closed).

## Install and run

Requires Python 3.12+ and [`uv`](https://docs.astral.sh/uv/):

```bash
uv sync --frozen
```

Start everything in dependency order (upstreams first, agora last):

```bash
./labloop start all     # backgrounds each server; waits on /health
./labloop status        # managed/unmanaged per server
./labloop logs episteme # tail one server
./labloop stop all
```

Or run a single server in the foreground:

```bash
./run-ml-episteme.sh    # also: agora, anamnesis, arete, zetesis
```

Each server logs to `~/.ml-<name>/logs/server.log`, pidfile at
`~/.ml-<name>/run/labloop.pid`. A server started outside `labloop`
shows as `unmanaged` in status; `stop` still terminates it by port.

**Trial sandboxing:** episteme executes trial code under bubblewrap
(`executor.sandbox = "full"` by default — read-only root, only the
trial's artifact dir writable). If `bwrap` is missing, trials FAIL
rather than degrade; `strace` powers executed-code capture. Install
both on Linux (`apt install bubblewrap strace`) or set
`sandbox = "none"` in `ml-episteme.toml` to opt out explicitly.

## Connecting a client

Every server speaks streamable-HTTP MCP at:

```
http://localhost:<port>/mcp
```

Point any MCP client (opencode, Claude Code, etc.) at that URL. The
GUI dashboards are plain HTTP on the `<port>+1` observability ports.

### Session-start convention

Each server serves a machine-readable digest at `X://status`
(`protocol://status`, `search://status`, `improver://status`,
`claims://status`), embeds it under `status` in `X://session`, and
renders it through a `status_report` prompt. Agora aggregates all
four into `lab://status`. Recommended workflow:

1. Call **agora**'s `status_report` prompt for lab triage.
2. Call the owning server's `status_report` for detail.
3. Call that server's tools directly.

## The workflow, by loop

### Loop 0 — episteme (experiments)

The canonical recorded-evidence sequence:

```
create_programme          # open a research programme (budget, scope)
formulate_hypothesis      # falsifiable statement under the programme
prepare_data              # register a dataset; verify_data checks it
design_experiment         # binds hypothesis + data + code_ref → trial
capture_bundle            # seal the code (code_ref resolves in-server)
run_trial                 # execute under the bubblewrap sandbox
record_observation        # measured outcomes land as state
update_belief             # posterior over the hypothesis
conclude_hypothesis       # accept/reject; mints a claim into anamnesis
```

Supporting tools: `list_hypotheses`, `get_trial_status`,
`cancel_trial`, `mark_retryable`, `correct_trial_status`,
`get_next_experiment`, `register_candidate` / `get_candidate_scorecard`
(tournament candidates), `create_evaluation_contract`, and the
archive family (`archive_pending_programmes`, `get_archive`,
`verify_archive`).

`design_experiment` may be called in parallel safely — hypothesis
promotion to `under_test` is atomic; concurrent callers get
idempotent results, never a partial failure with a created trial.

### Loop 1 — zetesis (search)

```
open_investigation → pull_evidence → record_finding
→ conclude_investigation
```

Findings can be promoted into the experiment loop (`promotion.py`).

### Loop 2 — arete (recursion)

Tournaments between improvers — the loop that changes the protocol:

```
open_tournament → register_improver → propose_meta_change
→ create_meta_contract → record_tournament_result
→ record_meta_decision → close_tournament
```

### Memory — anamnesis (claims)

`assert_claim`, `relate`, `get_claim`, `list_claims`. Normally
populated automatically — `conclude_hypothesis` mints a claim as a
byproduct when `[adaptors.claims]` is wired (it is, by default).

## Configuration

- `ports.env` — all ports in one place.
- `ml-<name>.toml` — per-server config: `[adaptors.<channel>]` wire
  upstreams (`transport`, `url`, `reconnect_seconds`,
  `call_timeout_seconds`). Unconfigured means absent (supported
  standalone mode); configured-but-down is retried by the
  connectivity supervisor and reported via `upstream_connectivity`.
- `ingest.env` — `ML_EPISTEME_INGEST_TOKEN` (gitignored; never
  commit). Enables the artifact-ingest surface.
- `ML_EPISTEME_EXECUTOR_PYTHON` — interpreter episteme's local
  executor uses for trials (defaults to the server's own). Point it
  at an env with your scientific stack (numpy/scipy/sklearn) — see
  `ml-episteme.toml` `[executor]`.

## Tests

```bash
uv run pytest -q        # parallel (pytest-xdist)
uv run pytest -n0 -q    # serial fallback
```

## Inside the labloop VM

This repo is also staged into the ml-labloop KVM lab, where the
servers run in a trusted-zone container (`lab-cnt-mcp`) with the
trial interpreter at `/opt/trial-env` (numpy/scipy/sklearn/
matplotlib/pandas baked in). There, agent-facing usage is governed
by `GENESIS-RESEARCH-PROMPT.md` — recorded evidence goes through
episteme's executor; `labloop-exec` is only for hostile-zone
iteration. See the ml-labloop repo for the VM side.

## License

Apache License 2.0. See [LICENSE](LICENSE).
