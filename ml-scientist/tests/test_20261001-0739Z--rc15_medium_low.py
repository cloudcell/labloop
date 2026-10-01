"""rc-15 extraction — F8 medium/low batch regression coverage.

- ``next`` hint injection marks its scope (``scope: "server"``) and
  carries ``entity_refs`` so a programme-scoped response can't read
  the lab-global top recommendation as per-entity advice.
- ``acknowledge_violation`` on an unmatched (check, object_ref) pair
  surfaces ``open_refs`` — a typo now shows what it should have said
  instead of reading identically to a valid ack.
- ``wait_trial`` echoes ``timeout_seconds_requested`` /
  ``timeout_seconds_applied`` so a silent 60 s clamp is visible.
- ``run_trial``'s running response carries ``submit_wait_seconds``
  (the ~10 s settle cost was prose-only).
- ``stalled_running_trials`` margin is reachable: 30 s post-deadline
  grace, not a second full executor timeout.
- ``connectivity_report`` rows disclose ``call_timeout_seconds`` —
  the per-channel response deadline was invisible in every digest.
- ``get_claim`` and ``get_claims`` return the same provenance bundle
  shape (rc-15 measured them as divergent; the singular read now
  matches — this pins parity).
- The correction gate's error names the real primary metric when it
  mentions ``recursive_gain`` (was interpolated once, literal once).
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from mcp.server.mcpserver.exceptions import ToolError


async def call_tool(mcp, name: str, args: dict) -> dict:
    try:
        result = await mcp.call_tool(name, args)
    except ToolError as exc:
        return {"error": str(exc)}
    text = result.content[0].text if result.content else ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"error": text}


async def read_status(mcp, uri: str) -> dict:
    contents = await mcp.read_resource(uri)
    return json.loads(list(contents)[0].content)


def _write_violation_log(store, check_name: str, object_ref: str) -> None:
    from ml_episteme_mcp.integrity.log import write_check_log
    from ml_episteme_mcp.integrity.checks import log_dir_for

    log_dir = log_dir_for(store)
    write_check_log(log_dir, {
        "server": "test",
        "checked_at": "2026-01-01T00:00:00+00:00",
        "checks": [{
            "name": check_name,
            "ok": False,
            "violations": [object_ref],
            "detail": f"test violation on {object_ref}",
        }],
    }, max_files=30)


@pytest.fixture
def episteme(tmp_path):
    from ml_episteme_mcp.state.store import StateStore
    from ml_episteme_mcp.server import create_server
    from ml_episteme_mcp.enforcement import recurrence

    recurrence.TRACKER.reset()
    store = StateStore(str(tmp_path / "state.db"))
    store.connect()
    mcp = create_server(
        store,
        enforcement_config={
            "recurrent_protocol": True,
            "status_freshness_seconds": 600,
        },
    )
    yield store, mcp
    store.close()


# --- next hint: scope + entity_refs -----------------------------------


async def test_next_hint_marks_server_scope(episteme):
    """A programme-scoped payload carrying a lab-global hint must say
    so — scope + entity_refs let the caller tell the difference."""
    _, mcp = episteme
    digest = await read_status(mcp, "protocol://status")
    r = await call_tool(mcp, "create_programme", {
        "goal": "g", "constraints": {}, "allowed_variables": ["x"],
        "budget": {"max_trials": 3},
    })
    # Hints only attach when the digest has recommendations and no
    # mutation has intervened — create_programme is a read-adjacent
    # mutation, so read the hint off a read-only tool instead.
    hint_holder = await call_tool(mcp, "list_programmes", {})
    top = (digest.get("recommended_next") or [{}])[0]
    if "next" in hint_holder:
        assert hint_holder["next"]["scope"] == "server"
        assert "entity_refs" in hint_holder["next"]
    # Direct unit check on the tracker — independent of which tool
    # receives the injection.
    from ml_episteme_mcp.enforcement import recurrence
    recurrence.TRACKER.digest = {
        "recommended_next": [{
            "action": "a", "tool": "t",
            "reason": "r", "entity_refs": ["prog-x"],
        }],
        "blockers": [],
    }
    recurrence.TRACKER.status_read_at = __import__("time").time()
    recurrence.TRACKER.mutated_since_read = False
    hint = recurrence.TRACKER.next_hint()
    assert hint["scope"] == "server"
    assert hint["entity_refs"] == ["prog-x"]


# --- acknowledge_violation: unmatched ref surfaces open_refs ----------


async def test_unmatched_ack_lists_open_refs(episteme):
    """A typo'd object_ref returns no_matching_violation PLUS the live
    open refs — diagnosable, not a silent no-op."""
    store, mcp = episteme
    _write_violation_log(store, "unsealed_execution", "trial-abc123")
    await read_status(mcp, "protocol://status")
    ack = await call_tool(mcp, "acknowledge_violation", {
        "check_name": "unsealed_execution",
        "object_ref": "trial-TYPO",
        "disposition": "accepted: test",
        "decided_by": "human:test",
    })
    assert ack["status"] == "no_matching_violation"
    assert ack["matched_open_violation"] is False
    refs = ack["open_refs"]
    assert {"check": "unsealed_execution",
            "object_ref": "trial-abc123"} in refs


async def test_matched_ack_has_no_open_refs_noise(episteme):
    """A matched ack doesn't need the correction hint."""
    store, mcp = episteme
    _write_violation_log(store, "unsealed_execution", "trial-abc123")
    await read_status(mcp, "protocol://status")
    ack = await call_tool(mcp, "acknowledge_violation", {
        "check_name": "unsealed_execution",
        "object_ref": "trial-abc123",
        "disposition": "accepted: test",
        "decided_by": "human:test",
    })
    assert ack["status"] == "acknowledged"
    assert "open_refs" not in ack


# --- wait_trial: clamp echo -------------------------------------------


async def test_wait_trial_echoes_timeout_clamp(tmp_path):
    """timeout_seconds=120 clamps to 60 — the response must say so."""
    from ml_episteme_mcp.state.store import StateStore
    from ml_episteme_mcp.server import create_server

    store = StateStore(str(tmp_path / "s.db"))
    store.connect()
    mcp = create_server(store)
    prog = await call_tool(mcp, "create_programme", {
        "goal": "g", "constraints": {}, "allowed_variables": ["x"],
        "budget": {"max_trials": 3},
    })
    hyp = await call_tool(mcp, "formulate_hypothesis", {
        "programme_id": prog["programme_id"],
        "statement": "s", "failure_criterion": "f",
        "variables_involved": ["x"],
    })
    trial = await call_tool(mcp, "design_experiment", {
        "programme_id": prog["programme_id"],
        "hypothesis_id": hyp["hypothesis_id"],
        "config": {"x": 1},
    })
    await call_tool(mcp, "cancel_trial", {
        "programme_id": prog["programme_id"],
        "trial_id": trial["trial_id"],
    })
    r = await call_tool(mcp, "wait_trial", {
        "programme_id": prog["programme_id"],
        "trial_id": trial["trial_id"],
        "timeout_seconds": 120, "poll_seconds": 0.5,
    })
    assert r["status"] == "abandoned"
    assert r["timeout_seconds_requested"] == 120
    assert r["timeout_seconds_applied"] == 60.0


async def test_run_trial_discloses_submit_wait(tmp_path):
    """The ~10 s settle cost is a field, not prose — the 'running'
    response carries submit_wait_seconds."""
    from ml_episteme_mcp.clients.adaptor import MCPAdaptor
    from ml_episteme_mcp.state.models import (
        Bundle, Hypothesis, Programme, Trial,
    )
    from ml_episteme_mcp.state.store import StateStore
    from ml_episteme_mcp.tools.trial import register

    store = StateStore(str(tmp_path / "s.db"))
    store.connect()
    try:
        store.create_programme(Programme(
            id="prog-9", goal="g", constraints={},
            allowed_variables=["x"], budget_max_trials=10,
            budget_max_wall_time_hours=1.0,
        ))
        store.create_hypothesis(Hypothesis(
            id="hyp-9", programme_id="prog-9", statement="s",
            failure_criterion="f", variables_involved=["x"],
        ))
        store.create_trial(Trial(
            id="trial-9", programme_id="prog-9", hypothesis_id="hyp-9",
            config_json="{}",
        ))
        store.create_bundle(Bundle(
            id="bundle-9", trial_id="trial-9",
            code_ref=str(tmp_path / "trainer.py"),
            env_ref="local", seeds_json="[1]", splits_json="{}",
        ))
        store.conn.execute(
            "UPDATE trials SET bundle_id = 'bundle-9' "
            "WHERE id = 'trial-9'"
        )
        store.conn.commit()

        class SpyExecutor:
            async def execute_code_async(self, trial_id, code, **kw):
                return json.dumps({"status": "running"})

            async def await_async(self, trial_id, timeout_seconds):
                return json.dumps({"status": "running"})

            async def cancel_async(self, trial_id):
                return json.dumps({"status": "cancelled"})

            def get_async_status(self, trial_id):
                return json.dumps({"status": "unknown"})

        adaptor = MCPAdaptor({})
        adaptor.set_executor(SpyExecutor())

        class FakeMCP:
            def __init__(self):
                self.tools = {}

            def tool(self):
                def deco(fn):
                    self.tools[fn.__name__] = fn
                    return fn
                return deco

        mcp = FakeMCP()
        register(
            mcp, store, adaptor,
            executor_config={"submit_wait_seconds": 10.0},
        )
        res = await mcp.tools["run_trial"](
            programme_id="prog-9", trial_id="trial-9",
        )
        payload = json.loads(res.content[0].text)
        assert payload["status"] == "running"
        assert payload["submit_wait_seconds"] == 10.0
        # The auto-finalize task polls forever on this spy — cancel it
        # rather than leak it into other tests.
        for t in list(__import__("asyncio").all_tasks()):
            if "_auto_finalize" in str(t.get_coro()):
                t.cancel()
    finally:
        store.close()


# --- stalled_running_trials: reachable margin -------------------------


def test_stalled_margin_reachable(tmp_path):
    """30 s post-deadline grace — a wedged trial flags at timeout+30s,
    not after a second full executor timeout."""
    from ml_episteme_mcp.state.models import (
        Programme, Hypothesis, Trial,
    )
    from ml_episteme_mcp.state.store import StateStore
    from ml_episteme_mcp.integrity.checks import run_checks

    store = StateStore(str(tmp_path / "s.db"))
    store.connect()
    try:
        store.create_programme(Programme(
            id="prog-1", goal="g", constraints={},
            allowed_variables=["x"], budget_max_trials=3,
            budget_max_wall_time_hours=1.0,
        ))
        store.create_hypothesis(Hypothesis(
            id="hyp-1", programme_id="prog-1", statement="s",
            failure_criterion="f", variables_involved=["x"],
        ))
        store.create_trial(Trial(
            id="trial-1", programme_id="prog-1", hypothesis_id="hyp-1",
            config_json="{}",
        ))
        store.update_trial_status("trial-1", "running")
        store.conn.execute(
            "UPDATE trials SET started_at = ? WHERE id = 'trial-1'",
            ((datetime.now(timezone.utc) - timedelta(seconds=100))
             .isoformat(),),
        )
        store.conn.commit()

        class Task:
            def done(self):
                return False

        class FakeExecutor:
            _running_tasks = {"trial-1": Task()}
            _timeout = 60  # 100s elapsed > 60 + 30 margin → stalled

        # The stalled check would fire, but the running trial also
        # trips completed_without_observation-class companions — only
        # assert the target check.
        payload = run_checks(store, executor=FakeExecutor())
        check = next(
            c for c in payload["checks"]
            if c["name"] == "stalled_running_trials"
        )
        assert not check["ok"]
        assert check["violations"][0]["trial_id"] == "trial-1"
        assert check["violations"][0]["deadline_seconds"] == 60
    finally:
        store.close()


# --- connectivity report: call_timeout_seconds disclosed --------------


def test_connectivity_report_discloses_call_timeout():
    """The per-channel response deadline is a field, not a config-only
    secret — digests carry the channel rows."""
    from ml_zetesis_mcp.clients.adaptors import Adaptors
    from ml_zetesis_mcp.clients.mcp_client import MCPClientAdaptor

    adaptors = Adaptors()
    adaptors.register_channel(
        "episteme", "trials",
        MCPClientAdaptor(
            {"url": "http://x/mcp", "call_timeout_seconds": 17}
        ),
    )
    report = adaptors.connectivity_report()
    row = next(c for c in report if c["channel"] == "episteme")
    assert row["call_timeout_seconds"] == 17


# --- get_claim/get_claims bundle parity -------------------------------


@pytest.fixture
def anamnesis(tmp_path):
    from ml_anamnesis_mcp.state.store import MemoryStore
    from ml_anamnesis_mcp.server import create_server

    store = MemoryStore(str(tmp_path / "memory.db"))
    store.connect()
    mcp = create_server(store)
    yield store, mcp
    store.close()


async def test_get_claim_matches_get_claims_bundle(anamnesis):
    """The singular read returns the same provenance bundle the batch
    read does — claim fields + outgoing + incoming edges."""
    _, mcp = anamnesis
    a = await call_tool(mcp, "assert_claim", {
        "content": "alpha", "type": "empirical", "evidence": [],
    })
    b = await call_tool(mcp, "assert_claim", {
        "content": "beta", "type": "empirical", "evidence": [],
    })
    cid = a["claim_id"]
    await call_tool(mcp, "relate", {
        "from_claim": cid, "to_ref": b["claim_id"],
        "ref_type": "claim", "relation": "supports",
    })
    single = await call_tool(mcp, "get_claim", {"claim_id": cid})
    batch = await call_tool(mcp, "get_claims", {"claim_ids": [cid]})
    bundled = next(
        c for c in batch["claims"] if c["claim"]["id"] == cid
    )
    assert set(single["claim"]) == set(bundled["claim"])
    assert single["outgoing_edges"] == bundled["outgoing_edges"]
    assert single["incoming_edges"] == bundled["incoming_edges"]


# --- correction gate message names the metric --------------------------


async def test_correct_result_message_names_metric(tmp_path):
    """The refusal must interpolate the real primary metric in the
    clause that mentions recursive_gain — not leave the literal
    ambiguous next to 'the scored metric'."""
    from ml_arete_mcp.state.store import ImproverStore
    from ml_arete_mcp.server import create_server

    store = ImproverStore(str(tmp_path / "i.db"))
    store.connect()
    try:
        mcp = create_server(store)
        contract = await call_tool(mcp, "create_meta_contract", {
            "metrics": {"primary_metric": "hits", "direction": "max"},
            "promotion_policy": {
                "min_gain": 1.0,
                "sesoi_d": 4.0, "target_power": 0.8,
                "min_evidence_rung": "not_worth",
            },
        })
        store.conn.executescript("""
        INSERT INTO improver_versions
            (id, parent_id, proposal_id, code_artifact_digest,
             model_ref, capability_profile_json, is_champion,
             created_at)
          VALUES ('imp-p', NULL, NULL, 'd', 'm', '{}', 1, '2026-01-01'),
                 ('imp-c', 'imp-p', NULL, 'd', 'm', '{}', 0,
                  '2026-01-01');
        """)
        assert "error" not in contract, contract
        tourn = await call_tool(mcp, "open_tournament", {
            "contract_id": contract["contract_id"],
            "parent_improver_id": "imp-p",
            "candidate_improver_id": "imp-c",
            "budget": {"descendant_runs": 1},
            "seeds": [1],
            "allow_underpowered": True,
        })
        assert "error" not in tourn, tourn
        res = await call_tool(mcp, "record_tournament_result", {
            "tournament_id": tourn["tournament_id"],
            "arm": "candidate",
            "descendant_spec": {},
            "metrics": {"hits": 3, "seed": 1},
        })
        rid = res["result_id"]
        bad = await call_tool(mcp, "correct_tournament_result", {
            "result_id": rid,
            "reason": "test",
            "metrics": {"other": 1},
        })
        assert "error" in bad
        msg = bad["error"]
        assert "'hits'" in msg
        # The clause mentioning recursive_gain names the metric too.
        assert "recursive_gain is computed over 'hits'" in msg
    finally:
        store.close()
