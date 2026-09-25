"""Connectivity — the launch-order contract for upstream channels.

A configured channel survives a dead peer: the registry retains it,
the connectivity supervisor retries connect() until the upstream
appears, and upstream_connectivity reports who is connected in what
role to the integrity suite.
"""

from __future__ import annotations

import asyncio

import pytest

from ml_arete_mcp.clients.adaptors import (
    Adaptors,
    UpstreamAdaptor,
    run_connectivity_supervisor,
)
from ml_arete_mcp.integrity.checks import run_checks


class FakeChannelAdaptor:
    """Duck-typed MCP client — scriptable connect() and a killable
    session, no real transport."""

    def __init__(self, url: str = "http://upstream/mcp"):
        self.config = {"url": url}
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.failures: list[Exception] = []
        self.dead = False

    async def connect(self):
        self.connect_calls += 1
        if self.failures:
            raise self.failures.pop(0)

    async def disconnect(self):
        self.disconnect_calls += 1

    def session_dead(self):
        return self.dead


async def _supervise(adaptors, interval=0.02, ticks=8):
    """Run the supervisor for a few ticks, then cancel."""
    task = asyncio.create_task(
        run_connectivity_supervisor(
            adaptors, interval, log=lambda m: None
        )
    )
    await asyncio.sleep(interval * ticks)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def _connectivity(payload):
    return next(
        c for c in payload["checks"]
        if c["name"] == "upstream_connectivity"
    )


async def test_registered_channels_report_roles():
    adaptors = Adaptors()
    adaptors.register_channel("loop0", "loop0-read", FakeChannelAdaptor(
        "http://loop0/mcp"))
    adaptors.register_channel("loop1", "loop1-read", FakeChannelAdaptor(
        "http://loop1/mcp"))
    adaptors.register_channel("claims", "claims", FakeChannelAdaptor(
        "http://anamnesis/mcp"))

    report = adaptors.connectivity_report()
    assert {(c["channel"], c["role"]) for c in report} == {
        ("loop0", "loop0-read"),
        ("loop1", "loop1-read"),
        ("claims", "claims"),
    }
    assert all(c["state"] == "down" for c in report)


async def test_supervisor_retries_until_peer_appears():
    adaptors = Adaptors()
    fake = FakeChannelAdaptor()
    fake.failures = [ConnectionRefusedError("down")]
    adaptors.register_channel("loop1", "loop1-read", fake)

    await _supervise(adaptors)

    assert adaptors.loop1 is fake
    entry = adaptors.connectivity_report()[0]
    assert entry["state"] == "up"
    assert entry["attempts"] >= 2


async def test_supervisor_drops_dead_session_and_reconnects():
    adaptors = Adaptors()
    fake = FakeChannelAdaptor()
    adaptors.register_channel("loop0", "loop0-read", fake)
    await fake.connect()
    adaptors.loop0 = fake

    fake.dead = True
    await _supervise(adaptors, ticks=4)
    assert adaptors.loop0 is None
    assert (adaptors.connectivity_report()[0]["last_error"]
            == "upstream session ended")

    fake.dead = False
    await _supervise(adaptors, ticks=4)
    assert adaptors.loop0 is fake
    assert adaptors.connectivity_report()[0]["state"] == "up"


async def test_upstream_connectivity_violation_when_down(improver_store):
    adaptors = Adaptors()
    adaptors.register_channel("loop1", "loop1-read", FakeChannelAdaptor())
    payload = await run_checks(
        improver_store, connectivity=adaptors.connectivity_report()
    )
    check = _connectivity(payload)
    assert check["ok"] is False
    v = check["violations"][0]
    assert v["channel"] == "loop1"
    assert v["role"] == "loop1-read"


async def test_upstream_connectivity_ok_when_all_up(improver_store):
    adaptors = Adaptors()
    fake = FakeChannelAdaptor()
    adaptors.register_channel("claims", "claims", fake)
    await fake.connect()
    adaptors.claims = fake
    payload = await run_checks(
        improver_store, connectivity=adaptors.connectivity_report()
    )
    check = _connectivity(payload)
    assert check["ok"] is True
    assert check["violations"] == []


async def test_upstream_connectivity_skipped_without_container(
    improver_store,
):
    payload = await run_checks(improver_store)
    check = _connectivity(payload)
    assert check["ok"] is True
    assert "skipped" in check["detail"]


async def test_call_tool_times_out_on_dead_session():
    """A dead peer must never wedge a tool call forever — the result
    wait is bounded by call_timeout_seconds."""
    adaptor = UpstreamAdaptor({
        "url": "http://127.0.0.1:1/mcp",
        "call_timeout_seconds": 0.05,
    })
    adaptor._session = object()  # stale session; no _run consuming
    with pytest.raises(RuntimeError, match="did not answer"):
        await adaptor.call_tool("list_investigations", {})


# --- Real-transport launch-order battery ---

import json
import os
import subprocess
import sys
import time
import urllib.request

from .conftest import (
    REPO_ROOT,
    call_tool_http,
    find_free_port,
    wait_for_port,
)


def _spawn(cmd, log_path):
    lf = open(log_path, "w")
    proc = subprocess.Popen(
        cmd,
        stdout=lf,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        cwd=str(REPO_ROOT),
    )
    return proc, lf


def _reap(procs, logs):
    for p in procs:
        p.terminate()
    for p in procs:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
    for lf in logs:
        lf.close()


async def test_launch_order_arete_before_loop1(tmp_path):
    """arete starts while its Loop-1 peer is absent; when the peer
    appears the supervisor connects without a restart and
    upstream_connectivity reports the channel up."""
    loop1_port = find_free_port(29300)
    mcp_port = find_free_port(29400)
    db = tmp_path / "improver.db"
    cfg = tmp_path / "arete.toml"
    cfg.write_text(f'''
db_path = "{db}"

[adaptors]
reconnect_seconds = 1

[adaptors.loop1]
transport = "streamable-http"
url = "http://127.0.0.1:{loop1_port}/mcp"
''')
    procs, logs = [], []
    try:
        p, lf = _spawn(
            [
                sys.executable, "-m", "ml_arete_mcp",
                "--transport", "http", "--port", str(mcp_port),
                "--stateless", "--db-path", str(db),
                "--config", str(cfg),
            ],
            tmp_path / "arete.log",
        )
        procs.append(p)
        logs.append(lf)
        wait_for_port(mcp_port, timeout=20.0)
        url = f"http://127.0.0.1:{mcp_port}"

        # Configured-but-down is a violation before the peer appears.
        body = json.loads(urllib.request.urlopen(
            f"{url}/health/deep", timeout=10).read())
        conn = next(
            c for c in body["checks"]
            if c["name"] == "upstream_connectivity"
        )
        assert not conn["ok"], conn
        assert conn["violations"][0]["channel"] == "loop1"

        # The peer appears — the supervisor converges; the check
        # clears without a restart.
        up, ulf = _spawn(
            [
                sys.executable,
                str(REPO_ROOT / "tests" / "downstream_mcp_server.py"),
                "--role", "evidence",
                "--transport", "http", "--port", str(loop1_port),
            ],
            tmp_path / "upstream.log",
        )
        procs.append(up)
        logs.append(ulf)
        wait_for_port(loop1_port, timeout=20.0)

        deadline = time.time() + 10
        ok = False
        while time.time() < deadline:
            body = json.loads(urllib.request.urlopen(
                f"{url}/health/deep", timeout=10).read())
            conn = next(
                c for c in body["checks"]
                if c["name"] == "upstream_connectivity"
            )
            if conn["ok"]:
                ok = True
                break
            await asyncio.sleep(0.5)
        assert ok, conn
    finally:
        _reap(procs, logs)
