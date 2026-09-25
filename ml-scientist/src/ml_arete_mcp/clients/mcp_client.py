"""MCP-client machinery for arete's upstream connections.

Deliberately duplicated from ml-zetesis's clients/mcp_client.py
(ADR-0001/0002): the loop servers share no code — a shared module
would be coupling by another name, and each package must extract to
its own repo cleanly. Keep this copy small and identical in
behaviour.

Connection lifecycle: the MCP client context managers must be entered
and exited in the same task (anyio cancel scopes are task-local), so a
background task holds the session and serves a call queue.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client


class MCPClientAdaptor:
    """Base class for upstream adaptors connecting to MCP servers.

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
                name, args = await asyncio.wait_for(
                    self._call_queue.get(), timeout=0.1
                )
            except asyncio.TimeoutError:
                continue

            try:
                result = await self._session.call_tool(name, args)
                if result.is_error:
                    err = RuntimeError(
                        f"Upstream tool {name} returned error: {result.content}"
                    )
                    await self._result_queue.put(("error", err))
                elif result.content and hasattr(result.content[0], "text"):
                    await self._result_queue.put(("ok", result.content[0].text))
                else:
                    await self._result_queue.put(
                        ("ok", str(result.content[0]) if result.content else None)
                    )
            except Exception as e:
                await self._result_queue.put(("error", e))

    async def connect(self) -> None:
        """Connect to the upstream MCP server.

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
        """Disconnect from the upstream MCP server."""
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
        """True when the connection task ended — a dead upstream exits
        ``_run``, leaving a stale ``_session`` that would wedge calls."""
        return self._task is not None and self._task.done()

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Call a tool on the upstream MCP server."""
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
                f"Upstream tool {name} did not answer within "
                f"{timeout}s — channel may be dead"
            )
        if status == "error":
            raise result
        return result

    async def list_tools(self) -> list[str]:
        """List available tools on the upstream MCP server."""
        if self._session is None:
            raise RuntimeError("Not connected; call connect() first")
        result = await self._session.list_tools()
        return [t.name for t in result.tools]
