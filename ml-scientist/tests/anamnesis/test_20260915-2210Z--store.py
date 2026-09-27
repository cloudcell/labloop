"""Store-level tests for the anamnesis claim graph."""

from __future__ import annotations

import pytest

from ml_anamnesis_mcp.state.models import Claim, ClaimEdge, ClaimType, RefType, Relation
from ml_anamnesis_mcp.state.store import content_hash, normalize_content


def make_claim_obj(cid: str, content: str = "X outperforms Y", **kw) -> Claim:
    return Claim(
        id=cid,
        content=content,
        type=kw.pop("type", ClaimType.empirical),
        confidence=kw.pop("confidence", 0.8),
        content_hash=content_hash(content),
        **kw,
    )


def make_edge(eid: str, frm: str, to: str, relation=Relation.tested_by,
              ref_type=RefType.trial) -> ClaimEdge:
    return ClaimEdge(
        id=eid, from_claim=frm, to_ref=to,
        ref_type=ref_type, relation=relation,
    )


class TestSchema:
    def test_tables_created(self, mem_store):
        tables = {
            r["name"]
            for r in mem_store._fetchall(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"claims", "claim_edges"} <= tables

    def test_indexes_created(self, mem_store):
        idx = {
            r["name"]
            for r in mem_store._fetchall(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert {"idx_edges_from", "idx_edges_to"} <= idx


class TestNormalization:
    def test_normalize_collapses_whitespace(self):
        assert normalize_content("  a   b\n c ") == "a b c"

    def test_hash_stable_under_whitespace(self):
        assert content_hash("a  b") == content_hash(" a b ")


class TestClaimCRUD:
    def test_create_and_get(self, mem_store):
        mem_store.create_claim(make_claim_obj("claim-1"))
        c = mem_store.get_claim("claim-1")
        assert c is not None and c.content == "X outperforms Y"
        assert c.type == ClaimType.empirical

    def test_get_missing_returns_none(self, mem_store):
        assert mem_store.get_claim("claim-nope") is None

    def test_dedup_by_hash(self, mem_store):
        mem_store.create_claim(make_claim_obj("claim-1", "a b"))
        found = mem_store.get_claim_by_hash(content_hash(" a  b "))
        assert found is not None and found.id == "claim-1"

    def test_unique_hash_constraint(self, mem_store):
        mem_store.create_claim(make_claim_obj("claim-1", "same"))
        with pytest.raises(Exception):
            mem_store.create_claim(make_claim_obj("claim-2", "same"))


class TestEdgeCRUD:
    def test_edges_from_and_to(self, mem_store):
        mem_store.create_claim(make_claim_obj("claim-1"))
        mem_store.create_edge(make_edge("edge-1", "claim-1", "trial-9"))
        out = mem_store.edges_from("claim-1")
        inc = mem_store.edges_to("trial-9")
        assert len(out) == 1 and out[0].relation == Relation.tested_by
        assert len(inc) == 1 and inc[0].from_claim == "claim-1"

    def test_find_edge_dedup(self, mem_store):
        mem_store.create_claim(make_claim_obj("claim-1"))
        mem_store.create_edge(make_edge("edge-1", "claim-1", "trial-9"))
        dup = mem_store.find_edge("claim-1", "trial-9", "trial", "tested_by")
        assert dup is not None and dup.id == "edge-1"
        assert mem_store.find_edge("claim-1", "trial-9", "trial", "supports") is None


class TestListFilters:
    def _seed(self, store):
        store.create_claim(make_claim_obj("claim-a", "claim A"))
        store.create_claim(make_claim_obj("claim-b", "claim B",
                                          type=ClaimType.methodological))
        store.create_claim(make_claim_obj("claim-c", "claim C",
                                          valid_until="2000-01-01T00:00:00+00:00"))
        store.create_claim(make_claim_obj("claim-d", "claim D",
                                          supersedes_id="claim-a"))

    def test_type_filter(self, mem_store):
        self._seed(mem_store)
        claims, total = mem_store.list_claims(type="methodological",
                                              include_superseded=True,
                                              include_expired=True)
        assert total == 1 and claims[0].id == "claim-b"

    def test_default_hides_superseded_and_expired(self, mem_store):
        self._seed(mem_store)
        # claim-d has supersedes_id → claim-a is superseded
        store_edge = make_edge("edge-s", "claim-d", "claim-a",
                               relation=Relation.supersedes,
                               ref_type=RefType.claim)
        mem_store.create_edge(store_edge)
        claims, total = mem_store.list_claims()
        ids = {c.id for c in claims}
        assert "claim-a" not in ids  # superseded
        assert "claim-c" not in ids  # expired
        assert {"claim-b", "claim-d"} <= ids

    def test_pagination(self, mem_store):
        self._seed(mem_store)
        page, total = mem_store.list_claims(limit=2, offset=0,
                                            include_superseded=True,
                                            include_expired=True)
        assert len(page) == 2 and total == 4


class TestTransaction:
    def test_rollback_on_error(self, mem_store):
        with pytest.raises(Exception):
            with mem_store.transaction():
                mem_store.create_claim(make_claim_obj("claim-t"))
                raise RuntimeError("boom")
        assert mem_store.get_claim("claim-t") is None

    def test_commit_on_success(self, mem_store):
        with mem_store.transaction():
            mem_store.create_claim(make_claim_obj("claim-t"))
        assert mem_store.get_claim("claim-t") is not None
