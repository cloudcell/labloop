"""rc-11 extraction findings — anamnesis claim/graph fixes.

R41: assert_claim dedup silently dropped the caller's evidence edges —
a content-identical re-assertion early-returned before the edge insert.
Re-assertion now attaches the new edges to the existing claim and
reports edges_added.

R42: RefType gained bundle | dataref | reference; Relation gained
cites. 'external' is refused when to_ref carries a known internal
prefix, and a store-init retype migrates the legacy misfiled rows the
old vocabulary produced.
"""

from __future__ import annotations

import pytest

from .conftest import call_tool


EV_A = [{"to_ref": "trial-a", "ref_type": "trial",
         "relation": "tested_by"}]
EV_B = [{"to_ref": "trial-b", "ref_type": "trial",
         "relation": "supports"}]


def _edges(store, claim_id):
    return store._fetchall(
        "SELECT to_ref, ref_type, relation FROM claim_edges "
        "WHERE from_claim = ? ORDER BY to_ref", (claim_id,))


class TestDedupAttachesEvidence:
    """R41 — a re-assertion is a lookup AND an edge write."""

    async def test_new_evidence_lands_on_existing_claim(
        self, mcp_server, mem_store
    ):
        r1 = await call_tool(mcp_server, "assert_claim", {
            "content": "rc11 dedup probe", "type": "empirical",
            "confidence": 0.5, "evidence": EV_A,
        })
        cid = r1["claim_id"]
        assert r1.get("deduplicated") is not True

        r2 = await call_tool(mcp_server, "assert_claim", {
            "content": "rc11 dedup probe", "type": "empirical",
            "confidence": 0.5, "evidence": EV_B,
        })
        assert r2["deduplicated"] is True
        assert r2["claim_id"] == cid
        assert r2["edges_added"] == 1
        edges = _edges(mem_store, cid)
        assert {e["to_ref"] for e in edges} == {"trial-a", "trial-b"}

    async def test_same_evidence_not_duplicated(
        self, mcp_server, mem_store
    ):
        r1 = await call_tool(mcp_server, "assert_claim", {
            "content": "rc11 dedup twice", "type": "empirical",
            "confidence": 0.5, "evidence": EV_A,
        })
        cid = r1["claim_id"]
        r2 = await call_tool(mcp_server, "assert_claim", {
            "content": "rc11 dedup twice", "type": "empirical",
            "confidence": 0.5, "evidence": EV_A,
        })
        assert r2["deduplicated"] is True
        assert r2["edges_added"] == 0
        assert len(_edges(mem_store, cid)) == 1

    async def test_dedup_evidence_grows_edges(
        self, mcp_server, mem_store
    ):
        """Minted bare, then evidence accrues via re-assertion."""
        r1 = await call_tool(mcp_server, "assert_claim", {
            "content": "rc11 accrual", "type": "empirical",
            "confidence": 0.3,
        })
        cid = r1["claim_id"]
        assert len(_edges(mem_store, cid)) == 0
        r2 = await call_tool(mcp_server, "assert_claim", {
            "content": "rc11 accrual", "type": "empirical",
            "confidence": 0.3, "evidence": EV_A + EV_B,
        })
        assert r2["edges_added"] == 2
        assert len(_edges(mem_store, cid)) == 2

    async def test_dedup_malformed_evidence_is_named_error(
        self, mcp_server, mem_store
    ):
        await call_tool(mcp_server, "assert_claim", {
            "content": "rc11 malformed", "type": "empirical",
            "confidence": 0.3,
        })
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "rc11 malformed", "type": "empirical",
            "confidence": 0.3,
            "evidence": [{"to_ref": "trial-x", "ref_type": "bogus",
                          "relation": "supports"}],
        })
        assert "error" in r
        assert "ref_type" in r["error"]

    async def test_dedup_validates_claim_refs(
        self, mcp_server, mem_store
    ):
        await call_tool(mcp_server, "assert_claim", {
            "content": "rc11 claimref", "type": "empirical",
            "confidence": 0.3,
        })
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "rc11 claimref", "type": "empirical",
            "confidence": 0.3,
            "evidence": [{"to_ref": "claim-nonexist",
                          "ref_type": "claim", "relation": "supports"}],
        })
        assert "error" in r
        assert "not found" in r["error"]


class TestReferenceVocabulary:
    """R42 — honest types for bundle/data-ref ids; cites relation;
    external refuses internal prefixes."""

    async def test_bundle_dataref_reference_accepted(self, mcp_server):
        cid = (
            await call_tool(mcp_server, "assert_claim", {
                "content": "rc11 vocab", "type": "empirical",
                "confidence": 0.3,
            })
        )["claim_id"]
        for rt, ref in (("bundle", "bundle-abc12345"),
                        ("dataref", "data-ref-xyz"),
                        ("reference", "data-ref-xyz")):
            r = await call_tool(mcp_server, "relate", {
                "from_claim": cid, "to_ref": ref,
                "ref_type": rt, "relation": "cites",
            })
            assert "error" not in r, (rt, r)

    async def test_external_guard_refuses_internal_prefix(
        self, mcp_server
    ):
        cid = (
            await call_tool(mcp_server, "assert_claim", {
                "content": "rc11 guard", "type": "empirical",
                "confidence": 0.3,
            })
        )["claim_id"]
        for ref in ("bundle-44bd7102", "data-ref-9", "claim-x",
                    "trial-1"):
            r = await call_tool(mcp_server, "relate", {
                "from_claim": cid, "to_ref": ref,
                "ref_type": "external", "relation": "cites",
            })
            assert "error" in r, ref
            assert "internal prefix" in r["error"], r["error"]

    async def test_external_guard_on_assert_claim_evidence(
        self, mcp_server
    ):
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "rc11 guard2", "type": "empirical",
            "confidence": 0.3,
            "evidence": [{"to_ref": "bundle-1", "ref_type": "external",
                          "relation": "cites"}],
        })
        assert "error" in r
        assert "internal prefix" in r["error"]

    async def test_genuine_external_still_accepted(self, mcp_server):
        cid = (
            await call_tool(mcp_server, "assert_claim", {
                "content": "rc11 ext-ok", "type": "empirical",
                "confidence": 0.3,
            })
        )["claim_id"]
        r = await call_tool(mcp_server, "relate", {
            "from_claim": cid, "to_ref": "doi:10.1234/example",
            "ref_type": "external", "relation": "cites",
        })
        assert "error" not in r, r

    async def test_cites_is_not_evidence(self, mcp_server):
        """A cites edge does not satisfy the evidence ceiling —
        citation is not support."""
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "rc11 cites-not-evidence", "type": "empirical",
            "confidence": 0.9,
            "evidence": [{"to_ref": "bundle-1", "ref_type": "bundle",
                          "relation": "cites"}],
        })
        assert "error" in r
        assert "confidence" in r["error"].lower() or \
               "evidence" in r["error"].lower()


class TestMisfiledExternalMigration:
    """R42 — legacy external/*-prefixed edges retype at store init;
    the integrity check reports the residue (zero post-migration)."""

    def test_init_retypes_misfiled_edge(self, tmp_path):
        from ml_anamnesis_mcp.state.models import (
            Claim, ClaimEdge, ClaimType, RefType, Relation,
        )
        from ml_anamnesis_mcp.state.store import MemoryStore, content_hash

        db = str(tmp_path / "m.db")
        store = MemoryStore(db)
        store.connect()
        store.create_claim(Claim(
            id="claim-1", content="c", type=ClaimType.empirical,
            confidence=0.5, content_hash=content_hash("c"),
        ))
        store.create_edge(ClaimEdge(
            id="edge-1", from_claim="claim-1", to_ref="bundle-abc",
            ref_type=RefType.external, relation=Relation.cites,
        ))
        store.conn.commit()
        store.close()

        # Reopen — the init-time retype fires.
        store = MemoryStore(db)
        store.connect()
        try:
            row = store._fetchall(
                "SELECT ref_type FROM claim_edges WHERE id='edge-1'"
            )[0]
            assert row["ref_type"] == "bundle"
            from ml_anamnesis_mcp.integrity.checks import run_checks
            report = run_checks(store)
            check = next(
                c for c in report["checks"]
                if c["name"] == "misfiled_external_refs"
            )
            assert check["ok"], check
        finally:
            store.close()

    def test_check_flags_unmigrated_shape(self, tmp_path):
        """The audit still fires if a misfiled edge exists — e.g. one
        written before the guard (inserted directly here)."""
        from ml_anamnesis_mcp.integrity.checks import run_checks
        from ml_anamnesis_mcp.state.models import Claim, ClaimType
        from ml_anamnesis_mcp.state.store import (
            MemoryStore, content_hash,
        )

        store = MemoryStore(str(tmp_path / "m.db"))
        store.connect()
        try:
            store.create_claim(Claim(
                id="claim-1", content="c", type=ClaimType.empirical,
                confidence=0.5, content_hash=content_hash("c"),
            ))
            # Bypass the model/guard — the audit is precisely for rows
            # the write path could not have produced post-fix.
            store._execute(
                "INSERT INTO claim_edges (id, from_claim, to_ref, "
                "ref_type, relation, weight, created_at) "
                "VALUES ('edge-x', 'claim-1', 'tres-9', "
                "'external', 'cites', 1.0, '2026-01-01T00:00:00+00:00')"
            )
            store.conn.commit()
            report = run_checks(store)
            check = next(
                c for c in report["checks"]
                if c["name"] == "misfiled_external_refs"
            )
            assert not check["ok"]
            assert check["violations"][0]["to_ref"] == "tres-9"
        finally:
            store.close()
