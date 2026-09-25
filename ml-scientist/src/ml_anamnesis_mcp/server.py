"""anamnesis MCP server — the semantic memory of the scientific ecosystem.

A thin wiring layer mirroring ml-episteme's server shape: claims tools,
a /health route, and opt-in raw-argument logging. Standalone — this
server imports nothing from ml_episteme_mcp (ADR-0002).
"""

from __future__ import annotations

import logging

from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse

from .state.store import MemoryStore

logger = logging.getLogger(__name__)


def create_server(
    store: MemoryStore,
    name: str = "ml-anamnesis-mcp",
    log_tool_args: bool = False,
    integrity_config: dict | None = None,
) -> MCPServer:
    """Create an MCPServer with the claim-graph tools registered.

    Args:
        log_tool_args: Log raw tool-call arguments at INFO before
            validation (same diagnostic as ml-episteme's flag).
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

    from .enforcement.checks import PRIOR_CONFIDENCE_MAX
    from .prompts import status as status_prompts
    from .resources import graph as graph_resource
    from .resources import session as session_resource
    from .resources import status as status_resource
    from .tools import claims
    from .tools import integrity as integrity_tools

    claims.register(
        mcp, store,
        prior_confidence_max=float(
            (integrity_config or {}).get(
                "prior_confidence_max", PRIOR_CONFIDENCE_MAX
            )
        ),
    )
    integrity_tools.register(mcp, store, integrity_config=integrity_config)
    session_resource.register(mcp, store)
    status_resource.register(mcp, store)
    graph_resource.register(mcp, store)
    status_prompts.register(mcp, store)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    # Deep health — the integrity tier. Always 200; the payload's
    # `status` field carries the verdict. Each run is logged.
    @mcp.custom_route("/health/deep", methods=["GET"])
    async def health_deep(request: Request) -> JSONResponse:
        from .integrity.checks import run_and_log

        return JSONResponse(run_and_log(store, config=integrity_config, trigger="route"))

    return mcp
