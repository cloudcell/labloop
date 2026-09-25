"""Loop-1 promotion machinery — roster, campaigns, the verdict write
path, and the evidence channel's dual context.

The promotion channel is the ecosystem's first cross-server write:
whitelisted to Loop 0's three insert-only lineage surfaces. Tests use
the conftest fakes — FakeUpstreamAdaptor for reads, FakePromotionAdaptor
recording pushes for writes.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from .conftest import (
    FakePromotionAdaptor,
    FakeUpstreamAdaptor,
    call_tool,
)
from ml_zetesis_mcp.enforcement.checks import (
    PROMOTION_WRITE_TOOLS,
    check_campaign_evidence_refs,
    check_campaign_open,
    check_promotion_tool_whitelisted,
    check_verdict_decided_by,
)
from ml_zetesis_mcp.state.models import (
    CampaignArm,
    CampaignResult,
    CampaignStatus,
    DerivedStatus,
    EvidenceRef,
    EvidenceSource,
    Investigation,
    PolicyStatus,
    PromotionCampaign,
    RosterEntry,
    SearchPolicy,
)


# --- Fake payloads the promotion flow needs ---

CANDIDATES = json.dumps({
    "candidates": [
        {"id": "cand-alpha", "parent_id": None},
        {"id": "cand-beta", "parent_id": "cand-alpha"},
    ]
})
INCUMBENT = json.dumps({
    "candidate_id": "cand-alpha",
    "candidate": {"id": "cand-alpha", "model_ref": "m"},
})
CONTRACT = json.dumps({
    "contract": {
        "id": "contract-c1",
        "metrics": {"hits": "maximize"},
        "budget": {}, "promotion_policy": {},
    }
})


def _programmes(cand_a="cand-alpha", cand_b="cand-beta"):
    """list_programmes payload honouring the candidate filter."""
    all_progs = [
        {"programme_id": "prog-champ", "candidate_version_id": cand_a},
        {"programme_id": "prog-chall", "candidate_version_id": cand_b},
    ]

    class P:
        pass

    return all_progs


def _evidence_adaptor(programmes=None, candidates=CANDIDATES):
    """An evidence fake that honours the candidate_version_id filter —
    the real upstream filters server-side."""
    programmes = programmes if programmes is not None else _programmes()

    class F(FakeUpstreamAdaptor):
        async def pull(self, tool, args):
            self.calls.append((tool, args))
            if tool == "list_candidates":
                return candidates
            if tool == "get_incumbent":
                return INCUMBENT
            if tool == "get_evaluation_contract":
                return CONTRACT
            if tool == "get_candidate":
                return json.dumps({"candidate": {
                    "id": args["candidate_id"], "parent_id": "cand-alpha",
                }})
            if tool == "list_programmes":
                cv = args.get("candidate_version_id")
                progs = [
                    p for p in programmes
                    if cv is None or p["candidate_version_id"] == cv
                ]
                return json.dumps({"programmes": progs})
            if tool == "get_candidate_scorecard":
                return json.dumps({"candidate_id": args["candidate_id"]})
            return self.payloads.get(tool, json.dumps({"ok": True}))

    return F()


# --- Store layer ---


class TestPromotionStore:
    def test_roster_upsert_and_list(self, search_store):
        search_store.upsert_roster_entry(RosterEntry(
            id="cand-a", derived_status=DerivedStatus.champion,
        ))
        search_store.upsert_roster_entry(RosterEntry(
            id="cand-b", parent_id="cand-a",
            derived_status=DerivedStatus.challenger,
        ))
        entries, total = search_store.list_roster_entries()
        assert total == 2
        champs, _ = search_store.list_roster_entries(status="champion")
        assert [e.id for e in champs] == ["cand-a"]

    def test_roster_upsert_updates_status(self, search_store):
        search_store.upsert_roster_entry(RosterEntry(
            id="cand-a", derived_status=DerivedStatus.challenger,
        ))
        search_store.upsert_roster_entry(RosterEntry(
            id="cand-a", derived_status=DerivedStatus.champion,
            notes={"promoted_by": "camp-x"},
        ))
        e = search_store.get_roster_entry("cand-a")
        assert e.derived_status is DerivedStatus.champion
        assert e.notes == {"promoted_by": "camp-x"}

    def test_search_policy_versions_and_retire(self, search_store):
        search_store.create_search_policy(SearchPolicy(
            id="spol-1", name="default", version=1,
            status=PolicyStatus.active,
        ))
        search_store.retire_active_policies("default")
        search_store.create_search_policy(SearchPolicy(
            id="spol-2", name="default", version=2,
        ))
        assert search_store.next_policy_version("default") == 3
        active = [
            p for p in search_store.list_search_policies("default")
            if p.status.value == "retired"
        ]
        assert len(active) == 1

    def test_campaign_crud(self, search_store):
        c = PromotionCampaign(
            id="camp-1", contract_id="contract-x",
            champion_id="cand-a", challenger_id="cand-b",
            primary_metric="hits", budget={"seeds": 3},
        )
        search_store.create_campaign(c)
        search_store.create_campaign_result(CampaignResult(
            id="r1", campaign_id="camp-1", arm=CampaignArm.champion,
            programme_id="prog-1", metrics={"hits": 80},
        ))
        search_store.close_campaign("camp-1", 1.25)
        got = search_store.get_campaign("camp-1")
        assert got.status is CampaignStatus.closed
        assert got.promotion_score == 1.25
        assert got.closed_at is not None
        assert len(search_store.list_campaign_results("camp-1")) == 1
        search_store.set_campaign_decision("camp-1", "decision-9")
        search_store.set_campaign_claim("camp-1", "claim-9")
        got = search_store.get_campaign("camp-1")
        assert got.decision_id == "decision-9"
        assert got.claim_id == "claim-9"

    def test_evidence_ref_dual_context(self, search_store):
        search_store.create_investigation(Investigation(
            id="inv-1", question="q",
        ))
        search_store.create_campaign(PromotionCampaign(
            id="camp-1", contract_id="c", champion_id="a",
            challenger_id="b", primary_metric="m", budget={},
        ))
        search_store.create_evidence_ref(EvidenceRef(
            id="e-inv", investigation_id="inv-1",
            source=EvidenceSource.loop0, tool="t",
        ))
        search_store.create_evidence_ref(EvidenceRef(
            id="e-camp", campaign_id="camp-1",
            source=EvidenceSource.loop0, tool="t",
        ))
        assert [
            r.id for r in search_store.list_evidence_refs(
                investigation_id="inv-1"
            )
        ] == ["e-inv"]
        assert [
            r.id for r in search_store.list_evidence_refs(
                campaign_id="camp-1"
            )
        ] == ["e-camp"]
        # XOR enforced: both contexts set is rejected.
        with pytest.raises(sqlite3.IntegrityError):
            search_store.create_evidence_ref(EvidenceRef(
                id="e-bad", investigation_id="inv-1",
                campaign_id="camp-1",
                source=EvidenceSource.loop0, tool="t",
            ))

    def test_evidence_refs_migration(self, tmp_path):
        """A pre-promotion search.db (no campaign_id column) is
        rebuilt in place — old refs keep investigation context."""
        db = tmp_path / "old.db"
        conn = sqlite3.connect(db)
        conn.executescript(
            """CREATE TABLE investigations (
                id TEXT PRIMARY KEY, question TEXT, scope_json TEXT,
                budget_json TEXT, status TEXT, verdict TEXT,
                summary TEXT, implications_json TEXT,
                created_at TEXT, concluded_at TEXT);
            CREATE TABLE evidence_refs (
                id TEXT PRIMARY KEY,
                investigation_id TEXT NOT NULL
                    REFERENCES investigations(id),
                source TEXT, tool TEXT, args_json TEXT,
                ref_ids_json TEXT, created_at TEXT);
            INSERT INTO investigations VALUES
                ('inv-9','q','{}',NULL,'open',NULL,NULL,NULL,'t',NULL);
            INSERT INTO evidence_refs VALUES
                ('e9','inv-9','loop0','t','{}','[]','t');"""
        )
        conn.close()
        from ml_zetesis_mcp.state.store import SearchStore

        s = SearchStore(str(db))
        s.connect()
        s.create_campaign(PromotionCampaign(
            id="camp-9", contract_id="c", champion_id="a",
            challenger_id="b", primary_metric="m", budget={},
        ))
        s.create_evidence_ref(EvidenceRef(
            id="e10", campaign_id="camp-9",
            source=EvidenceSource.loop0, tool="t",
        ))
        old = s.list_evidence_refs(investigation_id="inv-9")
        assert [(r.id, r.campaign_id) for r in old] == [("e9", None)]
        new = s.list_evidence_refs(campaign_id="camp-9")
        assert [r.id for r in new] == ["e10"]
        s.close()


# --- Enforcement layer ---


class TestPromotionEnforcement:
    def test_write_whitelist_exactly_four(self):
        # +create_programme: the Loop-2 orchestration increment lets
        # spawn_campaign_programme create descendant programmes
        # upstream through the same insert-only surface.
        assert PROMOTION_WRITE_TOOLS == frozenset({
            "register_candidate",
            "create_evaluation_contract",
            "record_promotion_decision",
            "create_programme",
        })

    def test_whitelist_rejects_mutating_reads(self):
        assert check_promotion_tool_whitelisted("run_trial") is not None
        assert check_promotion_tool_whitelisted(
            "record_promotion_decision"
        ) is None

    def test_campaign_open_rejects_closed(self, search_store):
        search_store.create_campaign(PromotionCampaign(
            id="camp-1", contract_id="c", champion_id="a",
            challenger_id="b", primary_metric="m", budget={},
        ))
        assert check_campaign_open(search_store, "camp-1") is None
        search_store.close_campaign("camp-1", 1.0)
        err = check_campaign_open(search_store, "camp-1")
        assert err is not None and "closed" in err

    def test_campaign_evidence_anti_smuggling(self, search_store):
        search_store.create_campaign(PromotionCampaign(
            id="camp-1", contract_id="c", champion_id="a",
            challenger_id="b", primary_metric="m", budget={},
        ))
        search_store.create_campaign(PromotionCampaign(
            id="camp-2", contract_id="c", champion_id="a",
            challenger_id="b", primary_metric="m", budget={},
        ))
        search_store.create_evidence_ref(EvidenceRef(
            id="e-1", campaign_id="camp-1",
            source=EvidenceSource.loop0, tool="t",
        ))
        err = check_campaign_evidence_refs(search_store, "camp-2", ["e-1"])
        assert err is not None and "does not belong" in err

    def test_rollback_requires_human(self):
        assert check_verdict_decided_by("rollback", "agent:zetesis")
        assert check_verdict_decided_by("rollback", "human:operator") is None
        assert check_verdict_decided_by("promote", "agent:zetesis") is None


# --- Tool lifecycle (in-process, fake adaptors) ---


async def _open_campaign(mcp, adaptors, challenger="cand-beta"):
    adaptors.evidence = _evidence_adaptor()
    r = await call_tool(mcp, "refresh_roster", {"dry_run": False})
    assert "error" not in r
    r = await call_tool(mcp, "open_campaign", {
        "contract_id": "contract-c1",
        "challenger_id": challenger,
        "budget": {"seeds": 3},
    })
    assert "error" not in r, r
    return r["campaign_id"]


class TestRosterTools:
    async def test_refresh_dry_run_previews_without_writing(
        self, zetesis_server, adaptors, search_store
    ):
        adaptors.evidence = _evidence_adaptor()
        r = await call_tool(zetesis_server, "refresh_roster", {"dry_run": True})
        assert r["dry_run"] is True
        assert set(r["would_adopt"]) == {"cand-alpha", "cand-beta"}
        assert search_store.list_roster_entries()[1] == 0

    async def test_refresh_adopts_untracked(self, zetesis_server, adaptors):
        adaptors.evidence = _evidence_adaptor()
        r = await call_tool(zetesis_server, "refresh_roster", {"dry_run": False})
        assert r["upstream_candidates"] == 2
        assert set(r["adopted"]) == {"cand-alpha", "cand-beta"}
        # Second refresh adopts nothing — drift heals once.
        r2 = await call_tool(zetesis_server, "refresh_roster", {"dry_run": False})
        assert r2["adopted"] == []
        assert r2["already_tracked"] == 2

    async def test_refresh_without_adaptor_fails_clearly(
        self, zetesis_server, adaptors
    ):
        adaptors.evidence = None
        r = await call_tool(zetesis_server, "refresh_roster", {"dry_run": False})
        assert "error" in r and "evidence adaptor" in r["error"]

    async def test_refresh_reconciles_champion_pointer(
        self, zetesis_server, adaptors, search_store
    ):
        """The roster champion mirrors the upstream single-incumbent
        derivation. When the incumbent moved via a path the roster
        never saw (manual record_promotion_decision), refresh is the
        drift-healing step that re-points the annotation."""
        adaptors.evidence = _evidence_adaptor()
        # Drift: local champion cand-stale, upstream incumbent is
        # cand-alpha (INCUMBENT payload).
        search_store.upsert_roster_entry(RosterEntry(
            id="cand-stale",
            derived_status=DerivedStatus.champion,
            notes={"promoted_by": "camp-earlier"},
        ))
        r = await call_tool(zetesis_server, "refresh_roster", {"dry_run": False})
        assert "error" not in r
        champions = [
            e.id for e in search_store.list_roster_entries(
                status="champion"
            )[0]
        ]
        assert champions == ["cand-alpha"]
        stale = search_store.get_roster_entry("cand-stale")
        assert stale.derived_status is DerivedStatus.candidate
        assert stale.notes["superseded_by"] == "cand-alpha"

    async def test_refresh_keeps_champion_when_incumbent_matches(
        self, zetesis_server, adaptors, search_store
    ):
        """A correct champion annotation survives refresh — healing
        re-points drift, it does not churn honest state."""
        adaptors.evidence = _evidence_adaptor()
        search_store.upsert_roster_entry(RosterEntry(
            id="cand-alpha",
            derived_status=DerivedStatus.champion,
            notes={"promoted_by": "camp-earlier"},
        ))
        r = await call_tool(zetesis_server, "refresh_roster", {"dry_run": False})
        assert "error" not in r
        entry = search_store.get_roster_entry("cand-alpha")
        assert entry.derived_status is DerivedStatus.champion
        assert entry.notes["promoted_by"] == "camp-earlier"

    async def test_register_challenger_creates_upstream(
        self, zetesis_server, adaptors
    ):
        adaptors.evidence = _evidence_adaptor()
        r = await call_tool(zetesis_server, "register_challenger", {
            "code_artifact_digest": "none",
            "model_ref": "m",
            "capability_profile": {"tools": []},
            "parent_id": "cand-alpha",
        })
        assert r["candidate_id"].startswith("cand-new")
        tool, args = adaptors.promotion.pushed[-1]
        assert tool == "register_candidate"
        assert args["parent_id"] == "cand-alpha"

    async def test_register_challenger_annotates_existing(
        self, zetesis_server, adaptors
    ):
        adaptors.evidence = _evidence_adaptor()
        r = await call_tool(zetesis_server, "register_challenger", {
            "candidate_id": "cand-beta",
        })
        assert r["derived_status"] == "challenger"
        entry = zetesis_server  # roster check via store listing
        r = await call_tool(zetesis_server, "list_candidates", {
            "status": "challenger",
        })
        assert any(
            c["id"] == "cand-beta" for c in r["candidates"]
        )

    async def test_register_challenger_missing_fields(
        self, zetesis_server, adaptors
    ):
        r = await call_tool(zetesis_server, "register_challenger", {})
        assert "error" in r and "candidate_id" in r["error"]

    async def test_register_search_policy_versions(
        self, zetesis_server
    ):
        r1 = await call_tool(zetesis_server, "register_search_policy", {
            "name": "default", "policy": {"k": 1},
        })
        r2 = await call_tool(zetesis_server, "register_search_policy", {
            "name": "default", "policy": {"k": 2},
        })
        assert r1["version"] == 1 and r2["version"] == 2
        r = await call_tool(zetesis_server, "list_search_policies", {
            "name": "default",
        })
        statuses = {p["version"]: p["status"] for p in r["policies"]}
        assert statuses == {1: "retired", 2: "active"}


class TestCampaignLifecycle:
    async def test_open_requires_rostered_challenger(
        self, zetesis_server, adaptors
    ):
        adaptors.evidence = _evidence_adaptor()
        r = await call_tool(zetesis_server, "open_campaign", {
            "contract_id": "contract-c1",
            "challenger_id": "cand-ghost",
            "budget": {},
        })
        assert "error" in r and "roster" in r["error"]

    async def test_open_requires_incumbent(
        self, zetesis_server, adaptors
    ):
        class NoIncumbent(_evidence_adaptor().__class__):
            async def pull(self, tool, args):
                if tool == "get_incumbent":
                    return json.dumps({"candidate_id": None})
                return await super().pull(tool, args)
        adaptors.evidence = NoIncumbent()
        await call_tool(zetesis_server, "refresh_roster", {"dry_run": False})
        r = await call_tool(zetesis_server, "open_campaign", {
            "contract_id": "contract-c1",
            "challenger_id": "cand-beta",
            "budget": {},
        })
        assert "error" in r and "incumbent" in r["error"]

    async def test_result_attribution_gate(
        self, zetesis_server, adaptors
    ):
        cid = await _open_campaign(zetesis_server, adaptors)
        # prog-champ belongs to the champion arm — recording it as a
        # challenger result is rejected.
        r = await call_tool(zetesis_server, "record_campaign_result", {
            "campaign_id": cid, "arm": "challenger",
            "programme_id": "prog-champ", "metrics": {"hits": 999},
        })
        assert "error" in r and "attributed" in r["error"]
        r = await call_tool(zetesis_server, "record_campaign_result", {
            "campaign_id": cid, "arm": "challenger",
            "programme_id": "prog-chall", "metrics": {"hits": 120},
        })
        assert "error" not in r

    async def test_close_requires_both_arms(
        self, zetesis_server, adaptors
    ):
        cid = await _open_campaign(zetesis_server, adaptors)
        await call_tool(zetesis_server, "record_campaign_result", {
            "campaign_id": cid, "arm": "champion",
            "programme_id": "prog-champ", "metrics": {"hits": 80},
        })
        r = await call_tool(zetesis_server, "close_campaign", {
            "campaign_id": cid,
        })
        assert "error" in r and "challenger" in r["error"]

    async def test_close_computes_ratio(
        self, zetesis_server, adaptors
    ):
        cid = await _open_campaign(zetesis_server, adaptors)
        await call_tool(zetesis_server, "record_campaign_result", {
            "campaign_id": cid, "arm": "champion",
            "programme_id": "prog-champ", "metrics": {"hits": 80},
        })
        await call_tool(zetesis_server, "record_campaign_result", {
            "campaign_id": cid, "arm": "challenger",
            "programme_id": "prog-chall", "metrics": {"hits": 120},
        })
        r = await call_tool(zetesis_server, "close_campaign", {
            "campaign_id": cid,
        })
        assert r["promotion_score"] == pytest.approx(1.5)

    async def test_verdict_requires_closed_campaign(
        self, zetesis_server, adaptors
    ):
        cid = await _open_campaign(zetesis_server, adaptors)
        r = await call_tool(zetesis_server, "record_promotion_verdict", {
            "campaign_id": cid, "verdict": "promote",
            "decided_by": "human:x", "evidence_ref_ids": ["e"],
        })
        assert "error" in r and "open" in r["error"]

    async def test_verdict_requires_campaign_evidence(
        self, zetesis_server, adaptors
    ):
        cid = await _open_campaign(zetesis_server, adaptors)
        for arm, prog, hits in (
            ("champion", "prog-champ", 80),
            ("challenger", "prog-chall", 120),
        ):
            await call_tool(zetesis_server, "record_campaign_result", {
                "campaign_id": cid, "arm": arm,
                "programme_id": prog, "metrics": {"hits": hits},
            })
        await call_tool(zetesis_server, "close_campaign", {
            "campaign_id": cid,
        })
        r = await call_tool(zetesis_server, "record_promotion_verdict", {
            "campaign_id": cid, "verdict": "promote",
            "decided_by": "human:x",
        })
        assert "error" in r and "evidence" in r["error"]

    async def test_verdict_pushes_upstream_and_annotates(
        self, zetesis_server, adaptors, search_store
    ):
        cid = await _open_campaign(zetesis_server, adaptors)
        for arm, prog, hits in (
            ("champion", "prog-champ", 80),
            ("challenger", "prog-chall", 120),
        ):
            await call_tool(zetesis_server, "record_campaign_result", {
                "campaign_id": cid, "arm": arm,
                "programme_id": prog, "metrics": {"hits": hits},
            })
        ev = await call_tool(zetesis_server, "pull_campaign_evidence", {
            "campaign_id": cid, "source": "loop0",
            "tool": "get_candidate_scorecard",
            "args": {"candidate_id": "cand-beta"},
        })
        await call_tool(zetesis_server, "close_campaign", {
            "campaign_id": cid,
        })
        r = await call_tool(zetesis_server, "record_promotion_verdict", {
            "campaign_id": cid, "verdict": "promote",
            "decided_by": "human:x",
            "evidence_ref_ids": [ev["evidence_ref_id"]],
        })
        assert r["decision_id"].startswith("decision-")
        assert r["claim_status"] == "minted"
        tool, args = adaptors.promotion.pushed[-1]
        assert tool == "record_promotion_decision"
        assert args["candidate_id"] == "cand-beta"
        assert args["verdict"] == "promote"
        assert args["contract_id"] == "contract-c1"
        # Roster: challenger promoted, champion demoted — annotations.
        entry = search_store.get_roster_entry("cand-beta")
        assert entry.derived_status is DerivedStatus.champion
        champ = search_store.get_roster_entry("cand-alpha")
        assert champ.derived_status is DerivedStatus.candidate
        # A second verdict on the same campaign is refused — insert-only.
        r2 = await call_tool(zetesis_server, "record_promotion_verdict", {
            "campaign_id": cid, "verdict": "promote",
            "decided_by": "human:x",
            "evidence_ref_ids": [ev["evidence_ref_id"]],
        })
        assert "error" in r2 and "already" in r2["error"]

    async def test_verdict_promote_sweeps_stale_champions(
        self, zetesis_server, adaptors, search_store
    ):
        """A champion annotation can outlive the campaign that made
        it — the incumbent moves upstream via paths that bypass
        campaigns (direct record_promotion_decision), so the roster
        champion at verdict time may not be campaign.champion_id.
        The promote sweep must demote every champion entry, not just
        the one the campaign recorded at open."""
        cid = await _open_campaign(zetesis_server, adaptors)
        # Drift: a champion the campaign never saw — as if a prior
        # campaign promoted it and a manual upstream decision moved
        # the incumbent on.
        search_store.upsert_roster_entry(RosterEntry(
            id="cand-stale",
            derived_status=DerivedStatus.champion,
            notes={"promoted_by": "camp-earlier"},
        ))
        for arm, prog, hits in (
            ("champion", "prog-champ", 80),
            ("challenger", "prog-chall", 120),
        ):
            await call_tool(zetesis_server, "record_campaign_result", {
                "campaign_id": cid, "arm": arm,
                "programme_id": prog, "metrics": {"hits": hits},
            })
        ev = await call_tool(zetesis_server, "pull_campaign_evidence", {
            "campaign_id": cid, "source": "loop0",
            "tool": "get_candidate_scorecard",
            "args": {"candidate_id": "cand-beta"},
        })
        await call_tool(zetesis_server, "close_campaign", {
            "campaign_id": cid,
        })
        r = await call_tool(zetesis_server, "record_promotion_verdict", {
            "campaign_id": cid, "verdict": "promote",
            "decided_by": "human:x",
            "evidence_ref_ids": [ev["evidence_ref_id"]],
        })
        assert "error" not in r
        champions = [
            e.id for e in search_store.list_roster_entries(
                status="champion"
            )[0]
        ]
        assert champions == ["cand-beta"]
        stale = search_store.get_roster_entry("cand-stale")
        assert stale.derived_status is DerivedStatus.candidate
        assert stale.notes["superseded_by"] == "cand-beta"

    async def test_rollback_requires_human(
        self, zetesis_server, adaptors
    ):
        cid = await _open_campaign(zetesis_server, adaptors)
        for arm, prog, hits in (
            ("champion", "prog-champ", 80),
            ("challenger", "prog-chall", 120),
        ):
            await call_tool(zetesis_server, "record_campaign_result", {
                "campaign_id": cid, "arm": arm,
                "programme_id": prog, "metrics": {"hits": hits},
            })
        ev = await call_tool(zetesis_server, "pull_campaign_evidence", {
            "campaign_id": cid, "source": "loop0",
            "tool": "get_candidate_scorecard",
            "args": {"candidate_id": "cand-beta"},
        })
        await call_tool(zetesis_server, "close_campaign", {
            "campaign_id": cid,
        })
        r = await call_tool(zetesis_server, "record_promotion_verdict", {
            "campaign_id": cid, "verdict": "rollback",
            "decided_by": "agent:zetesis",
            "evidence_ref_ids": [ev["evidence_ref_id"]],
        })
        assert "error" in r and "human:" in r["error"]

    async def test_evidence_channel_stays_read_only(
        self, zetesis_server, adaptors
    ):
        """The promotion write tools are NOT on the evidence read
        whitelist — pull_evidence cannot reach them."""
        cid = await _open_campaign(zetesis_server, adaptors)
        r = await call_tool(zetesis_server, "pull_campaign_evidence", {
            "campaign_id": cid, "source": "loop0",
            "tool": "record_promotion_decision",
            "args": {},
        })
        assert "error" in r and "Input should be" in r["error"]

    async def test_verdict_without_promotion_adaptor(
        self, zetesis_server, adaptors
    ):
        cid = await _open_campaign(zetesis_server, adaptors)
        for arm, prog, hits in (
            ("champion", "prog-champ", 80),
            ("challenger", "prog-chall", 120),
        ):
            await call_tool(zetesis_server, "record_campaign_result", {
                "campaign_id": cid, "arm": arm,
                "programme_id": prog, "metrics": {"hits": hits},
            })
        ev = await call_tool(zetesis_server, "pull_campaign_evidence", {
            "campaign_id": cid, "source": "loop0",
            "tool": "get_candidate_scorecard",
            "args": {"candidate_id": "cand-beta"},
        })
        await call_tool(zetesis_server, "close_campaign", {
            "campaign_id": cid,
        })
        adaptors.promotion = None
        r = await call_tool(zetesis_server, "record_promotion_verdict", {
            "campaign_id": cid, "verdict": "promote",
            "decided_by": "human:x",
            "evidence_ref_ids": [ev["evidence_ref_id"]],
        })
        assert "error" in r and "promotion adaptor" in r["error"]
