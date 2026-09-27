"""rc-7 regressions — agora.

Q6  read_resource reads lab:// resources locally and proxies every
    other registered scheme through the owning channel — the
    lab-wide read surface the diagnostics needed. Unknown schemes,
    unconfigured channels, and dead channels error explicitly.
"""

from __future__ import annotations

import json

from mcp.client import Client

from .conftest import FakeReadAdaptor


async def _read(server, uri: str) -> dict:
    async with Client(server) as client:
        result = await client.call_tool("read_resource", {"uri": uri})
        text = result.content[0].text
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"error": text}


class TestReadResource:
    async def test_reads_lab_status_locally(self, agora_server):
        r = await _read(agora_server, "lab://status")
        assert "error" not in r, r
        body = json.loads(r["contents"][0]["content"])
        assert body["server"] == "ml-agora-mcp"

    async def test_reads_lab_topology_locally(self, agora_server):
        r = await _read(agora_server, "lab://topology")
        assert "error" not in r, r
        assert isinstance(
            json.loads(r["contents"][0]["content"]), dict
        )

    async def test_proxies_upstream_resource(
        self, agora_server, adaptors
    ):
        """improver://classes rides the loop2 channel."""
        adaptors.loop2.payloads["improver://classes"] = {
            "server": "ml-arete-mcp", "classes": {},
        }
        r = await _read(agora_server, "improver://classes")
        assert "error" not in r, r
        assert r["via"] == "loop2"
        body = json.loads(r["contents"][0]["content"])
        assert body["server"] == "ml-arete-mcp"
        assert adaptors.loop2.reads == ["improver://classes"]

    async def test_routes_each_scheme_to_its_channel(
        self, agora_server, adaptors
    ):
        for uri, channel in (
            ("claims://status", "claims"),
            ("protocol://status", "loop0"),
            ("trial://t-1/artifacts", "loop0"),
            ("search://status", "loop1"),
            ("improver://status", "loop2"),
        ):
            r = await _read(agora_server, uri)
            assert "error" not in r, (uri, r)
            assert r["via"] == channel

    async def test_unknown_scheme_errors(self, agora_server):
        r = await _read(agora_server, "mystery://thing")
        assert "error" in r
        assert "scheme" in r["error"] or "unknown" in r["error"]

    async def test_unconfigured_channel_errors(self, tmp_path):
        """A scheme with no wired channel errors honestly."""
        from ml_agora_mcp.clients.adaptors import Adaptors
        from ml_agora_mcp.server import create_server

        bare = Adaptors()  # nothing registered
        server = create_server(bare, log_dir=tmp_path / "logs")
        r = await _read(server, "search://status")
        assert "error" in r
        assert "not configured" in r["error"]

    async def test_dead_channel_errors(self, adaptors, tmp_path):
        from ml_agora_mcp.server import create_server

        await adaptors.loop1.disconnect()  # session_dead() → True
        server = create_server(adaptors, log_dir=tmp_path / "logs")
        r = await _read(server, "search://status")
        assert "error" in r
        assert "down" in r["error"]
