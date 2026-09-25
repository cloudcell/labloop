"""arete MCP server — Loop 2, the recursive loop.

A thin wiring layer mirroring the zetesis server shape: meta-change
lifecycle tools, an improver://session resource, steering prompts, a
/health route, and opt-in raw-argument logging. Standalone — this
server imports nothing from ml_episteme_mcp or ml_zetesis_mcp
(ADR-0001).
"""

from __future__ import annotations

import logging

from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse

from .state.store import ImproverStore

logger = logging.getLogger(__name__)


def create_server(
    store: ImproverStore,
    adaptors=None,
    name: str = "ml-arete-mcp",
    log_tool_args: bool = False,
    integrity_config: dict | None = None,
) -> MCPServer:
    """Create an MCPServer with the meta-change lifecycle tools.

    Args:
        adaptors: an Adaptors container — tools read
            .loop0/.loop1/.claims at call time so a failed connect
            can disable a channel live (absent means absent).
        log_tool_args: Log raw tool-call arguments at INFO before
            validation (same diagnostic as the other servers' flag).
    """
    mcp = MCPServer(name, "0.1.0")

    if log_tool_args:
        _original_call_tool = mcp.call_tool

        async def _logged_call_tool(name, arguments, context=None):
            logger.info("call_tool %s arguments=%r", name, arguments)
            try:
                return await _original_call_tool(name, arguments, context)
            except Exception:
                logger.info(
                    "call_tool %s raised (validation or tool error)", name
                )
                raise

        mcp.call_tool = _logged_call_tool

    if adaptors is None:
        from .clients.adaptors import Adaptors
        adaptors = Adaptors()

    from .prompts import status as status_prompts
    from .prompts import workflows
    from .resources import graph as graph_resource
    from .resources import session as session_resource
    from .resources import status as status_resource
    from .tools import decisions, evidence, improvers, integrity
    from .tools import orchestration, proposals, tournaments

    improvers.register(mcp, store, adaptors)
    proposals.register(mcp, store, adaptors)
    tournaments.register(mcp, store, adaptors)
    orchestration.register(mcp, store, adaptors)
    decisions.register(mcp, store, adaptors)
    evidence.register(mcp, store, adaptors)
    integrity.register(
        mcp, store, adaptors, integrity_config=integrity_config
    )
    session_resource.register(mcp, store, adaptors)
    status_resource.register(mcp, store, adaptors)
    graph_resource.register(mcp, store)
    workflows.register(mcp)
    status_prompts.register(mcp, store, adaptors)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> JSONResponse:
        return JSONResponse({
            "status": "ok",
            "upstream": {
                "loop0": adaptors.loop0 is not None,
                "loop1": adaptors.loop1 is not None,
                "loop1_orchestration": (
                    adaptors.loop1_orchestration is not None
                ),
                "claims": adaptors.claims is not None,
            },
        })

    # Deep health — the integrity tier. Always 200; the payload's
    # `status` field carries the verdict. Each run is logged.
    @mcp.custom_route("/health/deep", methods=["GET"])
    async def health_deep(request: Request) -> JSONResponse:
        from .integrity.checks import run_and_log

        return JSONResponse(
            await run_and_log(
                store,
                claims=adaptors.claims,
                loop1=adaptors.loop1,
                connectivity=adaptors.connectivity_report(),
                config=integrity_config,
                trigger="route",
            )
        )

    return mcp
