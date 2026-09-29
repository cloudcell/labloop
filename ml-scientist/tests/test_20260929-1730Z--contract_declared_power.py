"""Contract-declared power (e-plan 20260929-1641Z).

promotion_policy now carries the design inputs — sesoi_d (declared,
never pilot-estimated), target_power, min_evidence_rung — and they
gate. This file pins: creation refusal on missing/malformed keys,
the required_n closed form, open-time declared-n and underpowered
gates with the explicit acknowledgement path, legacy-contract
refusal, close-time field persistence, and the declared-vs-claimed
rung check on all three decision writers.
"""

from __future__ import annotations

import json

import pytest

from ml_episteme_mcp.server import create_server as _episteme_server
from ml_episteme_mcp.state.models import Programme
from ml_episteme_mcp.state.store import StateStore

from ml_zetesis_mcp.clients.adaptors import Adaptors as _ZAdaptors
from ml_zetesis_mcp.server import create_server as _zetesis_server
from ml_zetesis_mcp.state.models import DerivedStatus, RosterEntry
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

import ml_episteme_mcp._grounded_constants as _gc

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
TIGHT = {
    # sesoi_d=0.5, power=0.8, alpha=0.05 → required_n=63 per arm.
    "sesoi_d": 0.5,
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
# Loop-0 (episteme) fixtures
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


# ---------------------------------------------------------------------------
# Loop-1 (zetesis) fixtures — powered contract served over evidence
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


# ---------------------------------------------------------------------------
# Loop-2 (arete) fixtures
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Registry procedures — closed-form + ladder
# ---------------------------------------------------------------------------


class TestRegistryProcedures:
    def test_required_n_closed_form(self):
        """Pinned: d=0.5, power=0.8, alpha=0.05 → ceil(62.79)=63."""
        assert _gc.required_n(0.5, 0.8, 0.05)["n_per_arm"] == 63

    def test_required_n_rejects_pilot_sesoi(self):
        with pytest.raises(Exception, match="pilot"):
            _gc.required_n(0.5, 0.8, source="pilot")

    def test_required_n_validation(self):
        for bad in (0, -0.5, "x"):
            with pytest.raises(Exception):
                _gc.required_n(bad, 0.8)
        with pytest.raises(Exception):
            _gc.required_n(0.5, 1.5)

    def test_rung_ladder_is_2lnbf(self):
        """Kass–Raftery 1995: the ladder is 2 ln BF — 0/2/6/10."""
        assert _gc.min_evidence_rung("not_worth") == 0.0
        assert _gc.min_evidence_rung("positive") == 2.0
        assert _gc.min_evidence_rung("strong") == 6.0
        assert _gc.min_evidence_rung("very_strong") == 10.0
        assert _gc.rung_at_least("strong", "positive")
        assert not _gc.rung_at_least("positive", "strong")
        with pytest.raises(Exception):
            _gc.min_evidence_rung("overwhelming")

    def test_type_s_m_null_when_nothing_achieved(self):
        assert _gc.type_s_m(0.5, 0) is None
        tsm = _gc.type_s_m(0.5, 63)
        assert tsm is not None
        assert 0 < tsm["type_s_risk"] < 1
        assert tsm["type_m_ratio"] > 1
        # At the required n the power must match the declared target.
        assert tsm["power"] == pytest.approx(0.8, abs=0.01)


# ---------------------------------------------------------------------------
# Contract creation — the three required keys gate both tools
# ---------------------------------------------------------------------------


class TestContractCreationGate:
    async def test_episteme_refuses_missing_keys(self, episteme):
        _, mcp = episteme
        r = await _call(mcp, "create_evaluation_contract", {
            "programme_id": "prog-p",
            "metrics": {"hits": "maximize"},
            "promotion_policy": {"min_gain": 1.0},
        })
        assert "error" in r
        for key in ("sesoi_d", "target_power", "min_evidence_rung"):
            assert key in r["error"]

    async def test_episteme_refuses_malformed(self, episteme):
        _, mcp = episteme
        r = await _call(mcp, "create_evaluation_contract", {
            "programme_id": "prog-p",
            "metrics": {"hits": "maximize"},
            "promotion_policy": {
                "sesoi_d": -1, "target_power": 2,
                "min_evidence_rung": "bogus",
            },
        })
        assert "error" in r

    async def test_episteme_accepts_powered(self, episteme):
        _, mcp = episteme
        r = await _call(mcp, "create_evaluation_contract", {
            "programme_id": "prog-p",
            "metrics": {"hits": "maximize"},
            "promotion_policy": dict(POWERED),
        })
        assert "error" not in r, r
        assert r["contract_id"].startswith("contract-")

    async def test_arete_refuses_missing_keys(self, arete):
        _, mcp = arete
        r = await _mk_meta_contract(mcp, {"min_gain": 1.05})
        assert "error" in r
        assert "sesoi_d" in r["error"]

    async def test_arete_accepts_powered(self, arete):
        _, mcp = arete
        r = await _mk_meta_contract(mcp, dict(POWERED))
        assert "error" not in r, r


# ---------------------------------------------------------------------------
# open_tournament — declared n + underpowered gate
# ---------------------------------------------------------------------------


class TestTournamentPowerGate:
    async def _open(self, mcp, cid, **kw):
        args = {
            "contract_id": cid,
            "parent_improver_id": "imp-p",
            "candidate_improver_id": "imp-c",
            "budget": {"descendant_runs": 1},
        }
        args.update(kw)
        return await _call(mcp, "open_tournament", args)

    async def test_missing_n_refused(self, arete):
        """seeds=None under a power contract cannot silently pass."""
        _, mcp = arete
        cid = (await _mk_meta_contract(mcp, dict(POWERED)))[
            "contract_id"]
        r = await self._open(mcp, cid)
        assert "error" in r and "declares no n" in r["error"]

    async def test_underpowered_refused(self, arete):
        _, mcp = arete
        cid = (await _mk_meta_contract(mcp, dict(TIGHT)))[
            "contract_id"]
        r = await self._open(mcp, cid, seeds=[1])
        assert "error" in r
        assert "under-powered" in r["error"]
        assert r["n_required"] == 63 and r["n_requested"] == 1

    async def test_explicit_acknowledgement_opens(self, arete):
        _, mcp = arete
        cid = (await _mk_meta_contract(mcp, dict(TIGHT)))[
            "contract_id"]
        r = await self._open(
            mcp, cid, seeds=[1], allow_underpowered=True
        )
        assert "error" not in r, r
        assert r["power_acknowledged"] is True
        assert r["n_required"] == 63 and r["n_requested"] == 1

    async def test_adequate_opens(self, arete):
        _, mcp = arete
        cid = (await _mk_meta_contract(mcp, dict(POWERED)))[
            "contract_id"]
        r = await self._open(mcp, cid, seeds=[1])
        assert "error" not in r, r
        assert r["power_acknowledged"] is False

    async def test_legacy_contract_cannot_open(self, arete):
        """A pre-power contract stays readable but opens no work."""
        store, mcp = arete
        store.create_meta_contract(MetaContract(
            id="mc-legacy", version=1,
            metrics={"primary_metric": "hits"},
            promotion_policy={"min_gain": 1.0},
        ))
        r = await self._open(mcp, "mc-legacy", seeds=[1])
        assert "error" in r and "mint a new" in r["error"]

    async def test_close_records_power_fields(self, arete):
        store, mcp = arete
        cid = (await _mk_meta_contract(mcp, dict(TIGHT)))[
            "contract_id"]
        tid = (await self._open(
            mcp, cid, seeds=[1], allow_underpowered=True
        ))["tournament_id"]
        for arm in ("parent", "candidate"):
            r = await _call(mcp, "record_tournament_result", {
                "tournament_id": tid, "arm": arm,
                "descendant_spec": {"g": 1},
                "metrics": {"hits": 80 if arm == "parent" else 100},
            })
            assert "error" not in r, r
        closed = await _call(mcp, "close_tournament", {
            "tournament_id": tid,
        })
        assert closed["n_achieved"] == 1
        assert closed["underpowered"] is True
        # Type S/M computed at n=1 under the declared SESOI.
        assert closed["type_s_risk"] is not None
        t = store.get_tournament(tid)
        assert t.n_requested == 1 and t.n_required == 63
        assert t.n_achieved == 1 and t.underpowered is True
        assert t.power_acknowledged is True
        assert t.sesoi_d == 0.5 and t.target_power == 0.8
        assert t.min_evidence_rung == "not_worth"
        assert t.type_s_risk is not None


# ---------------------------------------------------------------------------
# open_campaign — the gate runs on the pulled remote contract
# ---------------------------------------------------------------------------


class TestCampaignPowerGate:
    async def _open(self, mcp, **kw):
        args = {
            "contract_id": "contract-1",
            "challenger_id": "cand-beta",
            "budget": {},
        }
        args.update(kw)
        return await _call(mcp, "open_campaign", args)

    async def test_missing_n_refused(self, zetesis):
        """{} budget and no seeds declares no n — refuses."""
        _, mcp, _ = zetesis
        r = await self._open(mcp)
        assert "error" in r and "declares no n" in r["error"]

    async def test_seeds_declare_n(self, zetesis):
        _, mcp, _ = zetesis
        r = await self._open(mcp, seeds=[1])
        assert "error" not in r, r
        assert r["n_requested"] == 1 and r["n_required"] == 1

    async def test_budget_declares_n(self, zetesis):
        _, mcp, _ = zetesis
        r = await self._open(mcp, budget={
            "programmes_per_arm": 1, "trials_per_programme": 2,
        })
        assert "error" not in r, r
        assert r["n_requested"] == 1

    async def test_underpowered_refused_then_acked(self, zetesis):
        store, mcp, adaptors = zetesis
        adaptors.evidence = _evidence(dict(TIGHT))
        r = await self._open(mcp, seeds=[1])
        assert "error" in r and "under-powered" in r["error"]
        r = await self._open(
            mcp, seeds=[1], allow_underpowered=True
        )
        assert "error" not in r, r
        assert r["power_acknowledged"] is True
        c = store.get_campaign(r["campaign_id"])
        assert c.power_acknowledged is True
        assert c.n_required == 63 and c.n_requested == 1

    async def test_legacy_contract_cannot_open(self, zetesis):
        _, mcp, adaptors = zetesis
        adaptors.evidence = _evidence({"min_gain": 1.0})
        r = await self._open(mcp, seeds=[1])
        assert "error" in r and "mint a new" in r["error"]

    async def test_close_records_power_fields(self, zetesis):
        store, mcp, adaptors = zetesis
        adaptors.evidence = _evidence(dict(TIGHT))
        cid = (await self._open(
            mcp, seeds=[1], allow_underpowered=True
        ))["campaign_id"]
        for arm, prog, hits in (
            ("champion", "prog-champ", 80),
            ("challenger", "prog-chall", 120),
        ):
            r = await _call(mcp, "record_campaign_result", {
                "campaign_id": cid, "arm": arm,
                "programme_id": prog, "metrics": {"hits": hits},
            })
            assert "error" not in r, r
        closed = await _call(mcp, "close_campaign", {
            "campaign_id": cid,
        })
        assert closed["n_achieved"] == 1
        assert closed["underpowered"] is True
        assert closed["type_s_risk"] is not None
        c = store.get_campaign(cid)
        assert c.n_achieved == 1 and c.underpowered is True
        assert c.type_s_risk is not None
        assert c.sesoi_d == 0.5


# ---------------------------------------------------------------------------
# Decision writers — declared-vs-claimed rung
# ---------------------------------------------------------------------------


def _seed_eref(store) -> str:
    """A real evidence_ref row whose tournament context involves the
    candidate — decision writers check both existence and relation."""
    store.create_meta_contract(MetaContract(
        id="mc-ctx", version=1,
        metrics={"primary_metric": "hits"},
        promotion_policy={},
    ))
    store.create_tournament(Tournament(
        id="tourn-x", contract_id="mc-ctx",
        parent_improver_id="imp-p", candidate_improver_id="imp-c",
        budget={"descendant_runs": 1},
    ))
    store.create_evidence_ref(EvidenceRef(
        id="eref-1", context_type="tournament", context_id="tourn-x",
        source="loop0", tool="list_trials", args={},
        ref_ids=["trial-a1"],
    ))
    return "eref-1"


class TestRungGate:
    async def test_meta_decision_below_rung_refused(self, arete):
        store, mcp = arete
        eref = _seed_eref(store)
        cid = (await _mk_meta_contract(mcp, {
            **POWERED, "min_evidence_rung": "strong",
        }))["contract_id"]
        # No rung claimed at all → refused.
        r = await _call(mcp, "record_meta_decision", {
            "candidate_improver_id": "imp-c",
            "verdict": "promote",
            "evidence_refs": [eref],
            "rationale": "gain clears the bar",
            "decided_by": "human:x",
            "contract_id": cid,
        })
        assert "error" in r and "rung" in r["error"].lower()
        # Below the declared minimum → refused.
        r = await _call(mcp, "record_meta_decision", {
            "candidate_improver_id": "imp-c",
            "verdict": "promote",
            "evidence_refs": [eref],
            "rationale": "gain clears the bar",
            "decided_by": "human:x",
            "contract_id": cid,
            "claimed_rung": "positive",
        })
        assert "error" in r and "below" in r["error"]
        # Meeting the declared rung → records.
        r = await _call(mcp, "record_meta_decision", {
            "candidate_improver_id": "imp-c",
            "verdict": "promote",
            "evidence_refs": [eref],
            "rationale": "gain clears the bar",
            "decided_by": "human:x",
            "contract_id": cid,
            "claimed_rung": "very_strong",
        })
        assert "error" not in r, r
        assert r["declared_rung"] == "strong"
        assert r["claimed_rung"] == "very_strong"

    async def test_meta_decision_legacy_contract_ungated(self, arete):
        """No declared rung on the contract → promote proceeds."""
        store, mcp = arete
        eref = _seed_eref(store)
        store.create_meta_contract(MetaContract(
            id="mc-legacy", version=1,
            metrics={"primary_metric": "hits"},
            promotion_policy={"min_gain": 1.0},
        ))
        r = await _call(mcp, "record_meta_decision", {
            "candidate_improver_id": "imp-c",
            "verdict": "promote",
            "evidence_refs": [eref],
            "rationale": "legacy contract declares no rung",
            "decided_by": "human:x",
            "contract_id": "mc-legacy",
        })
        assert "error" not in r, r
        assert r["declared_rung"] is None

    async def test_bad_rung_name_refused(self, arete):
        store, mcp = arete
        eref = _seed_eref(store)
        r = await _call(mcp, "record_meta_decision", {
            "candidate_improver_id": "imp-c",
            "verdict": "hold",
            "evidence_refs": [eref],
            "rationale": "bogus rung name",
            "decided_by": "human:x",
            "claimed_rung": "decisive",
        })
        assert "error" in r and "claimed_rung" in r["error"]

    async def test_promotion_decision_below_rung_refused(
        self, episteme
    ):
        _, mcp = episteme
        ct = await _call(mcp, "create_evaluation_contract", {
            "programme_id": "prog-p",
            "metrics": {"hits": "maximize"},
            "promotion_policy": {
                **POWERED, "min_evidence_rung": "strong",
            },
        })
        cand = await _call(mcp, "register_candidate", {
            "code_artifact_digest": "none",
            "model_ref": "m", "capability_profile": {},
        })
        r = await _call(mcp, "record_promotion_decision", {
            "candidate_id": cand["candidate_id"],
            "contract_id": ct["contract_id"],
            "verdict": "promote",
            "evidence_refs": [cand["candidate_id"]],
            "rationale": "won",
            "decided_by": "human:x",
            "claimed_rung": "positive",
        })
        assert "error" in r and "below" in r["error"]

    async def test_promotion_verdict_below_rung_refused(self, zetesis):
        store, mcp, adaptors = zetesis
        adaptors.evidence = _evidence({
            **POWERED, "min_evidence_rung": "strong",
        })
        cid = (await _open_campaign(mcp))["campaign_id"]
        for arm, prog, hits in (
            ("champion", "prog-champ", 80),
            ("challenger", "prog-chall", 120),
        ):
            await _call(mcp, "record_campaign_result", {
                "campaign_id": cid, "arm": arm,
                "programme_id": prog, "metrics": {"hits": hits},
            })
        ev = await _call(mcp, "pull_campaign_evidence", {
            "campaign_id": cid, "source": "loop0",
            "tool": "get_candidate_scorecard",
            "args": {"candidate_id": "cand-beta"},
        })
        await _call(mcp, "close_campaign", {
            "campaign_id": cid,
        })
        r = await _call(mcp, "record_promotion_verdict", {
            "campaign_id": cid, "verdict": "promote",
            "decided_by": "human:x",
            "evidence_ref_ids": [ev["evidence_ref_id"]],
            "claimed_rung": "positive",
        })
        assert "error" in r and "below" in r["error"]
        # Nothing pushed upstream on a refused verdict.
        assert not any(
            t == "record_promotion_decision"
            for t, _ in adaptors.promotion.pushed
        )
        r = await _call(mcp, "record_promotion_verdict", {
            "campaign_id": cid, "verdict": "promote",
            "decided_by": "human:x",
            "evidence_ref_ids": [ev["evidence_ref_id"]],
            "claimed_rung": "strong",
        })
        assert "error" not in r, r
        assert r["declared_rung"] == "strong"
        tool, args = adaptors.promotion.pushed[-1]
        assert tool == "record_promotion_decision"
        assert args["claimed_rung"] == "strong"


async def _open_campaign(mcp):
    r = await _call(mcp, "open_campaign", {
        "contract_id": "contract-1",
        "challenger_id": "cand-beta",
        "budget": {}, "seeds": [1],
    })
    assert "error" not in r, r
    return r
