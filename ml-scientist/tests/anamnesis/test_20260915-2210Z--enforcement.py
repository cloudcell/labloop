"""Enforcement tests — the claim-graph integrity rules at tool level."""

from __future__ import annotations

from .conftest import call_tool, make_claim


class TestAssertClaim:
    async def test_caller_confidence_is_not_a_parameter(self, mcp_server):
        """The numeric provenance invariant: assert_claim has no
        confidence parameter — a caller cannot assert a number. A
        bare mint records NULL confidence and ungrounded basis."""
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "unsupported claim",
            "type": "empirical",
        })
        assert "error" not in r
        assert r["confidence"] is None
        assert r["confidence_basis"] == "ungrounded"
        got = await call_tool(
            mcp_server, "get_claim", {"claim_id": r["claim_id"]}
        )
        assert got["claim"]["confidence"] is None
        assert got["claim"]["confidence_basis"] == "ungrounded"

    async def test_computation_mints_grounded_confidence(
            self, mcp_server):
        """posterior_from_2lnbf(0.3, 2.7055) = 0.624 — the NAP D-1
        row, recomputed server-side and stored with the record."""
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "computed claim",
            "type": "empirical",
            "confidence_computation": {
                "procedure": "posterior_from_2lnbf",
                "inputs": {"prior": 0.3, "bf_2ln": 2.7055},
            },
            "evidence": [
                {"to_ref": "trial-1", "ref_type": "trial",
                 "relation": "tested_by"}
            ],
        })
        assert "error" not in r
        assert abs(r["confidence"] - 0.6237) < 0.001
        assert r["confidence_basis"] == "grounded"
        assert (
            r["confidence_computation"]["procedure"]
            == "posterior_from_2lnbf"
        )
        got = await call_tool(
            mcp_server, "get_claim", {"claim_id": r["claim_id"]}
        )
        assert got["claim"]["confidence_computation"]["inputs"][
            "bf_2ln"] == 2.7055

    async def test_computation_without_evidence_refused(
            self, mcp_server):
        """A posterior floating free of the graph is not grounded."""
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "floating posterior",
            "type": "empirical",
            "confidence_computation": {
                "procedure": "posterior_from_2lnbf",
                "inputs": {"prior": 0.3, "bf_2ln": 2.7055},
            },
        })
        assert "error" in r
        assert "evidence" in r["error"]

    async def test_unregistered_procedure_refused(self, mcp_server):
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "invented procedure",
            "type": "empirical",
            "confidence_computation": {
                "procedure": "my_magic_confidence",
                "inputs": {"prior": 0.3, "bf_2ln": 8.0},
            },
            "evidence": [
                {"to_ref": "t", "ref_type": "trial",
                 "relation": "tested_by"}
            ],
        })
        assert "error" in r and "my_magic_confidence" in r["error"]

    async def test_computation_missing_inputs_refused(self, mcp_server):
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "half a computation",
            "type": "empirical",
            "confidence_computation": {
                "procedure": "posterior_from_2lnbf",
                "inputs": {"prior": 0.3},
            },
            "evidence": [
                {"to_ref": "t", "ref_type": "trial",
                 "relation": "tested_by"}
            ],
        })
        assert "error" in r and "bf_2ln" in r["error"]

    async def test_evidence_without_computation_is_null(
            self, mcp_server):
        """Evidence-bearing edges without a derivation mint
        weakly_grounded — confidence stays NULL, not a policy
        constant."""
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "evidence but no likelihood",
            "type": "empirical",
            "evidence": [
                {"to_ref": "trial-1", "ref_type": "trial",
                 "relation": "tested_by"}
            ],
        })
        assert "error" not in r
        assert r["confidence"] is None
        assert r["confidence_basis"] == "weakly_grounded"

    async def test_structural_edge_is_not_evidence(self, mcp_server):
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "only similar_to edge",
            "type": "empirical",
            "evidence": [
                {"to_ref": "x", "ref_type": "external", "relation": "similar_to"}
            ],
        })
        assert "error" not in r
        assert r["confidence"] is None
        assert r["confidence_basis"] == "ungrounded"

    async def test_dedup_cannot_launder_computation(self, mcp_server):
        """Re-asserting existing content cannot upgrade a NULL claim
        to a grounded number — mint-time fields are mint-time-only."""
        cid = await call_tool(mcp_server, "assert_claim", {
            "content": "bare claim", "type": "empirical",
        })
        assert cid["confidence"] is None
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "bare claim", "type": "empirical",
            "confidence_computation": {
                "procedure": "posterior_from_2lnbf",
                "inputs": {"prior": 0.3, "bf_2ln": 2.7055},
            },
        })
        assert r.get("deduplicated") is True
        assert r["confidence"] is None  # existing row's NULL persists

    async def test_bad_claim_type_rejected(self, mcp_server):
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "bad type", "type": "vibes",
        })
        assert "error" in r and "empirical" in r["error"] and "methodological" in r["error"]

    async def test_bad_evidence_relation_rejected(self, mcp_server):
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "bad relation", "type": "empirical",
            "evidence": [
                {"to_ref": "x", "ref_type": "trial", "relation": "vibes"}
            ],
        })
        assert "error" in r and "relation" in r["error"]

    async def test_bad_evidence_ref_type_rejected(self, mcp_server):
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "bad ref_type", "type": "empirical",
            "evidence": [
                {"to_ref": "x", "ref_type": "vibes", "relation": "tested_by"}
            ],
        })
        assert "error" in r and "ref_type" in r["error"]

    async def test_claim_typed_evidence_ref_must_exist(self, mcp_server):
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "dangling claim ref", "type": "empirical",
            "evidence": [
                {"to_ref": "claim-nope", "ref_type": "claim",
                 "relation": "supports"}
            ],
        })
        assert "error" in r and "not found" in r["error"]

    async def test_external_ref_trusted(self, mcp_server):
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "external evidence", "type": "empirical",
            "evidence": [
                {"to_ref": "doi:10.1/x", "ref_type": "external",
                 "relation": "supports"}
            ],
        })
        assert "error" not in r

    async def test_supersedes_must_exist(self, mcp_server):
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "supersedes ghost", "type": "empirical",
            "supersedes_id": "claim-nope",
        })
        assert "error" in r and "not found" in r["error"]

    async def test_supersedes_writes_column_and_edge(self, mcp_server):
        """Review note: column + edge are one fact, written atomically."""
        old = await make_claim(mcp_server, "old claim")
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "new claim", "type": "empirical",
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
        })
        assert r.get("deduplicated") is True
        assert r["claim_id"] == cid

    async def test_dedup_is_a_lookup(self, mcp_server):
        """Re-asserting existing content is a lookup, not a new claim —
        edges still attach, the row's mint-time fields are untouched."""
        cid = await make_claim(mcp_server, "already here")
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "already here", "type": "empirical",
        })
        assert r.get("deduplicated") is True and r["claim_id"] == cid
        assert r["confidence_basis"] == "grounded"  # minted grounded

    async def test_json_string_evidence_accepted(self, mcp_server):
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "json string evidence", "type": "empirical",
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
            "content": "replacement", "type": "empirical",
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
