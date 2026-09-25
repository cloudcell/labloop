"""Tests for lab://status aggregation and ranking — the cross-server
contract: every configured server is reported honestly (ok /
unreachable / not_configured / error), never silently omitted, and
lab_next_actions ranks blockers > in-flight > stale > new work with
lower loops winning ties.
"""

from __future__ import annotations

import json

import pytest

from ml_agora_mcp.clients.adaptors import Adaptors
from ml_agora_mcp.resources.status import lab_status

from .conftest import FakeReadAdaptor, _digest


async def test_all_up_aggregates_four_digests(adaptors):
    agg = await lab_status(adaptors)
    assert agg["server"] == "ml-agora-mcp"
    assert agg["role"] == "status"
    assert agg["servers_reachable"] == 4
    assert agg["servers_configured"] == 4
    for name in ("claims", "loop0", "loop1", "loop2"):
        assert agg["servers"][name]["status"] == "ok"
        assert "digest" in agg["servers"][name]


async def test_unconfigured_channel_reported_not_omitted():
    """Absent means absent — an unwired server shows
    not_configured, it does not vanish from the aggregate."""
    a = Adaptors()  # nothing registered
    agg = await lab_status(a)
    assert agg["servers_configured"] == 0
    assert agg["servers_reachable"] == 0
    for name in ("claims", "loop0", "loop1", "loop2"):
        assert agg["servers"][name]["status"] == "not_configured"


async def test_down_channel_reports_unreachable(adaptors):
    """A configured-but-down channel is honest: unreachable with the
    last error, and a restore_channel action ranks first."""
    adaptors.loop1 = None
    adaptors._channels["loop1"].mark_down("connection refused")
    agg = await lab_status(adaptors)
    assert agg["servers"]["loop1"]["status"] == "unreachable"
    assert "refused" in agg["servers"]["loop1"]["detail"]
    top = agg["lab_next_actions"][0]
    assert top["action"] == "restore_channel"


async def test_read_error_reports_error(adaptors):
    adaptors._channels["claims"].adaptor.fail = True
    adaptors.claims.fail = True
    agg = await lab_status(adaptors)
    assert agg["servers"]["claims"]["status"] == "error"


async def test_ranking_blockers_before_in_flight(adaptors):
    """Blockers outrank in-flight work regardless of loop order."""
    adaptors.claims.digest = _digest(
        "ml-anamnesis-mcp", "claims",
        recommended_next=[{
            "rank": 1, "action": "serve_consumers", "tool": None,
            "entity_refs": [], "reason": "capability posture",
        }],
    )
    adaptors.loop2.digest = _digest(
        "ml-arete-mcp", "loop2",
        blockers=[{
            "kind": "upstream_down", "channel": "loop0-read",
            "role": "loop0-read", "blocks": ["pull_evidence"],
            "detail": "down",
        }],
    )
    adaptors.loop0.digest = _digest(
        "ml-episteme-mcp", "loop0",
        recommended_next=[{
            "rank": 1, "action": "run_trial", "tool": "run_trial",
            "entity_refs": ["trial-1"], "reason": "designed trial",
        }],
    )
    agg = await lab_status(adaptors)
    actions = agg["lab_next_actions"]
    assert actions[0]["action"] == "restore_channel"
    assert actions[1]["action"] == "run_trial"


async def test_ranking_in_flight_before_new_work(adaptors):
    adaptors.loop0.digest = _digest(
        "ml-episteme-mcp", "loop0",
        recommended_next=[{
            "rank": 1, "action": "create_programme",
            "tool": "create_programme", "entity_refs": [],
            "reason": "no open work",
        }],
    )
    adaptors.loop1.digest = _digest(
        "ml-zetesis-mcp", "loop1",
        recommended_next=[{
            "rank": 1, "action": "conclude_investigation",
            "tool": "conclude_investigation",
            "entity_refs": ["inv-1"], "reason": "findings ready",
        }],
    )
    agg = await lab_status(adaptors)
    actions = agg["lab_next_actions"]
    assert actions[0]["action"] == "conclude_investigation"
    assert actions[1]["action"] == "create_programme"


async def test_non_actions_filtered(adaptors):
    """anamnesis's serve_consumers is capability narration — never a
    lab task."""
    agg = await lab_status(adaptors)
    for a in agg["lab_next_actions"]:
        assert a["action"] != "serve_consumers"


async def test_stale_tier_between_in_flight_and_new(adaptors):
    adaptors.loop0.digest = _digest(
        "ml-episteme-mcp", "loop0",
        recommended_next=[
            {"rank": 1, "action": "create_programme",
             "tool": "create_programme", "entity_refs": [],
             "reason": "idle"},
            {"rank": 2, "action": "review_stale_programme",
             "tool": None, "entity_refs": ["prog-9"],
             "reason": "stale"},
            {"rank": 3, "action": "run_trial",
             "tool": "run_trial", "entity_refs": ["trial-2"],
             "reason": "designed"},
        ],
    )
    agg = await lab_status(adaptors)
    order = [a["action"] for a in agg["lab_next_actions"]]
    assert order.index("run_trial") < order.index(
        "review_stale_programme"
    ) < order.index("create_programme")


class TestSurfaces:
    async def test_lab_status_resource(self, agora_server):
        from mcp.client import Client

        async with Client(agora_server) as client:
            result = await client.read_resource("lab://status")
            agg = json.loads(result.contents[0].text)
            assert agg["server"] == "ml-agora-mcp"
            assert agg["role"] == "status"
            assert "lab_next_actions" in agg
            assert "servers" in agg

    async def test_lab_topology_resource(self, agora_server):
        from mcp.client import Client

        async with Client(agora_server) as client:
            result = await client.read_resource("lab://topology")
            topo = json.loads(result.contents[0].text)
            assert topo["server"] == "ml-agora-mcp"
            channels = {c["channel"]: c for c in topo["channels"]}
            assert channels["loop0"]["status_uri"] == "protocol://status"
            assert channels["claims"]["status_uri"] == "claims://status"
            assert channels["loop0"]["configured"] is True
            assert channels["loop0"]["state"] == "up"

    async def test_status_report_prompt(self, agora_server):
        from mcp.client import Client

        async with Client(agora_server) as client:
            result = await client.get_prompt("status_report", {})
            text = result.messages[0].content.text
            assert "Lab status report" in text
            assert "ml-agora-mcp" in text
            assert "advisory" in text
            # Per-server posture rendered.
            assert "loop0" in text

    async def test_check_invariants_tool(self, agora_server):
        from mcp.client import Client

        async with Client(agora_server) as client:
            result = await client.call_tool("check_invariants", {})
            payload = json.loads(result.content[0].text)
            assert payload["status"] == "ok"
            names = {c["name"] for c in payload["checks"]}
            assert names == {
                "upstream_connectivity", "status_reachability",
            }

    async def test_agora_exposes_no_downstream_tools(self, agora_server):
        """The hub surface stays minimal — one tool, not sixty."""
        from mcp.client import Client

        async with Client(agora_server) as client:
            tools = await client.list_tools()
            names = [t.name for t in tools.tools]
            assert names == ["check_invariants"]
