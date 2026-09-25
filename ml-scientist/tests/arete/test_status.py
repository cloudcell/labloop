"""Tests for the Loop-2 status surfaces — improver://status, the
status key embedded in improver://session, and the status_report
prompt. Same cross-server contract as the other products.
"""

from __future__ import annotations

import json

import pytest

from ml_arete_mcp.resources.status import status_digest
from ml_arete_mcp.state.models import (
    ImproverVersion,
    MetaChangeProposal,
    MetaContract,
    ProposalStatus,
    Tournament,
)


def _imp(i: str, champion=False) -> ImproverVersion:
    return ImproverVersion(
        id=i, code_artifact_digest="sha256:x", model_ref="m",
        capability_profile={}, is_champion=champion,
    )


def _proposal(
    pid: str, proposer: str,
    status: ProposalStatus = ProposalStatus.admitted,
) -> MetaChangeProposal:
    return MetaChangeProposal(
        id=pid, proposer_improver_id=proposer,
        spec_delta={"a": 1}, class_map={"planner": "modifiable"},
        expected_benefit="b", falsification="f",
        rollback_plan="r", status=status,
    )


def _tournament(tid: str, parent: str, cand: str) -> Tournament:
    return Tournament(
        id=tid, contract_id="contract-1",
        parent_improver_id=parent, candidate_improver_id=cand,
        budget={},
    )


class TestDigestShape:
    def test_empty_store_emits_full_contract(self, improver_store):
        d = status_digest(improver_store)
        for key in (
            "server", "role", "generated_at", "workflow_position",
            "open_work", "blockers", "recommended_next",
            "upstream_summary", "integrity_summary",
        ):
            assert key in d, f"missing contract key {key}"
        assert d["server"] == "ml-arete-mcp"
        assert d["role"] == "loop2"

    def test_no_champion_recommends_genesis(self, improver_store):
        d = status_digest(improver_store)
        tools = [r["tool"] for r in d["recommended_next"]]
        assert "register_improver" in tools


class TestRecommendations:
    def test_admitted_proposal_recommends_tournament(
        self, improver_store
    ):
        improver_store.create_improver(_imp("imp-a", champion=True))
        improver_store.create_improver(_imp("imp-b"))
        improver_store.create_proposal(_proposal("prop-1", "imp-b"))
        d = status_digest(improver_store)
        tools = [r["tool"] for r in d["recommended_next"]]
        assert "open_tournament" in tools

    def test_open_tournament_recommends_result(self, improver_store):
        improver_store.create_improver(_imp("imp-a", champion=True))
        improver_store.create_improver(_imp("imp-b"))
        improver_store.create_meta_contract(
            MetaContract(
                id="contract-1", version=1,
                metrics={"primary_metric": "hits"},
                promotion_policy={},
            )
        )
        improver_store.create_tournament(
            _tournament("tour-1", "imp-a", "imp-b")
        )
        d = status_digest(improver_store)
        tools = [r["tool"] for r in d["recommended_next"]]
        assert "record_tournament_result" in tools


class TestSurfaces:
    async def test_status_resource(self, arete_server):
        from mcp.client import Client

        async with Client(arete_server) as client:
            result = await client.read_resource("improver://status")
            d = json.loads(result.contents[0].text)
            assert d["server"] == "ml-arete-mcp"
            assert d["role"] == "loop2"

    async def test_session_embeds_status(self, arete_server):
        from mcp.client import Client

        async with Client(arete_server) as client:
            result = await client.read_resource("improver://session")
            session = json.loads(result.contents[0].text)
            assert "status" in session
            assert session["status"]["role"] == "loop2"

    async def test_status_report_prompt(self, arete_server):
        from mcp.client import Client

        async with Client(arete_server) as client:
            result = await client.get_prompt("status_report", {})
            text = result.messages[0].content.text
            assert "status" in text.lower()
