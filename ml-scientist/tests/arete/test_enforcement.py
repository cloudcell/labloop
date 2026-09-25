"""Enforcement tests — the ADR-0003 admission gate + lifecycle rules."""

from __future__ import annotations

from ml_arete_mcp.enforcement.checks import (
    admission_verdict,
    check_ancestry,
    check_contract_valid,
    check_decision_inputs,
    check_proposal_fields,
    check_tool_whitelisted,
    classify_class_map,
    classify_component,
    compute_recursive_gain,
)
from ml_arete_mcp.state.models import (
    ImproverVersion,
    MetaContract,
    TournamentResult,
)


def _imp(i, parent=None):
    return ImproverVersion(
        id=i, parent_id=parent, code_artifact_digest="d",
        model_ref="m", capability_profile={},
    )


# --- Boundary classification ---

def test_classify_class1_components():
    for comp in (
        "audit_log", "enforcement_kernel", "rollback_mechanism",
        "promotion_protocol", "budget_enforcement", "provenance",
        "capability_model", "holdout_access_control",
    ):
        assert classify_component(comp) == "immutable", comp


def test_classify_class3_components():
    for comp in (
        "evaluator_implementation", "metric_weighting",
        "memory_schema", "scheduler",
    ):
        assert classify_component(comp) == "conditional", comp


def test_classify_class2_components():
    for comp in (
        "prompts", "search_policy", "planner", "optimizer_policy",
        "memory_retrieval", "code_generation", "model_choice",
        "tool_adapters",
    ):
        assert classify_component(comp) == "modifiable", comp


def test_classify_unknown_component():
    assert classify_component("novel_thing") == "unknown"


def test_classify_normalizes_names():
    assert classify_component("Audit-Log") == "immutable"
    assert classify_component("  scheduler ") == "conditional"


def test_admission_verdict_immutable_rejects():
    classification = classify_class_map({
        "planner": "modifiable",
        "audit_log": "modifiable",  # declared modifiable — kernel
        #                                overrules to immutable
    })
    status, reason = admission_verdict(classification)
    assert status == "rejected"
    assert "audit_log" in reason


def test_admission_verdict_conditional():
    classification = classify_class_map({"scheduler": "modifiable"})
    status, reason = admission_verdict(classification)
    assert status == "conditional"
    assert reason is None


def test_admission_verdict_unknown_is_conditional():
    classification = classify_class_map({"mystery_part": "modifiable"})
    status, _ = admission_verdict(classification)
    assert status == "conditional"


def test_admission_verdict_clean_modifiable():
    classification = classify_class_map({
        "planner": "x", "prompts": "y",
    })
    status, _ = admission_verdict(classification)
    assert status == "admitted"


# --- Whitelist ---

def test_loop1_whitelist_is_read_only():
    assert check_tool_whitelisted("loop1", "list_investigations") is None
    assert check_tool_whitelisted("loop1", "get_investigation") is None
    err = check_tool_whitelisted("loop1", "open_investigation")
    assert err and "whitelist" in err


def test_loop1_whitelist_includes_promotion_reads():
    """Loop 2's inputs — the roster view, the incumbent, and campaign
    records — are reachable through the evidence channel."""
    for tool in (
        "list_candidates",
        "get_incumbent",
        "list_campaigns",
        "get_campaign",
    ):
        assert check_tool_whitelisted("loop1", tool) is None, tool
    # But the write side is not: Loop-1's own mutation tools stay
    # unreachable — arete consumes promotion outcomes, it does not
    # issue them.
    for tool in (
        "open_campaign",
        "record_promotion_verdict",
        "register_challenger",
        "refresh_roster",
    ):
        err = check_tool_whitelisted("loop1", tool)
        assert err and "whitelist" in err, tool


def test_loop0_whitelist_blocks_writes():
    err = check_tool_whitelisted("loop0", "run_trial")
    assert err and "whitelist" in err
    err = check_tool_whitelisted("loop0", "record_promotion_decision")
    assert err and "whitelist" in err


def test_anamnesis_whitelist_blocks_writes():
    assert check_tool_whitelisted("anamnesis", "get_claim") is None
    err = check_tool_whitelisted("anamnesis", "assert_claim")
    assert err and "whitelist" in err


# --- Contract validity ---

def test_contract_requires_primary_metric():
    assert check_contract_valid({"direction": "max"}) is not None
    assert check_contract_valid({"primary_metric": "hits"}) is None
    err = check_contract_valid(
        {"primary_metric": "hits", "direction": "sideways"}
    )
    assert err and "direction" in err


# --- Proposal fields ---

def test_proposal_fields_required():
    assert check_proposal_fields({}, "b", "f", "r") is not None
    assert check_proposal_fields(
        {"planner": "m"}, "", "f", "r"
    ) is not None
    assert check_proposal_fields(
        {"planner": "m"}, "b", "  ", "r"
    ) is not None
    assert check_proposal_fields(
        {"planner": "m"}, "b", "f", ""
    ) is not None
    assert check_proposal_fields(
        {"planner": "m"}, "b", "f", "r"
    ) is None


# --- Ancestry ---

def test_ancestry_enforced(improver_store):
    store = improver_store
    store.create_improver(_imp("imp-aaaa"))
    store.create_improver(_imp("imp-bbbb", parent="imp-aaaa"))
    store.create_improver(_imp("imp-cccc"))
    assert check_ancestry(store, "imp-aaaa", "imp-bbbb") is None
    assert check_ancestry(store, "imp-aaaa", "imp-aaaa") is not None
    err = check_ancestry(store, "imp-aaaa", "imp-cccc")
    assert err and "descend" in err


# --- Decision inputs ---

def test_decision_requires_evidence_and_attribution():
    assert check_decision_inputs(
        "promote", "r", "human:a", ["eref-1"]
    ) is None
    assert check_decision_inputs(
        "promote", "r", "human:a", []
    ) is not None
    assert check_decision_inputs(
        "promote", "", "human:a", ["eref-1"]
    ) is not None
    assert check_decision_inputs(
        "promote", "r", "", ["eref-1"]
    ) is not None
    err = check_decision_inputs("yea", "r", "human:a", ["eref-1"])
    assert err and "verdict" in err


# --- Recursive gain ---

def _result(metrics):
    return TournamentResult(
        id="t", tournament_id="t", arm="parent",
        descendant_spec={}, metrics=metrics,
    )


def _contract():
    return MetaContract(
        id="c", version=1,
        metrics={"primary_metric": "hits", "direction": "max"},
        promotion_policy={},
    )


def test_recursive_gain_per_seed_best():
    contract = _contract()
    parent = [
        _result({"hits": 10, "seed": 1}),
        _result({"hits": 20, "seed": 1}),  # same seed — best=20
        _result({"hits": 30, "seed": 2}),
    ]
    candidate = [
        _result({"hits": 50, "seed": 1}),
        _result({"hits": 30, "seed": 2}),
    ]
    gain, err = compute_recursive_gain(contract, parent, candidate)
    assert err is None
    # parent E[best] = (20+30)/2 = 25; candidate = (50+30)/2 = 40
    assert gain == 40 / 25


def test_recursive_gain_requires_both_arms():
    contract = _contract()
    gain, err = compute_recursive_gain(
        contract, [], [_result({"hits": 5})]
    )
    assert gain is None and "parent" in err
    gain, err = compute_recursive_gain(
        contract, [_result({"hits": 5})], [_result({"other": 1})]
    )
    assert gain is None and "candidate" in err


def test_recursive_gain_min_direction():
    """Direction-normalized: >1 always means 'candidate better',
    whatever the metric direction. Candidate loss 2.0 < parent 4.0
    is a genuine improvement — gain must read 2.0, not the raw 0.5
    that would report it as a regression."""
    contract = MetaContract(
        id="c", version=1,
        metrics={"primary_metric": "loss", "direction": "min"},
        promotion_policy={},
    )
    gain, err = compute_recursive_gain(
        contract,
        [_result({"loss": 4.0})],
        [_result({"loss": 2.0})],
    )
    assert err is None and gain == 2.0


def test_recursive_gain_min_direction_worse_candidate():
    """The reported-live bug: a WORSE candidate under min must not
    read >1. Parent loss 10.9, candidate 11.2 — the candidate is
    worse, so the normalized gain is <1."""
    contract = MetaContract(
        id="c", version=1,
        metrics={"primary_metric": "val_perplexity", "direction": "min"},
        promotion_policy={},
    )
    gain, err = compute_recursive_gain(
        contract,
        [_result({"val_perplexity": 10.903})],
        [_result({"val_perplexity": 11.225})],
    )
    assert err is None
    assert gain == 10.903 / 11.225
    assert gain < 1.0


def test_recursive_gain_min_direction_zero_candidate():
    """Under min the candidate is the divisor — a perfect candidate
    (E[best]=0) makes the gain undefined, not infinite."""
    contract = MetaContract(
        id="c", version=1,
        metrics={"primary_metric": "loss", "direction": "min"},
        promotion_policy={},
    )
    gain, err = compute_recursive_gain(
        contract,
        [_result({"loss": 4.0})],
        [_result({"loss": 0.0})],
    )
    assert gain is None and "candidate" in err and "0" in err


def test_recursive_gain_zero_parent():
    contract = _contract()
    gain, err = compute_recursive_gain(
        contract,
        [_result({"hits": 0})],
        [_result({"hits": 5})],
    )
    assert gain is None and "0" in err


def test_recursive_gain_refuses_non_scalar_seed():
    """A dict/list 'seed' (aggregate rows written before seed-shape
    validation) must refuse cleanly, naming the row — not crash with
    'unhashable type' inside the grouping dict."""
    contract = _contract()
    bad = _result({"hits": 38.7, "seed": {"7": 38.7, "8": 39.0}})
    gain, err = compute_recursive_gain(
        contract, [bad], [_result({"hits": 5, "seed": 7})]
    )
    assert gain is None
    assert "t" in err and "non-scalar" in err

    bad_list = _result({"hits": 38.7, "seed": [38.7, 39.0]})
    gain, err = compute_recursive_gain(
        contract, [_result({"hits": 10, "seed": 7})], [bad_list]
    )
    assert gain is None and "non-scalar" in err
