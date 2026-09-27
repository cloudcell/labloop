"""rc-7b R4 — last_error is *current* state, not history.

The VM showed zetesis reporting `state: "up"` + `probe: "ok"` +
`last_error: "ConnectError: All connection attempts failed"` on the
evidence channel: the client's last_error was set on failure and
never cleared on recovery, and the status surface composes
`spec.last_error or src.last_error` — resurrecting the stale value.

The clear boundary is channel-health-proving success: connect,
ping ok, transport-level call. An upstream is_error verdict is the
peer's word, not a channel fault, and does not clear. The
attribution fields last_failed_operation / last_failed_at stay
sticky — history is kept, current error is not.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


def _client_classes():
    from ml_agora_mcp.clients.mcp_client import (
        MCPClientAdaptor as Agora,
    )
    from ml_arete_mcp.clients.mcp_client import (
        MCPClientAdaptor as Arete,
    )
    from ml_episteme_mcp.clients.mcp_adaptor import (
        MCPClientAdaptor as Episteme,
    )
    from ml_zetesis_mcp.clients.mcp_client import (
        MCPClientAdaptor as Zetesis,
    )
    return [Episteme, Zetesis, Arete, Agora]


_CLIENT_IDS = ["episteme", "zetesis", "arete", "agora"]


class _Session:
    """Scriptable MCP session — records whether calls error."""

    def __init__(self, is_error: bool = False):
        self.is_error = is_error

    async def call_tool(self, name, args):
        return SimpleNamespace(
            is_error=self.is_error,
            content=[SimpleNamespace(text='{"ok": true}')],
        )

    async def read_resource(self, uri):
        return SimpleNamespace(
            contents=[SimpleNamespace(text='{"ok": true}')],
        )

    async def send_ping(self):
        return None


def _serve(client, session):
    """Wire a fake session + serve loop so call paths run."""
    client._session = session
    client._stop = asyncio.Event()
    client._task = asyncio.create_task(client._serve())


async def _stop(client):
    client._stop.set()
    client._task.cancel()
    try:
        await client._task
    except asyncio.CancelledError:
        pass


async def _ok_call(client):
    """One transport-level success through the client's op surface."""
    if hasattr(client, "call_tool"):
        return await client.call_tool("tool-x", {})
    return await client.read_resource("lab://status")


@pytest.mark.parametrize("cls", _client_classes(), ids=_CLIENT_IDS)
class TestLastErrorClearsOnRecovery:

    async def test_call_success_clears_last_error(self, cls):
        c = cls({})
        c.last_error = "ConnectError: All connection attempts failed"
        c.last_failed_operation = "connect"
        c.last_failed_at = "2026-01-01T00:00:00+00:00"
        _serve(c, _Session())
        try:
            await _ok_call(c)
            assert c.last_error is None
            # Attribution history survives the heal.
            assert c.last_failed_operation == "connect"
            assert c.last_failed_at == "2026-01-01T00:00:00+00:00"
        finally:
            await _stop(c)

    async def test_ping_ok_clears_last_error(self, cls):
        c = cls({})
        c.last_error = "stale transport error"
        c.last_failed_operation = "ping"
        _serve(c, _Session())
        try:
            ms = await c.ping(timeout=2.0)
            assert ms >= 0
            assert c.last_error is None
            assert c.last_probe_state == "ok"
            assert c.last_failed_operation == "ping"
        finally:
            await _stop(c)

    async def test_connect_success_clears_last_error(self, cls):
        c = cls({})
        c.last_error = "ConnectError: refused"
        c.last_failed_operation = "connect"
        c.last_failed_at = "2026-01-01T00:00:00+00:00"

        async def fake_run():
            c._ready.set()
            await asyncio.Event().wait()  # idle session

        c._run = fake_run
        await c.connect()
        try:
            assert c.last_error is None
            assert c.last_failed_operation == "connect"
        finally:
            await c.disconnect()


class TestClearBoundary:
    """An upstream error verdict is the peer's word — a channel-fault
    record it sets is legitimate, but a HEALTHY transport result
    after it must still clear."""

    async def test_error_verdict_sets_then_success_clears(self):
        from ml_zetesis_mcp.clients.mcp_client import MCPClientAdaptor

        c = MCPClientAdaptor({})
        _serve(c, _Session(is_error=True))
        try:
            with pytest.raises(Exception):
                await c.call_tool("tool-x", {})
            assert c.last_error is not None
            assert c.last_failed_operation == "tool-x"

            # Recover: same channel now answers cleanly.
            c._session = _Session(is_error=False)
            await c.call_tool("tool-x", {})
            assert c.last_error is None
            assert c.last_failed_operation == "tool-x"  # history kept
        finally:
            await _stop(c)
