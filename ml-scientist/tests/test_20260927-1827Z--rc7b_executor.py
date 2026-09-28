"""rc-7b regressions — episteme executor lifecycle + integrity.

R1  A cancel landing inside create_subprocess_exec still kills the
    child — the spawn is shielded so the process handle is always
    bound, then killed deterministically; pgrep retry sweeps a fork
    that completes post-cancel.
R2  _finalize_trial's terminal early-return projects the full store
    row — a late executor result must not thin retry_reason /
    finished_at / started_at from get_trial_status's surface.
R3  terminal_with_live_executor — terminal rows with a live
    executor task, or whose recorded executor duration outlived the
    row's started→finished window (the leaked-subprocess residue).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from ml_episteme_mcp.clients.local_executor import LocalExecutor
from ml_episteme_mcp.integrity.checks import run_checks
from ml_episteme_mcp.state.models import (
    Hypothesis,
    Programme,
    ProgrammeStatus,
    Trial,
    TrialStatus,
)
from ml_episteme_mcp.state.store import StateStore
from ml_episteme_mcp.tools.trial import _finalize_trial


@pytest.fixture
def store(tmp_path):
    s = StateStore(str(tmp_path / "state.db"))
    s.connect()
    yield s
    s.close()


def _seed_trial(store, pid="prog-b", hid="hyp-b", tid="trial-b"):
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


def _check(payload, name):
    return next(c for c in payload["checks"] if c["name"] == name)


def _pids_for(pattern: str) -> list[int]:
    out = subprocess.run(
        ["pgrep", "-f", pattern], capture_output=True, text=True
    )
    return [int(p) for p in out.stdout.split() if p.strip().isdigit()]


# --- R1: cancel during the spawn boundary kills the child ------------


class TestCancelSpawnRace:
    """The VM observed mark_retryable at +14ms leaving a 120s
    subprocess running: the cancel landed inside
    create_subprocess_exec, before proc was bound, and the
    single-shot pgrep sweep raced the in-flight fork."""

    async def test_cancel_inside_spawn_window_kills_child(
        self, tmp_path, monkeypatch
    ):
        marker = tmp_path / "leaked.marker"
        real_spawn = asyncio.create_subprocess_exec

        async def slow_spawn(*args, **kwargs):
            await asyncio.sleep(0.3)  # hold the spawn window open
            return await real_spawn(*args, **kwargs)

        monkeypatch.setattr(
            asyncio, "create_subprocess_exec", slow_spawn
        )
        ex = LocalExecutor(timeout=30, sandbox="none", trace_reads="off")
        await ex.execute_code_async(
            "trial-race-rc7b",
            "import time\n"
            "time.sleep(2)\n"
            f"open({str(marker)!r}, 'w').write('x')\n",
            artifact_dir=tmp_path / "art",
        )
        # Cancel lands inside the 0.3s spawn window — before proc.
        await asyncio.sleep(0.05)
        reply = json.loads(await ex.cancel_async("trial-race-rc7b"))
        assert reply["status"] == "cancelled"

        # The child that completed post-cancel must not survive.
        assert not _pids_for("trial-race-rc7b")
        # And it must never run to completion.
        await asyncio.sleep(2.2)
        assert not marker.exists()
        assert "trial-race-rc7b" not in ex._running_tasks

    async def test_cancel_after_spawn_kills_process(self, tmp_path):
        marker = tmp_path / "done.marker"
        ex = LocalExecutor(timeout=30, sandbox="none", trace_reads="off")
        await ex.execute_code_async(
            "trial-cancel-rc7b",
            "import time\n"
            "time.sleep(2)\n"
            f"open({str(marker)!r}, 'w').write('x')\n",
            artifact_dir=tmp_path / "art",
        )
        await asyncio.sleep(0.4)  # past spawn, inside communicate()
        reply = json.loads(
            await ex.cancel_async("trial-cancel-rc7b")
        )
        assert reply["status"] == "cancelled"
        assert not _pids_for("trial-cancel-rc7b")
        await asyncio.sleep(2.0)
        assert not marker.exists()


# --- R2: late finalization returns the full terminal row -------------


class TestFinalizeTerminalProjection:
    """A retryable row finalized late must return every terminal
    field — the VM saw finished_at/retry_reason absent (not null)."""

    def test_terminal_early_return_is_full_projection(self, store):
        tid = _seed_trial(store)
        store.update_trial_status(tid, "running")
        store.update_trial_status(
            tid, "retryable", reason="superseded mid-run"
        )
        res = _finalize_trial(
            store,
            tid,
            json.dumps({
                "status": "completed", "exit_code": 0,
                "duration_seconds": 120.073,
            }),
        ).structured_content

        for key in (
            "trial_id", "status", "started_at", "finished_at",
            "duration_seconds", "artifact_path", "retry_reason",
            "executor_output",
        ):
            assert key in res, f"late finalize thinned {key!r}"
        # The store's terminal truth wins over the late result.
        assert res["status"] == "retryable"
        assert res["retry_reason"] == "superseded mid-run"
        assert res["finished_at"] is not None
        assert store.get_trial(tid).status.value == "retryable"
        # And the late output was persisted, not dropped.
        assert json.loads(res["executor_output"])["duration_seconds"] \
            == 120.073

    def test_repeat_finalize_stays_idempotent(self, store):
        tid = _seed_trial(store)
        store.update_trial_status(tid, "running")
        store.update_trial_status(
            tid, "retryable", reason="infra flake"
        )
        out = json.dumps({"status": "completed", "exit_code": 0})
        _finalize_trial(store, tid, out)
        res = _finalize_trial(store, tid, out).structured_content
        assert res["status"] == "retryable"
        assert res["retry_reason"] == "infra flake"


# --- R3: terminal row + live executor task / outlived residue --------


class _FakeTask:
    def __init__(self, done: bool):
        self._done = done

    def done(self):
        return self._done


class _FakeExecutor:
    def __init__(self, tasks: dict):
        self._running_tasks = tasks


class TestTerminalWithLiveExecutor:

    def test_terminal_row_with_live_task_flags(self, store):
        """Prong (a): a retryable row with a non-done task entry —
        cancel never fired or failed to kill."""
        tid = _seed_trial(store)
        store.update_trial_status(tid, "running")
        store.update_trial_status(
            tid, "retryable", reason="infra"
        )
        ex = _FakeExecutor({tid: _FakeTask(done=False)})
        c = _check(
            run_checks(store, executor=ex),
            "terminal_with_live_executor",
        )
        assert not c["ok"]
        assert any(
            v.get("trial_id") == tid for v in c["violations"]
        )

    def test_done_task_entry_not_a_violation(self, store):
        """_running_tasks entries persist after task completion —
        membership alone is not the signal."""
        tid = _seed_trial(store)
        store.update_trial_status(tid, "running")
        store.update_trial_executor_output(
            tid, json.dumps({
                "status": "failed", "exit_code": 1,
                "duration_seconds": 0.4,
            }),
        )
        store.update_trial_status(tid, "failed")
        ex = _FakeExecutor({tid: _FakeTask(done=True)})
        c = _check(
            run_checks(store, executor=ex),
            "terminal_with_live_executor",
        )
        assert c["ok"], c

    def test_executor_outlived_terminal_mark_flags(self, store):
        """Prong (b): the cancel path deletes the map entry after
        killing, so the leaked subprocess leaves NO live-task
        signature — the residue is a recorded executor duration far
        beyond the row's started→finished window (VM: 120.073s over
        ~0.014s)."""
        tid = _seed_trial(store)
        store.update_trial_status(tid, "running")
        store.update_trial_status(
            tid, "retryable", reason="superseded"
        )
        store.update_trial_executor_output(
            tid, json.dumps({
                "status": "completed", "exit_code": 0,
                "duration_seconds": 120.073,
            }),
        )
        t0 = datetime.now(timezone.utc) - timedelta(seconds=130)
        store.conn.execute(
            "UPDATE trials SET started_at = ?, finished_at = ? "
            "WHERE id = ?",
            (
                t0.isoformat(),
                (t0 + timedelta(milliseconds=14)).isoformat(),
                tid,
            ),
        )
        store.conn.commit()
        c = _check(run_checks(store), "terminal_with_live_executor")
        assert not c["ok"]
        assert any(
            v.get("trial_id") == tid for v in c["violations"]
        )

    def test_executor_within_window_clean(self, store):
        """A normal fast-fail: executor duration inside the row's
        window is not residue."""
        tid = _seed_trial(store)
        store.update_trial_status(tid, "running")
        store.update_trial_executor_output(
            tid, json.dumps({
                "status": "failed", "exit_code": 1,
                "duration_seconds": 0.4,
            }),
        )
        store.update_trial_status(tid, "failed")
        c = _check(run_checks(store), "terminal_with_live_executor")
        assert c["ok"], c
