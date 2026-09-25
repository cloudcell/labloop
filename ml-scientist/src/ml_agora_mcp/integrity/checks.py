"""Integrity checks for agora — the status hub's invariant suite.

Two checks, both report-only:

- ``upstream_connectivity`` — are the four read-only channels
  connected in their roles? A configured-but-down channel is a
  violation (degradation, not absence); an unconfigured channel is
  absent, not flagged.
- ``status_reachability`` — a connected channel whose ``X://status``
  fetch errors or returns an unparseable digest. Connected is not
  the same as serving — this catches the wedge a connectivity check
  alone cannot see.

The suite is async: reachability does real upstream reads.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_CHECK_INTERVAL_SECONDS = 300
DEFAULT_LOG_MAX_FILES = 30

from ..clients.adaptors import CHANNEL_STATUS_URIS


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _check_upstream_connectivity(connectivity) -> dict:
    """Who is connected in what role — consumer-perspective."""
    if connectivity is None:
        return {
            "name": "upstream_connectivity",
            "ok": True,
            "skipped": True,
            "detail": "no channels configured — standalone mode",
            "violations": [],
        }
    down = [e for e in connectivity if e.get("state") == "down"]
    violations = [
        {
            "channel": e.get("channel"),
            "role": e.get("role"),
            "target": e.get("target"),
            "last_error": e.get("last_error"),
        }
        for e in down
    ]
    return {
        "name": "upstream_connectivity",
        "ok": not violations,
        "detail": (
            f"{len(connectivity)} channel(s) configured; "
            f"{len(connectivity) - len(down)} up"
            if not violations
            else f"{len(down)} channel(s) down"
        ),
        "violations": violations,
    }


async def _check_status_reachability(adaptors) -> dict:
    """Connected-but-broken digest endpoints — serving ≠ connected."""
    violations = []
    for name, uri in CHANNEL_STATUS_URIS.items():
        spec = adaptors._channels.get(name)
        if spec is None:
            continue  # unconfigured — absent means absent
        live = getattr(adaptors, name, None)
        dead = getattr(live, "session_dead", None)
        if live is None or (dead is not None and dead()):
            continue  # down — flagged by upstream_connectivity
        try:
            text = await live.read_resource(uri)
            parsed = json.loads(text) if isinstance(text, str) else text
            if not isinstance(parsed, dict) or "server" not in parsed:
                violations.append({
                    "channel": name,
                    "status_uri": uri,
                    "detail": "digest missing required keys",
                })
        except Exception as e:
            violations.append({
                "channel": name,
                "status_uri": uri,
                "detail": str(e),
            })
    return {
        "name": "status_reachability",
        "ok": not violations,
        "detail": (
            "all connected channels serve parseable digests"
            if not violations
            else f"{len(violations)} channel(s) connected but "
                 "not serving"
        ),
        "violations": violations,
    }


async def run_checks(
    adaptors,
    *,
    connectivity=None,
) -> dict:
    """Run the agora invariant suite; return the shared payload."""
    started = time.monotonic()
    checks = [
        _check_upstream_connectivity(connectivity),
        await _check_status_reachability(adaptors),
    ]
    return {
        "server": "ml-agora-mcp",
        "status": (
            "ok" if all(c["ok"] for c in checks) else "violations"
        ),
        "checked_at": _utc_now(),
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
        "checks": checks,
    }


async def run_and_log(
    adaptors,
    *,
    connectivity=None,
    log_dir: Path,
    config: dict | None = None,
    trigger: str = "tool",
) -> dict:
    """Run the suite, write the audit log, return the payload."""
    from .log import write_check_log

    cfg = config or {}
    payload = await run_checks(adaptors, connectivity=connectivity)
    payload["trigger"] = trigger
    log_path = write_check_log(
        log_dir,
        payload,
        cfg.get("log_max_files", DEFAULT_LOG_MAX_FILES),
    )
    if log_path is not None:
        payload["log_file"] = str(log_path)
    return payload


async def start_integrity_monitor(
    adaptors,
    *,
    connectivity=None,
    log_dir: Path,
    config=None,
):
    """Startup + periodic invariant sweeps — the audit trail is on by
    default, not on request.

    connectivity may be a callable returning the current report —
    channel state changes on reconnect; resolve per sweep.
    """
    import asyncio

    cfg = config or {}

    def _connectivity():
        return (
            connectivity() if callable(connectivity) else connectivity
        )

    await run_and_log(
        adaptors,
        connectivity=_connectivity(),
        log_dir=log_dir,
        config=cfg,
        trigger="startup",
    )
    interval = cfg.get(
        "check_interval_seconds", DEFAULT_CHECK_INTERVAL_SECONDS
    )
    if not interval or interval <= 0:
        return None

    async def _loop():
        while True:
            await asyncio.sleep(interval)
            try:
                await run_and_log(
                    adaptors,
                    connectivity=_connectivity(),
                    log_dir=log_dir,
                    config=cfg,
                    trigger="interval",
                )
            except Exception:
                pass  # the monitor must never take down the server

    return asyncio.create_task(_loop())
