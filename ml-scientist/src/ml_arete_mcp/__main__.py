"""Entry point for the arete MCP server (Loop 2 — recursive loop).

Run via stdio (for local MCP clients):

    uv run ml-arete-mcp

Or via Streamable HTTP (for remote/hosted):

    uv run ml-arete-mcp --transport http --port 38060

Own port (38060), own store (~/.ml-arete/improver.db). ADR-0001: one
MCP server per loop. Upstream adaptors ([adaptors.loop0],
[adaptors.loop1], [adaptors.claims] in ml-arete.toml) connect on the
server event loop — a failed connect disables that channel; the
server still runs.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
import sys
from pathlib import Path

from .server import create_server
from .state.store import ImproverStore


def _check_port_available(host: str, port: int, label: str) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
    except OSError:
        print(
            f"ERROR: Port {port} is already in use ({label}).\n"
            f"  → Another ml-arete-mcp process is likely still running.\n"
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
    from .config import create_adaptors_from_config, load_config

    parser = argparse.ArgumentParser(
        description="ml-arete-mcp — Loop 2 recursive loop (improver)",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default=None,
        help="Transport to use (default: config or stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port for HTTP transport (default: config or 38060)",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Host to bind for HTTP transport (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to the SQLite improver database "
        "(default: ~/.ml-arete/improver.db)",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to ml-arete.toml",
    )
    parser.add_argument(
        "--stateless",
        action="store_true",
        help="Use stateless HTTP mode (each request is independent, "
        "no session IDs)",
    )
    parser.add_argument(
        "--observability-port",
        type=int,
        default=None,
        help="Port for the read-only observability web GUI "
        "(default: disabled)",
    )
    parser.add_argument(
        "--log-tool-args",
        action="store_true",
        help="Log raw tool-call arguments at INFO before validation",
    )
    args = parser.parse_args()

    config = load_config(args.config)

    transport = args.transport or config["transport"]
    port = args.port or config["port"]
    host = args.host or config["host"]
    db_path = args.db_path or config["db_path"]

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    store = ImproverStore(db_path)
    store.connect()

    adaptors = create_adaptors_from_config(config)

    mcp = create_server(
        store,
        adaptors=adaptors,
        log_tool_args=args.log_tool_args,
        integrity_config=config.get("integrity", {}),
        enforcement_config=config.get("enforcement", {}),
    )

    async def _run_server(coro):
        """Connect upstream adaptors on the server loop, run, disconnect.

        MCP client sessions must live on the server's event loop, so
        connect() runs here — not before asyncio.run. An adaptor that
        fails to connect stays registered and the connectivity
        supervisor keeps retrying (absent means absent, but transient);
        the server still serves its own state and the failure is loud.
        """
        labels = {
            "loop0": "loop0 (ml-episteme)",
            "loop1": "loop1 (ml-zetesis)",
            "loop1_orchestration":
                "loop1_orchestration (ml-zetesis, write)",
            "claims": "claims (anamnesis)",
        }
        for name, label in labels.items():
            spec = adaptors._channels.get(name)
            if spec is None:
                print(f"{label} adaptor not configured — pulls to that "
                      f"source will fail", file=sys.stderr)
                continue
            spec.attempts += 1
            try:
                await spec.adaptor.connect()
            except Exception as e:
                spec.mark_down(str(e))
                print(f"WARNING: {label} adaptor unreachable ({e}) — "
                      f"the supervisor will keep retrying",
                      file=sys.stderr)
                continue
            setattr(adaptors, name, spec.adaptor)
            spec.mark_up()
            print(f"{label} adaptor connected", file=sys.stderr)
        # Connectivity supervisor — retries configured-but-down
        # channels so run-ml-*.sh launch order stops mattering, and
        # drops channels whose upstream session dies mid-run.
        supervisor_task = None
        reconnect_seconds = config.get("adaptors", {}).get(
            "reconnect_seconds", 15
        )
        if reconnect_seconds > 0 and adaptors._channels:
            from .clients.adaptors import run_connectivity_supervisor
            supervisor_task = asyncio.create_task(
                run_connectivity_supervisor(
                    adaptors, reconnect_seconds
                )
            )
            print(
                f"connectivity supervisor: reconnect every "
                f"{reconnect_seconds}s"
            , file=sys.stderr)
        # Integrity monitor — startup sweep (proving the audit trail is
        # live) then periodic sweeps; violations are recorded by
        # default. A monitor failure must not block startup.
        monitor_task = None
        try:
            from .integrity.checks import start_integrity_monitor
            monitor_task = await start_integrity_monitor(
                store,
                claims=lambda: adaptors.claims,
                loop1=lambda: adaptors.loop1,
                connectivity=adaptors.connectivity_report,
                config=config.get("integrity", {}),
            )
            interval = config.get("integrity", {}).get(
                "check_interval_seconds", 300
            )
            print(
                "integrity monitor: startup check logged"
                + (f"; periodic every {interval}s" if monitor_task else "")
            , file=sys.stderr)
        except Exception as e:
            print(
                f"WARNING: integrity monitor failed to start ({e})",
                file=sys.stderr,
            )
        try:
            await coro
        finally:
            if monitor_task is not None:
                monitor_task.cancel()
            if supervisor_task is not None:
                supervisor_task.cancel()
            for name in adaptors._channels:
                live = getattr(adaptors, name)
                if live is not None:
                    try:
                        await live.disconnect()
                    except Exception:
                        pass

    obs_server = None
    if args.observability_port:
        if not _check_port_available(
            host, args.observability_port, "observability GUI"
        ):
            sys.exit(1)
        import uvicorn
        from .observability.links import upstream_gui_bases
        from .observability.server import create_observability_app

        # The agora GUI base is deployment knowledge, not a code
        # constant: TOML [observability] agora_gui_url wins, else the
        # launcher-exported ML_AGORA_GUI_URL (ports.env-derived).
        obs_config = dict(config.get("observability", {}))
        if env_url := os.environ.get("ML_AGORA_GUI_URL"):
            obs_config.setdefault("agora_gui_url", env_url)
        obs_app = create_observability_app(
            store,
            mcp_health_url=(
                f"http://{host or '127.0.0.1'}:{port}/health"
            ),
            observability_config=obs_config,
            upstream_gui_bases=upstream_gui_bases(adaptors._channels),
        )
        obs_server = uvicorn.Server(
            uvicorn.Config(
                obs_app,
                host=host,
                port=args.observability_port,
                log_level="info",
            )
        )

    if transport == "stdio":
        if obs_server:
            async def run_stdio_with_obs():
                await asyncio.gather(
                    mcp.run_stdio_async(),
                    obs_server.serve(),
                )
            _run_gracefully(_run_server(run_stdio_with_obs()))
        else:
            _run_gracefully(_run_server(mcp.run_stdio_async()))
    else:
        if not _check_port_available(host, port, "arete HTTP server"):
            sys.exit(1)
        http_coro = mcp.run_streamable_http_async(
            host=host, port=port, stateless_http=args.stateless
        )
        if obs_server:
            async def run_both_http():
                await asyncio.gather(http_coro, obs_server.serve())
            _run_gracefully(_run_server(run_both_http()))
        else:
            _run_gracefully(_run_server(http_coro))


if __name__ == "__main__":
    main()
