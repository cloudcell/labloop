"""Scientific Experiment MCP server.

Exposes the state layer as MCP tools, resources, and prompts using the
MCP Python SDK v2. The server is an MCP server to LLM clients and an MCP
client of downstream roles (optimizer, executor, data source, claims)
via the adaptor layer.

This module is a thin wiring layer. The actual tool handlers, resource
handlers, prompt templates, and enforcement checks live in their own
packages:

- ``tools/``      — one file per entity group (programme, hypothesis, trial, ...)
- ``resources/``  — programme:// URI handlers
- ``prompts/``    — workflow scaffolds
- ``enforcement/`` — the 10 commitments as rejection points
- ``clients/``    — role interfaces + adaptors

The server never names, imports, or depends on a specific product. It
depends on roles; the adaptor maps roles to concrete MCP servers.
"""

from __future__ import annotations

import logging

from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse

from .clients.adaptor import MCPAdaptor, create_stub_adaptor
from .state.store import StateStore

logger = logging.getLogger(__name__)


def create_server(
    store: StateStore,
    adaptor: MCPAdaptor | None = None,
    name: str = "scientific-experiment-mcp",
    archiver=None,
    log_tool_args: bool = False,
    integrity_config: dict | None = None,
    executor_config: dict | None = None,
    claims_config: dict | None = None,
    session_config: dict | None = None,
    enforcement_config: dict | None = None,
) -> MCPServer:
    """Create an MCPServer instance with all tools, resources, and prompts
    registered against the given state store and adaptor.

    If no adaptor is provided, a stub adaptor is used (Phase 3 fallback).
    The adaptor maps roles (optimizer, executor, data source, claims) to
    concrete MCP servers. The server never names, imports, or depends on
    a specific product.

    Args:
        archiver: Optional Archiver instance for automatic archiving.
        log_tool_args: Log raw tool-call arguments at INFO before
            validation. Diagnostic for clients sending malformed
            arguments — the SDK's 'rejected arguments' log names only
            the failing fields, not the values.
    """
    if adaptor is None:
        adaptor = create_stub_adaptor()
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

    # Register tools — one module per entity group
    from .tools import assessment, belief, candidate, data, hypothesis, observation, programme, trial
    from .tools import archive as archive_tools
    from .tools import integrity as integrity_tools

    require_attribution = bool(
        (session_config or {}).get(
            "require_candidate_attribution", False
        )
    )
    programme.register(
        mcp, store, adaptor, archiver=archiver,
        claims_config=claims_config,
        require_candidate_attribution=require_attribution,
    )
    candidate.register(mcp, store, adaptor)
    hypothesis.register(mcp, store, adaptor)
    trial.register(mcp, store, adaptor, executor_config=executor_config)
    observation.register(mcp, store, adaptor)
    belief.register(mcp, store, adaptor)
    assessment.register(mcp, store, adaptor)
    data.register(mcp, store, adaptor)
    archive_tools.register(mcp, store, adaptor, archiver=archiver)
    integrity_tools.register(
        mcp, store, adaptor, integrity_config=integrity_config
    )

    # Register resources — programme:// and protocol:// URI templates
    from .resources import programme as programme_resources
    from .resources import session as session_resources

    programme_resources.register(mcp, store)
    stale_hours = float(
        (session_config or {}).get("stale_programme_hours", 24.0)
    )
    session_resources.register(
        mcp, store,
        stale_programme_hours=stale_hours,
        adaptor=adaptor,
        require_candidate_attribution=require_attribution,
    )
    from .resources import status as status_resources

    status_resources.register(
        mcp, store, adaptor, stale_programme_hours=stale_hours,
        archive_seal_warn_hours=float(
            (session_config or {}).get("archive_seal_warn_hours", 72.0)
        ),
    )
    from .resources import graph as graph_resource

    graph_resource.register(mcp, store)

    # Register prompts — workflow scaffolds + status report
    from .prompts import status as status_prompts
    from .prompts import workflows

    workflows.register(mcp)
    status_prompts.register(mcp, store, adaptor)

    # Health endpoint — liveness probe for the MCP HTTP server.
    # The SDK's custom_route decorator is explicitly intended for health
    # checks (see MCPServer.custom_route docstring). Mounted last so it
    # has the lowest route-matching precedence relative to MCP protocol
    # routes.
    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    # Deep health — the integrity tier. Always 200: integrity is not
    # availability; the payload's `status` field carries the verdict.
    # Same checks as the check_invariants tool; each run is logged.
    @mcp.custom_route("/health/deep", methods=["GET"])
    async def health_deep(request: Request) -> JSONResponse:
        from .integrity.checks import run_and_log

        executor = getattr(adaptor, "_executor", None)
        connectivity = (
            adaptor.connectivity_report()
            if hasattr(adaptor, "connectivity_report") else None
        )
        return JSONResponse(
            run_and_log(
                store, executor=executor, connectivity=connectivity,
                config=integrity_config, trigger="route",
            )
        )

    # Recurrent self-improvement protocol — consultation-duty gate +
    # response injection + open-violation gate (plan-20260926-0438Z).
    # Disabled unless [enforcement] is provided (in-process embedders
    # stay opt-in); __main__ always forwards it, so the shipped
    # servers run enabled by default.
    enf = enforcement_config or {}
    if enf and enf.get("recurrent_protocol", True):
        from .enforcement import recurrence

        recurrence.TRACKER.configure(
            enf.get("status_freshness_seconds", 600)
        )
        recurrence.install(mcp, store, recurrence.TRACKER)

    return mcp
