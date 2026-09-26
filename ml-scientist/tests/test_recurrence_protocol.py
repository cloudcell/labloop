"""Recurrent self-improvement protocol — plan-20260926-0438Z.

Covers the three mechanisms end-to-end, in-process:

- M1 freshness gate — mutating tools refuse until ``X://status`` (or
  the session digest) has been read within the TTL; read tools and
  the remediation/ack surface are exempt so the gate can never
  strand its own remedy.
- M2 post-injection — successful results carry a compact ``next``
  from the cached digest; failures don't.
- M3 governance-debt gates — open integrity violations and (arete)
  decision debt block mutating tools until acknowledged/adjudicated.

Plus the ancillary items that share the surface: contract-metric
lint, batch get_claims, claims://by-relation, wait_trial, and the
archive-seal advisory.
"""

import json
import tempfile
import time
from pathlib import Path

import pytest

from mcp.server.mcpserver.exceptions import ToolError


async def call_tool(mcp, name: str, args: dict) -> dict:
    """Call a tool in-process and parse the JSON payload."""
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
    """Read a status/session resource in-process (stamps the watermark)."""
    contents = await mcp.read_resource(uri)
    return json.loads(list(contents)[0].content)


def _write_violation_log(store, check_name: str, object_ref: str) -> None:
    """Record a synthetic check run carrying one violation — the
    append-only audit trail the open_violation gate reads."""
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


# ------------------------------------------------------------------
# Episteme — the reference implementation
# ------------------------------------------------------------------


@pytest.fixture
def episteme(tmp_path):
    """Episteme server with the protocol enabled — tmp_path keeps
    <db_dir>/logs/ (the audit trail) isolated per test."""
    from ml_episteme_mcp.state.store import StateStore
    from ml_episteme_mcp.server import create_server

    from ml_episteme_mcp.enforcement import recurrence
    recurrence.TRACKER.reset()  # module-global — isolate the watermark
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


async def test_freshness_gate_refuses_before_read(episteme):
    _, mcp = episteme
    r = await call_tool(mcp, "create_programme", {
        "goal": "g", "constraints": {}, "allowed_variables": ["x"],
        "budget": {"max_trials": 3},
    })
    assert "error" in r and "Stale session" in r["error"]
    assert "protocol://status" in r["error"]


async def test_freshness_gate_allows_after_read(episteme):
    _, mcp = episteme
    await read_status(mcp, "protocol://status")
    r = await call_tool(mcp, "create_programme", {
        "goal": "g", "constraints": {}, "allowed_variables": ["x"],
        "budget": {"max_trials": 3},
    })
    assert "error" not in r, r
    assert r["programme_id"].startswith("prog-")


async def test_freshness_session_resource_also_satisfies(episteme):
    _, mcp = episteme
    await read_status(mcp, "protocol://session")
    r = await call_tool(mcp, "create_programme", {
        "goal": "g", "constraints": {}, "allowed_variables": ["x"],
        "budget": {"max_trials": 3},
    })
    assert "error" not in r


async def test_freshness_ttl_expiry(episteme):
    """A read older than the TTL stops satisfying the duty."""
    from ml_episteme_mcp.enforcement import recurrence

    store, mcp = episteme
    await read_status(mcp, "protocol://status")
    # Backdate the watermark beyond the TTL.
    recurrence.TRACKER.status_read_at = time.time() - 601
    r = await call_tool(mcp, "create_programme", {
        "goal": "g", "constraints": {}, "allowed_variables": ["x"],
        "budget": {"max_trials": 3},
    })
    assert "error" in r and "Stale session" in r["error"]


async def test_gate_disabled_without_config(tmp_path):
    """In-process embedders stay opt-in: no [enforcement] → no gate."""
    from ml_episteme_mcp.state.store import StateStore
    from ml_episteme_mcp.server import create_server

    store = StateStore(str(tmp_path / "s.db"))
    store.connect()
    try:
        mcp = create_server(store)  # no enforcement_config
        r = await call_tool(mcp, "create_programme", {
            "goal": "g", "constraints": {}, "allowed_variables": ["x"],
            "budget": {"max_trials": 3},
        })
        assert "error" not in r, r
    finally:
        store.close()


async def test_read_tools_exempt(episteme):
    _, mcp = episteme
    r = await call_tool(mcp, "list_programmes", {})
    assert "Stale session" not in json.dumps(r)


async def test_check_invariants_exempt(episteme):
    """The audit tool is exempt — the gate can never block the check
    that would surface its own state."""
    _, mcp = episteme
    r = await call_tool(mcp, "check_invariants", {})
    assert "Stale session" not in json.dumps(r)


async def test_next_injection_on_success(episteme):
    """Successful results carry a compact `next` from the cached
    digest — the status read warms it (M2)."""
    _, mcp = episteme
    digest = await read_status(mcp, "protocol://status")
    r = await call_tool(mcp, "create_programme", {
        "goal": "g", "constraints": {}, "allowed_variables": ["x"],
        "budget": {"max_trials": 3},
    })
    assert "error" not in r, r
    assert "next" in r
    top = digest["recommended_next"][0]
    assert r["next"]["action"] == top["action"]
    assert r["next"]["blockers"] == len(digest["blockers"])


async def test_no_injection_on_failure(episteme):
    _, mcp = episteme
    await read_status(mcp, "protocol://status")
    r = await call_tool(mcp, "get_trial_status", {
        "programme_id": "prog-nonexistent", "trial_id": "trial-x",
    })
    assert "error" in r
    assert "next" not in r


async def test_open_violation_gates_and_ack_clears(episteme):
    """A logged, unacknowledged violation blocks mutating tools;
    acknowledge_violation is exempt and clears the gate."""
    store, mcp = episteme
    _write_violation_log(store, "unsealed_execution", "trial-abc123")

    digest = await read_status(mcp, "protocol://status")
    kinds = [b["kind"] for b in digest["blockers"]]
    assert "open_violation" in kinds

    r = await call_tool(mcp, "create_programme", {
        "goal": "g", "constraints": {}, "allowed_variables": ["x"],
        "budget": {"max_trials": 3},
    })
    assert "error" in r and "open integrity violation" in r["error"]

    # The ack tool is exempt even while violations are open.
    ack = await call_tool(mcp, "acknowledge_violation", {
        "check_name": "unsealed_execution",
        "object_ref": "trial-abc123",
        "disposition": "accepted: ran before sealing was enforced",
        "decided_by": "human:test",
    })
    assert ack["status"] == "acknowledged"
    assert ack["matched_open_violation"] is True
    assert ack["open_violations"] == 0

    # The remedy (a mutating tool for this check) now runs.
    r = await call_tool(mcp, "create_programme", {
        "goal": "g", "constraints": {}, "allowed_variables": ["x"],
        "budget": {"max_trials": 3},
    })
    assert "error" not in r


async def test_violation_remedy_tool_exempt(episteme):
    """correct_trial_status is the recorded-remedy path for
    unsealed_execution — the gate must not strand it."""
    store, mcp = episteme
    _write_violation_log(store, "unsealed_execution", "trial-abc123")
    await read_status(mcp, "protocol://status")
    r = await call_tool(mcp, "correct_trial_status", {
        "trial_id": "trial-abc123", "to_status": "failed",
        "reason": "test",
    })
    # Reaches the tool — a not-found error, not a gate refusal.
    assert "open integrity violation" not in json.dumps(r)


async def test_unmatched_ack_still_records(episteme):
    """Acks are insert-only: an unmatched (check, ref) pair records
    honestly rather than fabricating a match."""
    store, mcp = episteme
    _write_violation_log(store, "unsealed_execution", "trial-abc123")
    await read_status(mcp, "protocol://status")
    ack = await call_tool(mcp, "acknowledge_violation", {
        "check_name": "unsealed_execution",
        "object_ref": "trial-DIFFERENT",
        "disposition": "accepted: unrelated record",
        "decided_by": "human:test",
    })
    assert ack["matched_open_violation"] is False
    # The real violation is still open and still gating.
    r = await call_tool(mcp, "create_programme", {
        "goal": "g", "constraints": {}, "allowed_variables": ["x"],
        "budget": {"max_trials": 3},
    })
    assert "open integrity violation" in r["error"]


async def test_archive_seal_advisory(tmp_path):
    """Unsealed archives older than the warn window surface as a
    recommended_next advisory — never a blocker."""
    from ml_episteme_mcp.state.store import StateStore
    from ml_episteme_mcp.resources.status import status_digest

    store = StateStore(str(tmp_path / "s.db"))
    store.connect()
    try:
        store.conn.execute(
            "INSERT INTO archive_registry "
            "(archive_id, archive_path, created_at, batch_size, sealed) "
            "VALUES ('arch-1', '/tmp/a.db', '2020-01-01T00:00:00', 1, 0)"
        )
        store.conn.commit()
        digest = status_digest(store, archive_seal_warn_hours=72)
        seals = [
            r for r in digest["recommended_next"]
            if r["action"] == "seal_archive"
        ]
        assert seals and seals[0]["entity_refs"] == ["arch-1"]
        assert not any(b["kind"] == "seal" for b in digest["blockers"])
    finally:
        store.close()


async def test_wait_trial_returns_terminal(tmp_path):
    """wait_trial on an already-terminal trial returns immediately
    with the status payload plus wait metadata."""
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
    cancelled = await call_tool(mcp, "cancel_trial", {
        "programme_id": prog["programme_id"],
        "trial_id": trial["trial_id"],
    })
    assert cancelled["status"] == "abandoned"
    r = await call_tool(mcp, "wait_trial", {
        "programme_id": prog["programme_id"],
        "trial_id": trial["trial_id"],
        "timeout_seconds": 5, "poll_seconds": 0.5,
    })
    assert r["status"] == "abandoned"
    assert r["timed_out"] is False


# ------------------------------------------------------------------
# Arete — decision debt + improvement epoch
# ------------------------------------------------------------------


@pytest.fixture
def arete(tmp_path):
    from ml_arete_mcp.enforcement import recurrence
    from ml_arete_mcp.state.store import ImproverStore
    from ml_arete_mcp.server import create_server

    recurrence.TRACKER.reset()
    store = ImproverStore(str(tmp_path / "improver.db"))
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


def _seed_closed_tournament(store, tourn_id: str = "t-debt") -> None:
    """A closed tournament with no linked meta_decision — the debt
    row the gate reads."""
    store.conn.executescript(f"""
    INSERT INTO improver_versions
        (id, parent_id, proposal_id, code_artifact_digest, model_ref,
         capability_profile_json, is_champion, created_at)
      VALUES ('imp-p', NULL, NULL, 'd', 'm', '{{}}', 1, '2026-01-01'),
             ('imp-c', 'imp-p', NULL, 'd', 'm', '{{}}', 0, '2026-01-01');
    INSERT INTO meta_contracts
        (id, version, metrics_json, promotion_policy_json, created_at)
      VALUES ('mc-1', 1, '{{}}', '{{}}', '2026-01-01');
    INSERT INTO tournaments
        (id, contract_id, parent_improver_id, candidate_improver_id,
         budget_json, status, recursive_gain, created_at, closed_at)
      VALUES ('{tourn_id}', 'mc-1', 'imp-p', 'imp-c', '{{}}',
              'closed', 2.0, '2026-01-01', '2026-01-02');
    """)


async def test_decision_debt_blocks_mutations(arete):
    store, mcp = arete
    _seed_closed_tournament(store)
    digest = await read_status(mcp, "improver://status")
    kinds = [b["kind"] for b in digest["blockers"]]
    assert "decision_debt" in kinds

    r = await call_tool(mcp, "register_improver", {
        "code_artifact_digest": "sha256:" + "cd" * 32,
        "model_ref": "m", "capability_profile": {},
    })
    assert "error" in r and "meta_decision" in r["error"]


async def test_linked_hold_clears_debt(arete):
    """A hold verdict linked by tournament_id is a legitimate
    resolution — the gate must not strand it."""
    store, mcp = arete
    _seed_closed_tournament(store)
    await read_status(mcp, "improver://status")

    # record_meta_decision is exempt — the debt can never block its
    # own remedy.
    dec = await call_tool(mcp, "record_meta_decision", {
        "candidate_improver_id": "imp-c",
        "verdict": "hold",
        "rationale": "awaiting holdout replication",
        "decided_by": "human:pi",
        "evidence_refs": [],
        "tournament_id": "t-debt",
    })
    # Exempt means it reaches the tool — which may still refuse for
    # domain reasons (e.g. evidence requirements), but never with the
    # debt gate's own message.
    assert "closed tournament" not in json.dumps(dec)

    # Directly assert the SQL-level invariant: a linked decision row
    # removes the tournament from the debt set.
    from ml_arete_mcp.enforcement import recurrence
    store.conn.execute(
        "INSERT INTO meta_decisions "
        "(id, candidate_improver_id, tournament_id, verdict, "
        " rationale, decided_by, evidence_refs_json, created_at) "
        "VALUES ('mdec-1','imp-c','t-debt','hold','wait','human:x',"
        "        '[]','2026-01-03')"
    )
    store.conn.commit()
    assert "t-debt" not in recurrence.undecided_tournaments(store)


async def test_unlinked_decision_does_not_clear(arete):
    """An unlinked decision satisfies nothing — the NOT EXISTS join
    requires tournament_id."""
    from ml_arete_mcp.enforcement import recurrence
    store, _ = arete
    _seed_closed_tournament(store)
    store.conn.execute(
        "INSERT INTO meta_decisions "
        "(id, candidate_improver_id, tournament_id, verdict, "
        " rationale, decided_by, evidence_refs_json, created_at) "
        "VALUES ('mdec-2','imp-c',NULL,'hold','wait','human:x',"
        "        '[]','2026-01-03')"
    )
    store.conn.commit()
    assert "t-debt" in recurrence.undecided_tournaments(store)


async def test_rollback_exempt_from_debt(arete):
    store, mcp = arete
    _seed_closed_tournament(store)
    await read_status(mcp, "improver://status")
    r = await call_tool(mcp, "rollback", {
        "candidate_improver_id": "imp-c",
        "rationale": "x", "decided_by": "human:x",
        "evidence_refs": [],
    })
    # Reaches the tool (domain error possible) — never debt-blocked.
    assert "closed tournament" not in json.dumps(r)


async def test_improvement_due_advisory(arete):
    """The epoch produces a tier-2 recommended action, not a blocker —
    recurrence is a duty, not a fault."""
    from ml_arete_mcp.enforcement import recurrence
    store, mcp = arete
    recurrence.configure_epoch(0)  # zero epoch → always due
    try:
        digest = await read_status(mcp, "improver://status")
        actions = [r["action"] for r in digest["recommended_next"]]
        assert "propose_meta_change" in actions
        assert not any(
            b.get("kind") == "improvement_due" for b in digest["blockers"]
        )
    finally:
        recurrence.configure_epoch(86400)


async def test_contract_metric_lint(tmp_path):
    """Near-spelling a prior contract's metric warns — advisory,
    never a refusal (the drift pattern from the field report)."""
    from ml_arete_mcp.state.store import ImproverStore
    from ml_arete_mcp.server import create_server

    store = ImproverStore(str(tmp_path / "i.db"))
    store.connect()
    try:
        mcp = create_server(store)
        await call_tool(mcp, "create_meta_contract", {
            "metrics": {"primary_metric": "min_accuracy", "direction": "max"},
            "promotion_policy": {"min_gain": 1.0},
        })
        r = await call_tool(mcp, "create_meta_contract", {
            "metrics": {"primary_metric": "min_accuracy_v2"},
            "promotion_policy": {"min_gain": 1.0},
        })
        assert "error" not in r
        assert r.get("lint"), "expected a drift lint on near-spelling"
        assert "metric_alias" in r["lint"][0]
        # A genuinely new metric produces no lint.
        r2 = await call_tool(mcp, "create_meta_contract", {
            "metrics": {"primary_metric": "totally_different_xyz"},
            "promotion_policy": {"min_gain": 1.0},
        })
        assert not r2.get("lint")
    finally:
        store.close()


# ------------------------------------------------------------------
# Anamnesis — batch claims + by-relation resource
# ------------------------------------------------------------------


@pytest.fixture
def anamnesis(tmp_path):
    from ml_anamnesis_mcp.state.store import MemoryStore
    from ml_anamnesis_mcp.server import create_server

    store = MemoryStore(str(tmp_path / "memory.db"))
    store.connect()
    mcp = create_server(store)
    yield store, mcp
    store.close()


async def _mint_claim(mcp, content: str, evidence) -> str:
    r = await call_tool(mcp, "assert_claim", {
        "content": content, "type": "empirical", "confidence": 0.2,
        "evidence": evidence,
    })
    assert "error" not in r, r
    return r["claim_id"]


async def test_get_claims_batch(anamnesis):
    _, mcp = anamnesis
    a = await _mint_claim(mcp, "claim alpha", [])
    b = await _mint_claim(mcp, "claim beta", [])
    r = await call_tool(mcp, "get_claims", {"claim_ids": [a, b, "claim-none"]})
    assert "error" not in r
    ids = {c["claim"]["id"] for c in r["claims"]}
    assert ids == {a, b}
    assert r["missing"] == ["claim-none"]


async def test_by_relation_resource(anamnesis):
    _, mcp = anamnesis
    a = await _mint_claim(mcp, "claim x", [])
    b = await _mint_claim(mcp, "claim y", [])
    await call_tool(mcp, "relate", {
        "from_claim": a, "to_ref": b, "ref_type": "claim",
        "relation": "contradicts", "weight": 0.9,
    })
    res = await mcp.read_resource("claims://by-relation/contradicts")
    payload = json.loads(list(res)[0].content)
    assert payload["relation"] == "contradicts"
    assert any(
        e["from_claim"] == a and e["to_ref"] == b
        for e in payload["edges"]
    )
    assert a in {c["id"] for c in payload["claims"]}


async def test_anamnesis_freshness_gate(tmp_path):
    """The gate is lab-wide — anamnesis mutating tools answer to it."""
    from ml_anamnesis_mcp.enforcement import recurrence
    from ml_anamnesis_mcp.state.store import MemoryStore
    from ml_anamnesis_mcp.server import create_server

    recurrence.TRACKER.reset()
    store = MemoryStore(str(tmp_path / "m.db"))
    store.connect()
    try:
        mcp = create_server(
            store,
            enforcement_config={"status_freshness_seconds": 600},
        )
        r = await call_tool(mcp, "assert_claim", {
            "content": "c", "type": "empirical", "confidence": 0.2,
        })
        assert "Stale session" in r["error"]
        await read_status(mcp, "claims://status")
        r = await call_tool(mcp, "assert_claim", {
            "content": "c", "type": "empirical", "confidence": 0.2,
        })
        assert "error" not in r
        assert "next" in r
    finally:
        store.close()


# ------------------------------------------------------------------
# Zetesis — the gate is uniform across stateful servers
# ------------------------------------------------------------------


async def test_zetesis_freshness_gate(tmp_path):
    from ml_zetesis_mcp.enforcement import recurrence
    from ml_zetesis_mcp.state.store import SearchStore
    from ml_zetesis_mcp.server import create_server

    recurrence.TRACKER.reset()
    store = SearchStore(str(tmp_path / "search.db"))
    store.connect()
    try:
        mcp = create_server(
            store,
            enforcement_config={"status_freshness_seconds": 600},
        )
        r = await call_tool(mcp, "open_investigation", {
            "question": "q?", "scope": {},
        })
        assert "Stale session" in r["error"]
        assert "search://status" in r["error"]
        await read_status(mcp, "search://status")
        r = await call_tool(mcp, "open_investigation", {
            "question": "q?", "scope": {},
        })
        assert "Stale session" not in json.dumps(r)
    finally:
        store.close()


# ------------------------------------------------------------------
# Agora — blocker pass-through (read-only, never re-labeled)
# ------------------------------------------------------------------


def test_agora_blocker_passthrough():
    """Governance-debt blockers keep their own action/tool/kind —
    agora must never re-label them as channel failures."""
    from ml_agora_mcp.resources.status import _lab_next_actions

    servers = {
        "loop2": {
            "status": "ok",
            "digest": {
                "server": "ml-arete-mcp",
                "blockers": [{
                    "kind": "decision_debt",
                    "tournament_id": "t-abc",
                    "action": "record_meta_decision",
                    "tool": "record_meta_decision",
                    "blocks": ["*"],
                    "detail": "closed tournament lacks a meta_decision",
                }],
            },
        },
        "loop0": {
            "status": "ok",
            "digest": {
                "server": "ml-episteme-mcp",
                "blockers": [{
                    "kind": "upstream_down",
                    "channel": "executor",
                    "role": "executor",
                    "blocks": ["run_trial"],
                    "detail": "connection refused",
                }],
            },
        },
    }
    actions = _lab_next_actions(servers)
    by_server = {a["server"]: a for a in actions}

    ag = by_server["ml-arete-mcp"]
    assert ag["action"] == "record_meta_decision"
    assert ag["tool"] == "record_meta_decision"
    assert ag["kind"] == "decision_debt"
    assert ag["entity_refs"] == ["t-abc"]

    ep = by_server["ml-episteme-mcp"]
    assert ep["action"] == "restore_channel"
    assert ep["tool"] is None
