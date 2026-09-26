"""Enforcement checks for the arete meta-change lifecycle.

Each check returns an error string or None — the same convention as
the other loop servers. These are the integrity rules of the recursive
loop, and the admission gate is where ADR-0003 becomes mechanism:

- class-1 (immutable / human-governed) component touch → the proposal
  is rejected at ingress, before any tournament may reference it;
- class-3 (conditionally modifiable) or unknown component touch → the
  proposal is admitted as `conditional` and promote_policy later
  requires a decision whose decided_by names a human authority
  (the "human:" prefix convention);
- class-2 (recursively modifiable) → admitted.

The class table is data in code — the immutable kernel that
candidates may not rewrite (ADR-0003 class 1 includes the
enforcement kernel itself).
"""

from __future__ import annotations

import re

from ..state.models import (
    DecisionVerdict,
    EvidenceContext,
    EvidenceSource,
    MetaContract,
    ProposalStatus,
    TournamentStatus,
)
from ..state.store import ImproverStore

# ------------------------------------------------------------------
# ADR-0003 writable-boundary classes. Component names are normalized
# (lowercase, '-'/' ' → '_') before lookup. A component absent from
# every set classifies as conditional — unknown surfaces default to
# the more conservative review path, never to silently writable.
# ------------------------------------------------------------------

CLASS_IMMUTABLE: frozenset[str] = frozenset({
    "audit_log",
    "event_log",
    "audit_semantics",
    "artifact_hashing",
    "provenance",
    "artifact_provenance",
    "capability_model",
    "permission_model",
    "permissions",
    "budget_enforcement",
    "compute_ceiling",
    "compute_ceilings",
    "budgets",
    "promotion_protocol",
    "holdout_access_control",
    "heldout_access_control",
    "held_out_evaluation_access",
    "rollback_mechanism",
    "enforcement_kernel",
    "safety_kernel",
})

CLASS_CONDITIONAL: frozenset[str] = frozenset({
    "evaluator_implementation",
    "evaluator",
    "metric_weighting",
    "metric_weights",
    "memory_schema",
    "scheduler",
})

CLASS_MODIFIABLE: frozenset[str] = frozenset({
    "prompts",
    "policies",
    "search_policy",
    "planner",
    "planner_algorithm",
    "optimizer_policy",
    "optimizer",
    "role_composition",
    "memory_retrieval",
    "retrieval_strategy",
    "code_generation",
    "codegen_strategy",
    "experiment_templates",
    "model_choice",
    "fine_tuning",
    "tool_adapters",
    "tool_adaptors",
})


def normalize_component(name: str) -> str:
    return name.strip().lower().replace("-", "_").replace(" ", "_")


def classify_component(name: str) -> str:
    """ADR-0003 class for a component: 'immutable' | 'conditional' |
    'modifiable' | 'unknown'. Unknown is treated as conditional by
    the admission gate."""
    n = normalize_component(name)
    if n in CLASS_IMMUTABLE:
        return "immutable"
    if n in CLASS_CONDITIONAL:
        return "conditional"
    if n in CLASS_MODIFIABLE:
        return "modifiable"
    return "unknown"


def classify_class_map(class_map: dict) -> dict:
    """Authoritative classification of every changed component.

    Returns {component: class} where class ∈ immutable | conditional |
    modifiable | unknown. The proposal's *declared* classes are not
    consulted — the kernel classifies, the proposer only names
    components.
    """
    return {name: classify_component(name) for name in class_map}


def admission_verdict(classification: dict) -> tuple[str, str | None]:
    """Map a classified class_map to (status, rejection_reason).

    Any immutable touch → rejected. Any conditional or unknown touch →
    conditional. Otherwise admitted.
    """
    immutable = sorted(
        c for c, k in classification.items() if k == "immutable"
    )
    if immutable:
        return (
            ProposalStatus.rejected.value,
            "ADR-0003 class-1 (immutable / human-governed) "
            f"component(s) touched: {', '.join(immutable)}. "
            "Safety, evidence, governance and enforcement surfaces "
            "are not candidate-writable.",
        )
    flagged = sorted(
        c for c, k in classification.items()
        if k in {"conditional", "unknown"}
    )
    if flagged:
        return ProposalStatus.conditional.value, None
    return ProposalStatus.admitted.value, None


# ------------------------------------------------------------------
# Evidence channel whitelist — read-only upstream surfaces.
# ------------------------------------------------------------------

EVIDENCE_READ_TOOLS: dict[str, frozenset[str]] = {
    EvidenceSource.loop0.value: frozenset({
        "list_active_programmes",
        "list_hypotheses",
        "list_trials",
        "get_trial_status",
        "assess_programme",
        "get_candidate_lineage",
        "list_archives",
        "get_archive",
        "get_archived_programme",
        "describe_blob",  # read-only digest resolution — the check
        #                  behind register_improver's artifact claims
    }),
    EvidenceSource.loop1.value: frozenset({
        "list_investigations",
        "get_investigation",
        # Promotion state — Loop 2's actual inputs: the roster view of
        # candidate lineage, the incumbent, and campaign records.
        "list_candidates",
        "get_incumbent",
        "list_campaigns",
        "get_campaign",
    }),
    EvidenceSource.anamnesis.value: frozenset({
        "get_claim",
        "list_claims",
        "recall",  # anamnesis Phase 2 — propagates an upstream
        #            error until it lands
    }),
}


DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


async def check_digest_claim(
    field: str, value: str | None, resolve
) -> str | None:
    """Resolution-or-none for artifact digests — mirrors episteme's
    check, but resolution happens upstream through the loop0
    channel (arete holds no blob store of its own).

    ``resolve`` is an async callable returning True when the digest
    is a live content address on episteme; it raises when
    verification is impossible — absent channel or dead upstream —
    and that failure refuses the digest (degraded-honest: ``"none"``
    still passes, verify-skipping never does).
    """
    if value is None or value == "none":
        return None
    if not DIGEST_RE.match(value):
        return (
            f"{field} '{value}' is not a content digest — expected "
            "sha256:<64 hex> resolving to an ingested artifact, or "
            "'none' to declare no artifact."
        )
    try:
        exists = await resolve(value)
    except Exception as e:
        return (
            f"{field} cannot be verified — the loop0 (episteme) "
            f"channel is unavailable ({e}), so no digest can "
            "resolve. Pass 'none' to declare no artifact, or "
            "register once loop0 reconnects."
        )
    if not exists:
        return (
            f"{field} '{value}' does not resolve to any ingested "
            "artifact upstream — a hash of nothing is a claim with "
            "no referent. Ingest the bytes on episteme first and "
            "pass the returned digest, or pass 'none' to declare "
            "no artifact."
        )
    return None


def check_source_valid(source: str) -> str | None:
    if source not in EVIDENCE_READ_TOOLS:
        return (
            "source must be one of "
            f"{'|'.join(EVIDENCE_READ_TOOLS)}, got: {source}"
        )
    return None


def check_tool_whitelisted(source: str, tool: str) -> str | None:
    """The evidence channel is read-only — the whitelist is where
    that boundary is enforced."""
    allowed = EVIDENCE_READ_TOOLS.get(source, frozenset())
    if tool not in allowed:
        return (
            f"tool '{tool}' is not on the {source} read whitelist "
            f"({'|'.join(sorted(allowed))}). The evidence channel is "
            "read-only; writes to the lower loops belong to the "
            "promotion pipeline, not to pull_evidence."
        )
    return None


# ------------------------------------------------------------------
# Loop-1 orchestration channel — the write-capable campaign surface.
# A separate adaptor role from `loop1` (which stays read-only); the
# whitelist is the protocol boundary. Only campaign-lifecycle verbs
# pass — register_challenger and roster management are deliberately
# absent (the client drives those directly when needed).
# ------------------------------------------------------------------

LOOP1_ORCHESTRATION_TOOLS: frozenset[str] = frozenset({
    "open_campaign",
    "spawn_campaign_programme",
    "pull_campaign_evidence",
    "record_campaign_result",
    "close_campaign",
    "record_promotion_verdict",
})

# Tournament budget keys the orchestration channel requires — both
# arms' campaigns carry the tournament's budget verbatim so the
# upstream spawn cap and close-time audit have values to enforce.
ORCHESTRATION_BUDGET_KEYS: frozenset[str] = frozenset({
    "programmes_per_arm",
    "trials_per_programme",
})


def check_orchestration_tool_whitelisted(tool: str) -> str | None:
    if tool not in LOOP1_ORCHESTRATION_TOOLS:
        return (
            f"tool '{tool}' is not on the loop1_orchestration "
            f"whitelist ({'|'.join(sorted(LOOP1_ORCHESTRATION_TOOLS))}). "
            "The orchestration channel reaches only campaign-lifecycle "
            "verbs."
        )
    return None


def check_tournament_budget_carryable(budget: dict) -> str | None:
    """A tournament that orchestrates must carry the budget keys the
    upstream campaign audit enforces. Older tournament budgets lacking
    them fail open_arm_campaign loudly rather than inventing values."""
    missing = sorted(
        k for k in ORCHESTRATION_BUDGET_KEYS if k not in (budget or {})
    )
    if missing:
        return (
            f"tournament budget lacks required orchestration keys "
            f"({', '.join(missing)}). open_arm_campaign carries the "
            "tournament budget verbatim — it will not invent one."
        )
    return None


def check_evidence_context(
    store: ImproverStore, context_type: str, context_id: str
) -> str | None:
    """A pull must belong to a live context — evidence gathering is
    declared, never ambient."""
    if context_type == EvidenceContext.proposal.value:
        proposal = store.get_proposal(context_id)
        if proposal is None:
            return f"Proposal not found: {context_id}."
        if proposal.status not in {
            ProposalStatus.admitted, ProposalStatus.conditional
        }:
            return (
                f"Proposal {context_id} is {proposal.status.value} — "
                "only admitted or conditional proposals accept "
                "evidence pulls."
            )
        return None
    if context_type == EvidenceContext.tournament.value:
        tournament = store.get_tournament(context_id)
        if tournament is None:
            return f"Tournament not found: {context_id}."
        # Closed tournaments still accept pulls: the decision phase
        # that follows needs to consult upstream records (archives,
        # claims) — only the results are sealed at close.
        return None
    return (
        "context_type must be one of "
        f"{'|'.join(c.value for c in EvidenceContext)}, "
        f"got: {context_type}"
    )


# ------------------------------------------------------------------
# Lifecycle checks
# ------------------------------------------------------------------

def check_contract_valid(metrics: dict) -> str | None:
    """A meta-contract must declare the primary metric — the quantity
    recursive_gain is computed over."""
    if not isinstance(metrics, dict):
        return "metrics must be an object"
    primary = metrics.get("primary_metric")
    if not primary or not isinstance(primary, str):
        return (
            "metrics must declare 'primary_metric' — the metric "
            "name recursive_gain is computed over (e.g. "
            '{"primary_metric": "hits", "direction": "max"})'
        )
    direction = metrics.get("direction", "max")
    if direction not in {"max", "min"}:
        return f"metrics.direction must be 'max' or 'min', got: {direction}"
    return None


def check_proposal_fields(
    class_map: dict,
    expected_benefit: str,
    falsification: str,
    rollback_plan: str,
) -> str | None:
    if not isinstance(class_map, dict) or not class_map:
        return (
            "class_map must be a non-empty object mapping every "
            "changed component to its declared boundary class — "
            "the admission gate classifies authoritatively, but "
            "the declaration is the Duhem-Quine boundary of the "
            "proposal."
        )
    for field, value in (
        ("expected_benefit", expected_benefit),
        ("falsification", falsification),
        ("rollback_plan", rollback_plan),
    ):
        if not isinstance(value, str) or not value.strip():
            return f"{field} must be a non-empty string"
    return None


def check_ancestry(
    store: ImproverStore, parent_id: str, candidate_id: str
) -> str | None:
    """The ancestral tournament is *ancestral*: the candidate must
    descend from the parent arm. Otherwise the comparison is not
    parent-vs-child but two unrelated improvers — a different
    experiment."""
    if parent_id == candidate_id:
        return "parent and candidate arms must differ."
    if not store.is_descendant(candidate_id, parent_id):
        return (
            f"{candidate_id} does not descend from {parent_id}. "
            "The ancestral tournament compares an improver against "
            "its own descendant — register the candidate with "
            f"parent_id in {parent_id}'s lineage first."
        )
    return None


def check_arm_valid(arm: str) -> str | None:
    if arm not in {"parent", "candidate"}:
        return f"arm must be 'parent' or 'candidate', got: {arm}"
    return None


def check_tournament_open(store: ImproverStore, tournament_id: str):
    """Load the tournament; error if missing or closed."""
    tournament = store.get_tournament(tournament_id)
    if tournament is None:
        return f"Tournament not found: {tournament_id}.", None
    if tournament.status != TournamentStatus.open:
        state = tournament.status.value
        why = (
            "a dead record with an audit trail"
            if tournament.status == TournamentStatus.voided
            else "results are sealed"
        )
        return (
            f"Tournament {tournament_id} is {state} — {why}. Open a "
            "new tournament under a fresh contract instead.",
            tournament,
        )
    return None, tournament


def compute_recursive_gain(
    contract: MetaContract,
    parent_results: list,
    candidate_results: list,
) -> tuple[float | None, str | None]:
    """recursive_gain — the promotion signal, normalized so that >1
    always means "the candidate improver produces better descendants
    per unit budget", whatever the contract direction.

        direction=max: E[best | candidate] / E[best | parent]
        direction=min: E[best | parent] / E[best | candidate]

    Without normalization a minimized metric inverts the sign — a
    worse candidate (higher loss) would read gain > 1 while the
    interpretation says "promote case".

    Results carrying a 'seed' key in metrics are grouped per seed;
    the best primary-metric value per group is averaged (E[best]).
    Results lacking the primary metric are skipped. Both arms need
    ≥1 usable result — an unpaired tournament is not a tournament.
    Returns (gain, error).
    """
    primary = contract.metrics["primary_metric"]
    direction = contract.metrics.get("direction", "max")

    # Rows written before seed-shape validation existed may carry
    # dict/list seeds — grouping would crash on unhashable keys.
    # Refuse cleanly, naming the offending rows, rather than
    # TypeError-ing inside setdefault.
    def _seed(r):
        return r.metrics.get("seed", r.descendant_spec.get("seed"))

    malformed = [
        r.id for r in (*parent_results, *candidate_results)
        if primary in r.metrics
        and _seed(r) is not None
        and not isinstance(_seed(r), (str, int, float))
    ]
    if malformed:
        return None, (
            f"cannot close: result(s) {', '.join(malformed)} carry a "
            "non-scalar 'seed' (dict/list) — per-seed grouping is "
            "undefined for aggregates. Record corrected per-seed "
            "results (one row per seed) or leave the tournament open."
        )

    def per_seed_best(results):
        groups: dict = {}
        for r in results:
            if primary not in r.metrics:
                continue
            seed = r.metrics.get("seed", r.descendant_spec.get("seed"))
            groups.setdefault(seed, []).append(r.metrics[primary])
        if not groups:
            return None
        bests = [
            (max if direction == "max" else min)(vals)
            for vals in groups.values()
        ]
        return sum(bests) / len(bests)

    e_parent = per_seed_best(parent_results)
    e_candidate = per_seed_best(candidate_results)
    missing = []
    if e_parent is None:
        missing.append("parent")
    if e_candidate is None:
        missing.append("candidate")
    if missing:
        return None, (
            f"cannot close: no recorded results carrying the primary "
            f"metric '{primary}' on the "
            f"{' and '.join(missing)} arm(s). An unpaired tournament "
            "is not a tournament — record results on both arms "
            "(record_tournament_result) or leave it open."
        )
    # Normalize on the divisor: under direction=min the candidate
    # arm is the denominator, so its zero is the undefined case.
    divisor = e_candidate if direction == "min" else e_parent
    dividend = e_parent if direction == "min" else e_candidate
    divisor_arm = "candidate" if direction == "min" else "parent"
    if divisor == 0:
        return None, (
            f"cannot close: {divisor_arm}-arm E[best {primary}] is "
            "0 — recursive_gain is undefined under "
            f"direction={direction}. Record the tournament outcome "
            "via record_meta_decision with rationale."
        )
    return dividend / divisor, None


def check_decision_inputs(
    verdict: str, rationale: str, decided_by: str, evidence_refs: list
) -> str | None:
    if verdict not in {v.value for v in DecisionVerdict}:
        return (
            "verdict must be one of "
            f"{'|'.join(v.value for v in DecisionVerdict)}, "
            f"got: {verdict}"
        )
    if not rationale.strip():
        return "rationale must be non-empty — attribution of why."
    if not decided_by.strip():
        return (
            "decided_by must be non-empty — every meta-decision is "
            "attributable (e.g. 'human:alice', 'arete:protocol', "
            "an improver id)."
        )
    if not evidence_refs:
        return (
            "evidence_refs must cite at least one evidence_ref from "
            "pull_evidence — a meta-decision with no consulted "
            "evidence is unaccountable."
        )
    return None


def check_decision_evidence(
    store: ImproverStore, candidate_id: str, evidence_refs: list
) -> str | None:
    """Each cited eref must exist and belong to a context related to
    this candidate: a tournament involving it, or a proposal by the
    candidate or one of its ancestors. No smuggling another line's
    trail."""
    ancestor_ids = {imp.id for imp in store.lineage(candidate_id)}
    for ref_id in evidence_refs:
        ref = store.get_evidence_ref(ref_id)
        if ref is None:
            return f"Evidence ref not found: {ref_id}."
        if ref.context_type == EvidenceContext.tournament:
            t = store.get_tournament(ref.context_id)
            if t is None or candidate_id not in {
                t.parent_improver_id, t.candidate_improver_id
            }:
                return (
                    f"Evidence ref {ref_id} belongs to tournament "
                    f"{ref.context_id}, which does not involve "
                    f"{candidate_id}. Pull the evidence inside a "
                    "context related to this candidate."
                )
        elif ref.context_type == EvidenceContext.proposal:
            p = store.get_proposal(ref.context_id)
            if p is None or p.proposer_improver_id not in ancestor_ids:
                return (
                    f"Evidence ref {ref_id} belongs to proposal "
                    f"{ref.context_id} by {p.proposer_improver_id if p else '?'}, "
                    f"which is not {candidate_id} or its ancestor. "
                    "Pull the evidence inside a related context."
                )
    return None


def check_promote_allowed(
    store: ImproverStore, candidate_id: str
) -> tuple[str | None, object | None]:
    """Gate for promote_policy. Returns (error, promote_decision).

    - The candidate's latest meta_decision must be 'promote' — a
      later reject/hold/rollback supersedes any earlier promote.
    - If the candidate implements a `conditional` proposal (class-3
      or unknown component touched), that promote decision's
      decided_by must name a human authority — the 'human:' prefix
      is the mechanism, not a convention.
    - The candidate must not already be champion.
    """
    candidate = store.get_improver(candidate_id)
    if candidate is None:
        return f"Improver not found: {candidate_id}.", None
    if candidate.is_champion:
        return (
            f"{candidate_id} is already the champion — promotion is "
            "a pointer move, not a re-registration.",
            None,
        )
    decision = store.latest_decision(candidate_id)
    if decision is None:
        return (
            f"No meta_decision recorded for {candidate_id}. "
            "promote_policy requires a prior 'promote' decision — "
            "champions are made by decisions, not by registration.",
            None,
        )
    if decision.verdict != DecisionVerdict.promote:
        return (
            f"Latest meta_decision for {candidate_id} is "
            f"'{decision.verdict.value}', not 'promote'. A later "
            "decision supersedes an earlier promote — record a new "
            "promote decision first.",
            None,
        )
    if candidate.proposal_id:
        proposal = store.get_proposal(candidate.proposal_id)
        if (
            proposal is not None
            and proposal.status == ProposalStatus.conditional
            and not decision.decided_by.startswith("human:")
        ):
            return (
                f"{candidate_id} implements conditional proposal "
                f"{proposal.id} (class-3 or unknown component touch). "
                "Promotion requires a promote decision whose "
                "decided_by names a human authority ('human:<name>') "
                f"— got '{decision.decided_by}'. The frozen-review "
                "gate is a mechanism, not a convention.",
                None,
            )
    return None, decision


def check_metric_name_drift(
    store: ImproverStore, metrics: dict
) -> list[str]:
    """Contract-metric lint (plan A2) — advisory, never a refusal.

    Warns when a newly declared metric name has never appeared on a
    prior meta_contract but is a near-spelling of one that did — the
    drift pattern from the field report (same measurement, two names
    → recursive_gain series that silently compare different things).
    A declared 'metric_alias' mapping in metrics_json resolves the
    warning.
    """
    import difflib

    # Metric names are the *values* under metric-role keys
    # ({"primary_metric": "hits", "direction": "max", ...}) — keys are
    # schema fields, values are the names that drift.
    _SCHEMA_KEYS = {"direction", "metric_alias"}

    def _names(m: dict) -> set[str]:
        out = set()
        for k, v in m.items():
            if k in _SCHEMA_KEYS:
                continue
            if isinstance(v, str):
                out.add(v)
            elif isinstance(v, list):
                out.update(x for x in v if isinstance(x, str))
        return out

    declared = _names(metrics)
    if not declared:
        return []
    aliases = metrics.get("metric_alias") or {}

    prior: set[str] = set()
    for c in store.list_meta_contracts():
        prior.update(_names(c.metrics))
        for new, _old in (c.metrics.get("metric_alias") or {}).items():
            prior.add(new)

    warnings = []
    for name in sorted(declared):
        if name in prior or name in aliases:
            continue
        close = difflib.get_close_matches(name, prior, n=2, cutoff=0.55)
        for cand in close:
            warnings.append(
                f"metric '{name}' is new but near-spells prior "
                f"contract metric '{cand}' — if this is a rename, "
                "declare it via 'metric_alias' in metrics_json so "
                "cross-contract gain series stay comparable; if it "
                "is genuinely new, ignore this lint."
            )
    return warnings
