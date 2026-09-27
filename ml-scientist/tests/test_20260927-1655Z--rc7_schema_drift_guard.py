"""rc-7 Q7 — required-parameter drift guard.

The diagnostic saw published tool schemas that dropped required
params (capture_bundle's env_ref/seeds/splits, acknowledge_violation's
disposition/decided_by). Our TOOL_CATALOG already lists them — this
guard pins the invariant end-to-end: for every tool on every server,
a signature parameter with no default must appear in the published
inputSchema.required, and vice versa.
"""

from __future__ import annotations

import inspect

import pytest


def _required_signature_params(fn) -> set[str]:
    sig = inspect.signature(fn)
    return {
        name
        for name, p in sig.parameters.items()
        if p.default is inspect.Parameter.empty
        and p.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY)
    }


def _servers(tmp_path):
    """One in-process instance per server — fresh stores."""
    from ml_agora_mcp.clients.adaptors import Adaptors as AgoraAdaptors
    from ml_agora_mcp.server import create_server as agora_server
    from ml_anamnesis_mcp.server import create_server as ana_server
    from ml_anamnesis_mcp.state.store import MemoryStore
    from ml_arete_mcp.clients.adaptors import Adaptors as AreteAdaptors
    from ml_arete_mcp.server import create_server as arete_server
    from ml_arete_mcp.state.store import ImproverStore
    from ml_episteme_mcp.server import create_server as epi_server
    from ml_episteme_mcp.state.store import StateStore
    from ml_zetesis_mcp.clients.adaptors import Adaptors as ZetAdaptors
    from ml_zetesis_mcp.server import create_server as zet_server
    from ml_zetesis_mcp.state.store import SearchStore

    epi = StateStore(str(tmp_path / "e.db")); epi.connect()
    zet = SearchStore(str(tmp_path / "z.db")); zet.connect()
    are = ImproverStore(str(tmp_path / "a.db")); are.connect()
    ana = MemoryStore(str(tmp_path / "n.db")); ana.connect()
    return {
        "episteme": epi_server(
            epi, enforcement_config={"recurrent_protocol": False}
        ),
        "zetesis": zet_server(zet, adaptors=ZetAdaptors()),
        "arete": arete_server(are, adaptors=AreteAdaptors()),
        "anamnesis": ana_server(ana),
        "agora": agora_server(AgoraAdaptors(), log_dir=tmp_path),
    }


async def test_required_params_match_input_schema(tmp_path):
    for name, mcp in _servers(tmp_path).items():
        tools = await mcp.list_tools()
        by_name = {t.name: t for t in tools}
        for tname, tool in by_name.items():
            schema_required = set(
                tool.input_schema.get("required") or []
            )
            fn = mcp._tool_manager._tools[tname].fn
            sig_required = _required_signature_params(fn)
            # Both directions: a param required in the signature must
            # be required on the wire; a param required on the wire
            # must be required in the signature (no phantom requires).
            assert schema_required == sig_required, (
                f"{name}.{tname}: schema required "
                f"{sorted(schema_required)} != signature required "
                f"{sorted(sig_required)}"
            )


async def test_capture_bundle_required_params(tmp_path):
    """The originally-reported case: capture_bundle must advertise
    env_ref, seeds, and splits as required."""
    from ml_episteme_mcp.server import create_server
    from ml_episteme_mcp.state.store import StateStore

    s = StateStore(str(tmp_path / "e.db"))
    s.connect()
    mcp = create_server(
        s, enforcement_config={"recurrent_protocol": False}
    )
    tools = await mcp.list_tools()
    cb = next(t for t in tools if t.name == "capture_bundle")
    required = set(cb.input_schema.get("required") or [])
    assert {"trial_id", "code_ref", "env_ref", "seeds",
            "splits"} <= required


async def test_acknowledge_violation_required_params(tmp_path):
    """And acknowledge_violation must advertise disposition and
    decided_by on every server that serves it."""
    from ml_agora_mcp.clients.adaptors import Adaptors as AgoraAdaptors
    from ml_agora_mcp.server import create_server as agora_server
    from ml_anamnesis_mcp.server import create_server as ana_server
    from ml_anamnesis_mcp.state.store import MemoryStore
    from ml_arete_mcp.clients.adaptors import Adaptors as AreteAdaptors
    from ml_arete_mcp.server import create_server as arete_server
    from ml_arete_mcp.state.store import ImproverStore
    from ml_episteme_mcp.server import create_server as epi_server
    from ml_episteme_mcp.state.store import StateStore
    from ml_zetesis_mcp.clients.adaptors import Adaptors as ZetAdaptors
    from ml_zetesis_mcp.server import create_server as zet_server
    from ml_zetesis_mcp.state.store import SearchStore

    epi = StateStore(str(tmp_path / "e.db")); epi.connect()
    zet = SearchStore(str(tmp_path / "z.db")); zet.connect()
    are = ImproverStore(str(tmp_path / "a.db")); are.connect()
    ana = MemoryStore(str(tmp_path / "n.db")); ana.connect()
    for name, mcp in {
        "episteme": epi_server(
            epi, enforcement_config={"recurrent_protocol": False}
        ),
        "zetesis": zet_server(zet, adaptors=ZetAdaptors()),
        "arete": arete_server(are, adaptors=AreteAdaptors()),
        "anamnesis": ana_server(ana),
        "agora": agora_server(AgoraAdaptors(), log_dir=tmp_path),
    }.items():
        tools = {t.name: t for t in await mcp.list_tools()}
        ack = tools.get("acknowledge_violation")
        if ack is None:
            continue
        required = set(ack.input_schema.get("required") or [])
        assert {"disposition", "decided_by"} <= required, (
            f"{name}.acknowledge_violation required: {required}"
        )
