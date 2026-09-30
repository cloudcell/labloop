"""rc-13 regressions — plan-20260930-0610Z work items as tests.

The VM extraction (rc-13 battery, 20260930T0525Z) measured every one
of these on the deployed image; each test pins the fix against drift.

W1  correct_tournament_result carries the primary-metric gate — a
    correction that drops the scored metric is a laundering path.
W2  campaign_arm is required on spawn_arm_programme/record_arm_result
    (the axes are orthogonal: arm picks the campaign, campaign_arm
    picks the side — no derivation is possible).
W3  pull_arm_evidence mirrors the upstream enum — source='loop1' is a
    dead branch that must refuse naming the accepted set.
W4  pull_arm_evidence returns the upstream ref as evidence_ref_id and
    the local citation handle as wrapper_ref_id.
W5  get_tournament projects linked arm campaigns (best-effort live
    status; a dead peer must not break the read).
W6  the powered-policy teaching message names optional 'prior' in all
    THREE copies of check_promotion_policy_power.
W7  get_improver_lineage accepts a depth walk cap.
W8  the result read row carries corrections_count beside the array.
W9  list_claims projects source_id + valid_from — the same
    read-one/read-many parity class as rc-12 W2.
W10 create_meta_contract dedups byte-identical creates.
W15 create_evaluation_contract refuses a metrics object whose
    direction can never resolve — the spawn-side check gated at mint.
W16 refresh_roster's reconciled is a typed shape, not an opaque dict.
"""

import json

import pytest

from mcp.server.mcpserver.exceptions import ToolError


# ------------------------------------------------------- W6 (prior)

def test_powered_policy_message_names_prior_all_servers():
    """The three copies of the powered-policy gate must each teach the
    optional 'prior' slot — the message is where a caller learns the
    schema, and the rc-13 agent concluded the slot did not exist."""
    from ml_episteme_mcp.enforcement.commitments import (
        check_promotion_policy_power as l0)
    from ml_zetesis_mcp.enforcement.checks import (
        check_promotion_policy_power as l1)
    from ml_arete_mcp.enforcement.checks import (
        check_promotion_policy_power as l2)

    for fn in (l0, l1, l2):
        msg = fn({})  # missing all required keys → teaching message
        assert msg is not None
        assert "'prior'" in msg, (fn.__module__, msg)
        assert "sesoi_d" in msg, (fn.__module__, msg)


# ------------------------------------------- W9 (claims projection)

@pytest.fixture
def claims_server(tmp_path):
    from ml_anamnesis_mcp.server import create_server
    from ml_anamnesis_mcp.state.store import MemoryStore

    store = MemoryStore(str(tmp_path / "memory.db"))
    store.connect()
    mcp = create_server(
        store, enforcement_config={"recurrent_protocol": False})
    yield mcp
    store.close()


async def test_list_claims_projects_source_and_valid_from(claims_server):
    r = await _call(claims_server, "assert_claim", {
        "content": "provenance probe", "type": "empirical",
        "source_id": "trial-src1",
        "valid_from": "2026-01-15T12:00:00Z",
    })
    assert "error" not in r, r
    cid = r["claim_id"]

    listed = await _call(claims_server, "list_claims", {})
    row = next(c for c in listed["claims"] if c["id"] == cid)
    # rc-13 F9: get_claim carried both; list_claims dropped them.
    assert row["source_id"] == "trial-src1"
    assert row["valid_from"].startswith("2026-01-15")


# -------------------------------------- W15 (contract metrics gate)

@pytest.fixture
def episteme_server(tmp_path):
    from ml_episteme_mcp.server import create_server
    from ml_episteme_mcp.state.store import StateStore

    store = StateStore(str(tmp_path / "state.db"))
    store.connect()
    mcp = create_server(store)
    yield mcp
    store.close()


async def _programme(mcp):
    r = await _call(mcp, "create_programme", {
        "goal": "probe", "constraints": {},
        "allowed_variables": ["lr"],
        "budget": {"max_trials": 4},
    })
    assert "error" not in r, r
    return r["programme_id"]


_POLICY = {
    "sesoi_d": 0.5, "target_power": 0.8,
    "min_evidence_rung": "not_worth",
}


async def test_contract_metrics_gate(episteme_server):
    """A contract that can never resolve a direction refuses at mint —
    previously it minted cleanly and failed only at spawn."""
    prog = await _programme(episteme_server)

    # primary_metric declared but no direction anywhere → refused.
    r = await _call(episteme_server, "create_evaluation_contract", {
        "programme_id": prog,
        "metrics": {"primary_metric": "acc"},
        "promotion_policy": _POLICY,
    })
    assert "error" in r, r
    assert "direction" in r["error"], r["error"]

    # An unresolvable direction value → refused.
    r = await _call(episteme_server, "create_evaluation_contract", {
        "programme_id": prog,
        "metrics": {"primary_metric": "acc", "direction": "sideways"},
        "promotion_policy": _POLICY,
    })
    assert "error" in r, r
    assert "direction" in r["error"], r["error"]

    # No metrics at all → refused.
    r = await _call(episteme_server, "create_evaluation_contract", {
        "programme_id": prog,
        "metrics": {},
        "promotion_policy": _POLICY,
    })
    assert "error" in r, r

    # Both legal shapes mint: explicit direction key …
    r = await _call(episteme_server, "create_evaluation_contract", {
        "programme_id": prog,
        "metrics": {"primary_metric": "acc", "direction": "max"},
        "promotion_policy": _POLICY,
    })
    assert "error" not in r, r
    # … and the name→direction map form.
    r = await _call(episteme_server, "create_evaluation_contract", {
        "programme_id": prog,
        "metrics": {"acc": "maximize"},
        "promotion_policy": _POLICY,
    })
    assert "error" not in r, r


# ---------------------------------------- W16 (reconciled typing)

def test_refresh_roster_reconciled_is_typed():
    """The schema is the only contract a schema-only reader sees —
    reconciled must expose its four keys, not hide behind dict."""
    from ml_zetesis_mcp.tools.schemas import (
        RefreshRosterOut, RosterReconciled)

    keys = set(RosterReconciled.__annotations__)
    assert keys == {"demoted", "champion", "rolled_back", "unrolled"}
    assert RefreshRosterOut.__annotations__["reconciled"] is not dict


# ============================ arete-scoped items ============================
# Inline fixtures — kept out of tests/arete/ so this file has no
# conftest dependency (root conftest applies everywhere).

class _FakeUpstream:
    """Duck-typed upstream read adaptor — canned payloads."""

    def __init__(self, payloads: dict | None = None):
        self.payloads = payloads or {}
        self.calls: list[tuple[str, dict]] = []

    async def pull(self, tool: str, args: dict):
        self.calls.append((tool, args))
        return self.payloads.get(
            tool, json.dumps({"ok": True, "tool": tool}))


class _FakeOrchestration:
    """Duck-typed loop1_orchestration channel — records pushes and
    answers the campaign verbs with canned upstream payloads."""

    def __init__(self):
        self.pushed: list[tuple[str, dict]] = []
        self._camp_n = 0

    async def push(self, tool: str, args: dict):
        self.pushed.append((tool, args))
        if tool == "open_campaign":
            self._camp_n += 1
            return json.dumps({
                "campaign_id": f"camp-up{self._camp_n}",
                "status": "open",
            })
        if tool == "pull_campaign_evidence":
            return json.dumps({
                "evidence_ref_id": "eref-up1",
                "ref_ids": ["camp-up1"],
                "result": {"ok": True},
            })
        return json.dumps({"ok": True, "tool": tool})


@pytest.fixture
def arete(tmp_path):
    """In-process arete server with fake upstream channels."""
    from ml_arete_mcp.clients.adaptors import Adaptors
    from ml_arete_mcp.server import create_server
    from ml_arete_mcp.state.store import ImproverStore

    store = ImproverStore(str(tmp_path / "improver.db"))
    store.connect()
    adaptors = Adaptors()
    # register_improver verifies code_artifact_digest through loop0's
    # describe_blob — resolve any well-formed digest.
    adaptors.loop0 = _FakeUpstream(payloads={
        "describe_blob": json.dumps({
            "exists": True, "resolved_in": ["artifact_files"],
        }),
    })
    adaptors.loop1 = _FakeUpstream()
    adaptors.loop1_orchestration = _FakeOrchestration()
    mcp = create_server(store, adaptors=adaptors)
    yield mcp, adaptors, store
    store.close()


async def _call(mcp, name, args):
    try:
        result = await mcp.call_tool(name, args)
    except ToolError as exc:
        # Schema validation (missing required arg, bad Literal) raises
        # in-process.
        return {"error": str(exc)}
    text = result.content[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"error": text}


async def _register_imp(mcp, **overrides) -> str:
    args = {
        "code_artifact_digest": "sha256:" + "ab" * 32,
        "model_ref": "gpt-nano-v1",
        "capability_profile": {"can_propose": True},
    }
    args.update(overrides)
    result = await _call(mcp, "register_improver", args)
    assert "error" not in result, f"register_improver failed: {result}"
    return result["improver_id"]


_POLICY = {
    "sesoi_d": 0.5, "target_power": 0.8,
    "min_evidence_rung": "not_worth",
}
_CONTRACT_ARGS = {
    "metrics": {"primary_metric": "hits", "direction": "max"},
    "promotion_policy": {
        "min_gain": 1.05, "confidence": 0.95,
        "sesoi_d": 4.0, "target_power": 0.8,
        "min_evidence_rung": "not_worth",
    },
}


async def _make_contract(mcp, **overrides) -> str:
    args = dict(_CONTRACT_ARGS)
    args.update(overrides)
    result = await _call(mcp, "create_meta_contract", args)
    assert "error" not in result, f"create_meta_contract failed: {result}"
    return result["contract_id"]


async def _open_tournament(mcp):
    """Genesis + child improver, admitted proposal, open tournament."""
    parent = await _register_imp(mcp)
    child = await _register_imp(mcp, parent_id=parent)
    contract = await _make_contract(mcp)
    p = await _call(mcp, "propose_meta_change", {
        "proposer_improver_id": child,
        "spec_delta": {"change": "replace planner", "to": "beam"},
        "class_map": {"planner": "modifiable"},
        "expected_benefit": "better descendants per budget",
        "falsification": "recursive_gain <= 1.0",
        "rollback_plan": "restore prior champion policy",
    })
    assert "error" not in p, p
    r = await _call(mcp, "open_tournament", {
        "contract_id": contract,
        "parent_improver_id": parent,
        "candidate_improver_id": child,
        "budget": {"programmes_per_arm": 2, "trials_per_programme": 3},
        "seeds": [1, 2],
    })
    assert "error" not in r, r
    return r["tournament_id"]


async def _link_arm(mcp, tourn, arm="candidate"):
    r = await _call(mcp, "open_arm_campaign", {
        "tournament_id": tourn, "arm": arm,
        "upstream_contract_id": "contract-up1",
        "challenger_id": f"cand-{arm}",
    })
    assert "error" not in r, r
    return r["campaign_id"]


# ------------------------------------------------------------- W1

async def test_correction_refuses_to_drop_primary_metric(arete):
    mcp, _, _ = arete
    tourn = await _open_tournament(mcp)
    r = await _call(mcp, "record_tournament_result", {
        "tournament_id": tourn, "arm": "candidate",
        "descendant_spec": {"seed": 1},
        "metrics": {"hits": 0.9, "seed": 1},
    })
    assert "error" not in r, r

    # The canary payload the battery demonstrated: drops 'hits'.
    c = await _call(mcp, "correct_tournament_result", {
        "result_id": r["result_id"], "reason": "canonicalise",
        "metrics": {"seed": 1, "score": 0.9},
    })
    assert "error" in c, c
    assert "hits" in c["error"], c["error"]

    # A correction carrying the primary is still legal.
    c = await _call(mcp, "correct_tournament_result", {
        "result_id": r["result_id"], "reason": "canonicalise",
        "metrics": {"hits": 0.91, "seed": 1},
    })
    assert "error" not in c, c


# ------------------------------------------------------------- W2

async def test_campaign_arm_is_required(arete):
    mcp, _, _ = arete
    tourn = await _open_tournament(mcp)
    await _link_arm(mcp, tourn)

    r = await _call(mcp, "spawn_arm_programme", {
        "tournament_id": tourn, "arm": "candidate",
        "goal": "g", "constraints": {}, "allowed_variables": ["lr"],
    })
    assert "error" in r, r
    assert "campaign_arm" in r["error"], r["error"]

    r = await _call(mcp, "record_arm_result", {
        "tournament_id": tourn, "arm": "candidate",
        "programme_id": "prog-x", "metrics": {"hits": 1},
    })
    assert "error" in r, r
    assert "campaign_arm" in r["error"], r["error"]


# ------------------------------------------------------------- W3

async def test_pull_arm_evidence_refuses_loop1(arete):
    mcp, _, _ = arete
    tourn = await _open_tournament(mcp)
    await _link_arm(mcp, tourn)
    r = await _call(mcp, "pull_arm_evidence", {
        "tournament_id": tourn, "arm": "candidate",
        "source": "loop1", "tool": "list_campaigns",
    })
    assert "error" in r, r
    # The refusal names the accepted set, not just the bad value.
    assert "loop0" in r["error"] or "anamnesis" in r["error"], \
        r["error"]


# ------------------------------------------------------------- W4

async def test_pull_arm_evidence_names_which_ref_is_usable(arete):
    """evidence_ref_id is the upstream ref record_arm_verdict accepts;
    wrapper_ref_id is the local citation handle. Before the rename the
    prominent field pointed at the unusable local id — and the lab
    agent picked it."""
    mcp, _, store = arete
    tourn = await _open_tournament(mcp)
    await _link_arm(mcp, tourn)
    r = await _call(mcp, "pull_arm_evidence", {
        "tournament_id": tourn, "arm": "candidate",
        "source": "loop0", "tool": "list_trials",
        "args": {"programme_id": "prog-x"},
    })
    assert "error" not in r, r
    assert r["evidence_ref_id"] == "eref-up1"
    # The local wrapper eref exists, is distinct, and cites upstream.
    refs = store.list_evidence_refs("tournament", tourn)
    assert len(refs) == 1
    assert r["wrapper_ref_id"] == refs[0].id
    assert "eref-up1" in refs[0].ref_ids


# ------------------------------------------------------------- W5

async def test_linked_campaigns_projection(arete):
    mcp, adaptors, _ = arete
    adaptors.loop1.payloads["get_campaign"] = json.dumps({
        "campaign": {
            "id": "camp-up1", "status": "verdicted",
            "promotion_score": 1.31, "decision_id": "decision-up1",
        }})
    tourn = await _open_tournament(mcp)
    camp = await _link_arm(mcp, tourn)

    t = (await _call(mcp, "get_tournament", {
        "tournament_id": tourn}))["tournament"]
    linked = t["linked_campaigns"]
    assert len(linked) == 1
    assert linked[0]["arm"] == "candidate"
    assert linked[0]["campaign_id"] == camp
    assert linked[0]["status"] == "verdicted"
    assert linked[0]["promotion_score"] == 1.31


async def test_linked_campaigns_survive_dead_peer(arete):
    mcp, adaptors, _ = arete
    tourn = await _open_tournament(mcp)
    camp = await _link_arm(mcp, tourn)
    adaptors.loop1 = None  # peer down — the read must not fail
    t = (await _call(mcp, "get_tournament", {
        "tournament_id": tourn}))["tournament"]
    linked = t["linked_campaigns"]
    assert linked[0]["campaign_id"] == camp
    assert linked[0]["status"] is None


# ------------------------------------------------------------- W7

async def test_lineage_depth_cap(arete):
    mcp, _, _ = arete
    genesis = await _register_imp(mcp)
    child = await _register_imp(mcp, parent_id=genesis)
    grandchild = await _register_imp(mcp, parent_id=child)

    full = await _call(mcp, "get_improver_lineage", {
        "improver_id": grandchild})
    assert full["depth"] == 2
    assert len(full["lineage"]) == 3

    capped = await _call(mcp, "get_improver_lineage", {
        "improver_id": grandchild, "depth": 1})
    assert capped["depth"] == 1
    assert [i["id"] for i in capped["lineage"]] == [child, grandchild]

    zero = await _call(mcp, "get_improver_lineage", {
        "improver_id": grandchild, "depth": 0})
    assert [i["id"] for i in zero["lineage"]] == [grandchild]

    bad = await _call(mcp, "get_improver_lineage", {
        "improver_id": grandchild, "depth": -1})
    assert "error" in bad, bad


# ------------------------------------------------------------- W8

async def test_corrections_count_parity(arete):
    mcp, _, _ = arete
    tourn = await _open_tournament(mcp)
    r = await _call(mcp, "record_tournament_result", {
        "tournament_id": tourn, "arm": "candidate",
        "descendant_spec": {"seed": 1},
        "metrics": {"hits": 0.9, "seed": 1},
    })
    await _call(mcp, "correct_tournament_result", {
        "result_id": r["result_id"], "reason": "fix seed",
        "metrics": {"hits": 0.91, "seed": 1},
    })
    t = (await _call(mcp, "get_tournament", {
        "tournament_id": tourn}))["tournament"]
    row = t["results"]["candidate"][0]
    assert row["corrections_count"] == len(row["corrections"]) == 1


# ------------------------------------------------------------ W10

async def test_meta_contract_dedup(arete):
    mcp, _, _ = arete
    r1 = await _call(mcp, "create_meta_contract", dict(_CONTRACT_ARGS))
    r2 = await _call(mcp, "create_meta_contract", dict(_CONTRACT_ARGS))
    assert r2["contract_id"] == r1["contract_id"]
    assert r2["deduplicated"] is True
    assert r2["version"] == r1["version"]

    # A genuinely different contract still mints a new version.
    args = dict(_CONTRACT_ARGS)
    args["promotion_policy"] = dict(
        _CONTRACT_ARGS["promotion_policy"], target_power=0.9)
    r3 = await _call(mcp, "create_meta_contract", args)
    assert r3["contract_id"] != r1["contract_id"]
    assert "deduplicated" not in r3


# -------------------------------------- W11(f) wrong-loop ids

async def test_foreign_prefix_id_says_wrong_loop(episteme_server):
    mcp = episteme_server
    # An arete-minted id routed to a Loop-0 candidate read must say
    # 'wrong loop', not 'Candidate not found'.
    r = await _call(mcp, "get_candidate", {"candidate_id": "imp-deadbeef"})
    assert "error" in r
    assert "ml-arete" in r["error"]
    assert "not found" not in r["error"].lower()

    # A bare unknown id still reads as a missing Loop-0 candidate.
    r = await _call(mcp, "get_candidate", {"candidate_id": "cand-nope"})
    assert "error" in r and "not found" in r["error"].lower()
