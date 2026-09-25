"""Connectivity — the claims channel survives anamnesis restarts.

The claims adaptor is a registered upstream channel, not a one-shot
role: a failed connect stays registered, the supervisor retries, and
upstream_connectivity reports who is connected in what role.
"""

from __future__ import annotations

import asyncio

import pytest

from ml_episteme_mcp.clients.adaptor import (
    MCPAdaptor,
    run_claims_supervisor,
)
from ml_episteme_mcp.integrity.checks import run_checks
from ml_episteme_mcp.state.store import StateStore


@pytest.fixture
def store(tmp_path):
    s = StateStore(str(tmp_path / "state.db"))
    s.connect()
    yield s
    s.close()


class FakeClaimsRole:
    """Duck-typed claims role — scriptable connect() and a killable
    session, no real transport."""

    def __init__(self, url: str = "http://anamnesis/mcp"):
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


async def _supervise(adaptor, interval=0.02, ticks=8):
    task = asyncio.create_task(
        run_claims_supervisor(adaptor, interval, log=lambda m: None)
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


def test_report_covers_roles_and_claims_channel():
    adaptor = MCPAdaptor({})
    adaptor.register_claims(
        FakeClaimsRole(), {"url": "http://anamnesis/mcp"}
    )

    report = adaptor.connectivity_report()
    by_channel = {c["channel"]: c for c in report}
    # Who is connected in what role — all four slots reported.
    assert set(by_channel) == {
        "optimizer", "executor", "data_source", "claims"
    }
    claims = by_channel["claims"]
    assert claims["role"] == "claims"
    assert claims["target"] == "http://anamnesis/mcp"
    assert claims["state"] == "down"


async def test_claims_supervisor_retries_until_peer_appears():
    adaptor = MCPAdaptor({})
    fake = FakeClaimsRole()
    fake.failures = [ConnectionRefusedError("down")]
    adaptor.register_claims(fake, {"url": "http://anamnesis/mcp"})

    await _supervise(adaptor)

    assert adaptor.claims is fake
    claims = next(
        c for c in adaptor.connectivity_report()
        if c["channel"] == "claims"
    )
    assert claims["state"] == "up"
    assert claims["attempts"] >= 2
    assert claims["connected_at"] is not None


async def test_claims_supervisor_drops_dead_session():
    adaptor = MCPAdaptor({})
    fake = FakeClaimsRole()
    adaptor.register_claims(fake, {"url": "http://anamnesis/mcp"})
    await fake.connect()
    adaptor.set_claims(fake)

    fake.dead = True
    await _supervise(adaptor, ticks=4)
    assert adaptor.claims is None

    fake.dead = False
    await _supervise(adaptor, ticks=4)
    assert adaptor.claims is fake


def test_upstream_connectivity_flags_down_claims(store):
    adaptor = MCPAdaptor({})
    adaptor.register_claims(
        FakeClaimsRole(), {"url": "http://anamnesis/mcp"}
    )
    payload = run_checks(
        store, connectivity=adaptor.connectivity_report()
    )
    check = _connectivity(payload)
    assert check["ok"] is False
    channels = {v["channel"] for v in check["violations"]}
    assert "claims" in channels


def test_upstream_connectivity_clean_when_wired(store):
    from ml_episteme_mcp.clients.adaptor import create_stub_adaptor

    adaptor = create_stub_adaptor()  # local roles → "up", no claims
    payload = run_checks(
        store, connectivity=adaptor.connectivity_report()
    )
    check = _connectivity(payload)
    assert check["ok"] is True
    # claims absent from the report — unconfigured is standalone mode,
    # not a violation.
    assert all(
        v.get("channel") != "claims" for v in check["violations"]
    )


def test_upstream_connectivity_skipped_without_container(store):
    payload = run_checks(store)
    check = _connectivity(payload)
    assert check["ok"] is True
    assert "skipped" in check["detail"]


# --- Real-transport launch-order battery ---

import json
import os
import subprocess
import sys
import time
import urllib.request

from conftest import find_free_port, wait_for_port

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _spawn(cmd, log_path):
    lf = open(log_path, "w")
    proc = subprocess.Popen(
        cmd,
        stdout=lf,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        cwd=REPO_ROOT,
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


def _connectivity_at(url):
    body = json.loads(urllib.request.urlopen(
        f"{url}/health/deep", timeout=10).read())
    return next(
        c for c in body["checks"]
        if c["name"] == "upstream_connectivity"
    )


async def test_launch_order_episteme_before_anamnesis(tmp_path):
    """ml-episteme starts while anamnesis is absent; when the claims
    peer appears the supervisor connects without a restart and
    upstream_connectivity reports the claims channel up."""
    claims_port = find_free_port(29500)
    mcp_port = find_free_port(29550)
    db = tmp_path / "state.db"
    cfg = tmp_path / "ml-episteme.toml"
    cfg.write_text(f'''
db_path = "{db}"

[adaptors]
reconnect_seconds = 1

[adaptors.claims]
transport = "streamable-http"
url = "http://127.0.0.1:{claims_port}/mcp"
''')
    procs, logs = [], []
    try:
        p, lf = _spawn(
            [
                sys.executable, "-m", "ml_episteme_mcp",
                "--transport", "http", "--port", str(mcp_port),
                "--stateless", "--db-path", str(db),
                "--config", str(cfg),
            ],
            tmp_path / "episteme.log",
        )
        procs.append(p)
        logs.append(lf)
        wait_for_port(mcp_port, timeout=20.0)
        url = f"http://127.0.0.1:{mcp_port}"

        # Configured-but-down claims is a violation; the local roles
        # are up.
        conn = _connectivity_at(url)
        assert not conn["ok"], conn
        claims_v = next(
            v for v in conn["violations"] if v["channel"] == "claims"
        )
        assert claims_v["role"] == "claims"

        # Anamnesis appears — the supervisor converges; the check
        # clears without a restart.
        up, ulf = _spawn(
            [
                sys.executable,
                os.path.join(
                    REPO_ROOT, "tests", "downstream_mcp_server.py"
                ),
                "--role", "claims",
                "--transport", "http", "--port", str(claims_port),
            ],
            tmp_path / "anamnesis.log",
        )
        procs.append(up)
        logs.append(ulf)
        wait_for_port(claims_port, timeout=20.0)

        deadline = time.time() + 10
        ok = False
        while time.time() < deadline:
            conn = _connectivity_at(url)
            if conn["ok"]:
                ok = True
                break
            await asyncio.sleep(0.5)
        assert ok, conn
    finally:
        _reap(procs, logs)
