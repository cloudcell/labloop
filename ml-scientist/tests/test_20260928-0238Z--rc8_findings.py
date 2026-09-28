"""rc-8 regressions — the rc-7b VM diagnostic findings.

R1   run_trial's submit window was invisible to cancel_async: the row
     went 'running', then a synchronous probe ran for _submit_wait
     unregistered, and on timeout a SECOND child was dispatched — a
     cancel landing in the window was consumed and the second child
     ran to completion over a terminal row. Fix: dispatch-first
     (execute_code_async registers immediately) + shielded
     await_async + a cancel tombstone + a pre-dispatch row re-check.
R7   trial-surface stderr was never elided — 42 KB arrived verbatim.
     Payload is elided; the artifact log keeps every byte.
R9   prepare_data injected JSON null into the Python wrapper —
     {"seed": null} is a NameError, not a dict. Now a JSON string
     literal parsed at runtime.
R10  unrunnable_campaigns missed present-but-zero budgets.
R11  record_promotion_decision accepted refs with unrecognised
     prefixes (evr-… is minted by no server anywhere).
R12  close_campaign's both-arms refusal never named campaign_arm —
     spawn-linked campaigns dead-ended callers on the default
     'challenger' fill.
R13  a campaign closed before any evidence pull is permanently
     verdict-less, and nothing flagged it — campaigns_awaiting_verdict
     is the zetesis analogue of arete's decision_debt.
R14+R15 terminal_with_live_executor emitted string violations — the
     rendered sentence was the ack identity (re-renders expired acks)
     and _annotate_acks only marks dicts.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ml_episteme_mcp.clients.local_executor import LocalExecutor
from ml_episteme_mcp.state.models import (
    Bundle,
    CandidateVersion,
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


def _seed_trial(store, pid="prog-8", hid="hyp-8", tid="trial-8"):
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


class _Task:
    """Fake executor task with a controllable done() flag."""

    def __init__(self, done=False):
        self._done = done

    def done(self):
        return self._done


# --- R1: submit-window cancel can no longer leak a second child ------


class TestSubmitWindowCancel:
    """The VM observed marks landing 11–20 ms into the 10 s probe
    producing a second child that ran the full 25–30 s (residue:
    'executor ran 30.077s over a 0.011s window'). Dispatch-first means
    the cancel finds a registered task; the tombstone means even a
    pre-registration cancel refuses the spawn."""

    async def test_cancel_before_dispatch_tombstones(self, tmp_path):
        """cancel_async on an id with no registered task must make a
        LATER execute_code_async refuse to spawn — not silently
        consume the cancel."""
        ex = LocalExecutor(timeout=30, sandbox="none", trace_reads="off")
        reply = json.loads(await ex.cancel_async("trial-never"))
        assert reply["status"] == "cancelled"
        assert reply.get("tombstoned") is True

        marker = tmp_path / "should_not_exist.marker"
        out = json.loads(await ex.execute_code_async(
            "trial-never",
            f"open({str(marker)!r}, 'w').write('x')\n",
            artifact_dir=tmp_path / "art",
        ))
        assert out["status"] == "cancelled"
        assert out.get("cancelled_before_dispatch") is True
        await asyncio.sleep(0.3)
        assert not marker.exists()
        assert "trial-never" not in ex._running_tasks

    async def test_cancel_during_registered_wait_kills_task(
        self, tmp_path
    ):
        """A cancel landing inside the submit window (task registered,
        await_async shielded-waiting) kills the child — no second
        spawn, no run-to-completion."""
        ex = LocalExecutor(timeout=30, sandbox="none", trace_reads="off")
        marker = tmp_path / "leak.marker"
        await ex.execute_code_async(
            "trial-window",
            "import time\n"
            "time.sleep(2)\n"
            f"open({str(marker)!r}, 'w').write('x')\n",
            artifact_dir=tmp_path / "art",
        )
        waiter = asyncio.create_task(
            ex.await_async("trial-window", timeout_seconds=10)
        )
        await asyncio.sleep(0.05)
        reply = json.loads(await ex.cancel_async("trial-window"))
        assert reply["status"] == "cancelled"
        waited = json.loads(await waiter)
        assert waited["status"] == "cancelled"
        await asyncio.sleep(2.2)
        assert not marker.exists()

    async def test_await_async_timeout_leaves_task_running(
        self, tmp_path
    ):
        """The shielded wait reports 'running' on timeout — it does
        NOT cancel the task."""
        ex = LocalExecutor(timeout=30, sandbox="none", trace_reads="off")
        marker = tmp_path / "done.marker"
        await ex.execute_code_async(
            "trial-slow",
            "import time\n"
            "time.sleep(0.4)\n"
            f"open({str(marker)!r}, 'w').write('x')\n",
            artifact_dir=tmp_path / "art",
        )
        waited = json.loads(
            await ex.await_async("trial-slow", timeout_seconds=0.05)
        )
        assert waited["status"] == "running"
        await asyncio.sleep(0.6)
        assert marker.exists()

    async def test_await_async_returns_terminal_result(self, tmp_path):
        """A fast trial finalises inside the window — same surface as
        the old synchronous probe."""
        ex = LocalExecutor(timeout=30, sandbox="none", trace_reads="off")
        await ex.execute_code_async(
            "trial-fast",
            "import json\nprint(json.dumps({'ok': 1}))\n",
            artifact_dir=tmp_path / "art",
        )
        waited = json.loads(
            await ex.await_async("trial-fast", timeout_seconds=5)
        )
        assert waited["status"] == "completed"

    async def test_no_dispatch_when_row_terminal(self, store, tmp_path):
        """A row that goes terminal between the 'running' stamp and
        dispatch must not spawn — the pre-dispatch re-check. Simulates
        a concurrent mark_retryable landing mid-window by flipping the
        row to retryable right after run_trial stamps it running."""
        from ml_episteme_mcp.clients.adaptor import MCPAdaptor
        from ml_episteme_mcp.tools.trial import register

        tid = _seed_trial(store)
        store.create_bundle(Bundle(
            id="bundle-8", trial_id=tid,
            code_ref=str(tmp_path / "trainer.py"),
            env_ref="local",
            seeds_json="[1]",
            splits_json="{}",
        ))
        store.conn.execute(
            "UPDATE trials SET bundle_id = 'bundle-8' WHERE id = ?",
            (tid,),
        )
        store.conn.commit()

        dispatched = []

        class SpyExecutor:
            async def execute_code_async(self, trial_id, code, **kw):
                dispatched.append(trial_id)
                return json.dumps({"status": "running"})

            async def await_async(self, trial_id, timeout_seconds):
                return json.dumps({"status": "running"})

            async def cancel_async(self, trial_id):
                return json.dumps({"status": "cancelled"})

            def get_async_status(self, trial_id):
                return json.dumps({"status": "unknown"})

        adaptor = MCPAdaptor({})
        adaptor.set_executor(SpyExecutor())

        # Flip the row terminal the instant run_trial stamps it
        # 'running' — the mid-window mark.
        real_update = store.update_trial_status

        def racing_update(trial_id, status, reason=None):
            real_update(trial_id, status, reason)
            if status == "running":
                real_update(trial_id, "retryable", reason="superseded")

        store.update_trial_status = racing_update

        class FakeMCP:
            def __init__(self):
                self.tools = {}

            def tool(self):
                def deco(fn):
                    self.tools[fn.__name__] = fn
                    return fn
                return deco

        mcp = FakeMCP()
        register(mcp, store, adaptor, executor_config={})
        run_trial = mcp.tools["run_trial"]

        res = await run_trial(programme_id="prog-8", trial_id=tid)
        payload = json.loads(res.content[0].text)
        assert payload["status"] == "retryable"
        assert "execute_code_async" not in dispatched
        assert dispatched == []


# --- R7: trial-surface stderr elision --------------------------------


class TestStderrElision:
    """The alt run pushed 42,919 bytes of stderr through the trial
    surface unmarked — the payload field is elided, the artifact log
    keeps every byte."""

    async def test_long_stderr_elided_in_payload_full_in_artifact(
        self, tmp_path
    ):
        ex = LocalExecutor(timeout=30, sandbox="none", trace_reads="off")
        artifact_dir = tmp_path / "art"
        long_err = "header line\n" + ("noise\n" * 8000) + "tail line\n"
        code = (
            "import sys\n"
            f"sys.stderr.write({long_err!r})\n"
            "sys.exit(1)\n"
        )
        out = json.loads(await ex.execute_code(
            code, artifact_dir=artifact_dir,
        ))
        assert out["status"] == "failed"
        # Payload: head + marker + tail, bounded
        assert "elided" in out["stderr"]
        assert "header line" in out["stderr"]
        assert "tail line" in out["stderr"]
        assert len(out["stderr"]) < len(long_err)
        # Artifact: complete
        stderr_logs = list(artifact_dir.glob("*_stderr.log"))
        assert stderr_logs, "no stderr artifact written"
        assert stderr_logs[0].read_text() == long_err

    async def test_short_stderr_untouched(self, tmp_path):
        ex = LocalExecutor(timeout=30, sandbox="none", trace_reads="off")
        out = json.loads(await ex.execute_code(
            "import sys\nsys.stderr.write('small\\n')\nsys.exit(1)\n",
            artifact_dir=tmp_path / "art",
        ))
        assert out["stderr"].strip() == "small"
        assert "elided" not in out["stderr"]


# --- R9: generator wrapper JSON literal ------------------------------


class TestGeneratorJsonLiteral:
    """prepare_data's wrapper spliced json.dumps output verbatim —
    {"seed": null} is not Python. The fix injects a JSON string
    literal and parses it at runtime."""

    async def test_none_seed_and_bool_params_run(self, tmp_path):
        from ml_episteme_mcp.clients.local_data_handler import (
            LocalDataHandler,
        )

        ex = LocalExecutor(timeout=30, sandbox="none", trace_reads="off")
        handler = LocalDataHandler(
            data_dir=tmp_path / "data", executor=ex,
        )
        gen = tmp_path / "gen.py"
        gen.write_text(
            "def generate_data(config, output_path):\n"
            "    assert config['seed'] is None\n"
            "    assert config['flag'] is True\n"
            "    assert config['opt'] is False\n"
            "    with open(output_path, 'w') as f:\n"
            "        f.write('a,b\\n1,2\\n')\n"
        )
        ref = await handler.prepare_data(
            split="train",
            regime="generated",
            generator_code_ref=str(gen),
            generator_seed=None,
            generator_params={"flag": True, "opt": False},
        )
        assert ref.startswith("data-ref-")


# --- R10: zero-budget campaigns wedge --------------------------------


class TestZeroBudgetWedge:
    """camp-b1610105 carried {"programmes_per_arm": 0, ...} —
    unspawnable yet unrunnable_campaigns said ok and close_campaign
    gave the impossible 'both arms must run' advice."""

    def test_zero_budget_is_not_carryable(self):
        from ml_zetesis_mcp.enforcement.checks import (
            check_campaign_budget_carryable,
        )

        err = check_campaign_budget_carryable({
            "programmes_per_arm": 0, "trials_per_programme": 0,
        })
        assert err is not None
        assert "degenerate" in err
        assert "programmes_per_arm=0" in err

    def test_negative_and_bool_budgets_rejected(self):
        from ml_zetesis_mcp.enforcement.checks import (
            check_campaign_budget_carryable,
        )

        assert check_campaign_budget_carryable({
            "programmes_per_arm": -1, "trials_per_programme": 2,
        }) is not None
        # bool is a degenerate int — True/False are not counts
        assert check_campaign_budget_carryable({
            "programmes_per_arm": True, "trials_per_programme": 2,
        }) is not None
        assert check_campaign_budget_carryable({
            "programmes_per_arm": "2", "trials_per_programme": 2,
        }) is not None
        # Sane budgets still pass
        assert check_campaign_budget_carryable({
            "programmes_per_arm": 2, "trials_per_programme": 3,
        }) is None


# --- R11: unrecognised evidence-ref prefixes --------------------------


class TestUnknownEvidencePrefix:
    """evr-does-not-exist was accepted — it mints nothing anywhere.
    Loop-0 prefixes still resolve; recognised foreign prefixes pass
    through opaque; unrecognised prefixes are refused."""

    def _check(self, store, refs):
        from ml_episteme_mcp.enforcement.commitments import (
            check_decision_valid,
        )

        return check_decision_valid(
            verdict="promote", rationale="r", decided_by="human:t",
            candidate_id="cand-x", contract_id=None,
            store=store, evidence_refs=refs,
        )

    def test_unrecognised_prefix_refused(self, store):
        err = self._check(store, ["evr-does-not-exist"])
        assert err is not None
        assert "unrecognised" in err

    def test_known_foreign_prefixes_pass_through(self, store):
        """eref-/claim-/mdec- are other loops' mints — opaque, not
        resolved here (cross-server resolution is a separate design
        decision), but they ARE recognised shapes. Any remaining error
        is downstream (missing candidate), not the prefix gate."""
        err = self._check(
            store, ["eref-abc123", "claim-xyz", "mdec-q"]
        )
        assert err is None or "unrecognised" not in err

    def test_loop0_prefixed_unknown_still_refused(self, store):
        """A trial- id naming nothing is fabricated — Loop-0 mints
        must resolve."""
        err = self._check(store, ["trial-nonexistent"])
        assert err is not None
        assert "nonexistent Loop-0" in err

    def test_real_ref_accepted(self, store):
        tid = _seed_trial(store)
        err = self._check(store, [tid])
        assert err is None or "unrecognised" not in err


# --- R14+R15: stable violation identity -------------------------------


class TestAckIdentity:
    """Violations are dicts keyed on trial_id — an ack survives the
    live→residue re-render, and _annotate_acks can mark them."""

    def _terminal_retryable(self, store, tid="trial-8"):
        _seed_trial(store, tid=tid)
        store.update_trial_status(tid, "running")
        store.update_trial_status(tid, "retryable", reason="infra")
        return tid

    def test_violations_are_dicts_with_trial_id(self, store):
        from ml_episteme_mcp.integrity.checks import run_checks

        tid = self._terminal_retryable(store)
        ex = type("E", (), {"_running_tasks": {tid: _Task(False)}})()
        c = _check(
            run_checks(store, executor=ex),
            "terminal_with_live_executor",
        )
        assert not c["ok"]
        v = c["violations"][0]
        assert isinstance(v, dict)
        assert v["trial_id"] == tid
        assert v["kind"] == "live_task"

    def test_ack_survives_rerender(self, store):
        """The VM case: ack the live-task violation, the executor
        finishes, the row re-renders as residue — the ack must still
        match because the identity is trial_id, not the sentence."""
        from ml_episteme_mcp.enforcement.recurrence import (
            canonical_ref,
            open_violations,
            violation_ack,
        )
        from ml_episteme_mcp.integrity.checks import run_and_log
        from ml_episteme_mcp.tools.integrity import _annotate_acks

        tid = self._terminal_retryable(store)

        # Phase 1: live task — log the run so open_violations sees it.
        ex = type("E", (), {"_running_tasks": {tid: _Task(False)}})()
        run_and_log(store, executor=ex, trigger="test")
        ack = violation_ack(
            store, "terminal_with_live_executor", tid,
            disposition="accepted", decided_by="human:t",
        )
        assert ack["matched_open_violation"] is True

        # Phase 2: task reaped, executor record landed over the mark —
        # the row re-renders as a RESIDUE dict with different detail
        # text but the same canonical ref.
        t0 = datetime.now(timezone.utc) - timedelta(seconds=130)
        store.conn.execute(
            "UPDATE trials SET started_at = ?, finished_at = ? "
            "WHERE id = ?",
            (t0.isoformat(),
             (t0 + timedelta(milliseconds=14)).isoformat(), tid),
        )
        store.conn.commit()
        store.update_trial_executor_output(tid, json.dumps({
            "status": "completed", "exit_code": 0,
            "duration_seconds": 120.0,
        }))
        ex2 = type("E", (), {"_running_tasks": {}})()
        payload2 = run_and_log(store, executor=ex2, trigger="test")
        c2 = _check(payload2, "terminal_with_live_executor")
        assert not c2["ok"]
        assert canonical_ref(c2["violations"][0]) == tid
        assert c2["violations"][0]["kind"] == "residue"

        # The ack still suppresses the re-rendered violation and the
        # surface annotates it as acknowledged (R15).
        assert not [
            v for v in open_violations(store)
            if v["check"] == "terminal_with_live_executor"
        ]
        _annotate_acks(store, payload2)
        assert c2["violations"][0]["acknowledged"] is True


# --- R13: closed campaign awaiting verdict ---------------------------


class TestCampaignsAwaitingVerdict:
    async def test_closed_unverdicted_flags(self, tmp_path):
        from ml_zetesis_mcp.integrity.checks import run_checks as zc
        from ml_zetesis_mcp.state.models import (
            CampaignStatus, PromotionCampaign,
        )
        from ml_zetesis_mcp.state.store import SearchStore

        ss = SearchStore(str(tmp_path / "s.db"))
        ss.connect()
        try:
            ss.create_campaign(PromotionCampaign(
                id="camp-debt", contract_id="c1",
                champion_id="cand-a", challenger_id="cand-b",
                primary_metric="hits",
                status=CampaignStatus.closed,
                promotion_score=1.398,
            ))
            ss.create_campaign(PromotionCampaign(
                id="camp-judged", contract_id="c1",
                champion_id="cand-a", challenger_id="cand-b",
                primary_metric="hits",
                status=CampaignStatus.closed,
                promotion_score=0.9,
                decision_id="decision-1",
            ))
            payload = await zc(ss)
            chk = next(
                c for c in payload["checks"]
                if c["name"] == "campaigns_awaiting_verdict"
            )
            assert not chk["ok"]
            flagged = {v["campaign_id"] for v in chk["violations"]}
            assert flagged == {"camp-debt"}
        finally:
            ss.close()


# --- R12: close refusal names the affordance -------------------------


class TestCloseRefusalAffordance:
    async def test_spawn_linked_close_names_campaign_arm(self, tmp_path):
        """A spawn-linked campaign (opened via arete open_arm_campaign)
        missing champion results must name campaign_arm /
        spawn_arm_programme in the refusal — not just 'run more'."""
        from ml_zetesis_mcp.state.models import (
            CampaignArm, CampaignSpawn, PromotionCampaign, SpawnStatus,
        )
        from ml_zetesis_mcp.state.store import SearchStore
        from ml_zetesis_mcp.tools import promotion as zprom

        ss = SearchStore(str(tmp_path / "s.db"))
        ss.connect()
        try:
            ss.create_campaign(PromotionCampaign(
                id="camp-linked", contract_id="c1",
                champion_id="cand-a", challenger_id="cand-b",
                primary_metric="hits",
                budget={
                    "programmes_per_arm": 2,
                    "trials_per_programme": 3,
                },
            ))
            ss.create_campaign_spawn(CampaignSpawn(
                id="spawn-1", campaign_id="camp-linked",
                arm=CampaignArm.challenger,
                programme_id="prog-x",
                status=SpawnStatus.spawned,
            ))

            class FakeMCP:
                def __init__(self):
                    self.tools = {}

                def tool(self):
                    def deco(fn):
                        self.tools[fn.__name__] = fn
                        return fn
                    return deco

            mcp = FakeMCP()
            zprom.register(mcp, ss, adaptors=None)
            res = mcp.tools["close_campaign"]("camp-linked")
            text = res.content[0].text
            assert res.is_error
            assert "spawn_arm_programme" in text
            assert "campaign_arm" in text
            assert "champion" in text
        finally:
            ss.close()
