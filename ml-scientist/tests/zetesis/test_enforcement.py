"""Enforcement tests — the integrity rules of the search loop."""

from __future__ import annotations

from .conftest import call_tool, open_inv


async def test_pull_rejects_nonexistent_investigation(zetesis_server):
    r = await call_tool(zetesis_server, "pull_evidence", {
        "investigation_id": "inv-nope",
        "source": "loop0", "tool": "list_trials",
    })
    assert "error" in r
    assert "not found" in r["error"].lower()


async def test_pull_rejects_bad_source(zetesis_server):
    inv_id = await open_inv(zetesis_server)
    r = await call_tool(zetesis_server, "pull_evidence", {
        "investigation_id": inv_id, "source": "evil", "tool": "list_trials",
    })
    assert "error" in r
    assert "source" in r["error"]


async def test_pull_whitelist_rejects_write_tools(zetesis_server):
    """The evidence channel is read-only — run_trial et al. are refused
    before any upstream call is made."""
    inv_id = await open_inv(zetesis_server)
    for tool in ("run_trial", "create_programme", "record_observation",
                 "record_promotion_decision"):
        r = await call_tool(zetesis_server, "pull_evidence", {
            "investigation_id": inv_id, "source": "loop0", "tool": tool,
        })
        assert "error" in r, f"{tool} should be rejected"
        assert "Input should be" in r["error"]


async def test_pull_whitelist_rejects_anamnesis_writes(zetesis_server):
    inv_id = await open_inv(zetesis_server)
    r = await call_tool(zetesis_server, "pull_evidence", {
        "investigation_id": inv_id, "source": "anamnesis",
        "tool": "assert_claim",
    })
    assert "error" in r
    assert "Input should be" in r["error"]


async def test_pull_rejects_absent_adaptor(search_store, zetesis_server):
    """Unset a channel → pulls fail clearly, nothing is logged."""
    from ml_zetesis_mcp.clients.adaptors import Adaptors
    from ml_zetesis_mcp.server import create_server

    bare = Adaptors()  # no channels wired
    mcp = create_server(search_store, adaptors=bare)
    inv_id = await open_inv(mcp)
    r = await call_tool(mcp, "pull_evidence", {
        "investigation_id": inv_id, "source": "loop0",
        "tool": "list_trials",
    })
    assert "error" in r
    assert "adaptor" in r["error"].lower()
    # nothing was logged for the failed pull
    g = await call_tool(mcp, "get_investigation",
                        {"investigation_id": inv_id})
    assert g["evidence_refs"] == []


async def test_finding_confidence_gate(zetesis_server):
    inv_id = await open_inv(zetesis_server)
    r = await call_tool(zetesis_server, "record_finding", {
        "investigation_id": inv_id, "content": "ungrounded",
        "confidence": 0.8,
    })
    assert "error" in r
    assert "prior ceiling" in r["error"]

    # same confidence WITH an evidence ref is allowed
    pull = await call_tool(zetesis_server, "pull_evidence", {
        "investigation_id": inv_id, "source": "loop0",
        "tool": "list_active_programmes",
    })
    r = await call_tool(zetesis_server, "record_finding", {
        "investigation_id": inv_id, "content": "grounded",
        "confidence": 0.8,
        "evidence_ref_ids": [pull["evidence_ref_id"]],
    })
    assert "error" not in r


async def test_finding_confidence_gate_configurable(
    search_store, adaptors
):
    """[integrity] prior_confidence_max governs the write-time gate."""
    from ml_zetesis_mcp.server import create_server

    mcp = create_server(
        search_store, adaptors=adaptors,
        integrity_config={"prior_confidence_max": 0.5},
    )
    inv_id = await open_inv(mcp)
    r = await call_tool(mcp, "record_finding", {
        "investigation_id": inv_id, "content": "mid-confidence",
        "confidence": 0.4,
    })
    assert "error" not in r  # 0.4 < configured 0.5 → allowed
    r = await call_tool(mcp, "record_finding", {
        "investigation_id": inv_id, "content": "too confident",
        "confidence": 0.6,
    })
    assert "prior ceiling" in r["error"]


async def test_finding_below_ceiling_needs_no_evidence(zetesis_server):
    inv_id = await open_inv(zetesis_server)
    r = await call_tool(zetesis_server, "record_finding", {
        "investigation_id": inv_id, "content": "hunch",
        "confidence": 0.3,
    })
    assert "error" not in r


async def test_finding_rejects_foreign_evidence_ref(zetesis_server):
    """A ref from investigation A cannot ground a finding in B."""
    inv_a = await open_inv(zetesis_server)
    inv_b = await open_inv(zetesis_server)
    pull = await call_tool(zetesis_server, "pull_evidence", {
        "investigation_id": inv_a, "source": "loop0",
        "tool": "list_active_programmes",
    })
    r = await call_tool(zetesis_server, "record_finding", {
        "investigation_id": inv_b, "content": "smuggled",
        "confidence": 0.7,
        "evidence_ref_ids": [pull["evidence_ref_id"]],
    })
    assert "error" in r
    assert "belongs to" in r["error"]


async def test_closed_investigation_rejects_pulls_and_findings(
    zetesis_server,
):
    inv_id = await open_inv(zetesis_server)
    r = await call_tool(zetesis_server, "conclude_investigation", {
        "investigation_id": inv_id, "verdict": "null_result",
        "summary": "nothing",
    })
    assert "error" not in r

    for tool, args in (
        ("pull_evidence", {"source": "loop0",
                           "tool": "list_active_programmes"}),
        ("record_finding", {"content": "x", "confidence": 0.1}),
    ):
        args["investigation_id"] = inv_id
        r = await call_tool(zetesis_server, tool, args)
        assert "error" in r
        assert "concluded" in r["error"]


async def test_conclude_findings_requires_provisional(zetesis_server):
    inv_id = await open_inv(zetesis_server)
    r = await call_tool(zetesis_server, "conclude_investigation", {
        "investigation_id": inv_id, "verdict": "findings",
        "summary": "s",
    })
    assert "error" in r
    assert "provisional" in r["error"]


async def test_conclude_rejects_bad_verdict(zetesis_server):
    inv_id = await open_inv(zetesis_server)
    r = await call_tool(zetesis_server, "conclude_investigation", {
        "investigation_id": inv_id, "verdict": "accepted",
        "summary": "s",
    })
    assert "error" in r
    assert "verdict" in r["error"]


async def test_double_conclude_rejected(zetesis_server):
    inv_id = await open_inv(zetesis_server)
    args = {"investigation_id": inv_id, "verdict": "null_result",
            "summary": "done"}
    r = await call_tool(zetesis_server, "conclude_investigation", args)
    assert "error" not in r
    r = await call_tool(zetesis_server, "conclude_investigation", args)
    assert "error" in r


async def test_open_investigation_validates(zetesis_server):
    r = await call_tool(zetesis_server, "open_investigation", {
        "question": "   ", "scope": {},
    })
    assert "error" in r
    r = await call_tool(zetesis_server, "open_investigation", {
        "question": "q", "scope": "not-a-dict",
    })
    assert "error" in r


async def test_list_investigations_validates_status(zetesis_server):
    r = await call_tool(zetesis_server, "list_investigations",
                        {"status": "bogus"})
    assert "error" in r
