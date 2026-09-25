"""Shared fixtures for the arete (Loop 2) test battery.

In-process tests use duck-typed fake adaptors — no MCP transport, no
subprocess. The HTTP battery launches real stub upstream servers
(tests/downstream_mcp_server.py --role evidence|claims) plus a real
arete subprocess, exercising the adaptor path end-to-end.
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

REPO_ROOT = Path(__file__).parent.parent.parent


class FakeUpstreamAdaptor:
    """Duck-typed upstream for in-process tests — no MCP transport."""

    def __init__(self, payloads: dict | None = None):
        self.payloads = payloads or {}
        self.calls: list[tuple[str, dict]] = []

    async def pull(self, tool: str, args: dict):
        self.calls.append((tool, args))
        return self.payloads.get(
            tool, json.dumps({"ok": True, "tool": tool})
        )


class FakeClaimsAdaptor(FakeUpstreamAdaptor):
    """Fake anamnesis — records minted claims and edges in memory."""

    def __init__(self, payloads: dict | None = None):
        super().__init__(payloads)
        self.minted: list[dict] = []
        self.edges: list[dict] = []

    async def assert_claim(
        self, content, type, confidence, evidence=None, source_id=None
    ):
        cid = f"claim-{len(self.minted) + 1:04d}"
        self.minted.append({
            "claim_id": cid, "content": content, "type": type,
            "confidence": confidence, "evidence": evidence,
            "source_id": source_id,
        })
        # Inline evidence edges mint the same relations relate() would.
        for e in evidence or []:
            self.edges.append({
                "edge_id": f"edge-{len(self.edges) + 1}",
                "from_claim": cid, "to_ref": e["to_ref"],
                "ref_type": e["ref_type"], "relation": e["relation"],
            })
        return {"claim_id": cid, "status": "created"}

    async def relate(
        self, from_claim, to_ref, ref_type, relation, source_id=None
    ):
        eid = f"edge-{len(self.edges) + 1}"
        self.edges.append({
            "edge_id": eid, "from_claim": from_claim, "to_ref": to_ref,
            "ref_type": ref_type, "relation": relation,
        })
        return {"edge_id": eid, "status": "created"}


@pytest.fixture
def improver_store(tmp_path):
    """A fresh ImproverStore (temp file per test)."""
    from ml_arete_mcp.state.store import ImproverStore

    store = ImproverStore(str(tmp_path / "improver.db"))
    store.connect()
    yield store
    store.close()


@pytest.fixture
def adaptors():
    """An Adaptors container with fakes wired."""
    from ml_arete_mcp.clients.adaptors import Adaptors

    a = Adaptors()
    # describe_blob resolves any well-formed digest — in-process tests
    # exercise the real loop0 resolution path end-to-end.
    a.loop0 = FakeUpstreamAdaptor(payloads={
        "describe_blob": json.dumps({
            "exists": True, "resolved_in": ["artifact_files"],
        }),
    })
    a.loop1 = FakeUpstreamAdaptor()
    a.claims = FakeClaimsAdaptor()
    return a


@pytest.fixture
def arete_server(improver_store, adaptors):
    """An in-process arete MCPServer bound to temp store + fakes."""
    from ml_arete_mcp.server import create_server

    return create_server(improver_store, adaptors=adaptors)


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


async def register_imp(mcp, **overrides) -> str:
    """Register an improver; return its id."""
    args = {
        "code_artifact_digest": "sha256:" + "ab" * 32,
        "model_ref": "gpt-nano-v1",
        "capability_profile": {"can_propose": True},
    }
    args.update(overrides)
    result = await call_tool(mcp, "register_improver", args)
    assert "error" not in result, f"register_improver failed: {result}"
    return result["improver_id"]


async def make_contract(mcp, **overrides) -> str:
    """Create a meta-contract; return its id."""
    args = {
        "metrics": {"primary_metric": "hits", "direction": "max"},
        "promotion_policy": {"min_gain": 1.05, "confidence": 0.95},
    }
    args.update(overrides)
    result = await call_tool(mcp, "create_meta_contract", args)
    assert "error" not in result, f"create_meta_contract failed: {result}"
    return result["contract_id"]


async def propose(mcp, proposer_id: str, class_map=None) -> dict:
    """Submit a proposal; return the full result payload."""
    args = {
        "proposer_improver_id": proposer_id,
        "spec_delta": {"change": "replace planner", "to": "beam"},
        "class_map": class_map or {"planner": "modifiable"},
        "expected_benefit": "better descendants per budget",
        "falsification": "recursive_gain <= 1.0",
        "rollback_plan": "restore prior champion policy",
    }
    return await call_tool(mcp, "propose_meta_change", args)


# --- HTTP battery: real arete + stub upstreams ---


@pytest.fixture(scope="session")
def arete_http_server():
    """Launch stub upstreams + a real arete HTTP server.

    Yields (arete_url, gui_url, loop0_stub_url, loop1_stub_url,
    claims_stub_url).
    """
    timestamp = f"{int(time.time())}-{os.getpid()}"
    db_path = f"/tmp/arete-test-{timestamp}.db"
    log_path = f"/tmp/arete-test-{timestamp}.log"
    config_path = f"/tmp/arete-test-{timestamp}.toml"

    loop0_port = find_free_port(29600)
    loop1_port = find_free_port(29650)
    claims_port = find_free_port(29700)
    mcp_port = find_free_port(28060)
    obs_port = find_free_port(28061)

    procs = []
    logs = []
    try:
        for role, port in (("evidence", loop0_port),
                           ("evidence", loop1_port),
                           ("claims", claims_port)):
            lp = f"/tmp/arete-stub-{role}-{port}-{timestamp}.log"
            lf = open(lp, "w")
            logs.append((lf, lp))
            p = subprocess.Popen(
                [
                    sys.executable,
                    str(REPO_ROOT / "tests" / "downstream_mcp_server.py"),
                    "--role", role,
                    "--transport", "http",
                    "--port", str(port),
                ],
                stdout=lf, stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
                cwd=str(REPO_ROOT),
            )
            procs.append(p)
            wait_for_port(port, timeout=20.0)

        Path(config_path).write_text(f'''
db_path = "{db_path}"

[adaptors.loop0]
transport = "streamable-http"
url = "http://127.0.0.1:{loop0_port}/mcp"

[adaptors.loop1]
transport = "streamable-http"
url = "http://127.0.0.1:{loop1_port}/mcp"

[adaptors.claims]
transport = "streamable-http"
url = "http://127.0.0.1:{claims_port}/mcp"
''')

        log_file = open(log_path, "w")
        proc = subprocess.Popen(
            [
                sys.executable, "-m", "ml_arete_mcp",
                "--transport", "http",
                "--port", str(mcp_port),
                "--stateless",
                "--db-path", db_path,
                "--config", config_path,
                "--observability-port", str(obs_port),
            ],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            cwd=str(REPO_ROOT),
        )
        procs.append(proc)
        logs.append((log_file, log_path))

        wait_for_port(mcp_port, timeout=20.0)
        wait_for_port(obs_port, timeout=10.0)
    except TimeoutError:
        for p in procs:
            p.terminate()
        for lf, lp in logs:
            lf.close()
            print(f"LOG {lp}:", Path(lp).read_text())
        raise

    yield (
        f"http://127.0.0.1:{mcp_port}",
        f"http://127.0.0.1:{obs_port}",
        f"http://127.0.0.1:{loop0_port}",
        f"http://127.0.0.1:{loop1_port}",
        f"http://127.0.0.1:{claims_port}",
    )

    for p in procs:
        p.terminate()
    for p in procs:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
    for lf, lp in logs:
        lf.close()
    for p in (db_path, config_path, log_path):
        Path(p).unlink(missing_ok=True)


@pytest.fixture(scope="session")
def arete_orchestration_server():
    """Real-upstream orchestration battery — a REAL ml_episteme_mcp,
    a REAL ml_zetesis_mcp (evidence+promotion → loop0), a stub claims
    server, and a REAL ml_arete_mcp with all four channels wired
    (loop0, loop1, loop1_orchestration → zetesis, claims).

    The orchestration channel is the second write path in the
    ecosystem — this fixture exercises it against real enforcement
    on both hops, not a stub's canned replies.

    Yields (arete_url, arete_gui, zetesis_url, zet_gui,
    loop0_url, claims_url).
    """
    timestamp = f"{int(time.time())}-{os.getpid()}"
    loop0_db = f"/tmp/orch-loop0-{timestamp}.db"
    zet_db = f"/tmp/orch-zet-{timestamp}.db"
    arete_db = f"/tmp/orch-arete-{timestamp}.db"
    loop0_cfg = f"/tmp/orch-loop0-{timestamp}.toml"
    zet_cfg = f"/tmp/orch-zet-{timestamp}.toml"
    arete_cfg = f"/tmp/orch-arete-{timestamp}.toml"

    loop0_port = find_free_port(28700)
    claims_port = find_free_port(28720)
    zet_port = find_free_port(28740)
    zet_obs = find_free_port(28750)
    arete_port = find_free_port(28760)
    arete_obs = find_free_port(28770)

    procs = []
    logs = []
    paths = [loop0_db, zet_db, arete_db, loop0_cfg, zet_cfg, arete_cfg]

    def _spawn(cmd, log_path):
        lf = open(log_path, "w")
        logs.append((lf, log_path))
        paths.append(log_path)
        procs.append(subprocess.Popen(
            cmd,
            stdout=lf, stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            cwd=str(REPO_ROOT),
        ))

    try:
        Path(loop0_cfg).write_text(f'db_path = "{loop0_db}"\n')
        _spawn(
            [
                sys.executable, "-m", "ml_episteme_mcp",
                "--transport", "http",
                "--port", str(loop0_port),
                "--stateless",
                "--db-path", loop0_db,
                "--config", loop0_cfg,
            ],
            f"/tmp/orch-loop0-{timestamp}.log",
        )
        wait_for_port(loop0_port, timeout=20.0)

        _spawn(
            [
                sys.executable,
                str(REPO_ROOT / "tests" / "downstream_mcp_server.py"),
                "--role", "claims",
                "--transport", "http",
                "--port", str(claims_port),
            ],
            f"/tmp/orch-claims-{timestamp}.log",
        )
        wait_for_port(claims_port, timeout=20.0)

        Path(zet_cfg).write_text(f'''
db_path = "{zet_db}"

[adaptors.evidence]
transport = "streamable-http"
url = "http://127.0.0.1:{loop0_port}/mcp"

[adaptors.promotion]
transport = "streamable-http"
url = "http://127.0.0.1:{loop0_port}/mcp"

[adaptors.claims]
transport = "streamable-http"
url = "http://127.0.0.1:{claims_port}/mcp"
''')
        _spawn(
            [
                sys.executable, "-m", "ml_zetesis_mcp",
                "--transport", "http",
                "--port", str(zet_port),
                "--stateless",
                "--db-path", zet_db,
                "--config", zet_cfg,
                "--observability-port", str(zet_obs),
            ],
            f"/tmp/orch-zet-{timestamp}.log",
        )
        wait_for_port(zet_port, timeout=20.0)
        wait_for_port(zet_obs, timeout=10.0)

        Path(arete_cfg).write_text(f'''
db_path = "{arete_db}"

[adaptors.loop0]
transport = "streamable-http"
url = "http://127.0.0.1:{loop0_port}/mcp"

[adaptors.loop1]
transport = "streamable-http"
url = "http://127.0.0.1:{zet_port}/mcp"

[adaptors.loop1_orchestration]
transport = "streamable-http"
url = "http://127.0.0.1:{zet_port}/mcp"

[adaptors.claims]
transport = "streamable-http"
url = "http://127.0.0.1:{claims_port}/mcp"
''')
        _spawn(
            [
                sys.executable, "-m", "ml_arete_mcp",
                "--transport", "http",
                "--port", str(arete_port),
                "--stateless",
                "--db-path", arete_db,
                "--config", arete_cfg,
                "--observability-port", str(arete_obs),
            ],
            f"/tmp/orch-arete-{timestamp}.log",
        )
        wait_for_port(arete_port, timeout=20.0)
        wait_for_port(arete_obs, timeout=10.0)
        # Adaptor connects happen inside the server loop — give the
        # channels a beat to come up before the battery calls them.
        time.sleep(2.0)
    except TimeoutError:
        for p in procs:
            p.terminate()
        for lf, lp in logs:
            lf.close()
            print(f"LOG {lp}:", Path(lp).read_text())
        raise

    yield (
        f"http://127.0.0.1:{arete_port}",
        f"http://127.0.0.1:{arete_obs}",
        f"http://127.0.0.1:{zet_port}",
        f"http://127.0.0.1:{zet_obs}",
        f"http://127.0.0.1:{loop0_port}",
        f"http://127.0.0.1:{claims_port}",
    )

    for p in procs:
        p.terminate()
    for p in procs:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
    for lf, lp in logs:
        lf.close()
    for p in paths:
        Path(p).unlink(missing_ok=True)
