"""MCP-client machinery for agora's upstream connections.

Deliberately duplicated from ml-zetesis's clients/mcp_client.py
(ADR-0001/0002): the products share no code. One deliberate
difference: agora is read-only — the serve queue dispatches
``resources/read`` ops and there is **no public ``call_tool``** —
a status hub can never write into a loop.

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

    Read-only variant: the public surface is ``read_resource`` only.
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
        """Process queued read ops until stopped."""
        while not self._stop.is_set():
            try:
                op, payload = await asyncio.wait_for(
                    self._call_queue.get(), timeout=0.1
                )
            except asyncio.TimeoutError:
                continue

            try:
                if op == "resource":
                    result = await self._session.read_resource(payload)
                    contents = getattr(result, "contents", None) or []
                    if contents and hasattr(contents[0], "text"):
                        await self._result_queue.put(
                            ("ok", contents[0].text)
                        )
                    else:
                        await self._result_queue.put(
                            ("ok", str(contents[0]) if contents else None)
                        )
                else:
                    await self._result_queue.put(
                        ("error", RuntimeError(f"unknown op {op!r}"))
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

    async def read_resource(self, uri: str) -> Any:
        """Read a resource on the upstream MCP server.

        The only protocol op agora's channels expose — status
        aggregation must be side-effect-free, so ``resources/read``
        is the whole surface.
        """
        if self._session is None:
            raise RuntimeError("Not connected; call connect() first")
        timeout = self.config.get("call_timeout_seconds", 30)
        await self._call_queue.put(("resource", uri))
        try:
            status, result = await asyncio.wait_for(
                self._result_queue.get(), timeout=timeout
            )
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"Upstream resource {uri} did not answer within "
                f"{timeout}s — channel may be dead"
            )
        if status == "error":
            raise result
        return result
