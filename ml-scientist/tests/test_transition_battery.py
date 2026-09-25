"""Transition battery — every valid and invalid transition over real MCP HTTP.

Spins up the real server pair as subprocesses — ml-episteme-mcp AND
ml-anamnesis-mcp (not stubs) — and drives the full transition matrix
through a real streamable-HTTP MCP client: valid transitions land, invalid
transitions are rejected AND state does not move. This is the live-system
complement to the in-process enforcement tests.

Layout:
  - TestProgrammeTransitions   programme lifecycle + programme-level gates
  - TestHypothesisTransitions  hypothesis lifecycle + conclusion gates
  - TestTrialTransitions       trial lifecycle + bundle/execution gates
  - TestBeliefAndObservation   observation/belief ordering gates
  - TestLineage                RSI Phase-0 candidate/contract/decision gates
  - TestArchive                close → archive → verify
  - TestAnamnesisDirect        invalid ops on the claims server itself
  - TestClaimsUnavailable      claims absent / claims dies mid-session
"""

import json
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client


FIXTURES = Path(__file__).parent / "fixtures"
TRAIN_STUB = str(FIXTURES / "train_stub.py")
EXE_PROBE = str(FIXTURES / "exe_probe_stub.py")
FAIL_STUB = str(FIXTURES / "fail_stub.py")
SLEEP_STUB = str(FIXTURES / "sleep_stub.py")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 15.0) -> None:
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"Port {port} not listening after {timeout}s")


def _start_pair(tmp: Path, claims_up: bool = True):
    """Start a real anamnesis + ml-episteme pair; returns (procs, urls)."""
    mem_port = _free_port()
    sci_port = _free_port()
    procs = []

    if claims_up:
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "ml_anamnesis_mcp",
             "--transport", "http", "--port", str(mem_port),
             "--db-path", str(tmp / "memory.db"), "--stateless"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ))
        _wait_for_port(mem_port)

    cfg = tmp / "cfg.toml"
    cfg.write_text(
        f'[archive]\narchive_dir = "{tmp}/archives"\n\n'
        '[adaptors.claims]\ntransport = "streamable-http"\n'
        f'url = "http://localhost:{mem_port}/mcp"\n'
    )
    procs.append(subprocess.Popen(
        [sys.executable, "-m", "ml_episteme_mcp",
         "--transport", "http", "--port", str(sci_port),
         "--db-path", str(tmp / "state.db"), "--stateless",
         "--config", str(cfg)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ))
    _wait_for_port(sci_port)
    # claims connect happens during startup; give it a beat to settle
    time.sleep(0.5)
    return procs, {
        "sci": f"http://127.0.0.1:{sci_port}/mcp",
        "mem": f"http://127.0.0.1:{mem_port}/mcp",
        "mem_port": mem_port,
    }


def _stop(procs):
    for p in procs:
        p.terminate()
    for p in procs:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()


@pytest.fixture(scope="module")
def pair(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("battery")
    procs, urls = _start_pair(tmp)
    yield urls
    _stop(procs)


# ---------- client helpers ----------


async def call(s, name, args):
    """Tool call; returns (parsed payload, is_error flag)."""
    r = await s.call_tool(name, args)
    text = r.content[0].text
    try:
        return json.loads(text), r.is_error
    except json.JSONDecodeError:
        # Schema validation errors (bad Literal etc.) are plain text.
        return {"error": text}, r.is_error


async def ok(s, name, args):
    """Tool call that must succeed."""
    r, is_err = await call(s, name, args)
    assert not is_err and "error" not in r, f"{name} failed: {r}"
    return r


async def err(s, name, args):
    """Tool call that must be rejected; returns the error payload."""
    r, is_err = await call(s, name, args)
    assert is_err or "error" in r, \
        f"{name} should have failed, got: {r}"
    return r


async def resource(s, uri):
    r = await s.read_resource(uri)
    return json.loads(r.contents[0].text)


# ---------- scenario builders ----------


async def mk_programme(s, max_trials=6, candidate_id=None):
    args = {
        "goal": f"battery-{uuid.uuid4().hex[:8]}: does C affect accuracy?",
        "constraints": {"dataset": "synthetic"},
        "allowed_variables": ["C"],
        "budget": {"max_trials": max_trials, "max_wallclock_s_per_trial": 120},
        "metric_direction": "maximize",
    }
    if candidate_id:
        args["candidate_version_id"] = candidate_id
    return (await ok(s, "create_programme", args))["programme_id"]


async def mk_hypothesis(s, pid, stmt="C=1.0 yields accuracy > 0.9."):
    return (await ok(s, "formulate_hypothesis", {
        "programme_id": pid, "statement": stmt,
        "variables_involved": ["C"],
        "failure_criterion": "Reject if accuracy <= 0.9 on any seed.",
    }))["hypothesis_id"]


async def design_trial(s, pid, hid, config=None):
    return (await ok(s, "design_experiment", {
        "programme_id": pid, "hypothesis_id": hid,
        "config": config or {"C": 1.0},
    }))["trial_id"]


async def bundle_trial(s, tid, code=TRAIN_STUB, env_ref="uv@0.10"):
    return await ok(s, "capture_bundle", {
        "trial_id": tid, "code_ref": code, "env_ref": env_ref,
        "seeds": [7],
        "splits": {"train": "/tmp/train.csv", "val": "/tmp/val.csv"},
    })


async def completed_trial(s, pid, hid, code=TRAIN_STUB, config=None):
    """design → bundle → run; returns (trial_id, run_result)."""
    tid = await design_trial(s, pid, hid, config)
    await bundle_trial(s, tid, code)
    r, _ = await call(s, "run_trial",
                      {"programme_id": pid, "trial_id": tid})
    return tid, r


async def concludable(s, pid, config=None):
    """hypothesis → completed trial → observation → belief.
    Returns (hypothesis_id, trial_id, observation_id)."""
    hid = await mk_hypothesis(s, pid)
    tid, _ = await completed_trial(s, pid, hid, config=config)
    obs = await ok(s, "record_observation", {
        "trial_id": tid, "metrics": {"accuracy": 0.95},
        "variance": {"accuracy": 0.001},
        "spatiotemporal_region": "battery",
    })
    await ok(s, "update_belief", {
        "programme_id": pid, "trial_id": tid,
        "observation_id": obs["observation_id"],
    })
    return hid, tid, obs["observation_id"]


async def connect(url):
    """Async context: an initialized MCP session."""
    return streamable_http_client(url)


# ---------- tests ----------


class TestProgrammeTransitions:
    async def test_create_valid(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                prog = await resource(s, f"programme://{pid}")
                assert prog["status"] == "active"

    async def test_duplicate_goal_rejected(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                goal = f"dup-{uuid.uuid4().hex[:8]}: same question twice"
                args = {
                    "goal": goal, "constraints": {},
                    "allowed_variables": ["C"],
                    "budget": {"max_trials": 2},
                }
                await ok(s, "create_programme", args)
                e = await err(s, "create_programme", args)
                assert "programme" in e["error"].lower() or "duplicate" in e["error"].lower()

    async def test_utility_goal_rejected(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                e = await err(s, "create_programme", {
                    "goal": "paper capture for paper_id ARC-99",
                    "constraints": {}, "allowed_variables": ["C"],
                    "budget": {"max_trials": 2},
                })
                assert "utility" in e["error"].lower() or "research" in e["error"].lower()

    async def test_bad_candidate_ref_rejected(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                e = await err(s, "create_programme", {
                    "goal": f"candref-{uuid.uuid4().hex[:8]}: q?",
                    "constraints": {}, "allowed_variables": ["C"],
                    "budget": {"max_trials": 2},
                    "candidate_version_id": "cand-nonexistent",
                })
                assert "candidate" in e["error"].lower()

    async def test_close_completed_rejected_with_under_test(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                await design_trial(s, pid, hid)  # under_test, never concluded
                await err(s, "close_programme", {
                    "programme_id": pid, "status": "completed",
                })
                prog = await resource(s, f"programme://{pid}")
                assert prog["status"] == "active"

    async def test_close_abandoned_valid(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                await ok(s, "close_programme", {
                    "programme_id": pid, "status": "abandoned",
                })
                # the auto-archiver then moves the live row to `archived`
                prog = await resource(s, f"programme://{pid}")
                assert prog["status"] == "archived"

    async def test_update_metric_direction(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                r = await ok(s, "update_metric_direction", {
                    "programme_id": pid, "metric_direction": "minimize",
                })
                assert r["metric_direction"] == "minimize"
                await err(s, "update_metric_direction", {
                    "programme_id": pid, "metric_direction": "sideways",
                })
                await err(s, "update_metric_direction", {
                    "programme_id": "prog-nonexistent",
                    "metric_direction": "minimize",
                })


class TestHypothesisTransitions:
    async def test_formulate_valid(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                hyps = await resource(s, f"programme://{pid}/hypotheses")
                h = [x for x in hyps if x["id"] == hid][0]
                assert h["status"] == "proposed"
                # tool-level enumeration agrees with the resource
                lh = await ok(s, "list_hypotheses", {"programme_id": pid})
                h2 = [x for x in lh["hypotheses"] if x["id"] == hid][0]
                assert h2["status"] == "proposed"
                await err(s, "list_hypotheses", {
                    "programme_id": "prog-nonexistent"})

    async def test_tautological_criterion_rejected(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                e = await err(s, "formulate_hypothesis", {
                    "programme_id": pid, "statement": "X improves Y.",
                    "variables_involved": ["X"],
                    "failure_criterion": "bad luck",
                })
                assert "falsifiab" in e["error"].lower()

    async def test_conclude_on_proposed_rejected(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                await err(s, "conclude_hypothesis", {
                    "programme_id": pid, "hypothesis_id": hid,
                    "verdict": "accepted", "evidence_summary": "n/a",
                })
                hyps = await resource(s, f"programme://{pid}/hypotheses")
                h = [x for x in hyps if x["id"] == hid][0]
                assert h["status"] == "proposed"

    async def test_conclude_without_evidence_rejected(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                await design_trial(s, pid, hid)  # under_test but never ran
                e = await err(s, "conclude_hypothesis", {
                    "programme_id": pid, "hypothesis_id": hid,
                    "verdict": "accepted", "evidence_summary": "n/a",
                })
                assert "completed" in e["error"].lower() or "evidence" in e["error"].lower()

    async def test_conclude_without_belief_rejected(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                tid, _ = await completed_trial(s, pid, hid)
                await ok(s, "record_observation", {
                    "trial_id": tid, "metrics": {"accuracy": 0.95},
                    "variance": {"accuracy": 0.001},
                    "spatiotemporal_region": "battery",
                })
                # no update_belief — belief gate must fire
                e = await err(s, "conclude_hypothesis", {
                    "programme_id": pid, "hypothesis_id": hid,
                    "verdict": "accepted", "evidence_summary": "n/a",
                })
                assert "belief" in e["error"].lower() or "memory" in e["error"].lower()

    async def test_conclude_unrecorded_trial_rejected(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                tid, _ = await completed_trial(s, pid, hid)
                # a second completed trial under the same hypothesis that
                # never gets an observation — commitment 7 must fire
                tid2 = await design_trial(s, pid, hid, {"C": 0.5})
                await bundle_trial(s, tid2)
                await ok(s, "run_trial", {"programme_id": pid, "trial_id": tid2})
                obs = await ok(s, "record_observation", {
                    "trial_id": tid, "metrics": {"accuracy": 0.95},
                    "variance": {"accuracy": 0.001},
                    "spatiotemporal_region": "battery",
                })
                await ok(s, "update_belief", {
                    "programme_id": pid, "trial_id": tid,
                    "observation_id": obs["observation_id"],
                })
                # tid2 completed but unrecorded → commitment 7 fires
                e = await err(s, "conclude_hypothesis", {
                    "programme_id": pid, "hypothesis_id": hid,
                    "verdict": "accepted", "evidence_summary": "n/a",
                })
                assert "observation" in e["error"].lower() or "recorded" in e["error"].lower()

    async def test_conclude_accepted_mints_claim(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid, tid, _ = await concludable(s, pid)
                r = await ok(s, "conclude_hypothesis", {
                    "programme_id": pid, "hypothesis_id": hid,
                    "verdict": "accepted", "evidence_summary": "holds.",
                })
                assert r["claim_status"] == "minted"
                hyps = await resource(s, f"programme://{pid}/hypotheses")
                h = [x for x in hyps if x["id"] == hid][0]
                assert h["status"] == "accepted"
                # anamnesis side: real claim with real edges
                async with streamable_http_client(pair["mem"]) as (r2, w2):
                    async with ClientSession(r2, w2) as m:
                        await m.initialize()
                        c = await ok(m, "get_claim", {"claim_id": r["claim_id"]})
                        assert c["claim"]["confidence"] == 0.85
                        assert "holds under" in c["claim"]["content"]
                        rels = {(e["ref_type"], e["relation"])
                                for e in c["outgoing_edges"]}
                        assert ("trial", "tested_by") in rels
                        assert ("conclusion", "derived_from") in rels
                        assert ("observation", "supports") in rels

    async def test_conclude_rejected_mints_falsification(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid, _, _ = await concludable(s, pid)
                r = await ok(s, "conclude_hypothesis", {
                    "programme_id": pid, "hypothesis_id": hid,
                    "verdict": "rejected",
                    "evidence_summary": "clean falsification.",
                })
                assert r["claim_status"] == "minted"
                async with streamable_http_client(pair["mem"]) as (r2, w2):
                    async with ClientSession(r2, w2) as m:
                        await m.initialize()
                        c = await ok(m, "get_claim", {"claim_id": r["claim_id"]})
                        # falsification is knowledge: high confidence, negated content
                        assert c["claim"]["confidence"] == 0.85
                        assert "does not hold" in c["claim"]["content"]

    async def test_conclude_inconclusive_mints_nothing(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid, _, _ = await concludable(s, pid)
                r = await ok(s, "conclude_hypothesis", {
                    "programme_id": pid, "hypothesis_id": hid,
                    "verdict": "inconclusive",
                    "evidence_summary": "underpowered.",
                })
                assert r["claim_status"] == "skipped"
                assert "claim_id" not in r

    async def test_double_conclude_rejected(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid, _, _ = await concludable(s, pid)
                await ok(s, "conclude_hypothesis", {
                    "programme_id": pid, "hypothesis_id": hid,
                    "verdict": "accepted", "evidence_summary": "first.",
                })
                e = await err(s, "conclude_hypothesis", {
                    "programme_id": pid, "hypothesis_id": hid,
                    "verdict": "rejected", "evidence_summary": "second.",
                })
                assert "conclusion" in e["error"].lower() or "already" in e["error"].lower()

    async def test_invalid_verdict_rejected(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid, _, _ = await concludable(s, pid)
                await err(s, "conclude_hypothesis", {
                    "programme_id": pid, "hypothesis_id": hid,
                    "verdict": "maybe", "evidence_summary": "n/a",
                })
                hyps = await resource(s, f"programme://{pid}/hypotheses")
                h = [x for x in hyps if x["id"] == hid][0]
                assert h["status"] == "under_test"

    async def test_close_abandoned_marks_under_test(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                await design_trial(s, pid, hid)
                await ok(s, "close_programme", {
                    "programme_id": pid, "status": "abandoned",
                })
                hyps = await resource(s, f"programme://{pid}/hypotheses")
                h = [x for x in hyps if x["id"] == hid][0]
                assert h["status"] == "abandoned"


class TestTrialTransitions:
    async def test_design_valid(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                tid = await design_trial(s, pid, hid)
                st = await ok(s, "get_trial_status", {
                    "programme_id": pid, "trial_id": tid})
                assert st["status"] == "designed"
                # tool-level enumeration recovers the trial id
                lt = await ok(s, "list_trials", {"programme_id": pid})
                t2 = [x for x in lt["trials"] if x["id"] == tid][0]
                assert t2["status"] == "designed"
                assert t2["hypothesis_id"] == hid
                await err(s, "list_trials", {
                    "programme_id": "prog-nonexistent"})

    async def test_design_on_unknown_hypothesis_rejected(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                await err(s, "design_experiment", {
                    "programme_id": pid, "hypothesis_id": "hyp-nope",
                    "config": {"C": 1.0},
                })

    async def test_budget_exhausted_rejected(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s, max_trials=1)
                hid = await mk_hypothesis(s, pid)
                await design_trial(s, pid, hid)
                e = await err(s, "design_experiment", {
                    "programme_id": pid, "hypothesis_id": hid,
                    "config": {"C": 0.5},
                })
                assert "budget" in e["error"].lower()

    async def test_run_without_bundle_rejected(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                tid = await design_trial(s, pid, hid)
                e = await err(s, "run_trial", {
                    "programme_id": pid, "trial_id": tid})
                assert "bundle" in e["error"].lower()
                st = await ok(s, "get_trial_status", {
                    "programme_id": pid, "trial_id": tid})
                assert st["status"] == "designed"

    async def test_capture_bundle_twice_rejected(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                tid = await design_trial(s, pid, hid)
                await bundle_trial(s, tid)
                e = await err(s, "capture_bundle", {
                    "trial_id": tid, "code_ref": TRAIN_STUB,
                    "env_ref": "uv@0.10", "seeds": [7],
                    "splits": {"train": "/tmp/a", "val": "/tmp/b"},
                })
                assert "already" in e["error"].lower()

    async def test_capture_bad_code_ref_rejected(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                tid = await design_trial(s, pid, hid)
                e = await err(s, "capture_bundle", {
                    "trial_id": tid, "code_ref": "/nonexistent/x.py",
                    "env_ref": "uv", "seeds": [7],
                    "splits": {"train": "/a", "val": "/b"},
                })
                assert "code_ref" in e["error"].lower()

    async def test_run_completes(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                tid, r = await completed_trial(s, pid, hid)
                st = await ok(s, "get_trial_status", {
                    "programme_id": pid, "trial_id": tid})
                assert st["status"] == "completed"

    async def test_rerun_terminal_rejected(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                tid, _ = await completed_trial(s, pid, hid)
                e = await err(s, "run_trial", {
                    "programme_id": pid, "trial_id": tid})
                assert "terminal" in e["error"].lower() or "already" in e["error"].lower()

    async def test_executor_failure_marks_failed(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                # executor failure is a structured result, not a rejection:
                # {"status": "failed", "error": ...} — so use call(), not ok()
                tid, r = await completed_trial(s, pid, hid, code=FAIL_STUB)
                assert r["status"] == "failed"
                assert "error" in r
                st = await ok(s, "get_trial_status", {
                    "programme_id": pid, "trial_id": tid})
                assert st["status"] == "failed"

    async def test_cancel_running_marks_failed(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                tid = await design_trial(s, pid, hid)
                await bundle_trial(s, tid, SLEEP_STUB)
                r = await ok(s, "run_trial", {
                    "programme_id": pid, "trial_id": tid})
                assert r["status"] == "running"  # async dispatch path
                c = await ok(s, "cancel_trial", {
                    "programme_id": pid, "trial_id": tid})
                assert c["status"] == "failed"
                st = await ok(s, "get_trial_status", {
                    "programme_id": pid, "trial_id": tid})
                assert st["status"] == "failed"

    async def test_mark_retryable(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                tid = await design_trial(s, pid, hid)
                await bundle_trial(s, tid, SLEEP_STUB)
                await ok(s, "run_trial", {
                    "programme_id": pid, "trial_id": tid})
                c = await ok(s, "mark_retryable", {
                    "programme_id": pid, "trial_id": tid,
                    "reason": "infra: node preempted",
                })
                assert c["status"] == "retryable"

    async def test_cancel_non_running_rejected(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                tid, _ = await completed_trial(s, pid, hid)
                e = await err(s, "cancel_trial", {
                    "programme_id": pid, "trial_id": tid})
                assert "not running" in e["error"].lower()

    async def test_cancel_designed_abandons(self, pair):
        """designed → abandoned: the honest terminal for a design
        that will never run (e.g. a bundle locked to dead code).
        No executor to kill — same transition close_programme uses."""
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                tid = await design_trial(s, pid, hid)
                r = await ok(s, "cancel_trial", {
                    "programme_id": pid, "trial_id": tid})
                assert r["status"] == "abandoned"
                st = await ok(s, "get_trial_status", {
                    "programme_id": pid, "trial_id": tid})
                assert st["status"] == "abandoned"

    async def test_wrong_programme_rejected(self, pair):
        """Commitment 1: the loop is the unit — cross-programme access."""
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                other = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                tid = await design_trial(s, pid, hid)
                await err(s, "run_trial", {
                    "programme_id": other, "trial_id": tid})


class TestBeliefAndObservation:
    async def test_observation_on_uncompleted_rejected(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                tid = await design_trial(s, pid, hid)
                await bundle_trial(s, tid)  # bundled but designed, not run
                e = await err(s, "record_observation", {
                    "trial_id": tid, "metrics": {"accuracy": 0.9},
                    "variance": {"accuracy": 0.01},
                    "spatiotemporal_region": "battery",
                })
                assert "completed" in e["error"].lower() or "loop" in e["error"].lower()

    async def test_observation_without_variance_rejected(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                tid, _ = await completed_trial(s, pid, hid)
                e = await err(s, "record_observation", {
                    "trial_id": tid, "metrics": {"accuracy": 0.9},
                    "variance": {},
                    "spatiotemporal_region": "battery",
                })
                assert "variance" in e["error"].lower() or "reproducib" in e["error"].lower()

    async def test_update_belief_valid(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid, tid, obs_id = await concludable(s, pid)
                bel = await resource(s, f"programme://{pid}/belief")
                assert bel is not None

    async def test_update_belief_bogus_observation_rejected(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                tid, _ = await completed_trial(s, pid, hid)
                await err(s, "update_belief", {
                    "programme_id": pid, "trial_id": tid,
                    "observation_id": "obs-nonexistent",
                })

    async def test_update_belief_cross_programme_rejected(self, pair):
        """Observation from programme A must not feed programme B's belief."""
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pa = await mk_programme(s)
                pb = await mk_programme(s)
                # complete a trial + observation in A
                ha = await mk_hypothesis(s, pa)
                ta, _ = await completed_trial(s, pa, ha)
                obs_a = await ok(s, "record_observation", {
                    "trial_id": ta, "metrics": {"accuracy": 0.9},
                    "variance": {"accuracy": 0.01},
                    "spatiotemporal_region": "battery",
                })
                # complete a trial in B
                hb = await mk_hypothesis(s, pb)
                tb, _ = await completed_trial(s, pb, hb)
                # inject A's observation into B's belief → must reject
                e = await err(s, "update_belief", {
                    "programme_id": pb, "trial_id": tb,
                    "observation_id": obs_a["observation_id"],
                })
                assert "memory" in e["error"].lower() or "optimization" in e["error"].lower()


class TestLineage:
    async def test_register_and_lineage(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                c = await ok(s, "register_candidate", {
                    "code_artifact_digest": "none",
                    "model_ref": "m/1",
                    "capability_profile": {},
                })
                lin = await ok(s, "get_candidate_lineage", {
                    "candidate_id": c["candidate_id"]})
                assert lin["lineage"][0]["id"] == c["candidate_id"]

    async def test_bad_parent_rejected(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                await err(s, "register_candidate", {
                    "code_artifact_digest": "none",
                    "model_ref": "m/1", "capability_profile": {},
                    "parent_id": "cand-nonexistent",
                })

    async def test_contract_bad_programme_rejected(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                await err(s, "create_evaluation_contract", {
                    "programme_id": "prog-nonexistent",
                    "metrics": {"primary": "accuracy"},
                    "promotion_policy": {"rule": "beat champion"},
                })

    async def test_decision_validation(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                c = await ok(s, "register_candidate", {
                    "code_artifact_digest": "none",
                    "model_ref": "m/1", "capability_profile": {},
                })
                pid = await mk_programme(s)
                ct = await ok(s, "create_evaluation_contract", {
                    "programme_id": pid,
                    "metrics": {"primary": "accuracy"},
                    "promotion_policy": {"rule": "beat champion"},
                })
                args = {
                    "candidate_id": c["candidate_id"],
                    "contract_id": ct["contract_id"],
                    "verdict": "promote", "rationale": "won",
                    "decided_by": "battery", "evidence_refs": [],
                }
                # invalid verdict
                bad = dict(args, verdict="crown")
                await err(s, "record_promotion_decision", bad)
                # empty rationale
                bad = dict(args, rationale="")
                await err(s, "record_promotion_decision", bad)
                # valid
                await ok(s, "record_promotion_decision", args)


class TestArchive:
    async def test_close_completed_archives(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid, _, _ = await concludable(s, pid)
                await ok(s, "conclude_hypothesis", {
                    "programme_id": pid, "hypothesis_id": hid,
                    "verdict": "accepted", "evidence_summary": "done.",
                })
                r = await ok(s, "close_programme", {
                    "programme_id": pid, "status": "completed",
                })
                assert "archived" in r
                prog = await resource(s, f"programme://{pid}")
                assert prog["status"] == "archived"
                archives = await ok(s, "list_archives", {})
                assert len(archives["archives"]) >= 1

    async def test_mutation_on_closed_rejected(self, pair):
        """A closed programme is immutable — every mutation must reject."""
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                tid = await design_trial(s, pid, hid)
                await ok(s, "close_programme", {
                    "programme_id": pid, "status": "abandoned"})
                # the abandoned programme now rejects every mutation
                for name, args in [
                    ("formulate_hypothesis", {
                        "programme_id": pid, "statement": "X beats Y.",
                        "variables_involved": ["X"],
                        "failure_criterion": "reject if not",
                    }),
                    ("design_experiment", {
                        "programme_id": pid, "hypothesis_id": hid,
                        "config": {"C": 0.5},
                    }),
                    ("capture_bundle", {
                        "trial_id": tid, "code_ref": TRAIN_STUB,
                        "env_ref": "uv", "seeds": [7],
                        "splits": {"train": "/a", "val": "/b"},
                    }),
                    ("run_trial", {
                        "programme_id": pid, "trial_id": tid,
                    }),
                    ("record_observation", {
                        "trial_id": tid, "metrics": {"a": 1.0},
                        "variance": {"a": 0.1},
                        "spatiotemporal_region": "battery",
                    }),
                    ("update_belief", {
                        "programme_id": pid, "trial_id": tid,
                        "observation_id": "obs-x",
                    }),
                    ("conclude_hypothesis", {
                        "programme_id": pid, "hypothesis_id": hid,
                        "verdict": "accepted", "evidence_summary": "x",
                    }),
                    ("update_metric_direction", {
                        "programme_id": pid, "metric_direction": "minimize",
                    }),
                    ("create_evaluation_contract", {
                        "programme_id": pid,
                        "metrics": {"primary": "accuracy"},
                        "promotion_policy": {"rule": "beat champion"},
                    }),
                ]:
                    e = await err(s, name, args)
                    assert "immutable" in e["error"].lower() or \
                        "closed" in e["error"].lower() or \
                        "abandoned" in e["error"].lower(), \
                        f"{name}: {e['error']}"

    async def test_mutation_on_nonexistent_programme_rejected(self, pair):
        """formulate_hypothesis on a missing programme must not create
        an orphan hypothesis row."""
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                await err(s, "formulate_hypothesis", {
                    "programme_id": "prog-nonexistent",
                    "statement": "X beats Y.",
                    "variables_involved": ["X"],
                    "failure_criterion": "reject if not",
                })

    async def test_verify_archive(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                archives = await ok(s, "list_archives", {})
                if not archives["archives"]:
                    pytest.skip("no archives yet")
                aid = archives["archives"][0]["archive_id"]
                v = await ok(s, "verify_archive", {"archive_id": aid})
                assert v["verified"] is True
                d = await ok(s, "get_archive", {"archive_id": aid})
                assert "archive" in d


class TestAnamnesisDirect:
    """Invalid ops on the claims server itself — it has its own gates."""

    async def test_bad_claim_type_rejected(self, pair):
        async with streamable_http_client(pair["mem"]) as (r, w):
            async with ClientSession(r, w) as m:
                await m.initialize()
                e = await err(m, "assert_claim", {
                    "content": "x", "type": "astrological",
                    "confidence": 0.5,
                })
                assert "type" in e["error"].lower()

    async def test_no_evidence_caps_confidence(self, pair):
        """Prior ceiling: without evidence, confidence > 0.3 is rejected."""
        async with streamable_http_client(pair["mem"]) as (r, w):
            async with ClientSession(r, w) as m:
                await m.initialize()
                e = await err(m, "assert_claim", {
                    "content": f"unsupported-{uuid.uuid4().hex[:8]}",
                    "type": "empirical", "confidence": 0.9,
                })
                assert "ceiling" in e["error"].lower() or "evidence" in e["error"].lower()
                # at or below the ceiling it lands
                r = await ok(m, "assert_claim", {
                    "content": f"unsupported-{uuid.uuid4().hex[:8]}",
                    "type": "empirical", "confidence": 0.3,
                })
                c = await ok(m, "get_claim", {"claim_id": r["claim_id"]})
                assert c["claim"]["confidence"] == 0.3

    async def test_dedup_returns_existing(self, pair):
        async with streamable_http_client(pair["mem"]) as (r, w):
            async with ClientSession(r, w) as m:
                await m.initialize()
                content = f"dedup-{uuid.uuid4().hex[:8]}"
                args = {"content": content, "type": "empirical",
                        "confidence": 0.3}
                first = await ok(m, "assert_claim", args)
                second = await ok(m, "assert_claim", args)
                assert second["claim_id"] == first["claim_id"]
                assert second.get("deduplicated") is True

    async def test_supersedes_nonexistent_rejected(self, pair):
        async with streamable_http_client(pair["mem"]) as (r, w):
            async with ClientSession(r, w) as m:
                await m.initialize()
                await err(m, "assert_claim", {
                    "content": "x", "type": "empirical",
                    "confidence": 0.3,
                    "supersedes_id": "claim-nonexistent",
                })

    async def test_relate_validation(self, pair):
        async with streamable_http_client(pair["mem"]) as (r, w):
            async with ClientSession(r, w) as m:
                await m.initialize()
                c = await ok(m, "assert_claim", {
                    "content": f"relate-{uuid.uuid4().hex[:8]}",
                    "type": "empirical", "confidence": 0.3,
                })
                cid = c["claim_id"]
                # bad relation
                await err(m, "relate", {
                    "from_claim": cid, "to_ref": "trial-x",
                    "ref_type": "trial", "relation": "hugs",
                })
                # bad ref_type
                await err(m, "relate", {
                    "from_claim": cid, "to_ref": "x",
                    "ref_type": "planet", "relation": "supports",
                })
                # claim-typed ref must exist
                await err(m, "relate", {
                    "from_claim": cid, "to_ref": "claim-nope",
                    "ref_type": "claim", "relation": "supports",
                })
                # valid edge
                e = await ok(m, "relate", {
                    "from_claim": cid, "to_ref": "trial-real",
                    "ref_type": "trial", "relation": "tested_by",
                })
                assert e["status"] == "created"

    async def test_get_unknown_claim(self, pair):
        async with streamable_http_client(pair["mem"]) as (r, w):
            async with ClientSession(r, w) as m:
                await m.initialize()
                e = await err(m, "get_claim", {"claim_id": "claim-nope"})
                assert "not found" in e["error"].lower()


class TestClaimsUnavailable:
    """Absent means absent — claims never gate the verdict."""

    async def test_claims_down_at_startup(self, tmp_path):
        """ml-episteme starts fine with a dead claims URL; conclude works."""
        procs, urls = _start_pair(tmp_path, claims_up=False)
        try:
            async with streamable_http_client(urls["sci"]) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    pid = await mk_programme(s)
                    hid, _, _ = await concludable(s, pid)
                    r = await ok(s, "conclude_hypothesis", {
                        "programme_id": pid, "hypothesis_id": hid,
                        "verdict": "accepted",
                        "evidence_summary": "verdict must land",
                    })
                    # verdict recorded despite claims being absent
                    assert r["verdict"] == "accepted"
                    assert "claim_id" not in r
        finally:
            _stop(procs)

    async def test_claims_dies_mid_session(self, tmp_path):
        """Anamnesis killed after startup → mint fails, verdict still lands."""
        procs, urls = _start_pair(tmp_path)
        try:
            # kill anamnesis (first proc) after startup connect
            procs[0].terminate()
            procs[0].wait(timeout=5)
            async with streamable_http_client(urls["sci"]) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    pid = await mk_programme(s)
                    hid, _, _ = await concludable(s, pid)
                    r = await ok(s, "conclude_hypothesis", {
                        "programme_id": pid, "hypothesis_id": hid,
                        "verdict": "accepted",
                        "evidence_summary": "claims died mid-session",
                    })
                    assert r["verdict"] == "accepted"
                    assert r.get("claim_status") == "failed"
                    assert "claim_error" in r
                    assert "claim_id" not in r  # no fabricated id
        finally:
            _stop(procs)


class TestSessionProtocol:
    """The agent-facing protocol://session resource must not drift from
    the real tool surface — a stale catalog feeds agents hallucinated
    tool names."""

    async def test_tool_catalog_matches_registered_tools(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                proto = await resource(s, "protocol://session")
                registered = {t.name for t in (await s.list_tools()).tools}
                cataloged = {t["name"] for t in proto["tool_catalog"]}
                assert cataloged == registered, (
                    f"catalog drift — missing: {registered - cataloged}, "
                    f"phantom: {cataloged - registered}"
                )

    async def test_state_machine_covers_terminal_states(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                proto = await resource(s, "protocol://session")
                sm = proto["state_machine"]
                # archive is the real terminal programme state
                assert "archived" in sm["programme"]
                assert sm["programme"]["archived"] == []
                assert sm["programme"]["completed"] == ["archived"]
                assert sm["programme"]["abandoned"] == ["archived"]
                # trials can be abandoned from designed (programme close)
                assert "abandoned" in sm["trial"]["designed"]

    async def test_failure_handling_documented(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                proto = await resource(s, "protocol://session")
                rules = " ".join(proto["failure_handling"])
                # the honest encodings an agent must not get wrong
                assert "failed" in rules
                assert "record_observation" in rules
                assert "retryable" in rules
                assert "abandoned" in rules

    async def test_listed_resources_resolve(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                proto = await resource(s, "protocol://session")
                listed = {r["uri"] for r in proto["resources"]}
                # every non-parameterized URI must resolve
                concrete = [u for u in listed if "{" not in u]
                assert concrete  # at least protocol://session + executor://contract
                for uri in concrete:
                    r = await s.read_resource(uri)
                    assert r.contents, f"resource {uri} listed but empty"


class TestArtifactAndOutputAccess:
    """Executor output and captured artifacts are durable scientific
    records — they must survive restart and be readable over MCP."""

    async def test_executor_output_returned_after_finalize(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                tid, run = await completed_trial(s, pid, hid)
                assert run["status"] == "completed"
                st = await ok(s, "get_trial_status", {
                    "programme_id": pid, "trial_id": tid,
                })
                assert st["executor_output"] is not None
                out = json.loads(st["executor_output"])
                assert out["status"] == "completed"

    async def test_executor_output_persists_into_archive(self, pair):
        """The durable record must reach the archive — a live row that
        keeps output only in volatile executor memory loses it on
        restart (the bug this test guards)."""
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid, tid, _ = await concludable(s, pid)
                await ok(s, "conclude_hypothesis", {
                    "programme_id": pid, "hypothesis_id": hid,
                    "verdict": "accepted",
                    "evidence_summary": "output persistence check",
                })
                await ok(s, "close_programme", {
                    "programme_id": pid, "status": "completed",
                })
                arch = await ok(s, "get_archived_programme", {
                    "programme_id": pid,
                })
                rows = {t["id"]: t for t in arch["trials"]}
                assert tid in rows
                assert rows[tid]["executor_output_json"] is not None
                out = json.loads(rows[tid]["executor_output_json"])
                assert out["status"] == "completed"

    async def test_artifacts_served_from_sqlite(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                tid, run = await completed_trial(s, pid, hid)
                assert run["status"] == "completed"
                art = await resource(s, f"trial://{tid}/artifacts")
                assert art["source"] == "sqlite"
                assert art["artifacts"], "captured artifact list empty"
                for a in art["artifacts"]:
                    assert a["content_hash"]
                    assert a["uri"].startswith("artifact://")
                    assert a["size_bytes"] >= 0  # stderr.txt may be empty
                types = {a["artifact_type"] for a in art["artifacts"]}
                assert "stdout" in types

    async def test_artifact_content_by_hash(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                tid, _ = await completed_trial(s, pid, hid)
                art = await resource(s, f"trial://{tid}/artifacts")
                stdout = next(
                    a for a in art["artifacts"]
                    if a["artifact_type"] == "stdout"
                )
                doc = await resource(s, f"artifact://{stdout['content_hash']}")
                assert doc["encoding"] == "utf-8"
                # the stdout artifact carries the run_training result
                assert "metrics" in doc["content"] or "status" in doc["content"]

    async def test_artifact_unknown_hash_rejected(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                doc = await resource(s, "artifact://deadbeef")
                assert "error" in doc


class TestExecutionTelemetry:
    """A running trial must report elapsed time, progress, and ETA —
    and every trial must carry a real execution window
    (created_at is design time, not run time)."""

    async def test_running_trial_reports_elapsed_progress_eta(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                tid = await design_trial(s, pid, hid)
                await bundle_trial(s, tid, code=str(FIXTURES / "progress_stub.py"))
                r = await ok(s, "run_trial", {"programme_id": pid, "trial_id": tid})
                assert r["status"] == "running"

                st = await ok(s, "get_trial_status", {
                    "programme_id": pid, "trial_id": tid,
                })
                assert st["status"] == "running"
                assert st["started_at"] is not None
                assert st["elapsed_seconds"] is not None
                assert st["elapsed_seconds"] >= 0
                assert st["progress"] == {
                    "pct": 0.5, "message": "seed 1 of 2 done",
                }
                assert st["eta_seconds"] is not None
                assert st["eta_seconds"] >= 0

                # cleanup: cancel stamps finished_at on the durable row
                await ok(s, "cancel_trial", {"programme_id": pid, "trial_id": tid})
                st2 = await ok(s, "get_trial_status", {
                    "programme_id": pid, "trial_id": tid,
                })
                assert st2["status"] == "failed"
                assert st2["finished_at"] is not None
                assert st2["started_at"] <= st2["finished_at"]

    async def test_completed_trial_carries_execution_window(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                tid, run = await completed_trial(s, pid, hid)
                assert run["status"] == "completed"

                st = await ok(s, "get_trial_status", {
                    "programme_id": pid, "trial_id": tid,
                })
                assert st["started_at"] is not None
                assert st["finished_at"] is not None
                assert st["started_at"] <= st["finished_at"]
                assert st["duration_seconds"] is not None
                # design precedes execution
                trial_row = next(
                    t for t in (await ok(s, "list_trials", {
                        "programme_id": pid,
                    }))["trials"] if t["id"] == tid
                )
                assert trial_row["created_at"] <= trial_row["started_at"]
                assert trial_row["started_at"] <= trial_row["finished_at"]

    async def test_trials_resource_carries_timestamps(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                tid, _ = await completed_trial(s, pid, hid)
                rows = await resource(s, f"programme://{pid}/trials")
                row = next(t for t in rows if t["id"] == tid)
                for k in ("created_at", "started_at", "finished_at",
                          "duration_seconds"):
                    assert k in row, f"trials resource missing {k}"
                assert row["started_at"] <= row["finished_at"]

    async def test_no_progress_means_no_fabricated_eta(self, pair):
        """A running trial without progress.json reports elapsed time
        but null progress/eta — ETA is never invented."""
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                tid = await design_trial(s, pid, hid)
                await bundle_trial(s, tid, code=SLEEP_STUB)
                r = await ok(s, "run_trial", {"programme_id": pid, "trial_id": tid})
                assert r["status"] == "running"

                st = await ok(s, "get_trial_status", {
                    "programme_id": pid, "trial_id": tid,
                })
                assert st["status"] == "running"
                assert st["elapsed_seconds"] is not None
                assert st["progress"] is None
                assert st["eta_seconds"] is None
                # the null ETA is explained, not silent — the client is
                # told how to get live progress reporting
                assert "progress.json" in st["hint"]

                await ok(s, "cancel_trial", {"programme_id": pid, "trial_id": tid})


class TestContentAddressedExecution:
    """code:// bundles must execute with real import semantics — the
    inlining path could not satisfy `from pkg.mod import x`."""

    async def test_code_hash_bundle_resolves_dotted_import(self, pair):
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)

                # Seed the snippet store: capture the dep-importing
                # entry by file path on a throwaway trial. AST capture
                # picks up depmod/helper.py as an extra snippet.
                tid_seed = await design_trial(s, pid, hid)
                cap = await ok(s, "capture_bundle", {
                    "trial_id": tid_seed,
                    "code_ref": str(FIXTURES / "dep_entry.py"),
                    "env_ref": "uv@0.10", "seeds": [7],
                    "splits": {"train": "/tmp/train.csv"},
                })
                assert cap["code_hash_extra"], "dep import not captured"

                # Now the rerun path: a fresh trial bundled by content hash.
                tid = await design_trial(s, pid, hid)
                await ok(s, "capture_bundle_from_code_hash", {
                    "trial_id": tid,
                    "code_hash": cap["code_hash"],
                    "code_hash_extra": cap["code_hash_extra"],
                    "env_ref": "uv@0.10", "seeds": [7],
                    "splits": {"train": "/tmp/train.csv"},
                })
                run = await ok(s, "run_trial", {
                    "programme_id": pid, "trial_id": tid,
                })
                assert run["status"] == "completed", run
                out = json.loads(run["executor_output"])
                assert out["status"] == "completed"
                # run_training's result is the wrapper's stdout
                inner = json.loads(out["stdout"])
                assert inner["metrics"]["acc"] == 0.9

    async def test_trial_subprocess_cwd_is_artifact_dir(self, pair):
        """The wrapper subprocess runs with cwd = the trial's artifact
        dir, so relative-path writes in user code are per-trial
        (and captured as artifacts) rather than colliding in the
        server process's cwd across concurrent trials."""
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                tid = await design_trial(s, pid, hid)
                await bundle_trial(
                    s, tid, code=str(FIXTURES / "cwd_stub.py"))
                run = await ok(s, "run_trial", {
                    "programme_id": pid, "trial_id": tid,
                })
                assert run["status"] == "completed", run
                out = json.loads(run["executor_output"])
                inner = json.loads(out["stdout"])

                st = await ok(s, "get_trial_status", {
                    "programme_id": pid, "trial_id": tid,
                })
                assert Path(inner["_cwd"]) == Path(
                    st["artifact_path"]).resolve()
                # tempfile users are redirected into the trial workspace
                assert Path(inner["_tmpdir"]).resolve().is_relative_to(
                    Path(st["artifact_path"]).resolve()
                ), inner["_tmpdir"]

                arts = await resource(s, f"trial://{tid}/artifacts")
                names = [a["filename"] for a in arts["artifacts"]]
                assert any("rel_write.txt" in n for n in names), names

    async def test_capture_bundle_lints_shared_paths(self, pair):
        """Bundle code containing a hardcoded /tmp path produces an
        advisory warning at capture time (concurrent trials would race
        on the shared file). Non-blocking — capture still succeeds."""
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                tid = await design_trial(s, pid, hid)
                cap = await ok(s, "capture_bundle", {
                    "trial_id": tid,
                    "code_ref": str(FIXTURES / "shared_path_stub.py"),
                    "env_ref": "uv@0.10", "seeds": [7],
                    "splits": {"train": "/tmp/train.csv"},
                })
                assert cap["status"] == "captured"
                assert cap["warnings"], "expected shared-path warning"
                w = cap["warnings"][0]
                assert "/tmp/ml_sci_shared_cfg.json" in w["literal"]
                assert w["line"] is not None

                # clean code captures with no warnings
                tid2 = await design_trial(s, pid, hid)
                cap2 = await ok(s, "capture_bundle", {
                    "trial_id": tid2, "code_ref": TRAIN_STUB,
                    "env_ref": "uv@0.10", "seeds": [7],
                    "splits": {"train": "/tmp/train.csv"},
                })
                assert cap2["warnings"] == []

    async def test_trials_get_private_tmp_under_sandbox(self, pair):
        """Two sequential trials writing the same hardcoded /tmp path
        must not see each other's file — the sandbox gives each trial
        a private tmpfs (the prog-2fe3dfe3 config-clobber bug class).
        Skips when the executor degraded to sandbox=none."""
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                stub = str(FIXTURES / "tmp_probe_stub.py")

                results = []
                for _ in range(2):
                    tid = await design_trial(s, pid, hid)
                    await bundle_trial(s, tid, code=stub)
                    run = await ok(s, "run_trial", {
                        "programme_id": pid, "trial_id": tid,
                    })
                    assert run["status"] == "completed", run
                    out = json.loads(run["executor_output"])
                    if out.get("sandbox") == "none":
                        pytest.skip("sandbox unavailable on this host")
                    assert out["sandbox"] in ("minimal", "full")
                    results.append(json.loads(out["stdout"]))

                # trial 2 must not observe trial 1's /tmp file
                assert results[0]["_existed"] is False
                assert results[1]["_existed"] is False

    async def test_env_ref_selects_trial_interpreter(self, pair, tmp_path):
        """env_ref is load-bearing: a venv dir makes run_trial execute
        under that interpreter, and the applied interpreter is
        recorded in executor_output.python_exe."""
        import subprocess
        import sys
        subprocess.run(
            [sys.executable, "-m", "venv", "--without-pip",
             str(tmp_path / "venv")], check=True,
        )
        async with streamable_http_client(pair["sci"]) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pid = await mk_programme(s)
                hid = await mk_hypothesis(s, pid)
                tid = await design_trial(s, pid, hid)
                await bundle_trial(s, tid, code=EXE_PROBE,
                                   env_ref=str(tmp_path / "venv"))
                run = await ok(s, "run_trial", {
                    "programme_id": pid, "trial_id": tid,
                })
                assert run["status"] == "completed", run
                out = json.loads(run["executor_output"])
                venv_py = str(tmp_path / "venv" / "bin" / "python")
                assert out["python_exe"] == venv_py
                assert json.loads(out["stdout"])["exe"] == venv_py
