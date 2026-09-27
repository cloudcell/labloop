"""Enforcement tests — the claim-graph integrity rules at tool level."""

from __future__ import annotations

from .conftest import call_tool, make_claim


class TestAssertClaim:
    async def test_high_confidence_without_evidence_rejected(self, mcp_server):
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "unsupported claim",
            "type": "empirical",
            "confidence": 0.9,
        })
        assert "error" in r
        assert "prior ceiling" in r["error"]

    async def test_prior_ceiling_configurable(self, mem_store):
        """[integrity] prior_confidence_max governs the write-time
        gate — the same knob the audit reads."""
        from ml_anamnesis_mcp.server import create_server

        mcp = create_server(
            mem_store, integrity_config={"prior_confidence_max": 0.5}
        )
        r = await call_tool(mcp, "assert_claim", {
            "content": "mid-confidence claim",
            "type": "empirical",
            "confidence": 0.4,
        })
        assert "error" not in r  # 0.4 < configured 0.5 → allowed
        r = await call_tool(mcp, "assert_claim", {
            "content": "still too confident",
            "type": "empirical",
            "confidence": 0.6,
        })
        assert "prior ceiling" in r["error"]

    async def test_prior_confidence_without_evidence_accepted(self, mcp_server):
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "prior-level hunch",
            "type": "empirical",
            "confidence": 0.3,
        })
        assert "error" not in r
        assert r["claim_id"].startswith("claim-")

    async def test_evidence_unlocks_high_confidence(self, mcp_server):
        cid = await make_claim(mcp_server, confidence=0.95)
        assert cid.startswith("claim-")

    async def test_structural_edge_does_not_count_as_evidence(self, mcp_server):
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "only similar_to edge",
            "type": "empirical",
            "confidence": 0.9,
            "evidence": [
                {"to_ref": "x", "ref_type": "external", "relation": "similar_to"}
            ],
        })
        assert "error" in r and "prior ceiling" in r["error"]

    async def test_bad_claim_type_rejected(self, mcp_server):
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "bad type", "type": "vibes", "confidence": 0.1,
        })
        assert "error" in r and "empirical" in r["error"] and "methodological" in r["error"]

    async def test_bad_evidence_relation_rejected(self, mcp_server):
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "bad relation", "type": "empirical", "confidence": 0.8,
            "evidence": [
                {"to_ref": "x", "ref_type": "trial", "relation": "vibes"}
            ],
        })
        assert "error" in r and "relation" in r["error"]

    async def test_bad_evidence_ref_type_rejected(self, mcp_server):
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "bad ref_type", "type": "empirical", "confidence": 0.8,
            "evidence": [
                {"to_ref": "x", "ref_type": "vibes", "relation": "tested_by"}
            ],
        })
        assert "error" in r and "ref_type" in r["error"]

    async def test_claim_typed_evidence_ref_must_exist(self, mcp_server):
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "dangling claim ref", "type": "empirical",
            "confidence": 0.8,
            "evidence": [
                {"to_ref": "claim-nope", "ref_type": "claim",
                 "relation": "supports"}
            ],
        })
        assert "error" in r and "not found" in r["error"]

    async def test_external_ref_trusted(self, mcp_server):
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "external evidence", "type": "empirical",
            "confidence": 0.8,
            "evidence": [
                {"to_ref": "doi:10.1/x", "ref_type": "external",
                 "relation": "supports"}
            ],
        })
        assert "error" not in r

    async def test_supersedes_must_exist(self, mcp_server):
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "supersedes ghost", "type": "empirical",
            "confidence": 0.2, "supersedes_id": "claim-nope",
        })
        assert "error" in r and "not found" in r["error"]

    async def test_supersedes_writes_column_and_edge(self, mcp_server):
        """Review note: column + edge are one fact, written atomically."""
        old = await make_claim(mcp_server, "old claim")
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "new claim", "type": "empirical", "confidence": 0.8,
            "supersedes_id": old,
            "evidence": [
                {"to_ref": "t", "ref_type": "trial", "relation": "tested_by"}
            ],
        })
        assert "error" not in r
        got = await call_tool(mcp_server, "get_claim", {"claim_id": r["claim_id"]})
        assert got["claim"]["supersedes_id"] == old
        rels = {e["relation"] for e in got["outgoing_edges"]}
        assert "supersedes" in rels

    async def test_dedup_returns_existing_id(self, mcp_server):
        cid = await make_claim(mcp_server, "  spaced   out  claim ")
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "spaced out claim", "type": "empirical",
            "confidence": 0.1,
        })
        assert r.get("deduplicated") is True
        assert r["claim_id"] == cid

    async def test_dedup_bypasses_evidence_gate(self, mcp_server):
        """Re-asserting existing content is a lookup, not a new claim."""
        cid = await make_claim(mcp_server, "already here")
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "already here", "type": "empirical",
            "confidence": 0.99,  # would fail evidence gate for a new claim
        })
        assert r.get("deduplicated") is True and r["claim_id"] == cid

    async def test_json_string_evidence_accepted(self, mcp_server):
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "json string evidence", "type": "empirical",
            "confidence": 0.8,
            "evidence": '[{"to_ref": "t", "ref_type": "trial", '
                        '"relation": "tested_by"}]',
        })
        assert "error" not in r


class TestRelate:
    async def test_creates_edge(self, mcp_server):
        cid = await make_claim(mcp_server)
        r = await call_tool(mcp_server, "relate", {
            "from_claim": cid, "to_ref": "obs-1",
            "ref_type": "observation", "relation": "supports",
        })
        assert "error" not in r and r["edge_id"].startswith("edge-")

    async def test_from_claim_must_exist(self, mcp_server):
        r = await call_tool(mcp_server, "relate", {
            "from_claim": "claim-nope", "to_ref": "t",
            "ref_type": "trial", "relation": "tested_by",
        })
        assert "error" in r and "not found" in r["error"]

    async def test_self_edge_rejected(self, mcp_server):
        cid = await make_claim(mcp_server)
        r = await call_tool(mcp_server, "relate", {
            "from_claim": cid, "to_ref": cid,
            "ref_type": "claim", "relation": "supports",
        })
        assert "error" in r and "Self-edge" in r["error"]

    async def test_claim_ref_must_exist(self, mcp_server):
        cid = await make_claim(mcp_server)
        r = await call_tool(mcp_server, "relate", {
            "from_claim": cid, "to_ref": "claim-nope",
            "ref_type": "claim", "relation": "supports",
        })
        assert "error" in r and "not found" in r["error"]

    async def test_duplicate_edge_deduped(self, mcp_server):
        cid = await make_claim(mcp_server)
        args = {"from_claim": cid, "to_ref": "t", "ref_type": "trial",
                "relation": "tested_by"}
        r1 = await call_tool(mcp_server, "relate", args)
        r2 = await call_tool(mcp_server, "relate", args)
        assert r2.get("deduplicated") is True
        assert r2["edge_id"] == r1["edge_id"]


class TestGetClaim:
    async def test_provenance_bundle(self, mcp_server):
        cid = await make_claim(mcp_server)
        other = await make_claim(mcp_server, "citing claim")
        await call_tool(mcp_server, "relate", {
            "from_claim": other, "to_ref": cid,
            "ref_type": "claim", "relation": "generalizes",
        })
        r = await call_tool(mcp_server, "get_claim", {"claim_id": cid})
        assert r["claim"]["id"] == cid
        assert len(r["outgoing_edges"]) == 1  # its evidence edge
        assert len(r["incoming_edges"]) == 1  # the citing claim
        assert r["incoming_edges"][0]["from_claim"] == other

    async def test_missing_claim_errors(self, mcp_server):
        r = await call_tool(mcp_server, "get_claim", {"claim_id": "claim-nope"})
        assert "error" in r


class TestListClaims:
    async def test_list_shape_and_filters(self, mcp_server):
        cid = await make_claim(mcp_server, "live claim")
        old = await make_claim(mcp_server, "doomed claim")
        await call_tool(mcp_server, "assert_claim", {
            "content": "replacement", "type": "empirical", "confidence": 0.8,
            "supersedes_id": old,
            "evidence": [
                {"to_ref": "t", "ref_type": "trial", "relation": "tested_by"}
            ],
        })
        r = await call_tool(mcp_server, "list_claims", {})
        ids = {c["id"] for c in r["claims"]}
        assert cid in ids and old not in ids  # superseded hidden
        r2 = await call_tool(mcp_server, "list_claims", {
            "include_superseded": True
        })
        ids2 = {c["id"] for c in r2["claims"]}
        assert old in ids2
