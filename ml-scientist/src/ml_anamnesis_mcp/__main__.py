"""Entry point for the anamnesis MCP server.

Run via stdio (for local MCP clients):

    uv run ml-anamnesis-mcp

Or via Streamable HTTP (for remote/hosted):

    uv run ml-anamnesis-mcp --transport http --port 38090

Defaults differ from ml-episteme by design: own port (38090), own
store (~/.ml-anamnesis/memory.db). ADR-0002: semantic memory is a
separate server, never a module of the loop.
"""

from __future__ import annotations

import argparse
import asyncio
import socket
import sys
from pathlib import Path

from .server import create_server
from .state.store import MemoryStore


def _check_port_available(host: str, port: int, label: str) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
    except OSError:
        print(
            f"ERROR: Port {port} is already in use ({label}).\n"
            f"  → Another ml-anamnesis-mcp process is likely still running.\n"
            f"  → Find it:  fuser {port}/tcp\n"
            f"  → Kill it: fuser -k {port}/tcp",
            file=sys.stderr,
        )
        return False
    return True


def _run_gracefully(coro):
    try:
        asyncio.run(coro)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nShutting down.", file=sys.stderr)
        sys.exit(0)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ml-anamnesis-mcp — cross-programme claims memory (semantic memory)",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport to use (default: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=38090,
        help="Port for HTTP transport (default: 38090)",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind for HTTP transport (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--db-path",
        default=str(Path.home() / ".ml-anamnesis" / "memory.db"),
        help="Path to the SQLite memory database (default: ~/.ml-anamnesis/memory.db)",
    )
    parser.add_argument(
        "--stateless",
        action="store_true",
        help="Use stateless HTTP mode (each request is independent, no session IDs)",
    )
    parser.add_argument(
        "--observability-port",
        type=int,
        default=None,
        help="Port for the read-only observability web GUI (default: disabled)",
    )
    parser.add_argument(
        "--log-tool-args",
        action="store_true",
        help="Log raw tool-call arguments at INFO before validation",
    )
    args = parser.parse_args()

    Path(args.db_path).parent.mkdir(parents=True, exist_ok=True)
    store = MemoryStore(args.db_path)
    store.connect()

    from .config import load_config

    file_config = load_config()
    integrity_config = file_config.get("integrity", {})
    # Recurrent protocol ships enabled (plan-20260926-0438Z) —
    # absent [enforcement] table defaults to on, matching the other
    # servers' DEFAULTS; recurrent_protocol=false disables.
    enforcement_config = file_config.get("enforcement") or {
        "recurrent_protocol": True,
    }

    # Lab-wide env overrides — shared names across all servers so a
    # single variable flips the whole stack.
    import os
    if env_rp := os.environ.get("ML_RECURRENT_PROTOCOL"):
        enforcement_config["recurrent_protocol"] = (
            env_rp.strip().lower() not in ("0", "false", "off", "no")
        )
    if env_fs := os.environ.get("ML_STATUS_FRESHNESS_SECONDS"):
        enforcement_config["status_freshness_seconds"] = float(env_fs)

    mcp = create_server(
        store,
        log_tool_args=args.log_tool_args,
        integrity_config=integrity_config,
        enforcement_config=enforcement_config,
    )

    async def _with_integrity_monitor(coro):
        """Startup sweep (proving the audit trail is live) + periodic
        sweeps — violations are recorded by default, not on request. A
        monitor failure must not block startup."""
        from .integrity.checks import start_integrity_monitor

        task = None
        try:
            task = start_integrity_monitor(store, config=integrity_config)
            interval = integrity_config.get("check_interval_seconds", 300)
            print(
                "integrity monitor: startup check logged"
                + (f"; periodic every {interval}s" if task else "")
            , file=sys.stderr)
        except Exception as e:
            print(
                f"WARNING: integrity monitor failed to start ({e})",
                file=sys.stderr,
            )
        try:
            await coro
        finally:
            if task is not None:
                task.cancel()

    # Read-only observability GUI (optional) — same store, no write path
    obs_server = None
    if args.observability_port:
        if not _check_port_available(
            args.host, args.observability_port, "observability GUI"
        ):
            sys.exit(1)
        import uvicorn
        from .observability.server import create_observability_app

        # The agora GUI base is deployment knowledge, not a code
        # constant: TOML [observability] agora_gui_url wins, else the
        # launcher-exported ML_AGORA_GUI_URL (ports.env-derived).
        obs_config = dict(file_config.get("observability", {}))
        if env_url := os.environ.get("ML_AGORA_GUI_URL"):
            obs_config.setdefault("agora_gui_url", env_url)
        # Peer GUI bases for claim-edge hyperlinks — same resolution:
        # TOML <server>_gui_url wins, else the ports.env-derived env.
        for peer in ("episteme", "zetesis", "arete"):
            env_key = f"ML_{peer.upper()}_GUI_URL"
            if env_url := os.environ.get(env_key):
                obs_config.setdefault(f"{peer}_gui_url", env_url)
        obs_app = create_observability_app(
            store,
            mcp_health_url=f"http://{args.host or '127.0.0.1'}:{args.port}/health",
            observability_config=obs_config,
        )
        obs_server = uvicorn.Server(
            uvicorn.Config(
                obs_app,
                host=args.host,
                port=args.observability_port,
                log_level="info",
            )
        )

    if args.transport == "stdio":
        if obs_server:
            async def run_stdio_with_obs():
                await asyncio.gather(
                    mcp.run_stdio_async(),
                    obs_server.serve(),
                )
            _run_gracefully(_with_integrity_monitor(run_stdio_with_obs()))
        else:
            _run_gracefully(_with_integrity_monitor(mcp.run_stdio_async()))
    else:
        if not _check_port_available(args.host, args.port, "anamnesis HTTP server"):
            sys.exit(1)
        if obs_server:
            async def run_both_http():
                await asyncio.gather(
                    mcp.run_streamable_http_async(
                        host=args.host,
                        port=args.port,
                        stateless_http=args.stateless,
                    ),
                    obs_server.serve(),
                )
            _run_gracefully(_with_integrity_monitor(run_both_http()))
        else:
            _run_gracefully(
                _with_integrity_monitor(
                    mcp.run_streamable_http_async(
                        host=args.host,
                        port=args.port,
                        stateless_http=args.stateless,
                    )
                )
            )


if __name__ == "__main__":
    main()
