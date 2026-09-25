"""Agora MCP server — the lab status hub.

Exposes the aggregated status surface as MCP resources and a prompt,
plus the uniform check_invariants tool. Agora is a role-filler, not
a loop (ADR-0004): it reads every server's status digest through
read-only channels and synthesizes the lab-level answer to "what do
I do next". Its client has no call_tool path — it can never write
into a loop.

This module is a thin wiring layer — the digest fetching, ranking,
and checks live in their own packages:

- ``resources/`` — lab://status (aggregate) and lab://topology
- ``prompts/``   — status_report (the LLM-facing render)
- ``tools/``     — check_invariants (the uniform integrity surface)
- ``integrity/`` — the audit suite + JSONL log
- ``clients/``   — read-only channel machinery + supervisor
"""

from __future__ import annotations

import logging
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


def create_server(
    adaptors,
    *,
    log_dir: Path,
    name: str = "ml-agora-mcp",
    log_tool_args: bool = False,
    integrity_config: dict | None = None,
) -> MCPServer:
    """Create an MCPServer with the status surfaces registered.

    Args:
        adaptors: the Adaptors container — channels are read live so
            supervisor reconnects are reflected in every fetch.
        log_dir: the audit-log dir (~/.ml-agora/logs by default) —
            agora is state-free; this is the only persistence.
        log_tool_args: Log raw tool-call arguments at INFO before
            validation.
    """
    mcp = MCPServer(name, "0.1.0")

    if log_tool_args:
        _original_call_tool = mcp.call_tool

        async def _logged_call_tool(name, arguments, context=None):
            logger.info("call_tool %s arguments=%r", name, arguments)
            try:
                return await _original_call_tool(name, arguments, context)
            except Exception:
                logger.info("call_tool %s raised (validation or tool error)", name)
                raise

        mcp.call_tool = _logged_call_tool

    from .prompts import status as status_prompts
    from .resources import status as status_resource
    from .resources import topology as topology_resource
    from .tools import integrity as integrity_tools

    status_resource.register(mcp, adaptors)
    topology_resource.register(mcp, adaptors)
    status_prompts.register(mcp, adaptors)
    integrity_tools.register(
        mcp, adaptors, log_dir=log_dir,
        integrity_config=integrity_config,
    )

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> JSONResponse:
        return JSONResponse({
            "status": "ok",
            "channels": {
                name: getattr(adaptors, name) is not None
                for name in ("claims", "loop0", "loop1", "loop2")
            },
        })

    # Deep health — the integrity tier. Always 200: integrity is not
    # availability; the payload's `status` field carries the verdict.
    @mcp.custom_route("/health/deep", methods=["GET"])
    async def health_deep(request: Request) -> JSONResponse:
        from .integrity.checks import run_and_log

        return JSONResponse(
            await run_and_log(
                adaptors,
                connectivity=adaptors.connectivity_report(),
                log_dir=log_dir,
                config=integrity_config,
                trigger="route",
            )
        )

    return mcp
