"""Integrity-layer tests for ml-episteme — the Loop-0 audit.

Covers: each check's violation and clean paths, the audit log's
write/prune/config behaviour, the check_invariants tool, the
/health/deep route, the /integrity GUI view, and non-mutation.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from ml_episteme_mcp.integrity.checks import run_and_log, run_checks
from ml_episteme_mcp.integrity.log import (
    list_check_logs,
    read_check_log,
    write_check_log,
)
from ml_episteme_mcp.state.models import (
    Bundle,
    Hypothesis,
    Observation,
    Programme,
    Trial,
)
from ml_episteme_mcp.state.store import StateStore


@pytest.fixture
def store(tmp_path):
    s = StateStore(str(tmp_path / "state.db"))
    s.connect()
    yield s
    s.close()


def _programme(pid="prog-t1", max_trials=50, max_hours=200.0):
    return Programme(
        id=pid,
        goal="test goal",
        constraints={},
        allowed_variables=["x"],
        budget_max_trials=max_trials,
        budget_max_wall_time_hours=max_hours,
    )


def _hypothesis(pid, hid="hyp-t1"):
    return Hypothesis(
        id=hid,
        programme_id=pid,
        statement="x improves y",
        failure_criterion="y(x) <= baseline",
        variables_involved=["x"],
    )


def _trial(pid, hid, tid="trial-t1"):
    return Trial(
        id=tid,
        programme_id=pid,
        hypothesis_id=hid,
        config_json=json.dumps({"x": 1}),
    )


def _seed(store, pid="prog-t1", hid="hyp-t1", tid="trial-t1"):
    store.create_programme(_programme(pid))
    store.create_hypothesis(_hypothesis(pid, hid))
    store.create_trial(_trial(pid, hid, tid))


def _check(payload, name):
    return next(c for c in payload["checks"] if c["name"] == name)


# --- Individual checks ---


def test_clean_store_is_ok(store):
    payload = run_checks(store)
    assert payload["status"] == "ok"
    assert len(payload["checks"]) == 10
    assert all(c["ok"] for c in payload["checks"])


def test_orphaned_running_trial_flagged(store):
    _seed(store)
    store.update_trial_status("trial-t1", "running")
    payload = run_checks(store)  # no executor → orphan
    c = _check(payload, "orphaned_running_trials")
    assert not c["ok"] and "trial-t1" in c["violations"]
    assert payload["status"] == "violations"


def test_live_executor_task_not_orphaned(store):
    _seed(store)
    store.update_trial_status("trial-t1", "running")

    class FakeExecutor:
        _running_tasks = {"trial-t1": object()}

    payload = run_checks(store, executor=FakeExecutor())
    assert _check(payload, "orphaned_running_trials")["ok"]


def test_completed_without_observation(store):
    _seed(store)
    store.update_trial_status("trial-t1", "running")
    store.update_trial_executor_output("trial-t1", '{"status": "completed", "exit_code": 0}')
    store.update_trial_status("trial-t1", "completed")

    # Inside the grace window — completed→observe is the normal loop
    # order; a trial mid-gap is not debt
    assert _check(run_checks(store), "completed_without_observation")["ok"]

    # Past the grace window — an abandoned observation is debt
    old = (
        datetime.now(timezone.utc) - timedelta(days=2)
    ).isoformat()
    store.conn.execute(
        "UPDATE trials SET finished_at = ? WHERE id = 'trial-t1'",
        (old,),
    )
    store.conn.commit()
    c = _check(run_checks(store), "completed_without_observation")
    assert not c["ok"] and "trial-t1" in c["violations"]

    store.create_observation(Observation(
        id="obs-t1", trial_id="trial-t1",
        metrics_json=json.dumps({"m": 1.0}),
        variance_json=json.dumps({"m": 0.01}),
        spatiotemporal_region="host:gpu-0@t",
    ))
    assert _check(run_checks(store), "completed_without_observation")["ok"]


def test_unsealed_execution(store):
    _seed(store)
    store.update_trial_executor_output(
        "trial-t1",
        json.dumps({"seal_enforced": False, "sandbox": "minimal"}),
    )
    c = _check(run_checks(store), "unsealed_execution")
    assert not c["ok"] and "trial-t1" in c["violations"]

    # sandbox=none is an honest opt-out — not a violation
    store.update_trial_executor_output(
        "trial-t1",
        json.dumps({"seal_enforced": False, "sandbox": "none"}),
    )
    assert _check(run_checks(store), "unsealed_execution")["ok"]


def test_strace_divergence(store, tmp_path):
    _seed(store)
    store.create_bundle(Bundle(
        id="bundle-t1", trial_id="trial-t1",
        code_ref="git:x", env_ref="e", seeds_json="[1]",
        splits_json="{}", baseline_ref="b",
        code_hash="sha256:sealed",
    ))
    # The check reads the manifest from the blob store — the staging
    # artifact dir is deleted at finalize and is not consulted.
    import hashlib
    content = json.dumps({
        "files": [
            {"original_path": "a.py", "code_hash": "sha256:sealed"},
            {"original_path": "b.py", "code_hash": "sha256:escaped"},
        ]
    }).encode()
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    store.create_artifact_file(
        content_hash=digest, filename="executed_code.json",
        content=content, content_type="application/json",
        captured_at="2026-01-01T00:00:00Z", original_path="/x",
    )
    store.create_trial_artifact(
        ta_id="ta-1", trial_id="trial-t1", content_hash=digest,
        filename="executed_code.json", artifact_type="other",
        created_at="2026-01-01T00:00:00Z",
    )
    c = _check(run_checks(store), "strace_divergence")
    assert not c["ok"]
    assert c["violations"][0]["trial_id"] == "trial-t1"
    assert c["violations"][0]["paths"] == ["b.py"]


def test_budget_exceeded(store):
    _seed(store)
    store.create_hypothesis(_hypothesis("prog-t1", "hyp-t2"))
    store.create_trial(_trial("prog-t1", "hyp-t2", "trial-t2"))
    store._conn.execute(
        "UPDATE programmes SET budget_max_trials = 1 WHERE id = 'prog-t1'"
    )
    c = _check(run_checks(store), "budget_exceeded")
    assert not c["ok"]
    assert c["violations"][0]["programme_id"] == "prog-t1"


def test_budget_undeclared_wall_not_flagged(store):
    """wall_time_hours=0 means no wall budget was declared (the schema
    default) — unbounded, not 'zero allowed'. Programmes with unset
    wall budgets must not trip the check on any wall time."""
    _seed(store)
    store._conn.execute(
        "UPDATE programmes SET budget_max_wall_time_hours = 0 "
        "WHERE id = 'prog-t1'"
    )
    store._conn.execute(
        "UPDATE trials SET duration_seconds = 99999 "
        "WHERE id = 'trial-t1'"
    )
    c = _check(run_checks(store), "budget_exceeded")
    assert c["ok"]
    # …but a declared wall budget still binds
    store._conn.execute(
        "UPDATE programmes SET budget_max_wall_time_hours = 1 "
        "WHERE id = 'prog-t1'"
    )
    c = _check(run_checks(store), "budget_exceeded")
    assert not c["ok"]
    assert c["violations"][0]["programme_id"] == "prog-t1"


def test_stuck_hypothesis(store):
    store.create_programme(_programme())
    store.create_hypothesis(_hypothesis("prog-t1"))
    store.update_hypothesis_status("hyp-t1", "under_test")
    c = _check(run_checks(store), "stuck_hypotheses")
    assert not c["ok"] and "hyp-t1" in c["violations"]


def test_stalled_running_trial(store):
    """Stalled = wedged past the trial's own enforced deadline, not a
    flat age threshold — a multi-hour trial inside its executor
    timeout is healthy work and must not flag."""
    _seed(store)
    store.update_trial_status("trial-t1", "running")

    class Task:
        def done(self):
            return False

    class FakeExecutor:
        _running_tasks = {"trial-t1": Task()}
        _timeout = 14400  # 4h trial

    # No executor → the check can't consult a deadline; skipped, ok
    assert _check(
        run_checks(store), "stalled_running_trials"
    )["ok"]

    # 30min into a 4h deadline — healthy work, not a stall
    store._conn.execute(
        "UPDATE trials SET started_at = ? WHERE id = 'trial-t1'",
        ((datetime.now(timezone.utc) - timedelta(minutes=30))
         .isoformat(),),
    )
    c = _check(run_checks(store, executor=FakeExecutor()),
               "stalled_running_trials")
    assert c["ok"]

    # Past deadline + margin → the executor should have killed it
    store._conn.execute(
        "UPDATE trials SET started_at = ? WHERE id = 'trial-t1'",
        ((datetime.now(timezone.utc) - timedelta(hours=5))
         .isoformat(),),
    )
    c = _check(run_checks(store, executor=FakeExecutor()),
               "stalled_running_trials")
    assert not c["ok"]
    assert c["violations"][0]["trial_id"] == "trial-t1"

    # Finished task that never finalized → wedged finalize path
    class DoneTask:
        def done(self):
            return True

    class DoneExecutor:
        _running_tasks = {"trial-t1": DoneTask()}
        _timeout = 14400

    store._conn.execute(
        "UPDATE trials SET started_at = ? WHERE id = 'trial-t1'",
        (datetime.now(timezone.utc).isoformat(),),
    )
    c = _check(run_checks(store, executor=DoneExecutor()),
               "stalled_running_trials")
    assert not c["ok"]
    assert "never finalized" in c["violations"][0]["detail"]


def test_mislabeled_outcome(store):
    """completed trial whose recorded output documents an inner crash —
    the trial-f8ffbb1f class: outer wrapper exit 0, inner status error."""
    _seed(store)
    store.update_trial_status("trial-t1", "running")
    store.update_trial_executor_output("trial-t1", '{"status": "completed", "exit_code": 0}')
    store.update_trial_status("trial-t1", "completed")
    store.update_trial_executor_output("trial-t1", json.dumps({
        "status": "completed",
        "exit_code": 0,
        "stdout": json.dumps({
            "status": "error",
            "_exit_code": 1,
            "_stderr_tail": "Traceback ...",
        }),
    }))
    c = _check(run_checks(store), "mislabeled_outcome")
    assert not c["ok"]
    assert c["violations"][0]["trial_id"] == "trial-t1"

    # An honest completed trial — no inner failure signal
    store.update_trial_executor_output("trial-t1", json.dumps({
        "status": "completed", "exit_code": 0,
        "stdout": json.dumps({"metrics": {"m": 1.0}}),
    }))
    assert _check(run_checks(store), "mislabeled_outcome")["ok"]

    # A corrected record is not re-flagged — the correction is recorded
    store.update_trial_executor_output("trial-t1", json.dumps({
        "status": "completed", "exit_code": 0,
        "stdout": json.dumps({"status": "error"}),
        "corrections": [{"from": "completed", "to": "failed",
                         "reason": "mislabeled", "corrected_at": "t"}],
    }))
    # (status stays completed here; corrections mark it already handled)
    assert _check(run_checks(store), "mislabeled_outcome")["ok"]


def test_non_mutation(store):
    _seed(store)
    store.update_trial_status("trial-t1", "running")
    before = store._fetchall("SELECT * FROM trials")
    run_checks(store)
    after = store._fetchall("SELECT * FROM trials")
    assert before == after


# --- Audit log ---


def test_log_appends_to_day_file(store):
    payload = run_and_log(store, config={})
    log_dir = Path(store.path).parent / "logs"
    files = list(log_dir.glob("check-*.jsonl"))
    assert len(files) == 1  # one file per UTC day
    assert files[0].name.endswith(
        datetime.now(timezone.utc).strftime("%Y-%m-%d") + ".jsonl"
    )
    recorded = json.loads(files[0].read_text().splitlines()[0])
    assert recorded["status"] == payload["status"]
    assert payload["log_file"] == str(files[0])

    # A second run appends a line to the SAME file — no new file
    run_and_log(store, config={})
    assert len(list(log_dir.glob("check-*.jsonl"))) == 1
    assert len(files[0].read_text().splitlines()) == 2


def test_log_pruning(store):
    log_dir = Path(store.path).parent / "logs"
    log_dir.mkdir(parents=True)
    # Seed fake day files — pruning bounds files, not runs
    for i in range(4):
        (log_dir / f"check-2026-09-1{i}.jsonl").write_text(
            json.dumps({"status": "ok", "checks": []}) + "\n"
        )
    write_check_log(log_dir, {"status": "ok", "checks": []}, 3)
    remaining = sorted(log_dir.glob("check-*.jsonl"))
    assert len(remaining) == 3
    assert "2026-09-10" not in [f.name for f in remaining]  # oldest pruned


def test_log_disabled(store):
    payload = run_and_log(store, config={"log_max_files": 0})
    assert "log_file" not in payload
    log_dir = Path(store.path).parent / "logs"
    assert not log_dir.exists() or not list(log_dir.glob("check-*.jsonl"))


# --- Tool + route + GUI ---


async def test_check_invariants_tool(store):
    from ml_episteme_mcp.server import create_server

    mcp = create_server(store, integrity_config={"log_max_files": 5})
    result = await mcp.call_tool("check_invariants", {})
    payload = json.loads(result.content[0].text)
    assert payload["server"] == "ml-episteme-mcp"
    assert payload["status"] == "ok"
    assert Path(payload["log_file"]).exists()


async def test_health_deep_route(store):
    from ml_episteme_mcp.server import create_server

    mcp = create_server(store, integrity_config={"log_max_files": 5})
    app = mcp.streamable_http_app()
    with TestClient(app) as client:
        r = client.get("/health/deep")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert len(body["checks"]) == 10


def test_integrity_gui(store):
    from ml_episteme_mcp.observability.server import (
        create_observability_app,
    )

    app = create_observability_app(store, refresh_interval=0)
    client = TestClient(app)

    # Empty: honest "no runs yet" page
    r = client.get("/integrity")
    assert r.status_code == 200
    assert "No integrity checks" in r.text

    # After a logged run: latest + history render, violations listed
    _seed(store)
    store.update_trial_status("trial-t1", "running")
    run_and_log(store, config={})
    r = client.get("/integrity")
    assert r.status_code == 200
    assert "orphaned_running_trials" in r.text
    assert "check-" in r.text  # history rows

    run = list_check_logs(Path(store.path).parent / "logs")[0]
    r = client.get(f"/integrity/run/{run['file']}/{run['index']}")
    assert r.status_code == 200
    assert "trial-t1" in r.text
    r = client.get(f"/integrity/log/{run['file']}")
    assert r.status_code == 200
    assert "orphaned_running_trials" in r.text


def test_integrity_help(store):
    """/integrity/help explains every emitted check name and is linked
    from the main page — static reference, not a log route."""
    from ml_episteme_mcp.observability.server import (
        create_observability_app,
    )

    app = create_observability_app(store, refresh_interval=0)
    client = TestClient(app)

    r = client.get("/integrity")
    assert r.status_code == 200
    assert '/integrity/help' in r.text

    r = client.get("/integrity/help")
    assert r.status_code == 200
    for name in (
        "upstream_connectivity", "orphaned_running_trials",
        "completed_without_observation", "unsealed_execution",
        "strace_divergence", "budget_exceeded", "stuck_hypotheses",
        "mislabeled_outcome", "stalled_running_trials",
    ):
        assert name in r.text


async def test_integrity_monitor(store):
    """The audit trail is on by default: startup sweep writes the day's
    first record immediately; periodic sweeps append interval records."""
    from ml_episteme_mcp.integrity.checks import start_integrity_monitor

    log_dir = Path(store.path).parent / "logs"

    # interval disabled → startup record still lands, no task
    task = start_integrity_monitor(
        store, config={"check_interval_seconds": 0}
    )
    assert task is None
    lines = list(log_dir.glob("check-*.jsonl"))[0].read_text().splitlines()
    assert json.loads(lines[0])["trigger"] == "startup"

    # periodic sweep appends trigger="interval" records
    task = start_integrity_monitor(
        store, config={"check_interval_seconds": 0.05}
    )
    assert task is not None
    await asyncio.sleep(0.15)
    task.cancel()
    lines = list(log_dir.glob("check-*.jsonl"))[0].read_text().splitlines()
    assert len(lines) >= 3
    assert json.loads(lines[-1])["trigger"] == "interval"


# --- Recorded repair ---


async def test_correct_trial_status_tool(store):
    """The legal repair surface — what the LLM should call instead of
    hand-editing state.db with sqlite3."""
    from ml_episteme_mcp.server import create_server

    _seed(store)
    store.update_trial_status("trial-t1", "running")
    store.update_trial_executor_output("trial-t1", '{"status": "completed", "exit_code": 0}')
    store.update_trial_status("trial-t1", "completed")
    store.update_trial_executor_output("trial-t1", json.dumps({
        "status": "completed", "exit_code": 0,
        "stdout": json.dumps({"status": "error", "_exit_code": 1}),
    }))

    mcp = create_server(store, integrity_config={"log_max_files": 0})

    # The mislabel is detected
    assert not _check(
        json.loads(
            (await mcp.call_tool("check_invariants", {})).content[0].text
        ),
        "mislabeled_outcome",
    )["ok"]

    # Correct it through the tool — recorded, not silent
    result = json.loads((await mcp.call_tool("correct_trial_status", {
        "programme_id": "prog-t1", "trial_id": "trial-t1",
        "to_status": "failed", "reason": "inner job crashed",
    })).content[0].text)
    assert result["from"] == "completed" and result["to"] == "failed"
    trial = store.get_trial("trial-t1")
    assert trial.status.value == "failed"
    out = json.loads(trial.executor_output_json)
    assert out["corrections"][0]["reason"] == "inner job crashed"

    # Missing reason is refused — a correction without a stated basis
    # is a silent rewrite
    result = json.loads((await mcp.call_tool("correct_trial_status", {
        "programme_id": "prog-t1", "trial_id": "trial-t1",
        "to_status": "completed", "reason": "  ",
    })).content[0].text)
    assert "error" in result

    # Non-terminal source is refused — running trials use
    # cancel_trial / mark_retryable
    store.create_trial(_trial("prog-t1", "hyp-t1", "trial-t2"))
    store.update_trial_status("trial-t2", "running")
    result = json.loads((await mcp.call_tool("correct_trial_status", {
        "programme_id": "prog-t1", "trial_id": "trial-t2",
        "to_status": "failed", "reason": "test",
    })).content[0].text)
    assert "error" in result


# --- Unevidenced completion: write-time guard ---


def test_completed_requires_executor_record(store):
    """'completed' is a claim that the trial ran — the store refuses to
    write it without an executor record."""
    _seed(store)
    store.update_trial_status("trial-t1", "running")
    with pytest.raises(ValueError, match="executor record"):
        store.update_trial_status("trial-t1", "completed")
    assert store.get_trial("trial-t1").status.value == "running"


def test_completed_accepts_executor_record(store):
    _seed(store)
    store.update_trial_status("trial-t1", "running")
    store.update_trial_executor_output(
        "trial-t1", '{"status": "completed", "exit_code": 0}'
    )
    store.update_trial_status("trial-t1", "completed")
    assert store.get_trial("trial-t1").status.value == "completed"


def test_corrections_only_record_is_not_evidence(store):
    """Bookkeeping ('corrections') alone is not an executor record."""
    _seed(store)
    store.update_trial_status("trial-t1", "running")
    store.update_trial_executor_output(
        "trial-t1", '{"corrections": [{"from": "a", "to": "b"}]}'
    )
    with pytest.raises(ValueError, match="executor record"):
        store.update_trial_status("trial-t1", "completed")


def test_correct_to_completed_requires_record(store):
    _seed(store)
    store.update_trial_status("trial-t1", "running")
    store.update_trial_status("trial-t1", "failed")  # needs no record
    with pytest.raises(ValueError, match="executor output"):
        store.correct_trial_status("trial-t1", "completed", "reviewed")
    # Once a verdict-bearing record exists the correction is legal.
    store.update_trial_executor_output(
        "trial-t1", '{"status": "completed", "exit_code": 0}'
    )
    res = store.correct_trial_status("trial-t1", "completed", "reviewed")
    assert res["to"] == "completed"


def test_mislabeled_outcome_flags_unevidenced_completion(store):
    """A completed trial with no executor record — the legacy debt
    class — is flagged. Simulates a write that bypassed the guard."""
    _seed(store)
    store.update_trial_status("trial-t1", "running")
    store.conn.execute(
        "UPDATE trials SET status='completed' WHERE id='trial-t1'"
    )
    store.conn.commit()
    c = _check(run_checks(store), "mislabeled_outcome")
    assert not c["ok"]
    assert c["violations"][0]["trial_id"] == "trial-t1"
    assert "no executor record" in c["violations"][0]["signals"][0]


def test_mislabeled_outcome_observation_corroborates(store):
    """Completed + observation + no executor record is a provenance
    gap, not a mislabel — reported in detail, not flagged."""
    _seed(store)
    store.update_trial_status("trial-t1", "running")
    store.conn.execute(
        "UPDATE trials SET status='completed' WHERE id='trial-t1'"
    )
    store.conn.commit()
    store.create_observation(Observation(
        id="obs-t1", trial_id="trial-t1",
        metrics_json=json.dumps({"m": 1.0}),
        variance_json=json.dumps({"m": 0.01}),
        spatiotemporal_region="host:gpu-0@t",
    ))
    c = _check(run_checks(store), "mislabeled_outcome")
    assert c["ok"] and c["violations"] == []
    assert "provenance gap" in c["detail"]


def test_finalize_empty_output_marks_failed(store):
    """An executor that returns nothing recordable cannot produce a
    'completed' trial — finalize marks it failed instead."""
    from ml_episteme_mcp.tools.trial import _finalize_trial

    _seed(store)
    store.update_trial_status("trial-t1", "running")
    res = _finalize_trial(store, "trial-t1", "").structured_content
    assert res["status"] == "failed"
    assert store.get_trial("trial-t1").status.value == "failed"

    # Same for a payload with no execution verdict
    store.create_trial(_trial("prog-t1", "hyp-t1", "trial-t2"))
    store.update_trial_status("trial-t2", "running")
    res = _finalize_trial(store, "trial-t2", "{}").structured_content
    assert res["status"] == "failed"
