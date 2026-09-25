"""Shared fixtures for the anamnesis MCP test battery.

Starts ml-anamnesis-mcp over HTTP on an unused high port (28080+) with a
temporary database. Reuses the root conftest's port/client helpers —
the anamnesis fixture itself is separate because the server under test
is a different process (ADR-0002).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mcp.server.mcpserver.exceptions import ToolError

import importlib.util as _ilu

# Load the ROOT tests/conftest.py explicitly — a plain `import conftest`
# resolves to this file (same name, nearer in sys.path) and circularly
# imports itself.
_root_conftest_path = Path(__file__).parent.parent / "conftest.py"
_spec = _ilu.spec_from_file_location("root_conftest", _root_conftest_path)
_root_conftest = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_root_conftest)

call_tool_http = _root_conftest.call_tool_http
find_free_port = _root_conftest.find_free_port
wait_for_port = _root_conftest.wait_for_port


@pytest.fixture(scope="session")
def anamnesis_server():
    """Start ml-anamnesis-mcp over HTTP plus the observability GUI.

    Yields (mcp_url, gui_url)."""
    # Unique per worker: second-resolution timestamps collide when two
    # pytest-xdist workers start a server in the same second.
    timestamp = f"{int(time.time())}-{os.getpid()}"
    db_path = f"/tmp/anamnesis-test-{timestamp}.db"
    log_path = f"/tmp/anamnesis-test-{timestamp}.log"
    port = find_free_port(28080)
    obs_port = find_free_port(29080)

    # Same reason as the ml-episteme fixture: log file, not an
    # undrained pipe (64KB buffer deadlock).
    log_file = open(log_path, "w")
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "ml_anamnesis_mcp",
            "--transport", "http",
            "--port", str(port),
            "--stateless",
            "--db-path", db_path,
            "--observability-port", str(obs_port),
        ],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        cwd=str(Path(__file__).parent.parent.parent),
    )

    try:
        wait_for_port(port, timeout=20.0)
        wait_for_port(obs_port, timeout=10.0)
    except TimeoutError:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log_file.close()
        print("ANAMNESIS LOG:", Path(log_path).read_text())
        raise

    yield f"http://127.0.0.1:{port}", f"http://127.0.0.1:{obs_port}"

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    log_file.close()
    Path(db_path).unlink(missing_ok=True)
    Path(log_path).unlink(missing_ok=True)


@pytest.fixture(scope="session")
def anamnesis_url(anamnesis_server):
    """The MCP endpoint URL."""
    return anamnesis_server[0]


@pytest.fixture(scope="session")
def anamnesis_gui_url(anamnesis_server):
    """The observability GUI URL."""
    return anamnesis_server[1]


@pytest.fixture
def mem_store(tmp_path):
    """A fresh in-memory-equivalent MemoryStore (temp file per test)."""
    from ml_anamnesis_mcp.state.store import MemoryStore

    store = MemoryStore(str(tmp_path / "memory.db"))
    store.connect()
    yield store
    store.close()


@pytest.fixture
def mcp_server(mem_store):
    """An in-process anamnesis MCPServer bound to the temp store."""
    from ml_anamnesis_mcp.server import create_server

    return create_server(mem_store)


async def call_tool(mcp, name: str, args: dict) -> dict:
    """Call a tool in-process and parse the JSON result."""
    try:
        result = await mcp.call_tool(name, args)
    except ToolError as exc:
        # Schema validation (bad Literal etc.) raises in-process.
        return {"error": str(exc)}
    text = result.content[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"error": text}


async def make_claim(mcp, content: str = "claim under test", **overrides) -> str:
    """Assert a claim with evidence; return its claim_id."""
    args = {
        "content": content,
        "type": "empirical",
        "confidence": 0.8,
        "evidence": [
            {"to_ref": "trial-seed", "ref_type": "trial", "relation": "tested_by"}
        ],
    }
    args.update(overrides)
    result = await call_tool(mcp, "assert_claim", args)
    assert "error" not in result, f"assert_claim failed: {result}"
    return result["claim_id"]
