"""rc-12 regressions — plan-20260929-2344Z work items as tests.

The VM extraction (rc-12 battery, 20260929T2315Z) measured every one
of these failing on the deployed image; each test pins a fix against
drift. Items:

W1  'read_' in READ_PREFIXES on all four gated servers — resource
    reads must never sit behind write gates.
W2  list_claims projects confidence_basis — the oracle-bound label
    exists only if the listing carries it.
W3  null computed_rung collapses the verdict ceiling to the declared
    rung — unmeasured work cannot claim a stronger rung.
W4  spawn-scope: a programme spawn-bound to another campaign cannot
    be reported here, and the audit check catches the breach.
W6  undeclared arguments are refused at dispatch, not dropped.
W8  p-value underflow persists NULL, keeping bf_2ln.
W9  assert_claim honours valid_from.
W10 X://constants serves the live registry on all five servers.
W11 /hypothesis/{id} renders the full statement (8192 cap + marker).
W12 status digests carry the build stamp.
"""

import json

import pytest


# ---------------------------------------------------------------- W1

def test_read_prefixes_exempt_reads_all_gated_servers():
    from ml_anamnesis_mcp.enforcement import recurrence as r_claims
    from ml_episteme_mcp.enforcement import recurrence as r_l0
    from ml_zetesis_mcp.enforcement import recurrence as r_l1
    from ml_arete_mcp.enforcement import recurrence as r_l2

    for mod in (r_claims, r_l0, r_l1, r_l2):
        assert "read_" in mod.READ_PREFIXES, mod.__name__


# ------------------------------------------------- W2 / W9 (claims)

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


async def _call(mcp, name, args):
    result = await mcp.call_tool(name, args)
    text = result.content[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"error": text}


async def test_list_claims_projects_confidence_basis(claims_server):
    r = await _call(claims_server, "assert_claim", {
        "content": "grounded probe", "type": "empirical",
        "confidence": 0.3, "confidence_basis": "weakly_grounded",
    })
    assert "error" not in r, r
    cid = r["claim_id"]

    got = await _call(claims_server, "get_claim", {"claim_id": cid})
    assert got["claim"]["confidence_basis"] == "weakly_grounded"

    listed = await _call(claims_server, "list_claims", {})
    row = next(c for c in listed["claims"] if c["id"] == cid)
    # rc-12 W2: the listing dropped the label get_claim carried.
    assert row["confidence_basis"] == "weakly_grounded"


async def test_assert_claim_honours_valid_from(claims_server):
    r = await _call(claims_server, "assert_claim", {
        "content": "dated claim", "type": "empirical",
        "confidence": 0.3,
        "valid_from": "2026-01-15T12:00:00+00:00",
    })
    assert "error" not in r, r
    got = await _call(claims_server, "get_claim", {
        "claim_id": r["claim_id"]})
    assert got["claim"]["valid_from"].startswith("2026-01-15")

    # Naive timestamps are refused — validity is a UTC statement.
    r = await _call(claims_server, "assert_claim", {
        "content": "naive", "type": "empirical",
        "confidence": 0.3, "valid_from": "2026-01-15 12:00:00",
    })
    assert "error" in r, r

    # valid_from must not postdate valid_until.
    r = await _call(claims_server, "assert_claim", {
        "content": "inverted", "type": "empirical",
        "confidence": 0.3,
        "valid_from": "2026-02-01T00:00:00Z",
        "valid_until": "2026-01-01T00:00:00Z",
    })
    assert "error" in r, r


# ------------------------------------------------------- W4 (spawn)

async def test_spawn_scope_blocks_cross_campaign_result(tmp_path):
    """A programme bound to campaign A cannot be reported under
    campaign B — on the write gate AND on the audit check."""
    from ml_zetesis_mcp.enforcement.checks import (
        check_spawn_scoped_result)
    from ml_zetesis_mcp.integrity.checks import run_checks
    from ml_zetesis_mcp.state.models import (
        CampaignArm, CampaignResult, CampaignSpawn,
        PromotionCampaign)
    from ml_zetesis_mcp.state.store import SearchStore

    store = SearchStore(str(tmp_path / "search.db"))
    store.connect()
    try:
        store.create_campaign(PromotionCampaign(
            id="camp-a", contract_id="c", champion_id="x",
            challenger_id="y", primary_metric="hits",
            budget={"programmes_per_arm": 1,
                    "trials_per_programme": 2}))
        store.create_campaign_spawn(CampaignSpawn(
            id="spawn-a1", campaign_id="camp-a",
            arm=CampaignArm.champion, programme_id="prog-bound"))
        # camp-b has NO spawns — the old early-exit let this through.
        store.create_campaign(PromotionCampaign(
            id="camp-b", contract_id="c", champion_id="x",
            challenger_id="y", primary_metric="hits"))

        # Write gate: spawn-bound elsewhere refuses even on a
        # spawn-less campaign.
        err = check_spawn_scoped_result(
            store, "camp-b", "champion", "prog-bound")
        assert err is not None and "camp-a" in err

        # Unspawned programme on a spawn-less campaign stays legal
        # (client-driven mode).
        assert check_spawn_scoped_result(
            store, "camp-b", "champion", "prog-free") is None

        # Audit check catches the breach after the fact.
        store.create_campaign_result(CampaignResult(
            id="res-x", campaign_id="camp-b",
            arm=CampaignArm.champion, programme_id="prog-bound",
            metrics={"hits": 1}))
        payload = await run_checks(store, connectivity=None)
        chk = next(c for c in payload["checks"]
                   if c["name"] == "results_spawn_scoped")
        assert chk["ok"] is False
        assert any(v["result_id"] == "res-x"
                   for v in chk["violations"])
    finally:
        store.close()


# --------------------------------------------- W6 (strict extras)

async def test_undeclared_args_refused_on_gated_dispatch(tmp_path):
    """Every gated server refuses unknown keys instead of dropping
    them into extra=ignore."""
    from ml_anamnesis_mcp.server import create_server as mk_claims
    from ml_anamnesis_mcp.state.store import MemoryStore
    from ml_episteme_mcp.server import create_server as mk_l0
    from ml_episteme_mcp.state.store import StateStore
    from ml_zetesis_mcp.server import create_server as mk_l1
    from ml_zetesis_mcp.state.store import SearchStore

    cases = []
    stores = []
    for mk, store in (
        (mk_claims, MemoryStore(str(tmp_path / "m.db"))),
        (mk_l0, StateStore(str(tmp_path / "s.db"))),
        (mk_l1, SearchStore(str(tmp_path / "z.db"))),
    ):
        store.connect()
        stores.append(store)
        cases.append(mk(
            store,
            enforcement_config={"status_freshness_seconds": 0}))

    try:
        for mcp, tool, args in (
            (cases[0], "list_claims", {"typo_arg": 1}),
            (cases[1], "list_programmes", {"typo_arg": 1}),
            (cases[2], "list_investigations", {"typo_arg": 1}),
        ):
            r = await _call(mcp, tool, args)
            assert "error" in r, (tool, r)
            assert "typo_arg" in r["error"], r["error"]
    finally:
        for s in stores:
            s.close()


# -------------------------------------------------------- W8 (p→0)

def test_p_value_underflow_persists_null():
    """Extreme z underflows 1−Φ(z) to 0.0 — that is a float artefact,
    not a measured zero. The record must keep NULL p and real bf_2ln."""
    from ml_zetesis_mcp import _grounded_constants as _gc

    ev = _gc.arm_evidence(
        [0.0, 0.1, -0.1, 0.05, -0.05],
        [50.0, 50.1, 49.9, 50.05, 49.95])
    assert ev is not None
    assert ev["p"] is None            # underflow → NULL, not 0.0
    assert ev["bf_2ln"] > 0           # the statistic survives
    assert ev["computed_rung"] is not None

    # n < 2 per arm honestly has no statistic at all.
    assert _gc.arm_evidence([1.0], [2.0]) is None
    # Identical arms: zero pooled variance → no statistic.
    assert _gc.arm_evidence([1.0, 1.0], [1.0, 1.0]) is None


# -------------------------------------------- W10 (X://constants)

async def test_constants_resource_serves_live_registry_all_servers(
        tmp_path):
    """All five servers expose X://constants — the disclosure is the
    running module's registry, not a copied file."""
    from mcp.client import Client

    from ml_agora_mcp.clients.adaptors import Adaptors as AgoraAd
    from ml_agora_mcp.server import create_server as mk_agora
    from ml_anamnesis_mcp.server import create_server as mk_claims
    from ml_anamnesis_mcp.state.store import MemoryStore
    from ml_arete_mcp.server import create_server as mk_l2
    from ml_arete_mcp.state.store import ImproverStore
    from ml_episteme_mcp.server import create_server as mk_l0
    from ml_episteme_mcp.state.store import StateStore
    from ml_zetesis_mcp.server import create_server as mk_l1
    from ml_zetesis_mcp.state.store import SearchStore
    from ml_anamnesis_mcp import _grounded_constants as canon

    stores = [
        MemoryStore(str(tmp_path / "m.db")),
        StateStore(str(tmp_path / "s.db")),
        SearchStore(str(tmp_path / "z.db")),
        ImproverStore(str(tmp_path / "a.db")),
    ]
    for s in stores:
        s.connect()

    servers = [
        ("claims://constants", mk_claims(stores[0])),
        ("protocol://constants", mk_l0(stores[1])),
        ("search://constants", mk_l1(stores[2])),
        ("improver://constants", mk_l2(stores[3])),
        ("lab://constants",
         mk_agora(AgoraAd(), log_dir=tmp_path / "logs")),
    ]
    expected_names = set(canon.REGISTRY)

    try:
        for uri, mcp in servers:
            async with Client(mcp) as client:
                res = await client.read_resource(uri)
            d = json.loads(res.contents[0].text)
            names = {c["name"] for c in d["constants"]}
            assert names == expected_names, uri
            # Provenance travels with the disclosure.
            for c in d["constants"]:
                assert "citation" in c and "status" in c, (uri, c)
    finally:
        for s in stores:
            s.close()


# -------------------------------------------- W11 (hypothesis page)

def test_hypothesis_detail_caps_at_display_max(tmp_path):
    from ml_episteme_mcp.observability.views.programme import (
        HYPOTHESIS_DISPLAY_MAX, render_hypothesis_detail)
    from ml_episteme_mcp.state.models import Hypothesis, Programme
    from ml_episteme_mcp.state.store import StateStore

    store = StateStore(str(tmp_path / "state.db"))
    store.connect()
    try:
        store.create_programme(Programme(
            id="prog-x", goal="g", constraints={},
            allowed_variables=["depth", "v"],
            budget_max_trials=10, budget_max_wall_time_hours=1.0))
        long_stmt = "depth improves generalization. " * 400  # ~12k chars
        store.create_hypothesis(Hypothesis(
            id="hyp-long", programme_id="prog-x",
            statement=long_stmt,
            failure_criterion="acc does not improve",
            variables_involved=["depth"]))

        res = render_hypothesis_detail(store, "hyp-long")
        assert res.status_code == 200
        assert "[truncated at 8192 characters]" in res.body.decode()
        assert HYPOTHESIS_DISPLAY_MAX == 8192

        store.create_hypothesis(Hypothesis(
            id="hyp-short", programme_id="prog-x",
            statement="short statement",
            failure_criterion="f",
            variables_involved=["v"]))
        res = render_hypothesis_detail(store, "hyp-short")
        body = res.body.decode()
        assert "short statement" in body
        assert "truncated" not in body

        res = render_hypothesis_detail(store, "hyp-ghost")
        assert res.status_code == 404
    finally:
        store.close()


# --------------------------------------------- W12 (build stamp)

async def test_status_digests_carry_build_key(tmp_path):
    """Every server's status digest exposes 'build' — a dev checkout
    reports None honestly; a packaged image carries the rev."""
    from mcp.client import Client

    from ml_anamnesis_mcp.server import create_server
    from ml_anamnesis_mcp.state.store import MemoryStore

    store = MemoryStore(str(tmp_path / "m.db"))
    store.connect()
    try:
        mcp = create_server(store)
        async with Client(mcp) as client:
            res = await client.read_resource("claims://status")
        d = json.loads(res.contents[0].text)
        assert "build" in d          # key always present
        if d["build"] is not None:   # stamped image → rev + dirty
            assert isinstance(d["build"]["rev"], str)
            assert "dirty" in d["build"]
    finally:
        store.close()
