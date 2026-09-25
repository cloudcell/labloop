"""Shared fixtures for the zetesis (Loop 1) test battery.

In-process tests use duck-typed fake adaptors — no MCP transport, no
subprocess. The HTTP battery launches real stub upstream servers
(tests/downstream_mcp_server.py --role evidence|claims) plus a real
zetesis subprocess, exercising the adaptor path end-to-end.
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


class FakePromotionAdaptor(FakeUpstreamAdaptor):
    """Duck-typed promotion channel — records pushes, returns canned
    upstream write results (register_candidate → cand-, decisions →
    decision-). No MCP transport; the whitelist gate is tested through
    the tool layer, not here."""

    def __init__(self):
        super().__init__()
        self.pushed: list[tuple[str, dict]] = []
        self._cand_n = 0
        self._dec_n = 0

    async def push(self, tool: str, args: dict):
        self.pushed.append((tool, args))
        if tool == "register_candidate":
            self._cand_n += 1
            return json.dumps({"candidate_id": f"cand-new{self._cand_n}"})
        if tool == "record_promotion_decision":
            self._dec_n += 1
            return json.dumps({"decision_id": f"decision-{self._dec_n}"})
        return json.dumps({"error": f"unexpected push: {tool}"})


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
def search_store(tmp_path):
    """A fresh SearchStore (temp file per test)."""
    from ml_zetesis_mcp.state.store import SearchStore

    store = SearchStore(str(tmp_path / "search.db"))
    store.connect()
    yield store
    store.close()


@pytest.fixture
def adaptors():
    """An Adaptors container with fakes wired."""
    from ml_zetesis_mcp.clients.adaptors import Adaptors

    a = Adaptors()
    a.evidence = FakeUpstreamAdaptor()
    a.promotion = FakePromotionAdaptor()
    a.claims = FakeClaimsAdaptor()
    return a


@pytest.fixture
def zetesis_server(search_store, adaptors):
    """An in-process zetesis MCPServer bound to temp store + fakes."""
    from ml_zetesis_mcp.server import create_server

    return create_server(search_store, adaptors=adaptors)


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


async def open_inv(mcp, **overrides) -> str:
    """Open an investigation; return its id."""
    args = {
        "question": "does X work?",
        "scope": {"programme_ids": ["prog-aaaa1111"]},
    }
    args.update(overrides)
    result = await call_tool(mcp, "open_investigation", args)
    assert "error" not in result, f"open_investigation failed: {result}"
    return result["investigation_id"]


# --- HTTP battery: real zetesis + stub upstreams ---


@pytest.fixture(scope="session")
def zetesis_http_server():
    """Launch stub upstreams + a real zetesis HTTP server.

    Yields (zetesis_url, gui_url, evidence_stub_url, claims_stub_url).
    """
    timestamp = f"{int(time.time())}-{os.getpid()}"
    db_path = f"/tmp/zetesis-test-{timestamp}.db"
    log_path = f"/tmp/zetesis-test-{timestamp}.log"
    config_path = f"/tmp/zetesis-test-{timestamp}.toml"

    evidence_port = find_free_port(29400)
    claims_port = find_free_port(29500)
    mcp_port = find_free_port(28070)
    obs_port = find_free_port(28071)

    procs = []
    logs = []
    try:
        for role, port in (("evidence", evidence_port),
                           ("claims", claims_port)):
            lp = f"/tmp/zetesis-stub-{role}-{timestamp}.log"
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

[adaptors.evidence]
transport = "streamable-http"
url = "http://127.0.0.1:{evidence_port}/mcp"

[adaptors.claims]
transport = "streamable-http"
url = "http://127.0.0.1:{claims_port}/mcp"
''')

        log_file = open(log_path, "w")
        proc = subprocess.Popen(
            [
                sys.executable, "-m", "ml_zetesis_mcp",
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
        f"http://127.0.0.1:{evidence_port}",
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
def zetesis_promotion_server():
    """Real upstream battery — a REAL ml_episteme_mcp (Loop 0) plus a
    stub claims server plus a real zetesis, so the promotion channel
    exercises actual Loop-0 enforcement (lineage, contract existence,
    decision validation), not a stub's canned replies.

    Yields (zetesis_url, gui_url, loop0_url, claims_stub_url).
    """
    timestamp = f"{int(time.time())}-{os.getpid()}"
    zet_db = f"/tmp/zetesis-prom-{timestamp}.db"
    loop0_db = f"/tmp/loop0-prom-{timestamp}.db"
    zet_log = f"/tmp/zetesis-prom-{timestamp}.log"
    loop0_log = f"/tmp/loop0-prom-{timestamp}.log"
    loop0_cfg = f"/tmp/loop0-prom-{timestamp}.toml"
    zet_cfg = f"/tmp/zetesis-prom-{timestamp}.toml"

    loop0_port = find_free_port(28300)
    claims_port = find_free_port(28400)
    mcp_port = find_free_port(28500)
    obs_port = find_free_port(28510)

    procs = []
    logs = []
    try:
        Path(loop0_cfg).write_text(f'db_path = "{loop0_db}"\n')
        lf = open(loop0_log, "w")
        logs.append((lf, loop0_log))
        procs.append(subprocess.Popen(
            [
                sys.executable, "-m", "ml_episteme_mcp",
                "--transport", "http",
                "--port", str(loop0_port),
                "--stateless",
                "--db-path", loop0_db,
                "--config", loop0_cfg,
            ],
            stdout=lf, stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            cwd=str(REPO_ROOT),
        ))
        wait_for_port(loop0_port, timeout=20.0)

        lp = f"/tmp/zetesis-prom-claims-{timestamp}.log"
        lf = open(lp, "w")
        logs.append((lf, lp))
        procs.append(subprocess.Popen(
            [
                sys.executable,
                str(REPO_ROOT / "tests" / "downstream_mcp_server.py"),
                "--role", "claims",
                "--transport", "http",
                "--port", str(claims_port),
            ],
            stdout=lf, stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            cwd=str(REPO_ROOT),
        ))
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

        lf = open(zet_log, "w")
        logs.append((lf, zet_log))
        procs.append(subprocess.Popen(
            [
                sys.executable, "-m", "ml_zetesis_mcp",
                "--transport", "http",
                "--port", str(mcp_port),
                "--stateless",
                "--db-path", zet_db,
                "--config", zet_cfg,
                "--observability-port", str(obs_port),
            ],
            stdout=lf, stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            cwd=str(REPO_ROOT),
        ))
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
    for p in (zet_db, loop0_db, zet_log, loop0_log, loop0_cfg, zet_cfg):
        Path(p).unlink(missing_ok=True)
