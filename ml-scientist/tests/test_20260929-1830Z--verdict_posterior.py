"""Verdict confidence → computed posterior (e-plans 20260929-1642Z,
20260930-0240Z).

The indicted confidence literals are gone — and post-0240Z no number
crosses the wire at all: the mint names a registered derivation in
`confidence_computation` and anamnesis recomputes it (the NAP D-1
oracle posterior bound from the contract's declared prior and the
measured arm statistic), or the claim carries NULL confidence with a
derived basis label. The rung gate stands: promote may claim at most
what the evidence's own upper bound reaches (declared ≤ claimed ≤
computed).

Pins: the 8 transcribed NAP D-1 cells, directional correction (a
losing challenger gets BF = 1, not BF > 1), prior validation and
snapshotting, the computed-rung refusal, the grounded/weakly_grounded/
ungrounded basis on all four mint paths, and legacy-row compatibility.
"""

from __future__ import annotations

import json
import math
import sqlite3

import pytest

from ml_episteme_mcp.server import create_server as _episteme_server
from ml_episteme_mcp.state.models import Programme
from ml_episteme_mcp.state.store import StateStore

from ml_zetesis_mcp.clients.adaptors import Adaptors as _ZAdaptors
from ml_zetesis_mcp.server import create_server as _zetesis_server
from ml_zetesis_mcp.state.models import (
    CampaignStatus,
    DerivedStatus,
    PromotionCampaign,
    RosterEntry,
)
from ml_zetesis_mcp.state.store import SearchStore

from ml_arete_mcp.clients.adaptors import Adaptors as _AAdaptors
from ml_arete_mcp.server import create_server as _arete_server
from ml_arete_mcp.state.models import (
    EvidenceRef,
    ImproverVersion,
    MetaContract,
    Tournament,
)
from ml_arete_mcp.state.store import ImproverStore

import ml_zetesis_mcp._grounded_constants as _gc

# tests/arete and tests/zetesis are packages on sys.path.
from zetesis.conftest import (
    FakeClaimsAdaptor,
    FakePromotionAdaptor,
    FakeUpstreamAdaptor,
)
from arete.conftest import (
    FakeClaimsAdaptor as _AreteClaims,
    FakeUpstreamAdaptor as _AreteUpstream,
)

POWERED = {
    "sesoi_d": 4.0,          # required_n=1 — fixture convenience
    "target_power": 0.8,
    "min_evidence_rung": "not_worth",
}


async def _call(mcp, name: str, args: dict) -> dict:
    result = await mcp.call_tool(name, args)
    text = result.content[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"error": text}


# ---------------------------------------------------------------------------
# Registry procedures — NAP D-1 pins + directional correction
# ---------------------------------------------------------------------------


class TestVerdictPosterior:
    """The 8 transcribed NAP Table D-1 cells are the oracle bound's
    pins — posterior = prior·BF/(prior·BF + 1 − prior) with the
    oracle BF = exp(z²/2), z = Φ⁻¹(1 − p)."""

    @pytest.mark.parametrize("prior,expected", [
        (0.01, 0.038), (0.05, 0.169), (0.10, 0.301), (0.20, 0.492),
        (0.25, 0.563), (0.30, 0.624), (0.40, 0.721), (0.50, 0.795),
    ])
    def test_nap_d1_p005_row(self, prior, expected):
        assert _gc.verdict_posterior(prior, 0.05) == pytest.approx(
            expected, abs=0.001
        )

    def test_p001_range(self):
        """The audit's transcribed p=0.01 range: ~0.13–0.94."""
        lo = _gc.verdict_posterior(0.01, 0.01)
        hi = _gc.verdict_posterior(0.50, 0.01)
        assert lo == pytest.approx(0.131, abs=0.002)
        assert hi == pytest.approx(0.937, abs=0.002)

    def test_losing_evidence_cannot_mint_support(self):
        """p > 0.5 → BF = 1 → posterior = prior. Evidence against H₁
        mints nothing for it."""
        assert _gc.verdict_posterior(0.3, 0.9) == pytest.approx(0.3)

    def test_p_underflow_saturates(self):
        """1 − Φ(z) underflows to 0.0 at extreme z — the bound
        saturates at 1 rather than refusing or fabricating."""
        assert _gc.verdict_posterior(0.3, 0.0) == 1.0

    def test_posterior_from_2lnbf_matches(self):
        p = 0.05
        z = 1.6448536269514722  # Φ⁻¹(0.95)
        assert _gc.posterior_from_2lnbf(
            0.3, z * z
        ) == pytest.approx(_gc.verdict_posterior(0.3, p), abs=1e-9)

    def test_posterior_from_2lnbf_overflow_saturates(self):
        assert _gc.posterior_from_2lnbf(0.3, 1e6) == 1.0

    def test_validation(self):
        for bad in (0, -0.1, 1.0, "x", True):
            with pytest.raises(Exception):
                _gc.verdict_posterior(bad, 0.05)
        for bad in (-0.1, 2.0, "x"):
            with pytest.raises(Exception):
                _gc.verdict_posterior(0.3, bad)


class TestArmEvidence:
    def test_clear_win(self):
        ev = _gc.arm_evidence([80, 82, 78], [120, 118, 122])
        # z≈24 → p underflows to 0.0, recorded honestly as None;
        # bf_2ln retains the statistic (rc-12 W8).
        assert ev["z"] > 0 and ev["p"] is None
        assert ev["bf_2ln"] == ev["z"] ** 2
        assert ev["computed_rung"] == "very_strong"

    def test_loser_gets_bf_one(self):
        """The directional correction: a challenger that lost mints
        2 ln BF = 0 (LR = 1), never BF > 1."""
        ev = _gc.arm_evidence([120, 118, 122], [80, 82, 78])
        assert ev["z"] < 0
        assert ev["bf_2ln"] == 0.0
        assert ev["computed_rung"] == "not_worth"

    def test_insufficient_n_returns_none(self):
        assert _gc.arm_evidence([80], [120, 121]) is None
        assert _gc.arm_evidence([], [120]) is None

    def test_zero_variance_returns_none(self):
        assert _gc.arm_evidence([80, 80], [120, 120]) is None

    def test_nonfinite_refused(self):
        with pytest.raises(Exception, match="finite"):
            _gc.arm_evidence([80, math.inf], [120, 121])

    def test_rung_boundaries(self):
        assert _gc.rung_for_2lnbf(0.0) == "not_worth"
        assert _gc.rung_for_2lnbf(1.99) == "not_worth"
        assert _gc.rung_for_2lnbf(2.0) == "positive"
        assert _gc.rung_for_2lnbf(5.99) == "positive"
        assert _gc.rung_for_2lnbf(6.0) == "strong"
        assert _gc.rung_for_2lnbf(9.99) == "strong"
        assert _gc.rung_for_2lnbf(10.0) == "very_strong"


# ---------------------------------------------------------------------------
# Contract prior — validation + snapshot at open
# ---------------------------------------------------------------------------


@pytest.fixture
def episteme(tmp_path):
    store = StateStore(str(tmp_path / "state.db"))
    store.connect()
    store.create_programme(Programme(
        id="prog-p", goal="g", constraints={}, allowed_variables=["x"],
        budget_max_trials=5, budget_max_wall_time_hours=1.0,
    ))
    mcp = _episteme_server(
        store, enforcement_config={"recurrent_protocol": False}
    )
    yield store, mcp
    store.close()


class TestContractPrior:
    async def test_bad_prior_refused_episteme(self, episteme):
        _, mcp = episteme
        for bad in (0, -0.1, 0.51, 1.0, "x"):
            r = await _call(mcp, "create_evaluation_contract", {
                "programme_id": "prog-p",
                "metrics": {"hits": "maximize"},
                "promotion_policy": {**POWERED, "prior": bad},
            })
            assert "error" in r, f"prior={bad} accepted"
            assert "prior" in r["error"]

    async def test_prior_accepted_and_echoed(self, episteme):
        _, mcp = episteme
        r = await _call(mcp, "create_evaluation_contract", {
            "programme_id": "prog-p",
            "metrics": {"hits": "maximize"},
            "promotion_policy": {**POWERED, "prior": 0.4},
        })
        assert "error" not in r, r

    async def test_prior_snapshotted_on_campaign(self, zetesis):
        store, mcp, adaptors = zetesis
        adaptors.evidence = _evidence({**POWERED, "prior": 0.4})
        r = await _call(mcp, "open_campaign", {
            "contract_id": "contract-1",
            "challenger_id": "cand-beta",
            "budget": {}, "seeds": [1],
        })
        assert "error" not in r, r
        assert store.get_campaign(r["campaign_id"]).prior == 0.4

    async def test_prior_snapshotted_on_tournament(self, arete):
        store, mcp = arete
        cid = (await _mk_meta_contract(
            mcp, {**POWERED, "prior": 0.4}
        ))["contract_id"]
        r = await _call(mcp, "open_tournament", {
            "contract_id": cid,
            "parent_improver_id": "imp-p",
            "candidate_improver_id": "imp-c",
            "budget": {"descendant_runs": 1}, "seeds": [1],
        })
        assert "error" not in r, r
        assert store.get_tournament(r["tournament_id"]).prior == 0.4


# ---------------------------------------------------------------------------
# Zetesis fixture — powered contract served over the evidence channel
# ---------------------------------------------------------------------------


def _evidence(policy):
    return FakeUpstreamAdaptor(payloads={
        "get_evaluation_contract": json.dumps({
            "contract": {
                "id": "contract-1",
                "metrics": {"hits": "maximize"},
                "budget": {},
                "promotion_policy": policy,
            }
        }),
        "get_incumbent": json.dumps({"candidate_id": "cand-alpha"}),
        "list_programmes": json.dumps({
            "programmes": [
                {"programme_id": "prog-champ",
                 "candidate_version_id": "cand-alpha"},
                {"programme_id": "prog-chall",
                 "candidate_version_id": "cand-beta"},
            ]
        }),
        "get_candidate": json.dumps(
            {"candidate": {"id": "cand-beta"}}
        ),
    })


@pytest.fixture
def zetesis(tmp_path):
    store = SearchStore(str(tmp_path / "search.db"))
    store.connect()
    store.upsert_roster_entry(RosterEntry(
        id="cand-alpha", derived_status=DerivedStatus.champion,
    ))
    store.upsert_roster_entry(RosterEntry(
        id="cand-beta", derived_status=DerivedStatus.candidate,
    ))
    adaptors = _ZAdaptors()
    adaptors.evidence = _evidence(POWERED)
    adaptors.promotion = FakePromotionAdaptor()
    adaptors.claims = FakeClaimsAdaptor()
    mcp = _zetesis_server(store, adaptors=adaptors)
    yield store, mcp, adaptors
    store.close()


@pytest.fixture
def arete(tmp_path):
    store = ImproverStore(str(tmp_path / "imp.db"))
    store.connect()
    store.create_improver(ImproverVersion(
        id="imp-p", code_artifact_digest="d", model_ref="m",
    ))
    store.create_improver(ImproverVersion(
        id="imp-c", parent_id="imp-p",
        code_artifact_digest="d2", model_ref="m",
    ))
    adaptors = _AAdaptors()
    adaptors.loop0 = _AreteUpstream()
    adaptors.loop1 = _AreteUpstream()
    adaptors.claims = _AreteClaims()
    mcp = _arete_server(store, adaptors=adaptors)
    yield store, mcp
    store.close()


async def _mk_meta_contract(mcp, policy) -> dict:
    return await _call(mcp, "create_meta_contract", {
        "metrics": {"primary_metric": "hits", "direction": "max"},
        "promotion_policy": policy,
    })


async def _open_and_close_campaign(mcp, champ_hits, chall_hits):
    """One result row per value — the arm lists are per-row samples."""
    cid = (await _call(mcp, "open_campaign", {
        "contract_id": "contract-1",
        "challenger_id": "cand-beta",
        "budget": {}, "seeds": [1],
    }))["campaign_id"]
    for hits in champ_hits:
        await _call(mcp, "record_campaign_result", {
            "campaign_id": cid, "arm": "champion",
            "programme_id": "prog-champ", "metrics": {"hits": hits},
        })
    for hits in chall_hits:
        await _call(mcp, "record_campaign_result", {
            "campaign_id": cid, "arm": "challenger",
            "programme_id": "prog-chall", "metrics": {"hits": hits},
        })
    ev = await _call(mcp, "pull_campaign_evidence", {
        "campaign_id": cid, "source": "loop0",
        "tool": "get_candidate_scorecard",
        "args": {"candidate_id": "cand-beta"},
    })
    closed = await _call(mcp, "close_campaign", {"campaign_id": cid})
    return cid, ev["evidence_ref_id"], closed


# ---------------------------------------------------------------------------
# Close computes the statistic; the verdict gate reads it
# ---------------------------------------------------------------------------


class TestCloseStatistic:
    async def test_close_persists_statistic(self, zetesis):
        store, mcp, _ = zetesis
        cid, _, closed = await _open_and_close_campaign(
            mcp, [80, 82, 78], [120, 118, 122]
        )
        assert closed["bf_2ln"] == 600.0
        assert closed["computed_rung"] == "very_strong"
        c = store.get_campaign(cid)
        assert c.p_value is None       # underflow at z≈24 → honest NULL
        assert c.bf_2ln == 600.0

    async def test_close_null_statistic_on_single_result(
        self, zetesis
    ):
        """n<2 per arm → honestly null, not a fabricated rung."""
        store, mcp, _ = zetesis
        cid, _, closed = await _open_and_close_campaign(
            mcp, [80], [120]
        )
        assert closed["bf_2ln"] is None
        assert closed["computed_rung"] is None
        assert store.get_campaign(cid).bf_2ln is None

    async def test_get_campaign_echoes_statistic(self, zetesis):
        _, mcp, _ = zetesis
        cid, _, _ = await _open_and_close_campaign(
            mcp, [80, 82, 78], [120, 118, 122]
        )
        got = await _call(mcp, "get_campaign", {"campaign_id": cid})
        assert got["campaign"]["bf_2ln"] == 600.0
        assert got["campaign"]["computed_rung"] == "very_strong"
        assert got["campaign"]["prior"] is None  # absent on contract


class TestComputedRungGate:
    async def test_overclaim_refused(self, zetesis):
        """claimed > computed refuses — the evidence's own bound is
        the ceiling on what a promote may assert."""
        _, mcp, adaptors = zetesis
        adaptors.evidence = _evidence({
            **POWERED, "min_evidence_rung": "not_worth",
        })
        # Deliberately weak separation → small z → low computed rung.
        cid, eref, closed = await _open_and_close_campaign(
            mcp, [80, 85, 82], [81, 84, 83]
        )
        assert closed["computed_rung"] in ("not_worth", "positive")
        r = await _call(mcp, "record_promotion_verdict", {
            "campaign_id": cid, "verdict": "promote",
            "decided_by": "human:x",
            "evidence_ref_ids": [eref],
            "claimed_rung": "very_strong",
        })
        assert "error" in r and "exceeds the evidence" in r["error"]
        assert r["computed_rung"] == closed["computed_rung"]
        assert not any(
            t == "record_promotion_decision"
            for t, _ in adaptors.promotion.pushed
        )

    async def test_at_bound_passes_and_mints_grounded(self, zetesis):
        store, mcp, adaptors = zetesis
        adaptors.evidence = _evidence({
            **POWERED, "prior": 0.4,
        })
        cid, eref, closed = await _open_and_close_campaign(
            mcp, [80, 82, 78], [120, 118, 122]
        )
        r = await _call(mcp, "record_promotion_verdict", {
            "campaign_id": cid, "verdict": "promote",
            "decided_by": "human:x",
            "evidence_ref_ids": [eref],
            "claimed_rung": "very_strong",
        })
        assert "error" not in r, r
        assert r["computed_rung"] == "very_strong"
        assert r["confidence_basis"] == "grounded"
        # No number crosses the wire (plan-20260930-0240Z): the mint
        # names the registered derivation — anamnesis recomputes
        # posterior_from_2lnbf(0.4, 600) and stores the result.
        minted = adaptors.claims.minted[-1]
        assert minted["confidence_basis"] == "grounded"
        comp = minted["confidence_computation"]
        assert comp["procedure"] == "posterior_from_2lnbf"
        assert comp["inputs"]["prior"] == 0.4
        assert comp["inputs"]["bf_2ln"] == 600.0
        # Upstream decision row carries the measured statistic.
        _, args = adaptors.promotion.pushed[-1]
        assert args["computed_rung"] == "very_strong"
        assert args["bf_2ln"] == 600.0

    async def test_retain_mints_h0_bound(self, zetesis):
        _, mcp, adaptors = zetesis
        cid, eref, _ = await _open_and_close_campaign(
            mcp, [120, 118, 122], [80, 82, 78]  # challenger lost
        )
        r = await _call(mcp, "record_promotion_verdict", {
            "campaign_id": cid, "verdict": "retain",
            "decided_by": "human:x",
            "evidence_ref_ids": [eref],
        })
        assert "error" not in r, r
        minted = adaptors.claims.minted[-1]
        assert minted["confidence_basis"] == "grounded"
        # bf_2ln=0 → posterior=prior → the H₀ bound is a distinct
        # registered derivation, named (not computed) by the caller.
        comp = minted["confidence_computation"]
        assert comp["procedure"] == "h0_bound_from_2lnbf"
        assert comp["inputs"]["bf_2ln"] == 0.0
        assert comp["inputs"]["prior"] == _gc.PRIOR_CONFIDENCE_MAX.value

    async def test_no_statistic_claim_above_declared_refused(
        self, zetesis
    ):
        """computed_rung null (n<2): the ceiling falls back to the
        declared minimum — claiming past it is refused (rc-12 W3)."""
        _, mcp, _ = zetesis
        cid, eref, _ = await _open_and_close_campaign(
            mcp, [80], [120]  # n=1/arm → no statistic
        )
        r = await _call(mcp, "record_promotion_verdict", {
            "campaign_id": cid, "verdict": "promote",
            "decided_by": "human:x",
            "evidence_ref_ids": [eref],
            "claimed_rung": "positive",
        })
        assert "error" in r, r
        assert "ceiling" in r["error"]

    async def test_no_statistic_mints_ceiling_weakly_grounded(
        self, zetesis
    ):
        """Null computed rung: claiming exactly the declared minimum
        still passes — the mint carries no computation, so the claim
        is NULL-confidence, labelled weakly_grounded."""
        _, mcp, adaptors = zetesis
        cid, eref, _ = await _open_and_close_campaign(
            mcp, [80], [120]  # n=1/arm → no statistic
        )
        r = await _call(mcp, "record_promotion_verdict", {
            "campaign_id": cid, "verdict": "promote",
            "decided_by": "human:x",
            "evidence_ref_ids": [eref],
            "claimed_rung": "not_worth",
        })
        assert "error" not in r, r
        assert r["computed_rung"] is None
        assert r["confidence_basis"] == "weakly_grounded"
        minted = adaptors.claims.minted[-1]
        assert minted["confidence_computation"] is None
        assert minted["confidence_basis"] == "weakly_grounded"


# ---------------------------------------------------------------------------
# Arete: tournament statistic + meta-decision gate
# ---------------------------------------------------------------------------


def _seed_eref(store, tournament_id="tourn-x") -> str:
    """An evidence_ref naming the tournament context that involves
    the candidate — decision writers check both existence + relation.
    When the tournament doesn't exist yet (the legacy-context path),
    stub contract + tournament rows stand in for it."""
    if store.get_tournament(tournament_id) is None:
        store.create_meta_contract(MetaContract(
            id="mc-ctx", version=1,
            metrics={"primary_metric": "hits"},
            promotion_policy={},
        ))
        store.create_tournament(Tournament(
            id=tournament_id, contract_id="mc-ctx",
            parent_improver_id="imp-p", candidate_improver_id="imp-c",
            budget={"descendant_runs": 1},
        ))
    store.create_evidence_ref(EvidenceRef(
        id=f"eref-{tournament_id}", context_type="tournament",
        context_id=tournament_id,
        source="loop0", tool="list_trials", args={},
        ref_ids=["trial-a1"],
    ))
    return f"eref-{tournament_id}"


async def _open_results_close_tournament(mcp, cid):
    tid = (await _call(mcp, "open_tournament", {
        "contract_id": cid,
        "parent_improver_id": "imp-p",
        "candidate_improver_id": "imp-c",
        "budget": {"descendant_runs": 2}, "seeds": [7, 31],
    }))["tournament_id"]
    # Two seeds per arm — per-seed bests are the arm samples.
    for arm, seed, hits in (
        ("parent", 7, 100), ("parent", 31, 120),
        ("candidate", 7, 150), ("candidate", 31, 160),
    ):
        r = await _call(mcp, "record_tournament_result", {
            "tournament_id": tid, "arm": arm,
            "descendant_spec": {"gen": 1},
            "metrics": {"hits": hits, "seed": seed},
        })
        assert "error" not in r, r
    closed = await _call(mcp, "close_tournament", {
        "tournament_id": tid,
    })
    return tid, closed


class TestTournamentStatistic:
    async def test_close_persists_statistic(self, arete):
        store, mcp = arete
        cid = (await _mk_meta_contract(mcp, dict(POWERED)))[
            "contract_id"]
        tid, closed = await _open_results_close_tournament(mcp, cid)
        # bests: parent [100,120] (mean 110, sd 14.14), candidate
        # [150,160] (mean 155, sd 7.07); sd_pooled ≈ 11.18 →
        # z ≈ 45/11.18 ≈ 4.02 → bf_2ln ≈ 16.2
        assert closed["p_value"] is not None
        assert closed["bf_2ln"] == pytest.approx(16.2, abs=0.2)
        assert closed["computed_rung"] == "very_strong"
        t = store.get_tournament(tid)
        assert t.bf_2ln == pytest.approx(16.2, abs=0.2)

    async def test_overclaim_on_tournament_refused(self, arete):
        store, mcp = arete
        cid = (await _mk_meta_contract(mcp, dict(POWERED)))[
            "contract_id"]
        # Weak separation: candidate marginally better → low rung.
        tid = (await _call(mcp, "open_tournament", {
            "contract_id": cid,
            "parent_improver_id": "imp-p",
            "candidate_improver_id": "imp-c",
            "budget": {"descendant_runs": 2}, "seeds": [7, 31],
        }))["tournament_id"]
        for arm, seed, hits in (
            ("parent", 7, 100), ("parent", 31, 140),
            ("candidate", 7, 105), ("candidate", 31, 138),
        ):
            await _call(mcp, "record_tournament_result", {
                "tournament_id": tid, "arm": arm,
                "descendant_spec": {"gen": 1},
                "metrics": {"hits": hits, "seed": seed},
            })
        closed = await _call(mcp, "close_tournament", {
            "tournament_id": tid,
        })
        assert closed["computed_rung"] == "not_worth"
        eref = _seed_eref(store, tid)
        r = await _call(mcp, "record_meta_decision", {
            "candidate_improver_id": "imp-c",
            "verdict": "promote",
            "evidence_refs": [eref],
            "rationale": "asserting past the bound",
            "decided_by": "human:x",
            "tournament_id": tid,
            "contract_id": cid,
            "claimed_rung": "strong",
        })
        assert "error" in r and "exceeds the evidence" in r["error"]

    async def test_meta_decision_mints_grounded_posterior(self, arete):
        store, mcp = arete
        cid = (await _mk_meta_contract(mcp, dict(POWERED)))[
            "contract_id"]
        tid, closed = await _open_results_close_tournament(mcp, cid)
        eref = _seed_eref(store, tid)
        r = await _call(mcp, "record_meta_decision", {
            "candidate_improver_id": "imp-c",
            "verdict": "promote",
            "evidence_refs": [eref],
            "rationale": "gain clears the bar",
            "decided_by": "human:x",
            "tournament_id": tid,
            "contract_id": cid,
            "claimed_rung": "very_strong",
        })
        assert "error" not in r, r
        assert r["computed_rung"] == "very_strong"
        assert r["confidence_basis"] == "grounded"
        d = store.list_meta_decisions("imp-c")[-1]
        assert d.computed_rung == "very_strong"
        assert d.confidence_basis == "grounded"
        minted = _last_mint(store)
        assert minted is not None

    async def test_no_statistic_mints_weakly_grounded(self, arete):
        store, mcp = arete
        eref = _seed_eref(store)
        r = await _call(mcp, "record_meta_decision", {
            "candidate_improver_id": "imp-c",
            "verdict": "promote",
            "evidence_refs": [eref],
            "rationale": "legacy context — no statistic",
            "decided_by": "human:x",
        })
        assert "error" not in r, r
        assert r["confidence_basis"] == "weakly_grounded"


def _last_mint(store):
    """The adaptor fake is not on the store; read the minted claim
    list from the decision row instead."""
    d = store.list_meta_decisions("imp-c")[-1]
    return d.claim_id


# ---------------------------------------------------------------------------
# Episteme: verdict mint is the ceiling, labelled weakly_grounded
# ---------------------------------------------------------------------------


class _EpistemeClaims:
    def __init__(self):
        self.asserts = []

    async def assert_claim(
        self, content, type, evidence=None,
        source_id=None, confidence_computation=None,
    ):
        has_evidence = any(
            e.get("relation") in (
                "supports", "derived_from", "tested_by", "valid_under",
            )
            for e in (evidence or [])
        )
        basis = (
            "grounded" if confidence_computation is not None
            else "weakly_grounded" if has_evidence
            else "ungrounded"
        )
        self.asserts.append({
            "evidence": evidence,
            "confidence_computation": confidence_computation,
            "confidence_basis": basis,
        })
        return {
            "claim_id": "claim-1", "status": "created",
            "confidence": None, "confidence_basis": basis,
            "confidence_computation": confidence_computation,
        }


class TestHypothesisMint:
    async def test_conclusion_mints_ceiling_weakly_grounded(
        self, tmp_path
    ):
        """Single-arm verdicts carry no comparative likelihood —
        the minted confidence is the prior ceiling, honestly labelled."""
        from ml_episteme_mcp.clients.adaptor import MCPAdaptor
        from ml_episteme_mcp.state.models import (
            Belief, Hypothesis, Observation,
            ProgrammeStatus, Trial, TrialStatus,
        )

        store = StateStore(str(tmp_path / "s.db"))
        store.connect()
        claims = _EpistemeClaims()
        adaptor = MCPAdaptor({})
        adaptor.set_claims(claims)
        try:
            store.create_programme(Programme(
                id="prog-1", goal="g", constraints={},
                allowed_variables=["x"], budget_max_trials=10,
                budget_max_wall_time_hours=1.0,
                status=ProgrammeStatus.active,
            ))
            store.create_hypothesis(Hypothesis(
                id="hyp-1", programme_id="prog-1",
                statement="s", failure_criterion="f",
                variables_involved=["x"],
            ))
            store.update_hypothesis_status("hyp-1", "under_test")
            store.create_trial(Trial(
                id="trial-1", programme_id="prog-1",
                hypothesis_id="hyp-1", config_json="{}",
                status=TrialStatus.completed,
            ))
            store.create_observation(Observation(
                id="obs-1", trial_id="trial-1",
                metrics_json='{"acc": 0.9}',
                variance_json='{"acc": 0.01}',
                spatiotemporal_region="gpu-0",
            ))
            store.create_belief(Belief(
                id="belief-1", programme_id="prog-1", state_json="{}",
            ))
            mcp = _episteme_server(
                store,
                adaptor=adaptor,
                enforcement_config={"recurrent_protocol": False},
            )
            r = await _call(mcp, "conclude_hypothesis", {
                "programme_id": "prog-1", "hypothesis_id": "hyp-1",
                "verdict": "accepted", "evidence_summary": "done",
            })
            assert "error" not in r, r
            assert r["claim_status"] == "minted"
            mint = claims.asserts[0]
            assert mint["confidence_computation"] is None
            assert mint["confidence_basis"] == "weakly_grounded"
        finally:
            store.close()


# ---------------------------------------------------------------------------
# Investigation mint: basis reflects whether evidence backed the finding
# ---------------------------------------------------------------------------


class TestInvestigationBasis:
    async def _open_and_conclude(self, mcp, with_ref: bool):
        inv = await _call(mcp, "open_investigation", {
            "question": "q?", "scope": {"programme_ids": ["prog-x"]},
        })
        inv_id = inv["investigation_id"]
        refs = []
        if with_ref:
            pull = await _call(mcp, "pull_evidence", {
                "investigation_id": inv_id, "source": "loop0",
                "tool": "list_trials",
                "args": {"programme_id": "prog-x"},
            })
            refs = [pull["evidence_ref_id"]]
        await _call(mcp, "record_finding", {
            "investigation_id": inv_id,
            "content": "f",
            "evidence_ref_ids": refs,
        })
        r = await _call(mcp, "conclude_investigation", {
            "investigation_id": inv_id, "verdict": "findings",
            "summary": "s",
        })
        return r

    async def test_evidence_backed_is_weakly_grounded(self, zetesis):
        _, mcp, adaptors = zetesis
        r = await self._open_and_conclude(mcp, with_ref=True)
        assert r["claim_status"] == "minted"
        assert adaptors.claims.minted[-1][
            "confidence_basis"
        ] == "weakly_grounded"

    async def test_unevidenced_is_ungrounded(self, zetesis):
        """A finding minted under the prior ceiling with no refs is
        honestly labelled ungrounded — the reachable case."""
        _, mcp, adaptors = zetesis
        r = await self._open_and_conclude(mcp, with_ref=False)
        assert r["claim_status"] == "minted"
        assert adaptors.claims.minted[-1][
            "confidence_basis"
        ] == "ungrounded"


# ---------------------------------------------------------------------------
# Anamnesis: basis validation + read-path echo + legacy migration
# ---------------------------------------------------------------------------


class TestClaimBasisColumn:
    async def test_assert_claim_basis_round_trip(self, tmp_path):
        """A computation mint derives grounded confidence server-side
        and the derivation record survives the read path."""
        from ml_anamnesis_mcp.server import create_server as _a_server
        from ml_anamnesis_mcp.state.store import MemoryStore

        store = MemoryStore(str(tmp_path / "m.db"))
        store.connect()
        try:
            mcp = _a_server(store)
            comp = {
                "procedure": "posterior_from_2lnbf",
                "inputs": {"prior": 0.3, "bf_2ln": 2.7055},
            }
            r = await _call(mcp, "assert_claim", {
                "content": "c", "type": "empirical",
                "confidence_computation": comp,
                "evidence": [{
                    "to_ref": "trial-x", "ref_type": "trial",
                    "relation": "derived_from",
                }],
            })
            assert "error" not in r, r
            assert r["confidence"] == pytest.approx(
                _gc.posterior_from_2lnbf(0.3, 2.7055)
            )
            got = await _call(mcp, "get_claim", {
                "claim_id": r["claim_id"],
            })
            assert got["claim"]["confidence_basis"] == "grounded"
            assert got["claim"]["confidence_computation"] == comp
        finally:
            store.close()

    async def test_unknown_procedure_refused(self, tmp_path):
        from ml_anamnesis_mcp.server import create_server as _a_server
        from ml_anamnesis_mcp.state.store import MemoryStore

        store = MemoryStore(str(tmp_path / "m.db"))
        store.connect()
        try:
            mcp = _a_server(store)
            r = await _call(mcp, "assert_claim", {
                "content": "c", "type": "empirical",
                "confidence_computation": {
                    "procedure": "sorta",
                    "inputs": {"prior": 0.3, "bf_2ln": 2.7},
                },
                "evidence": [{
                    "to_ref": "trial-x", "ref_type": "trial",
                    "relation": "derived_from",
                }],
            })
            assert "error" in r
            assert "procedure" in r["error"]
        finally:
            store.close()

    def test_legacy_claims_table_migrates(self, tmp_path):
        """A pre-basis claims table gains the nullable column, the
        dead `importance` column is dropped, and legacy rows read
        back with basis NULL."""
        from ml_anamnesis_mcp.state.store import MemoryStore

        db = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db))
        conn.executescript("""
            CREATE TABLE claims (
                id TEXT PRIMARY KEY, content TEXT, type TEXT,
                confidence REAL, importance REAL,
                valid_from TEXT, valid_until TEXT,
                supersedes_id TEXT, content_hash TEXT UNIQUE,
                source_id TEXT, created_at TEXT
            );
            INSERT INTO claims VALUES (
                'claim-old', 'x', 'empirical', 0.5, 0.5,
                't', NULL, NULL, 'h-old', NULL, 't');
        """)
        conn.close()
        store = MemoryStore(str(db))
        store.connect()
        try:
            claim = store.get_claim("claim-old")
            assert claim is not None
            assert claim.confidence_basis is None
            cols = {
                r["name"]
                for r in store._fetchall("PRAGMA table_info(claims)")
            }
            assert "importance" not in cols
            assert "confidence_computation" in cols
            # The legacy row's bare 0.5 survives but its derivation is
            # honestly NULL — never reconstructed.
            assert claim.confidence_computation is None
        finally:
            store.close()


# ---------------------------------------------------------------------------
# The indicted literals are gone
# ---------------------------------------------------------------------------


class TestDeletedConstants:
    def test_indicted_trio_absent(self):
        for gone in (
            "VERDICT_CONFIDENCE_ACCEPTED",
            "VERDICT_CONFIDENCE_REJECTED",
            "METADECISION_CONFIDENCE",
        ):
            assert not hasattr(_gc, gone)

    def test_scoring_violations_empty(self):
        assert _gc.scoring_violations() == []

    def test_no_transitional_markers_readded(self):
        # The mirror is byte-identical to the canonical file.
        for c in _gc.REGISTRY.values():
            assert "transitional" not in (c.rationale or "")
