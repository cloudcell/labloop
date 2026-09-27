"""rc-7 regressions — arete.

Q2  record_meta_decision surfaces claim_error on mint failure and
    persists it; disabled/minted paths carry claim_error=None.
Q6  read_resource reaches improver:// resources (classes, status) and
    errors clearly on unknown URIs.
Q8  improver://classes carries the ADR-0003 class mapping.
Q10 pull_evidence's tool enum equals the union of the per-source
    whitelists — no over- or under-advertising.
"""

from __future__ import annotations

import json

import pytest

from .conftest import call_tool, make_contract, propose, register_imp


async def _tourn_with_eref(mcp, store, eref_id="eref-x1"):
    """Open a tournament for a registered candidate and seed an
    evidence ref in its context; return (candidate_id, tourn, eref)."""
    from ml_arete_mcp.state.models import EvidenceRef

    parent = await register_imp(mcp)
    candidate = await register_imp(mcp, parent_id=parent)
    contract = await make_contract(mcp)
    p = await propose(mcp, candidate)
    assert "error" not in p, p
    t = await call_tool(mcp, "open_tournament", {
        "contract_id": contract,
        "parent_improver_id": parent,
        "candidate_improver_id": candidate,
        "budget": {"descendant_runs": 2},
    })
    tourn = t["tournament_id"]
    store.create_evidence_ref(EvidenceRef(
        id=eref_id, context_type="tournament", context_id=tourn,
        source="loop0", tool="list_trials", args={},
        ref_ids=["trial-a1"],
    ))
    return candidate, tourn, eref_id


def _decision_args(candidate, tourn, eref):
    return {
        "candidate_improver_id": candidate,
        "verdict": "hold",
        "evidence_refs": [eref],
        "rationale": "test decision",
        "decided_by": "human:pi",
        "tournament_id": tourn,
    }


class TestClaimError:
    """rc-7 Q2 — a failed mint must say why, not just 'failed'."""

    async def test_failed_mint_surfaces_and_persists_claim_error(
        self, arete_server, adaptors, improver_store
    ):
        class ExplodingClaims:
            async def assert_claim(self, **kw):
                raise RuntimeError("anamnesis refused: type='bogus'")

        adaptors.claims = ExplodingClaims()
        cand, tourn, eref = await _tourn_with_eref(
            arete_server, improver_store
        )
        dec = await call_tool(
            arete_server, "record_meta_decision",
            _decision_args(cand, tourn, eref),
        )
        assert "error" not in dec, dec
        assert dec["claim_status"] == "failed"
        assert dec["claim_id"] is None
        assert "anamnesis refused" in dec["claim_error"]
        # Persisted — the failure reason is durable, not just returned.
        d = improver_store.get_meta_decision(dec["decision_id"])
        assert "anamnesis refused" in d.claim_error

    async def test_disabled_claims_has_no_error(
        self, arete_server, adaptors, improver_store
    ):
        adaptors.claims = None
        cand, tourn, eref = await _tourn_with_eref(
            arete_server, improver_store
        )
        dec = await call_tool(
            arete_server, "record_meta_decision",
            _decision_args(cand, tourn, eref),
        )
        assert "error" not in dec, dec
        assert dec["claim_status"] == "disabled"
        assert dec["claim_error"] is None

    async def test_minted_claim_has_no_error(
        self, arete_server, adaptors, improver_store
    ):
        cand, tourn, eref = await _tourn_with_eref(
            arete_server, improver_store
        )
        dec = await call_tool(
            arete_server, "record_meta_decision",
            _decision_args(cand, tourn, eref),
        )
        assert "error" not in dec, dec
        assert dec["claim_status"] == "minted"
        assert dec["claim_error"] is None
        # And it surfaces in the list/get projection.
        listed = await call_tool(arete_server, "list_decisions", {})
        row = next(
            d for d in listed["decisions"]
            if d["id"] == dec["decision_id"]
        )
        assert row["claim_error"] is None


class TestReadResource:
    """rc-7 Q6 — the tool surface can read resources."""

    async def test_reads_improver_classes(self, arete_server):
        r = await call_tool(arete_server, "read_resource", {
            "uri": "improver://classes",
        })
        assert "error" not in r, r
        body = json.loads(r["contents"][0]["content"])
        assert body["server"] == "ml-arete-mcp"
        assert "classes" in body

    async def test_reads_improver_status(self, arete_server):
        r = await call_tool(arete_server, "read_resource", {
            "uri": "improver://status",
        })
        assert "error" not in r, r
        body = json.loads(r["contents"][0]["content"])
        assert body["server"] == "ml-arete-mcp"

    async def test_unknown_uri_errors(self, arete_server):
        r = await call_tool(arete_server, "read_resource", {
            "uri": "improver://nonexistent",
        })
        assert "error" in r

    async def test_foreign_scheme_errors(self, arete_server):
        """Ownership: arete cannot read another loop's resources."""
        r = await call_tool(arete_server, "read_resource", {
            "uri": "protocol://status",
        })
        assert "error" in r


class TestClassesVocabulary:
    """rc-7 Q8 — improver://classes names the ADR-0003 ordinals."""

    async def test_classes_resource_carries_adr_map(self, arete_server):
        r = await call_tool(arete_server, "read_resource", {
            "uri": "improver://classes",
        })
        body = json.loads(r["contents"][0]["content"])
        m = body["adr_class_map"]
        assert m["immutable"].startswith("first class")
        assert m["modifiable"].startswith("second class")
        assert m["conditional"].startswith("third class")


class TestPullEvidenceWhitelist:
    """rc-7 Q10 — the tool enum is exactly the whitelist union."""

    async def test_enum_matches_whitelist_union(self, arete_server):
        from ml_arete_mcp.enforcement.checks import EVIDENCE_READ_TOOLS

        tools = await arete_server.list_tools()
        pull = next(t for t in tools if t.name == "pull_evidence")
        enum = set(
            pull.input_schema["properties"]["tool"]["enum"]
        )
        union = set().union(*EVIDENCE_READ_TOOLS.values())
        assert enum == union

    async def test_per_source_refusal_names_whitelist(
        self, arete_server
    ):
        """list_trials is valid for loop0 but not anamnesis — the
        refusal must name the source's own whitelist."""
        from ml_arete_mcp.enforcement.checks import (
            check_tool_whitelisted,
        )

        assert check_tool_whitelisted("loop0", "list_trials") is None
        err = check_tool_whitelisted("anamnesis", "list_trials")
        assert err and "whitelist" in err
        assert "get_claim" in err
