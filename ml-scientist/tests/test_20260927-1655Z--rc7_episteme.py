"""rc-7 regressions — episteme.

Q1  list_trials carries the persisted retry_reason.
Q4  record_promotion_decision existence-checks Loop-0-prefixed
    evidence refs; cross-loop opaque prefixes pass through.
Q6  read_resource reaches protocol:// resources; unknown URIs error.
Q9  generator failure surfaces stdout's structured error, stderr,
    and the partial artifact path — not just the exit code.
Q11 artifact typing uses role suffixes, not filename substrings:
    huge_stderr.py is a wrapper, *_stderr.log is stderr.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from ml_episteme_mcp.server import create_server
from ml_episteme_mcp.state.models import (
    Hypothesis,
    Programme,
    ProgrammeStatus,
    Trial,
    TrialStatus,
)
from ml_episteme_mcp.state.store import StateStore


@pytest.fixture
def store(tmp_path):
    s = StateStore(str(tmp_path / "state.db"))
    s.connect()
    yield s
    s.close()


@pytest.fixture
def mcp(store):
    return create_server(
        store, enforcement_config={"recurrent_protocol": False}
    )


@pytest.fixture
def client(mcp):
    from mcp.client import Client
    return Client(mcp)


async def call_tool(client, name: str, args: dict) -> dict:
    result = await client.call_tool(name, args)
    text = result.content[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        if result.is_error:
            return {"error": text}
        raise


def _seed_trial(store, pid="prog-q", hid="hyp-q", tid="trial-q"):
    """Programme → hypothesis → trial, returning the trial id."""
    store.create_programme(Programme(
        id=pid, goal="g", constraints={},
        allowed_variables=["x"], budget_max_trials=10,
        budget_max_wall_time_hours=1.0,
        status=ProgrammeStatus.active,
    ))
    store.create_hypothesis(Hypothesis(
        id=hid, programme_id=pid,
        statement="s", failure_criterion="f",
        variables_involved=["x"],
    ))
    store.create_trial(Trial(
        id=tid, programme_id=pid, hypothesis_id=hid,
        config_json="{}", status=TrialStatus.designed,
    ))
    return tid


class TestListTrialsRetryReason:
    """rc-7 Q1 — the queue surface must carry the persisted reason."""

    async def test_list_trials_includes_retry_reason(
        self, client, store
    ):
        tid = _seed_trial(store)
        store.update_trial_status(tid, "running")
        store.update_trial_status(
            tid, "retryable", reason="worker died mid-run"
        )
        async with client:
            r = await call_tool(client, "list_trials", {
                "programme_id": "prog-q",
            })
            t = next(t for t in r["trials"] if t["id"] == tid)
            assert t["retry_reason"] == "worker died mid-run"
            assert t["status"] == "retryable"
            # get_trial_status agrees — same row, both surfaces.
            g = await call_tool(client, "get_trial_status", {
                "programme_id": "prog-q", "trial_id": tid,
            })
            assert g["retry_reason"] == t["retry_reason"]

    async def test_list_trials_retry_reason_none_when_unset(
        self, client, store
    ):
        tid = _seed_trial(store)
        async with client:
            r = await call_tool(client, "list_trials", {
                "programme_id": "prog-q",
            })
            t = next(t for t in r["trials"] if t["id"] == tid)
            assert "retry_reason" in t
            assert t["retry_reason"] is None


class TestEvidenceRefExistence:
    """rc-7 Q4 — Loop-0-prefixed refs must resolve; other loops'
    prefixes are opaque by convention."""

    async def _register(self, client):
        r = await call_tool(client, "register_candidate", {
            "code_artifact_digest": "none",
            "model_ref": "m",
            "capability_profile": {},
        })
        assert "error" not in r, r
        return r["candidate_id"]

    async def test_real_loop0_ref_accepted(self, client):
        async with client:
            cand = await self._register(client)
            r = await call_tool(client, "record_promotion_decision", {
                "candidate_id": cand,
                "verdict": "hold",
                "evidence_refs": [cand],
                "rationale": "awaiting replication",
                "decided_by": "human:tester",
            })
        assert "error" not in r, r
        assert r["decision_id"].startswith("decision-")

    async def test_nonexistent_loop0_ref_refused(self, client):
        async with client:
            cand = await self._register(client)
            r = await call_tool(client, "record_promotion_decision", {
                "candidate_id": cand,
                "verdict": "promote",
                "evidence_refs": ["trial-deadbeef99"],
                "rationale": "won",
                "decided_by": "human:tester",
            })
        assert "error" in r
        assert "trial-deadbeef99" in r["error"]
        assert "nonexistent" in r["error"]

    async def test_mixed_refs_name_only_the_missing(self, client):
        async with client:
            cand = await self._register(client)
            r = await call_tool(client, "record_promotion_decision", {
                "candidate_id": cand,
                "verdict": "promote",
                "evidence_refs": [cand, "obs-ghost"],
                "rationale": "won",
                "decided_by": "human:tester",
            })
        assert "error" in r
        assert "obs-ghost" in r["error"]
        assert cand not in r["error"]

    async def test_cross_loop_refs_pass_through(self, client):
        """eref-/mdec-/camp- prefixes belong to other loops — Loop-0
        cannot existence-check them, so they pass (opaque refs)."""
        async with client:
            cand = await self._register(client)
            r = await call_tool(client, "record_promotion_decision", {
                "candidate_id": cand,
                "verdict": "hold",
                "evidence_refs": ["eref-abc123", "mdec-def456"],
                "rationale": "downstream attestation",
                "decided_by": "human:tester",
            })
        assert "error" not in r, r

    async def test_empty_evidence_refs_refused(self, client):
        async with client:
            cand = await self._register(client)
            r = await call_tool(client, "record_promotion_decision", {
                "candidate_id": cand,
                "verdict": "promote",
                "evidence_refs": [],
                "rationale": "vibes",
                "decided_by": "human:tester",
            })
        assert "error" in r and "evidence" in r["error"]


class TestReadResource:
    """rc-7 Q6 — the tool surface can read this server's resources."""

    async def test_reads_protocol_status(self, client):
        async with client:
            r = await call_tool(client, "read_resource", {
                "uri": "protocol://status",
            })
        assert "error" not in r, r
        body = json.loads(r["contents"][0]["content"])
        assert body["server"] == "ml-episteme-mcp"

    async def test_reads_protocol_session(self, client):
        async with client:
            r = await call_tool(client, "read_resource", {
                "uri": "protocol://session",
            })
        assert "error" not in r, r
        body = json.loads(r["contents"][0]["content"])
        assert "tool_catalog" in body or "status" in body

    async def test_unknown_uri_errors(self, client):
        async with client:
            r = await call_tool(client, "read_resource", {
                "uri": "protocol://nonexistent",
            })
        assert "error" in r

    async def test_foreign_scheme_errors(self, client):
        async with client:
            r = await call_tool(client, "read_resource", {
                "uri": "improver://classes",
            })
        assert "error" in r


class _GenExecutor:
    """Executor-role fake returning a canned wire result."""

    def __init__(self, result: dict):
        self.result = result

    async def execute_code(self, code, **kwargs):
        return json.dumps(self.result)


class TestGeneratorFailureSurface:
    """rc-7 Q9 — a generator failure must say why, not just the
    exit code. The wrapper's structured stdout error, real stderr,
    and any partial artifact path all surface."""

    async def _fail(self, tmp_path, result, stub_partial=False):
        from ml_episteme_mcp.clients.local_data_handler import (
            LocalDataHandler,
        )

        handler = LocalDataHandler(
            tmp_path / "data", executor=_GenExecutor(result)
        )
        storage = tmp_path / "data" / "data-ref-t1"
        storage.mkdir(parents=True, exist_ok=True)
        if stub_partial:
            (storage / "dataset.csv").write_text("a,b\n")
        gen = tmp_path / "gen.py"
        gen.write_text("def generate_data(c, o): pass\n")
        try:
            await handler._prepare_generated(
                "data-ref-t1", storage, "train",
                str(gen), 7, None,
            )
        except RuntimeError as e:
            return str(e)
        raise AssertionError("expected RuntimeError")

    async def test_structured_stdout_error_surfaced(self, tmp_path):
        """The wrapper prints {"error": ...} to stdout on a missing
        contract — that payload, not the bare exit code."""
        msg = await self._fail(tmp_path, {
            "status": "failed",
            "error": "Process exited with code 1",
            "stdout": json.dumps({
                "error": "generator does not expose "
                         "generate_data(config, output_path)"
            }),
            "stderr": "",
        })
        assert "generate_data(config, output_path)" in msg
        assert "Process exited with code 1" in msg

    async def test_stderr_surfaced(self, tmp_path):
        msg = await self._fail(tmp_path, {
            "status": "failed",
            "error": "Process exited with code 1",
            "stdout": "",
            "stderr": "Traceback...\nValueError: bad seed",
        })
        assert "ValueError: bad seed" in msg
        assert "stderr:" in msg

    async def test_nonjson_stdout_surfaced(self, tmp_path):
        msg = await self._fail(tmp_path, {
            "status": "failed",
            "error": "Process exited with code 1",
            "stdout": "some unstructured generator noise",
            "stderr": "",
        })
        assert "unstructured generator noise" in msg

    async def test_partial_artifact_path_surfaced(self, tmp_path):
        msg = await self._fail(
            tmp_path,
            {"status": "failed", "error": "Process exited with code 1",
             "stdout": "", "stderr": ""},
            stub_partial=True,
        )
        assert "partial output" in msg
        assert "dataset.csv" in msg

    async def test_no_partial_artifact_no_path(self, tmp_path):
        msg = await self._fail(tmp_path, {
            "status": "failed", "error": "Process exited with code 1",
            "stdout": "", "stderr": "",
        })
        assert "partial output" not in msg

    async def test_long_stderr_elided(self, tmp_path):
        big = "Traceback (most recent call last):\n" + "x\n" * 5000
        msg = await self._fail(tmp_path, {
            "status": "failed", "error": "Process exited with code 1",
            "stdout": "", "stderr": big,
        })
        assert "bytes elided" in msg
        assert len(msg) < len(big) + 200


class TestArtifactTyping:
    """rc-7 Q11 — role suffixes classify; incidental substrings
    in a code filename do not."""

    def test_stderr_in_py_filename_is_not_stderr(self, store, tmp_path):
        tid = _seed_trial(store)
        art = tmp_path / "arts"
        art.mkdir()
        (art / "0000_huge_stderr.py").write_text("print(1)\n")
        (art / "trial-q_x_stderr.log").write_text("err\n")
        (art / "trial-q_x_stdout.json").write_text("{}\n")
        (art / "trial-q_x_manifest.json").write_text("{}\n")
        (art / "trial-q_x_wrapper.py").write_text("pass\n")
        res = store.capture_artifacts_from_dir(
            tid, str(art), cleanup_after=False
        )
        types = {
            c["filename"]: c["artifact_type"]
            for c in res["captured"]
        }
        assert types["0000_huge_stderr.py"] == "wrapper"
        assert types["trial-q_x_stderr.log"] == "stderr"
        assert types["trial-q_x_stdout.json"] == "stdout"
        assert types["trial-q_x_manifest.json"] == "manifest"
        assert types["trial-q_x_wrapper.py"] == "wrapper"
