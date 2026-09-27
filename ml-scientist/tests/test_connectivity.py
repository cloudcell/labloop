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


# --- Fault attribution — a dead channel names its trigger ---


def test_describe_error_unwraps_exception_groups():
    """'unhandled errors in a TaskGroup' must name the leaf cause."""
    from ml_episteme_mcp.clients.mcp_adaptor import describe_error

    leaf = ConnectionRefusedError(111, "Connection refused")
    wrapped = ExceptionGroup("unhandled errors in a TaskGroup", [leaf])
    assert describe_error(wrapped) == (
        "ConnectionRefusedError: [Errno 111] Connection refused"
    )
    nested = ExceptionGroup("outer", [
        ExceptionGroup("inner", [RuntimeError("boom")])
    ])
    assert describe_error(nested) == "RuntimeError: boom"
    assert describe_error(leaf) == (
        "ConnectionRefusedError: [Errno 111] Connection refused"
    )


async def test_call_failure_records_operation_and_cause():
    """call_tool attributes the fault: name, unwrapped cause, when."""
    from ml_episteme_mcp.clients.mcp_adaptor import MCPClientAdaptor

    a = MCPClientAdaptor({"call_timeout_seconds": 1})
    a._session = object()  # non-None gate — no _serve task needed
    await a._result_queue.put((
        "error", ExceptionGroup("TaskGroup", [OSError("pipe broke")])
    ))
    with pytest.raises(ExceptionGroup):
        await a.call_tool("assert_claim", {"subject": "x"})
    assert a.last_operation == "assert_claim"
    assert a.last_failed_operation == "assert_claim"
    assert a.last_error == "OSError: pipe broke"
    assert a.last_failed_at is not None


async def test_call_timeout_records_operation():
    from ml_episteme_mcp.clients.mcp_adaptor import MCPClientAdaptor

    a = MCPClientAdaptor({"call_timeout_seconds": 0.05})
    a._session = object()
    with pytest.raises(RuntimeError, match="did not answer"):
        await a.call_tool("pull_evidence", {})
    assert a.last_failed_operation == "pull_evidence"
    assert "did not answer" in a.last_error


async def test_supervisor_connect_error_is_unwrapped():
    """A failed connect records the leaf cause, not the shell."""
    adaptor = MCPAdaptor({})
    fake = FakeClaimsRole()
    fake.failures = [
        ExceptionGroup(
            "unhandled errors in a TaskGroup",
            [ConnectionRefusedError(111, "Connection refused")],
        )
        for _ in range(50)
    ]
    adaptor.register_claims(fake, {"url": "http://anamnesis/mcp"})

    await _supervise(adaptor, ticks=3)

    claims = next(
        c for c in adaptor.connectivity_report()
        if c["channel"] == "claims"
    )
    assert claims["state"] == "down"
    assert "TaskGroup" not in claims["last_error"]
    assert "ConnectionRefusedError" in claims["last_error"]


async def test_session_death_names_in_flight_operation():
    """'upstream session ended' names the call it died serving —
    transiently in last_error, persistently in last_failed_operation
    (a reconnect clears the error but keeps the attribution)."""
    adaptor = MCPAdaptor({})
    fake = FakeClaimsRole()
    adaptor.register_claims(fake, {"url": "http://anamnesis/mcp"})
    await fake.connect()
    adaptor.set_claims(fake)

    fake.in_flight_operation = "assert_claim"
    fake.dead = True

    # Reconnect wedges mid-call so the death attribution is observable —
    # last_error records *why the channel is down right now* and a later
    # connect outcome legitimately overwrites it.
    release = asyncio.Event()

    async def blocked_connect():
        await release.wait()

    fake.connect = blocked_connect
    task = asyncio.create_task(
        run_claims_supervisor(adaptor, 0.02, log=lambda m: None)
    )
    await asyncio.sleep(0.1)
    claims = next(
        c for c in adaptor.connectivity_report()
        if c["channel"] == "claims"
    )
    assert claims["last_error"] == (
        "upstream session ended during assert_claim"
    )
    assert claims["last_failed_operation"] == "assert_claim"
    assert claims["last_failed_at"] is not None

    fake.dead = False
    await asyncio.sleep(0.1)
    # Supervisor is parked inside the blocked connect — the death record
    # still stands.
    claims = next(
        c for c in adaptor.connectivity_report()
        if c["channel"] == "claims"
    )
    assert "upstream session ended during assert_claim" in (
        claims["last_error"]
    )

    # Attribution survives the error clearing on a real reconnect.
    release.set()
    await asyncio.sleep(0.15)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    claims = next(
        c for c in adaptor.connectivity_report()
        if c["channel"] == "claims"
    )
    assert claims["last_error"] is None
    assert claims["last_failed_operation"] == "assert_claim"


def test_report_carries_fault_fields():
    """connectivity_report exposes last/failed operation + when."""
    adaptor = MCPAdaptor({})
    fake = FakeClaimsRole()
    fake.last_operation = "recall"
    fake.last_failed_operation = "assert_claim"
    fake.last_failed_at = "2026-09-26T21:00:00+00:00"
    fake.last_error = "OSError: pipe broke"
    adaptor.register_claims(fake, {"url": "http://anamnesis/mcp"})

    claims = next(
        c for c in adaptor.connectivity_report()
        if c["channel"] == "claims"
    )
    assert claims["last_operation"] == "recall"
    assert claims["last_failed_operation"] == "assert_claim"
    assert claims["last_failed_at"] == "2026-09-26T21:00:00+00:00"
    assert claims["last_error"] == "OSError: pipe broke"


def test_violation_names_triggering_operation(store):
    """The upstream_connectivity violation carries the failed op."""
    adaptor = MCPAdaptor({})
    fake = FakeClaimsRole()
    fake.last_failed_operation = "assert_claim"
    adaptor.register_claims(fake, {"url": "http://anamnesis/mcp"})

    payload = run_checks(
        store, connectivity=adaptor.connectivity_report()
    )
    check = _connectivity(payload)
    v = next(
        v for v in check["violations"] if v["channel"] == "claims"
    )
    assert v["last_failed_operation"] == "assert_claim"


# --- Liveness probe — busy ≠ down ---


async def _claims_probe_setup(adaptor, fake):
    adaptor.register_claims(fake, {"url": "http://anamnesis/mcp"})
    await fake.connect()
    adaptor.set_claims(fake)


async def test_probe_ok_records_liveness():
    """A live channel is pinged each tick; ok records probe state."""
    adaptor = MCPAdaptor({})
    fake = FakeClaimsRole()

    async def ok_ping(timeout):
        return 1.5

    fake.ping = ok_ping
    await _claims_probe_setup(adaptor, fake)
    await _supervise(adaptor, ticks=3)

    claims = next(
        c for c in adaptor.connectivity_report()
        if c["channel"] == "claims"
    )
    assert claims["state"] == "up"
    assert claims["probe"] == "ok"
    assert claims["last_probe_at"] is not None
    assert adaptor.claims is fake  # session kept


async def test_probe_timeout_marks_busy_not_down():
    """A timed-out ping is BUSY — alive but unresponsive. The channel
    stays up and the session is NOT dropped: reconnecting a live peer
    would not help."""
    from ml_episteme_mcp.clients.roles import ProbeTimeout

    adaptor = MCPAdaptor({})
    fake = FakeClaimsRole()

    async def slow_ping(timeout):
        raise ProbeTimeout("no answer")

    fake.ping = slow_ping
    await _claims_probe_setup(adaptor, fake)
    await _supervise(adaptor, ticks=3)

    claims = next(
        c for c in adaptor.connectivity_report()
        if c["channel"] == "claims"
    )
    assert claims["state"] == "up"
    assert claims["probe"] == "busy"
    assert adaptor.claims is fake


async def test_probe_transport_error_marks_down_and_retries():
    """A ping transport error means the peer is gone even though the
    session task lingers — drop, mark down with the leaf cause, and
    let the retry path take over."""
    adaptor = MCPAdaptor({})
    fake = FakeClaimsRole()

    async def dead_ping(timeout):
        raise ConnectionResetError("pipe broke")

    fake.ping = dead_ping
    await _claims_probe_setup(adaptor, fake)

    # Freeze the reconnect so the ping-failure record is observable —
    # a later connect outcome legitimately overwrites last_error.
    release = asyncio.Event()

    async def blocked_connect():
        await release.wait()

    task = asyncio.create_task(
        run_claims_supervisor(
            adaptor, 0.02, probe_timeout_seconds=0.05,
            log=lambda m: None,
        )
    )
    await asyncio.sleep(0.06)
    fake.connect = blocked_connect
    await asyncio.sleep(0.08)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    claims = next(
        c for c in adaptor.connectivity_report()
        if c["channel"] == "claims"
    )
    assert claims["state"] == "down"
    assert claims["last_error"] == (
        "ping failed: ConnectionResetError: pipe broke"
    )
    assert fake.disconnect_calls >= 1


async def test_ping_routes_through_serve_queue():
    """ping() goes through the call queue and matches answers by seq —
    a stale answer is discarded, never desyncs a later probe."""
    from ml_episteme_mcp.clients.mcp_adaptor import MCPClientAdaptor
    from ml_episteme_mcp.clients.roles import ProbeTimeout

    a = MCPClientAdaptor({"call_timeout_seconds": 1})
    a._session = object()  # non-None gate

    async def drive():
        name, seq = await a._call_queue.get()
        assert name == "__ping__"
        # a stale answer to an earlier probe must be discarded
        await a._probe_queue.put(("ok", seq + 100))
        await a._probe_queue.put(("ok", seq))

    t = asyncio.create_task(drive())
    rtt = await a.ping(timeout=1.0)
    await t
    assert rtt >= 0
    assert a.last_probe_state == "ok"

    # Nobody answers — busy, not a transport failure.
    with pytest.raises(ProbeTimeout):
        await a.ping(timeout=0.05)
    assert a.last_probe_state == "busy"


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
