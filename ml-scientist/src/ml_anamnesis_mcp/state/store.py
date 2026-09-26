"""MemoryStore — SQLite persistence for the anamnesis claim graph.

Standalone store (~/.ml-anamnesis/memory.db). Own schema, own lifecycle:
claims outlive programmes by design. Insert-only by surface — a claim
is invalidated by setting valid_until, never by deleting it.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

from .models import Claim, ClaimEdge


SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    id            TEXT PRIMARY KEY,
    content       TEXT NOT NULL,
    type          TEXT NOT NULL,
    confidence    REAL NOT NULL,
    importance    REAL NOT NULL DEFAULT 0.5,
    valid_from    TEXT NOT NULL,
    valid_until   TEXT,
    supersedes_id TEXT REFERENCES claims(id),
    content_hash  TEXT NOT NULL UNIQUE,
    source_id     TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claim_edges (
    id          TEXT PRIMARY KEY,
    from_claim  TEXT NOT NULL REFERENCES claims(id),
    to_ref      TEXT NOT NULL,
    ref_type    TEXT NOT NULL,
    relation    TEXT NOT NULL,
    weight      REAL NOT NULL DEFAULT 1.0,
    source_id   TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_edges_from ON claim_edges(from_claim);
CREATE INDEX IF NOT EXISTS idx_edges_to   ON claim_edges(to_ref);

-- Insert-only acknowledgment ledger for integrity-check findings
-- (recurrent protocol, plan-20260926-0438Z). The check log stays
-- append-only; an ack records disposition, never erases a finding.
CREATE TABLE IF NOT EXISTS violation_acks (
    id TEXT PRIMARY KEY,
    check_name TEXT NOT NULL,
    object_ref TEXT NOT NULL,
    disposition TEXT NOT NULL,
    decided_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def normalize_content(content: str) -> str:
    """Normalize claim text for dedup: strip + collapse whitespace."""
    return " ".join(content.split())


def content_hash(content: str) -> str:
    return hashlib.sha256(normalize_content(content).encode()).hexdigest()


class MemoryStore:
    """SQLite-backed store for claims and claim_edges."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

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

    # --- Violation acknowledgments (insert-only, recurrent protocol) ---

    def record_violation_ack(
        self, *, ack_id: str, check_name: str, object_ref: str,
        disposition: str, decided_by: str, created_at: str,
    ) -> None:
        self._execute(
            "INSERT INTO violation_acks "
            "(id, check_name, object_ref, disposition, decided_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ack_id, check_name, object_ref, disposition, decided_by,
             created_at),
        )
        self.conn.commit()

    def violation_ack_keys(self) -> set[tuple[str, str]]:
        """(check_name, object_ref) pairs already acknowledged."""
        return {
            (r["check_name"], r["object_ref"])
            for r in self._fetchall(
                "SELECT check_name, object_ref FROM violation_acks"
            )
        }

    # --- Claims ---

    def create_claim(self, claim: Claim) -> None:
        self._execute(
            """INSERT INTO claims
               (id, content, type, confidence, importance, valid_from,
                valid_until, supersedes_id, content_hash, source_id,
                created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                claim.id, claim.content, claim.type.value,
                claim.confidence, claim.importance, claim.valid_from,
                claim.valid_until, claim.supersedes_id,
                claim.content_hash, claim.source_id, claim.created_at,
            ),
        )

    def get_claim(self, claim_id: str) -> Claim | None:
        row = self._fetchone("SELECT * FROM claims WHERE id = ?", (claim_id,))
        return self._row_to_claim(row) if row else None

    def get_claim_by_hash(self, chash: str) -> Claim | None:
        row = self._fetchone(
            "SELECT * FROM claims WHERE content_hash = ?", (chash,)
        )
        return self._row_to_claim(row) if row else None

    _SUPERSEDED_CLAUSE = (
        "(id IN (SELECT to_ref FROM claim_edges "
        "WHERE relation = 'supersedes' AND ref_type = 'claim') "
        "OR id IN (SELECT supersedes_id FROM claims "
        "WHERE supersedes_id IS NOT NULL))"
    )

    def list_claims(
        self,
        type: str | None = None,
        include_superseded: bool = False,
        include_expired: bool = False,
        only_superseded: bool = False,
        only_expired: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Claim], int]:
        """List claims with filters. Returns (page, total_count).

        only_superseded / only_expired invert the corresponding filter —
        used by the observability GUI's view tabs.
        """
        clauses: list[str] = []
        params: list = []
        if type is not None:
            clauses.append("type = ?")
            params.append(type)
        if only_superseded:
            clauses.append(self._SUPERSEDED_CLAUSE)
        elif not include_superseded:
            clauses.append(f"NOT {self._SUPERSEDED_CLAUSE}")
        if only_expired:
            clauses.append("valid_until IS NOT NULL AND valid_until < ?")
            params.append(_now_iso())
        elif not include_expired:
            clauses.append("(valid_until IS NULL OR valid_until >= ?)")
            params.append(_now_iso())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        total = self._fetchone(
            f"SELECT COUNT(*) AS n FROM claims {where}", tuple(params)
        )["n"]
        rows = self._fetchall(
            f"SELECT * FROM claims {where} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return [self._row_to_claim(r) for r in rows], total

    def search_claims(self, query: str, limit: int = 50) -> list[Claim]:
        """Substring search over claim content (GUI search box).

        Phase-2 FTS5 recall is a different concern — this is a simple
        LIKE filter for the observability GUI.
        """
        rows = self._fetchall(
            "SELECT * FROM claims WHERE content LIKE ? "
            "ORDER BY created_at DESC LIMIT ?",
            (f"%{query}%", limit),
        )
        return [self._row_to_claim(r) for r in rows]

    def stats(self) -> dict:
        """Header stats for the observability GUI."""
        total = self._fetchone("SELECT COUNT(*) AS n FROM claims")["n"]
        superseded = self._fetchone(
            f"SELECT COUNT(*) AS n FROM claims WHERE {self._SUPERSEDED_CLAUSE}"
        )["n"]
        expired = self._fetchone(
            "SELECT COUNT(*) AS n FROM claims "
            "WHERE valid_until IS NOT NULL AND valid_until < ?",
            (_now_iso(),),
        )["n"]
        live = self._fetchone(
            f"SELECT COUNT(*) AS n FROM claims "
            f"WHERE NOT {self._SUPERSEDED_CLAUSE} "
            "AND (valid_until IS NULL OR valid_until >= ?)",
            (_now_iso(),),
        )["n"]
        edges = self._fetchone("SELECT COUNT(*) AS n FROM claim_edges")["n"]
        by_type = {
            r["type"]: r["n"]
            for r in self._fetchall(
                "SELECT type, COUNT(*) AS n FROM claims GROUP BY type"
            )
        }
        return {
            "total": total,
            "live": live,
            "superseded": superseded,
            "expired": expired,
            "edges": edges,
            "by_type": by_type,
        }

    def _row_to_claim(self, row: sqlite3.Row) -> Claim:
        return Claim(
            id=row["id"],
            content=row["content"],
            type=row["type"],
            confidence=row["confidence"],
            importance=row["importance"],
            valid_from=row["valid_from"],
            valid_until=row["valid_until"],
            supersedes_id=row["supersedes_id"],
            content_hash=row["content_hash"],
            source_id=row["source_id"],
            created_at=row["created_at"],
        )

    # --- Edges ---

    def create_edge(self, edge: ClaimEdge) -> None:
        self._execute(
            """INSERT INTO claim_edges
               (id, from_claim, to_ref, ref_type, relation, weight,
                source_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                edge.id, edge.from_claim, edge.to_ref,
                edge.ref_type.value, edge.relation.value, edge.weight,
                edge.source_id, edge.created_at,
            ),
        )

    def get_edge(self, edge_id: str) -> ClaimEdge | None:
        row = self._fetchone(
            "SELECT * FROM claim_edges WHERE id = ?", (edge_id,)
        )
        return self._row_to_edge(row) if row else None

    def find_edge(
        self, from_claim: str, to_ref: str, ref_type: str, relation: str
    ) -> ClaimEdge | None:
        """Exact-duplicate lookup for idempotent relate()."""
        row = self._fetchone(
            """SELECT * FROM claim_edges
               WHERE from_claim = ? AND to_ref = ?
                 AND ref_type = ? AND relation = ?""",
            (from_claim, to_ref, ref_type, relation),
        )
        return self._row_to_edge(row) if row else None

    def list_edges(self, limit: int = 1000) -> list[ClaimEdge]:
        """All claim edges, newest first — bulk listing for the
        claims://graph projection (agora's cross-server graph)."""
        rows = self._fetchall(
            "SELECT * FROM claim_edges ORDER BY created_at DESC "
            "LIMIT ?",
            (limit,),
        )
        return [self._row_to_edge(r) for r in rows]

    def edges_from(self, claim_id: str) -> list[ClaimEdge]:
        rows = self._fetchall(
            "SELECT * FROM claim_edges WHERE from_claim = ? "
            "ORDER BY created_at",
            (claim_id,),
        )
        return [self._row_to_edge(r) for r in rows]

    def edges_to(self, ref: str) -> list[ClaimEdge]:
        rows = self._fetchall(
            "SELECT * FROM claim_edges WHERE to_ref = ? ORDER BY created_at",
            (ref,),
        )
        return [self._row_to_edge(r) for r in rows]

    def _row_to_edge(self, row: sqlite3.Row) -> ClaimEdge:
        return ClaimEdge(
            id=row["id"],
            from_claim=row["from_claim"],
            to_ref=row["to_ref"],
            ref_type=row["ref_type"],
            relation=row["relation"],
            weight=row["weight"],
            source_id=row["source_id"],
            created_at=row["created_at"],
        )

    # --- Transactions ---

    def transaction(self):
        """Context manager: commit on success, rollback on error."""
        return _Transaction(self.conn)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


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
