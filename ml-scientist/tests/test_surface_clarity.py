"""Surface-clarity audit — the probe as a regression test.

Adopts the live-stack probe (scripts/mcp_probe.py) into the suite,
run in-process against all five servers. The property under test is
surface legibility: how learnable the MCP surface is for an agent
that has never seen it — the same property W1–W4 established and
the warmup protocol depends on.

Assertions are deliberately conservative — they fail on definite
regressions only:

- any UNCLEAR probe verdict (unreadable resource, ambiguous
  rejection, unguarded mutation, leaked internal error)
- an error delivered inside a *successful* payload (error-as-result —
  the hazard W1 eliminated)
- lint findings that W1–W4 drove to zero: undocumented params,
  closed vocabs as bare strings, undocumented object|string unions

Informational only (collected, not asserted): "output is
unstructured" — that's W5, deferred per the tool-surface plan.
"""

import tempfile
from pathlib import Path

import pytest

from surface_probe import probe_client, worst_verdict

SENTINEL_LINT_MARKERS = (
    "has no description",
    "undiscoverable",
    "object shape undocumented",
    "no tool description at all",
    "described only in prose",
)


@pytest.fixture
def episteme_store(tmp_path):
    from ml_episteme_mcp.state.store import StateStore

    store = StateStore(str(tmp_path / "state.db"))
    store.connect()
    yield store
    store.close()


@pytest.fixture
def memory_store(tmp_path):
    from ml_anamnesis_mcp.state.store import MemoryStore

    store = MemoryStore(str(tmp_path / "memory.db"))
    store.connect()
    yield store
    store.close()


@pytest.fixture
def improver_store(tmp_path):
    from ml_arete_mcp.state.store import ImproverStore

    store = ImproverStore(str(tmp_path / "improver.db"))
    store.connect()
    yield store
    store.close()


@pytest.fixture
def search_store(tmp_path):
    from ml_zetesis_mcp.state.store import SearchStore

    store = SearchStore(str(tmp_path / "search.db"))
    store.connect()
    yield store
    store.close()


def _agora_client(tmp_path):
    from mcp.client import Client
    from ml_agora_mcp.clients.adaptors import Adaptors
    from ml_agora_mcp.server import create_server as agora_server

    return Client(agora_server(
        Adaptors(), log_dir=tmp_path / "agora-logs"))


def _episteme_client(episteme_store):
    from mcp.client import Client
    from ml_episteme_mcp.server import create_server

    return Client(create_server(episteme_store))


def _anamnesis_client(memory_store):
    from mcp.client import Client
    from ml_anamnesis_mcp.server import create_server

    return Client(create_server(memory_store))


def _arete_client(improver_store):
    from mcp.client import Client
    from ml_arete_mcp.server import create_server

    return Client(create_server(improver_store))


def _zetesis_client(search_store):
    from mcp.client import Client
    from ml_zetesis_mcp.server import create_server

    return Client(create_server(search_store))


def _assert_clean_surface(report: dict) -> None:
    """The conservative invariants — definite regressions only."""
    assert not report["unreachable"], report.get("error")

    for r in report["resources"]:
        assert r["status"] == "CLEAR", (
            f"resource {r['uri']} unreadable: {r.get('error')}")

    for t in report["tools"]:
        name = t["tool"]
        for p in t["probes"]:
            assert p["verdict"] != "UNCLEAR", (
                f"{name} [{p['kind']}]: {p['note']}")
            note = p.get("note", "")
            assert "error-as-result" not in note, (
                f"{name} [{p['kind']}] smuggled an error inside a "
                f"success payload: {note}")
            # W5a invariant: a successful read must carry
            # structured_content equal to its parsed text payload.
            if p["kind"] == "live_read" and p["verdict"] == "CLEAR":
                assert p.get("structured") is True, (
                    f"{name} [live_read] returned JSON text without "
                    "structured_content — W5a regression")
        for issue in t["lint"]:
            for marker in SENTINEL_LINT_MARKERS:
                assert marker not in issue, f"{name}: {issue}"


class TestSurfaceClarity:
    """One audit per server — the probe's full battery in-process."""

    @pytest.mark.asyncio
    async def test_episteme_surface(self, episteme_store):
        async with _episteme_client(episteme_store) as client:
            report = await probe_client(client, "ml-episteme")
        # Floor, not exact count: removal is a breaking surface
        # change (a tool an agent learned vanishing); growth is
        # audited by the per-tool invariants automatically.
        assert len(report["tools"]) >= 40
        _assert_clean_surface(report)

    @pytest.mark.asyncio
    async def test_zetesis_surface(self, search_store):
        async with _zetesis_client(search_store) as client:
            report = await probe_client(client, "ml-zetesis")
        assert len(report["tools"]) >= 23
        _assert_clean_surface(report)

    @pytest.mark.asyncio
    async def test_arete_surface(self, improver_store):
        async with _arete_client(improver_store) as client:
            report = await probe_client(client, "ml-arete")
        assert len(report["tools"]) >= 27
        _assert_clean_surface(report)

    @pytest.mark.asyncio
    async def test_anamnesis_surface(self, memory_store):
        async with _anamnesis_client(memory_store) as client:
            report = await probe_client(client, "ml-anamnesis")
        assert len(report["tools"]) >= 5
        _assert_clean_surface(report)

    @pytest.mark.asyncio
    async def test_agora_surface(self, tmp_path):
        async with _agora_client(tmp_path) as client:
            report = await probe_client(client, "ml-agora")
        _assert_clean_surface(report)

    @pytest.mark.asyncio
    async def test_structured_content_parity(self, episteme_store,
                                             search_store, improver_store,
                                             memory_store, tmp_path):
        """The core W5a invariant: structured_content on the wire is
        the parsed text payload — identical by construction via ok()."""
        import json as _json

        async def check(client, tool, args):
            async with client as c:
                r = await c.call_tool(tool, args)
            assert not r.is_error, f"{tool}: {r.content[0].text[:100]}"
            text_payload = _json.loads(r.content[0].text)
            assert r.structured_content == text_payload, (
                f"{tool}: structured_content diverges from text")

        await check(_episteme_client(episteme_store),
                    "list_active_programmes", {})
        await check(_zetesis_client(search_store),
                    "list_investigations", {})
        await check(_arete_client(improver_store),
                    "list_decisions", {})
        await check(_anamnesis_client(memory_store),
                    "list_claims", {})
        await check(_agora_client(tmp_path),
                    "check_invariants", {})

    @pytest.mark.asyncio
    async def test_undeclared_schema_ratchet(self, episteme_store,
                                             search_store, improver_store,
                                             memory_store, tmp_path):
        """W5b ratchet: tools lacking a declared output_schema. Post-
        W5a every tool emits structured_content; declared schemas land
        per-tool starting with the loop hot path. Each count must only
        shrink — a new tool adds itself to the debt unless it declares
        a schema."""
        async def undeclared_count(client, name):
            async with client as c:
                report = await probe_client(c, name)
            return len([
                t["tool"] for t in report["tools"]
                if any("no declared output_schema" in i for i in t["lint"])
            ])

        # W5b complete — every tool declares an output schema. The
        # ratchet is now zero: a new tool without a declared schema
        # fails here.
        assert await undeclared_count(
            _episteme_client(episteme_store), "ml-episteme") == 0
        assert await undeclared_count(
            _zetesis_client(search_store), "ml-zetesis") == 0
        assert await undeclared_count(
            _arete_client(improver_store), "ml-arete") == 0
        assert await undeclared_count(
            _anamnesis_client(memory_store), "ml-anamnesis") == 0
        assert await undeclared_count(
            _agora_client(tmp_path), "ml-agora") == 0
