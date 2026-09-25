"""Adaptor implementations — concrete implementations of the role interfaces.

Two adaptors are provided:

1. ``MCPAdaptor`` — connects to a real downstream MCP server via the MCP
   Client. Used in production. The only place where product names appear
   (in the configuration, not in the code).

2. ``StubAdaptor`` — returns canned data. Used in tests and Phase 3
   fallback. No downstream MCP server required.

The server never imports a specific product. It receives role instances
from the adaptor layer and calls the role interface.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from .roles import ClaimsRole, DataSourceRole, ExecutorRole, OptimizerRole


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- Stub adaptors (for tests and Phase 3 fallback) ---


class StubOptimizer(OptimizerRole):
    """Stub optimizer that returns canned configurations."""

    def __init__(self):
        self._studies: dict[str, list[str]] = {}
        self._results: dict[str, list[dict[str, float]]] = {}
        self._call_count = 0

    async def create_study(
        self, programme_id: str, variables: list[str], direction: str = "maximize"
    ) -> str:
        self._studies[programme_id] = variables
        self._results[programme_id] = []
        return f"study-{programme_id}"

    async def ask(self, programme_id: str) -> dict[str, Any]:
        self._call_count += 1
        variables = self._studies.get(programme_id, ["learning_rate"])
        # Return a simple config: vary the first variable
        config = {v: 0.001 * (self._call_count if v == "learning_rate" else 1) for v in variables}
        config["_trial_number"] = self._call_count
        return config

    async def tell(self, programme_id: str, trial_id: str, result: dict[str, float]) -> None:
        self._results.setdefault(programme_id, []).append(result)

    async def best_trials(
        self, programme_id: str, direction: str | None = None
    ) -> list[dict[str, Any]]:
        results = self._results.get(programme_id, [])
        if not results:
            return []
        # Return the best by first metric
        best = max(results, key=lambda r: list(r.values())[0] if r else 0)
        return [{"config": {}, "result": best}]

    async def param_importance(self, programme_id: str) -> dict[str, float]:
        variables = self._studies.get(programme_id, ["learning_rate"])
        return {v: 1.0 / len(variables) for v in variables}


class StubExecutor(ExecutorRole):
    """Stub executor that returns canned output."""

    async def execute_code(
        self,
        code: str,
        artifact_dir: Any = None,
        trial_id: str | None = None,
        programme_id: str | None = None,
        bundle_id: str | None = None,
        extra_ro_paths: list[str] | None = None,
        extra_rw_paths: list[str] | None = None,
        python_exe: str | None = None,
        overlay_ro: list[tuple[str, str]] | None = None,
    ) -> str:
        return json.dumps({"status": "completed", "output": "stub execution succeeded"})

    async def read_cell_output(self, cell_id: str) -> str:
        return json.dumps({"cell_id": cell_id, "output": "stub output"})


class StubDataHandler(DataSourceRole):
    """Stub data handler that returns canned DataRef IDs."""

    def __init__(self):
        self._refs: dict[str, dict[str, Any]] = {}

    async def prepare_data(
        self,
        split: str,
        regime: str,
        generator_code_ref: str | None = None,
        generator_seed: int | None = None,
        generator_params: dict[str, Any] | None = None,
        source_uri: str | None = None,
        version: str | None = None,
        capture_window_start: str | None = None,
        capture_window_end: str | None = None,
        capture_source_metadata: dict[str, Any] | None = None,
    ) -> str:
        import uuid
        data_ref_id = f"data-ref-{uuid.uuid4().hex[:8]}"
        self._refs[data_ref_id] = {
            "id": data_ref_id,
            "split": split,
            "regime": regime,
            "generator_code_ref": generator_code_ref,
            "generator_seed": generator_seed,
            "generator_params": generator_params,
            "source_uri": source_uri,
            "version": version,
            "capture_window_start": capture_window_start,
            "capture_window_end": capture_window_end,
            "capture_source_metadata": capture_source_metadata,
            "content_hash": None,
            "storage_uri": None,
            "reproducibility_risk": "none" if regime == "generated" else "external-dependency",
        }
        return data_ref_id

    async def get_data_ref(self, data_ref_id: str) -> dict[str, Any]:
        return self._refs.get(data_ref_id, {"error": f"DataRef not found: {data_ref_id}"})

    async def verify_data(self, data_ref_id: str) -> dict[str, Any]:
        ref = self._refs.get(data_ref_id)
        if ref is None:
            return {"error": f"DataRef not found: {data_ref_id}"}
        return {
            "data_ref_id": data_ref_id,
            "verified": True,
            "recorded_hash": ref.get("content_hash"),
            "computed_hash": ref.get("content_hash"),
        }

    async def resolve_data_path(self, data_ref_id: str) -> str:
        ref = self._refs.get(data_ref_id)
        if ref is None:
            raise FileNotFoundError(f"DataRef not found: {data_ref_id}")
        return ref.get("storage_uri", "/tmp/stub_data")


# --- MCP adaptor (for production — connects to real downstream MCPs) ---


class MCPAdaptor:
    """Connects to downstream MCP servers via the MCP Client.

    This is the only place where product names appear — in the configuration
    that specifies which MCP server fills each role. The adaptor translates
    role-level calls into MCP tool calls on the downstream server.

    Configuration example:

        adaptors:
          optimizer:
            transport: stdio
            command: uv run <optimizer-mcp>
          executor:
            transport: streamable-http
            url: http://localhost:8888/mcp
          claims:
            transport: streamable-http
            url: http://localhost:38090/mcp
    """

    def __init__(self, config: dict[str, dict[str, Any]]):
        self.config = config
        self._optimizer: OptimizerRole | None = None
        self._executor: ExecutorRole | None = None
        self._claims: ClaimsRole | None = None
        self._data_source: DataSourceRole | None = None
        # Configured claims channel — survives a dead peer so the
        # connectivity supervisor can keep retrying. The live slot
        # (``_claims``) stays None until a connect succeeds.
        self._claims_channel: dict[str, Any] | None = None

    @property
    def optimizer(self) -> OptimizerRole:
        if self._optimizer is None:
            raise RuntimeError("Optimizer role not connected")
        return self._optimizer

    @property
    def executor(self) -> ExecutorRole:
        if self._executor is None:
            raise RuntimeError("Executor role not connected")
        return self._executor

    @property
    def claims(self) -> ClaimsRole | None:
        """Semantic memory role — None when no claims adaptor is wired.

        Absent means absent: claims are optional infrastructure, never
        a gate (ADR-0005). Callers must handle None.
        """
        return self._claims

    @property
    def data_source(self) -> DataSourceRole:
        if self._data_source is None:
            raise RuntimeError("Data source role not connected")
        return self._data_source

    def set_optimizer(self, opt: OptimizerRole) -> None:
        """Inject an optimizer (for testing or manual wiring)."""
        self._optimizer = opt

    def set_executor(self, exe: ExecutorRole) -> None:
        """Inject an executor (for testing or manual wiring)."""
        self._executor = exe

    def set_claims(self, claims: ClaimsRole | None) -> None:
        """Inject a claims role (for testing or manual wiring)."""
        self._claims = claims

    def set_data_source(self, ds: DataSourceRole) -> None:
        """Inject a data source (for testing or manual wiring)."""
        self._data_source = ds

    def register_claims(self, role: ClaimsRole, cfg: dict[str, Any]) -> None:
        """Register the configured claims channel.

        Does NOT set the live slot — ``_claims`` is assigned only when
        connect() succeeds (at startup or on a supervisor retry), so a
        failed peer leaves the channel registered-but-down rather than
        permanently absent.
        """
        target = cfg.get("url") or f"stdio:{cfg.get('command', '?')}"
        self._claims_channel = {
            "role_obj": role,
            "target": target,
            "state": "down",
            "attempts": 0,
            "last_error": None,
            "connected_at": None,
        }

    def connectivity_report(self) -> list[dict[str, Any]]:
        """Snapshot of who is connected in what role — the integrity
        layer's ``upstream_connectivity`` input. Local roles are
        reported as wired fillers; only the claims channel is a
        retryable upstream connection."""
        report = []
        for name, role in (
            ("optimizer", self._optimizer),
            ("executor", self._executor),
            ("data_source", self._data_source),
        ):
            cfg = getattr(role, "config", None) or {}
            target = cfg.get("url") or (
                f"stdio:{cfg['command']}" if "command" in cfg else "local"
            )
            dead = getattr(role, "session_dead", None)
            report.append({
                "channel": name,
                "role": name,
                "target": target,
                "state": (
                    "unwired" if role is None
                    else "down" if dead is not None and dead()
                    else "up"
                ),
                "attempts": None,
                "last_error": None,
                "connected_at": None,
            })
        ch = self._claims_channel
        if ch is not None:
            live = self._claims
            dead = getattr(live, "session_dead", None)
            up = live is not None and not (dead and dead())
            report.append({
                "channel": "claims",
                "role": "claims",
                "target": ch["target"],
                "state": "up" if up else "down",
                "attempts": ch["attempts"],
                "last_error": ch["last_error"],
                "connected_at": ch["connected_at"],
            })
        return report


def create_stub_adaptor() -> MCPAdaptor:
    """Create an adaptor with stub implementations of the local roles.

    Used in tests. Claims has no stub — absent means absent (ADR-0005).
    """
    adaptor = MCPAdaptor({})
    adaptor.set_optimizer(StubOptimizer())
    adaptor.set_executor(StubExecutor())
    adaptor.set_data_source(StubDataHandler())
    return adaptor


def create_local_adaptor(
    executor_timeout: int = 300,
    optimizer_seed: int | None = None,
    data_dir: str | None = None,
    executor_sandbox: str = "auto",
    executor_trace: str = "auto",
    executor_shared_caches: list[str] | None = None,
    executor_python: str | None = None,
) -> MCPAdaptor:
    """Create an adaptor with local implementations for all roles.

    Local adaptors do real work on the local machine:
    - LocalOptimizer: random search HPO with best-trial tracking
    - LocalExecutor: runs Python code in a subprocess
    - LocalDataHandler: local filesystem data storage with hashing

    Episodic memory is not a role — state.db IS the loop's episodic
    memory (ADR-0005). Semantic memory (ClaimsRole) has no local filler;
    wire anamnesis via [adaptors.claims] to enable it.

    No external services needed. For production, swap individual roles
    for MCP-backed adaptors via configuration.

    Args:
        executor_timeout: Max execution time in seconds.
        optimizer_seed: Random seed for reproducible HPO (default: random).
        data_dir: Root directory for data storage (default: ~/.ml-episteme/data).
        executor_sandbox: Trial subprocess isolation — "auto" (bwrap
            minimal if available), "none", "minimal" (private /tmp), or
            "full" (read-only root, artifact dir only writable mount).
        executor_trace: Execution-time read capture — "auto" (strace if
            available), "on", or "off".
        executor_shared_caches: Additional host cache dirs bind-mounted
            rw at their real paths under sandbox="full" (e.g.
            ~/.cache/pip, ~/.cache/uv). Missing dirs are skipped and
            reported in executor_output.shared_caches.missing.
        executor_python: Default trial interpreter (default: the
            server's own sys.executable). Deployments can point this at
            a dedicated env carrying the scientific stack so trials
            don't degrade to dependency-free code.
    """
    from .local_data_handler import LocalDataHandler
    from .local_executor import LocalExecutor
    from .local_optimizer import LocalOptimizer

    executor = LocalExecutor(
        timeout=executor_timeout,
        sandbox=executor_sandbox,
        trace_reads=executor_trace,
        shared_caches=executor_shared_caches,
        python_exe=executor_python,
    )

    if data_dir is None:
        from pathlib import Path
        # Default: ~/.ml-episteme/data (alongside the default state DB).
        # Never use the project folder — data must not live in code storage.
        data_dir = Path.home() / ".ml-episteme" / "data"

    adaptor = MCPAdaptor({})
    adaptor.set_optimizer(LocalOptimizer(seed=optimizer_seed))
    adaptor.set_executor(executor)
    adaptor.set_data_source(LocalDataHandler(data_dir=data_dir, executor=executor))
    return adaptor


async def run_claims_supervisor(
    adaptor: MCPAdaptor,
    interval_seconds: float,
    *,
    log=print,
) -> None:
    """Keep the configured claims channel connected — the convergence
    path that makes run-ml-*.sh launch order irrelevant.

    Each tick: a live claims role whose session died (the peer exiting
    kills the client task — passive detection, no probing) is dropped
    back to registered-but-down; a down channel gets a fresh
    ``connect()``. Runs until cancelled.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        ch = adaptor._claims_channel
        if ch is None:
            continue
        live = adaptor._claims
        if live is not None:
            dead = getattr(live, "session_dead", None)
            if dead is not None and dead():
                try:
                    await live.disconnect()
                except Exception:
                    pass
                adaptor.set_claims(None)
                ch["state"] = "down"
                ch["last_error"] = "upstream session ended"
                log("WARNING: claims channel lost — will retry")
            continue
        ch["attempts"] += 1
        try:
            await ch["role_obj"].connect()
        except Exception as e:
            ch["state"] = "down"
            ch["last_error"] = str(e)
            continue
        adaptor.set_claims(ch["role_obj"])
        ch["state"] = "up"
        ch["connected_at"] = _utc_now()
        ch["last_error"] = None
        log("claims adaptor connected")
