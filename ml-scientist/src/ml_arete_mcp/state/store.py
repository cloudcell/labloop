"""ImproverStore — SQLite persistence for the arete recursive loop.

Standalone store (~/.ml-arete/improver.db) — the loop's owned episodic
memory (ADR-0005: episodic memory is owned, never a role). Own schema,
own lifecycle: tournament_results, meta_decisions and evidence_refs are
insert-only (reversal is an event, never an edit); the champion pointer
moves only inside explicit promote/rollback transactions — never by
"latest registered".
"""

from __future__ import annotations

import json
import sqlite3

from .models import (
    CanaryDeployment,
    CanaryStatus,
    DecisionVerdict,
    EvidenceContext,
    EvidenceRef,
    EvidenceSource,
    ImproverVersion,
    MetaChangeProposal,
    MetaContract,
    MetaDecision,
    PolicyStatus,
    PolicyVersion,
    ProposalStatus,
    Tournament,
    TournamentArm,
    TournamentCampaign,
    TournamentResult,
    TournamentStatus,
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS improver_versions (
    id TEXT PRIMARY KEY,
    parent_id TEXT REFERENCES improver_versions(id),
    proposal_id TEXT REFERENCES meta_change_proposals(id),
    code_artifact_digest TEXT NOT NULL,
    harness_artifact_digest TEXT,
    model_ref TEXT NOT NULL,
    search_policy_ref TEXT,
    capability_profile_json TEXT NOT NULL,
    is_champion INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_imp_parent
    ON improver_versions(parent_id);
CREATE INDEX IF NOT EXISTS idx_imp_champion
    ON improver_versions(is_champion);

CREATE TABLE IF NOT EXISTS meta_contracts (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    metrics_json TEXT NOT NULL,
    promotion_policy_json TEXT NOT NULL,
    holdouts_json TEXT,
    budget_json TEXT,
    frozen INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta_change_proposals (
    id TEXT PRIMARY KEY,
    proposer_improver_id TEXT NOT NULL
        REFERENCES improver_versions(id),
    spec_delta_json TEXT NOT NULL,
    class_map_json TEXT NOT NULL,
    expected_benefit TEXT NOT NULL,
    falsification TEXT NOT NULL,
    budget_json TEXT,
    rollback_plan TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'admitted',
    rejection_reason TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prop_status
    ON meta_change_proposals(status);
CREATE INDEX IF NOT EXISTS idx_prop_proposer
    ON meta_change_proposals(proposer_improver_id);

CREATE TABLE IF NOT EXISTS tournaments (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL REFERENCES meta_contracts(id),
    parent_improver_id TEXT NOT NULL
        REFERENCES improver_versions(id),
    candidate_improver_id TEXT NOT NULL
        REFERENCES improver_versions(id),
    budget_json TEXT NOT NULL,
    seeds_json TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    recursive_gain REAL,
    created_at TEXT NOT NULL,
    closed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tourn_status ON tournaments(status);

CREATE TABLE IF NOT EXISTS tournament_results (
    id TEXT PRIMARY KEY,
    tournament_id TEXT NOT NULL REFERENCES tournaments(id),
    arm TEXT NOT NULL,
    descendant_spec_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    corrections_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tres_tourn
    ON tournament_results(tournament_id);

CREATE TABLE IF NOT EXISTS tournament_campaigns (
    id TEXT PRIMARY KEY,
    tournament_id TEXT NOT NULL REFERENCES tournaments(id),
    arm TEXT NOT NULL,
    campaign_id TEXT NOT NULL UNIQUE,
    upstream_contract_id TEXT NOT NULL,
    challenger_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(tournament_id, arm)
);
CREATE INDEX IF NOT EXISTS idx_tcamp_tourn
    ON tournament_campaigns(tournament_id);

CREATE TABLE IF NOT EXISTS meta_decisions (
    id TEXT PRIMARY KEY,
    candidate_improver_id TEXT NOT NULL
        REFERENCES improver_versions(id),
    contract_id TEXT REFERENCES meta_contracts(id),
    tournament_id TEXT REFERENCES tournaments(id),
    verdict TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    rationale TEXT NOT NULL,
    decided_by TEXT NOT NULL,
    claim_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mdec_candidate
    ON meta_decisions(candidate_improver_id);

CREATE TABLE IF NOT EXISTS policy_versions (
    id TEXT PRIMARY KEY,
    improver_version_id TEXT NOT NULL
        REFERENCES improver_versions(id),
    decision_id TEXT NOT NULL REFERENCES meta_decisions(id),
    policy_json TEXT NOT NULL,
    previous_champion_id TEXT
        REFERENCES improver_versions(id),
    status TEXT NOT NULL DEFAULT 'minted',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pol_improver
    ON policy_versions(improver_version_id);

CREATE TABLE IF NOT EXISTS canary_deployments (
    id TEXT PRIMARY KEY,
    policy_version_id TEXT NOT NULL
        REFERENCES policy_versions(id),
    scope_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    closed_at TEXT
);

CREATE TABLE IF NOT EXISTS evidence_refs (
    id TEXT PRIMARY KEY,
    context_type TEXT NOT NULL,
    context_id TEXT NOT NULL,
    source TEXT NOT NULL,
    tool TEXT NOT NULL,
    args_json TEXT NOT NULL,
    ref_ids_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_eref_context
    ON evidence_refs(context_type, context_id);
"""


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


class ImproverStore:
    """SQLite-backed store for the Loop-2 meta-change lifecycle."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Additive column migrations for existing databases."""
        cols = {
            r[1] for r in self.conn.execute(
                "PRAGMA table_info(tournament_results)"
            )
        }
        if "corrections_json" not in cols:
            self.conn.execute(
                "ALTER TABLE tournament_results "
                "ADD COLUMN corrections_json TEXT NOT NULL DEFAULT '[]'"
            )
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

    # --- Improver versions ---

    def create_improver(self, imp: ImproverVersion) -> None:
        self._execute(
            """INSERT INTO improver_versions
               (id, parent_id, proposal_id, code_artifact_digest,
                harness_artifact_digest, model_ref, search_policy_ref,
                capability_profile_json, is_champion, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                imp.id, imp.parent_id, imp.proposal_id,
                imp.code_artifact_digest, imp.harness_artifact_digest,
                imp.model_ref, imp.search_policy_ref,
                json.dumps(imp.capability_profile),
                1 if imp.is_champion else 0, imp.created_at,
            ),
        )
        self.conn.commit()

    def get_improver(self, improver_id: str) -> ImproverVersion | None:
        row = self._fetchone(
            "SELECT * FROM improver_versions WHERE id = ?",
            (improver_id,),
        )
        return self._row_to_improver(row) if row else None

    def list_improvers(
        self,
        champion_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ImproverVersion], int]:
        where = "WHERE is_champion = 1" if champion_only else ""
        total = self._fetchone(
            f"SELECT COUNT(*) AS n FROM improver_versions {where}"
        )["n"]
        rows = self._fetchall(
            f"SELECT * FROM improver_versions {where} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [self._row_to_improver(r) for r in rows], total

    def get_champion(self) -> ImproverVersion | None:
        row = self._fetchone(
            "SELECT * FROM improver_versions WHERE is_champion = 1 "
            "ORDER BY created_at LIMIT 1"
        )
        return self._row_to_improver(row) if row else None

    def count_champions(self) -> int:
        return self._fetchone(
            "SELECT COUNT(*) AS n FROM improver_versions "
            "WHERE is_champion = 1"
        )["n"]

    def set_champion_flag(self, improver_id: str, is_champion: bool) -> None:
        """Flip the pointer flag. Only meaningful inside a
        promote/rollback transaction — never called alone by tools."""
        self._execute(
            "UPDATE improver_versions SET is_champion = ? WHERE id = ?",
            (1 if is_champion else 0, improver_id),
        )

    def lineage(self, improver_id: str) -> list[ImproverVersion]:
        """Walk parent links from the improver back to genesis.

        Returns oldest-first (genesis … the improver itself). Cycles
        and dangling parents terminate the walk — the integrity layer
        reports them separately.
        """
        chain: list[ImproverVersion] = []
        seen: set[str] = set()
        current = self.get_improver(improver_id)
        while current is not None and current.id not in seen:
            seen.add(current.id)
            chain.append(current)
            if current.parent_id is None:
                break
            current = self.get_improver(current.parent_id)
        chain.reverse()
        return chain

    def is_descendant(self, candidate_id: str, ancestor_id: str) -> bool:
        """True if ancestor_id appears in candidate_id's parent chain."""
        for imp in self.lineage(candidate_id):
            if imp.id == ancestor_id and imp.id != candidate_id:
                return True
        return False

    def _row_to_improver(self, row: sqlite3.Row) -> ImproverVersion:
        return ImproverVersion(
            id=row["id"],
            parent_id=row["parent_id"],
            proposal_id=row["proposal_id"],
            code_artifact_digest=row["code_artifact_digest"],
            harness_artifact_digest=row["harness_artifact_digest"],
            model_ref=row["model_ref"],
            search_policy_ref=row["search_policy_ref"],
            capability_profile=json.loads(row["capability_profile_json"]),
            is_champion=bool(row["is_champion"]),
            created_at=row["created_at"],
        )

    # --- Meta contracts ---

    def next_contract_version(self) -> int:
        row = self._fetchone(
            "SELECT MAX(version) AS v FROM meta_contracts"
        )
        return (row["v"] or 0) + 1

    def create_meta_contract(self, contract: MetaContract) -> None:
        self._execute(
            """INSERT INTO meta_contracts
               (id, version, metrics_json, promotion_policy_json,
                holdouts_json, budget_json, frozen, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                contract.id, contract.version,
                json.dumps(contract.metrics),
                json.dumps(contract.promotion_policy),
                json.dumps(contract.holdouts)
                if contract.holdouts is not None else None,
                json.dumps(contract.budget)
                if contract.budget is not None else None,
                1 if contract.frozen else 0, contract.created_at,
            ),
        )
        self.conn.commit()

    def get_meta_contract(self, contract_id: str) -> MetaContract | None:
        row = self._fetchone(
            "SELECT * FROM meta_contracts WHERE id = ?", (contract_id,)
        )
        return self._row_to_contract(row) if row else None

    def freeze_meta_contract(self, contract_id: str) -> None:
        self._execute(
            "UPDATE meta_contracts SET frozen = 1 WHERE id = ?",
            (contract_id,),
        )

    def list_meta_contracts(self) -> list[MetaContract]:
        rows = self._fetchall(
            "SELECT * FROM meta_contracts ORDER BY version"
        )
        return [self._row_to_contract(r) for r in rows]

    def _row_to_contract(self, row: sqlite3.Row) -> MetaContract:
        return MetaContract(
            id=row["id"],
            version=row["version"],
            metrics=json.loads(row["metrics_json"]),
            promotion_policy=json.loads(row["promotion_policy_json"]),
            holdouts=json.loads(row["holdouts_json"])
            if row["holdouts_json"] else None,
            budget=json.loads(row["budget_json"])
            if row["budget_json"] else None,
            frozen=bool(row["frozen"]),
            created_at=row["created_at"],
        )

    # --- Meta-change proposals ---

    def create_proposal(self, proposal: MetaChangeProposal) -> None:
        self._execute(
            """INSERT INTO meta_change_proposals
               (id, proposer_improver_id, spec_delta_json, class_map_json,
                expected_benefit, falsification, budget_json,
                rollback_plan, status, rejection_reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                proposal.id, proposal.proposer_improver_id,
                json.dumps(proposal.spec_delta),
                json.dumps(proposal.class_map),
                proposal.expected_benefit, proposal.falsification,
                json.dumps(proposal.budget)
                if proposal.budget is not None else None,
                proposal.rollback_plan, proposal.status.value,
                proposal.rejection_reason, proposal.created_at,
            ),
        )
        self.conn.commit()

    def get_proposal(self, proposal_id: str) -> MetaChangeProposal | None:
        row = self._fetchone(
            "SELECT * FROM meta_change_proposals WHERE id = ?",
            (proposal_id,),
        )
        return self._row_to_proposal(row) if row else None

    def list_proposals(
        self,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[MetaChangeProposal], int]:
        clauses: list[str] = []
        params: list = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        total = self._fetchone(
            f"SELECT COUNT(*) AS n FROM meta_change_proposals {where}",
            tuple(params),
        )["n"]
        rows = self._fetchall(
            f"SELECT * FROM meta_change_proposals {where} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return [self._row_to_proposal(r) for r in rows], total

    def set_proposal_status(
        self,
        proposal_id: str,
        status: ProposalStatus,
        rejection_reason: str | None = None,
    ) -> None:
        self._execute(
            "UPDATE meta_change_proposals SET status = ?, "
            "rejection_reason = ? WHERE id = ?",
            (status.value, rejection_reason, proposal_id),
        )
        self.conn.commit()

    def _row_to_proposal(self, row: sqlite3.Row) -> MetaChangeProposal:
        return MetaChangeProposal(
            id=row["id"],
            proposer_improver_id=row["proposer_improver_id"],
            spec_delta=json.loads(row["spec_delta_json"]),
            class_map=json.loads(row["class_map_json"]),
            expected_benefit=row["expected_benefit"],
            falsification=row["falsification"],
            budget=json.loads(row["budget_json"])
            if row["budget_json"] else None,
            rollback_plan=row["rollback_plan"],
            status=ProposalStatus(row["status"]),
            rejection_reason=row["rejection_reason"],
            created_at=row["created_at"],
        )

    # --- Tournaments ---

    def create_tournament(self, tournament: Tournament) -> None:
        self._execute(
            """INSERT INTO tournaments
               (id, contract_id, parent_improver_id,
                candidate_improver_id, budget_json, seeds_json,
                status, recursive_gain, created_at, closed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                tournament.id, tournament.contract_id,
                tournament.parent_improver_id,
                tournament.candidate_improver_id,
                json.dumps(tournament.budget),
                json.dumps(tournament.seeds)
                if tournament.seeds is not None else None,
                tournament.status.value, tournament.recursive_gain,
                tournament.created_at, tournament.closed_at,
            ),
        )
        self.conn.commit()

    def get_tournament(self, tournament_id: str) -> Tournament | None:
        row = self._fetchone(
            "SELECT * FROM tournaments WHERE id = ?", (tournament_id,)
        )
        return self._row_to_tournament(row) if row else None

    def list_tournaments(
        self,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Tournament], int]:
        clauses: list[str] = []
        params: list = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        total = self._fetchone(
            f"SELECT COUNT(*) AS n FROM tournaments {where}",
            tuple(params),
        )["n"]
        rows = self._fetchall(
            f"SELECT * FROM tournaments {where} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return [self._row_to_tournament(r) for r in rows], total

    def close_tournament(
        self, tournament_id: str, recursive_gain: float | None
    ) -> None:
        self._execute(
            "UPDATE tournaments SET status = ?, recursive_gain = ?, "
            "closed_at = ? WHERE id = ?",
            (
                TournamentStatus.closed.value, recursive_gain,
                _now_iso(), tournament_id,
            ),
        )
        self.conn.commit()

    def list_tournaments_for_improver(
        self, improver_id: str
    ) -> list[Tournament]:
        rows = self._fetchall(
            "SELECT * FROM tournaments WHERE parent_improver_id = ? "
            "OR candidate_improver_id = ? ORDER BY created_at",
            (improver_id, improver_id),
        )
        return [self._row_to_tournament(r) for r in rows]

    def _row_to_tournament(self, row: sqlite3.Row) -> Tournament:
        return Tournament(
            id=row["id"],
            contract_id=row["contract_id"],
            parent_improver_id=row["parent_improver_id"],
            candidate_improver_id=row["candidate_improver_id"],
            budget=json.loads(row["budget_json"]),
            seeds=json.loads(row["seeds_json"])
            if row["seeds_json"] else None,
            status=TournamentStatus(row["status"]),
            recursive_gain=row["recursive_gain"],
            created_at=row["created_at"],
            closed_at=row["closed_at"],
        )

    # --- Tournament results (insert-only) ---

    def create_tournament_result(self, result: TournamentResult) -> None:
        self._execute(
            """INSERT INTO tournament_results
               (id, tournament_id, arm, descendant_spec_json,
                metrics_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                result.id, result.tournament_id, result.arm.value,
                json.dumps(result.descendant_spec),
                json.dumps(result.metrics), result.created_at,
            ),
        )
        self.conn.commit()

    def list_tournament_results(
        self, tournament_id: str, arm: str | None = None
    ) -> list[TournamentResult]:
        if arm is not None:
            rows = self._fetchall(
                "SELECT * FROM tournament_results "
                "WHERE tournament_id = ? AND arm = ? "
                "ORDER BY created_at",
                (tournament_id, arm),
            )
        else:
            rows = self._fetchall(
                "SELECT * FROM tournament_results "
                "WHERE tournament_id = ? ORDER BY created_at",
                (tournament_id,),
            )
        return [self._row_to_tournament_result(r) for r in rows]

    def correct_tournament_result(
        self,
        result_id: str,
        reason: str,
        metrics: dict | None = None,
        descendant_spec: dict | None = None,
    ) -> dict:
        """Recorded repair: rewrite a result row's metrics/spec.

        Not a silent rewrite — the correction is appended to
        corrections_json with the previous values, the reason, and a
        timestamp. Only allowed while the tournament is open: a sealed
        record stays sealed.
        """
        row = self._fetchone(
            "SELECT * FROM tournament_results WHERE id = ?",
            (result_id,),
        )
        if row is None:
            raise ValueError(f"Tournament result not found: {result_id}")
        tournament = self.get_tournament(row["tournament_id"])
        if tournament is None:
            raise ValueError(
                f"Tournament not found: {row['tournament_id']}"
            )
        if tournament.status != TournamentStatus.open:
            raise ValueError(
                f"Tournament {tournament.id} is closed — its results "
                "are sealed and cannot be corrected."
            )
        corrections = json.loads(row["corrections_json"] or "[]")
        corrections.append({
            "corrected_at": _now_iso(),
            "reason": reason,
            "metrics_from": json.loads(row["metrics_json"]),
            "descendant_spec_from": json.loads(row["descendant_spec_json"]),
        })
        self._execute(
            """UPDATE tournament_results
               SET metrics_json = ?, descendant_spec_json = ?,
                   corrections_json = ?
               WHERE id = ?""",
            (
                json.dumps(metrics if metrics is not None
                           else json.loads(row["metrics_json"])),
                json.dumps(descendant_spec if descendant_spec is not None
                           else json.loads(row["descendant_spec_json"])),
                json.dumps(corrections),
                result_id,
            ),
        )
        self.conn.commit()
        return {
            "result_id": result_id,
            "tournament_id": row["tournament_id"],
            "corrections_count": len(corrections),
            "corrected_at": corrections[-1]["corrected_at"],
        }

    def _row_to_tournament_result(
        self, row: sqlite3.Row
    ) -> TournamentResult:
        return TournamentResult(
            id=row["id"],
            tournament_id=row["tournament_id"],
            arm=TournamentArm(row["arm"]),
            descendant_spec=json.loads(row["descendant_spec_json"]),
            metrics=json.loads(row["metrics_json"]),
            corrections=json.loads(row["corrections_json"] or "[]"),
            created_at=row["created_at"],
        )

    # --- Tournament-campaign links (insert-only) ---

    def create_tournament_campaign(self, link: TournamentCampaign) -> None:
        self._execute(
            """INSERT INTO tournament_campaigns
               (id, tournament_id, arm, campaign_id,
                upstream_contract_id, challenger_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                link.id, link.tournament_id, link.arm.value,
                link.campaign_id, link.upstream_contract_id,
                link.challenger_id, link.created_at,
            ),
        )
        self.conn.commit()

    def get_tournament_campaign(
        self, tournament_id: str, arm: str
    ) -> TournamentCampaign | None:
        row = self._fetchone(
            "SELECT * FROM tournament_campaigns "
            "WHERE tournament_id = ? AND arm = ?",
            (tournament_id, arm),
        )
        return self._row_to_tournament_campaign(row) if row else None

    def get_tournament_campaign_by_id(
        self, link_id: str
    ) -> TournamentCampaign | None:
        row = self._fetchone(
            "SELECT * FROM tournament_campaigns WHERE id = ?",
            (link_id,),
        )
        return self._row_to_tournament_campaign(row) if row else None

    def get_tournament_campaign_by_campaign(
        self, campaign_id: str
    ) -> TournamentCampaign | None:
        row = self._fetchone(
            "SELECT * FROM tournament_campaigns WHERE campaign_id = ?",
            (campaign_id,),
        )
        return self._row_to_tournament_campaign(row) if row else None

    def list_tournament_campaigns(
        self, tournament_id: str | None = None
    ) -> list[TournamentCampaign]:
        if tournament_id is not None:
            rows = self._fetchall(
                "SELECT * FROM tournament_campaigns "
                "WHERE tournament_id = ? ORDER BY created_at",
                (tournament_id,),
            )
        else:
            rows = self._fetchall(
                "SELECT * FROM tournament_campaigns ORDER BY created_at"
            )
        return [self._row_to_tournament_campaign(r) for r in rows]

    def _row_to_tournament_campaign(
        self, row: sqlite3.Row
    ) -> TournamentCampaign:
        return TournamentCampaign(
            id=row["id"],
            tournament_id=row["tournament_id"],
            arm=TournamentArm(row["arm"]),
            campaign_id=row["campaign_id"],
            upstream_contract_id=row["upstream_contract_id"],
            challenger_id=row["challenger_id"],
            created_at=row["created_at"],
        )

    # --- Meta decisions (insert-only) ---

    def create_meta_decision(self, decision: MetaDecision) -> None:
        self._execute(
            """INSERT INTO meta_decisions
               (id, candidate_improver_id, contract_id, tournament_id,
                verdict, evidence_refs_json, rationale, decided_by,
                claim_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                decision.id, decision.candidate_improver_id,
                decision.contract_id, decision.tournament_id,
                decision.verdict.value,
                json.dumps(decision.evidence_refs),
                decision.rationale, decision.decided_by,
                decision.claim_id, decision.created_at,
            ),
        )
        self.conn.commit()

    def get_meta_decision(self, decision_id: str) -> MetaDecision | None:
        row = self._fetchone(
            "SELECT * FROM meta_decisions WHERE id = ?", (decision_id,)
        )
        return self._row_to_decision(row) if row else None

    def list_meta_decisions(
        self, candidate_improver_id: str | None = None
    ) -> list[MetaDecision]:
        if candidate_improver_id is not None:
            rows = self._fetchall(
                "SELECT * FROM meta_decisions "
                "WHERE candidate_improver_id = ? ORDER BY created_at",
                (candidate_improver_id,),
            )
        else:
            rows = self._fetchall(
                "SELECT * FROM meta_decisions ORDER BY created_at"
            )
        return [self._row_to_decision(r) for r in rows]

    def latest_decision(
        self, candidate_improver_id: str
    ) -> MetaDecision | None:
        row = self._fetchone(
            "SELECT * FROM meta_decisions "
            "WHERE candidate_improver_id = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (candidate_improver_id,),
        )
        return self._row_to_decision(row) if row else None

    def set_decision_claim(self, decision_id: str, claim_id: str) -> None:
        self._execute(
            "UPDATE meta_decisions SET claim_id = ? WHERE id = ?",
            (claim_id, decision_id),
        )
        self.conn.commit()

    def _row_to_decision(self, row: sqlite3.Row) -> MetaDecision:
        return MetaDecision(
            id=row["id"],
            candidate_improver_id=row["candidate_improver_id"],
            contract_id=row["contract_id"],
            tournament_id=row["tournament_id"],
            verdict=DecisionVerdict(row["verdict"]),
            evidence_refs=json.loads(row["evidence_refs_json"]),
            rationale=row["rationale"],
            decided_by=row["decided_by"],
            claim_id=row["claim_id"],
            created_at=row["created_at"],
        )

    # --- Policy versions ---

    def create_policy_version(self, policy: PolicyVersion) -> None:
        self._execute(
            """INSERT INTO policy_versions
               (id, improver_version_id, decision_id, policy_json,
                previous_champion_id, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                policy.id, policy.improver_version_id,
                policy.decision_id, json.dumps(policy.policy),
                policy.previous_champion_id, policy.status.value,
                policy.created_at,
            ),
        )

    def get_policy_version(
        self, policy_version_id: str
    ) -> PolicyVersion | None:
        row = self._fetchone(
            "SELECT * FROM policy_versions WHERE id = ?",
            (policy_version_id,),
        )
        return self._row_to_policy(row) if row else None

    def list_policy_versions(
        self, improver_version_id: str | None = None
    ) -> list[PolicyVersion]:
        if improver_version_id is not None:
            rows = self._fetchall(
                "SELECT * FROM policy_versions "
                "WHERE improver_version_id = ? ORDER BY created_at",
                (improver_version_id,),
            )
        else:
            rows = self._fetchall(
                "SELECT * FROM policy_versions ORDER BY created_at"
            )
        return [self._row_to_policy(r) for r in rows]

    def get_active_policy(
        self, improver_version_id: str
    ) -> PolicyVersion | None:
        row = self._fetchone(
            "SELECT * FROM policy_versions "
            "WHERE improver_version_id = ? AND status = 'active' "
            "ORDER BY created_at DESC LIMIT 1",
            (improver_version_id,),
        )
        return self._row_to_policy(row) if row else None

    def set_policy_status(
        self, policy_version_id: str, status: PolicyStatus
    ) -> None:
        self._execute(
            "UPDATE policy_versions SET status = ? WHERE id = ?",
            (status.value, policy_version_id),
        )

    def _row_to_policy(self, row: sqlite3.Row) -> PolicyVersion:
        return PolicyVersion(
            id=row["id"],
            improver_version_id=row["improver_version_id"],
            decision_id=row["decision_id"],
            policy=json.loads(row["policy_json"]),
            previous_champion_id=row["previous_champion_id"],
            status=PolicyStatus(row["status"]),
            created_at=row["created_at"],
        )

    # --- Canary deployments ---

    def create_canary(self, canary: CanaryDeployment) -> None:
        self._execute(
            """INSERT INTO canary_deployments
               (id, policy_version_id, scope_json, status,
                created_at, closed_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                canary.id, canary.policy_version_id,
                json.dumps(canary.scope), canary.status.value,
                canary.created_at, canary.closed_at,
            ),
        )
        self.conn.commit()

    def get_canary(self, canary_id: str) -> CanaryDeployment | None:
        row = self._fetchone(
            "SELECT * FROM canary_deployments WHERE id = ?",
            (canary_id,),
        )
        return self._row_to_canary(row) if row else None

    def list_canaries(
        self, policy_version_id: str | None = None
    ) -> list[CanaryDeployment]:
        if policy_version_id is not None:
            rows = self._fetchall(
                "SELECT * FROM canary_deployments "
                "WHERE policy_version_id = ? ORDER BY created_at",
                (policy_version_id,),
            )
        else:
            rows = self._fetchall(
                "SELECT * FROM canary_deployments ORDER BY created_at"
            )
        return [self._row_to_canary(r) for r in rows]

    def close_canary(self, canary_id: str, status: CanaryStatus) -> None:
        self._execute(
            "UPDATE canary_deployments SET status = ?, closed_at = ? "
            "WHERE id = ?",
            (status.value, _now_iso(), canary_id),
        )
        self.conn.commit()

    def _row_to_canary(self, row: sqlite3.Row) -> CanaryDeployment:
        return CanaryDeployment(
            id=row["id"],
            policy_version_id=row["policy_version_id"],
            scope=json.loads(row["scope_json"]),
            status=CanaryStatus(row["status"]),
            created_at=row["created_at"],
            closed_at=row["closed_at"],
        )

    # --- Evidence refs (insert-only) ---

    def create_evidence_ref(self, ref: EvidenceRef) -> None:
        self._execute(
            """INSERT INTO evidence_refs
               (id, context_type, context_id, source, tool, args_json,
                ref_ids_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ref.id, ref.context_type.value, ref.context_id,
                ref.source.value, ref.tool, json.dumps(ref.args),
                json.dumps(ref.ref_ids), ref.created_at,
            ),
        )
        self.conn.commit()

    def get_evidence_ref(self, evidence_ref_id: str) -> EvidenceRef | None:
        row = self._fetchone(
            "SELECT * FROM evidence_refs WHERE id = ?",
            (evidence_ref_id,),
        )
        return self._row_to_evidence_ref(row) if row else None

    def list_evidence_refs(
        self, context_type: str, context_id: str
    ) -> list[EvidenceRef]:
        rows = self._fetchall(
            "SELECT * FROM evidence_refs "
            "WHERE context_type = ? AND context_id = ? "
            "ORDER BY created_at",
            (context_type, context_id),
        )
        return [self._row_to_evidence_ref(r) for r in rows]

    def _row_to_evidence_ref(self, row: sqlite3.Row) -> EvidenceRef:
        return EvidenceRef(
            id=row["id"],
            context_type=EvidenceContext(row["context_type"]),
            context_id=row["context_id"],
            source=EvidenceSource(row["source"]),
            tool=row["tool"],
            args=json.loads(row["args_json"]),
            ref_ids=json.loads(row["ref_ids_json"]),
            created_at=row["created_at"],
        )

    # --- GUI stats ---

    def stats(self) -> dict:
        improvers = self._fetchone(
            "SELECT COUNT(*) AS n FROM improver_versions"
        )["n"]
        proposals = {
            r["status"]: r["n"]
            for r in self._fetchall(
                "SELECT status, COUNT(*) AS n FROM meta_change_proposals "
                "GROUP BY status"
            )
        }
        tournaments = {
            r["status"]: r["n"]
            for r in self._fetchall(
                "SELECT status, COUNT(*) AS n FROM tournaments "
                "GROUP BY status"
            )
        }
        results = self._fetchone(
            "SELECT COUNT(*) AS n FROM tournament_results"
        )["n"]
        decisions = self._fetchone(
            "SELECT COUNT(*) AS n FROM meta_decisions"
        )["n"]
        policies = self._fetchone(
            "SELECT COUNT(*) AS n FROM policy_versions"
        )["n"]
        refs = self._fetchone(
            "SELECT COUNT(*) AS n FROM evidence_refs"
        )["n"]
        champion = self.get_champion()
        return {
            "improvers": improvers,
            "champion_id": champion.id if champion else None,
            "proposals": proposals,
            "proposals_total": sum(proposals.values()),
            "tournaments": tournaments,
            "tournaments_total": sum(tournaments.values()),
            "tournament_results": results,
            "decisions": decisions,
            "policy_versions": policies,
            "evidence_refs": refs,
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
