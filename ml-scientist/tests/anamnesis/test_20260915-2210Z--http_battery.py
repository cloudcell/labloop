"""anamnesis over real MCP streamable-HTTP transport.

Mirrors the ml-episteme battery: a real subprocess server on an
isolated port with a temp memory.db, exercised through the MCP client.
"""

from __future__ import annotations

import json
import urllib.request

import pytest

from .conftest import call_tool_http


async def assert_claim_http(url, content, **overrides):
    args = {
        "content": content,
        "type": "empirical",
        "confidence": 0.8,
        "evidence": [
            {"to_ref": "trial-seed", "ref_type": "trial",
             "relation": "tested_by"}
        ],
    }
    args.update(overrides)
    r = await call_tool_http(url, "assert_claim", args)
    assert "error" not in r, f"assert_claim failed: {r}"
    return r["claim_id"]


class TestAnamnesisHTTP:
    def test_health_endpoint(self, anamnesis_url):
        with urllib.request.urlopen(f"{anamnesis_url}/health") as resp:
            assert json.loads(resp.read()) == {"status": "ok"}

    def test_health_deep_and_integrity_gui(
        self, anamnesis_url, anamnesis_gui_url
    ):
        """/health/deep runs the audit over real HTTP (always 200); the
        GUI renders the persisted log."""
        with urllib.request.urlopen(
            f"{anamnesis_url}/health/deep", timeout=10
        ) as resp:
            assert resp.status == 200
            body = json.loads(resp.read())
            assert body["status"] in ("ok", "violations")
            assert len(body["checks"]) == 4
        with urllib.request.urlopen(
            f"{anamnesis_gui_url}/integrity", timeout=5
        ) as resp:
            assert resp.status == 200
            # The test db sits directly in /tmp — all test servers share
            # /tmp/logs/, so the newest run may be another server's.
            # Assert the history renders, not a specific check name.
            assert "check-" in resp.read().decode()

    @pytest.mark.asyncio
    async def test_full_claim_flow(self, anamnesis_url):
        """assert → relate → get (provenance) → list — the Phase-1 exit."""
        cid = await assert_claim_http(
            anamnesis_url,
            "Tokenizer-ID hashing outperforms learned discretization",
            evidence=[{
                "to_ref": "trial-dcmha-1",
                "ref_type": "trial",
                "relation": "tested_by",
            }],
        )
        assert cid.startswith("claim-")

        edge = await call_tool_http(anamnesis_url, "relate", {
            "from_claim": cid, "to_ref": "conc-dcmha",
            "ref_type": "conclusion", "relation": "derived_from",
        })
        assert edge["edge_id"].startswith("edge-")

        got = await call_tool_http(anamnesis_url, "get_claim", {"claim_id": cid})
        assert got["claim"]["content"].startswith("Tokenizer-ID")
        rels = {e["relation"] for e in got["outgoing_edges"]}
        assert rels == {"tested_by", "derived_from"}

        listed = await call_tool_http(anamnesis_url, "list_claims", {})
        assert cid in {c["id"] for c in listed["claims"]}

    @pytest.mark.asyncio
    async def test_evidence_rule_over_http(self, anamnesis_url):
        r = await call_tool_http(anamnesis_url, "assert_claim", {
            "content": "unsupported over http",
            "type": "empirical",
            "confidence": 0.95,
        })
        assert "prior ceiling" in r["error"]

    @pytest.mark.asyncio
    async def test_json_string_evidence_over_http(self, anamnesis_url):
        r = await call_tool_http(anamnesis_url, "assert_claim", {
            "content": "json-string evidence over http",
            "type": "empirical",
            "confidence": 0.8,
            "evidence": '[{"to_ref": "trial-x", "ref_type": "trial",'
                        ' "relation": "tested_by"}]',
        })
        assert "error" not in r, f"json-string evidence failed: {r}"

    @pytest.mark.asyncio
    async def test_supersede_lifecycle_over_http(self, anamnesis_url):
        old = await assert_claim_http(anamnesis_url, "old belief http")
        new = await assert_claim_http(
            anamnesis_url, "new belief http", supersedes_id=old,
            evidence=[{
                "to_ref": "trial-y", "ref_type": "trial",
                "relation": "tested_by",
            }],
        )
        got = await call_tool_http(anamnesis_url, "get_claim",
                                   {"claim_id": new})
        assert got["claim"]["supersedes_id"] == old
        rels = {e["relation"] for e in got["outgoing_edges"]}
        assert "supersedes" in rels

        live = await call_tool_http(anamnesis_url, "list_claims", {})
        ids = {c["id"] for c in live["claims"]}
        assert new in ids and old not in ids
