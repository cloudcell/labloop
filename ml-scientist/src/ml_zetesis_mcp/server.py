"""zetesis MCP server — Loop 1, the search loop.

A thin wiring layer mirroring the anamnesis server shape:
investigation tools, a search://session resource, steering prompts, a
/health route, and opt-in raw-argument logging. Standalone — this
server imports nothing from ml_episteme_mcp (ADR-0001).
"""

from __future__ import annotations

import logging

from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse

from .state.store import SearchStore

logger = logging.getLogger(__name__)


def create_server(
    store: SearchStore,
    adaptors=None,
    name: str = "ml-zetesis-mcp",
    log_tool_args: bool = False,
    integrity_config: dict | None = None,
) -> MCPServer:
    """Create an MCPServer with the investigation-loop tools registered.

    Args:
        adaptors: an Adaptors container — tools read .evidence/.claims
            at call time so a failed connect can disable a channel
            live (absent means absent).
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
    from .tools import integrity as integrity_tools
    from .tools import investigation, promotion

    from .enforcement.checks import PRIOR_CONFIDENCE_MAX

    investigation.register(
        mcp, store, adaptors,
        prior_confidence_max=float(
            (integrity_config or {}).get(
                "prior_confidence_max", PRIOR_CONFIDENCE_MAX
            )
        ),
    )
    promotion.register(mcp, store, adaptors)
    integrity_tools.register(
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
                "evidence": adaptors.evidence is not None,
                "promotion": adaptors.promotion is not None,
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
                connectivity=adaptors.connectivity_report(),
                config=integrity_config,
                trigger="route",
            )
        )

    return mcp
