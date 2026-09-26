"""Entry point for the Scientific Experiment MCP server.

Run via stdio (for local use with Claude Desktop, Cursor, etc.):

    uv run ml-episteme-mcp

Or via Streamable HTTP (for remote/hosted):

    uv run ml-episteme-mcp --transport http --port 38080

Configuration:
    - CLI flags (highest priority)
    - Environment variables (ML_EPISTEME_DB, ML_EPISTEME_TRANSPORT, etc.)
    - Config file (./ml-episteme.toml or ~/.ml-episteme/config.toml)
    - Defaults (lowest priority)

Config file format (TOML):

    db_path = "/path/to/state.db"
    transport = "stdio"
    port = 38080

    [adaptors.optimizer]
    transport = "stdio"
    command = "uv run some-optimizer-mcp"

    [adaptors.executor]
    transport = "streamable-http"
    url = "http://localhost:8888/mcp"

    [adaptors.claims]
    transport = "streamable-http"
    url = "http://localhost:38090/mcp"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys
from pathlib import Path

from .config import create_adaptor_from_config, load_config
from .server import create_server
from .state.store import StateStore


def _check_port_available(host: str, port: int, label: str) -> bool:
    """Check if a port is available before starting the server.

    Returns True if available, False if in use. Prints a clear error.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
    except OSError:
        print(
            f"ERROR: Port {port} is already in use ({label}).\n"
            f"  → Another ml-episteme-mcp process is likely still running.\n"
            f"  → Find it:  fuser {port}/tcp\n"
            f"  → Kill it: fuser -k {port}/tcp\n"
            f"  → Or use a different port: --port {port + 1} --observability-port {port + 2}",
            file=sys.stderr,
        )
        return False
    return True


def _run_gracefully(coro):
    """Run an asyncio coroutine, handling Ctrl+C without printing a traceback.

    When the user presses Ctrl+C, asyncio cancels the running tasks, which
    surfaces as CancelledError/KeyboardInterrupt. We catch these and exit
    cleanly with code 0 (intentional shutdown), printing a short message.
    """
    try:
        asyncio.run(coro)
    except KeyboardInterrupt:
        print("\nShutting down.", file=sys.stderr)
        sys.exit(0)
    except asyncio.CancelledError:
        # asyncio.run may re-raise CancelledError if the event loop was
        # cancelled before the main task completed. Treat it the same as
        # a user-initiated shutdown.
        sys.exit(0)


def reap_orphaned_trials(store: "StateStore") -> int:
    """Mark orphaned 'running' trials as failed. Returns count reaped.

    The executor's task map is in-memory: a restart leaves any trial
    still marked 'running' with no live process behind it — it could
    never finalize (a zombie that can block its programme forever).
    Marking it 'failed' with an interrupted note is the honest record:
    we know the trial never reached a completion boundary, not what
    the vanished process did.
    """
    orphans = store._fetchall("SELECT id FROM trials WHERE status = 'running'")
    for row in orphans:
        store.update_trial_status(row["id"], "failed")
        store.update_trial_executor_output(
            row["id"],
            json.dumps({
                "status": "failed",
                "interrupted": True,
                "error": "server restarted while trial was running; "
                         "actual process outcome unknown",
            }),
        )
    return len(orphans)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scientific Experiment MCP server — the hypothesis → experiment → evidence → conclusion loop as a first-class object",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  # stdio (default, for Claude Desktop / Cursor / Bionic)
  ml-episteme-mcp

  # HTTP (for remote clients)
  ml-episteme-mcp --transport http --port 38080

  # Stateless HTTP (survives server restarts)
  ml-episteme-mcp --transport http --port 38080 --stateless

  # With observability web GUI
  ml-episteme-mcp --transport http --port 38080 --stateless --observability-port 38081

  # Custom database
  ml-episteme-mcp --db-path /path/to/state.db

  # Custom config file
  ml-episteme-mcp --config /path/to/ml-episteme.toml
""",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default=None,
        help="Transport to use (default: stdio, or from config)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port for HTTP transport (default: 38080, or from config)",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Host to bind for HTTP transport (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to the SQLite state database (default: ~/.ml-episteme/state.db)",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to config file (default: ./ml-episteme.toml or ~/.ml-episteme/config.toml)",
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
        help="Port for the observability web GUI (default: disabled). Open http://localhost:PORT in a browser.",
    )
    parser.add_argument(
        "--observability-refresh",
        type=int,
        default=5,
        help="HTMX auto-refresh interval in seconds for the observability GUI (default: 5, 0 = disabled)",
    )
    parser.add_argument(
        "--log-tool-args",
        action="store_true",
        help="Log raw tool-call arguments at INFO before validation (diagnoses client argument-shape errors)",
    )
    parser.add_argument(
        "--ingest-port",
        type=int,
        default=None,
        help="Port for the artifact-ingest surface (default: disabled). "
        "Requires ML_EPISTEME_INGEST_TOKEN (or [ingest] token in config); "
        "the listener refuses to start without it.",
    )
    args = parser.parse_args()

    # Load config (file + env)
    config = load_config(args.config)

    # CLI overrides (highest priority)
    transport = args.transport or config["transport"]
    port = args.port or config["port"]
    host = args.host or config["host"]
    db_path = args.db_path or config["db_path"]

    # Ensure the DB directory exists
    db_path_obj = Path(db_path)
    db_path_obj.parent.mkdir(parents=True, exist_ok=True)

    # Create the store and connect
    store = StateStore(db_path)
    store.connect()

    # [lifecycle] reap_orphaned_trials = "failed" (default) | "off"
    reap_mode = config.get("lifecycle", {}).get("reap_orphaned_trials", "failed")
    if reap_mode != "off":
        try:
            reaped = reap_orphaned_trials(store)
            if reaped:
                print(f"Startup reaper: {reaped} orphaned running trial(s) marked failed", file=sys.stderr)
        except Exception as e:
            print(f"Startup reaper warning: {e}", file=sys.stderr)

    # Create the adaptor from config
    adaptor = create_adaptor_from_config(config)

    # Create the archiver from config (Phase 3)
    from .archive import ArchiveConfig, Archiver
    archive_config = ArchiveConfig.from_config(config)
    archiver = Archiver(store, archive_config) if archive_config.enabled else None

    # Phase 4: archive pending programmes at startup (safety net).
    # Catches terminal programmes that were closed but never archived
    # due to a crash or lock failure. Also marks pre-no-purge zombies
    # (in archive_entries but still completed/abandoned) as archived.
    if archiver is not None:
        try:
            # Repair archived programmes first: fixes status corruption
            # (archived reset to 'active' by an old migration bug) and
            # restores live rows for programmes purged by the old
            # delete-after-archive behaviour. Idempotent.
            repair = archiver.repair_archived_programmes()
            if repair["repaired"] or repair["restored"]:
                print(
                    f"Startup repair: {len(repair['repaired'])} repaired, "
                    f"{len(repair['restored'])} restored"
                , file=sys.stderr)

            rows = store._fetchall(
                "SELECT id, status FROM programmes WHERE status IN ('completed', 'abandoned')"
            )
            already = {e["programme_id"] for e in store._fetchall(
                "SELECT programme_id FROM archive_entries"
            )}
            archived_count = 0
            marked_count = 0
            for row in rows:
                pid = row["id"]
                if pid in already:
                    # Already in archive_entries but not yet marked archived
                    store.update_programme_status(pid, "archived")
                    marked_count += 1
                else:
                    archiver.archive_programme(pid)
                    archived_count += 1
            if archived_count or marked_count:
                print(
                    f"Startup archive: {archived_count} archived, "
                    f"{marked_count} marked archived"
                , file=sys.stderr)
        except Exception as e:
            print(f"Startup archive warning: {e}", file=sys.stderr)

    # Create the server
    mcp = create_server(
        store, adaptor, archiver=archiver, log_tool_args=args.log_tool_args,
        integrity_config=config.get("integrity", {}),
        executor_config=config.get("executor", {}),
        claims_config=config.get("claims", {}),
        session_config=config.get("session", {}),
        enforcement_config=config.get("enforcement", {}),
    )

    async def _run_server(coro):
        """Connect MCP-backed role adaptors on the server loop, run, disconnect.

        MCP client sessions must live on the server's event loop, so
        connect() runs here — not before asyncio.run. A claims adaptor
        that fails to connect is disabled (absent means absent); memory
        is a byproduct, never a gate on startup. Required roles warn on
        failure but stay wired — the server still serves state reads.
        """
        roles = [
            ("optimizer", adaptor._optimizer),
            ("executor", adaptor._executor),
            ("data_source", adaptor._data_source),
        ]
        for name, role in roles:
            if role is None or not hasattr(role, "connect"):
                continue
            try:
                await role.connect()
                print(f"{name} adaptor connected", file=sys.stderr)
            except Exception as e:
                print(
                    f"WARNING: {name} adaptor unreachable ({e}) — "
                    f"{name} calls will fail",
                    file=sys.stderr,
                )
        # Claims is a retryable upstream channel, not a one-shot role:
        # a failed connect stays registered and the supervisor keeps
        # retrying — run-ml-*.sh launch order stops mattering.
        ch = adaptor._claims_channel
        if ch is None:
            print("claims adaptor not configured — claims disabled",
                  file=sys.stderr)
        else:
            ch["attempts"] += 1
            try:
                await ch["role_obj"].connect()
            except Exception as e:
                ch["state"] = "down"
                ch["last_error"] = str(e)
                print(
                    f"WARNING: claims adaptor unreachable ({e}) — "
                    "the supervisor will keep retrying",
                    file=sys.stderr,
                )
            else:
                from .clients.adaptor import _utc_now
                adaptor.set_claims(ch["role_obj"])
                ch["state"] = "up"
                ch["connected_at"] = _utc_now()
                ch["last_error"] = None
                print("claims adaptor connected", file=sys.stderr)
        supervisor_task = None
        reconnect_seconds = config.get("adaptors", {}).get(
            "reconnect_seconds", 15
        )
        if reconnect_seconds > 0 and ch is not None:
            from .clients.adaptor import run_claims_supervisor
            supervisor_task = asyncio.create_task(
                run_claims_supervisor(adaptor, reconnect_seconds)
            )
            print(
                f"connectivity supervisor: reconnect every "
                f"{reconnect_seconds}s"
            , file=sys.stderr)
        # Integrity monitor — a startup sweep (proving the audit trail
        # is live) then periodic sweeps; violations are recorded by
        # default, not on request. A monitor failure must not block
        # startup.
        monitor_task = None
        try:
            from .integrity.checks import start_integrity_monitor
            monitor_task = start_integrity_monitor(
                store,
                executor=adaptor._executor,
                connectivity=(
                    adaptor.connectivity_report
                    if hasattr(adaptor, "connectivity_report") else None
                ),
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
            for _, role in roles:
                if role is not None and hasattr(role, "disconnect"):
                    try:
                        await role.disconnect()
                    except Exception:
                        pass
            live_claims = adaptor._claims
            if live_claims is not None and hasattr(live_claims, "disconnect"):
                try:
                    await live_claims.disconnect()
                except Exception:
                    pass

    # Auxiliary HTTP surfaces — observability GUI (read-only) and
    # artifact ingest (write, token-gated). Both are uvicorn servers
    # gathered alongside whichever MCP transport runs.
    import os

    aux_servers = []

    obs_port = args.observability_port
    obs_refresh = args.observability_refresh
    if obs_port:
        if not _check_port_available(host, obs_port, "observability GUI"):
            sys.exit(1)
        import uvicorn
        from .observability.server import create_observability_app

        # The agora GUI base is deployment knowledge, not a code
        # constant: TOML [observability] agora_gui_url wins, else the
        # launcher-exported ML_AGORA_GUI_URL (ports.env-derived).
        obs_config = dict(config.get("observability", {}))
        if env_url := os.environ.get("ML_AGORA_GUI_URL"):
            obs_config.setdefault("agora_gui_url", env_url)
        obs_app = create_observability_app(
            store, obs_refresh, archiver=archiver,
            mcp_health_url=f"http://{host or '127.0.0.1'}:{port}/health",
            observability_config=obs_config,
        )
        aux_servers.append(
            uvicorn.Server(
                uvicorn.Config(
                    obs_app, host=host, port=obs_port, log_level="info"
                )
            )
        )

    ingest_port = args.ingest_port or os.environ.get(
        "ML_EPISTEME_INGEST_PORT"
    )
    if ingest_port:
        ingest_port = int(ingest_port)
        ingest_cfg = config.get("ingest", {})
        ingest_token = os.environ.get(
            "ML_EPISTEME_INGEST_TOKEN"
        ) or ingest_cfg.get("token")
        if not ingest_token:
            print(
                "ERROR: --ingest-port given but no ingest token "
                "configured.\n"
                "  → Set ML_EPISTEME_INGEST_TOKEN (or [ingest] token in "
                "config).\n"
                "  → The ingest surface writes to the state store; it "
                "never starts unauthenticated.",
                file=sys.stderr,
            )
            sys.exit(1)
        if not _check_port_available(host, ingest_port, "artifact ingest"):
            sys.exit(1)
        import uvicorn
        from .ingest.server import (
            DEFAULT_MAX_BYTES,
            create_ingest_app,
        )

        max_bytes = int(
            os.environ.get("ML_EPISTEME_INGEST_MAX_BYTES")
            or ingest_cfg.get("max_bytes")
            or DEFAULT_MAX_BYTES
        )
        ingest_app = create_ingest_app(
            store, token=ingest_token, max_bytes=max_bytes
        )
        aux_servers.append(
            uvicorn.Server(
                uvicorn.Config(
                    ingest_app,
                    host=host,
                    port=ingest_port,
                    log_level="info",
                )
            )
        )
        print(f"artifact ingest: :{ingest_port} (token-gated)", file=sys.stderr)

    async def _serve_all(mcp_coro):
        await asyncio.gather(
            mcp_coro, *(s.serve() for s in aux_servers)
        )

    if transport == "stdio":
        _run_gracefully(_run_server(_serve_all(mcp.run_stdio_async())))
    elif transport == "http":
        # Pre-check the MCP port before starting — fail gracefully
        # instead of crashing
        if not _check_port_available(host, port, "MCP HTTP server"):
            sys.exit(1)
        _run_gracefully(
            _run_server(
                _serve_all(
                    mcp.run_streamable_http_async(
                        host=host,
                        port=port,
                        stateless_http=args.stateless,
                    )
                )
            )
        )


if __name__ == "__main__":
    main()
