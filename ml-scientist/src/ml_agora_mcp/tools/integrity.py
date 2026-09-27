"""Integrity tool — check_invariants.

Audits the status hub's channels against its invariants and returns
structured violations. Every invocation is logged to
<db_dir>/logs/ — the check trail is itself evidence. Report-only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from pydantic import Field

from ..integrity.checks import run_and_log
from .schemas import ok, fail, CheckInvariantsOut, ReadResourceOut
from mcp.types import CallToolResult


# URI scheme → upstream channel — the hub's read surface spans the
# lab, but only through configured channels: an unregistered scheme
# errors rather than guessing a route.
_SCHEME_CHANNELS = {
    "claims": "claims",
    "protocol": "loop0",
    "executor": "loop0",
    "programme": "loop0",
    "trial": "loop0",
    "artifact": "loop0",
    "dataref": "loop0",
    "code": "loop0",
    "search": "loop1",
    "improver": "loop2",
}


def register(
    mcp, adaptors, *, log_dir: Path,
    integrity_config: dict | None = None,
) -> None:
    """Register the integrity tool.

    integrity_config: the [integrity] table from ml-agora.toml —
    log_max_files.
    """

    @mcp.tool()
    async def check_invariants() -> Annotated[CallToolResult, CheckInvariantsOut]:
        """Audit the lab's channels against the hub's invariants.

        Returns {status: ok|violations, checks: [{name, ok,
        violations, detail}]}: upstream_connectivity (configured-but-
        down channels) and status_reachability (connected channels
        whose status digest fails to serve). Report-only — nothing
        is repaired or mutated. The run is logged to <db_dir>/logs/
        (retention: [integrity] log_max_files).
        """
        return ok(await run_and_log(
                adaptors,
                connectivity=adaptors.connectivity_report(),
                log_dir=log_dir,
                config=integrity_config,
                trigger="tool",
            ))

    @mcp.tool()
    async def read_resource(
        uri: Annotated[str, Field(description="Resource URI to read — lab://* locally; claims://, protocol://, executor://, programme://, trial://, artifact://, dataref://, code://, search://, improver:// through the configured upstream channel.")],
    ) -> Annotated[CallToolResult, ReadResourceOut]:
        """Read a resource by URI — the lab-wide read surface.

        lab://* resolves locally; every other registered scheme is
        fetched through the owning server's read-only channel (the
        channels expose resources/read only — this can never become
        a write path). Unknown schemes and unconfigured or down
        channels error explicitly rather than guessing.
        """
        scheme = uri.split("://", 1)[0] if "://" in uri else ""
        try:
            if scheme == "lab":
                items = []
                for c in await mcp.read_resource(uri):
                    content = c.content
                    if isinstance(content, bytes):
                        content = content.decode(
                            "utf-8", errors="replace")
                    items.append({
                        "mime_type": c.mime_type,
                        "content": content,
                    })
                return ok({"uri": uri, "contents": items})
            channel = _SCHEME_CHANNELS.get(scheme)
            if channel is None:
                return fail(json.dumps({
                    "error": f"unknown resource scheme {scheme!r} — "
                    "no channel owns it",
                }))
            spec = adaptors._channels.get(channel)
            if spec is None:
                return fail(json.dumps({
                    "error": f"channel {channel!r} not configured — "
                    f"{scheme}:// resources have no route",
                }))
            live = getattr(adaptors, channel, None)
            dead = getattr(live, "session_dead", None)
            if live is None or (dead is not None and dead()):
                return fail(json.dumps({
                    "error": f"channel {channel!r} is down — "
                    f"{uri} unreachable",
                    "detail": spec.last_error,
                }))
            text = await live.read_resource(uri)
            return ok({
                "uri": uri,
                "via": channel,
                "contents": [{"mime_type": "application/json",
                              "content": text}],
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))
