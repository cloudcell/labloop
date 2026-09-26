"""Configuration — environment-based and file-based config for downstream MCP endpoints.

The server depends on roles, not products — a *product* is a concrete
MCP server implementation filling a protocol role. This config maps
roles to product endpoints. Products are swappable; roles are not.

Config sources (in priority order):
1. CLI flags (highest)
2. Environment variables
3. Config file (ml-episteme.toml or ~/.ml-episteme/config.toml)
4. Defaults (lowest — stub adaptors)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .clients.adaptor import (
    MCPAdaptor,
    StubExecutor,
    StubOptimizer,
)


# --- Defaults ---


DEFAULTS: dict[str, Any] = {
    "db_path": str(Path.home() / ".ml-episteme" / "state.db"),
    "transport": "stdio",
    "port": 38080,
    "host": "0.0.0.0",
    # Recurrent protocol ships enabled (plan-20260926-0438Z):
    # mutating tools require a status digest read within the TTL;
    # violations gate writes until acknowledged. Set
    # recurrent_protocol=false or status_freshness_seconds=0 to
    # disable for debugging.
    "enforcement": {
        "recurrent_protocol": True,
        "status_freshness_seconds": 600,
    },
}


def find_config_file(explicit_path: str | None = None) -> Path | None:
    """Find the config file to use.

    Priority:
    1. Explicit path (from --config flag)
    2. ./ml-episteme.toml (current directory)
    3. ~/.ml-episteme/config.toml
    """
    if explicit_path:
        p = Path(explicit_path)
        if p.exists():
            return p
        return None

    for candidate in [
        Path.cwd() / "ml-episteme.toml",
        Path.home() / ".ml-episteme" / "config.toml",
    ]:
        if candidate.exists():
            return candidate

    return None


def load_config(config_path: str | None = None) -> dict[str, Any]:
    """Load configuration from file + environment.

    Returns a dict with keys:
        db_path, transport, port, host, adaptors
    """
    config = dict(DEFAULTS)

    # Load from file (TOML)
    path = find_config_file(config_path)
    if path is not None:
        try:
            import tomllib

            with open(path, "rb") as f:
                file_config = tomllib.load(f)
            config.update(file_config)
        except Exception:
            pass  # Ignore malformed config files

    # Environment overrides
    if env_db := os.environ.get("ML_EPISTEME_DB"):
        config["db_path"] = env_db
    if env_transport := os.environ.get("ML_EPISTEME_TRANSPORT"):
        config["transport"] = env_transport
    if env_port := os.environ.get("ML_EPISTEME_PORT"):
        config["port"] = int(env_port)
    if env_host := os.environ.get("ML_EPISTEME_HOST"):
        config["host"] = env_host
    if env_sandbox := os.environ.get("ML_EPISTEME_SANDBOX"):
        config.setdefault("executor", {})["sandbox"] = env_sandbox
    if env_trace := os.environ.get("ML_EPISTEME_TRACE_READS"):
        config.setdefault("executor", {})["trace_reads"] = env_trace
    if env_caches := os.environ.get("ML_EPISTEME_SHARED_CACHES"):
        config.setdefault("executor", {})["shared_caches"] = [
            p.strip() for p in env_caches.split(",") if p.strip()
        ]
    if env_timeout := os.environ.get("ML_EPISTEME_EXECUTOR_TIMEOUT"):
        config.setdefault("executor", {})["timeout_seconds"] = int(env_timeout)
    if env_python := os.environ.get("ML_EPISTEME_EXECUTOR_PYTHON"):
        config.setdefault("executor", {})["python_exe"] = env_python
    # Lab-wide protocol overrides (shared env names across all
    # servers so a single variable flips the whole stack).
    enf = config.setdefault("enforcement", {})
    if env_rp := os.environ.get("ML_RECURRENT_PROTOCOL"):
        enf["recurrent_protocol"] = env_rp.strip().lower() not in (
            "0", "false", "off", "no",
        )
    if env_fs := os.environ.get("ML_STATUS_FRESHNESS_SECONDS"):
        enf["status_freshness_seconds"] = float(env_fs)

    return config


def create_adaptor_from_config(config: dict[str, Any]) -> MCPAdaptor:
    """Create an adaptor from config.

    If adaptors are configured in the config file, use MCP-backed adaptors.
    Otherwise, use local adaptors (real implementations that run on the
    local machine — no external services needed).

    Config format (TOML):

        db_path = "/path/to/state.db"
        data_dir = "/path/to/data/storage"

        [adaptors.optimizer]
        transport = "stdio"
        command = "uv run some-optimizer-mcp"

        [adaptors.executor]
        transport = "streamable-http"
        url = "http://localhost:8888/mcp"

        [adaptors.data_source]
        transport = "stdio"
        command = "uv run some-data-source-mcp"

        [adaptors.claims]
        transport = "streamable-http"
        url = "http://localhost:38090/mcp"
    """
    adaptor_configs = config.get("adaptors", {})
    data_dir = config.get("data_dir")
    executor_cfg = config.get("executor", {})
    executor_sandbox = executor_cfg.get("sandbox", "auto")
    executor_trace = executor_cfg.get("trace_reads", "auto")
    executor_caches = executor_cfg.get("shared_caches", [])
    # Per-trial hard deadline — trials legitimately run for hours, so
    # this is an operator knob, not a code constant.
    executor_timeout = int(executor_cfg.get("timeout_seconds", 300))
    executor_python = executor_cfg.get("python_exe")

    if not adaptor_configs:
        # No adaptors configured — use local adaptors (real work, no external services)
        from .clients.adaptor import create_local_adaptor

        return create_local_adaptor(
            data_dir=data_dir, executor_sandbox=executor_sandbox,
            executor_trace=executor_trace,
            executor_shared_caches=executor_caches,
            executor_timeout=executor_timeout,
            executor_python=executor_python,
        )

    # MCP-backed adaptors configured — use them
    from .clients.adaptor import MCPAdaptor
    from .clients.local_executor import LocalExecutor
    from .clients.local_optimizer import LocalOptimizer

    adaptor = MCPAdaptor({})

    # Shared per-call bound for every MCP-backed role (per-channel
    # override allowed). [adaptors] reconnect_seconds is the
    # supervisor cadence, read in __main__.
    shared_timeout = adaptor_configs.get("call_timeout_seconds", 30)

    def _cfg(name: str) -> dict[str, Any]:
        cfg = dict(adaptor_configs[name])
        cfg.setdefault("call_timeout_seconds", shared_timeout)
        return cfg

    # Use MCP adaptors where configured, local adaptors as fallback
    if "optimizer" in adaptor_configs:
        from .clients.mcp_adaptor import MCPOptimizerAdaptor
        adaptor.set_optimizer(MCPOptimizerAdaptor(_cfg("optimizer")))
    else:
        adaptor.set_optimizer(LocalOptimizer())

    if "executor" in adaptor_configs:
        from .clients.mcp_adaptor import MCPExecutorAdaptor
        adaptor.set_executor(MCPExecutorAdaptor(_cfg("executor")))
    else:
        adaptor.set_executor(
            LocalExecutor(
                sandbox=executor_sandbox, trace_reads=executor_trace,
                shared_caches=executor_caches, timeout=executor_timeout,
                python_exe=executor_python,
            )
        )

    if "claims" in adaptor_configs:
        from .clients.mcp_adaptor import AnamnesisClaimsAdaptor
        adaptor.register_claims(
            AnamnesisClaimsAdaptor(_cfg("claims")), _cfg("claims")
        )
    # No local claims filler — absent means absent: episodic memory is
    # state.db (owned, not a role); semantic memory needs a peer server.

    if "data_source" in adaptor_configs:
        from .clients.mcp_adaptor import MCPDataSourceAdaptor
        adaptor.set_data_source(MCPDataSourceAdaptor(_cfg("data_source")))
    else:
        from .clients.local_data_handler import LocalDataHandler
        from pathlib import Path
        if data_dir is None:
            data_dir = Path.home() / ".ml-episteme" / "data"
        adaptor.set_data_source(LocalDataHandler(
            data_dir=data_dir, executor=LocalExecutor(
                sandbox=executor_sandbox, trace_reads=executor_trace,
                shared_caches=executor_caches, timeout=executor_timeout,
            )
        ))

    return adaptor
