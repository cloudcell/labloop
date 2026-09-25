"""Configuration — ml-zetesis.toml loading and adaptor wiring.

Config sources (priority order): CLI flags > environment > config file
(./ml-zetesis.toml or ~/.ml-zetesis/config.toml) > defaults.

The server depends on roles, not products — a *product* is a concrete
MCP server implementation filling a protocol role:
[adaptors.evidence] points at the Loop-0 server, [adaptors.claims] at
the claims-memory server.
Both are optional — absent means absent (no stubs): pulls against an
unwired source fail clearly; minting reports 'disabled'.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "db_path": str(Path.home() / ".ml-zetesis" / "search.db"),
    "transport": "stdio",
    "port": 38070,
    "host": "0.0.0.0",
}


def find_config_file(explicit_path: str | None = None) -> Path | None:
    """Find the config file to use.

    Priority: explicit path > ./ml-zetesis.toml > ~/.ml-zetesis/config.toml
    """
    if explicit_path:
        p = Path(explicit_path)
        return p if p.exists() else None

    for candidate in [
        Path.cwd() / "ml-zetesis.toml",
        Path.home() / ".ml-zetesis" / "config.toml",
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

    if env_db := os.environ.get("ML_ZETESIS_DB"):
        config["db_path"] = env_db
    if env_port := os.environ.get("ML_ZETESIS_PORT"):
        config["port"] = int(env_port)
    if env_host := os.environ.get("ML_ZETESIS_HOST"):
        config["host"] = env_host
    if env_transport := os.environ.get("ML_ZETESIS_TRANSPORT"):
        config["transport"] = env_transport

    return config


def create_adaptors_from_config(config: dict[str, Any]):
    """Create upstream adaptors from config.

    Config format (TOML):

        [adaptors.evidence]
        transport = "streamable-http"
        url = "http://localhost:38080/mcp"      # ml-episteme (Loop 0)

        [adaptors.promotion]
        transport = "streamable-http"
        url = "http://localhost:38080/mcp"      # ml-episteme (Loop 0)
                                               # — write channel, same
                                               #   endpoint, separate
                                               #   role

        [adaptors.claims]
        transport = "streamable-http"
        url = "http://localhost:38090/mcp"     # anamnesis

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
        EvidenceAdaptor,
        PromotionAdaptor,
    )

    adaptor_configs = config.get("adaptors", {})
    shared_timeout = adaptor_configs.get("call_timeout_seconds", 30)

    def _cfg(name: str) -> dict[str, Any]:
        cfg = dict(adaptor_configs[name])
        cfg.setdefault("call_timeout_seconds", shared_timeout)
        return cfg

    adaptors = Adaptors()
    if "evidence" in adaptor_configs:
        adaptors.register_channel(
            "evidence", "loop0-read", EvidenceAdaptor(_cfg("evidence"))
        )
    if "promotion" in adaptor_configs:
        adaptors.register_channel(
            "promotion", "loop0-write",
            PromotionAdaptor(_cfg("promotion")),
        )
    if "claims" in adaptor_configs:
        adaptors.register_channel(
            "claims", "claims", ClaimsAdaptor(_cfg("claims"))
        )
    return adaptors
