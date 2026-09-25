"""Tool-level lifecycle tests — the full loop with fake adaptors."""

from __future__ import annotations

import json

from .conftest import (
    FakeClaimsAdaptor,
    FakeUpstreamAdaptor,
    call_tool,
    open_inv,
)


async def test_full_lifecycle_mints_claims(zetesis_server, adaptors):
    """open → pull → finding → conclude: the claim lands in anamnesis
    with derived_from edges to the consulted Loop-0 entities."""
    inv_id = await open_inv(zetesis_server)

    pull = await call_tool(zetesis_server, "pull_evidence", {
        "investigation_id": inv_id, "source": "loop0",
        "tool": "list_trials",
        "args": {"programme_id": "prog-aaaa1111"},
    })
    assert "error" not in pull
    assert pull["evidence_ref_id"].startswith("eref-")

    find = await call_tool(zetesis_server, "record_finding", {
        "investigation_id": inv_id,
        "content": "strategy X beats Y under budget B",
        "confidence": 0.7,
        "evidence_ref_ids": [pull["evidence_ref_id"]],
    })
    assert "error" not in find

    conc = await call_tool(zetesis_server, "conclude_investigation", {
        "investigation_id": inv_id, "verdict": "findings",
        "summary": "X wins",
        "implications": {"propose_programme": "try X at scale"},
    })
    assert "error" not in conc
    assert conc["claim_status"] == "minted"
    assert len(conc["claim_ids"]) == 1

    minted = adaptors.claims.minted
    assert len(minted) == 1
    assert minted[0]["type"] == "methodological"
    assert minted[0]["confidence"] == 0.7
    assert minted[0]["source_id"] == inv_id


async def test_pull_extracts_entity_ids(zetesis_server, adaptors):
    adaptors.evidence.payloads["list_trials"] = json.dumps({
        "trials": [{"id": "trial-cccc3333"}, {"id": "trial-dddd4444"}],
    })
    inv_id = await open_inv(zetesis_server)
    pull = await call_tool(zetesis_server, "pull_evidence", {
        "investigation_id": inv_id, "source": "loop0",
        "tool": "list_trials", "args": {},
    })
    assert set(pull["ref_ids"]) == {"trial-cccc3333", "trial-dddd4444"}
    assert pull["result"]["trials"][0]["id"] == "trial-cccc3333"


async def test_pull_logs_evidence_ref(zetesis_server):
    inv_id = await open_inv(zetesis_server)
    pull = await call_tool(zetesis_server, "pull_evidence", {
        "investigation_id": inv_id, "source": "anamnesis",
        "tool": "list_claims", "args": {"type": "methodological"},
    })
    g = await call_tool(zetesis_server, "get_investigation",
                        {"investigation_id": inv_id})
    refs = g["evidence_refs"]
    assert len(refs) == 1
    assert refs[0]["id"] == pull["evidence_ref_id"]
    assert refs[0]["source"] == "anamnesis"
    assert refs[0]["tool"] == "list_claims"
    assert refs[0]["args"] == {"type": "methodological"}


async def test_pull_survives_non_json_payload(zetesis_server, adaptors):
    """A non-JSON upstream response still gets its evidence_ref — the
    provenance record is not erased by a malformed payload."""
    adaptors.evidence.payloads["get_archive"] = "NOT JSON {{{"
    inv_id = await open_inv(zetesis_server)
    pull = await call_tool(zetesis_server, "pull_evidence", {
        "investigation_id": inv_id, "source": "loop0",
        "tool": "get_archive", "args": {"archive_id": "a1"},
    })
    assert "error" not in pull
    assert pull["result"] == "NOT JSON {{{"
    g = await call_tool(zetesis_server, "get_investigation",
                        {"investigation_id": inv_id})
    assert len(g["evidence_refs"]) == 1


async def test_conclusion_mints_derived_from_edges(
    zetesis_server, adaptors,
):
    adaptors.evidence.payloads["assess_programme"] = json.dumps({
        "programme_id": "prog-aaaa1111",
        "conclusions": [{"id": "conc-ffff6666"}],
        "observations": [{"id": "obs-aaaa7777"}],
        "trials": [{"id": "trial-cccc3333"}],
        "notes": [{"id": "weird-thing-9"}],  # unknown prefix → not a ref
    })
    inv_id = await open_inv(zetesis_server)
    pull = await call_tool(zetesis_server, "pull_evidence", {
        "investigation_id": inv_id, "source": "loop0",
        "tool": "assess_programme",
        "args": {"programme_id": "prog-aaaa1111"},
    })
    await call_tool(zetesis_server, "record_finding", {
        "investigation_id": inv_id, "content": "f", "confidence": 0.6,
        "evidence_ref_ids": [pull["evidence_ref_id"]],
    })
    conc = await call_tool(zetesis_server, "conclude_investigation", {
        "investigation_id": inv_id, "verdict": "findings",
        "summary": "s",
    })
    assert conc["claim_status"] == "minted"

    edges = adaptors.claims.edges
    by_ref = {e["to_ref"]: e for e in edges}
    assert by_ref["conc-ffff6666"]["ref_type"] == "conclusion"
    assert by_ref["obs-aaaa7777"]["ref_type"] == "observation"
    assert by_ref["trial-cccc3333"]["ref_type"] == "trial"
    assert by_ref["prog-aaaa1111"]["ref_type"] == "programme"
    assert all(e["relation"] == "derived_from" for e in edges)


async def test_null_result_mints_nothing(zetesis_server, adaptors):
    inv_id = await open_inv(zetesis_server)
    pull = await call_tool(zetesis_server, "pull_evidence", {
        "investigation_id": inv_id, "source": "loop0",
        "tool": "list_active_programmes",
    })
    # a provisional finding exists but the verdict is honest
    await call_tool(zetesis_server, "record_finding", {
        "investigation_id": inv_id, "content": "weak signal",
        "confidence": 0.4,
        "evidence_ref_ids": [pull["evidence_ref_id"]],
    })
    conc = await call_tool(zetesis_server, "conclude_investigation", {
        "investigation_id": inv_id, "verdict": "null_result",
        "summary": "no systematic pattern found",
    })
    assert "error" not in conc
    assert conc["claim_status"] == "skipped"
    assert conc["claim_ids"] == []
    assert adaptors.claims.minted == []
    # finding stays provisional — recorded, not asserted
    g = await call_tool(zetesis_server, "get_investigation",
                        {"investigation_id": inv_id})
    assert g["findings"][0]["status"] == "provisional"
    assert g["findings"][0]["claim_id"] is None


async def test_conclude_without_claims_adaptor_reports_disabled(
    search_store,
):
    """No claims channel → the investigation still concludes; the
    absence is reported, never hidden."""
    from ml_zetesis_mcp.clients.adaptors import Adaptors
    from ml_zetesis_mcp.server import create_server

    a = Adaptors()
    a.evidence = FakeUpstreamAdaptor()
    mcp = create_server(search_store, adaptors=a)

    inv_id = await open_inv(mcp)
    pull = await call_tool(mcp, "pull_evidence", {
        "investigation_id": inv_id, "source": "loop0",
        "tool": "list_active_programmes",
    })
    await call_tool(mcp, "record_finding", {
        "investigation_id": inv_id, "content": "f", "confidence": 0.5,
        "evidence_ref_ids": [pull["evidence_ref_id"]],
    })
    conc = await call_tool(mcp, "conclude_investigation", {
        "investigation_id": inv_id, "verdict": "findings",
        "summary": "s",
    })
    assert "error" not in conc
    assert conc["claim_status"] == "disabled"
    assert conc["claim_ids"] == []


async def test_mint_failure_reports_failed(search_store):
    """A claims channel that throws → claim_status 'failed'; the
    investigation still concludes honestly."""

    class BrokenClaims(FakeClaimsAdaptor):
        async def assert_claim(self, *a, **kw):
            raise RuntimeError("anamnesis down")

    from ml_zetesis_mcp.clients.adaptors import Adaptors
    from ml_zetesis_mcp.server import create_server

    a = Adaptors()
    a.evidence = FakeUpstreamAdaptor()
    a.claims = BrokenClaims()
    mcp = create_server(search_store, adaptors=a)

    inv_id = await open_inv(mcp)
    pull = await call_tool(mcp, "pull_evidence", {
        "investigation_id": inv_id, "source": "loop0",
        "tool": "list_active_programmes",
    })
    await call_tool(mcp, "record_finding", {
        "investigation_id": inv_id, "content": "f", "confidence": 0.5,
        "evidence_ref_ids": [pull["evidence_ref_id"]],
    })
    conc = await call_tool(mcp, "conclude_investigation", {
        "investigation_id": inv_id, "verdict": "findings",
        "summary": "s",
    })
    assert conc["claim_status"] == "failed"


async def test_drop_finding(zetesis_server, adaptors):
    inv_id = await open_inv(zetesis_server)
    find = await call_tool(zetesis_server, "record_finding", {
        "investigation_id": inv_id, "content": "maybe", "confidence": 0.2,
    })
    r = await call_tool(zetesis_server, "drop_finding",
                        {"finding_id": find["finding_id"]})
    assert r["status"] == "dropped"
    # a dropped finding is not minted
    conc = await call_tool(zetesis_server, "conclude_investigation", {
        "investigation_id": inv_id, "verdict": "null_result",
        "summary": "s",
    })
    assert "error" not in conc


async def test_drop_asserted_finding_rejected(zetesis_server, adaptors):
    inv_id = await open_inv(zetesis_server)
    find = await call_tool(zetesis_server, "record_finding", {
        "investigation_id": inv_id, "content": "f", "confidence": 0.2,
    })
    await call_tool(zetesis_server, "conclude_investigation", {
        "investigation_id": inv_id, "verdict": "findings",
        "summary": "s",
    })
    r = await call_tool(zetesis_server, "drop_finding",
                        {"finding_id": find["finding_id"]})
    assert "error" in r


async def test_abandon_investigation(zetesis_server):
    inv_id = await open_inv(zetesis_server)
    r = await call_tool(zetesis_server, "abandon_investigation",
                        {"investigation_id": inv_id})
    assert r["status"] == "abandoned"
    g = await call_tool(zetesis_server, "get_investigation",
                        {"investigation_id": inv_id})
    assert g["investigation"]["status"] == "abandoned"


async def test_get_investigation_not_found(zetesis_server):
    r = await call_tool(zetesis_server, "get_investigation",
                        {"investigation_id": "inv-nope"})
    assert "error" in r


async def test_list_investigations(zetesis_server):
    await open_inv(zetesis_server)
    r = await call_tool(zetesis_server, "list_investigations", {})
    assert r["total"] == 1
    assert r["investigations"][0]["status"] == "open"


async def test_args_accept_json_strings(zetesis_server):
    """MCP clients sometimes send JSON-encoded strings for object
    params — coerce_json handles them."""
    r = await call_tool(zetesis_server, "open_investigation", {
        "question": "q",
        "scope": json.dumps({"programme_ids": ["prog-1"]}),
        "budget": json.dumps({"max_pulls": 10}),
    })
    assert "error" not in r


async def test_channel_disabled_late_is_visible(zetesis_server, adaptors):
    """The mutable-container contract: nulling a channel after
    registration disables pulls live."""
    inv_id = await open_inv(zetesis_server)
    adaptors.evidence = None  # late failure
    r = await call_tool(zetesis_server, "pull_evidence", {
        "investigation_id": inv_id, "source": "loop0",
        "tool": "list_active_programmes",
    })
    assert "error" in r
    assert "adaptor" in r["error"].lower()
