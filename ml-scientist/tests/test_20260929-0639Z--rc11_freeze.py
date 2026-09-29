"""rc-11 regression tests — the episteme event-loop freeze
(plan-20260929-0628Z).

A trial opened /dev/urandom; the finalize-time digest streamed it
synchronously inside `async def run_trial` and never hit EOF — every
episteme port listened, nothing answered, ~55 min. The fixes under
test:

- W1: the digest is bounded — non-regular paths and oversize files
  produce `sha256: null` + a naming `reason`, never a hang and never
  a truncated hash.
- W2: the finalize captures run via `asyncio.to_thread` — a capture
  cannot hold the loop.
- W3: every tool call answers inside [server] tool_deadline_seconds
  with a named `deadline_exceeded`.
- W4: agora's channel supervisor bounds `connect()` — a listening-
  but-silent upstream is marked down and retried, never parks.
"""

import asyncio
import importlib
import json
import os
import threading
import time
from pathlib import Path

import pytest

from ml_episteme_mcp.state.store import StateStore, MAX_DIGEST_BYTES
from ml_episteme_mcp.state.models import (
    Hypothesis,
    Programme,
    ProgrammeStatus,
    Trial,
    TrialStatus,
)


@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "state.db")
    s.connect()
    yield s
    s.close()


def _fake_trace(artifact: Path, lines: list[str]) -> Path:
    log = artifact / "t1_2026_readtrace.strace"
    log.write_text("\n".join(lines) + "\n")
    return log


def _seed_running_trial(store, artifact_dir: Path) -> str:
    store.create_programme(Programme(
        id="prog-f", goal="g", constraints={}, allowed_variables=["x"],
        budget_max_trials=10, budget_max_wall_time_hours=1.0,
        status=ProgrammeStatus.active,
    ))
    store.create_hypothesis(Hypothesis(
        id="hyp-f", programme_id="prog-f",
        statement="s", failure_criterion="f", variables_involved=["x"],
    ))
    store.create_trial(Trial(
        id="trial-f", programme_id="prog-f", hypothesis_id="hyp-f",
        config_json="{}", status=TrialStatus.designed,
        artifact_path=str(artifact_dir),
    ))
    store.update_trial_status("trial-f", "running")
    return "trial-f"


# ---- W1: bounded digest ----------------------------------------------------


class TestBoundedDigest:
    """A trial can name any path it opened — including paths that can
    never be digested. The row must record the gap, not hang on it."""

    def test_character_device_not_digested(self, store, tmp_path):
        """The incident path-shape: /dev/null is a character device —
        same S_ISREG failure as /dev/urandom (which must NOT be used in
        a test: pre-fix it reads forever)."""
        art = tmp_path / "art"
        art.mkdir()
        _fake_trace(art, [
            '10 openat(AT_FDCWD, "/dev/null", O_RDONLY|O_CLOEXEC) = 3',
        ])

        # Bounded in time — the thread join makes a regression (a
        # digest that blocks) a test failure, not a wedged suite.
        box = {}
        t = threading.Thread(
            target=lambda: box.setdefault(
                "r", store.capture_executed_code("t1", art)
            )
        )
        t.start()
        t.join(15)
        assert not t.is_alive(), "digest blocked on a character device"

        entry = box["r"]["captured"][0]
        assert entry["role"] == "input_data"
        assert entry["sha256"] is None
        assert entry["size_bytes"] is None
        assert "not a regular file" in entry["reason"]

    def test_fifo_not_digested(self, store, tmp_path):
        """A FIFO's open() blocks until a writer arrives — pre-fix
        this hung even earlier than the read. Stat-only refusal."""
        art = tmp_path / "art"
        art.mkdir()
        fifo = tmp_path / "trial-fifo"
        os.mkfifo(fifo)
        _fake_trace(art, [
            f'10 openat(AT_FDCWD, "{fifo}", O_RDONLY|O_CLOEXEC) = 3',
        ])

        box = {}
        t = threading.Thread(
            target=lambda: box.setdefault(
                "r", store.capture_executed_code("t1", art)
            )
        )
        t.start()
        t.join(15)
        assert not t.is_alive(), "digest blocked on a FIFO"

        entry = box["r"]["captured"][0]
        assert entry["sha256"] is None
        assert "not a regular file" in entry["reason"]

    def test_oversize_file_capped_not_truncated(self, store, tmp_path):
        """A sparse file past MAX_DIGEST_BYTES: null + cap reason —
        never a truncated hash that looks real and matches nothing."""
        art = tmp_path / "art"
        art.mkdir()
        big = tmp_path / "big.bin"
        with open(big, "wb") as fh:
            fh.truncate(MAX_DIGEST_BYTES + 1)
        _fake_trace(art, [
            f'10 openat(AT_FDCWD, "{big}", O_RDONLY|O_CLOEXEC) = 3',
        ])
        entry = store.capture_executed_code("t1", art)["captured"][0]
        assert entry["sha256"] is None
        assert "digest cap" in entry["reason"]

    def test_regular_file_still_digested(self, store, tmp_path):
        """Positive control — an ordinary input digests as before."""
        art = tmp_path / "art"
        art.mkdir()
        data = tmp_path / "input.json"
        data.write_text('{"x": 1}\n')
        _fake_trace(art, [
            f'10 openat(AT_FDCWD, "{data}", O_RDONLY|O_CLOEXEC) = 3',
        ])
        entry = store.capture_executed_code("t1", art)["captured"][0]
        assert entry["sha256"] is not None
        assert entry["sha256"].startswith("sha256:")
        assert entry["reason"] if "reason" in entry else True


# ---- W2: finalize captures run off the event loop --------------------------


class TestFinalizeOffLoop:
    """_finalize_trial is async and hands each capture to a worker
    thread — the loop stays schedulable while the capture runs."""

    @pytest.mark.asyncio
    async def test_finalize_with_fifo_does_not_block_loop(
        self, store, tmp_path
    ):
        from ml_episteme_mcp.tools.trial import _finalize_trial

        art = tmp_path / "art"
        art.mkdir()
        fifo = tmp_path / "trial-fifo"
        os.mkfifo(fifo)
        _fake_trace(art, [
            f'10 openat(AT_FDCWD, "{fifo}", O_RDONLY|O_CLOEXEC) = 3',
        ])
        tid = _seed_running_trial(store, art)
        output = json.dumps({
            "status": "completed", "exit_code": 0, "stdout": "{}",
        })

        ticks = 0
        stop = False

        async def probe():
            nonlocal ticks
            while not stop:
                ticks += 1
                await asyncio.sleep(0)

        probe_task = asyncio.create_task(probe())
        try:
            # A regression (capture back on the loop, or an unbounded
            # path) turns this into a timeout, not a hang.
            result = await asyncio.wait_for(
                _finalize_trial(store, tid, output), timeout=15
            )
        finally:
            stop = True
            await probe_task

        assert ticks > 0, "event loop starved during finalize"
        payload = json.loads(result.content[0].text)
        assert payload["status"] == "completed"

        # The capture ran end-to-end: its manifest was ingested as a
        # trial artifact (the staging dir is cleaned afterwards —
        # the store is where it lives). The FIFO row's reason shape
        # is covered by the W1 tests above.
        names = {
            f["filename"]
            for f in payload["artifacts"]["captured"]
        }
        assert "executed_code.json" in names


# ---- W3: per-call tool deadline --------------------------------------------


class TestToolDeadline:
    """Every tool call answers inside [server] tool_deadline_seconds.
    The deadline is a response bound (shield, not cancel): a sync spin
    can't be killed anyway, and unwinding mid-write would risk the
    store's transaction."""

    @pytest.mark.asyncio
    async def test_slow_tool_returns_deadline_exceeded(self, store):
        from mcp.client import Client
        from ml_episteme_mcp.server import create_server

        mcp = create_server(
            store, server_config={"tool_deadline_seconds": 0.3}
        )

        @mcp.tool()
        async def slow_op() -> str:
            """A test tool that outlives the deadline."""
            await asyncio.sleep(3)
            return "done"

        client = Client(mcp)
        async with client:
            t0 = time.monotonic()
            result = await client.call_tool("slow_op", {})
            elapsed = time.monotonic() - t0
            assert result.is_error
            payload = json.loads(result.content[0].text)
            assert "deadline_exceeded" in payload["error"]

            # The server still answers — the deadline didn't kill the
            # handler's loop.
            r = await client.call_tool("list_programmes", {})
            assert not r.is_error
        assert elapsed < 2.0, "deadline did not bound the response"

    @pytest.mark.asyncio
    async def test_fast_tool_under_deadline_passes(self, store):
        from mcp.client import Client
        from ml_episteme_mcp.server import create_server

        mcp = create_server(
            store, server_config={"tool_deadline_seconds": 5.0}
        )
        client = Client(mcp)
        async with client:
            r = await client.call_tool("list_programmes", {})
            assert not r.is_error


# ---- W4: bounded channel connect -------------------------------------------


class _NeverConnectAdaptor:
    """A peer that accepts TCP and never finishes the handshake —
    the listening-but-unresponsive upstream the supervisor must not
    park on."""

    def __init__(self):
        self.config = {"url": "http://127.0.0.1:9/mcp"}

    async def connect(self):
        await asyncio.sleep(60)

    async def disconnect(self):
        pass


class TestChannelConnectDeadline:
    @pytest.mark.asyncio
    async def test_supervisor_marks_down_and_keeps_cycling(self):
        from ml_agora_mcp.clients.adaptors import (
            Adaptors,
            run_connectivity_supervisor,
        )

        adaptors = Adaptors()
        adaptors.register_channel(
            "loop0", "protocol", _NeverConnectAdaptor()
        )
        spec = adaptors._channels["loop0"]

        task = asyncio.create_task(
            run_connectivity_supervisor(
                adaptors, 0.05,
                probe_timeout_seconds=0.15,
                log=lambda *a: None,
            )
        )
        try:
            await asyncio.sleep(0.6)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert spec.state == "down"
        assert spec.last_error is not None
        assert "timed out" in spec.last_error
        # The proof it never parked: attempts kept accruing across
        # ticks — a wedged connect froze this counter at the incident.
        assert spec.attempts >= 2


# ---- S1 (watchdog-roll plan): sibling-server coverage ------------------------
#
# The incident fixes landed on episteme + agora, but the wedge class is
# generic: any sibling tool handler that stalls freezes that server the
# same way, and zetesis/arete run the same connectivity supervisor —
# an unbounded connect() there parks identically.


def _sibling_server(kind: str, tmp_path: Path):
    """Build each sibling's MCPServer with a live store and a short
    tool deadline. Returns (server, store|None) so callers can close."""
    cfg = {"tool_deadline_seconds": 0.3}
    if kind == "zetesis":
        from ml_zetesis_mcp.server import create_server
        from ml_zetesis_mcp.state.store import SearchStore
        store = SearchStore(str(tmp_path / "z.db"))
        store.connect()
        return create_server(store, server_config=cfg), store
    if kind == "arete":
        from ml_arete_mcp.server import create_server
        from ml_arete_mcp.state.store import ImproverStore
        store = ImproverStore(str(tmp_path / "a.db"))
        store.connect()
        return create_server(store, server_config=cfg), store
    if kind == "anamnesis":
        from ml_anamnesis_mcp.server import create_server
        from ml_anamnesis_mcp.state.store import MemoryStore
        store = MemoryStore(str(tmp_path / "m.db"))
        store.connect()
        return create_server(store, server_config=cfg), store
    if kind == "agora":
        from ml_agora_mcp.clients.adaptors import Adaptors
        from ml_agora_mcp.server import create_server
        return (
            create_server(
                Adaptors(),
                log_dir=tmp_path / "agora-logs",
                server_config=cfg,
            ),
            None,
        )
    raise AssertionError(f"unknown sibling {kind}")


class TestSiblingToolDeadline:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "kind", ["zetesis", "arete", "anamnesis", "agora"]
    )
    async def test_slow_tool_returns_deadline_exceeded(
        self, kind, tmp_path
    ):
        from mcp.client import Client

        mcp, store = _sibling_server(kind, tmp_path)
        try:

            @mcp.tool()
            async def slow_op() -> str:
                """A test tool that outlives the deadline."""
                await asyncio.sleep(3)
                return "done"

            @mcp.tool()
            async def fast_op() -> str:
                """A test tool inside the deadline."""
                return "ok"

            client = Client(mcp)
            async with client:
                t0 = time.monotonic()
                result = await client.call_tool("slow_op", {})
                elapsed = time.monotonic() - t0
                assert result.is_error
                payload = json.loads(result.content[0].text)
                assert "deadline_exceeded" in payload["error"]

                r = await client.call_tool("fast_op", {})
                assert not r.is_error
            assert elapsed < 2.0, "deadline did not bound the response"
        finally:
            if store is not None:
                store.close()


class TestSiblingConnectDeadline:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("mod", ["ml_zetesis_mcp", "ml_arete_mcp"])
    async def test_supervisor_marks_down_and_keeps_cycling(self, mod):
        m = importlib.import_module(f"{mod}.clients.adaptors")
        adaptors = m.Adaptors()
        adaptors.register_channel(
            "peer", "protocol", _NeverConnectAdaptor()
        )
        spec = adaptors._channels["peer"]

        task = asyncio.create_task(
            m.run_connectivity_supervisor(
                adaptors, 0.05,
                probe_timeout_seconds=0.15,
                log=lambda *a: None,
            )
        )
        try:
            await asyncio.sleep(0.6)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert spec.state == "down"
        assert spec.last_error is not None
        assert "timed out" in spec.last_error
        assert spec.attempts >= 2
