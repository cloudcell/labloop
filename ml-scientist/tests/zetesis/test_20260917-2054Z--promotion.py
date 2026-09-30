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
        "budget": {},
        # sesoi_d=4.0 keeps required_n=1 — fixture convenience, not a
        # design point. "not_worth" rung means any claimed rung passes
        # ordering; promote still must claim one.
        "promotion_policy": {
            "sesoi_d": 4.0, "target_power": 0.8,
            "min_evidence_rung": "not_worth",
        },
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
        "budget": {},
        "seeds": [1],
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
            "seeds": [1],
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
            "claimed_rung": "not_worth",  # null statistic: ceiling = declared min (rc-12 W3)
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
            "claimed_rung": "not_worth",  # null statistic: ceiling = declared min (rc-12 W3)
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
            "claimed_rung": "not_worth",  # null statistic: ceiling = declared min (rc-12 W3)
        })
        assert "error" in r and "promotion adaptor" in r["error"]


# --- rc-5 B1: the contract's primary metric gates at write ----------


def _evidence_adaptor_with_decisions(decisions_by_cand):
    """Evidence fake that also serves list_promotion_decisions —
    the verdict trail B2 reconciliation reads."""
    base = _evidence_adaptor()

    class F(base.__class__):
        async def pull(self, tool, args):
            if tool == "list_promotion_decisions":
                return json.dumps({
                    "decisions": decisions_by_cand.get(
                        args.get("candidate_id"), []
                    )
                })
            return await super().pull(tool, args)

    return F()


class TestCampaignMetricGate:
    async def test_missing_primary_metric_refused_at_write(
        self, zetesis_server, adaptors, search_store
    ):
        """rc-5 camp F1: a result without the contract's primary
        metric must be refused at WRITE — the record is insert-only,
        so the bad row cannot be repaired later."""
        cid = await _open_campaign(zetesis_server, adaptors)
        r = await call_tool(zetesis_server, "record_campaign_result", {
            "campaign_id": cid, "arm": "challenger",
            "programme_id": "prog-chall",
            "metrics": {"wrong_metric": 1},
        })
        assert "error" in r
        assert "primary_metric" in r["error"]
        assert "hits" in r["error"]  # names the required metric
        # Nothing persisted — the insert-only record stays clean.
        assert search_store.list_campaign_results(cid) == []

    async def test_result_with_primary_metric_accepted(
        self, zetesis_server, adaptors
    ):
        cid = await _open_campaign(zetesis_server, adaptors)
        r = await call_tool(zetesis_server, "record_campaign_result", {
            "campaign_id": cid, "arm": "challenger",
            "programme_id": "prog-chall",
            "metrics": {"hits": 120, "latency_ms": 40},
        })
        assert "error" not in r


# --- rc-5 B2: the roster derives rolled_back from the verdict trail -


class TestRolledBackDerivation:
    async def test_upstream_rollback_derives_rolled_back(
        self, zetesis_server, adaptors, search_store
    ):
        """A 'rollback' verdict on the upstream trail must derive
        rolled_back — the roster mirrors the decision trail, not just
        the incumbent pointer (the promote row stays upstream as
        history; the derived status is the mirror)."""
        adaptors.evidence = _evidence_adaptor_with_decisions({
            "cand-beta": [
                {"id": "d1", "verdict": "promote",
                 "created_at": "2026-01-01T00:00:00+00:00"},
                {"id": "d2", "verdict": "rollback",
                 "created_at": "2026-01-02T00:00:00+00:00"},
            ],
        })
        r = await call_tool(
            zetesis_server, "refresh_roster", {"dry_run": False})
        assert "error" not in r
        assert "cand-beta" in r["reconciled"]["rolled_back"]
        e = search_store.get_roster_entry("cand-beta")
        assert e.derived_status is DerivedStatus.rolled_back
        assert e.notes["rolled_back_by"] == "refresh_roster"
        assert e.notes["decision_id"] == "d2"

    async def test_rollback_dry_run_writes_nothing(
        self, zetesis_server, adaptors, search_store
    ):
        adaptors.evidence = _evidence_adaptor_with_decisions({
            "cand-beta": [
                {"id": "d1", "verdict": "promote",
                 "created_at": "2026-01-01T00:00:00+00:00"},
                {"id": "d2", "verdict": "rollback",
                 "created_at": "2026-01-02T00:00:00+00:00"},
            ],
        })
        r = await call_tool(
            zetesis_server, "refresh_roster", {"dry_run": True})
        assert r["dry_run"] is True
        # A dry run previews the would-be roster — promote→rollback
        # flips the previewed row to rolled_back.
        assert "cand-beta" in r["would_reconcile"]["rolled_back"]
        assert search_store.list_roster_entries()[1] == 0

    async def test_late_promote_un_rolls(
        self, zetesis_server, adaptors, search_store
    ):
        """Latest decision wins: a promote AFTER the rollback clears
        the derived state — the newest verdict governs."""
        adaptors.evidence = _evidence_adaptor_with_decisions({
            "cand-beta": [
                {"id": "d1", "verdict": "rollback",
                 "created_at": "2026-01-01T00:00:00+00:00"},
                {"id": "d2", "verdict": "promote",
                 "created_at": "2026-01-03T00:00:00+00:00"},
            ],
        })
        await call_tool(
            zetesis_server, "refresh_roster", {"dry_run": False})
        e = search_store.get_roster_entry("cand-beta")
        assert e.derived_status is not DerivedStatus.rolled_back

    async def test_non_rollback_verdict_not_rolled_back(
        self, zetesis_server, adaptors, search_store
    ):
        """retain/reject/etc. never derive rolled_back — only an
        actual 'rollback' verdict revokes a promotion."""
        adaptors.evidence = _evidence_adaptor_with_decisions({
            "cand-beta": [
                {"id": "d1", "verdict": "retain",
                 "created_at": "2026-01-01T00:00:00+00:00"},
            ],
        })
        await call_tool(
            zetesis_server, "refresh_roster", {"dry_run": False})
        e = search_store.get_roster_entry("cand-beta")
        assert e.derived_status is not DerivedStatus.rolled_back


def _evidence_adaptor_deriving_incumbent(decisions_by_cand):
    """Upstream fake whose get_incumbent derives from the verdict trail
    like the real store: newest promote not shadowed by a later rollback
    wins; when the incumbent's promote is rolled back there is no
    incumbent at all."""
    base = _evidence_adaptor_with_decisions(decisions_by_cand)

    def _incumbent():
        all_d = sorted(
            (
                {**d, "candidate_id": cid}
                for cid, ds in decisions_by_cand.items()
                for d in ds
            ),
            key=lambda d: d.get("created_at") or "",
            reverse=True,
        )
        for d in all_d:
            if d.get("verdict") != "promote":
                continue
            shadowed = any(
                x.get("verdict") == "rollback"
                and (x.get("created_at") or "") > (d.get("created_at") or "")
                for x in all_d
                if x["candidate_id"] == d["candidate_id"]
            )
            if not shadowed:
                return d["candidate_id"]
        return None

    class F(base.__class__):
        async def pull(self, tool, args):
            if tool == "get_incumbent":
                return json.dumps({"candidate_id": _incumbent()})
            return await super().pull(tool, args)

    return F()


class TestRolledBackIncumbent:
    """rc-6 state-machine row 30: when the CHAMPION's promote is rolled
    back, upstream incumbent goes null — the roster row must land
    rolled_back, not linger as champion. The earlier reconciliation
    exempted champion rows and skipped everything when the incumbent
    pull answered null — a rolled-back incumbent was checked by
    neither path."""

    async def test_rolled_back_incumbent_derives(
        self, zetesis_server, adaptors, search_store
    ):
        adaptors.evidence = _evidence_adaptor_deriving_incumbent({
            "cand-alpha": [
                {"id": "d1", "verdict": "promote",
                 "created_at": "2026-01-01T00:00:00+00:00"},
            ],
        })
        r = await call_tool(
            zetesis_server, "refresh_roster", {"dry_run": False})
        assert "error" not in r
        assert search_store.get_roster_entry(
            "cand-alpha").derived_status is DerivedStatus.champion

        adaptors.evidence = _evidence_adaptor_deriving_incumbent({
            "cand-alpha": [
                {"id": "d1", "verdict": "promote",
                 "created_at": "2026-01-01T00:00:00+00:00"},
                {"id": "d2", "verdict": "rollback",
                 "created_at": "2026-01-02T00:00:00+00:00"},
            ],
        })
        r = await call_tool(
            zetesis_server, "refresh_roster", {"dry_run": False})
        assert r["incumbent"] is None
        assert "cand-alpha" in r["reconciled"]["rolled_back"]
        e = search_store.get_roster_entry("cand-alpha")
        assert e.derived_status is DerivedStatus.rolled_back
        assert e.notes["decision_id"] == "d2"

    async def test_no_incumbent_demotes_stale_champion(
        self, zetesis_server, adaptors, search_store
    ):
        """A champion row with no incumbent upstream and no rollback
        trail still demotes — upstream truth (no incumbent) governs."""
        adaptors.evidence = _evidence_adaptor_deriving_incumbent({
            "cand-alpha": [
                {"id": "d1", "verdict": "promote",
                 "created_at": "2026-01-01T00:00:00+00:00"},
            ],
        })
        await call_tool(
            zetesis_server, "refresh_roster", {"dry_run": False})
        assert search_store.get_roster_entry(
            "cand-alpha").derived_status is DerivedStatus.champion

        # Incumbent pull succeeds but names nobody and no decision
        # trail exists — the row cannot keep claiming champion.
        adaptors.evidence = _evidence_adaptor_deriving_incumbent({})
        r = await call_tool(
            zetesis_server, "refresh_roster", {"dry_run": False})
        assert r["incumbent"] is None
        e = search_store.get_roster_entry("cand-alpha")
        assert e.derived_status is DerivedStatus.candidate

    async def test_failed_incumbent_pull_never_demotes(
        self, zetesis_server, adaptors, search_store
    ):
        """A failed get_incumbent pull is not an empty incumbent —
        transient connectivity must not demote anyone."""
        adaptors.evidence = _evidence_adaptor_deriving_incumbent({
            "cand-alpha": [
                {"id": "d1", "verdict": "promote",
                 "created_at": "2026-01-01T00:00:00+00:00"},
            ],
        })
        await call_tool(
            zetesis_server, "refresh_roster", {"dry_run": False})
        assert search_store.get_roster_entry(
            "cand-alpha").derived_status is DerivedStatus.champion

        class Down(FakeUpstreamAdaptor):
            async def pull(self, tool, args):
                return json.dumps({"error": "upstream unreachable"})

        adaptors.evidence = Down()
        r = await call_tool(
            zetesis_server, "refresh_roster", {"dry_run": False})
        e = search_store.get_roster_entry("cand-alpha")
        assert e.derived_status is DerivedStatus.champion


# --- rc-6 W4: guard ordering, abandonment, metric-value gate ---------


class _SpawnCapablePromotionAdaptor(FakePromotionAdaptor):
    """Promotion fake whose create_programme push mints a programme id —
    the spawn path needs it; the base fake errors on unexpected tools."""

    def __init__(self):
        super().__init__()
        self._prog_n = 0

    async def push(self, tool, args):
        if tool == "create_programme":
            self._prog_n += 1
            self.pushed.append((tool, args))
            return json.dumps(
                {"programme_id": f"prog-spawn{self._prog_n}"}
            )
        return await super().push(tool, args)


class TestGuardOrdering:
    """rc-6 P8 — the cap must not shadow the illegal-override check."""

    async def _open_capped_campaign(self, mcp, adaptors):
        adaptors.evidence = _evidence_adaptor()
        await call_tool(mcp, "refresh_roster", {"dry_run": False})
        r = await call_tool(mcp, "open_campaign", {
            "contract_id": "contract-c1",
            "challenger_id": "cand-beta",
            "budget": {
                "programmes_per_arm": 1,
                "trials_per_programme": 2,
            },
        })
        assert "error" not in r, r
        return r["campaign_id"]

    async def test_illegal_override_beats_cap_error(
        self, zetesis_server, adaptors
    ):
        """An arm already at cap + an illegal override key must report
        the OVERRIDE violation, not the cap — the ordering proved
        backwards on the VM (cap shadowed the real refusal)."""
        adaptors.promotion = _SpawnCapablePromotionAdaptor()
        cid = await self._open_capped_campaign(zetesis_server, adaptors)
        # Fill the arm's cap.
        r = await call_tool(zetesis_server, "spawn_campaign_programme", {
            "campaign_id": cid, "arm": "challenger",
            "goal": "g", "constraints": {},
            "allowed_variables": ["x"],
        })
        assert "error" not in r, r
        # Arm is now at cap AND carries an illegal override key.
        r = await call_tool(zetesis_server, "spawn_campaign_programme", {
            "campaign_id": cid, "arm": "challenger",
            "goal": "g", "constraints": {},
            "allowed_variables": ["x"],
            "budget": {"max_trials": 99},
        })
        assert "error" in r
        assert "cap" not in r["error"]
        assert "override" in r["error"] or "may only" in r["error"]

    async def test_cap_still_fires_on_legal_spawn(
        self, zetesis_server, adaptors
    ):
        """The cap is still enforced — a legal repeat spawn on a full
        arm gets the cap refusal."""
        adaptors.promotion = _SpawnCapablePromotionAdaptor()
        cid = await self._open_capped_campaign(zetesis_server, adaptors)
        await call_tool(zetesis_server, "spawn_campaign_programme", {
            "campaign_id": cid, "arm": "challenger",
            "goal": "g", "constraints": {},
            "allowed_variables": ["x"],
        })
        r = await call_tool(zetesis_server, "spawn_campaign_programme", {
            "campaign_id": cid, "arm": "challenger",
            "goal": "g", "constraints": {},
            "allowed_variables": ["x"],
        })
        assert "error" in r and "cap" in r["error"]

    async def test_arm_scope_message_reachable(
        self, zetesis_server, adaptors
    ):
        """A result naming a programme spawned for the OTHER arm must
        get the arm-scope refusal, not generic attribution — the check
        now runs before the upstream attribution pull."""
        adaptors.promotion = _SpawnCapablePromotionAdaptor()
        cid = await self._open_capped_campaign(zetesis_server, adaptors)
        r = await call_tool(zetesis_server, "spawn_campaign_programme", {
            "campaign_id": cid, "arm": "champion",
            "goal": "g", "constraints": {},
            "allowed_variables": ["x"],
        })
        assert "error" not in r, r
        # prog-spawn1 was spawned for champion; report it as challenger.
        r = await call_tool(zetesis_server, "record_campaign_result", {
            "campaign_id": cid, "arm": "challenger",
            "programme_id": "prog-spawn1", "metrics": {"hits": 5},
        })
        assert "error" in r
        assert "arm" in r["error"]
        assert "'champion'" in r["error"]

    async def test_unspawned_programme_named(
        self, zetesis_server, adaptors
    ):
        """A result naming a programme never spawned under the campaign
        gets the not-spawned refusal."""
        adaptors.promotion = _SpawnCapablePromotionAdaptor()
        cid = await self._open_capped_campaign(zetesis_server, adaptors)
        await call_tool(zetesis_server, "spawn_campaign_programme", {
            "campaign_id": cid, "arm": "challenger",
            "goal": "g", "constraints": {},
            "allowed_variables": ["x"],
        })
        r = await call_tool(zetesis_server, "record_campaign_result", {
            "campaign_id": cid, "arm": "challenger",
            "programme_id": "prog-outsider", "metrics": {"hits": 5},
        })
        assert "error" in r and "not spawned" in r["error"]


class TestAbandonCampaign:
    """rc-6 P9 — the exit for campaigns that cannot close."""

    async def test_open_to_abandoned_terminal(
        self, zetesis_server, adaptors, search_store
    ):
        cid = await _open_campaign(zetesis_server, adaptors)
        r = await call_tool(zetesis_server, "abandon_campaign", {
            "campaign_id": cid,
            "rationale": "mis-opened budget",
            "decided_by": "human:operator",
        })
        assert r["status"] == "abandoned"
        assert r["abandoned_by"] == "human:operator"
        c = search_store.get_campaign(cid)
        assert c.status is CampaignStatus.abandoned
        assert c.abandon_rationale == "mis-opened budget"
        # rc-7 Q5 — abandonment stamps abandoned_at, never closed_at:
        # an abandoned campaign never closed.
        assert c.abandoned_at is not None
        assert c.closed_at is None
        assert r["abandoned_at"] == c.abandoned_at

    async def test_abandoned_refuses_results_spawns_close(
        self, zetesis_server, adaptors
    ):
        cid = await _open_campaign(zetesis_server, adaptors)
        await call_tool(zetesis_server, "abandon_campaign", {
            "campaign_id": cid,
            "rationale": "wedged", "decided_by": "human:x",
        })
        r = await call_tool(zetesis_server, "record_campaign_result", {
            "campaign_id": cid, "arm": "challenger",
            "programme_id": "prog-chall", "metrics": {"hits": 1},
        })
        assert "error" in r and "abandoned" in r["error"]
        r = await call_tool(zetesis_server, "spawn_campaign_programme", {
            "campaign_id": cid, "arm": "challenger",
            "goal": "g", "constraints": {},
            "allowed_variables": ["x"],
        })
        assert "error" in r and "abandoned" in r["error"]
        r = await call_tool(zetesis_server, "close_campaign", {
            "campaign_id": cid,
        })
        assert "error" in r and "abandoned" in r["error"]
        r = await call_tool(zetesis_server, "pull_campaign_evidence", {
            "campaign_id": cid, "source": "loop0",
            "tool": "list_trials",
        })
        assert "error" in r and "abandoned" in r["error"]

    async def test_double_abandon_refused(
        self, zetesis_server, adaptors
    ):
        cid = await _open_campaign(zetesis_server, adaptors)
        await call_tool(zetesis_server, "abandon_campaign", {
            "campaign_id": cid,
            "rationale": "first", "decided_by": "human:x",
        })
        r = await call_tool(zetesis_server, "abandon_campaign", {
            "campaign_id": cid,
            "rationale": "again", "decided_by": "human:x",
        })
        assert "error" in r and "abandoned" in r["error"]

    async def test_abandon_requires_attribution(
        self, zetesis_server, adaptors
    ):
        cid = await _open_campaign(zetesis_server, adaptors)
        r = await call_tool(zetesis_server, "abandon_campaign", {
            "campaign_id": cid, "rationale": "", "decided_by": "h",
        })
        assert "error" in r and "rationale" in r["error"]
        r = await call_tool(zetesis_server, "abandon_campaign", {
            "campaign_id": cid, "rationale": "r", "decided_by": "  ",
        })
        assert "error" in r and "decided_by" in r["error"]
        # Still open — refused abandonment leaves the campaign live.
        r = await call_tool(zetesis_server, "get_campaign", {
            "campaign_id": cid,
        })
        assert r["campaign"]["status"] == "open"

    async def test_abandon_skips_budget_audit(
        self, zetesis_server, adaptors, search_store
    ):
        """Abandonment is the exit for campaigns that can never satisfy
        the close audit — no both-arms requirement, no spend check."""
        cid = await _open_campaign(zetesis_server, adaptors)
        # No results at all — close would refuse on missing arms.
        r = await call_tool(zetesis_server, "abandon_campaign", {
            "campaign_id": cid,
            "rationale": "upstream lineage abandoned",
            "decided_by": "human:x",
        })
        assert r["status"] == "abandoned"

    async def test_list_campaigns_filters_abandoned(
        self, zetesis_server, adaptors
    ):
        cid = await _open_campaign(zetesis_server, adaptors)
        await call_tool(zetesis_server, "abandon_campaign", {
            "campaign_id": cid,
            "rationale": "x", "decided_by": "human:x",
        })
        r = await call_tool(zetesis_server, "list_campaigns", {
            "status": "abandoned",
        })
        assert cid in [c["id"] for c in r["campaigns"]]
        r = await call_tool(zetesis_server, "list_campaigns", {
            "status": "open",
        })
        assert cid not in [c["id"] for c in r["campaigns"]]


class TestMetricValueGate:
    """rc-6 P12 — key presence is not enough; the value must score."""

    async def _open(self, mcp, adaptors):
        return await _open_campaign(mcp, adaptors)

    async def test_null_metric_refused_naming_key_and_value(
        self, zetesis_server, adaptors, search_store
    ):
        cid = await self._open(zetesis_server, adaptors)
        r = await call_tool(zetesis_server, "record_campaign_result", {
            "campaign_id": cid, "arm": "challenger",
            "programme_id": "prog-chall", "metrics": {"hits": None},
        })
        assert "error" in r
        assert "'hits'" in r["error"]
        assert "None" in r["error"]
        # Nothing persisted — insert-only rows can't be repaired.
        assert search_store.get_campaign(cid) is not None
        results = search_store._fetchall(
            "SELECT * FROM campaign_results WHERE campaign_id = ?",
            (cid,),
        )
        assert results == []

    async def test_string_metric_refused(self, zetesis_server, adaptors):
        cid = await self._open(zetesis_server, adaptors)
        r = await call_tool(zetesis_server, "record_campaign_result", {
            "campaign_id": cid, "arm": "challenger",
            "programme_id": "prog-chall",
            "metrics": {"hits": "high"},
        })
        assert "error" in r and "'high'" in r["error"]

    async def test_bool_metric_refused(self, zetesis_server, adaptors):
        """bool is an int subclass — must be refused explicitly."""
        cid = await self._open(zetesis_server, adaptors)
        r = await call_tool(zetesis_server, "record_campaign_result", {
            "campaign_id": cid, "arm": "challenger",
            "programme_id": "prog-chall", "metrics": {"hits": True},
        })
        assert "error" in r and "True" in r["error"]

    async def test_nan_metric_refused(self, zetesis_server, adaptors):
        cid = await self._open(zetesis_server, adaptors)
        # JSON can't carry NaN — go through the dict surface.
        r = await call_tool(zetesis_server, "record_campaign_result", {
            "campaign_id": cid, "arm": "challenger",
            "programme_id": "prog-chall",
            "metrics": {"hits": float("nan")},
        })
        assert "error" in r and "finite" in r["error"]

    async def test_valid_numeric_accepted(
        self, zetesis_server, adaptors
    ):
        cid = await self._open(zetesis_server, adaptors)
        for v in (0, -1, 1e-9, 120.5):
            r = await call_tool(
                zetesis_server, "record_campaign_result", {
                    "campaign_id": cid, "arm": "challenger",
                    "programme_id": "prog-chall",
                    "metrics": {"hits": v},
                })
            assert "error" not in r, (v, r)

    async def test_wedged_close_names_the_refusal(
        self, zetesis_server, adaptors, search_store
    ):
        """A historical null row (pre-gate) must produce a named close
        refusal, never a raw TypeError — abandon is the remediation."""
        cid = await self._open(zetesis_server, adaptors)
        # Simulate the pre-gate row landing directly in the store.
        from ml_zetesis_mcp.state.models import CampaignResult as CR
        search_store.create_campaign_result(CR(
            id="cres-null", campaign_id=cid, arm=CampaignArm.challenger,
            programme_id="prog-chall", metrics={"hits": None},
        ))
        search_store.create_campaign_result(CR(
            id="cres-ok", campaign_id=cid, arm=CampaignArm.champion,
            programme_id="prog-champ", metrics={"hits": 10},
        ))
        r = await call_tool(zetesis_server, "close_campaign", {
            "campaign_id": cid,
        })
        assert "error" in r
        assert "unscorable" in r["error"]
        assert "cres-null" in r["error"]
        assert "abandon_campaign" in r["error"]
        assert "TypeError" not in r["error"]


class TestOrphanRollback:
    """rc-6 P13 — a rollback with no prior promote revokes nothing."""

    async def test_rollback_only_candidate_stays_unadopted(
        self, zetesis_server, adaptors, search_store
    ):
        adaptors.evidence = _evidence_adaptor_deriving_incumbent({
            "cand-beta": [
                {"id": "d1", "verdict": "rollback",
                 "created_at": "2026-01-01T00:00:00+00:00"},
            ],
        })
        r = await call_tool(
            zetesis_server, "refresh_roster", {"dry_run": False})
        assert "error" not in r
        e = search_store.get_roster_entry("cand-beta")
        assert e is not None
        # Nothing was ever promoted — the orphan rollback cannot mark
        # the row rolled_back; it stays an ordinary candidate.
        assert e.derived_status is DerivedStatus.candidate
        assert "cand-beta" not in r["reconciled"]["rolled_back"]

    async def test_promote_then_rollback_still_rolls_back(
        self, zetesis_server, adaptors, search_store
    ):
        """Control: promote→rollback still derives rolled_back."""
        adaptors.evidence = _evidence_adaptor_deriving_incumbent({
            "cand-beta": [
                {"id": "d1", "verdict": "promote",
                 "created_at": "2026-01-01T00:00:00+00:00"},
                {"id": "d2", "verdict": "rollback",
                 "created_at": "2026-01-02T00:00:00+00:00"},
            ],
        })
        r = await call_tool(
            zetesis_server, "refresh_roster", {"dry_run": False})
        e = search_store.get_roster_entry("cand-beta")
        assert e.derived_status is DerivedStatus.rolled_back
