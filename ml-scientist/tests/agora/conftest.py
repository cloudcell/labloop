"""Shared fixtures for the agora (status hub) test battery.

In-process tests use duck-typed fake read-only adaptors — no MCP
transport, no subprocess. The fakes mimic MCPClientAdaptor's public
surface: .config, connect/disconnect, session_dead, read_resource.
"""

from __future__ import annotations

import json

import pytest


def _digest(server: str, role: str, **overrides) -> dict:
    """A minimal contract-shaped digest for a fake upstream."""
    d = {
        "server": server,
        "role": role,
        "generated_at": "2026-09-18T15:00:00+00:00",
        "workflow_position": None,
        "open_work": [],
        "blockers": [],
        "recommended_next": [],
        "upstream_summary": {
            "configured": 0, "up": 0, "down": 0, "verdict": "ok",
        },
        "integrity_summary": {"verdict": "no_runs"},
    }
    d.update(overrides)
    return d


class FakeReadAdaptor:
    """Duck-typed read-only upstream — serves canned digests."""

    def __init__(
        self, server: str = "ml-fake-mcp", role: str = "loop0",
        digest: dict | None = None, fail: bool = False,
        payloads: dict | None = None,
    ):
        self.config = {"url": f"http://fake-{server}:8000/mcp"}
        self.digest = digest or _digest(server, role)
        self.fail = fail
        # Per-URI overrides (e.g. "search://graph" -> graph payload);
        # anything unmapped falls back to the digest.
        self.payloads = payloads or {}
        self.reads: list[str] = []
        self._dead = False

    async def connect(self):
        if self.fail:
            raise ConnectionError("fake upstream refused")

    async def disconnect(self):
        self._dead = True

    def session_dead(self) -> bool:
        return self._dead

    async def read_resource(self, uri: str):
        self.reads.append(uri)
        if self.fail:
            raise RuntimeError("read failed")
        return json.dumps(self.payloads.get(uri, self.digest))


@pytest.fixture
def digest_factory():
    return _digest


@pytest.fixture
def adaptors():
    """An Adaptors container with all four channels wired to fakes."""
    from ml_agora_mcp.clients.adaptors import Adaptors

    a = Adaptors()
    for name, server, role in (
        ("claims", "ml-anamnesis-mcp", "claims"),
        ("loop0", "ml-episteme-mcp", "loop0"),
        ("loop1", "ml-zetesis-mcp", "loop1"),
        ("loop2", "ml-arete-mcp", "loop2"),
    ):
        fake = FakeReadAdaptor(server, role)
        a.register_channel(name, role, fake)
        setattr(a, name, fake)  # simulate a connected channel
        a._channels[name].mark_up()
    return a


@pytest.fixture
def agora_server(adaptors, tmp_path):
    """An in-process agora MCPServer bound to fake channels."""
    from ml_agora_mcp.server import create_server

    return create_server(adaptors, log_dir=tmp_path / "logs")
