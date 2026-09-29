"""rc-9 diagnostic findings — plan-20260928-0642Z regression tests.

R18 incomplete_campaigns — a started-but-one-armed open campaign was
    invisible to every zetesis check (camp-4f94d190 specimen).
R19 arete.rollback minted no claim — mdec- rows indistinguishable.
R20 candidate/contract/decision missing from anamnesis RefType.
R21 episteme claim source_id pointed at the candidate, not the record.
R22 open_arm_campaign docstring omitted the fill mechanism.
R23 assert_claim dedup response dropped valid_until.
"""

import json

import pytest


# --- shared in-process helpers ----------------------------------------


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


async def _call(mcp_client, name, args):
    result = await mcp_client.call_tool(name, args)
    text = result.content[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        if result.is_error:
            return {"error": text}
        raise


# --- R23: dedup response echoes valid_until ---------------------------


@pytest.mark.asyncio
class TestDedupEchoesValidUntil:
    async def test_dedup_returns_stored_valid_until(self, tmp_path):
        """The non-dedup path returns valid_until; a deduping caller
        deserves the same read-back without a get_claim round-trip."""
        from mcp.client import Client
        from ml_anamnesis_mcp.server import create_server
        from ml_anamnesis_mcp.state.store import MemoryStore

        store = MemoryStore(str(tmp_path / "a.db"))
        store.connect()
        try:
            client = Client(create_server(store))
            async with client:
                first = await _call(client, "assert_claim", {
                    "content": "dedup echo test claim",
                    "type": "empirical",
                    "confidence": 0.5,
                    "valid_until": "2026-12-31T00:00:00Z",
                    "evidence": [{
                        "to_ref": "trial-x", "ref_type": "trial",
                        "relation": "tested_by",
                    }],
                })
                assert "error" not in first, first
                again = await _call(client, "assert_claim", {
                    "content": "dedup echo test claim",
                    "type": "empirical",
                    "confidence": 0.5,
                })
            assert again.get("deduplicated") is True
            assert again["claim_id"] == first["claim_id"]
            assert again["valid_until"] == "2026-12-31T00:00:00+00:00"
        finally:
            store.close()


# --- R20: Loop-0 ref types in anamnesis + prefix maps -----------------


class TestRefTypeVocabulary:
    @pytest.mark.asyncio
    async def test_relate_accepts_candidate_contract_decision(
        self, tmp_path
    ):
        """Loop-0 entity families cand-/contract-/decision- were
        typed 'external' — the closed enum now covers them."""
        from mcp.client import Client
        from mcp.server.mcpserver.exceptions import ToolError
        from ml_anamnesis_mcp.server import create_server
        from ml_anamnesis_mcp.state.store import MemoryStore

        store = MemoryStore(str(tmp_path / "a.db"))
        store.connect()
        try:
            client = Client(create_server(store))
            async with client:
                claim = await _call(client, "assert_claim", {
                    "content": "typed ref edge test",
                    "type": "empirical",
                    "confidence": 0.5,
                    "evidence": [{
                        "to_ref": "trial-x", "ref_type": "trial",
                        "relation": "tested_by",
                    }],
                })
                cid = claim["claim_id"]
                for rt in ("candidate", "contract", "decision"):
                    try:
                        r = await _call(client, "relate", {
                            "from_claim": cid,
                            "to_ref": f"{rt}-x1",
                            "ref_type": rt,
                            "relation": "derived_from",
                        })
                    except ToolError as e:
                        r = {"error": str(e)}
                    assert "error" not in r, (rt, r)
        finally:
            store.close()

    def test_arete_prefix_map_types_loop0_refs(self):
        from ml_arete_mcp.tools.decisions import _ref_type_for

        assert _ref_type_for("cand-abc") == "candidate"
        assert _ref_type_for("contract-abc") == "contract"
        assert _ref_type_for("decision-abc") == "decision"
        assert _ref_type_for("zzz-future") == "external"

    def test_zetesis_prefix_map_types_loop0_refs(self):
        from ml_zetesis_mcp.tools.investigation import _ref_type_for

        assert _ref_type_for("cand-abc") == "candidate"
        assert _ref_type_for("contract-abc") == "contract"
        assert _ref_type_for("decision-abc") == "decision"


# --- R19: rollback mints a claim like record_meta_decision ------------


class _RecordingClaims:
    """Minimal claims-role fake — records assert_claim calls."""

    def __init__(self):
        self.minted = []

    async def assert_claim(
        self, content, type, confidence, evidence=None, source_id=None,
        confidence_basis=None,
    ):
        cid = f"claim-{len(self.minted) + 1:04d}"
        self.minted.append({
            "claim_id": cid, "content": content,
            "evidence": evidence, "source_id": source_id,
        })
        return {"claim_id": cid, "status": "created"}


@pytest.mark.asyncio
class TestRollbackMintsClaim:
    async def test_rollback_mints_and_persists_claim_id(self, tmp_path):
        """rollback previously wrote an mdec- row with claim_id NULL —
        indistinguishable from a minted sibling. Now it mints through
        the same helper and stores claim_id on the row."""
        from ml_arete_mcp.clients.adaptors import Adaptors
        from ml_arete_mcp.server import create_server
        from ml_arete_mcp.state.models import (
            EvidenceContext, EvidenceRef, EvidenceSource,
            ImproverVersion, MetaContract, Tournament,
        )
        from ml_arete_mcp.state.store import ImproverStore
        from mcp.client import Client

        store = ImproverStore(str(tmp_path / "i.db"))
        store.connect()
        claims = _RecordingClaims()
        adaptors = Adaptors()
        adaptors.claims = claims
        try:
            store.create_improver(ImproverVersion(
                id="imp-parent", code_artifact_digest="sha256:x",
                model_ref="m",
            ))
            store.create_improver(ImproverVersion(
                id="imp-cand", parent_id="imp-parent",
                code_artifact_digest="sha256:y", model_ref="m",
            ))
            store.create_meta_contract(MetaContract(
                id="mc-1", version=1,
                metrics={"primary_metric": "hits", "direction": "max"},
                promotion_policy={"min_gain": 1.0},
            ))
            store.create_tournament(Tournament(
                id="tourn-1", contract_id="mc-1",
                parent_improver_id="imp-parent",
                candidate_improver_id="imp-cand",
                budget={"descendant_runs": 1},
            ))
            store.create_evidence_ref(EvidenceRef(
                id="eref-1",
                context_type=EvidenceContext.tournament,
                context_id="tourn-1",
                source=EvidenceSource.loop0,
                tool="list_trials",
                ref_ids=["trial-1", "cand-9"],
            ))

            client = Client(create_server(store, adaptors=adaptors))
            async with client:
                r = await _call(client, "rollback", {
                    "candidate_improver_id": "imp-cand",
                    "rationale": "regression on holdout",
                    "decided_by": "human:reviewer",
                    "evidence_refs": ["eref-1"],
                })
            assert "error" not in r, r
            assert r["claim_status"] == "minted"
            assert r["claim_id"] == "claim-0001"
            # The decision row carries the claim — no more NULL gap.
            dec = store.get_meta_decision(r["decision_id"])
            assert dec.claim_id == "claim-0001"
            # source_id convention: the decision record produced it.
            mint = claims.minted[0]
            assert mint["source_id"] == r["decision_id"]
            assert "'rollback'" in mint["content"]
            # cand-9 typed as candidate, not external, now that the
            # vocabulary covers it.
            edge_types = {
                e["to_ref"]: e["ref_type"] for e in mint["evidence"]
            }
            assert edge_types == {
                "trial-1": "trial", "cand-9": "candidate",
            }
        finally:
            store.close()


# --- R18: incomplete_campaigns -----------------------------------------


def _zstore(tmp_path):
    from ml_zetesis_mcp.state.store import SearchStore

    ss = SearchStore(str(tmp_path / "s.db"))
    ss.connect()
    return ss


def _campaign(cid, **kw):
    from ml_zetesis_mcp.state.models import PromotionCampaign

    return PromotionCampaign(
        id=cid, contract_id="c1",
        champion_id="cand-a", challenger_id="cand-b",
        primary_metric="hits",
        budget={"programmes_per_arm": 1, "trials_per_programme": 2},
        **kw,
    )


def _spawn(sid, cid, arm, created_at=None, status="spawned"):
    from ml_zetesis_mcp.state.models import (
        CampaignArm, CampaignSpawn, SpawnStatus,
    )

    kw = {}
    if created_at:
        kw["created_at"] = created_at
    return CampaignSpawn(
        id=sid, campaign_id=cid, arm=CampaignArm(arm),
        programme_id=f"prog-{sid}", budget={},
        status=SpawnStatus(status), **kw,
    )


def _result(rid, cid, arm, created_at=None):
    from ml_zetesis_mcp.state.models import (
        CampaignArm, CampaignResult,
    )

    kw = {}
    if created_at:
        kw["created_at"] = created_at
    return CampaignResult(
        id=rid, campaign_id=cid, arm=CampaignArm(arm),
        programme_id=f"prog-{rid}",
        metrics={"hits": 5.0}, **kw,
    )


@pytest.mark.asyncio
class TestIncompleteCampaigns:
    async def test_one_armed_idle_campaign_flags(self, tmp_path):
        """The camp-4f94d190 shape: open, spawn-linked, results on
        challenger only, stale — invisible to every check before."""
        from ml_zetesis_mcp.integrity.checks import run_checks as zc

        ss = _zstore(tmp_path)
        try:
            ss.create_campaign(_campaign(
                "camp-half",
                created_at="2020-01-01T00:00:00+00:00",
            ))
            ss.create_campaign_spawn(_spawn(
                "spawn-1", "camp-half", "challenger",
                created_at="2020-01-01T00:00:00+00:00",
            ))
            ss.create_campaign_result(_result(
                "cres-1", "camp-half", "challenger",
                created_at="2020-01-01T00:00:00+00:00",
            ))
            payload = await zc(ss, stale_campaign_seconds=3600)
            chk = next(
                c for c in payload["checks"]
                if c["name"] == "incomplete_campaigns"
            )
            assert not chk["ok"]
            v = chk["violations"][0]
            assert v["campaign_id"] == "camp-half"
            assert v["missing_arms"] == ["champion"]
            assert v["arms_with_results"] == ["challenger"]
            assert v["pending_spawns"] == 1
        finally:
            ss.close()

    async def test_fresh_one_armed_is_clean(self, tmp_path):
        """Transient single-arm state mid-orchestration must not
        flag — the idle gate is what makes it debt."""
        from ml_zetesis_mcp.integrity.checks import run_checks as zc

        ss = _zstore(tmp_path)
        try:
            ss.create_campaign(_campaign("camp-fresh"))
            ss.create_campaign_spawn(_spawn(
                "spawn-1", "camp-fresh", "challenger"
            ))
            ss.create_campaign_result(_result(
                "cres-1", "camp-fresh", "challenger"
            ))
            payload = await zc(ss, stale_campaign_seconds=3600)
            chk = next(
                c for c in payload["checks"]
                if c["name"] == "incomplete_campaigns"
            )
            assert chk["ok"], chk
        finally:
            ss.close()

    async def test_both_arms_populated_is_clean(self, tmp_path):
        from ml_zetesis_mcp.integrity.checks import run_checks as zc

        ss = _zstore(tmp_path)
        try:
            ss.create_campaign(_campaign(
                "camp-full",
                created_at="2020-01-01T00:00:00+00:00",
            ))
            for arm in ("champion", "challenger"):
                ss.create_campaign_result(_result(
                    f"cres-{arm}", "camp-full", arm,
                    created_at="2020-01-01T00:00:00+00:00",
                ))
            payload = await zc(ss, stale_campaign_seconds=3600)
            chk = next(
                c for c in payload["checks"]
                if c["name"] == "incomplete_campaigns"
            )
            assert chk["ok"], chk
        finally:
            ss.close()

    async def test_unstarted_campaign_not_flagged(self, tmp_path):
        """A carryable open campaign with zero spawns/results is early
        state, not debt — unrunnable_campaigns owns the wedge case."""
        from ml_zetesis_mcp.integrity.checks import run_checks as zc

        ss = _zstore(tmp_path)
        try:
            ss.create_campaign(_campaign(
                "camp-empty",
                created_at="2020-01-01T00:00:00+00:00",
            ))
            payload = await zc(ss, stale_campaign_seconds=3600)
            chk = next(
                c for c in payload["checks"]
                if c["name"] == "incomplete_campaigns"
            )
            assert chk["ok"], chk
        finally:
            ss.close()


# --- R21: episteme claim source_id is the conclusion ------------------


class _EpistemeClaims:
    """ClaimsRole duck — records assert_claim calls."""

    def __init__(self):
        self.asserts = []

    async def assert_claim(
        self, content, type, confidence, evidence=None, source_id=None,
        confidence_basis=None,
    ):
        self.asserts.append({
            "content": content, "source_id": source_id,
            "evidence": evidence,
        })
        return {"claim_id": "claim-9", "status": "created"}


@pytest.mark.asyncio
class TestClaimSourceId:
    async def test_source_id_is_conclusion_not_candidate(
        self, tmp_path
    ):
        """source_id names the producing record across all servers —
        for the programme-close mint that is the conclusion, not the
        candidate_version_id (rc-9 F5)."""
        from mcp.client import Client
        from ml_episteme_mcp.clients.adaptor import MCPAdaptor
        from ml_episteme_mcp.server import create_server
        from ml_episteme_mcp.state.models import (
            Belief, Hypothesis, Observation, Programme,
            ProgrammeStatus, Trial, TrialStatus,
        )
        from ml_episteme_mcp.state.store import StateStore

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
                candidate_version_id="cand-77",
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
                id="belief-1", programme_id="prog-1",
                state_json="{}",
            ))

            client = Client(create_server(store, adaptor))
            async with client:
                r = await _call(client, "conclude_hypothesis", {
                    "programme_id": "prog-1",
                    "hypothesis_id": "hyp-1",
                    "verdict": "accepted",
                    "evidence_summary": "done",
                })
            assert "error" not in r, r
            assert r["claim_status"] == "minted"
            assert claims.asserts[0]["source_id"] == r["conclusion_id"]
        finally:
            store.close()


# --- R22: open_arm_campaign docstring names the mechanism -------------


class TestOpenArmCampaignDoc:
    def test_docstring_names_campaign_arm_fill(self):
        from ml_arete_mcp.server import create_server
        from ml_arete_mcp.state.store import ImproverStore
        import tempfile
        import os

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            store = ImproverStore(path)
            store.connect()
            mcp = create_server(store)
            tool = mcp._tool_manager.get_tool("open_arm_campaign")
            doc = tool.description or ""
            assert "spawn_arm_programme" in doc
            assert "campaign_arm" in doc
            store.close()
        finally:
            os.unlink(path)
