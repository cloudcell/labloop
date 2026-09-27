"""Tests for the Loop-1 status surfaces — search://status, the
status key embedded in search://session, and the status_report
prompt. Same cross-server contract as the other products.
"""

from __future__ import annotations

import json

import pytest

from ml_zetesis_mcp.resources.status import status_digest
from ml_zetesis_mcp.state.models import (
    EvidenceRef,
    EvidenceSource,
    Finding,
    Investigation,
)


def _inv(inv_id: str) -> Investigation:
    return Investigation(id=inv_id, question="q?", scope={})


def _eref(ref_id: str, inv_id: str) -> EvidenceRef:
    return EvidenceRef(
        id=ref_id, investigation_id=inv_id,
        source=EvidenceSource.loop0, tool="list_trials",
        args={}, ref_ids=["trial-x"],
    )


def _finding(fid: str, inv_id: str) -> Finding:
    return Finding(
        id=fid, investigation_id=inv_id,
        content="the evidence shows X", confidence=0.6,
    )


class TestDigestShape:
    def test_empty_store_emits_full_contract(self, search_store):
        d = status_digest(search_store)
        for key in (
            "server", "role", "generated_at", "workflow_position",
            "open_work", "blockers", "recommended_next",
            "upstream_summary", "integrity_summary",
        ):
            assert key in d, f"missing contract key {key}"
        assert d["server"] == "ml-zetesis-mcp"
        assert d["role"] == "loop1"
        assert d["recommended_next"][0]["tool"] == "open_investigation"


class TestRecommendations:
    def test_fresh_investigation_recommends_pull(self, search_store):
        search_store.create_investigation(_inv("inv-1"))
        d = status_digest(search_store)
        tools = [r["tool"] for r in d["recommended_next"]]
        assert "pull_evidence" in tools

    def test_evidence_without_finding_recommends_record(
        self, search_store
    ):
        search_store.create_investigation(_inv("inv-1"))
        search_store.create_evidence_ref(_eref("ref-1", "inv-1"))
        d = status_digest(search_store)
        tools = [r["tool"] for r in d["recommended_next"]]
        assert "record_finding" in tools

    def test_findings_recommend_conclude(self, search_store):
        search_store.create_investigation(_inv("inv-1"))
        search_store.create_evidence_ref(_eref("ref-1", "inv-1"))
        search_store.create_finding(_finding("f-1", "inv-1"))
        d = status_digest(search_store)
        tools = [r["tool"] for r in d["recommended_next"]]
        assert "conclude_investigation" in tools


class TestSurfaces:
    async def test_status_resource(self, zetesis_server):
        from mcp.client import Client

        async with Client(zetesis_server) as client:
            result = await client.read_resource("search://status")
            d = json.loads(result.contents[0].text)
            assert d["server"] == "ml-zetesis-mcp"
            assert d["role"] == "loop1"

    async def test_session_embeds_status(self, zetesis_server):
        from mcp.client import Client

        async with Client(zetesis_server) as client:
            result = await client.read_resource("search://session")
            session = json.loads(result.contents[0].text)
            assert "status" in session
            assert session["status"]["role"] == "loop1"
            for key in (
                "workflow_position", "open_work",
                "recommended_next", "integrity_summary",
            ):
                assert key in session["status"]

    async def test_status_report_prompt(self, zetesis_server):
        from mcp.client import Client

        async with Client(zetesis_server) as client:
            result = await client.get_prompt("status_report", {})
            text = result.messages[0].content.text
            assert "status" in text.lower()
