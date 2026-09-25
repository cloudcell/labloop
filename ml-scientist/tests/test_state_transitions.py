"""Tests for state transition enforcement (b-01 §2.5).

Verifies that legal transitions succeed and illegal transitions are
rejected for trials, hypotheses, and programmes.
"""

import json
import tempfile
from pathlib import Path

import pytest

from ml_episteme_mcp.server import create_server
from ml_episteme_mcp.state.models import (
    Hypothesis,
    Programme,
    Trial,
)
from ml_episteme_mcp.state.store import StateStore


# --- Fixtures ---


@pytest.fixture
def store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    s = StateStore(path)
    s.connect()
    yield s
    s.close()
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def mcp(store):
    return create_server(store)


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
        # Schema-validation failures (bad Literal etc.) arrive as
        # is_error results with non-JSON text — surface as error.
        if result.is_error:
            return {"error": text}
        raise


async def call_tool_error(client, name: str, args: dict) -> dict:
    """Call a tool expected to fail; returns the error payload."""
    result = await client.call_tool(name, args)
    text = result.content[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"error": text}


def make_programme(store, pid="prog-t1"):
    store.create_programme(Programme(
        id=pid,
        goal="test",
        constraints={},
        allowed_variables=["lr"],
        budget_max_trials=10,
        budget_max_wall_time_hours=5.0,
        metric_direction="minimize",
    ))
    return pid


def make_hypothesis(store, pid, hid="hyp-t1"):
    store.create_hypothesis(Hypothesis(
        id=hid,
        programme_id=pid,
        statement="test",
        failure_criterion="val < 0.5",
        variables_involved=["lr"],
    ))
    return hid


def make_trial(store, pid, hid, tid="trial-t1"):
    store.create_trial(Trial(
        id=tid,
        programme_id=pid,
        hypothesis_id=hid,
        config_json='{"lr": 0.001}',
    ))
    return tid


# --- Trial transitions ---


class TestTrialTransitions:
    def test_designed_to_running(self, store):
        pid = make_programme(store)
        hid = make_hypothesis(store, pid)
        tid = make_trial(store, pid, hid)
        store.update_trial_status(tid, "running")
        assert store.get_trial(tid).status.value == "running"

    def test_running_to_completed(self, store):
        pid = make_programme(store)
        hid = make_hypothesis(store, pid)
        tid = make_trial(store, pid, hid)
        store.update_trial_status(tid, "running")
        store.update_trial_executor_output(tid, '{"status": "completed", "exit_code": 0}')
        store.update_trial_status(tid, "completed")
        assert store.get_trial(tid).status.value == "completed"

    def test_running_to_failed(self, store):
        pid = make_programme(store)
        hid = make_hypothesis(store, pid)
        tid = make_trial(store, pid, hid)
        store.update_trial_status(tid, "running")
        store.update_trial_status(tid, "failed")
        assert store.get_trial(tid).status.value == "failed"

    def test_running_to_retryable(self, store):
        pid = make_programme(store)
        hid = make_hypothesis(store, pid)
        tid = make_trial(store, pid, hid)
        store.update_trial_status(tid, "running")
        store.update_trial_status(tid, "retryable")
        assert store.get_trial(tid).status.value == "retryable"

    def test_retryable_is_terminal(self, store):
        pid = make_programme(store)
        hid = make_hypothesis(store, pid)
        tid = make_trial(store, pid, hid)
        store.update_trial_status(tid, "running")
        store.update_trial_status(tid, "retryable")
        with pytest.raises(ValueError, match="Illegal trial status transition"):
            store.update_trial_status(tid, "running")

    def test_completed_is_terminal(self, store):
        pid = make_programme(store)
        hid = make_hypothesis(store, pid)
        tid = make_trial(store, pid, hid)
        store.update_trial_status(tid, "running")
        store.update_trial_executor_output(tid, '{"status": "completed", "exit_code": 0}')
        store.update_trial_status(tid, "completed")
        with pytest.raises(ValueError, match="Illegal trial status transition"):
            store.update_trial_status(tid, "running")

    def test_failed_is_terminal(self, store):
        pid = make_programme(store)
        hid = make_hypothesis(store, pid)
        tid = make_trial(store, pid, hid)
        store.update_trial_status(tid, "running")
        store.update_trial_status(tid, "failed")
        with pytest.raises(ValueError, match="Illegal trial status transition"):
            store.update_trial_status(tid, "running")

    def test_designed_to_completed_illegal(self, store):
        pid = make_programme(store)
        hid = make_hypothesis(store, pid)
        tid = make_trial(store, pid, hid)
        with pytest.raises(ValueError, match="Illegal trial status transition"):
            store.update_trial_executor_output(tid, '{"status": "completed", "exit_code": 0}')
            store.update_trial_status(tid, "completed")

    def test_designed_to_failed_illegal(self, store):
        pid = make_programme(store)
        hid = make_hypothesis(store, pid)
        tid = make_trial(store, pid, hid)
        with pytest.raises(ValueError, match="Illegal trial status transition"):
            store.update_trial_status(tid, "failed")

    def test_nonexistent_trial(self, store):
        with pytest.raises(ValueError, match="Trial not found"):
            store.update_trial_status("trial-nope", "running")


# --- Hypothesis transitions ---


class TestHypothesisTransitions:
    def test_proposed_to_under_test(self, store):
        pid = make_programme(store)
        hid = make_hypothesis(store, pid)
        store.update_hypothesis_status(hid, "under_test")
        assert store.get_hypothesis(hid).status.value == "under_test"

    def test_under_test_to_accepted(self, store):
        pid = make_programme(store)
        hid = make_hypothesis(store, pid)
        store.update_hypothesis_status(hid, "under_test")
        store.update_hypothesis_status(hid, "accepted")
        assert store.get_hypothesis(hid).status.value == "accepted"

    def test_under_test_to_rejected(self, store):
        pid = make_programme(store)
        hid = make_hypothesis(store, pid)
        store.update_hypothesis_status(hid, "under_test")
        store.update_hypothesis_status(hid, "rejected")
        assert store.get_hypothesis(hid).status.value == "rejected"

    def test_under_test_to_inconclusive(self, store):
        pid = make_programme(store)
        hid = make_hypothesis(store, pid)
        store.update_hypothesis_status(hid, "under_test")
        store.update_hypothesis_status(hid, "inconclusive")
        assert store.get_hypothesis(hid).status.value == "inconclusive"

    def test_proposed_to_abandoned(self, store):
        pid = make_programme(store)
        hid = make_hypothesis(store, pid)
        store.update_hypothesis_status(hid, "abandoned")
        assert store.get_hypothesis(hid).status.value == "abandoned"

    def test_under_test_to_abandoned(self, store):
        pid = make_programme(store)
        hid = make_hypothesis(store, pid)
        store.update_hypothesis_status(hid, "under_test")
        store.update_hypothesis_status(hid, "abandoned")
        assert store.get_hypothesis(hid).status.value == "abandoned"

    def test_accepted_is_terminal(self, store):
        pid = make_programme(store)
        hid = make_hypothesis(store, pid)
        store.update_hypothesis_status(hid, "under_test")
        store.update_hypothesis_status(hid, "accepted")
        with pytest.raises(ValueError, match="Illegal hypothesis status transition"):
            store.update_hypothesis_status(hid, "proposed")

    def test_rejected_is_terminal(self, store):
        pid = make_programme(store)
        hid = make_hypothesis(store, pid)
        store.update_hypothesis_status(hid, "under_test")
        store.update_hypothesis_status(hid, "rejected")
        with pytest.raises(ValueError, match="Illegal hypothesis status transition"):
            store.update_hypothesis_status(hid, "under_test")

    def test_inconclusive_is_terminal(self, store):
        pid = make_programme(store)
        hid = make_hypothesis(store, pid)
        store.update_hypothesis_status(hid, "under_test")
        store.update_hypothesis_status(hid, "inconclusive")
        with pytest.raises(ValueError, match="Illegal hypothesis status transition"):
            store.update_hypothesis_status(hid, "proposed")

    def test_abandoned_is_terminal(self, store):
        pid = make_programme(store)
        hid = make_hypothesis(store, pid)
        store.update_hypothesis_status(hid, "abandoned")
        with pytest.raises(ValueError, match="Illegal hypothesis status transition"):
            store.update_hypothesis_status(hid, "proposed")

    def test_proposed_to_accepted_illegal(self, store):
        pid = make_programme(store)
        hid = make_hypothesis(store, pid)
        with pytest.raises(ValueError, match="Illegal hypothesis status transition"):
            store.update_hypothesis_status(hid, "accepted")

    def test_nonexistent_hypothesis(self, store):
        with pytest.raises(ValueError, match="Hypothesis not found"):
            store.update_hypothesis_status("hyp-nope", "under_test")


class TestPromoteHypothesisIfProposed:
    """Atomic proposed -> under_test used by design_experiment.

    Regression: two parallel design_experiment calls both read
    "proposed", both create a trial, then both tried the transition —
    the loser hit under_test -> under_test and failed AFTER its trial
    row existed. The promote must be idempotent for the loser and still
    fail closed on genuinely illegal states.
    """

    def test_proposed_promotes(self, store):
        pid = make_programme(store)
        hid = make_hypothesis(store, pid)
        store.promote_hypothesis_if_proposed(hid)
        assert store.get_hypothesis(hid).status.value == "under_test"

    def test_already_under_test_is_noop(self, store):
        pid = make_programme(store)
        hid = make_hypothesis(store, pid)
        store.promote_hypothesis_if_proposed(hid)
        store.promote_hypothesis_if_proposed(hid)  # second caller's race
        assert store.get_hypothesis(hid).status.value == "under_test"

    def test_terminal_status_still_fails_closed(self, store):
        pid = make_programme(store)
        hid = make_hypothesis(store, pid)
        store.update_hypothesis_status(hid, "under_test")
        store.update_hypothesis_status(hid, "accepted")
        with pytest.raises(ValueError, match="Illegal hypothesis transition"):
            store.promote_hypothesis_if_proposed(hid)
        assert store.get_hypothesis(hid).status.value == "accepted"

    def test_nonexistent_raises(self, store):
        with pytest.raises(ValueError, match="Hypothesis not found"):
            store.promote_hypothesis_if_proposed("hyp-nope")


# --- Programme transitions ---


class TestProgrammeTransitions:
    def test_active_to_completed(self, store):
        pid = make_programme(store)
        store.update_programme_status(pid, "completed")
        assert store.get_programme(pid).status.value == "completed"

    def test_active_to_abandoned(self, store):
        pid = make_programme(store)
        store.update_programme_status(pid, "abandoned")
        assert store.get_programme(pid).status.value == "abandoned"

    def test_completed_is_terminal(self, store):
        pid = make_programme(store)
        store.update_programme_status(pid, "completed")
        with pytest.raises(ValueError, match="Illegal programme status transition"):
            store.update_programme_status(pid, "active")

    def test_abandoned_is_terminal(self, store):
        pid = make_programme(store)
        store.update_programme_status(pid, "abandoned")
        with pytest.raises(ValueError, match="Illegal programme status transition"):
            store.update_programme_status(pid, "active")

    def test_nonexistent_programme(self, store):
        with pytest.raises(ValueError, match="Programme not found"):
            store.update_programme_status("prog-nope", "completed")


# --- design_experiment sets hypothesis to under_test ---


class TestDesignExperimentSetsUnderTest:
    @pytest.mark.asyncio
    async def test_design_sets_under_test(self, client, store):
        async with client:
            prog = await call_tool(client, "create_programme", {
                "goal": "test",
                "constraints": {},
                "allowed_variables": ["lr"],
                "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
            })
            pid = prog["programme_id"]
            hyp = await call_tool(client, "formulate_hypothesis", {
                "programme_id": pid,
                "statement": "test",
                "failure_criterion": "val < 0.5",
                "variables_involved": ["lr"],
            })
            hid = hyp["hypothesis_id"]
            assert store.get_hypothesis(hid).status.value == "proposed"

            await call_tool(client, "design_experiment", {
                "programme_id": pid,
                "hypothesis_id": hid,
                "config": {"lr": 0.001},
            })
            assert store.get_hypothesis(hid).status.value == "under_test"


# --- close_programme (the programme, not the hypothesis) ---


class TestCloseProgramme:
    @pytest.mark.asyncio
    async def test_close_programme_completed_auto_abandons_proposed(self, client, store):
        """close_programme(completed) auto-marks proposed hypotheses as abandoned."""
        async with client:
            prog = await call_tool(client, "create_programme", {
                "goal": "test",
                "constraints": {},
                "allowed_variables": ["lr"],
                "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
            })
            pid = prog["programme_id"]
            hyp = await call_tool(client, "formulate_hypothesis", {
                "programme_id": pid,
                "statement": "test",
                "failure_criterion": "val < 0.5",
                "variables_involved": ["lr"],
            })
            hid = hyp["hypothesis_id"]
            # Hypothesis is still proposed (no trial designed)
            assert store.get_hypothesis(hid).status.value == "proposed"
            # A trial row must exist — close(completed) rejects
            # zero-trial programmes. Created directly so the hypothesis
            # stays proposed (design_experiment would set under_test).
            make_trial(store, pid, hid)

            result = await call_tool(client, "close_programme", {
                "programme_id": pid,
                "status": "completed",
            })
            assert "error" not in result
            assert result["status"] == "completed"
            assert store.get_programme(pid).status.value == "completed"
            # Hypothesis auto-marked abandoned (no effort was expended)
            assert store.get_hypothesis(hid).status.value == "abandoned"
            assert len(result["auto_marked"]) == 1
            assert result["auto_marked"][0]["to"] == "abandoned"

    @pytest.mark.asyncio
    async def test_close_programme_completed_rejects_under_test(self, client, store):
        """close_programme(completed) rejects if any hypothesis is still under_test."""
        async with client:
            prog = await call_tool(client, "create_programme", {
                "goal": "test",
                "constraints": {},
                "allowed_variables": ["lr"],
                "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
            })
            pid = prog["programme_id"]
            hyp = await call_tool(client, "formulate_hypothesis", {
                "programme_id": pid,
                "statement": "test",
                "failure_criterion": "val < 0.5",
                "variables_involved": ["lr"],
            })
            hid = hyp["hypothesis_id"]
            # Design a trial (sets hypothesis to under_test)
            await call_tool(client, "design_experiment", {
                "programme_id": pid,
                "hypothesis_id": hid,
                "config": {"lr": 0.001},
            })
            assert store.get_hypothesis(hid).status.value == "under_test"

            result = await call_tool(client, "close_programme", {
                "programme_id": pid,
                "status": "completed",
            })
            assert "error" in result
            assert "conclude_hypothesis" in result["error"]
            # Hypothesis stays under_test (not auto-marked)
            assert store.get_hypothesis(hid).status.value == "under_test"

    @pytest.mark.asyncio
    async def test_close_programme_abandoned_marks_under_test_abandoned(self, client, store):
        """close_programme(abandoned) marks under_test hypotheses as abandoned."""
        async with client:
            prog = await call_tool(client, "create_programme", {
                "goal": "test",
                "constraints": {},
                "allowed_variables": ["lr"],
                "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
            })
            pid = prog["programme_id"]
            hyp = await call_tool(client, "formulate_hypothesis", {
                "programme_id": pid,
                "statement": "test",
                "failure_criterion": "val < 0.5",
                "variables_involved": ["lr"],
            })
            hid = hyp["hypothesis_id"]
            await call_tool(client, "design_experiment", {
                "programme_id": pid,
                "hypothesis_id": hid,
                "config": {"lr": 0.001},
            })
            assert store.get_hypothesis(hid).status.value == "under_test"

            result = await call_tool(client, "close_programme", {
                "programme_id": pid,
                "status": "abandoned",
            })
            assert "error" not in result
            # User abandoned — under_test hypotheses also abandoned
            assert store.get_hypothesis(hid).status.value == "abandoned"
            assert result["auto_marked"][0]["to"] == "abandoned"

    @pytest.mark.asyncio
    async def test_close_programme_rejects_already_closed(self, client, store):
        """close_programme rejects if programme is already closed."""
        async with client:
            prog = await call_tool(client, "create_programme", {
                "goal": "test",
                "constraints": {},
                "allowed_variables": ["lr"],
                "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
            })
            pid = prog["programme_id"]
            # A trial row must exist — close(completed) rejects
            # zero-trial programmes.
            hid2 = make_hypothesis(store, pid)
            make_trial(store, pid, hid2)
            await call_tool(client, "close_programme", {
                "programme_id": pid,
                "status": "completed",
            })
            result = await call_tool(client, "close_programme", {
                "programme_id": pid,
                "status": "completed",
            })
            assert "error" in result
            assert "Illegal programme status transition" in result["error"]

    @pytest.mark.asyncio
    async def test_close_programme_rejects_invalid_status(self, client, store):
        """close_programme rejects invalid status values."""
        async with client:
            prog = await call_tool(client, "create_programme", {
                "goal": "test",
                "constraints": {},
                "allowed_variables": ["lr"],
                "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
            })
            pid = prog["programme_id"]
            result = await call_tool(client, "close_programme", {
                "programme_id": pid,
                "status": "running",
            })
            assert "error" in result
            assert "completed" in result["error"] and "abandoned" in result["error"]

    @pytest.mark.asyncio
    async def test_close_programme_not_found(self, client, store):
        """close_programme rejects if programme doesn't exist."""
        async with client:
            result = await call_tool(client, "close_programme", {
                "programme_id": "prog-nope",
                "status": "completed",
            })
            assert "error" in result
            assert "Programme not found" in result["error"]
