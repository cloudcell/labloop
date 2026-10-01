"""anamnesis MCP server — the semantic memory of the scientific ecosystem.

A thin wiring layer mirroring ml-episteme's server shape: claims tools,
a /health route, and opt-in raw-argument logging. Standalone — this
server imports nothing from ml_episteme_mcp (ADR-0002).
"""

from __future__ import annotations

import asyncio
import json
import logging

from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse

from .state.store import MemoryStore
from . import _grounded_constants as _gc

logger = logging.getLogger(__name__)


def create_server(
    store: MemoryStore,
    name: str = "ml-anamnesis-mcp",
    log_tool_args: bool = False,
    integrity_config: dict | None = None,
    enforcement_config: dict | None = None,
    server_config: dict | None = None,
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

    # Per-call response deadline ([server] tool_deadline_seconds,
    # default 120; <=0 disables). A wedged tool once froze a sibling
    # server — every port listened, nothing answered. The deadline is a
    # response bound, not an abort: the handler runs under
    # asyncio.shield because cancelling it could unwind mid-write and
    # leave the store's implicit transaction dangling (and a pure-sync
    # spin ignores cancellation anyway). What the client gets on expiry
    # is a named error it can reason about: deadline_exceeded.
    tool_deadline = float(
        (server_config or {}).get("tool_deadline_seconds", 120.0)
    )
    if tool_deadline > 0:
        _inner_call_tool = mcp.call_tool

        async def _deadlined_call_tool(name, arguments, context=None):
            try:
                return await asyncio.wait_for(
                    asyncio.shield(
                        _inner_call_tool(name, arguments, context)
                    ),
                    tool_deadline,
                )
            except TimeoutError:
                from .tools.schemas import fail
                return fail(json.dumps({
                    "error": (
                        f"deadline_exceeded — {name} exceeded "
                        f"{tool_deadline:g}s"
                    )
                }))

        mcp.call_tool = _deadlined_call_tool

    from .prompts import status as status_prompts
    from .resources import graph as graph_resource
    from .resources import session as session_resource
    from .resources import status as status_resource
    from .tools import claims
    from .tools import integrity as integrity_tools

    claims.register(mcp, store)
    integrity_tools.register(mcp, store, integrity_config=integrity_config)
    session_resource.register(
        mcp, store, tool_deadline_seconds=tool_deadline,
    )
    status_resource.register(
        mcp, store, tool_deadline_seconds=tool_deadline
    )
    graph_resource.register(mcp, store)
    status_prompts.register(
        mcp, store, tool_deadline_seconds=tool_deadline,
    )

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    # Deep health — the integrity tier. Always 200; the payload's
    # `status` field carries the verdict. Each run is logged.
    @mcp.custom_route("/health/deep", methods=["GET"])
    async def health_deep(request: Request) -> JSONResponse:
        from .integrity.checks import run_and_log

        return JSONResponse(run_and_log(store, config=integrity_config, trigger="route"))

    # Recurrent self-improvement protocol — consultation-duty gate +
    # response injection + open-violation gate (plan-20260926-0438Z).
    # Disabled unless [enforcement] is provided; __main__ always
    # forwards it, so the shipped server runs enabled by default.
    from .enforcement import recurrence

    enf = enforcement_config or {}
    if enf and enf.get("recurrent_protocol", True):
        recurrence.TRACKER.configure(
            enf.get("status_freshness_seconds", _gc.STATUS_FRESHNESS_SECONDS.value)
        )
        recurrence.install(mcp, store, recurrence.TRACKER)

    # Argument hygiene is not a protocol feature — the undeclared-arg
    # refusal must survive recurrent_protocol=0 (and an absent
    # [enforcement] block). Outermost wrapper: malformed calls refuse
    # by name before any gate runs.
    recurrence.install_strict_args(mcp)

    return mcp
