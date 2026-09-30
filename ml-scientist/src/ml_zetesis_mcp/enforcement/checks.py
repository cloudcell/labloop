"""Enforcement checks for the zetesis investigation lifecycle.

Each check returns an error string or None — the same convention as
ml-episteme's commitments layer and anamnesis's checks. These are the
integrity rules of the search loop: an investigation that can assert
findings without consulting evidence produces prose, not research.
"""

from __future__ import annotations

import math

from ..state.models import (
    CampaignStatus,
    EvidenceSource,
    Investigation,
    InvestigationStatus,
    InvestigationVerdict,
)
from ..state.store import SearchStore
from .. import _grounded_constants as _gc

# The read-only upstream surface. pull_evidence proxies calls through
# the evidence channel; anything outside these names is rejected before
# the call — a client cannot reach run_trial or record_promotion_decision
# through this server. Loop-0 writes come only from the promotion
# pipeline (a later increment), not from the evidence adaptor.
EVIDENCE_READ_TOOLS: dict[str, frozenset[str]] = {
    EvidenceSource.loop0.value: frozenset(
        {
            "list_active_programmes",
            "list_hypotheses",
            "list_trials",
            "get_trial_status",
            "assess_programme",
            "get_candidate_lineage",
            "list_archives",
            "get_archive",
            "get_archived_programme",
            # Population reads (Loop-1 promotion increment)
            "list_candidates",
            "get_candidate",
            "list_promotion_decisions",
            "get_incumbent",
            "get_candidate_scorecard",
            "list_programmes",
            "get_evaluation_contract",
        }
    ),
    EvidenceSource.anamnesis.value: frozenset(
        {
            "get_claim",
            "list_claims",
            "recall",  # anamnesis Phase 2 — propagates an upstream
            #            error until it lands
        }
    ),
}

# The promotion channel — the ecosystem's first cross-server write.
# A separate adaptor role with its own whitelist; the evidence channel
# stays read-only. All three targets are already insert-only upstream
# surfaces. The whitelist is a protocol boundary, not auth.
PROMOTION_WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "register_candidate",
        "create_evaluation_contract",
        "record_promotion_decision",
        # Loop-2 orchestration: spawned descendant programmes are
        # created upstream through the same insert-only surface.
        "create_programme",
    }
)

# Campaign budget keys the orchestration channel carries from the
# upstream tournament. Both must be present for the arm-level spawn
# and spend accounting to be auditable.
CAMPAIGN_BUDGET_KEYS: frozenset[str] = frozenset(
    {"programmes_per_arm", "trials_per_programme"}
)


def check_promotion_tool_whitelisted(tool: str) -> str | None:
    if tool not in PROMOTION_WRITE_TOOLS:
        return (
            f"tool '{tool}' is not on the promotion write whitelist "
            f"({'|'.join(sorted(PROMOTION_WRITE_TOOLS))}). The promotion "
            "channel reaches only the insert-only lineage surfaces."
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
    """The evidence channel is read-only — the whitelist is where that
    boundary is enforced."""
    allowed = EVIDENCE_READ_TOOLS.get(source, frozenset())
    if tool not in allowed:
        return (
            f"tool '{tool}' is not on the {source} read whitelist "
            f"({'|'.join(sorted(allowed))}). The evidence channel is "
            "read-only; writes to Loop 0 belong to the promotion "
            "pipeline, not to pull_evidence."
        )
    return None


def check_investigation_open(
    store: SearchStore, investigation_id: str
) -> tuple[str | None, Investigation | None]:
    """Load the investigation; error if missing or not open."""
    inv = store.get_investigation(investigation_id)
    if inv is None:
        return f"Investigation not found: {investigation_id}.", None
    if inv.status != InvestigationStatus.open:
        return (
            f"Investigation {investigation_id} is {inv.status.value} — "
            "only open investigations accept evidence or findings.",
            inv,
        )
    return None, inv


def check_evidence_refs_exist(
    store: SearchStore, investigation_id: str, evidence_ref_ids: list[str]
) -> str | None:
    """Each cited ref must exist and belong to this investigation —
    no smuggling another inquiry's trail."""
    for ref_id in evidence_ref_ids:
        ref = store.get_evidence_ref(ref_id)
        if ref is None:
            return f"Evidence ref not found: {ref_id}."
        if ref.investigation_id != investigation_id:
            return (
                f"Evidence ref {ref_id} belongs to "
                f"{ref.investigation_id}, not {investigation_id}. "
                "Re-pull the evidence inside this investigation."
            )
    return None


def check_verdict_valid(verdict: str) -> str | None:
    if verdict not in {v.value for v in InvestigationVerdict}:
        return (
            "verdict must be one of "
            f"{'|'.join(v.value for v in InvestigationVerdict)}, "
            f"got: {verdict}"
        )
    return None


def check_conclude_allowed(
    store: SearchStore, investigation: Investigation, verdict: str
) -> str | None:
    """'findings' requires at least one live provisional finding —
    concluding with findings when none exist would mint nothing and
    report success."""
    if verdict == InvestigationVerdict.findings.value:
        live = store.list_findings(
            investigation.id, status="provisional"
        )
        if not live:
            return (
                "verdict 'findings' requires at least one provisional "
                "finding — record one first, drop_finding the ones that "
                "did not survive, or conclude 'null_result'."
            )
    return None


# --- Promotion lifecycle ---


def check_campaign_exists(store: SearchStore, campaign_id: str) -> str | None:
    if store.get_campaign(campaign_id) is None:
        return f"Campaign not found: {campaign_id}."
    return None


def check_campaign_open(store: SearchStore, campaign_id: str) -> str | None:
    """Load the campaign; error if missing or already terminal —
    closed campaigns freeze results and score, abandoned ones are
    equally immutable."""
    campaign = store.get_campaign(campaign_id)
    if campaign is None:
        return f"Campaign not found: {campaign_id}."
    if campaign.status is CampaignStatus.closed:
        return (
            f"Campaign '{campaign_id}' is already closed — its "
            "promotion_score and recorded results are frozen."
        )
    if campaign.status is CampaignStatus.abandoned:
        return (
            f"Campaign '{campaign_id}' was abandoned — a terminal "
            "record that accrues no further writes."
        )
    return None


def check_campaign_evidence_pullable(
    store: SearchStore, campaign_id: str
) -> str | None:
    """Evidence consultation is a read against the campaign, not a
    mutation of the frozen record — it stays legal while a verdict
    is still pending. A closed campaign awaiting its verdict needs
    exactly these pulls (the eref is what record_promotion_verdict
    cites). What freezes at close is results and score; what ends
    consultation is the verdict itself — pulling after decision_id
    is set would mint erefs the verdict never consulted."""
    campaign = store.get_campaign(campaign_id)
    if campaign is None:
        return f"Campaign not found: {campaign_id}."
    if campaign.status is CampaignStatus.abandoned:
        return (
            f"Campaign '{campaign_id}' was abandoned — a terminal "
            "record that accrues no further writes."
        )
    if (campaign.status is CampaignStatus.closed
            and campaign.decision_id is not None):
        return (
            f"Campaign '{campaign_id}' already has verdict "
            f"{campaign.decision_id} — the evidence trail it cites "
            "is final."
        )
    return None


def check_roster_entry(store: SearchStore, entry_id: str) -> str | None:
    if store.get_roster_entry(entry_id) is None:
        return (
            f"Candidate {entry_id} is not on the roster — run "
            "refresh_roster or register_challenger first."
        )
    return None


def check_campaign_evidence_refs(
    store: SearchStore, campaign_id: str, evidence_ref_ids: list[str]
) -> str | None:
    """Same anti-smuggling rule as investigations, scoped to the
    campaign context — a ref must belong to this campaign."""
    for ref_id in evidence_ref_ids:
        ref = store.get_evidence_ref(ref_id)
        if ref is None:
            return f"Evidence ref not found: {ref_id}."
        if ref.campaign_id != campaign_id:
            return (
                f"Evidence ref {ref_id} does not belong to campaign "
                f"{campaign_id}. Pull the evidence under this "
                "campaign's context."
            )
    return None


def check_verdict_decided_by(verdict: str, decided_by: str) -> str | None:
    """Corrective verdicts need human authority — a rollback undoes a
    promotion and can't be issued by the search policy alone."""
    if verdict == "rollback" and not decided_by.startswith("human:"):
        return (
            "Rollback verdicts require human authority — decided_by "
            "must start with 'human:'."
        )
    return None


# --- Orchestration (Loop-2 driven campaigns) ---


POWER_POLICY_KEYS = ("sesoi_d", "target_power", "min_evidence_rung")


def check_promotion_policy_power(policy: dict | None) -> str | None:
    """A promotion policy must declare the design inputs a power
    analysis reads — Bakker et al. (2020): a preregistration states
    its SESOI and target power before the data exists. Zetesis runs
    this on the contract payload pulled over the evidence channel —
    the upstream mint validates too, but a campaign opened under a
    legacy contract must refuse here, not inherit."""
    policy = policy or {}
    missing = [k for k in POWER_POLICY_KEYS if k not in policy]
    if missing:
        return (
            f"promotion_policy lacks required power keys "
            f"({', '.join(missing)}) — contracts declare the design "
            "inputs up front: 'sesoi_d' (standardized smallest "
            "effect of interest, Cohen's d > 0), 'target_power' in "
            "(0, 1), 'min_evidence_rung' "
            f"({'|'.join(_gc.EVIDENCE_RUNGS)}); optional 'alpha' "
            "(default 0.05), optional 'prior' in (0, 0.5] "
            "(declared P[H1] before the evidence; absent → "
            "PRIOR_CONFIDENCE_MAX)."
        )
    d = policy["sesoi_d"]
    if (
        not isinstance(d, (int, float))
        or isinstance(d, bool)
        or d <= 0
    ):
        return (
            f"promotion_policy.sesoi_d must be a number > 0 — the "
            f"standardized smallest effect worth detecting (a "
            f"declaration, never a pilot estimate), got: {d!r}"
        )
    p = policy["target_power"]
    if (
        not isinstance(p, (int, float))
        or isinstance(p, bool)
        or not 0 < p < 1
    ):
        return (
            f"promotion_policy.target_power must be in (0, 1), "
            f"got: {p!r}"
        )
    rung = policy["min_evidence_rung"]
    if rung not in _gc.EVIDENCE_RUNGS:
        return (
            f"promotion_policy.min_evidence_rung must be one of "
            f"{'|'.join(_gc.EVIDENCE_RUNGS)} (the Kass–Raftery "
            f"2 ln BF ladder), got: {rung!r}"
        )
    alpha = policy.get("alpha", 0.05)
    if (
        not isinstance(alpha, (int, float))
        or isinstance(alpha, bool)
        or not 0 < alpha < 1
    ):
        return (
            f"promotion_policy.alpha must be in (0, 1), "
            f"got: {alpha!r}"
        )
    # Optional declared prior P[H₁] — NAP Table D-1 tabulates to
    # 0.5; a challenger starting above 'as likely as not' before
    # the evidence exists is not a preregistration. Absent → the
    # mint's computation inputs default to PRIOR_CONFIDENCE_MAX (0.3).
    prior = policy.get("prior")
    if prior is not None and (
        not isinstance(prior, (int, float))
        or isinstance(prior, bool)
        or not math.isfinite(prior)
        or not 0 < prior <= 0.5
    ):
        return (
            f"promotion_policy.prior must be in (0, 0.5] — the "
            f"NAP D-1 tabulated range; P[H₁] declared before the "
            f"evidence exists, got: {prior!r}"
        )
    return None


def check_campaign_budget_carryable(
    campaign_budget: dict | None,
) -> str | None:
    """Orchestrated campaigns must carry the tournament's budget so
    spawn caps and close-time audits have something to enforce. A
    campaign opened without programmes_per_arm/trials_per_programme
    can't be audited — reject rather than default. Present-but-zero
    (or negative) is the same wedge wearing a different shape: a cap
    of 0 can never spawn, so the campaign is structurally dead."""
    budget = campaign_budget or {}
    missing = sorted(k for k in CAMPAIGN_BUDGET_KEYS if k not in budget)
    if missing:
        return (
            f"campaign budget lacks required orchestration keys "
            f"({', '.join(missing)}). Orchestrated campaigns carry the "
            "tournament budget — open_arm_campaign refuses to invent one."
        )
    degenerate = sorted(
        k for k in CAMPAIGN_BUDGET_KEYS
        if not isinstance(budget.get(k), (int, float))
        or isinstance(budget.get(k), bool)
        or budget[k] <= 0
    )
    if degenerate:
        return (
            f"campaign budget is degenerate — "
            f"{', '.join(f'{k}={budget[k]!r}' for k in degenerate)} "
            "cannot spawn or spend anything. A zero/negative cap is "
            "a wedged campaign, not a runnable one."
        )
    return None


def check_spawn_cap(
    store: SearchStore, campaign_id: str, arm: str, budget: dict | None
) -> str | None:
    """A campaign arm may spawn at most programmes_per_arm programmes.
    The cap is what makes 'the descendant campaign ran within the
    tournament's budget' a checkable claim rather than prose."""
    cap = (budget or {}).get("programmes_per_arm")
    if cap is None:
        return check_campaign_budget_carryable(budget)
    spawned = len(store.list_campaign_spawns(campaign_id, arm=arm))
    if spawned >= int(cap):
        return (
            f"spawn cap reached: campaign {campaign_id} arm '{arm}' "
            f"already has {spawned} programmes (programmes_per_arm={cap})."
        )
    return None


def check_spawn_scoped_result(
    store: SearchStore,
    campaign_id: str,
    arm: str,
    programme_id: str,
) -> str | None:
    """If the campaign has spawn records, results must name a programme
    spawned for this campaign and this arm — no reporting on a
    programme that was never part of the orchestrated run. Campaigns
    with no spawns at all keep the client-driven behaviour (the
    client names its own programmes) — unless the named programme is
    spawn-bound to a *different* campaign, which is orchestrated
    elsewhere and may not be reported here (rc-12: the early exit
    let a cross-campaign result through)."""
    spawns = store.list_campaign_spawns(campaign_id)
    spawn = store.get_spawn_for_programme(programme_id)
    if not spawns:
        if spawn is not None and spawn.campaign_id != campaign_id:
            return (
                f"programme {programme_id} is spawn-bound to campaign "
                f"{spawn.campaign_id}, not {campaign_id} — a "
                "client-driven campaign may not report on another "
                "campaign's orchestrated programme."
            )
        return None
    if spawn is None or spawn.campaign_id != campaign_id:
        return (
            f"programme {programme_id} was not spawned under campaign "
            f"{campaign_id} — orchestrated campaigns report only on "
            "spawned programmes."
        )
    if spawn.arm.value != arm:
        return (
            f"programme {programme_id} was spawned for arm "
            f"'{spawn.arm.value}', not '{arm}' — results stay "
            "attributed to the arm that created them."
        )
    return None


def check_close_budget_audit(
    store: SearchStore, campaign_id: str, budget: dict | None
) -> str | None:
    """Close-time audit: recorded spend must not exceed the carried
    budget. Per arm, spend = spawned programmes × trials_per_programme
    compared against programmes_per_arm × trials_per_programme — and
    spawn count itself is the harder bound (enforced at spawn). This
    check catches campaigns whose spawn records somehow outgrew the
    carried budget."""
    budget = budget or {}
    ppa = budget.get("programmes_per_arm")
    tpp = budget.get("trials_per_programme")
    if ppa is None or tpp is None:
        return check_campaign_budget_carryable(budget)
    for arm in ("champion", "challenger"):
        spawned = len(store.list_campaign_spawns(campaign_id, arm=arm))
        if spawned > int(ppa):
            return (
                f"budget audit failed: campaign {campaign_id} arm "
                f"'{arm}' recorded {spawned} spawned programmes "
                f"against programmes_per_arm={ppa}."
            )
    return None
