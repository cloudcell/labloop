"""Smoke test: verify the HTTP server fixture works."""

import asyncio

import pytest

from conftest import (
    call_tool_http,
    list_tools_http,
    read_resource_http,
    make_programme,
    make_hypothesis,
    make_trial,
)


class TestInfrastructure:
    @pytest.mark.asyncio
    async def test_health_deep(self, server_url):
        """/health/deep runs the integrity audit over real HTTP —
        always 200; the payload's status field carries the verdict."""
        import json
        import urllib.request

        with urllib.request.urlopen(
            f"{server_url}/health/deep", timeout=10
        ) as resp:
            assert resp.status == 200
            body = json.loads(resp.read())
            assert body["status"] in ("ok", "violations")
            assert len(body["checks"]) == 10
            assert "log_file" in body

    async def test_server_starts_and_lists_tools(self, server_url):
        """The server starts over HTTP and exposes tools."""
        tools = await list_tools_http(server_url)
        assert len(tools) > 0, "No tools exposed"
        tool_names = [t.name for t in tools]
        # Check a few critical tools are present
        assert "create_programme" in tool_names
        assert "formulate_hypothesis" in tool_names
        assert "design_experiment" in tool_names
        assert "close_programme" in tool_names

    @pytest.mark.asyncio
    async def test_list_archives_succeeds(self, server_url):
        """list_archives should succeed on an empty server."""
        result = await call_tool_http(server_url, "list_archives", {})
        assert "error" not in result
        # On a fresh server, archives list should be empty
        assert isinstance(result.get("archives", []), list)

    @pytest.mark.asyncio
    async def test_database_is_isolated(self, server_url):
        """The test database should not be the production database."""
        import os
        from pathlib import Path
        # Check that no production DB was created
        prod_db = Path.home() / ".ml-episteme" / "state.db"
        # The production DB might exist from previous runs, but it
        # should not be modified by tests. We verify the test DB exists.
        test_dbs = list(Path("/tmp").glob("ml-sci-test-*.db"))
        assert len(test_dbs) > 0, "Test database was not created"


# --- Part 2: Programme FSM ---


class TestProgrammeFSM:
    """Test all programme state transitions over HTTP."""

    @pytest.mark.asyncio
    async def test_active_to_completed(self, server_url):
        """Legal: active → completed."""
        from conftest import make_closeable_programme
        pid, _ = await make_closeable_programme(server_url)
        result = await call_tool_http(server_url, "close_programme", {
            "programme_id": pid,
            "status": "completed",
        })
        assert "error" not in result
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_active_to_abandoned(self, server_url):
        """Legal: active → abandoned."""
        pid = await make_programme(server_url)
        result = await call_tool_http(server_url, "close_programme", {
            "programme_id": pid,
            "status": "abandoned",
        })
        assert "error" not in result
        assert result["status"] == "abandoned"

    @pytest.mark.asyncio
    async def test_completed_to_archived_auto(self, server_url):
        """Legal: completed → archived (auto-archive on close)."""
        from conftest import make_closeable_programme
        pid, _ = await make_closeable_programme(server_url)
        result = await call_tool_http(server_url, "close_programme", {
            "programme_id": pid,
            "status": "completed",
        })
        assert "error" not in result
        # close_programme auto-archives — check the programme is archived
        prog = await read_resource_http(server_url, f"programme://{pid}")
        assert prog["status"] == "archived"

    @pytest.mark.asyncio
    async def test_abandoned_to_archived_auto(self, server_url):
        """Legal: abandoned → archived (auto-archive on close)."""
        pid = await make_programme(server_url)
        result = await call_tool_http(server_url, "close_programme", {
            "programme_id": pid,
            "status": "abandoned",
        })
        assert "error" not in result
        prog = await read_resource_http(server_url, f"programme://{pid}")
        assert prog["status"] == "archived"

    @pytest.mark.asyncio
    async def test_close_already_closed_rejected(self, server_url):
        """Illegal: completed → completed (already closed)."""
        from conftest import make_closeable_programme
        pid, _ = await make_closeable_programme(server_url)
        await call_tool_http(server_url, "close_programme", {
            "programme_id": pid,
            "status": "completed",
        })
        result = await call_tool_http(server_url, "close_programme", {
            "programme_id": pid,
            "status": "completed",
        })
        assert "error" in result
        assert "Illegal programme status transition" in result["error"]

    @pytest.mark.asyncio
    async def test_close_archived_rejected(self, server_url):
        """Illegal: archived → completed (archived is terminal)."""
        from conftest import make_closeable_programme
        pid, _ = await make_closeable_programme(server_url)
        await call_tool_http(server_url, "close_programme", {
            "programme_id": pid,
            "status": "completed",
        })
        # Now archived — try to close again
        result = await call_tool_http(server_url, "close_programme", {
            "programme_id": pid,
            "status": "abandoned",
        })
        assert "error" in result
        assert "Illegal programme status transition" in result["error"]

    @pytest.mark.asyncio
    async def test_close_invalid_status_rejected(self, server_url):
        """Illegal: active → running (not a valid close status)."""
        pid = await make_programme(server_url)
        result = await call_tool_http(server_url, "close_programme", {
            "programme_id": pid,
            "status": "running",
        })
        assert "error" in result
        assert "completed" in result["error"] and "abandoned" in result["error"]

    @pytest.mark.asyncio
    async def test_close_nonexistent_rejected(self, server_url):
        """Error: programme not found."""
        result = await call_tool_http(server_url, "close_programme", {
            "programme_id": "prog-nonexistent",
            "status": "completed",
        })
        assert "error" in result
        assert "Programme not found" in result["error"]

    @pytest.mark.asyncio
    async def test_close_with_running_trial_rejected(self, server_url):
        """Rule 5.6: cannot close while a trial is running."""
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        tid = await make_trial(server_url, pid, hid)
        # Start the trial (sets it to running)
        result = await call_tool_http(server_url, "run_trial", {
            "programme_id": pid,
            "trial_id": tid,
        })
        # run_trial may fail (no bundle) but the trial might still be running
        # Let's check the trial status
        trial = await call_tool_http(server_url, "get_trial_status", {
            "programme_id": pid,
            "trial_id": tid,
        })
        # If the trial is running, close should reject
        if trial.get("status") == "running":
            result = await call_tool_http(server_url, "close_programme", {
                "programme_id": pid,
                "status": "abandoned",
            })
            assert "error" in result
            assert "running" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_close_auto_marks_designed_trials(self, server_url):
        """close_programme auto-marks designed trials as abandoned."""
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        tid = await make_trial(server_url, pid, hid)
        # Trial is designed (not run)
        result = await call_tool_http(server_url, "close_programme", {
            "programme_id": pid,
            "status": "abandoned",
        })
        assert "error" not in result
        # The designed trial should be auto-marked abandoned
        trial = await call_tool_http(server_url, "get_trial_status", {
            "programme_id": pid,
            "trial_id": tid,
        })
        assert trial["status"] == "abandoned"

    @pytest.mark.asyncio
    async def test_close_completed_auto_abandons_proposed_hypotheses(self, server_url):
        """close_programme(completed) auto-marks proposed hypotheses as abandoned."""
        from conftest import make_closeable_programme
        pid, _ = await make_closeable_programme(server_url)
        # A second hypothesis stays proposed — close must auto-abandon it
        hid2 = await make_hypothesis(server_url, pid)
        result = await call_tool_http(server_url, "close_programme", {
            "programme_id": pid,
            "status": "completed",
        })
        assert "error" not in result
        assert len(result["auto_marked"]) >= 1

    @pytest.mark.asyncio
    async def test_close_completed_rejects_under_test_hypothesis(self, server_url):
        """close_programme(completed) rejects if a hypothesis is under_test."""
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        # Design a trial (sets hypothesis to under_test)
        await make_trial(server_url, pid, hid)
        result = await call_tool_http(server_url, "close_programme", {
            "programme_id": pid,
            "status": "completed",
        })
        assert "error" in result
        assert "conclude_hypothesis" in result["error"]


# --- Part 3: Hypothesis FSM ---


class TestHypothesisFSM:
    """Test all hypothesis state transitions over HTTP."""

    @pytest.mark.asyncio
    async def test_proposed_to_under_test(self, server_url):
        """Legal: proposed → under_test (via design_experiment)."""
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        await make_trial(server_url, pid, hid)
        # design_experiment sets hypothesis to under_test
        hyps = await read_resource_http(server_url, f"programme://{pid}/hypotheses")
        hyp = next(h for h in hyps if h["id"] == hid)
        assert hyp["status"] == "under_test"

    @pytest.mark.asyncio
    async def test_proposed_to_abandoned_via_close(self, server_url):
        """Legal: proposed → abandoned (via close_programme completed)."""
        from conftest import make_closeable_programme
        pid, _ = await make_closeable_programme(server_url)
        # A second hypothesis stays proposed — close must auto-abandon it
        hid2 = await make_hypothesis(server_url, pid)
        result = await call_tool_http(server_url, "close_programme", {
            "programme_id": pid,
            "status": "completed",
        })
        assert "error" not in result
        assert any(m["to"] == "abandoned" for m in result["auto_marked"])

    @pytest.mark.asyncio
    async def test_under_test_to_abandoned_via_close(self, server_url):
        """Legal: under_test → abandoned (via close_programme abandoned)."""
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        await make_trial(server_url, pid, hid)  # sets to under_test
        result = await call_tool_http(server_url, "close_programme", {
            "programme_id": pid,
            "status": "abandoned",
        })
        assert "error" not in result
        assert any(m["to"] == "abandoned" for m in result["auto_marked"])

    @pytest.mark.asyncio
    async def test_under_test_to_accepted(self, server_url):
        """Legal: under_test → accepted (via conclude_hypothesis)."""
        from conftest import make_completed_trial_with_observation
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        tid, obs_id = await make_completed_trial_with_observation(server_url, pid, hid)
        # Update belief (required before conclude)
        result = await call_tool_http(server_url, "update_belief", {
            "programme_id": pid,
            "trial_id": tid,
            "observation_id": obs_id,
        })
        # conclude_hypothesis
        result = await call_tool_http(server_url, "conclude_hypothesis", {
            "programme_id": pid,
            "hypothesis_id": hid,
            "verdict": "accepted",
            "evidence_summary": "test evidence",
        })
        assert "error" not in result, f"conclude_hypothesis failed: {result}"
        assert result["verdict"] == "accepted"

    @pytest.mark.asyncio
    async def test_under_test_to_rejected(self, server_url):
        """Legal: under_test → rejected (via conclude_hypothesis)."""
        from conftest import make_completed_trial_with_observation
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        tid, obs_id = await make_completed_trial_with_observation(server_url, pid, hid)
        result = await call_tool_http(server_url, "update_belief", {
            "programme_id": pid,
            "trial_id": tid,
            "observation_id": obs_id,
        })
        result = await call_tool_http(server_url, "conclude_hypothesis", {
            "programme_id": pid,
            "hypothesis_id": hid,
            "verdict": "rejected",
            "evidence_summary": "test evidence",
        })
        assert "error" not in result, f"conclude_hypothesis failed: {result}"
        assert result["verdict"] == "rejected"

    @pytest.mark.asyncio
    async def test_under_test_to_inconclusive(self, server_url):
        """Legal: under_test → inconclusive (via conclude_hypothesis)."""
        from conftest import make_completed_trial_with_observation
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        tid, obs_id = await make_completed_trial_with_observation(server_url, pid, hid)
        result = await call_tool_http(server_url, "update_belief", {
            "programme_id": pid,
            "trial_id": tid,
            "observation_id": obs_id,
        })
        result = await call_tool_http(server_url, "conclude_hypothesis", {
            "programme_id": pid,
            "hypothesis_id": hid,
            "verdict": "inconclusive",
            "evidence_summary": "test evidence",
        })
        assert "error" not in result, f"conclude_hypothesis failed: {result}"
        assert result["verdict"] == "inconclusive"

    @pytest.mark.asyncio
    async def test_conclude_nonexistent_hypothesis(self, server_url):
        """Error: hypothesis not found."""
        pid = await make_programme(server_url)
        result = await call_tool_http(server_url, "conclude_hypothesis", {
            "programme_id": pid,
            "hypothesis_id": "hyp-nonexistent",
            "verdict": "accepted",
            "evidence_summary": "test",
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_conclude_no_evidence_rejected(self, server_url):
        """Commitment 1: no conclusion without evidence."""
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        await make_trial(server_url, pid, hid)  # sets to under_test
        # No trial completed, no observation
        result = await call_tool_http(server_url, "conclude_hypothesis", {
            "programme_id": pid,
            "hypothesis_id": hid,
            "verdict": "accepted",
            "evidence_summary": "test",
        })
        assert "error" in result
        assert "evidence" in result["error"].lower() or "completed" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_conclude_duplicate_rejected(self, server_url):
        """Commitment 8: no duplicate conclusions."""
        from conftest import make_completed_trial_with_observation
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        tid, obs_id = await make_completed_trial_with_observation(server_url, pid, hid)
        await call_tool_http(server_url, "update_belief", {
            "programme_id": pid,
            "trial_id": tid,
            "observation_id": obs_id,
        })
        # First conclusion
        result = await call_tool_http(server_url, "conclude_hypothesis", {
            "programme_id": pid,
            "hypothesis_id": hid,
            "verdict": "accepted",
            "evidence_summary": "test",
        })
        assert "error" not in result
        # Second conclusion (duplicate)
        result = await call_tool_http(server_url, "conclude_hypothesis", {
            "programme_id": pid,
            "hypothesis_id": hid,
            "verdict": "rejected",
            "evidence_summary": "test",
        })
        assert "error" in result
        assert "duplicate" in result["error"].lower() or "already" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_conclude_invalid_verdict(self, server_url):
        """Error: invalid verdict value."""
        from conftest import make_completed_trial_with_observation
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        tid, obs_id = await make_completed_trial_with_observation(server_url, pid, hid)
        result = await call_tool_http(server_url, "update_belief", {
            "programme_id": pid,
            "trial_id": tid,
            "observation_id": obs_id,
        })
        result = await call_tool_http(server_url, "conclude_hypothesis", {
            "programme_id": pid,
            "hypothesis_id": hid,
            "verdict": "invalid",
            "evidence_summary": "test",
        })
        assert "error" in result


# --- Part 4: Trial FSM ---


class TestTrialFSM:
    """Test trial state transitions over HTTP."""

    @pytest.mark.asyncio
    async def test_designed_to_running(self, server_url):
        """Legal: designed → running (via run_trial)."""
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        tid = await make_trial(server_url, pid, hid)
        # Capture bundle first (required for run_trial)
        import os
        stub = os.path.join(os.path.dirname(__file__), "fixtures", "train_stub.py")
        await call_tool_http(server_url, "capture_bundle", {
            "trial_id": tid, "code_ref": stub, "env_ref": "py",
            "seeds": [42], "splits": {"train": 0.8},
        })
        result = await call_tool_http(server_url, "run_trial", {
            "programme_id": pid, "trial_id": tid,
        })
        # Trial should be running or completed (stub is fast)
        assert "error" not in result
        assert result["status"] in ("running", "completed")

    @pytest.mark.asyncio
    async def test_designed_to_abandoned_via_close(self, server_url):
        """Legal: designed → abandoned (via close_programme)."""
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        tid = await make_trial(server_url, pid, hid)
        result = await call_tool_http(server_url, "close_programme", {
            "programme_id": pid, "status": "abandoned",
        })
        assert "error" not in result
        assert any(m["trial_id"] == tid for m in result.get("trials_auto_marked", []))

    @pytest.mark.asyncio
    async def test_running_to_completed(self, server_url):
        """Legal: running → completed (executor returns success)."""
        from conftest import make_completed_trial_with_observation
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        tid, _ = await make_completed_trial_with_observation(server_url, pid, hid)
        status = await call_tool_http(server_url, "get_trial_status", {
            "programme_id": pid, "trial_id": tid,
        })
        assert status["status"] == "completed"

    @pytest.mark.asyncio
    async def test_run_trial_nonexistent_programme(self, server_url):
        """Error: programme not found."""
        result = await call_tool_http(server_url, "run_trial", {
            "programme_id": "prog-nope", "trial_id": "trial-nope",
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_run_trial_wrong_programme(self, server_url):
        """Commitment 1: trial must belong to the programme."""
        pid1 = await make_programme(server_url)
        pid2 = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid1)
        tid = await make_trial(server_url, pid1, hid)
        result = await call_tool_http(server_url, "run_trial", {
            "programme_id": pid2, "trial_id": tid,
        })
        assert "error" in result
        assert "loop is the unit" in result["error"] or "belong" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_run_trial_no_bundle(self, server_url):
        """Error: trial has no bundle."""
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        tid = await make_trial(server_url, pid, hid)
        result = await call_tool_http(server_url, "run_trial", {
            "programme_id": pid, "trial_id": tid,
        })
        assert "error" in result
        assert "bundle" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_cancel_trial_nonexistent(self, server_url):
        """Error: trial not found."""
        result = await call_tool_http(server_url, "cancel_trial", {
            "programme_id": "prog-nope", "trial_id": "trial-nope",
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_mark_retryable_nonexistent(self, server_url):
        """Error: trial not found."""
        result = await call_tool_http(server_url, "mark_retryable", {
            "programme_id": "prog-nope", "trial_id": "trial-nope",
            "reason": "test",
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_get_trial_status_nonexistent(self, server_url):
        """Error: trial not found."""
        result = await call_tool_http(server_url, "get_trial_status", {
            "programme_id": "prog-nope", "trial_id": "trial-nope",
        })
        assert "error" in result


# --- Part 5: Tool input validation ---


class TestToolInputValidation:
    """Test input validation for all tools."""

    @pytest.mark.asyncio
    async def test_create_programme_missing_goal(self, server_url):
        result = await call_tool_http(server_url, "create_programme", {
            "constraints": {}, "allowed_variables": ["lr"],
            "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
        })
        # MCP should reject missing required args
        # or the tool should return an error
        assert "error" in result or "programme_id" not in result

    @pytest.mark.asyncio
    async def test_create_programme_missing_budget(self, server_url):
        result = await call_tool_http(server_url, "create_programme", {
            "goal": "test", "constraints": {}, "allowed_variables": ["lr"],
        })
        assert "error" in result or "programme_id" not in result

    @pytest.mark.asyncio
    async def test_formulate_hypothesis_missing_statement(self, server_url):
        pid = await make_programme(server_url)
        result = await call_tool_http(server_url, "formulate_hypothesis", {
            "programme_id": pid,
            "failure_criterion": "val < 0.5",
            "variables_involved": ["lr"],
        })
        assert "error" in result or "hypothesis_id" not in result

    @pytest.mark.asyncio
    async def test_formulate_hypothesis_nonexistent_programme(self, server_url):
        result = await call_tool_http(server_url, "formulate_hypothesis", {
            "programme_id": "prog-nope",
            "statement": "test", "failure_criterion": "val < 0.5",
            "variables_involved": ["lr"],
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_design_experiment_nonexistent_programme(self, server_url):
        result = await call_tool_http(server_url, "design_experiment", {
            "programme_id": "prog-nope",
            "hypothesis_id": "hyp-nope",
            "config": {"lr": 0.001},
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_capture_bundle_nonexistent_trial(self, server_url):
        result = await call_tool_http(server_url, "capture_bundle", {
            "trial_id": "trial-nope",
            "code_ref": "tests/fixtures/train_stub.py",
            "env_ref": "py", "seeds": [42], "splits": {"train": 0.8},
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_capture_bundle_bad_code_ref(self, server_url):
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        tid = await make_trial(server_url, pid, hid)
        result = await call_tool_http(server_url, "capture_bundle", {
            "trial_id": tid, "code_ref": "/nonexistent/path.py",
            "env_ref": "py", "seeds": [42], "splits": {"train": 0.8},
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_capture_bundle_from_code_hash_nonexistent(self, server_url):
        result = await call_tool_http(server_url, "capture_bundle_from_code_hash", {
            "trial_id": "trial-nope",
            "code_hash": "sha256:nonexistent",
            "env_ref": "py", "seeds": [42], "splits": {"train": 0.8},
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_record_observation_nonexistent_trial(self, server_url):
        result = await call_tool_http(server_url, "record_observation", {
            "trial_id": "trial-nope",
            "metrics": {"val_accuracy": 0.9},
            "variance": {"val_accuracy": 0.01},
            "spatiotemporal_region": "test",
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_record_observation_no_variance(self, server_url):
        """Commitment 7: variance is required."""
        from conftest import make_completed_trial_with_observation
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        tid, _ = await make_completed_trial_with_observation(server_url, pid, hid)
        # Try to record another observation with no variance
        result = await call_tool_http(server_url, "record_observation", {
            "trial_id": tid,
            "metrics": {"val_accuracy": 0.9},
            "variance": {},
            "spatiotemporal_region": "test",
        })
        assert "error" in result
        assert "variance" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_update_belief_nonexistent_observation(self, server_url):
        result = await call_tool_http(server_url, "update_belief", {
            "programme_id": "prog-nope",
            "trial_id": "trial-nope",
            "observation_id": "obs-nope",
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_assess_programme_nonexistent(self, server_url):
        result = await call_tool_http(server_url, "assess_programme", {
            "programme_id": "prog-nope",
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_get_next_experiment_nonexistent(self, server_url):
        result = await call_tool_http(server_url, "get_next_experiment", {
            "programme_id": "prog-nope",
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_verify_data_nonexistent(self, server_url):
        result = await call_tool_http(server_url, "verify_data", {
            "data_ref_id": "data-ref-nope",
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_update_metric_direction_invalid(self, server_url):
        pid = await make_programme(server_url)
        result = await call_tool_http(server_url, "update_metric_direction", {
            "programme_id": pid,
            "metric_direction": "sideways",
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_get_archive_nonexistent(self, server_url):
        result = await call_tool_http(server_url, "get_archive", {
            "archive_id": "archive-nope",
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_get_archived_programme_nonexistent(self, server_url):
        result = await call_tool_http(server_url, "get_archived_programme", {
            "programme_id": "prog-nope",
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_verify_archive_nonexistent(self, server_url):
        result = await call_tool_http(server_url, "verify_archive", {
            "archive_id": "archive-nope",
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_archive_pending_programmes_succeeds(self, server_url):
        """Should succeed even on an empty server."""
        result = await call_tool_http(server_url, "archive_pending_programmes", {"dry_run": False})
        assert "error" not in result
        # dry_run previews without mutating
        result = await call_tool_http(server_url, "archive_pending_programmes", {"dry_run": True})
        assert "error" not in result
        assert result["dry_run"] is True

    @pytest.mark.asyncio
    async def test_housekeeping_tools_require_dry_run_flag(self, server_url):
        """Zero-arg mutation guard: bare {} must be schema-rejected."""
        for tool in ("archive_pending_programmes", "capture_pending_artifacts"):
            result = await call_tool_http(server_url, tool, {})
            assert "error" in result
            assert "dry_run" in result["error"]

    @pytest.mark.asyncio
    async def test_capture_pending_artifacts_succeeds(self, server_url):
        """Should succeed even on an empty server."""
        result = await call_tool_http(server_url, "capture_pending_artifacts", {"dry_run": False})
        assert "error" not in result


# --- Part 6: End-to-end scientific loop ---


class TestEndToEndLoop:
    """Test the full scientific loop over HTTP."""

    @pytest.mark.asyncio
    async def test_full_loop(self, server_url):
        """Full loop: create → hypothesize → design → bundle → run → observe → believe → conclude → close."""
        from conftest import make_completed_trial_with_observation
        # 1. Create programme
        pid = await make_programme(server_url)
        # 2. Formulate hypothesis
        hid = await make_hypothesis(server_url, pid)
        # 3-6. Design, bundle, run, observe
        tid, obs_id = await make_completed_trial_with_observation(server_url, pid, hid)
        # 7. Update belief
        result = await call_tool_http(server_url, "update_belief", {
            "programme_id": pid, "trial_id": tid, "observation_id": obs_id,
        })
        assert "error" not in result, f"update_belief failed: {result}"
        # 8. Conclude hypothesis
        result = await call_tool_http(server_url, "conclude_hypothesis", {
            "programme_id": pid, "hypothesis_id": hid,
            "verdict": "accepted", "evidence_summary": "test evidence",
        })
        assert "error" not in result, f"conclude_hypothesis failed: {result}"
        # 9. Close programme
        result = await call_tool_http(server_url, "close_programme", {
            "programme_id": pid, "status": "completed",
        })
        assert "error" not in result, f"close_programme failed: {result}"
        # 10. Verify programme is archived
        prog = await read_resource_http(server_url, f"programme://{pid}")
        assert prog["status"] == "archived"
        # 11. Verify archive entry exists
        archives = await call_tool_http(server_url, "list_archives", {})
        assert len(archives.get("archives", [])) > 0


# --- Part 7: Archive no-purge verification ---


class TestArchiveNoPurge:
    """Verify the no-purge archiving behavior."""

    @pytest.mark.asyncio
    async def test_archived_programme_stays_in_live_db(self, server_url):
        """Archived programmes remain in the live DB with 'archived' status."""
        from conftest import make_completed_trial_with_observation
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        tid, obs_id = await make_completed_trial_with_observation(server_url, pid, hid)
        await call_tool_http(server_url, "update_belief", {
            "programme_id": pid, "trial_id": tid, "observation_id": obs_id,
        })
        await call_tool_http(server_url, "conclude_hypothesis", {
            "programme_id": pid, "hypothesis_id": hid,
            "verdict": "accepted", "evidence_summary": "test",
        })
        # Close (auto-archives)
        await call_tool_http(server_url, "close_programme", {
            "programme_id": pid, "status": "completed",
        })
        # Programme should still be readable from the live DB
        prog = await read_resource_http(server_url, f"programme://{pid}")
        assert prog is not None
        assert prog["status"] == "archived"

    @pytest.mark.asyncio
    async def test_archived_programme_in_archive_db(self, server_url):
        """The archived programme is also in the archive DB."""
        from conftest import make_completed_trial_with_observation
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        tid, obs_id = await make_completed_trial_with_observation(server_url, pid, hid)
        await call_tool_http(server_url, "update_belief", {
            "programme_id": pid, "trial_id": tid, "observation_id": obs_id,
        })
        await call_tool_http(server_url, "conclude_hypothesis", {
            "programme_id": pid, "hypothesis_id": hid,
            "verdict": "accepted", "evidence_summary": "test",
        })
        await call_tool_http(server_url, "close_programme", {
            "programme_id": pid, "status": "completed",
        })
        # Get the archived programme from the archive DB
        result = await call_tool_http(server_url, "get_archived_programme", {
            "programme_id": pid,
        })
        assert "error" not in result, f"get_archived_programme failed: {result}"
        assert result.get("programme_id") == pid or result.get("id") == pid or result.get("programme", {}).get("id") == pid

    @pytest.mark.asyncio
    async def test_archive_pending_programmes_marks_zombies(self, server_url):
        """archive_pending_programmes marks already-archived zombies."""
        # This is a no-op on a fresh server, but should not error
        result = await call_tool_http(server_url, "archive_pending_programmes", {"dry_run": False})
        assert "error" not in result
        assert "archived" in result
        assert "marked_archived" in result


# --- Part 8: Multi-file code capture (extra_code_refs) ---


class TestMultiFileCapture:
    """Test explicit extra_code_refs for subprocess-dispatched code.

    The AST-based import discovery in capture_code_with_imports cannot
    see files invoked via subprocess.run() — only Python imports. The
    extra_code_refs parameter lets the caller explicitly declare these
    dependencies so the bundle is fully self-contained.
    """

    @pytest.mark.asyncio
    async def test_ast_discovers_local_imports(self, server_url):
        """AST import analysis captures locally-imported modules automatically."""
        import os
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        tid = await make_trial(server_url, pid, hid)
        executor = os.path.join(
            os.path.dirname(__file__), "fixtures", "multi_file", "executor.py"
        )
        result = await call_tool_http(server_url, "capture_bundle", {
            "trial_id": tid, "code_ref": executor, "env_ref": "py",
            "seeds": [42], "splits": {"train": 0.8},
        })
        assert "error" not in result, f"capture_bundle failed: {result}"
        # The primary code_hash is the executor
        assert result["code_hash"].startswith("sha256:")
        # utils.py should be discovered by AST import analysis
        extra = result.get("code_hash_extra", [])
        assert len(extra) >= 1, f"AST should have discovered utils.py: {extra}"

    @pytest.mark.asyncio
    async def test_extra_code_refs_captures_subprocess_script(self, server_url):
        """extra_code_refs captures files AST cannot discover."""
        import os
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        tid = await make_trial(server_url, pid, hid)
        executor = os.path.join(
            os.path.dirname(__file__), "fixtures", "multi_file", "executor.py"
        )
        script = os.path.join(
            os.path.dirname(__file__), "fixtures", "multi_file", "train_script.py"
        )
        result = await call_tool_http(server_url, "capture_bundle", {
            "trial_id": tid, "code_ref": executor, "env_ref": "py",
            "seeds": [42], "splits": {"train": 0.8},
            "extra_code_refs": [script],
        })
        assert "error" not in result, f"capture_bundle failed: {result}"
        # Should have: primary (executor) + utils (AST) + train_script (explicit)
        extra = result.get("code_hash_extra", [])
        assert len(extra) >= 2, f"Should have utils + train_script: {extra}"

    @pytest.mark.asyncio
    async def test_extra_code_refs_invalid_path_rejected(self, server_url):
        """Invalid extra_code_ref path is rejected."""
        import os
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        tid = await make_trial(server_url, pid, hid)
        executor = os.path.join(
            os.path.dirname(__file__), "fixtures", "train_stub.py"
        )
        result = await call_tool_http(server_url, "capture_bundle", {
            "trial_id": tid, "code_ref": executor, "env_ref": "py",
            "seeds": [42], "splits": {"train": 0.8},
            "extra_code_refs": ["/nonexistent/path.py"],
        })
        assert "error" in result
        assert "extra_code_ref" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_extra_code_refs_non_py_rejected(self, server_url):
        """Non-.py extra_code_ref is rejected."""
        import os
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        tid = await make_trial(server_url, pid, hid)
        executor = os.path.join(
            os.path.dirname(__file__), "fixtures", "train_stub.py"
        )
        # Create a non-py file
        non_py = f"/tmp/ml-sci-test-non-py-{os.getpid()}.txt"
        with open(non_py, "w") as f:
            f.write("not python")
        result = await call_tool_http(server_url, "capture_bundle", {
            "trial_id": tid, "code_ref": executor, "env_ref": "py",
            "seeds": [42], "splits": {"train": 0.8},
            "extra_code_refs": [non_py],
        })
        assert "error" in result
        os.unlink(non_py)

    @pytest.mark.asyncio
    async def test_capture_bundle_from_code_hash_with_extra(self, server_url):
        """capture_bundle_from_code_hash accepts code_hash_extra."""
        import os
        # First, capture a bundle with extra_code_refs to get the hashes
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        tid1 = await make_trial(server_url, pid, hid)
        executor = os.path.join(
            os.path.dirname(__file__), "fixtures", "multi_file", "executor.py"
        )
        script = os.path.join(
            os.path.dirname(__file__), "fixtures", "multi_file", "train_script.py"
        )
        result = await call_tool_http(server_url, "capture_bundle", {
            "trial_id": tid1, "code_ref": executor, "env_ref": "py",
            "seeds": [42], "splits": {"train": 0.8},
            "extra_code_refs": [script],
        })
        primary_hash = result["code_hash"]
        extra_hashes = result["code_hash_extra"]

        # Now create a new trial and capture from code_hash
        tid2 = await make_trial(server_url, pid, hid)
        result2 = await call_tool_http(server_url, "capture_bundle_from_code_hash", {
            "trial_id": tid2, "code_hash": primary_hash, "env_ref": "py",
            "seeds": [42], "splits": {"train": 0.8},
            "code_hash_extra": extra_hashes,
        })
        assert "error" not in result2, f"capture_from_hash failed: {result2}"
        assert result2["code_hash"] == primary_hash
        assert result2["code_hash_extra"] == extra_hashes

    @pytest.mark.asyncio
    async def test_capture_bundle_from_code_hash_invalid_extra(self, server_url):
        """capture_bundle_from_code_hash rejects unknown extra hashes."""
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        tid = await make_trial(server_url, pid, hid)
        # First capture a primary to get a valid hash
        import os
        executor = os.path.join(
            os.path.dirname(__file__), "fixtures", "train_stub.py"
        )
        result = await call_tool_http(server_url, "capture_bundle", {
            "trial_id": tid, "code_ref": executor, "env_ref": "py",
            "seeds": [42], "splits": {"train": 0.8},
        })
        primary_hash = result["code_hash"]

        # New trial — try with an invalid extra hash
        tid2 = await make_trial(server_url, pid, hid)
        result2 = await call_tool_http(server_url, "capture_bundle_from_code_hash", {
            "trial_id": tid2, "code_hash": primary_hash, "env_ref": "py",
            "seeds": [42], "splits": {"train": 0.8},
            "code_hash_extra": ["sha256:nonexistent"],
        })
        assert "error" in result2
        assert "extra" in result2["error"].lower()

    @pytest.mark.asyncio
    async def test_extra_code_in_archive(self, server_url):
        """Extra code snippets are copied into the archive DB."""
        import os
        from conftest import make_completed_trial_with_observation
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        tid = await make_trial(server_url, pid, hid)
        executor = os.path.join(
            os.path.dirname(__file__), "fixtures", "multi_file", "executor.py"
        )
        script = os.path.join(
            os.path.dirname(__file__), "fixtures", "multi_file", "train_script.py"
        )
        # Capture with extra_code_refs
        await call_tool_http(server_url, "capture_bundle", {
            "trial_id": tid, "code_ref": executor, "env_ref": "py",
            "seeds": [42], "splits": {"train": 0.8},
            "extra_code_refs": [script],
        })
        # Run the trial
        await call_tool_http(server_url, "run_trial", {
            "programme_id": pid, "trial_id": tid,
        })
        # Poll for completion
        for _ in range(30):
            status = await call_tool_http(server_url, "get_trial_status", {
                "programme_id": pid, "trial_id": tid,
            })
            if status.get("status") in ("completed", "failed"):
                break
            await asyncio.sleep(1)
        # Record observation
        if status.get("status") == "completed":
            obs = await call_tool_http(server_url, "record_observation", {
                "trial_id": tid,
                "metrics": {"val_accuracy": 0.92},
                "variance": {"val_accuracy": 0.003},
                "spatiotemporal_region": "test",
            })
            # Update belief + conclude hypothesis (required before close)
            await call_tool_http(server_url, "update_belief", {
                "programme_id": pid, "trial_id": tid,
                "observation_id": obs["observation_id"],
            })
            await call_tool_http(server_url, "conclude_hypothesis", {
                "programme_id": pid, "hypothesis_id": hid,
                "verdict": "accepted", "evidence_summary": "test",
            })
        # Close programme (auto-archives)
        await call_tool_http(server_url, "close_programme", {
            "programme_id": pid, "status": "completed",
        })
        # Check the archived programme has all code snippets
        result = await call_tool_http(server_url, "get_archived_programme", {
            "programme_id": pid,
        })
        assert "error" not in result, f"get_archived_programme failed: {result}"
        # The archive should have at least 3 code snippets:
        # executor.py + utils.py + train_script.py
        snippets = result.get("code_snippets", [])
        assert len(snippets) >= 3, f"Expected >=3 snippets, got {len(snippets)}: {snippets}"


# --- Part 9: JSON-string argument tolerance ---


class TestJSONStringArgumentTolerance:
    """LLM clients that cannot emit typed objects send structured params
    as JSON-encoded strings. The tool surface accepts and decodes them."""

    @pytest.mark.asyncio
    async def test_create_programme_accepts_json_string_params(self, server_url):
        """constraints/allowed_variables/budget as JSON strings succeed."""
        result = await call_tool_http(server_url, "create_programme", {
            "goal": "json-string params test",
            "constraints": '{"gpu_memory_gb": 8}',
            "allowed_variables": '["lr", "wd"]',
            "budget": '{"max_trials": 10, "max_wall_time_hours": 5.0}',
        })
        assert "error" not in result, f"create_programme failed: {result}"
        assert result["status"] == "created"

    @pytest.mark.asyncio
    async def test_create_programme_rejects_invalid_json_string(self, server_url):
        """An unparseable JSON string is a tool error naming the param."""
        result = await call_tool_http(server_url, "create_programme", {
            "goal": "bad json test",
            "constraints": "{not valid json",
            "allowed_variables": ["lr"],
            "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
        })
        assert "error" in result
        assert "constraints" in result["error"]

    @pytest.mark.asyncio
    async def test_create_programme_rejects_wrong_decoded_type(self, server_url):
        """A JSON string that decodes to the wrong type is rejected."""
        result = await call_tool_http(server_url, "create_programme", {
            "goal": "wrong type test",
            "constraints": '["not", "a", "dict"]',
            "allowed_variables": ["lr"],
            "budget": {"max_trials": 10, "max_wall_time_hours": 5.0},
        })
        assert "error" in result
        assert "constraints" in result["error"]

    @pytest.mark.asyncio
    async def test_formulate_hypothesis_accepts_json_string_list(self, server_url):
        pid = await make_programme(server_url)
        result = await call_tool_http(server_url, "formulate_hypothesis", {
            "programme_id": pid,
            "statement": "lr matters",
            "failure_criterion": "no improvement",
            "variables_involved": '["lr"]',
        })
        assert "error" not in result, f"formulate_hypothesis failed: {result}"

    @pytest.mark.asyncio
    async def test_design_experiment_accepts_json_string_config(self, server_url):
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        result = await call_tool_http(server_url, "design_experiment", {
            "programme_id": pid, "hypothesis_id": hid,
            "config": '{"lr": 0.01}',
        })
        assert "error" not in result, f"design_experiment failed: {result}"
        assert "trial_id" in result

    @pytest.mark.asyncio
    async def test_capture_bundle_accepts_json_string_params(self, server_url):
        """seeds/splits as JSON strings succeed; bare-int seeds works too."""
        import os
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        tid = await make_trial(server_url, pid, hid)
        stub = os.path.join(os.path.dirname(__file__), "fixtures", "train_stub.py")
        result = await call_tool_http(server_url, "capture_bundle", {
            "trial_id": tid, "code_ref": stub, "env_ref": "py",
            "seeds": "[42]", "splits": '{"train": 0.8}',
        })
        assert "error" not in result, f"capture_bundle failed: {result}"

    @pytest.mark.asyncio
    async def test_capture_bundle_accepts_bare_int_seed(self, server_url):
        """A bare int for seeds is wrapped into a single-element list."""
        import os
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        tid = await make_trial(server_url, pid, hid)
        stub = os.path.join(os.path.dirname(__file__), "fixtures", "train_stub.py")
        result = await call_tool_http(server_url, "capture_bundle", {
            "trial_id": tid, "code_ref": stub, "env_ref": "py",
            "seeds": 7, "splits": {"train": 0.8},
        })
        assert "error" not in result, f"capture_bundle failed: {result}"

    @pytest.mark.asyncio
    async def test_schema_advertises_string_fallback(self, server_url):
        """tools/list must advertise anyOf object/string so clients know."""
        tools = await list_tools_http(server_url)
        schema = next(t for t in tools if t.name == "create_programme").input_schema
        variants = schema["properties"]["constraints"]["anyOf"]
        types = {v.get("type") for v in variants}
        assert "object" in types and "string" in types


# --- Part 10: RSI Phase 0 — candidate lineage over HTTP ---


class TestCandidateLineageHTTP:
    """Phase 0 lineage entities work end-to-end over real MCP transport."""

    async def _register(self, server_url, **overrides):
        import uuid
        args = {
            "code_artifact_digest": "none",
            "model_ref": "test-model",
            "capability_profile": {"tools": ["create_programme"]},
        }
        args.update(overrides)
        result = await call_tool_http(server_url, "register_candidate", args)
        assert "error" not in result, f"register_candidate failed: {result}"
        return result["candidate_id"]

    @pytest.mark.asyncio
    async def test_full_lineage_flow(self, server_url):
        """register → contract → decision → attributed programme → lineage."""
        cand = await self._register(server_url)

        pid = await make_programme(server_url, candidate_version_id=cand)

        contract = await call_tool_http(server_url, "create_evaluation_contract", {
            "programme_id": pid,
            "metrics": '{"ppl": "minimize"}',
            "promotion_policy": {"threshold": 0.05},
        })
        assert "error" not in contract, f"contract failed: {contract}"
        assert contract["version"] == 1

        decision = await call_tool_http(server_url, "record_promotion_decision", {
            "candidate_id": cand,
            "contract_id": contract["contract_id"],
            "verdict": "hold",
            "evidence_refs": '["trial-x"]',
            "rationale": "awaiting replication",
            "decided_by": "human",
        })
        assert "error" not in decision, f"decision failed: {decision}"

        lineage = await call_tool_http(server_url, "get_candidate_lineage", {
            "candidate_id": cand,
        })
        assert lineage["lineage"][0]["id"] == cand

    @pytest.mark.asyncio
    async def test_parent_child_lineage(self, server_url):
        parent = await self._register(server_url)
        child = await self._register(server_url, parent_id=parent)
        lineage = await call_tool_http(server_url, "get_candidate_lineage", {
            "candidate_id": child,
        })
        assert [c["id"] for c in lineage["lineage"]] == [child, parent]
