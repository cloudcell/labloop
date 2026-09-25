"""Configuration — ml-arete.toml loading and adaptor wiring.

Config sources (priority order): CLI flags > environment > config file
(./ml-arete.toml or ~/.ml-arete/config.toml) > defaults.

The server depends on roles, not products — a *product* is a concrete
MCP server implementation filling a protocol role:
[adaptors.loop0] points at the Loop-0 experiment server,
[adaptors.loop1] at the Loop-1 search server, [adaptors.claims] at
the claims-memory server. All optional —
absent means absent (no stubs): pulls against an unwired source fail
clearly; minting reports 'disabled'.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "db_path": str(Path.home() / ".ml-arete" / "improver.db"),
    "transport": "stdio",
    "port": 38060,
    "host": "0.0.0.0",
}


def find_config_file(explicit_path: str | None = None) -> Path | None:
    """Find the config file to use.

    Priority: explicit path > ./ml-arete.toml > ~/.ml-arete/config.toml
    """
    if explicit_path:
        p = Path(explicit_path)
        return p if p.exists() else None

    for candidate in [
        Path.cwd() / "ml-arete.toml",
        Path.home() / ".ml-arete" / "config.toml",
    ]:
        if candidate.exists():
            return candidate
    return None


def load_config(config_path: str | None = None) -> dict[str, Any]:
    """Load configuration from file + environment."""
    config = dict(DEFAULTS)

    path = find_config_file(config_path)
    if path is not None:
        try:
            import tomllib

            with open(path, "rb") as f:
                config.update(tomllib.load(f))
        except Exception:
            pass  # Ignore malformed config files

    if env_db := os.environ.get("ML_ARETE_DB"):
        config["db_path"] = env_db
    if env_port := os.environ.get("ML_ARETE_PORT"):
        config["port"] = int(env_port)
    if env_host := os.environ.get("ML_ARETE_HOST"):
        config["host"] = env_host
    if env_transport := os.environ.get("ML_ARETE_TRANSPORT"):
        config["transport"] = env_transport

    return config


def create_adaptors_from_config(config: dict[str, Any]):
    """Create upstream adaptors from config.

    Config format (TOML):

        [adaptors.loop0]
        transport = "streamable-http"
        url = "http://localhost:38080/mcp"        # ml-episteme (Loop 0)

        [adaptors.loop1]
        transport = "streamable-http"
        url = "http://localhost:38070/mcp"       # ml-zetesis (Loop 1)

        [adaptors.loop1_orchestration]
        transport = "streamable-http"
        url = "http://localhost:38070/mcp"       # ml-zetesis — write channel

        [adaptors.claims]
        transport = "streamable-http"
        url = "http://localhost:38090/mcp"       # anamnesis

    Shared knobs under [adaptors]: `call_timeout_seconds` bounds one
    upstream call (per-channel override allowed); `reconnect_seconds`
    is the supervisor cadence (0 = one-shot connect, pre-supervisor
    behaviour).

    Returns an Adaptors container with *registered* channels — the
    live slots stay None until connect() succeeds (at startup or on a
    later supervisor retry).
    """
    from .clients.adaptors import (
        Adaptors,
        ClaimsAdaptor,
        Loop0Adaptor,
        Loop1Adaptor,
        Loop1OrchestrationAdaptor,
    )

    adaptor_configs = config.get("adaptors", {})
    shared_timeout = adaptor_configs.get("call_timeout_seconds", 30)

    def _cfg(name: str) -> dict[str, Any]:
        cfg = dict(adaptor_configs[name])
        cfg.setdefault("call_timeout_seconds", shared_timeout)
        return cfg

    adaptors = Adaptors()
    if "loop0" in adaptor_configs:
        adaptors.register_channel(
            "loop0", "loop0-read", Loop0Adaptor(_cfg("loop0"))
        )
    if "loop1" in adaptor_configs:
        adaptors.register_channel(
            "loop1", "loop1-read", Loop1Adaptor(_cfg("loop1"))
        )
    if "loop1_orchestration" in adaptor_configs:
        adaptors.register_channel(
            "loop1_orchestration", "loop1-orchestration",
            Loop1OrchestrationAdaptor(_cfg("loop1_orchestration"))
        )
    if "claims" in adaptor_configs:
        adaptors.register_channel(
            "claims", "claims", ClaimsAdaptor(_cfg("claims"))
        )
    return adaptors
