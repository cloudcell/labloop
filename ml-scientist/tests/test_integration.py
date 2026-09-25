"""Phase 7: Integration tests -- real downstream MCP servers.

These tests spin up real MCP servers as subprocesses (HTTP transport) and
verify the ml-episteme server orchestrates them correctly through the MCP
protocol.

HTTP transport is used for downstream servers because the MCP stdio client
does not support multiple concurrent connections in the same event loop.
"""

import json
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from ml_episteme_mcp.clients.adaptor import MCPAdaptor
from ml_episteme_mcp.clients.mcp_adaptor import (
    AnamnesisClaimsAdaptor,
    MCPExecutorAdaptor,
    MCPOptimizerAdaptor,
)
from ml_episteme_mcp.server import create_server
from ml_episteme_mcp.state.store import StateStore


DOWNSTREAM_SCRIPT = str(Path(__file__).parent / "downstream_mcp_server.py")


def _free_port() -> int:
    """Get a free port number."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 10.0) -> None:
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


@pytest.fixture
def downstream_servers():
    """Start 3 downstream MCP servers (optimizer, executor, claims) on HTTP."""
    procs = []
    ports = {}

    for role in ["optimizer", "executor", "claims"]:
        port = _free_port()
        ports[role] = port
        proc = subprocess.Popen(
            [sys.executable, DOWNSTREAM_SCRIPT, "--role", role, "--transport", "http", "--port", str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        procs.append(proc)
        _wait_for_port(port)

    yield ports

    for proc in procs:
        proc.terminate()
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()


@pytest.fixture
def store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    s = StateStore(path)
    s.connect()
    yield s
    s.close()
    Path(path).unlink(missing_ok=True)


def optimizer_config(port: int):
    return {"transport": "streamable-http", "url": f"http://127.0.0.1:{port}/mcp"}


def executor_config(port: int):
    return {"transport": "streamable-http", "url": f"http://127.0.0.1:{port}/mcp"}


def claims_config(port: int):
    return {"transport": "streamable-http", "url": f"http://127.0.0.1:{port}/mcp"}


# --- Integration tests ---


class TestMCPClientAdaptorConnection:
    """The MCP client adaptor can connect to a real downstream server."""

    @pytest.mark.asyncio
    async def test_connect_and_list_tools(self, downstream_servers):
        adaptor = MCPOptimizerAdaptor(optimizer_config(downstream_servers["optimizer"]))
        await adaptor.connect()
        try:
            tools = await adaptor.list_tools()
            assert "create_study" in tools
            assert "ask" in tools
            assert "tell" in tools
            assert "best_trials" in tools
            assert "param_importance" in tools
        finally:
            await adaptor.disconnect()


class TestOptimizerRoleViaMCP:
    """The optimizer role through a real MCP connection."""

    @pytest.mark.asyncio
    async def test_create_study_and_ask(self, downstream_servers):
        adaptor = MCPOptimizerAdaptor(optimizer_config(downstream_servers["optimizer"]))
        await adaptor.connect()
        try:
            study_id = await adaptor.create_study("prog-test", ["learning_rate", "depth"])
            assert study_id == "study-prog-test"

            config = await adaptor.ask("prog-test")
            assert "learning_rate" in config
            assert "depth" in config

            await adaptor.tell("prog-test", "trial-1", {"val_accuracy": 0.92})

            best = await adaptor.best_trials("prog-test")
            assert len(best) == 1
            assert best[0]["result"]["val_accuracy"] == 0.92

            importance = await adaptor.param_importance("prog-test")
            assert "learning_rate" in importance
            assert "depth" in importance
        finally:
            await adaptor.disconnect()


class TestExecutorRoleViaMCP:
    """The executor role through a real MCP connection."""

    @pytest.mark.asyncio
    async def test_execute_code(self, downstream_servers):
        adaptor = MCPExecutorAdaptor(executor_config(downstream_servers["executor"]))
        await adaptor.connect()
        try:
            output = await adaptor.execute_code("print('hello')")
            data = json.loads(output)
            assert data["status"] == "completed"
            assert "integration test" in data["output"]
        finally:
            await adaptor.disconnect()


class TestClaimsRoleViaMCP:
    """The claims role through a real MCP connection."""

    @pytest.mark.asyncio
    async def test_assert_relate_and_read_claim(self, downstream_servers):
        adaptor = AnamnesisClaimsAdaptor(claims_config(downstream_servers["claims"]))
        await adaptor.connect()
        try:
            claim_id = await adaptor.assert_claim(
                "depth=12 beats depth=6",
                "empirical",
                0.85,
                evidence=[{"ref": "conclusion-1", "relation": "tested_by"}],
                source_id="prog-test",
            )
            assert claim_id.startswith("claim-")

            edge_id = await adaptor.relate(
                claim_id, "conclusion-1", "conclusion", "tested_by"
            )
            assert edge_id.startswith("edge-")

            claim = await adaptor.get_claim(claim_id)
            assert claim["content"] == "depth=12 beats depth=6"

            claims = await adaptor.list_claims(type="empirical")
            assert len(claims) == 1
            assert claims[0]["id"] == claim_id
        finally:
            await adaptor.disconnect()


class TestFullLoopWithRealMCPDownstream:
    """Test the full ask -> run -> tell cycle with real downstream MCP servers."""

    @pytest.mark.asyncio
    async def test_end_to_end_with_real_downstream(self, store, downstream_servers):
        """Full lifecycle with real MCP downstream servers for all roles."""
        optimizer = MCPOptimizerAdaptor(optimizer_config(downstream_servers["optimizer"]))
        executor = MCPExecutorAdaptor(executor_config(downstream_servers["executor"]))
        claims = AnamnesisClaimsAdaptor(claims_config(downstream_servers["claims"]))

        await optimizer.connect()
        await executor.connect()
        await claims.connect()

        try:
            adaptor = MCPAdaptor({})
            adaptor.set_optimizer(optimizer)
            adaptor.set_executor(executor)
            adaptor.set_claims(claims)

            mcp = create_server(store, adaptor)

            from mcp.client import Client
            client = Client(mcp)

            async with client:
                # 1. Create programme
                result = await client.call_tool("create_programme", {
                    "goal": "maximize val accuracy",
                    "constraints": {"gpu_memory_gb": 40},
                    "allowed_variables": ["learning_rate", "depth"],
                    "budget": {"max_trials": 50, "max_wall_time_hours": 200.0},
                })
                prog = json.loads(result.content[0].text)
                pid = prog["programme_id"]
                assert prog["status"] == "created"

                # 2. Formulate hypothesis
                result = await client.call_tool("formulate_hypothesis", {
                    "programme_id": pid,
                    "statement": "depth=12 beats depth=6",
                    "failure_criterion": "val_acc(depth=12) <= val_acc(depth=6)",
                    "variables_involved": ["depth"],
                })
                hyp = json.loads(result.content[0].text)
                hid = hyp["hypothesis_id"]

                # 3. Design experiment
                result = await client.call_tool("design_experiment", {
                    "programme_id": pid,
                    "hypothesis_id": hid,
                    "config": {"depth": 12, "learning_rate": 0.001},
                })
                trial = json.loads(result.content[0].text)
                tid = trial["trial_id"]

                # 4. Capture bundle
                await client.call_tool("capture_bundle", {
                    "trial_id": tid,
                    "code_ref": "tests/fixtures/train_stub.py",
                    "env_ref": "conda:env1",
                    "seeds": [42, 43],
                    "splits": {"train": 0.8, "val": 0.1, "test": 0.1},
                })

                # 5. Run trial (calls real executor via MCP)
                result = await client.call_tool("run_trial", {
                    "programme_id": pid,
                    "trial_id": tid,
                })
                run = json.loads(result.content[0].text)
                assert run["status"] == "completed"
                executor_output = json.loads(run["executor_output"])
                assert "integration test" in executor_output["output"]

                # 6. Record observation (persisted to state.db)
                result = await client.call_tool("record_observation", {
                    "trial_id": tid,
                    "metrics": {"val_accuracy": 0.94},
                    "variance": {"val_accuracy": 0.002},
                    "spatiotemporal_region": "gpu-0@2026-09-14",
                })
                obs = json.loads(result.content[0].text)
                assert obs["status"] == "recorded"

                # 7. Update belief (calls real optimizer tell + best_trials via MCP)
                result = await client.call_tool("update_belief", {
                    "programme_id": pid,
                    "trial_id": tid,
                    "observation_id": obs["observation_id"],
                })
                belief = json.loads(result.content[0].text)
                assert belief["status"] == "updated"

                # 8. Assess programme (calls real optimizer via MCP, reads state.db)
                result = await client.call_tool("assess_programme", {
                    "programme_id": pid,
                })
                assess = json.loads(result.content[0].text)
                assert "health" in assess

                # 9. Get next experiment (calls real optimizer ask via MCP)
                result = await client.call_tool("get_next_experiment", {
                    "programme_id": pid,
                })
                next_exp = json.loads(result.content[0].text)
                assert "next_config" in next_exp
                assert "learning_rate" in next_exp["next_config"]

                # 10. Close programme — mints a claim via the claims role
                result = await client.call_tool("conclude_hypothesis", {
                    "programme_id": pid,
                    "hypothesis_id": hid,
                    "verdict": "accepted",
                    "evidence_summary": "val_accuracy improved significantly",
                })
                close = json.loads(result.content[0].text)
                assert close["verdict"] == "accepted"
                assert close["claim_status"] == "minted"
                assert close["claim_id"].startswith("claim-")

                # Verify the minted claim is readable through the MCP surface
                minted = await claims.get_claim(close["claim_id"])
                assert "depth=12 beats depth=6" in minted["content"]
                assert "holds under" in minted["content"]
                assert minted["type"] == "empirical"
                assert minted["confidence"] == 0.85

                # Verify the tested_by evidence edge to the trial
                tested_by = [
                    e for e in minted["evidence"]
                    if e["relation"] == "tested_by"
                ]
                assert len(tested_by) == 1
                assert tested_by[0]["to_ref"] == tid

                # Verify state persisted
                belief_res = await client.read_resource(f"programme://{pid}/belief")
                belief_data = json.loads(belief_res.contents[0].text)
                assert "state" in belief_data
        finally:
            await optimizer.disconnect()
            await executor.disconnect()
            await claims.disconnect()
