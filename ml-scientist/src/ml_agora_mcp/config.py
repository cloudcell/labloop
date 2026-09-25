"""Configuration — ml-agora.toml loading and adaptor wiring.

Config sources (priority order): CLI flags > environment > config file
(./ml-agora.toml or ~/.ml-agora/config.toml) > defaults.

The server depends on roles, not products — a *product* is a concrete
MCP server implementation filling a protocol role: [adaptors.loop0]
points at the Loop-0 server, [adaptors.claims] at the claims-memory
server, and so on. All four channels are optional — absent means
absent: an unwired server reports "not_configured" in the aggregate
rather than vanishing.

Agora is state-free: no domain database. ``db_dir`` exists solely for
the audit-log convention (~/.ml-agora/logs/check-*.jsonl).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "db_dir": str(Path.home() / ".ml-agora"),
    "transport": "stdio",
    "port": 38050,
    "host": "0.0.0.0",
}


def find_config_file(explicit_path: str | None = None) -> Path | None:
    """Find the config file to use.

    Priority: explicit path > ./ml-agora.toml > ~/.ml-agora/config.toml
    """
    if explicit_path:
        p = Path(explicit_path)
        return p if p.exists() else None

    for candidate in [
        Path.cwd() / "ml-agora.toml",
        Path.home() / ".ml-agora" / "config.toml",
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

    if env_dir := os.environ.get("ML_AGORA_DB_DIR"):
        config["db_dir"] = env_dir
    if env_port := os.environ.get("ML_AGORA_PORT"):
        config["port"] = int(env_port)
    if env_host := os.environ.get("ML_AGORA_HOST"):
        config["host"] = env_host
    if env_transport := os.environ.get("ML_AGORA_TRANSPORT"):
        config["transport"] = env_transport

    return config


def create_adaptors_from_config(config: dict[str, Any]):
    """Create upstream adaptors from config.

    Config format (TOML):

        [adaptors.claims]
        transport = "streamable-http"
        url = "http://localhost:38090/mcp"      # anamnesis

        [adaptors.loop0]
        transport = "streamable-http"
        url = "http://localhost:38080/mcp"      # ml-episteme

        [adaptors.loop1]
        transport = "streamable-http"
        url = "http://localhost:38070/mcp"      # ml-zetesis

        [adaptors.loop2]
        transport = "streamable-http"
        url = "http://localhost:38060/mcp"      # ml-arete

    Shared knobs under [adaptors]: `call_timeout_seconds` bounds one
    upstream read (per-channel override allowed); `reconnect_seconds`
    is the supervisor cadence (0 = one-shot connect).

    Returns an Adaptors container with *registered* channels — the
    live slots stay None until connect() succeeds (at startup or on a
    later supervisor retry).
    """
    from .clients.adaptors import Adaptors
    from .clients.mcp_client import MCPClientAdaptor

    adaptor_configs = config.get("adaptors", {})
    shared_timeout = adaptor_configs.get("call_timeout_seconds", 30)

    adaptors = Adaptors()
    for name, role in (
        ("claims", "claims"),
        ("loop0", "loop0"),
        ("loop1", "loop1"),
        ("loop2", "loop2"),
    ):
        if name in adaptor_configs:
            cfg = dict(adaptor_configs[name])
            cfg.setdefault("call_timeout_seconds", shared_timeout)
            adaptors.register_channel(
                name, role, MCPClientAdaptor(cfg)
            )
    return adaptors
