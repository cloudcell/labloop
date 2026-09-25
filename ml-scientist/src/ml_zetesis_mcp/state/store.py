"""SearchStore — SQLite persistence for the zetesis search loop.

Standalone store (~/.ml-zetesis/search.db) — the loop's owned episodic
memory (ADR-0005: episodic memory is owned, never a role). Own schema,
own lifecycle: evidence_refs are insert-only (the consulted trail is
the provenance of the research); findings mutate in status only;
investigations carry a lifecycle status.
"""

from __future__ import annotations

import json
import sqlite3

from .models import (
    CampaignArm,
    CampaignResult,
    CampaignSpawn,
    CampaignStatus,
    DerivedStatus,
    EvidenceRef,
    EvidenceSource,
    Finding,
    FindingStatus,
    Investigation,
    InvestigationStatus,
    InvestigationVerdict,
    PolicyStatus,
    PromotionCampaign,
    RosterEntry,
    SearchPolicy,
    SpawnStatus,
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS investigations (
    id                TEXT PRIMARY KEY,
    question          TEXT NOT NULL,
    scope_json        TEXT NOT NULL,
    budget_json       TEXT,
    status            TEXT NOT NULL DEFAULT 'open',
    verdict           TEXT,
    summary           TEXT,
    implications_json TEXT,
    created_at        TEXT NOT NULL,
    concluded_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_inv_status ON investigations(status);

CREATE TABLE IF NOT EXISTS promotion_campaigns (
    id              TEXT PRIMARY KEY,
    contract_id     TEXT NOT NULL,
    champion_id     TEXT NOT NULL,
    challenger_id   TEXT NOT NULL,
    primary_metric  TEXT NOT NULL,
    budget_json     TEXT NOT NULL,
    seeds_json      TEXT NOT NULL DEFAULT '[]',
    status          TEXT NOT NULL DEFAULT 'open',
    promotion_score REAL,
    decision_id     TEXT,
    claim_id        TEXT,
    created_at      TEXT NOT NULL,
    closed_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_camp_status ON promotion_campaigns(status);

CREATE TABLE IF NOT EXISTS evidence_refs (
    id               TEXT PRIMARY KEY,
    investigation_id TEXT REFERENCES investigations(id),
    campaign_id      TEXT REFERENCES promotion_campaigns(id),
    source           TEXT NOT NULL,
    tool             TEXT NOT NULL,
    args_json        TEXT NOT NULL,
    ref_ids_json     TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    CHECK ((investigation_id IS NULL) != (campaign_id IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_eref_inv ON evidence_refs(investigation_id);
CREATE INDEX IF NOT EXISTS idx_eref_camp ON evidence_refs(campaign_id);

CREATE TABLE IF NOT EXISTS findings (
    id               TEXT PRIMARY KEY,
    investigation_id TEXT NOT NULL REFERENCES investigations(id),
    content          TEXT NOT NULL,
    confidence       REAL NOT NULL,
    status           TEXT NOT NULL DEFAULT 'provisional',
    claim_id         TEXT,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_find_inv ON findings(investigation_id);

CREATE TABLE IF NOT EXISTS finding_evidence (
    finding_id      TEXT NOT NULL REFERENCES findings(id),
    evidence_ref_id TEXT NOT NULL REFERENCES evidence_refs(id),
    PRIMARY KEY (finding_id, evidence_ref_id)
);

CREATE TABLE IF NOT EXISTS roster_entries (
    id              TEXT PRIMARY KEY,
    parent_id       TEXT,
    derived_status  TEXT NOT NULL DEFAULT 'candidate',
    annotated_at    TEXT NOT NULL,
    notes_json      TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS search_policies (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    version         INTEGER NOT NULL,
    policy_json     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'draft',
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_spol_name ON search_policies(name);

CREATE TABLE IF NOT EXISTS campaign_results (
    id              TEXT PRIMARY KEY,
    campaign_id     TEXT NOT NULL REFERENCES promotion_campaigns(id),
    arm             TEXT NOT NULL,
    programme_id    TEXT NOT NULL,
    metrics_json    TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cres_camp ON campaign_results(campaign_id);

CREATE TABLE IF NOT EXISTS campaign_spawns (
    id              TEXT PRIMARY KEY,
    campaign_id     TEXT NOT NULL REFERENCES promotion_campaigns(id),
    arm             TEXT NOT NULL,
    programme_id    TEXT NOT NULL UNIQUE,
    budget_json     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'spawned',
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_spawn_camp ON campaign_spawns(campaign_id);
"""


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


class SearchStore:
    """SQLite-backed store for the Loop-1 investigation lifecycle."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()
        self.conn.executescript(SCHEMA)

    def _migrate(self) -> None:
        """Additive migrations for pre-existing search.db files.

        evidence_refs gained `campaign_id` (and a nullable
        investigation_id) with the promotion increment. SQLite can't
        ALTER COLUMN, so the table is rebuilt — ids are preserved and
        the finding_evidence junction keeps resolving (FK off during
        the swap, back on after). Runs BEFORE executescript(SCHEMA):
        the schema's idx_eref_camp index would fail against the old
        column set.
        """
        tables = {
            r["name"]
            for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "evidence_refs" not in tables:
            return
        cols = {
            r["name"]
            for r in self.conn.execute("PRAGMA table_info(evidence_refs)")
        }
        if "campaign_id" in cols:
            return
        self.conn.execute("PRAGMA foreign_keys = OFF")
        self.conn.executescript(
            """
            CREATE TABLE evidence_refs_new (
                id               TEXT PRIMARY KEY,
                investigation_id TEXT REFERENCES investigations(id),
                campaign_id      TEXT REFERENCES promotion_campaigns(id),
                source           TEXT NOT NULL,
                tool             TEXT NOT NULL,
                args_json        TEXT NOT NULL,
                ref_ids_json     TEXT NOT NULL,
                created_at       TEXT NOT NULL,
                CHECK ((investigation_id IS NULL) != (campaign_id IS NULL))
            );
            INSERT INTO evidence_refs_new
                (id, investigation_id, campaign_id, source, tool,
                 args_json, ref_ids_json, created_at)
                SELECT id, investigation_id, NULL, source, tool,
                       args_json, ref_ids_json, created_at
                FROM evidence_refs;
            DROP TABLE evidence_refs;
            ALTER TABLE evidence_refs_new RENAME TO evidence_refs;
            CREATE INDEX IF NOT EXISTS idx_eref_inv
                ON evidence_refs(investigation_id);
            CREATE INDEX IF NOT EXISTS idx_eref_camp
                ON evidence_refs(campaign_id);
            """
        )
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.commit()

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

    # --- Internal helpers ---

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def _fetchone(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def _fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    # --- Investigations ---

    def create_investigation(self, inv: Investigation) -> None:
        self._execute(
            """INSERT INTO investigations
               (id, question, scope_json, budget_json, status,
                verdict, summary, implications_json,
                created_at, concluded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                inv.id, inv.question, json.dumps(inv.scope),
                json.dumps(inv.budget) if inv.budget is not None else None,
                inv.status.value,
                inv.verdict.value if inv.verdict else None,
                inv.summary,
                json.dumps(inv.implications)
                if inv.implications is not None else None,
                inv.created_at, inv.concluded_at,
            ),
        )
        self.conn.commit()

    def get_investigation(self, investigation_id: str) -> Investigation | None:
        row = self._fetchone(
            "SELECT * FROM investigations WHERE id = ?", (investigation_id,)
        )
        return self._row_to_investigation(row) if row else None

    def list_investigations(
        self,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Investigation], int]:
        clauses: list[str] = []
        params: list = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        total = self._fetchone(
            f"SELECT COUNT(*) AS n FROM investigations {where}", tuple(params)
        )["n"]
        rows = self._fetchall(
            f"SELECT * FROM investigations {where} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return [self._row_to_investigation(r) for r in rows], total

    def conclude_investigation(
        self,
        investigation_id: str,
        verdict: InvestigationVerdict,
        summary: str,
        implications: dict | None,
    ) -> None:
        self._execute(
            """UPDATE investigations SET status = ?, verdict = ?,
               summary = ?, implications_json = ?, concluded_at = ?
               WHERE id = ?""",
            (
                InvestigationStatus.concluded.value, verdict.value,
                summary,
                json.dumps(implications) if implications is not None else None,
                _now_iso(), investigation_id,
            ),
        )
        self.conn.commit()

    def abandon_investigation(self, investigation_id: str) -> None:
        self._execute(
            """UPDATE investigations SET status = ?, concluded_at = ?
               WHERE id = ?""",
            (
                InvestigationStatus.abandoned.value,
                _now_iso(), investigation_id,
            ),
        )
        self.conn.commit()

    def _row_to_investigation(self, row: sqlite3.Row) -> Investigation:
        return Investigation(
            id=row["id"],
            question=row["question"],
            scope=json.loads(row["scope_json"]),
            budget=json.loads(row["budget_json"]) if row["budget_json"] else None,
            status=InvestigationStatus(row["status"]),
            verdict=InvestigationVerdict(row["verdict"]) if row["verdict"] else None,
            summary=row["summary"],
            implications=(
                json.loads(row["implications_json"])
                if row["implications_json"] else None
            ),
            created_at=row["created_at"],
            concluded_at=row["concluded_at"],
        )

    # --- Evidence refs (insert-only) ---

    def create_evidence_ref(self, ref: EvidenceRef) -> None:
        self._execute(
            """INSERT INTO evidence_refs
               (id, investigation_id, campaign_id, source, tool, args_json,
                ref_ids_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ref.id, ref.investigation_id, ref.campaign_id,
                ref.source.value, ref.tool,
                json.dumps(ref.args), json.dumps(ref.ref_ids),
                ref.created_at,
            ),
        )
        self.conn.commit()

    def get_evidence_ref(self, evidence_ref_id: str) -> EvidenceRef | None:
        row = self._fetchone(
            "SELECT * FROM evidence_refs WHERE id = ?", (evidence_ref_id,)
        )
        return self._row_to_evidence_ref(row) if row else None

    def list_evidence_refs(
        self,
        investigation_id: str | None = None,
        campaign_id: str | None = None,
    ) -> list[EvidenceRef]:
        if campaign_id is not None:
            rows = self._fetchall(
                "SELECT * FROM evidence_refs WHERE campaign_id = ? "
                "ORDER BY created_at",
                (campaign_id,),
            )
        else:
            rows = self._fetchall(
                "SELECT * FROM evidence_refs WHERE investigation_id = ? "
                "ORDER BY created_at",
                (investigation_id,),
            )
        return [self._row_to_evidence_ref(r) for r in rows]

    def _row_to_evidence_ref(self, row: sqlite3.Row) -> EvidenceRef:
        return EvidenceRef(
            id=row["id"],
            investigation_id=row["investigation_id"],
            campaign_id=row["campaign_id"],
            source=EvidenceSource(row["source"]),
            tool=row["tool"],
            args=json.loads(row["args_json"]),
            ref_ids=json.loads(row["ref_ids_json"]),
            created_at=row["created_at"],
        )

    # --- Findings ---

    def create_finding(self, finding: Finding) -> None:
        self._execute(
            """INSERT INTO findings
               (id, investigation_id, content, confidence, status,
                claim_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                finding.id, finding.investigation_id, finding.content,
                finding.confidence, finding.status.value, finding.claim_id,
                finding.created_at,
            ),
        )

    def get_finding(self, finding_id: str) -> Finding | None:
        row = self._fetchone("SELECT * FROM findings WHERE id = ?", (finding_id,))
        return self._row_to_finding(row) if row else None

    def list_findings(
        self, investigation_id: str, status: str | None = None
    ) -> list[Finding]:
        if status is not None:
            rows = self._fetchall(
                "SELECT * FROM findings WHERE investigation_id = ? "
                "AND status = ? ORDER BY created_at",
                (investigation_id, status),
            )
        else:
            rows = self._fetchall(
                "SELECT * FROM findings WHERE investigation_id = ? "
                "ORDER BY created_at",
                (investigation_id,),
            )
        return [self._row_to_finding(r) for r in rows]

    def set_finding_status(self, finding_id: str, status: FindingStatus) -> None:
        self._execute(
            "UPDATE findings SET status = ? WHERE id = ?",
            (status.value, finding_id),
        )
        self.conn.commit()

    def set_finding_claim(self, finding_id: str, claim_id: str) -> None:
        self._execute(
            "UPDATE findings SET claim_id = ? WHERE id = ?",
            (claim_id, finding_id),
        )
        self.conn.commit()

    def _row_to_finding(self, row: sqlite3.Row) -> Finding:
        return Finding(
            id=row["id"],
            investigation_id=row["investigation_id"],
            content=row["content"],
            confidence=row["confidence"],
            status=FindingStatus(row["status"]),
            claim_id=row["claim_id"],
            created_at=row["created_at"],
        )

    # --- Finding ↔ evidence junction ---

    def add_finding_evidence(self, finding_id: str, evidence_ref_id: str) -> None:
        self._execute(
            "INSERT OR IGNORE INTO finding_evidence "
            "(finding_id, evidence_ref_id) VALUES (?, ?)",
            (finding_id, evidence_ref_id),
        )

    def finding_evidence_refs(self, finding_id: str) -> list[EvidenceRef]:
        rows = self._fetchall(
            """SELECT e.* FROM evidence_refs e
               JOIN finding_evidence fe ON fe.evidence_ref_id = e.id
               WHERE fe.finding_id = ? ORDER BY e.created_at""",
            (finding_id,),
        )
        return [self._row_to_evidence_ref(r) for r in rows]

    # --- Roster (working set over upstream candidate_versions) ---

    def upsert_roster_entry(self, entry: RosterEntry) -> None:
        self._execute(
            """INSERT INTO roster_entries
               (id, parent_id, derived_status, annotated_at, notes_json)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 parent_id = excluded.parent_id,
                 derived_status = excluded.derived_status,
                 annotated_at = excluded.annotated_at,
                 notes_json = excluded.notes_json""",
            (
                entry.id, entry.parent_id, entry.derived_status.value,
                entry.annotated_at, json.dumps(entry.notes),
            ),
        )
        self.conn.commit()

    def get_roster_entry(self, entry_id: str) -> RosterEntry | None:
        row = self._fetchone(
            "SELECT * FROM roster_entries WHERE id = ?", (entry_id,)
        )
        return self._row_to_roster_entry(row) if row else None

    def list_roster_children(self, parent_id: str) -> list[RosterEntry]:
        rows = self._fetchall(
            "SELECT * FROM roster_entries WHERE parent_id = ? "
            "ORDER BY annotated_at",
            (parent_id,),
        )
        return [self._row_to_roster_entry(r) for r in rows]

    def list_roster_entries(
        self,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[RosterEntry], int]:
        clauses, params = [], []
        if status is not None:
            clauses.append("derived_status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        total = self._fetchone(
            f"SELECT COUNT(*) AS n FROM roster_entries {where}",
            tuple(params),
        )["n"]
        rows = self._fetchall(
            f"SELECT * FROM roster_entries {where} "
            "ORDER BY annotated_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return [self._row_to_roster_entry(r) for r in rows], total

    def _row_to_roster_entry(self, row: sqlite3.Row) -> RosterEntry:
        return RosterEntry(
            id=row["id"],
            parent_id=row["parent_id"],
            derived_status=DerivedStatus(row["derived_status"]),
            annotated_at=row["annotated_at"],
            notes=json.loads(row["notes_json"]),
        )

    # --- Search policies (versioned registry) ---

    def create_search_policy(self, policy: SearchPolicy) -> None:
        self._execute(
            """INSERT INTO search_policies
               (id, name, version, policy_json, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                policy.id, policy.name, policy.version,
                json.dumps(policy.policy), policy.status.value,
                policy.created_at,
            ),
        )
        self.conn.commit()

    def get_search_policy(self, policy_id: str) -> SearchPolicy | None:
        row = self._fetchone(
            "SELECT * FROM search_policies WHERE id = ?", (policy_id,)
        )
        return self._row_to_policy(row) if row else None

    def list_search_policies(
        self, name: str | None = None
    ) -> list[SearchPolicy]:
        if name is not None:
            rows = self._fetchall(
                "SELECT * FROM search_policies WHERE name = ? "
                "ORDER BY version",
                (name,),
            )
        else:
            rows = self._fetchall(
                "SELECT * FROM search_policies ORDER BY name, version"
            )
        return [self._row_to_policy(r) for r in rows]

    def next_policy_version(self, name: str) -> int:
        row = self._fetchone(
            "SELECT MAX(version) AS v FROM search_policies WHERE name = ?",
            (name,),
        )
        return (row["v"] or 0) + 1

    def retire_active_policies(self, name: str) -> None:
        self._execute(
            "UPDATE search_policies SET status = ? "
            "WHERE name = ? AND status = ?",
            (PolicyStatus.retired.value, name, PolicyStatus.active.value),
        )

    def _row_to_policy(self, row: sqlite3.Row) -> SearchPolicy:
        return SearchPolicy(
            id=row["id"],
            name=row["name"],
            version=row["version"],
            policy=json.loads(row["policy_json"]),
            status=PolicyStatus(row["status"]),
            created_at=row["created_at"],
        )

    # --- Promotion campaigns ---

    def create_campaign(self, c: PromotionCampaign) -> None:
        self._execute(
            """INSERT INTO promotion_campaigns
               (id, contract_id, champion_id, challenger_id,
                primary_metric, budget_json, seeds_json, status,
                promotion_score, decision_id, claim_id,
                created_at, closed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                c.id, c.contract_id, c.champion_id, c.challenger_id,
                c.primary_metric, json.dumps(c.budget),
                json.dumps(c.seeds), c.status.value, c.promotion_score,
                c.decision_id, c.claim_id, c.created_at, c.closed_at,
            ),
        )
        self.conn.commit()

    def get_campaign(self, campaign_id: str) -> PromotionCampaign | None:
        row = self._fetchone(
            "SELECT * FROM promotion_campaigns WHERE id = ?",
            (campaign_id,),
        )
        return self._row_to_campaign(row) if row else None

    def list_campaigns(
        self,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[PromotionCampaign], int]:
        clauses, params = [], []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        total = self._fetchone(
            f"SELECT COUNT(*) AS n FROM promotion_campaigns {where}",
            tuple(params),
        )["n"]
        rows = self._fetchall(
            f"SELECT * FROM promotion_campaigns {where} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return [self._row_to_campaign(r) for r in rows], total

    def list_campaigns_for_candidate(
        self, candidate_id: str
    ) -> list[PromotionCampaign]:
        """Campaigns where the candidate ran — as challenger or
        as the incumbent (champion_id) it was measured against."""
        rows = self._fetchall(
            "SELECT * FROM promotion_campaigns "
            "WHERE champion_id = ? OR challenger_id = ? "
            "ORDER BY created_at",
            (candidate_id, candidate_id),
        )
        return [self._row_to_campaign(r) for r in rows]

    def close_campaign(self, campaign_id: str, score: float) -> None:
        self._execute(
            """UPDATE promotion_campaigns
               SET status = ?, promotion_score = ?, closed_at = ?
               WHERE id = ?""",
            (CampaignStatus.closed.value, score, _now_iso(), campaign_id),
        )
        self.conn.commit()

    def set_campaign_decision(
        self, campaign_id: str, decision_id: str
    ) -> None:
        self._execute(
            "UPDATE promotion_campaigns SET decision_id = ? WHERE id = ?",
            (decision_id, campaign_id),
        )
        self.conn.commit()

    def set_campaign_claim(self, campaign_id: str, claim_id: str) -> None:
        self._execute(
            "UPDATE promotion_campaigns SET claim_id = ? WHERE id = ?",
            (claim_id, campaign_id),
        )
        self.conn.commit()

    def _row_to_campaign(self, row: sqlite3.Row) -> PromotionCampaign:
        return PromotionCampaign(
            id=row["id"],
            contract_id=row["contract_id"],
            champion_id=row["champion_id"],
            challenger_id=row["challenger_id"],
            primary_metric=row["primary_metric"],
            budget=json.loads(row["budget_json"]),
            seeds=json.loads(row["seeds_json"]),
            status=CampaignStatus(row["status"]),
            promotion_score=row["promotion_score"],
            decision_id=row["decision_id"],
            claim_id=row["claim_id"],
            created_at=row["created_at"],
            closed_at=row["closed_at"],
        )

    # --- Campaign results (insert-only) ---

    def create_campaign_result(self, r: CampaignResult) -> None:
        self._execute(
            """INSERT INTO campaign_results
               (id, campaign_id, arm, programme_id, metrics_json,
                created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                r.id, r.campaign_id, r.arm.value, r.programme_id,
                json.dumps(r.metrics), r.created_at,
            ),
        )
        self.conn.commit()

    def list_campaign_results(
        self, campaign_id: str
    ) -> list[CampaignResult]:
        rows = self._fetchall(
            "SELECT * FROM campaign_results WHERE campaign_id = ? "
            "ORDER BY created_at",
            (campaign_id,),
        )
        return [
            CampaignResult(
                id=r["id"],
                campaign_id=r["campaign_id"],
                arm=CampaignArm(r["arm"]),
                programme_id=r["programme_id"],
                metrics=json.loads(r["metrics_json"]),
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # --- Campaign spawns (insert-only) ---

    def create_campaign_spawn(self, s: CampaignSpawn) -> None:
        self._execute(
            """INSERT INTO campaign_spawns
               (id, campaign_id, arm, programme_id, budget_json,
                status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                s.id, s.campaign_id, s.arm.value, s.programme_id,
                json.dumps(s.budget), s.status.value, s.created_at,
            ),
        )
        self.conn.commit()

    def list_campaign_spawns(
        self, campaign_id: str, arm: str | None = None
    ) -> list[CampaignSpawn]:
        if arm:
            rows = self._fetchall(
                "SELECT * FROM campaign_spawns WHERE campaign_id = ? "
                "AND arm = ? ORDER BY created_at",
                (campaign_id, arm),
            )
        else:
            rows = self._fetchall(
                "SELECT * FROM campaign_spawns WHERE campaign_id = ? "
                "ORDER BY created_at",
                (campaign_id,),
            )
        return [
            CampaignSpawn(
                id=r["id"],
                campaign_id=r["campaign_id"],
                arm=CampaignArm(r["arm"]),
                programme_id=r["programme_id"],
                budget=json.loads(r["budget_json"]),
                status=r["status"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def get_spawn_for_programme(
        self, programme_id: str
    ) -> CampaignSpawn | None:
        row = self._fetchone(
            "SELECT * FROM campaign_spawns WHERE programme_id = ?",
            (programme_id,),
        )
        if row is None:
            return None
        return CampaignSpawn(
            id=row["id"],
            campaign_id=row["campaign_id"],
            arm=CampaignArm(row["arm"]),
            programme_id=row["programme_id"],
            budget=json.loads(row["budget_json"]),
            status=row["status"],
            created_at=row["created_at"],
        )

    def set_spawn_status(self, spawn_id: str, status: SpawnStatus) -> None:
        self._execute(
            "UPDATE campaign_spawns SET status = ? WHERE id = ?",
            (status.value, spawn_id),
        )
        self.conn.commit()

    # --- GUI stats ---

    def stats(self) -> dict:
        total = self._fetchone("SELECT COUNT(*) AS n FROM investigations")["n"]
        by_status = {
            r["status"]: r["n"]
            for r in self._fetchall(
                "SELECT status, COUNT(*) AS n FROM investigations "
                "GROUP BY status"
            )
        }
        findings = self._fetchone("SELECT COUNT(*) AS n FROM findings")["n"]
        refs = self._fetchone("SELECT COUNT(*) AS n FROM evidence_refs")["n"]
        minted = self._fetchone(
            "SELECT COUNT(*) AS n FROM findings WHERE claim_id IS NOT NULL"
        )["n"]
        campaigns = self._fetchone(
            "SELECT COUNT(*) AS n FROM promotion_campaigns"
        )["n"]
        open_campaigns = self._fetchone(
            "SELECT COUNT(*) AS n FROM promotion_campaigns "
            "WHERE status = 'open'"
        )["n"]
        roster = self._fetchone(
            "SELECT COUNT(*) AS n FROM roster_entries"
        )["n"]
        return {
            "total": total,
            "by_status": by_status,
            "findings": findings,
            "evidence_refs": refs,
            "minted_claims": minted,
            "campaigns": campaigns,
            "open_campaigns": open_campaigns,
            "roster": roster,
        }

    # --- Transactions ---

    def transaction(self):
        """Context manager: commit on success, rollback on error."""
        return _Transaction(self.conn)


class _Transaction:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.conn.commit()
        else:
            self.conn.rollback()
        return False
