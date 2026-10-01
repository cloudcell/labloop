"""Regression: call_tool / read_resource must correlate results to the
request that asked for them (rc-15 F1 / W1).

Pre-fix, every adaptor drained a single shared ``_result_queue`` FIFO —
a call that timed out left its late result in the queue, and the next
call dequeued its predecessor's payload. On the lab this surfaced as
``record_promotion_verdict`` reporting the *previous* call's claim id
(13 reproductions across the rc-15 battery). The fix resolves each call
through a per-call Future, so a stale answer lands on a cancelled
future and is dropped.

The battery here drives each client's op surface (``call_tool`` where
it exists, ``read_resource`` on agora) through ``_serve`` with a
scriptable session whose first call answers late.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


def _client_classes():
    from ml_agora_mcp.clients.mcp_client import MCPClientAdaptor as A
    from ml_arete_mcp.clients.mcp_client import MCPClientAdaptor as R
    from ml_episteme_mcp.clients.mcp_adaptor import MCPClientAdaptor as E
    from ml_zetesis_mcp.clients.mcp_client import MCPClientAdaptor as Z

    return [E, Z, R, A]


_CLIENT_IDS = ["episteme", "zetesis", "arete", "agora"]


class _LateFirstSession:
    """First op sleeps past the caller's timeout, then answers late;
    later ops answer immediately with their own marker."""

    def __init__(self):
        self.calls = 0

    async def _respond(self, marker):
        self.calls += 1
        if self.calls == 1:
            await asyncio.sleep(0.5)  # late answer — outlives the timeout
        return marker

    async def call_tool(self, name, args):
        marker = await self._respond(f'{{"for": "{name}"}}')
        return SimpleNamespace(
            is_error=False, content=[SimpleNamespace(text=marker)]
        )

    async def read_resource(self, uri):
        marker = await self._respond(f'{{"for": "{uri}"}}')
        return SimpleNamespace(
            contents=[SimpleNamespace(text=marker)]
        )

    async def send_ping(self):
        return None


def _serve(client, session):
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


async def _op(client, name):
    if hasattr(client, "call_tool"):
        return await client.call_tool(name, {})
    return await client.read_resource(name)


@pytest.mark.parametrize("cls", _client_classes(), ids=_CLIENT_IDS)
async def test_late_result_cannot_desync_next_call(cls):
    """call N times out; its late answer must not reach call N+1."""
    c = cls({"call_timeout_seconds": 0.05})
    _serve(c, _LateFirstSession())
    try:
        with pytest.raises(RuntimeError, match="did not answer"):
            await _op(c, "slow-op")
        # The late result arrives ~0.5 s later — give it time to land
        # (and, pre-fix, to poison the queue).
        await asyncio.sleep(0.6)
        result = await _op(c, "fast-op")
        assert '"for": "fast-op"' in result
    finally:
        await _stop(c)


@pytest.mark.parametrize("cls", _client_classes(), ids=_CLIENT_IDS)
async def test_consecutive_calls_get_own_results(cls):
    c = cls({"call_timeout_seconds": 5})
    _serve(c, _LateFirstSession())
    try:
        # slow session but generous timeout — both calls resolve with
        # their own payloads, in order
        first = await _op(c, "op-one")
        second = await _op(c, "op-two")
        assert '"for": "op-one"' in first
        assert '"for": "op-two"' in second
    finally:
        await _stop(c)


@pytest.mark.parametrize("cls", _client_classes(), ids=_CLIENT_IDS)
async def test_late_error_result_does_not_poison_next_call(cls):
    """A late *error* is equally capable of desync — same guard."""

    class _ErrThenOk(_LateFirstSession):
        async def _respond(self, marker):
            self.calls += 1
            if self.calls == 1:
                await asyncio.sleep(0.5)
                raise ConnectionError("peer reset")
            return marker

    c = cls({"call_timeout_seconds": 0.05})
    _serve(c, _ErrThenOk())
    try:
        with pytest.raises(RuntimeError, match="did not answer"):
            await _op(c, "doomed-op")
        await asyncio.sleep(0.6)
        result = await _op(c, "recovery-op")
        assert '"for": "recovery-op"' in result
    finally:
        await _stop(c)
