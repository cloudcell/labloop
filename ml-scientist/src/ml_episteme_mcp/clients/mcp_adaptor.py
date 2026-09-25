"""MCP-client-backed adaptor implementations.

These connect to real downstream MCP servers via the MCP Client. This is
the production adaptor — the only place where product names appear (in
the configuration, not in the code).

Each role adaptor:
1. Connects to a downstream MCP server (stdio or streamable-http)
2. Translates role-level calls into MCP tool calls on the downstream server
3. Returns the results in the role interface format

Connection lifecycle: the MCP client context managers must be entered
and exited in the same task (anyio cancel scopes are task-local). We use
a background task with a command queue to manage this.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from .roles import ClaimsRole, DataSourceRole, ExecutorRole, OptimizerRole


class MCPClientAdaptor:
    """Base class for role adaptors that connect to downstream MCP servers.

    Uses a background task to manage the MCP client context manager
    lifecycle, since anyio cancel scopes are task-local.
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self._session: ClientSession | None = None
        self._task: asyncio.Task | None = None
        self._ready: asyncio.Event | None = None
        self._stop: asyncio.Event | None = None
        self._error: Exception | None = None
        self._call_queue: asyncio.Queue = asyncio.Queue()
        self._result_queue: asyncio.Queue = asyncio.Queue()

    async def _run(self) -> None:
        """Background task that holds the MCP client connection open."""
        transport = self.config.get("transport", "stdio")

        try:
            if transport == "stdio":
                params = StdioServerParameters(
                    command=self.config["command"],
                    args=self.config.get("args", []),
                    env=self.config.get("env"),
                )
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        self._session = session
                        self._ready.set()
                        await self._serve()
            elif transport == "streamable-http":
                url = self.config["url"]
                async with streamable_http_client(url) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        self._session = session
                        self._ready.set()
                        await self._serve()
            else:
                raise ValueError(f"Unknown transport: {transport}")
        except Exception as e:
            self._error = e
            self._ready.set()

    async def _serve(self) -> None:
        """Process tool calls from the queue until stopped."""
        while not self._stop.is_set():
            try:
                name, args = await asyncio.wait_for(self._call_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue

            try:
                result = await self._session.call_tool(name, args)
                if result.is_error:
                    err = RuntimeError(f"Downstream tool {name} returned error: {result.content}")
                    await self._result_queue.put(("error", err))
                elif result.content and hasattr(result.content[0], "text"):
                    await self._result_queue.put(("ok", result.content[0].text))
                else:
                    await self._result_queue.put(("ok", str(result.content[0]) if result.content else None))
            except Exception as e:
                await self._result_queue.put(("error", e))

    async def connect(self) -> None:
        """Connect to the downstream MCP server.

        Reusable: clears state left by a previous attempt so the
        connectivity supervisor can retry on the same object.
        """
        self._error = None
        self._session = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        for q in (self._call_queue, self._result_queue):
            while not q.empty():
                q.get_nowait()
        self._task = asyncio.create_task(self._run())
        await self._ready.wait()
        if self._error:
            raise self._error

    async def disconnect(self) -> None:
        """Disconnect from the downstream MCP server."""
        if self._stop is not None:
            self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._session = None
        self._task = None

    def session_dead(self) -> bool:
        """True when the connection task ended — a dead downstream
        exits ``_run``, leaving a stale ``_session`` that would wedge
        calls."""
        return self._task is not None and self._task.done()

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Call a tool on the downstream MCP server."""
        if self._session is None:
            raise RuntimeError("Not connected; call connect() first")
        timeout = self.config.get("call_timeout_seconds", 30)
        await self._call_queue.put((name, arguments))
        try:
            status, result = await asyncio.wait_for(
                self._result_queue.get(), timeout=timeout
            )
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"Downstream tool {name} did not answer within "
                f"{timeout}s — channel may be dead"
            )
        if status == "error":
            raise result
        return result

    async def list_tools(self) -> list[str]:
        """List available tools on the downstream MCP server."""
        if self._session is None:
            raise RuntimeError("Not connected; call connect() first")
        result = await self._session.list_tools()
        return [t.name for t in result.tools]


class MCPOptimizerAdaptor(MCPClientAdaptor, OptimizerRole):
    """Optimizer role backed by a downstream MCP server.

    Expected downstream tools:
    - create_study(programme_id, variables) -> study_id
    - ask(programme_id) -> config dict (JSON string)
    - tell(programme_id, trial_id, result) -> void
    - best_trials(programme_id) -> list of best results (JSON string)
    - param_importance(programme_id) -> importance dict (JSON string)
    """

    async def create_study(
        self, programme_id: str, variables: list[str], direction: str = "maximize"
    ) -> str:
        return await self.call_tool("create_study", {
            "programme_id": programme_id,
            "variables": variables,
            "direction": direction,
        })

    async def ask(self, programme_id: str) -> dict[str, Any]:
        result = await self.call_tool("ask", {"programme_id": programme_id})
        return json.loads(result) if isinstance(result, str) else result

    async def tell(self, programme_id: str, trial_id: str, result: dict[str, float]) -> None:
        await self.call_tool("tell", {
            "programme_id": programme_id,
            "trial_id": trial_id,
            "result": result,
        })

    async def best_trials(
        self, programme_id: str, direction: str | None = None
    ) -> list[dict[str, Any]]:
        args = {"programme_id": programme_id}
        if direction is not None:
            args["direction"] = direction
        result = await self.call_tool("best_trials", args)
        return json.loads(result) if isinstance(result, str) else result

    async def param_importance(self, programme_id: str) -> dict[str, float]:
        result = await self.call_tool("param_importance", {"programme_id": programme_id})
        return json.loads(result) if isinstance(result, str) else result


class MCPExecutorAdaptor(MCPClientAdaptor, ExecutorRole):
    """Executor role backed by a downstream MCP server.

    Expected downstream tools:
    - execute_code(code) -> output (JSON string)
    - read_cell_output(cell_id) -> output (JSON string)
    """

    async def execute_code(
        self,
        code: str,
        artifact_dir: Any = None,
        trial_id: str | None = None,
        programme_id: str | None = None,
        bundle_id: str | None = None,
        extra_ro_paths: list[str] | None = None,
        extra_rw_paths: list[str] | None = None,
        python_exe: str | None = None,
        overlay_ro: list[tuple[str, str]] | None = None,
    ) -> str:
        # extra_*_paths, overlay_ro, and python_exe are local-executor
        # hints — a remote executor defines its own isolation and
        # environment; they can't be honored across a network boundary
        # and are intentionally ignored.
        return await self.call_tool("execute_code", {"code": code})

    async def read_cell_output(self, cell_id: str) -> str:
        return await self.call_tool("read_cell_output", {"cell_id": cell_id})


class AnamnesisClaimsAdaptor(MCPClientAdaptor, ClaimsRole):
    """Claims role backed by a claims-memory MCP server (anamnesis).

    Semantic memory — claims and typed evidence edges — as opposed to
    episodic memory, which is owned by the loop's state.db (ADR-0005).

    Expected downstream tools:
    - assert_claim(content, type, confidence, evidence, source_id) -> {claim_id}
    - relate(from_claim, to_ref, ref_type, relation) -> {edge_id}
    - get_claim(claim_id) -> claim + provenance bundle (JSON string)
    - list_claims(type?, limit?) -> list of claims (JSON string)
    """

    @staticmethod
    def _parse(result: Any, tool: str) -> dict[str, Any]:
        data = json.loads(result) if isinstance(result, str) else result
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(f"Claims server rejected {tool}: {data['error']}")
        return data

    async def assert_claim(
        self,
        content: str,
        type: str,
        confidence: float,
        evidence: list[dict[str, Any]] | None = None,
        source_id: str | None = None,
    ) -> str:
        data = self._parse(await self.call_tool("assert_claim", {
            "content": content,
            "type": type,
            "confidence": confidence,
            "evidence": evidence or [],
            "source_id": source_id,
        }), "assert_claim")
        return data["claim_id"]

    async def relate(
        self,
        from_claim: str,
        to_ref: str,
        ref_type: str,
        relation: str,
    ) -> str:
        data = self._parse(await self.call_tool("relate", {
            "from_claim": from_claim,
            "to_ref": to_ref,
            "ref_type": ref_type,
            "relation": relation,
        }), "relate")
        return data["edge_id"]

    async def get_claim(self, claim_id: str) -> dict[str, Any]:
        return self._parse(
            await self.call_tool("get_claim", {"claim_id": claim_id}), "get_claim"
        )

    async def list_claims(
        self, type: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        args: dict[str, Any] = {"limit": limit}
        if type is not None:
            args["type"] = type
        data = self._parse(await self.call_tool("list_claims", args), "list_claims")
        return data.get("claims", [])


class MCPDataSourceAdaptor(MCPClientAdaptor, DataSourceRole):
    """Data Source role backed by a downstream MCP server.

    Expected downstream tools:
    - prepare_data(split, regime, ...) -> data_ref_id
    - get_data_ref(data_ref_id) -> data_ref dict (JSON string)
    - verify_data(data_ref_id) -> verification dict (JSON string)
    - resolve_data_path(data_ref_id) -> filesystem path (string)
    """

    async def prepare_data(
        self,
        split: str,
        regime: str,
        generator_code_ref: str | None = None,
        generator_seed: int | None = None,
        generator_params: dict[str, Any] | None = None,
        source_uri: str | None = None,
        version: str | None = None,
        capture_window_start: str | None = None,
        capture_window_end: str | None = None,
        capture_source_metadata: dict[str, Any] | None = None,
    ) -> str:
        return await self.call_tool("prepare_data", {
            "split": split,
            "regime": regime,
            "generator_code_ref": generator_code_ref,
            "generator_seed": generator_seed,
            "generator_params": generator_params,
            "source_uri": source_uri,
            "version": version,
            "capture_window_start": capture_window_start,
            "capture_window_end": capture_window_end,
            "capture_source_metadata": capture_source_metadata,
        })

    async def get_data_ref(self, data_ref_id: str) -> dict[str, Any]:
        result = await self.call_tool("get_data_ref", {"data_ref_id": data_ref_id})
        return json.loads(result) if isinstance(result, str) else result

    async def verify_data(self, data_ref_id: str) -> dict[str, Any]:
        result = await self.call_tool("verify_data", {"data_ref_id": data_ref_id})
        return json.loads(result) if isinstance(result, str) else result

    async def resolve_data_path(self, data_ref_id: str) -> str:
        return await self.call_tool("resolve_data_path", {"data_ref_id": data_ref_id})


def create_mcp_adaptor(config: dict[str, Any]) -> tuple[MCPOptimizerAdaptor, MCPExecutorAdaptor, AnamnesisClaimsAdaptor]:
    """Create MCP-backed adaptors from config.

    Config format:

        [adaptors.optimizer]
        transport = "stdio"
        command = "uv run some-optimizer-mcp"

        [adaptors.executor]
        transport = "streamable-http"
        url = "http://localhost:8888/mcp"

        [adaptors.claims]
        transport = "streamable-http"
        url = "http://localhost:38090/mcp"

    Returns (optimizer, executor, claims) adaptors. Call connect() on each
    before use, and disconnect() when done.
    """
    adaptor_configs = config.get("adaptors", {})

    optimizer = MCPOptimizerAdaptor(adaptor_configs.get("optimizer", {}))
    executor = MCPExecutorAdaptor(adaptor_configs.get("executor", {}))
    claims = AnamnesisClaimsAdaptor(adaptor_configs.get("claims", {}))

    return optimizer, executor, claims
