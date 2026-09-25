"""Tests for the agora observability GUI — the read-only topology
and integrity views, including /integrity/help. In-process via
Starlette's TestClient — the app is read-only over the adaptors
container and the audit-log dir.
"""

from __future__ import annotations

import pytest

from ml_agora_mcp.integrity.checks import run_and_log
from ml_agora_mcp.observability.server import create_observability_app


@pytest.fixture
def gui_client(adaptors, tmp_path):
    from starlette.testclient import TestClient

    app = create_observability_app(
        adaptors, log_dir=tmp_path / "logs"
    )
    return TestClient(app)


def test_overview_page(gui_client):
    """The bird's-eye page: one card per server + the action list."""
    r = gui_client.get("/")
    assert r.status_code == 200
    assert "Lab overview" in r.text
    for name in ("Claims", "Loop 0", "Loop 1", "Loop 2"):
        assert name in r.text
    assert "Lab next actions" in r.text


def test_overview_degrades_on_down_channel(adaptors, tmp_path):
    from starlette.testclient import TestClient

    adaptors.loop1 = None
    adaptors._channels["loop1"].mark_down("refused")
    app = create_observability_app(
        adaptors, log_dir=tmp_path / "logs"
    )
    r = TestClient(app).get("/")
    assert r.status_code == 200
    assert "refused" in r.text
    assert "unreachable" in r.text


def test_nav_panel_lists_plugins(gui_client):
    """The left sidebar menu is generated from views.PLUGINS."""
    r = gui_client.get("/")
    assert 'class="sidebar"' in r.text
    for label in ("Overview", "Graph", "Topology", "Integrity"):
        assert label in r.text


def test_topology_page(gui_client):
    r = gui_client.get("/topology")
    assert r.status_code == 200
    for name in ("claims", "loop0", "loop1", "loop2"):
        assert name in r.text
    assert "claims://status" in r.text
    assert "protocol://status" in r.text


def test_topology_shows_down_channel(adaptors, tmp_path):
    from starlette.testclient import TestClient

    adaptors.loop1 = None
    adaptors._channels["loop1"].mark_down("refused")
    app = create_observability_app(
        adaptors, log_dir=tmp_path / "logs"
    )
    r = TestClient(app).get("/topology")
    assert r.status_code == 200
    assert "refused" in r.text


def test_health_json(gui_client):
    r = gui_client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_health_pill_fragment(gui_client):
    r = gui_client.get("/health", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "health-pill ok" in r.text


def test_integrity_empty_state(gui_client):
    r = gui_client.get("/integrity")
    assert r.status_code == 200
    assert "No integrity checks" in r.text
    # The help link is visible precisely when the page is empty.
    assert "/integrity/help" in r.text


async def test_integrity_renders_logged_run(adaptors, tmp_path):
    from starlette.testclient import TestClient

    await run_and_log(
        adaptors,
        connectivity=adaptors.connectivity_report(),
        log_dir=tmp_path / "logs",
        trigger="tool",
    )
    app = create_observability_app(
        adaptors, log_dir=tmp_path / "logs"
    )
    client = TestClient(app)
    r = client.get("/integrity")
    assert r.status_code == 200
    assert "upstream_connectivity" in r.text
    assert "status_reachability" in r.text


def test_integrity_help(gui_client):
    r = gui_client.get("/integrity/help")
    assert r.status_code == 200
    assert "upstream_connectivity" in r.text
    assert "status_reachability" in r.text
    # Explains the verdicts and the report-only contract.
    assert "report-only" in r.text or "report only" in r.text
    assert "skipped" in r.text


def test_unknown_route_404(gui_client):
    r = gui_client.get("/nope")
    assert r.status_code == 404


# --- Entity graph plugin ---


def _graph_payload(server: str, loop: str, nodes: list, edges: list):
    return {
        "server": server,
        "loop": loop,
        "generated_at": "2026-09-19T14:00:00+00:00",
        "truncated": False,
        "nodes": nodes,
        "edges": edges,
    }


def test_graph_data_merges_channels(adaptors, tmp_path):
    """The merged graph stitches all four loops, dedupes shared ids,
    and stubs cross-server edge endpoints."""
    from starlette.testclient import TestClient

    payloads = {
        "claims://graph": _graph_payload(
            "ml-anamnesis-mcp", "claims",
            [{"id": "claim-1", "kind": "claim", "label": "c1",
              "status": "active", "gui_path": "/claim/claim-1"}],
            [{"src": "mdec-1", "dst": "claim-1", "kind": "mints"}],
        ),
        "protocol://graph": _graph_payload(
            "ml-episteme-mcp", "loop0",
            [{"id": "prog-1", "kind": "programme", "label": "p1",
              "status": "archived", "gui_path": "/programme/prog-1"}],
            [{"src": "camp-1", "dst": "prog-1", "kind": "spawn"}],
        ),
        "search://graph": _graph_payload(
            "ml-zetesis-mcp", "loop1",
            [{"id": "camp-1", "kind": "campaign", "label": "c",
              "status": "closed", "gui_path": "/campaign/camp-1"}],
            [{"src": "camp-1", "dst": "prog-1", "kind": "spawn:x"}],
        ),
        "improver://graph": _graph_payload(
            "ml-arete-mcp", "loop2",
            [{"id": "mdec-1", "kind": "meta_decision", "label": "d",
              "status": "hold"}],
            [{"src": "mdec-1", "dst": "claim-1", "kind": "mints"}],
        ),
    }
    # Rewire the fakes to serve graph payloads.
    chans = {"claims": "claims://graph", "loop0": "protocol://graph",
             "loop1": "search://graph", "loop2": "improver://graph"}
    for name, uri in chans.items():
        getattr(adaptors, name).payloads = {uri: payloads[uri]}

    app = create_observability_app(adaptors, log_dir=tmp_path / "logs")
    r = TestClient(app).get("/graph/data")
    assert r.status_code == 200
    data = r.json()
    ids = {n["id"] for n in data["nodes"]}
    # Real nodes deduped across servers, stubs created for dangling
    # endpoints — the whole provenance chain resolves.
    assert {"claim-1", "prog-1", "camp-1", "mdec-1"} <= ids
    assert all(s == "ok" for s in data["sources"].values())
    prog = next(n for n in data["nodes"] if n["id"] == "prog-1")
    assert prog["level"] == 1
    assert prog["gui_url"].endswith("/programme/prog-1")
    # Edges only reference nodes that exist.
    for e in data["edges"]:
        assert e["src"] in ids and e["dst"] in ids


def test_graph_data_degrades_when_channel_down(adaptors, tmp_path):
    from starlette.testclient import TestClient

    adaptors.loop1 = None
    adaptors._channels["loop1"].mark_down("refused")
    app = create_observability_app(adaptors, log_dir=tmp_path / "logs")
    r = TestClient(app).get("/graph/data")
    assert r.status_code == 200
    assert r.json()["sources"]["loop1"] == "unreachable"


def test_graph_page_loads(gui_client):
    r = gui_client.get("/graph")
    assert r.status_code == 200
    assert "pixi.min.js" in r.text
    assert "graph-canvas" in r.text


def test_static_pixi_served(gui_client):
    r = gui_client.get("/static/pixi.min.js")
    assert r.status_code == 200
    assert "pixi" in r.text.lower()
