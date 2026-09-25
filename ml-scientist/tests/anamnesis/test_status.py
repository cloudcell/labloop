"""Tests for the claims-role status surfaces — claims://status, the
status key embedded in claims://session, and the status_report
prompt.

Anamnesis is a role-filler, not a loop (ADR-0004): its digest must
report capability posture honestly — no workflow position, no
invented open work.
"""

from __future__ import annotations

import json

import pytest

from ml_anamnesis_mcp.resources.status import status_digest


class TestDigestShape:
    def test_emits_full_contract_in_capability_posture(
        self, mem_store
    ):
        d = status_digest(mem_store)
        for key in (
            "server", "role", "generated_at", "workflow_position",
            "open_work", "blockers", "recommended_next",
            "upstream_summary", "integrity_summary",
        ):
            assert key in d, f"missing contract key {key}"
        assert d["server"] == "ml-anamnesis-mcp"
        assert d["role"] == "claims"

    def test_no_fabricated_workflow(self, mem_store):
        """A capability server has no workflow of its own — null is
        the honest answer, and open_work stays empty."""
        d = status_digest(mem_store)
        assert d["workflow_position"] is None
        assert d["open_work"] == []
        assert d["blockers"] == []

    def test_recommendation_defers_to_consumers(self, mem_store):
        d = status_digest(mem_store)
        rec = d["recommended_next"][0]
        assert rec["action"] == "serve_consumers"
        assert rec["tool"] is None
        assert "consuming loops" in rec["reason"]

    def test_upstream_summary_is_explicit_skip(self, mem_store):
        d = status_digest(mem_store)
        assert d["upstream_summary"]["verdict"] == "skipped"
        assert d["upstream_summary"]["configured"] == 0

    def test_claim_counts_reported(self, mem_store):
        d = status_digest(mem_store)
        assert d["claims"]["total"] == 0
        assert d["claims"]["superseded"] == 0
        assert d["claims"]["expired"] == 0


class TestSurfaces:
    async def test_status_resource(self, mcp_server):
        from mcp.client import Client

        async with Client(mcp_server) as client:
            result = await client.read_resource("claims://status")
            d = json.loads(result.contents[0].text)
            assert d["server"] == "ml-anamnesis-mcp"
            assert d["role"] == "claims"
            assert d["workflow_position"] is None

    async def test_session_embeds_status(self, mcp_server):
        from mcp.client import Client

        async with Client(mcp_server) as client:
            result = await client.read_resource("claims://session")
            session = json.loads(result.contents[0].text)
            assert "status" in session
            assert session["status"]["role"] == "claims"

    async def test_status_report_prompt(self, mcp_server):
        from mcp.client import Client

        async with Client(mcp_server) as client:
            result = await client.get_prompt("status_report", {})
            text = result.messages[0].content.text
            assert "status" in text.lower()
            # The capability posture must come through — the prompt
            # must not pretend anamnesis has a loop.
            assert (
                "capabilit" in text.lower()
                or "consumer" in text.lower()
            )
