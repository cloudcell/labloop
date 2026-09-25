"""Connectivity — the launch-order contract for upstream channels.

A configured channel survives a dead peer: the registry retains it,
the connectivity supervisor retries connect() until the upstream
appears, and upstream_connectivity reports who is connected in what
role to the integrity suite.
"""

from __future__ import annotations

import asyncio

import pytest

from ml_zetesis_mcp.clients.adaptors import (
    Adaptors,
    UpstreamAdaptor,
    run_connectivity_supervisor,
)
from ml_zetesis_mcp.integrity.checks import run_checks


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


async def test_registered_channel_reports_down_until_connected():
    adaptors = Adaptors()
    adaptors.register_channel(
        "evidence", "loop0-read", FakeChannelAdaptor()
    )

    report = adaptors.connectivity_report()
    assert report == [{
        "channel": "evidence",
        "role": "loop0-read",
        "target": "http://upstream/mcp",
        "state": "down",
        "attempts": 0,
        "last_error": None,
        "connected_at": None,
    }]
    # Unconfigured channels are absent — supported standalone mode,
    # not a violation.
    assert all(c["channel"] != "claims" for c in report)


async def test_supervisor_retries_until_peer_appears():
    adaptors = Adaptors()
    fake = FakeChannelAdaptor()
    fake.failures = [
        ConnectionRefusedError("down"),
        ConnectionRefusedError("down"),
    ]
    adaptors.register_channel("evidence", "loop0-read", fake)

    await _supervise(adaptors)

    assert adaptors.evidence is fake
    entry = adaptors.connectivity_report()[0]
    assert entry["state"] == "up"
    assert entry["attempts"] >= 3
    assert entry["last_error"] is None
    assert entry["connected_at"] is not None


async def test_supervisor_drops_dead_session_and_reconnects():
    adaptors = Adaptors()
    fake = FakeChannelAdaptor()
    adaptors.register_channel("claims", "claims", fake)
    await fake.connect()
    adaptors.claims = fake

    # Peer exits — the client task ends but the live slot still points
    # at the stale object.
    fake.dead = True
    await _supervise(adaptors, ticks=4)
    assert adaptors.claims is None
    entry = adaptors.connectivity_report()[0]
    assert entry["state"] == "down"
    assert entry["last_error"] == "upstream session ended"

    # Peer returns — the supervisor reconnects without a restart.
    fake.dead = False
    await _supervise(adaptors, ticks=4)
    assert adaptors.claims is fake
    assert adaptors.connectivity_report()[0]["state"] == "up"


async def test_upstream_connectivity_violation_when_down(search_store):
    adaptors = Adaptors()
    adaptors.register_channel(
        "evidence", "loop0-read", FakeChannelAdaptor()
    )
    payload = await run_checks(
        search_store, connectivity=adaptors.connectivity_report()
    )
    check = _connectivity(payload)
    assert check["ok"] is False
    v = check["violations"][0]
    assert v["channel"] == "evidence"
    assert v["role"] == "loop0-read"
    assert v["target"] == "http://upstream/mcp"


async def test_upstream_connectivity_ok_when_all_up(search_store):
    adaptors = Adaptors()
    fake = FakeChannelAdaptor()
    adaptors.register_channel("evidence", "loop0-read", fake)
    await fake.connect()
    adaptors.evidence = fake
    payload = await run_checks(
        search_store, connectivity=adaptors.connectivity_report()
    )
    check = _connectivity(payload)
    assert check["ok"] is True
    assert check["violations"] == []


async def test_upstream_connectivity_skipped_without_container(
    search_store,
):
    payload = await run_checks(search_store)
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
        await adaptor.call_tool("list_programmes", {})


async def test_connect_resets_stale_error():
    """connect() clears a previous failure so the supervisor can retry
    on the same object."""
    adaptor = UpstreamAdaptor({"url": "http://127.0.0.1:1/mcp"})
    stale = RuntimeError("stale failure")
    adaptor._error = stale
    with pytest.raises(Exception) as excinfo:
        await adaptor.connect()
    assert excinfo.value is not stale


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


async def test_launch_order_zetesis_before_upstream(tmp_path):
    """zetesis starts while its Loop-0 peer is absent; when the peer
    appears the supervisor connects without a restart, pull_evidence
    works, and upstream_connectivity reports the channel up."""
    ev_port = find_free_port(29100)
    mcp_port = find_free_port(29200)
    db = tmp_path / "search.db"
    cfg = tmp_path / "zetesis.toml"
    cfg.write_text(f'''
db_path = "{db}"

[adaptors]
reconnect_seconds = 1

[adaptors.evidence]
transport = "streamable-http"
url = "http://127.0.0.1:{ev_port}/mcp"
''')
    procs, logs = [], []
    try:
        p, lf = _spawn(
            [
                sys.executable, "-m", "ml_zetesis_mcp",
                "--transport", "http", "--port", str(mcp_port),
                "--stateless", "--db-path", str(db),
                "--config", str(cfg),
            ],
            tmp_path / "zetesis.log",
        )
        procs.append(p)
        logs.append(lf)
        wait_for_port(mcp_port, timeout=20.0)
        url = f"http://127.0.0.1:{mcp_port}"

        inv = await call_tool_http(url, "open_investigation", {
            "question": "launch-order probe",
            "scope": {"programme_ids": ["prog-launch-order"]},
        })
        inv_id = inv["investigation_id"]

        # Configured-but-down: the pull fails clearly while the
        # supervisor keeps retrying in the background.
        pull = await call_tool_http(url, "pull_evidence", {
            "investigation_id": inv_id, "source": "loop0",
            "tool": "list_active_programmes",
        })
        assert "error" in pull

        # The peer appears — no restart; the supervisor converges
        # within a few ticks.
        up, ulf = _spawn(
            [
                sys.executable,
                str(REPO_ROOT / "tests" / "downstream_mcp_server.py"),
                "--role", "evidence",
                "--transport", "http", "--port", str(ev_port),
            ],
            tmp_path / "upstream.log",
        )
        procs.append(up)
        logs.append(ulf)
        wait_for_port(ev_port, timeout=20.0)

        deadline = time.time() + 10
        while time.time() < deadline:
            pull = await call_tool_http(url, "pull_evidence", {
                "investigation_id": inv_id, "source": "loop0",
                "tool": "list_active_programmes",
            })
            if "error" not in pull:
                break
            await asyncio.sleep(0.5)
        assert "error" not in pull, pull
        assert pull["evidence_ref_id"].startswith("eref-")

        # The integrity surface reports who connected in what role.
        body = json.loads(urllib.request.urlopen(
            f"{url}/health/deep", timeout=10).read())
        conn = next(
            c for c in body["checks"]
            if c["name"] == "upstream_connectivity"
        )
        assert conn["ok"], conn
    finally:
        _reap(procs, logs)
