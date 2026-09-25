"""zetesis over real MCP streamable-HTTP transport — a real subprocess
server backed by real stub upstreams (evidence + claims roles)."""

from __future__ import annotations

import urllib.request

import pytest

from .conftest import call_tool_http


async def _open(url, **overrides):
    args = {
        "question": "which strategy produced better outcomes?",
        "scope": {"programme_ids": ["prog-aaaa1111"]},
    }
    args.update(overrides)
    r = await call_tool_http(url, "open_investigation", args)
    assert "error" not in r, f"open_investigation failed: {r}"
    return r["investigation_id"]


async def test_health_endpoint(zetesis_http_server):
    url, _gui, _ev, _cl = zetesis_http_server
    with urllib.request.urlopen(f"{url}/health", timeout=5) as resp:
        assert resp.status == 200
        import json
        body = json.loads(resp.read())
        assert body["status"] == "ok"
        assert body["upstream"]["evidence"] is True
        assert body["upstream"]["claims"] is True


async def test_health_deep_and_integrity_gui(zetesis_http_server):
    """/health/deep runs the audit over real HTTP (always 200); the GUI
    renders the persisted log."""
    url, gui, _ev, _cl = zetesis_http_server
    import json
    with urllib.request.urlopen(f"{url}/health/deep", timeout=10) as resp:
        assert resp.status == 200
        body = json.loads(resp.read())
        assert body["status"] in ("ok", "violations")
        assert len(body["checks"]) == 11
        names = {c["name"] for c in body["checks"]}
        assert "minted_claims_resolve" in names
    with urllib.request.urlopen(f"{gui}/integrity", timeout=5) as resp:
        assert resp.status == 200
        # Test DBs sit directly in /tmp — all test servers share
        # /tmp/logs/, so the newest run may be another server's. Assert
        # the history renders, not a specific check name.
        assert "check-" in resp.read().decode()


async def test_session_resource(zetesis_http_server):
    from .conftest import _root_conftest
    url, _gui, _ev, _cl = zetesis_http_server
    payload = await _root_conftest.read_resource_http(
        url, "search://session"
    )
    assert payload["server"] == "ml-zetesis-mcp"
    assert payload["loop"] == 1
    assert any(s["tool"] == "pull_evidence" for s in payload["steps"])


async def test_full_lifecycle_over_http(zetesis_http_server):
    """The whole loop over real MCP: pull reaches the stub upstream,
    conclusion mints into the stub claims server."""
    url, _gui, _ev, _cl = zetesis_http_server
    inv_id = await _open(url)

    pull = await call_tool_http(url, "pull_evidence", {
        "investigation_id": inv_id, "source": "loop0",
        "tool": "list_trials", "args": {"programme_id": "prog-aaaa1111"},
    })
    assert "error" not in pull
    assert "trial-cccc3333" in pull["ref_ids"]
    assert pull["result"]["trials"][0]["status"] == "completed"

    find = await call_tool_http(url, "record_finding", {
        "investigation_id": inv_id,
        "content": "strategy X correlates with completion",
        "confidence": 0.6,
        "evidence_ref_ids": [pull["evidence_ref_id"]],
    })
    assert "error" not in find

    conc = await call_tool_http(url, "conclude_investigation", {
        "investigation_id": inv_id, "verdict": "findings",
        "summary": "X wins", "implications": {"next": "replicate"},
    })
    assert "error" not in conc
    assert conc["claim_status"] == "minted"
    assert len(conc["claim_ids"]) == 1
    assert conc["edges_created"] >= 1

    # the claim actually landed in the claims stub
    claims = await call_tool_http(_cl, "list_claims", {})
    minted = [c for c in claims["claims"]
              if c.get("source_id") == inv_id]
    assert len(minted) == 1
    assert minted[0]["type"] == "methodological"


async def test_whitelist_rejection_over_http(zetesis_http_server):
    url, _gui, _ev, _cl = zetesis_http_server
    inv_id = await _open(url)
    r = await call_tool_http(url, "pull_evidence", {
        "investigation_id": inv_id, "source": "loop0",
        "tool": "run_trial",
    })
    assert "error" in r
    assert "Input should be" in r["error"]


async def test_prompts_registered(zetesis_http_server):
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    url, _gui, _ev, _cl = zetesis_http_server
    async with streamable_http_client(f"{url}/mcp") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_prompts()
            names = {p.name for p in result.prompts}
            assert {"conduct_investigation",
                    "scope_investigation"} <= names


async def test_tools_registered(zetesis_http_server):
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    url, _gui, _ev, _cl = zetesis_http_server
    async with streamable_http_client(f"{url}/mcp") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            names = {t.name for t in result.tools}
            assert {
                "open_investigation", "pull_evidence",
                "record_finding", "drop_finding",
                "conclude_investigation", "abandon_investigation",
                "get_investigation", "list_investigations",
            } <= names
