"""Unit tests for the four X://graph resources — the read-only
entity projections the agora hub stitches into the cross-server
graph. Each store is seeded minimally and projected via
``build_graph``.
"""

from __future__ import annotations


def _episteme_store(tmp_path):
    from ml_episteme_mcp.state.store import StateStore

    s = StateStore(str(tmp_path / "ep.db"))
    s.connect()
    return s


def test_episteme_graph_projects_programme(tmp_path):
    from ml_episteme_mcp.resources.graph import build_graph
    from ml_episteme_mcp.state.models import Programme

    store = _episteme_store(tmp_path)
    store.create_programme(Programme(
        id="prog-t1", goal="g", constraints={}, allowed_variables=[],
        budget_max_trials=5, budget_max_wall_time_hours=1.0,
    ))
    g = build_graph(store)
    node = next(n for n in g["nodes"] if n["id"] == "prog-t1")
    assert node["kind"] == "programme"
    assert node["gui_path"] == "/programme/prog-t1"
    assert g["loop"] == "loop0"


def test_zetesis_graph_projects_campaign_spawn(tmp_path):
    from ml_zetesis_mcp.resources.graph import build_graph
    from ml_zetesis_mcp.state.models import (
        CampaignArm, CampaignSpawn, PromotionCampaign,
    )
    from ml_zetesis_mcp.state.store import SearchStore

    store = SearchStore(str(tmp_path / "z.db"))
    store.connect()
    store.create_campaign(PromotionCampaign(
        id="camp-t1", contract_id="c1", champion_id="cand-ch",
        challenger_id="cand-cl", primary_metric="val_perplexity",
    ))
    store.create_campaign_spawn(CampaignSpawn(
        id="sp-t1", campaign_id="camp-t1", arm=CampaignArm.challenger,
        programme_id="prog-t9", budget={"trials_per_programme": 2},
        metric_direction="minimize",
    ))
    g = build_graph(store)
    ids = {n["id"] for n in g["nodes"]}
    assert {"camp-t1", "cand-ch", "cand-cl", "prog-t9"} <= ids
    kinds = {(e["src"], e["dst"], e["kind"]) for e in g["edges"]}
    assert ("camp-t1", "cand-ch", "arm:champion") in kinds
    assert ("camp-t1", "prog-t9", "spawn:challenger") in kinds


def test_arete_graph_projects_lineage(tmp_path):
    from ml_arete_mcp.resources.graph import build_graph
    from ml_arete_mcp.state.models import ImproverVersion
    from ml_arete_mcp.state.store import ImproverStore

    store = ImproverStore(str(tmp_path / "a.db"))
    store.connect()
    store.create_improver(ImproverVersion(
        id="imp-parent", code_artifact_digest="d1",
        model_ref="m", is_champion=True,
    ))
    store.create_improver(ImproverVersion(
        id="imp-child", parent_id="imp-parent",
        code_artifact_digest="d2", model_ref="m",
    ))
    g = build_graph(store)
    kinds = {(e["src"], e["dst"], e["kind"]) for e in g["edges"]}
    assert ("imp-parent", "imp-child", "parent_of") in kinds
    champ = next(n for n in g["nodes"] if n["id"] == "imp-parent")
    assert champ["status"] == "champion"


def test_anamnesis_graph_projects_claims(tmp_path):
    from ml_anamnesis_mcp.resources.graph import build_graph
    from ml_anamnesis_mcp.state.models import Claim, ClaimEdge, \
        ClaimType, RefType, Relation
    from ml_anamnesis_mcp.state.store import MemoryStore

    store = MemoryStore(str(tmp_path / "m.db"))
    store.connect()
    store.create_claim(Claim(
        id="claim-t1", content="x beats y", type=ClaimType.empirical,
        confidence=0.8, content_hash="h1", source_id="prog-t1",
    ))
    store.create_edge(ClaimEdge(
        id="ce-t1", from_claim="claim-t1", to_ref="prog-t1",
        ref_type=RefType.programme, relation=Relation.derived_from,
    ))
    g = build_graph(store)
    ids = {n["id"] for n in g["nodes"]}
    assert "claim-t1" in ids
    kinds = {(e["src"], e["dst"], e["kind"]) for e in g["edges"]}
    assert ("prog-t1", "claim-t1", "sourced") in kinds
    assert ("claim-t1", "prog-t1", "derived_from") in kinds


def test_graphs_have_uniform_shape(tmp_path):
    """All four payloads carry the merge contract: nodes/edges/loop."""
    from ml_episteme_mcp.resources.graph import build_graph as e_g
    from ml_anamnesis_mcp.resources.graph import build_graph as a_g
    from ml_anamnesis_mcp.state.store import MemoryStore

    e = _episteme_store(tmp_path)
    m = MemoryStore(str(tmp_path / "m2.db"))
    m.connect()
    for g, loop in ((e_g(e), "loop0"), (a_g(m), "claims")):
        assert {"server", "loop", "generated_at", "truncated",
                "nodes", "edges"} <= set(g)
        assert g["loop"] == loop
