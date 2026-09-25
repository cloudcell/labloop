"""Shared fixtures for the MCP HTTP test battery.

Starts the MCP server over HTTP on unused high-number ports, using a
temporary database at /tmp/ml-sci-test-<timestamp>.db. Never touches
the production database at ~/.ml-episteme/state.db.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

# starlette's testclient (1.6.0) resolves the lazy alias
# `anyio.abc.BlockingPortal`, which is deprecated in anyio>=4.4 and
# emits a DeprecationWarning on import. Assign the canonical class
# before starlette.testclient is imported anywhere in the test suite.
import anyio.abc
import anyio.from_thread

anyio.abc.BlockingPortal = anyio.from_thread.BlockingPortal


# --- Port allocation ---


def find_free_port(start: int = 18080) -> int:
    """Return a free TCP port (kernel-assigned ephemeral).

    Scanning a fixed range races under pytest-xdist: every worker scans
    the same range, and two can claim the same port in the window
    between probe-close and subprocess-bind. Binding port 0 lets the
    kernel assign distinct ephemeral ports. `start` is kept for
    call-site compatibility and ignored.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_port(port: int, timeout: float = 15.0) -> None:
    """Wait for a port to be listening."""
    start = time.time()
    while time.time() - start < timeout:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            try:
                s.connect(("127.0.0.1", port))
                return
            except (ConnectionRefusedError, OSError):
                time.sleep(0.1)
    raise TimeoutError(f"Port {port} not listening after {timeout}s")


# --- Server fixture ---


@pytest.fixture(scope="session")
def server_url():
    """Start the MCP server over HTTP and return the base URL.

    Uses a temporary database at /tmp/ml-sci-test-<timestamp>.db and
    unused high-number ports. Never touches the production database.
    """
    # Unique per worker: second-resolution timestamps collide when two
    # pytest-xdist workers start a server in the same second.
    timestamp = f"{int(time.time())}-{os.getpid()}"
    db_path = f"/tmp/ml-sci-test-{timestamp}.db"
    archive_dir = f"/tmp/ml-sci-test-{timestamp}-archives"
    config_path = f"/tmp/ml-sci-test-{timestamp}.toml"

    mcp_port = find_free_port(18080)
    obs_port = find_free_port(19080)

    # Write a config file that points the archive to a temp dir
    with open(config_path, "w") as f:
        f.write(f"""
db_path = "{db_path}"

[archive]
enabled = true
batch_size = 5
archive_dir = "{archive_dir}"
""")

    # Server output goes to a log file, not a pipe. An undrained PIPE
    # fills its 64KB OS buffer mid-suite, at which point the server
    # blocks on write() and every subsequent client call hangs — the
    # failure surface is a frozen test, not a log error.
    log_path = f"/tmp/ml-sci-test-{timestamp}.log"
    log_file = open(log_path, "w")
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "ml_episteme_mcp",
            "--transport", "http",
            "--port", str(mcp_port),
            "--stateless",
            "--observability-port", str(obs_port),
            "--config", config_path,
        ],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        cwd=str(Path(__file__).parent.parent),
    )

    try:
        wait_for_port(mcp_port, timeout=20.0)
        wait_for_port(obs_port, timeout=10.0)
    except TimeoutError:
        # Capture output for debugging
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log_file.close()
        print("SERVER LOG:", Path(log_path).read_text())
        raise

    base_url = f"http://127.0.0.1:{mcp_port}"

    yield base_url

    # Cleanup — terminate and reap the process, then close its log file
    # so no unclosed-file warnings leak at GC.
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    log_file.close()

    # Remove temp files
    Path(db_path).unlink(missing_ok=True)
    Path(config_path).unlink(missing_ok=True)
    Path(log_path).unlink(missing_ok=True)
    import shutil
    shutil.rmtree(archive_dir, ignore_errors=True)


# --- HTTP MCP client helper ---


async def call_tool_http(server_url: str, tool: str, args: dict) -> dict:
    """Call an MCP tool over HTTP and return the parsed result.

    Returns a dict. If the tool returned an error, the dict will have
    an "error" key. Otherwise it will have the tool's result fields.
    If the MCP framework rejects the call (e.g. missing required args),
    returns {"error": "<framework error message>"}.
    """
    from mcp.client.streamable_http import streamable_http_client
    from mcp.client.session import ClientSession

    async with streamable_http_client(f"{server_url}/mcp") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, args)
            text = result.content[0].text if result.content else ""
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                # is_error payloads are still JSON {"error": ...}; a
                # non-JSON text is a framework-level error.
                return {"error": text or "unknown error"}


async def list_tools_http(server_url: str) -> list:
    """List all available MCP tools over HTTP."""
    from mcp.client.streamable_http import streamable_http_client
    from mcp.client.session import ClientSession

    async with streamable_http_client(f"{server_url}/mcp") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            return result.tools


async def read_resource_http(server_url: str, uri: str) -> dict:
    """Read an MCP resource over HTTP and return the parsed content.

    Args:
        uri: e.g. "programme://prog-123"
    """
    from mcp.client.streamable_http import streamable_http_client
    from mcp.client.session import ClientSession

    async with streamable_http_client(f"{server_url}/mcp") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.read_resource(uri)
            return json.loads(result.contents[0].text)


# --- Helper to create a programme + hypothesis + trial ---


async def make_programme(server_url: str, **overrides) -> str:
    """Create a programme via HTTP and return its ID.

    The goal is unique per call — the shared session server enforces
    duplicate-goal rejection (commitment 8), so identical goals across
    tests would collide. Tests needing a specific goal pass goal=.
    """
    import uuid as _uuid
    args = {
        "goal": f"test goal {_uuid.uuid4().hex[:8]}",
        "constraints": {},
        "allowed_variables": ["lr"],
        "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
    }
    args.update(overrides)
    result = await call_tool_http(server_url, "create_programme", args)
    assert "error" not in result, f"create_programme failed: {result}"
    return result["programme_id"]


async def make_hypothesis(server_url: str, programme_id: str, **overrides) -> str:
    """Create a hypothesis via HTTP and return its ID."""
    args = {
        "programme_id": programme_id,
        "statement": "test hypothesis",
        "failure_criterion": "val < 0.5",
        "variables_involved": ["lr"],
    }
    args.update(overrides)
    result = await call_tool_http(server_url, "formulate_hypothesis", args)
    assert "error" not in result, f"formulate_hypothesis failed: {result}"
    return result["hypothesis_id"]


async def make_trial(server_url: str, programme_id: str, hypothesis_id: str, **overrides) -> str:
    """Design an experiment (create a trial) via HTTP and return its ID."""
    args = {
        "programme_id": programme_id,
        "hypothesis_id": hypothesis_id,
        "config": {"lr": 0.001},
    }
    args.update(overrides)
    result = await call_tool_http(server_url, "design_experiment", args)
    assert "error" not in result, f"design_experiment failed: {result}"
    return result["trial_id"]


async def make_completed_trial_with_observation(
    server_url: str,
    programme_id: str,
    hypothesis_id: str,
) -> tuple[str, str]:
    """Full loop: design → capture_bundle → run_trial → record_observation.

    Returns (trial_id, observation_id). The trial is completed with an
    observation. Required for conclude_hypothesis (commitment 1 + 7).
    """
    import os

    tid = await make_trial(server_url, programme_id, hypothesis_id)

    # Capture bundle with the stub code file
    stub_path = os.path.join(
        os.path.dirname(__file__), "fixtures", "train_stub.py"
    )
    result = await call_tool_http(server_url, "capture_bundle", {
        "trial_id": tid,
        "code_ref": stub_path,
        "env_ref": "python3.12",
        "seeds": [42],
        "splits": {"train": 0.8},
    })
    assert "error" not in result, f"capture_bundle failed: {result}"

    # Run the trial (should complete synchronously with the stub)
    result = await call_tool_http(server_url, "run_trial", {
        "programme_id": programme_id,
        "trial_id": tid,
    })
    assert "error" not in result, f"run_trial failed: {result}"

    # If the trial is still running, poll for completion
    for _ in range(30):
        status = await call_tool_http(server_url, "get_trial_status", {
            "programme_id": programme_id,
            "trial_id": tid,
        })
        if status.get("status") in ("completed", "failed"):
            break
        await asyncio.sleep(1)

    assert status.get("status") == "completed", f"Trial did not complete: {status}"

    # Record an observation
    result = await call_tool_http(server_url, "record_observation", {
        "trial_id": tid,
        "metrics": {"val_accuracy": 0.92},
        "variance": {"val_accuracy": 0.003},
        "spatiotemporal_region": "test",
    })
    assert "error" not in result, f"record_observation failed: {result}"
    obs_id = result["observation_id"]

    return tid, obs_id


async def make_closeable_programme(server_url: str) -> tuple[str, str]:
    """Create a programme that can be closed as 'completed'.

    Runs the full loop: programme → hypothesis → design → bundle →
    run → observe → believe → conclude. Returns (programme_id,
    hypothesis_id). Required because close_programme(completed)
    rejects zero-trial programmes and under_test hypotheses.
    """
    pid = await make_programme(server_url)
    hid = await make_hypothesis(server_url, pid)
    tid, obs_id = await make_completed_trial_with_observation(
        server_url, pid, hid
    )
    await call_tool_http(server_url, "update_belief", {
        "programme_id": pid, "trial_id": tid, "observation_id": obs_id,
    })
    result = await call_tool_http(server_url, "conclude_hypothesis", {
        "programme_id": pid, "hypothesis_id": hid,
        "verdict": "accepted", "evidence_summary": "test",
    })
    assert "error" not in result, f"conclude_hypothesis failed: {result}"
    return pid, hid
