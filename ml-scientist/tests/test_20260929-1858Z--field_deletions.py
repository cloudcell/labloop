"""Field deletions (plan-20260929-1643Z) — drop-migration probes and
API-surface pins for claims.importance and claim_edges.weight.

The two fields were written-never-read — midpoint/unit defaults that
carried no information and invited false readings (a weighted-looking
claim graph that never ranked). The plan deletes them outright:
guarded DROP COLUMN migrations, params removed from assert_claim /
relate, echoes and renders swept.

Ontology: process — schema migration + API boundary probes.
"""

import inspect
import sqlite3

import pytest

from ml_anamnesis_mcp import _grounded_constants as _gc


# ---------------------------------------------------------------------------
# Drop migration — legacy tables carrying both columns
# ---------------------------------------------------------------------------

LEGACY_SCHEMA = """
CREATE TABLE claims (
    id TEXT PRIMARY KEY, content TEXT, type TEXT,
    confidence REAL, importance REAL,
    valid_from TEXT, valid_until TEXT,
    supersedes_id TEXT, content_hash TEXT UNIQUE,
    source_id TEXT, created_at TEXT
);
CREATE TABLE claim_edges (
    id TEXT PRIMARY KEY, from_claim TEXT, to_ref TEXT,
    ref_type TEXT, relation TEXT, weight REAL,
    source_id TEXT, created_at TEXT
);
INSERT INTO claims VALUES (
    'claim-old', 'old', 'empirical', 0.5, 0.5,
    't', NULL, NULL, 'h-old', NULL, 't1');
INSERT INTO claims VALUES (
    'claim-hand', 'hand-set', 'empirical', 0.7, 0.9,
    't', NULL, NULL, 'h-hand', NULL, 't2');
INSERT INTO claim_edges VALUES (
    'edge-old', 'claim-old', 'tres-1', 'trial', 'tested_by',
    1.0, NULL, 't3');
INSERT INTO claim_edges VALUES (
    'edge-hand', 'claim-hand', 'tres-2', 'trial', 'supports',
    0.4, NULL, 't4');
"""


def _cols(store, table):
    return {
        r["name"] for r in store._fetchall(f"PRAGMA table_info({table})")
    }


class TestDropMigration:
    def test_both_columns_dropped_rows_intact(self, tmp_path):
        from ml_anamnesis_mcp.state.store import MemoryStore

        db = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db))
        conn.executescript(LEGACY_SCHEMA)
        conn.close()

        store = MemoryStore(str(db))
        store.connect()
        try:
            assert "importance" not in _cols(store, "claims")
            assert "weight" not in _cols(store, "claim_edges")

            claim = store.get_claim("claim-hand")
            assert claim.content == "hand-set"
            assert claim.confidence == 0.7
            edge = store.get_edge("edge-hand")
            assert edge.relation.value == "supports"
            assert edge.to_ref == "tres-2"
        finally:
            store.close()

    def test_migration_is_idempotent(self, tmp_path):
        from ml_anamnesis_mcp.state.store import MemoryStore

        db = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db))
        conn.executescript(LEGACY_SCHEMA)
        conn.close()

        for _ in range(2):
            store = MemoryStore(str(db))
            store.connect()
            try:
                assert "importance" not in _cols(store, "claims")
                assert "weight" not in _cols(store, "claim_edges")
            finally:
                store.close()

    def test_non_default_counts_logged(self, tmp_path, caplog):
        """The drop leaves an audit trail — one log line per column
        naming how many non-default rows were discarded."""
        import logging

        from ml_anamnesis_mcp.state.store import MemoryStore

        db = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db))
        conn.executescript(LEGACY_SCHEMA)
        conn.close()

        store = MemoryStore(str(db))
        with caplog.at_level(logging.INFO, "ml_anamnesis_mcp"):
            store.connect()
        try:
            msgs = [
                r.message for r in caplog.records
                if "dropping" in r.message
            ]
            assert any("importance" in m and "1" in m for m in msgs)
            assert any("weight" in m and "1" in m for m in msgs)
        finally:
            store.close()

    def test_fresh_db_never_has_the_columns(self, tmp_path):
        from ml_anamnesis_mcp.state.store import MemoryStore

        store = MemoryStore(str(tmp_path / "fresh.db"))
        store.connect()
        try:
            assert "importance" not in _cols(store, "claims")
            assert "weight" not in _cols(store, "claim_edges")
        finally:
            store.close()


# ---------------------------------------------------------------------------
# API surface — the params are gone, not deprecated
# ---------------------------------------------------------------------------


class TestApiSurface:
    def test_assert_claim_has_no_importance_param(self):
        from ml_anamnesis_mcp.tools.claims import register
        # The tool functions are closures inside register(); the
        # signature check lives at the pydantic layer — models must
        # not carry the fields either.
        src = inspect.getsource(register)
        assert "importance" not in src
        assert "weight" not in src

    def test_models_carry_no_dead_fields(self):
        from ml_anamnesis_mcp.state.models import Claim, ClaimEdge

        assert "importance" not in Claim.model_fields
        assert "weight" not in ClaimEdge.model_fields

    def test_registry_entries_gone(self):
        assert not hasattr(_gc, "CLAIM_IMPORTANCE_DEFAULT")
        assert not hasattr(_gc, "EDGE_WEIGHT_DEFAULT")
        assert "CLAIM_IMPORTANCE_DEFAULT" not in _gc.REGISTRY
        assert "EDGE_WEIGHT_DEFAULT" not in _gc.REGISTRY

    def test_no_ungrounded_or_transitional_entries(self):
        """Terminal state — the grounding transition completed."""
        assert not [
            c.name for c in _gc.REGISTRY.values()
            if c.status == "UNGROUNDED" or c.transitional
        ]
