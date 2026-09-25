"""Programme tool handlers — create_programme, update_metric_direction,
conclude_hypothesis, close_programme."""

from __future__ import annotations

import asyncio
import json
import uuid

from ..state.models import Conclusion, Programme, Verdict
from ..state.store import StateStore
from ..clients.adaptor import MCPAdaptor
from ..enforcement.commitments import (
    check_duplicate_conclusion,
    check_duplicate_programme,
    check_evidence_exists,
    check_all_results_recorded,
    check_hypothesis_in_programme,
    check_belief_recorded,
    check_programme_active,
    check_programme_is_research,
    check_programme_has_trials,
    check_programme_candidate_exists,
)
from .schemas import BudgetInput, ConstraintsInput, coerce_json, fail, ok, CloseProgrammeOut, ConcludeHypothesisOut, CreateProgrammeOut, ListActiveProgrammesOut, ListProgrammesOut, UpdateMetricDirectionOut
from typing import Annotated, Literal
from pydantic import Field
from mcp.types import CallToolResult


# Verdict → claim confidence for Phase-3a minting. Claim confidence
# tracks strength of evidence for the *finding*, not the verdict
# (Popper: a clean falsification is positive knowledge, not a weak
# claim). Accepted and rejected are symmetric when evidence is decisive;
# inconclusive mints nothing — an undecided test is methodological
# knowledge about the regime's power, not an empirical claim.
VERDICT_CONFIDENCE = {"accepted": 0.85, "rejected": 0.85}
VERDICT_FINDING = {"accepted": "holds", "rejected": "does not hold"}


def register(
    mcp, store: StateStore, adaptor: MCPAdaptor, archiver=None,
    claims_config: dict | None = None,
    require_candidate_attribution: bool = False,
) -> None:
    """Register programme tools. claims_config is the [claims] table —
    call_timeout_seconds bounds upstream mint calls so a claims outage
    can never hang a verdict. require_candidate_attribution (the
    [session] flag) makes candidate_version_id mandatory on
    create_programme — the attribution gate behind the warmup
    protocol."""
    _claims_call_timeout = float(
        (claims_config or {}).get("call_timeout_seconds", 10.0)
    )
    """Register programme-related tools on the MCP server.

    Args:
        archiver: Optional Archiver instance. If provided, close_programme
            will automatically archive the programme after closing.
    """

    @mcp.tool()
    async def create_programme(
        goal: Annotated[str, Field(description='Research goal — what the programme is trying to learn.')],
        constraints: Annotated[dict | str, Field(description='Typed constraint fields {gpu_memory_gb, max_training_hours_per_trial, max_parameter_count} — extra keys allowed (commitment 4); may be JSON-encoded.')],
        allowed_variables: Annotated[list[str] | str, Field(description='Variables the programme may search/vary; list or JSON-encoded list.')],
        budget: Annotated[dict | str, Field(description='Budget object {max_trials (required), max_wall_time_hours}; extra keys recorded but not enforced. May be a JSON-encoded string.')],
        metric_direction: Annotated[Literal['minimize', 'maximize'], Field(description="'minimize' (loss-like: perplexity, error) | 'maximize' (quality: accuracy, F1) — steers search and best-trial ranking.")] = "maximize",
        candidate_version_id: Annotated[str | None, Field(description='RSI Phase-0 correlation — which registered candidate created this programme; must exist if given.')] = None,
    ) -> Annotated[CallToolResult, CreateProgrammeOut]:
        """Create a research programme with a goal, constraints, and budget.

        Constraints are typed fields, not strings (commitment 4).
        Wires the optimizer role for this programme.

        Structured params (constraints, allowed_variables, budget) may be
        sent as JSON-encoded strings if your client cannot emit objects.

        metric_direction: 'minimize' (e.g. perplexity, loss) or 'maximize' (e.g. accuracy).
        The optimizer uses this to steer search and rank best trials.

        candidate_version_id: optional RSI Phase 0 correlation — which
        registered candidate created this programme. Must exist if given.
        """
        try:
            constraints = coerce_json(constraints, dict, "constraints")
            allowed_variables = coerce_json(allowed_variables, list, "allowed_variables")
            budget = coerce_json(budget, dict, "budget")
            c = ConstraintsInput(**constraints)
            b = BudgetInput(**budget)
            if metric_direction not in ("minimize", "maximize"):
                return fail(json.dumps({"error": "metric_direction must be 'minimize' or 'maximize'"}))

            # Enforcement: commitment 8 — programmes, not runs (no duplicates)
            err = check_duplicate_programme(goal, store)
            if err:
                return fail(json.dumps({"error": err}))

            # Enforcement: commitment 8 — programmes, not runs (research only)
            err = check_programme_is_research(goal)
            if err:
                return fail(json.dumps({"error": err}))

            # Enforcement: warmup protocol — when the deployment
            # requires attribution, an anonymous programme is refused.
            if require_candidate_attribution and candidate_version_id is None:
                return fail(json.dumps({
                    "error": "candidate_version_id is required — this "
                    "deployment sets require_candidate_attribution. "
                    "register_candidate for the agent (carry the operating "
                    "document's digest in harness_artifact_digest) and "
                    "attribute the programme to it. See protocol://session "
                    "warmup."
                }))

            # Enforcement: RSI Phase 0 — candidate attribution must be real
            err = check_programme_candidate_exists(candidate_version_id, store)
            if err:
                return fail(json.dumps({"error": err}))

            # Extra budget keys are preserved under 'budget_extras' in
            # the constraints record but are NOT enforced — only
            # max_trials and max_wall_time_hours have enforcement
            # semantics. The response admits which keys were recorded
            # but not enforced so nothing is silently dropped.
            constraints_dict = c.model_dump()
            budget_extras = getattr(b, "model_extra", None) or {}
            if budget_extras:
                constraints_dict["budget_extras"] = dict(budget_extras)

            programme = Programme(
                id=f"prog-{uuid.uuid4().hex[:8]}",
                goal=goal,
                constraints=constraints_dict,
                allowed_variables=allowed_variables,
                budget_max_trials=b.max_trials,
                budget_max_wall_time_hours=b.max_wall_time_hours,
                metric_direction=metric_direction,
                candidate_version_id=candidate_version_id,
            )
            store.create_programme(programme)

            # Wire downstream roles — pass direction to optimizer
            await adaptor.optimizer.create_study(
                programme.id, allowed_variables, metric_direction
            )

            result = {"programme_id": programme.id, "status": "created"}
            if budget_extras:
                result["budget_extras_not_enforced"] = sorted(budget_extras)
            return ok(result)
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def list_active_programmes() -> Annotated[CallToolResult, ListActiveProgrammesOut]:
        """List all active research programmes.

        Call this BEFORE create_programme to check whether a programme
        already covers your goal. If one exists, add a hypothesis to it
        (formulate_hypothesis) instead of creating a duplicate programme.
        """
        try:
            # Raw dicts, not Programme objects — access via p["id"] etc.
            programmes = store.list_programmes_paginated(
                status_filter="active", per_page=1000
            )
            result = [
                {
                    "programme_id": p["id"],
                    "goal": p["goal"][:120],
                    "created_at": p["created_at"],
                    "hypotheses": len(store.list_hypotheses(p["id"])),
                    "trials": len(store.list_trials(p["id"])),
                }
                for p in programmes
            ]
            return ok({
                "active_programmes": result,
                "count": len(result),
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def list_programmes(
        candidate_version_id: Annotated[str | None, Field(description='RSI Phase-0 correlation — which registered candidate created this programme; must exist if given.')] = None,
        status: Annotated[Literal['active', 'completed', 'abandoned', 'archived'] | None, Field(description='Optional status filter.')] = None,
        limit: Annotated[int, Field(description='Max rows to return (pagination).')] = 50,
        offset: Annotated[int, Field(description='Rows to skip before returning (pagination).')] = 0,
    ) -> Annotated[CallToolResult, ListProgrammesOut]:
        """List programmes with optional attribution/status filters (read-only).

        candidate_version_id filters to programmes created under that
        candidate (the RSI Phase-0 correlation) — this is how a
        candidate's descendants are enumerated. Each row includes
        candidate_version_id. Unlike list_active_programmes this is a
        paginated general listing across all statuses.
        """
        try:
            programmes = store.list_programmes_paginated(
                page=1,
                per_page=offset + limit,
                status_filter=status,
                candidate_version_id=candidate_version_id,
            )[offset:]
            return ok({
                "programmes": [
                    {
                        "programme_id": p["id"],
                        "goal": p["goal"][:120],
                        "status": p["status"],
                        "candidate_version_id": p["candidate_version_id"],
                        "metric_direction": p["metric_direction"],
                        "created_at": p["created_at"],
                    }
                    for p in programmes
                ],
                "total": store.count_programmes(
                    status_filter=status,
                    candidate_version_id=candidate_version_id,
                ),
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def update_metric_direction(programme_id: Annotated[str, Field(description='ID of the target research programme.')], metric_direction: Annotated[Literal['minimize', 'maximize'], Field(description="'minimize' (loss-like: perplexity, error) | 'maximize' (quality: accuracy, F1) — steers search and best-trial ranking.")]) -> Annotated[CallToolResult, UpdateMetricDirectionOut]:
        """Update the optimization direction for an existing programme.

        Use 'minimize' for loss-like metrics (perplexity, error rate) or
        'maximize' for quality metrics (accuracy, F1). The optimizer uses this
        to rank best trials and steer the search.

        This is needed for programmes created before the metric_direction
        parameter was added to create_programme.
        """
        try:
            if metric_direction not in ("minimize", "maximize"):
                return fail(json.dumps({"error": "metric_direction must be 'minimize' or 'maximize'"}))
            programme = store.get_programme(programme_id)
            err = check_programme_active(programme)
            if err:
                return fail(json.dumps({"error": err}))
            store.update_programme_direction(programme_id, metric_direction)
            return ok({
                "programme_id": programme_id,
                "metric_direction": metric_direction,
                "status": "updated",
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    async def conclude_hypothesis(
        programme_id: Annotated[str, Field(description='ID of the target research programme.')],
        hypothesis_id: Annotated[str, Field(description='ID of the target hypothesis.')],
        verdict: Annotated[Literal['accepted', 'rejected', 'inconclusive'], Field(description='accepted | rejected | inconclusive — creates an immutable conclusion.')],
        evidence_summary: Annotated[str, Field(description='Summary of the evidence the verdict rests on.')],
    ) -> Annotated[CallToolResult, ConcludeHypothesisOut]:
        """Conclude a hypothesis by accepting/rejecting it.

        Creates an immutable conclusion (commitment 8: programmes, not runs).
        When a claims role is wired, also mints a semantic claim with
        provenance edges — a byproduct, never a gate on the verdict.
        Enforcement: commitment 8 — rejects duplicate conclusions for the same hypothesis.
        Enforcement: commitment 10 — rejects if hypothesis not found or not in this programme.
        Enforcement: commitment 1 — rejects if no completed trials for the hypothesis.
        Enforcement: commitment 7 — rejects if any completed trial has no observations.
        """
        try:
            # Enforcement: commitment 10 — hypothesis exists and belongs to programme
            err = check_hypothesis_in_programme(programme_id, hypothesis_id, store)
            if err:
                return fail(json.dumps({"error": err}))

            # Enforcement: commitment 1 — a closed programme is immutable
            err = check_programme_active(store.get_programme(programme_id))
            if err:
                return fail(json.dumps({"error": err}))

            # Enforcement: commitment 8 — programmes, not runs (no duplicate conclusions)
            err = check_duplicate_conclusion(programme_id, hypothesis_id, store)
            if err:
                return fail(json.dumps({"error": err}))

            # Enforcement: commitment 1 — the loop is the unit (no conclusion without evidence)
            err = check_evidence_exists(programme_id, hypothesis_id, store)
            if err:
                return fail(json.dumps({"error": err}))

            # Enforcement: commitment 7 — reproducibility (all results must be recorded)
            err = check_all_results_recorded(programme_id, hypothesis_id, store)
            if err:
                return fail(json.dumps({"error": err}))

            # Enforcement: commitment 2 — memory precedes optimization (belief must be updated)
            err = check_belief_recorded(programme_id, store)
            if err:
                return fail(json.dumps({"error": err}))

            conclusion = Conclusion(
                id=f"conc-{uuid.uuid4().hex[:8]}",
                hypothesis_id=hypothesis_id,
                programme_id=programme_id,
                verdict=Verdict(verdict),
                evidence_ref=evidence_summary,
                evidence_summary=evidence_summary,
            )
            store.create_conclusion(conclusion)

            # Update the hypothesis status to match the verdict
            if verdict == "accepted":
                store.update_hypothesis_status(hypothesis_id, "accepted")
            elif verdict == "rejected":
                store.update_hypothesis_status(hypothesis_id, "rejected")
            else:
                store.update_hypothesis_status(hypothesis_id, "inconclusive")

            result = {"conclusion_id": conclusion.id, "verdict": verdict, "status": "closed"}

            # Mint a semantic claim as a byproduct (Phase 3a). The claim
            # is the *finding*, not the conjecture: accepted mints the
            # affirmed statement, rejected mints the falsification (the
            # negation is the knowledge), inconclusive mints nothing —
            # an undecided test yields no empirical claim. Provenance:
            # tested_by->trials (the process tested), derived_from->
            # conclusion (the verdict record), supports->observations
            # (the grounding data items). A claims outage must never
            # block a verdict — failures are reported, not raised.
            if adaptor.claims is not None:
                if verdict not in VERDICT_FINDING:
                    result["claim_status"] = "skipped"
                else:
                    try:
                        hypothesis = store.get_hypothesis(hypothesis_id)
                        programme = store.get_programme(programme_id)
                        completed = [
                            t for t in store.list_trials(programme_id)
                            if t.hypothesis_id == hypothesis_id
                            and t.status.value == "completed"
                        ]
                        regime = ", ".join(
                            sorted({t.config_json for t in completed})
                        )
                        observation_ids = [
                            o.id
                            for t in completed
                            for o in store.list_observations(t.id)
                        ]
                        finding = VERDICT_FINDING[verdict]
                        # Bounded call: a claims outage must never block
                        # a verdict — not even by hanging. A dead server
                        # can leave the MCP read pending indefinitely.
                        result["claim_id"] = await asyncio.wait_for(
                            adaptor.claims.assert_claim(
                            content=(
                                f"{hypothesis.statement} — {finding} "
                                f"under {regime}: {evidence_summary}"
                            ),
                            type="empirical",
                            confidence=VERDICT_CONFIDENCE[verdict],
                            evidence=[
                                {
                                    "to_ref": conclusion.id,
                                    "ref_type": "conclusion",
                                    "relation": "derived_from",
                                },
                                *[
                                    {
                                        "to_ref": t.id,
                                        "ref_type": "trial",
                                        "relation": "tested_by",
                                    }
                                    for t in completed
                                ],
                                *[
                                    {
                                        "to_ref": o,
                                        "ref_type": "observation",
                                        "relation": "supports",
                                    }
                                    for o in observation_ids
                                ],
                            ],
                            source_id=(
                                programme.candidate_version_id
                                if programme and programme.candidate_version_id
                                else programme_id
                            ),
                            ),
                            timeout=_claims_call_timeout,
                        )
                        result["claim_status"] = "minted"
                    except Exception as e:
                        result["claim_status"] = "failed"
                        result["claim_error"] = str(e)

            return ok(result)
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def close_programme(
        programme_id: Annotated[str, Field(description='ID of the target research programme.')],
        status: Annotated[Literal['completed', 'abandoned'], Field(description="completed | abandoned — 'completed' requires all hypotheses concluded; 'abandoned' sweeps the rest.")] = "completed",
    ) -> Annotated[CallToolResult, CloseProgrammeOut]:
        """Close a programme by setting its status to completed or abandoned.

        For status="completed": rejects if any hypothesis is still
        under_test — the agent must call conclude_hypothesis first.
        Auto-marks proposed → abandoned (no effort was expended).

        For status="abandoned": auto-marks all unconcluded hypotheses
        as abandoned (the whole programme is being abandoned).

        Enforcement: state machine — rejects if programme is not active.
        Enforcement: commitment 1 — the loop is the unit (no closing
        with unconcluded hypotheses when completing).
        """
        try:
            programme = store.get_programme(programme_id)
            if programme is None:
                return fail(json.dumps({"error": f"Programme not found: {programme_id}"}))

            if status not in ("completed", "abandoned"):
                return fail(json.dumps({
                    "error": f"Invalid programme status: {status}. "
                             f"Must be 'completed' or 'abandoned'."
                }))

            # Fetch trials once — reused by the zero-trial gate below
            # and the running-trial check further down.
            trials = store.list_trials(programme_id)

            # Enforcement: commitment 1 — the loop is the unit (no empty
            # completion). Runs BEFORE any state mutation below so a
            # rejected close leaves the programme untouched.
            if status == "completed":
                err = check_programme_has_trials(programme_id, trials)
                if err:
                    return fail(json.dumps({"error": err}))

            hypotheses = store.list_hypotheses(programme_id)
            auto_marked = []
            for h in hypotheses:
                if h.status.value == "proposed":
                    store.update_hypothesis_status(h.id, "abandoned")
                    auto_marked.append({
                        "hypothesis_id": h.id,
                        "from": "proposed",
                        "to": "abandoned",
                    })
                elif h.status.value == "under_test":
                    if status == "abandoned":
                        store.update_hypothesis_status(h.id, "abandoned")
                        auto_marked.append({
                            "hypothesis_id": h.id,
                            "from": "under_test",
                            "to": "abandoned",
                        })
                    else:
                        # Completing a programme with unconcluded
                        # hypotheses is forbidden — the agent must
                        # call conclude_hypothesis first. Auto-marking
                        # as inconclusive would record a false verdict.
                        return fail(json.dumps({
                            "error": (
                                f"Cannot complete programme: hypothesis "
                                f"{h.id} is still under_test. Call "
                                f"conclude_hypothesis with a verdict "
                                f"(accepted/rejected/inconclusive) first."
                            ),
                        }))

            # Enforcement: no closing with running trials (Rule 5.6 —
            # a running trial is an occurrent that hasn't reached its
            # end-boundary; archiving it would freeze an incomplete
            # process as if it were a complete continuant).
            # `trials` was already fetched above.
            trials_auto_marked = []
            for t in trials:
                if t.status.value == "running":
                    return fail(json.dumps({
                        "error": (
                            f"Cannot close programme: trial {t.id} is still "
                            f"running. Call cancel_trial first (running → "
                            f"failed) or wait for it to complete."
                        ),
                    }))
                elif t.status.value == "designed":
                    # Auto-mark designed trials as abandoned (no effort
                    # was expended — like proposed hypotheses)
                    store.update_trial_status(t.id, "abandoned")
                    trials_auto_marked.append({
                        "trial_id": t.id,
                        "from": "designed",
                        "to": "abandoned",
                    })

            store.update_programme_status(programme_id, status)
            result = {
                "programme_id": programme_id,
                "status": status,
                "auto_marked": auto_marked,
                "trials_auto_marked": trials_auto_marked,
            }

            # Phase 3: automatic archiving on terminal state
            if archiver is not None:
                try:
                    archive_result = archiver.archive_programme(programme_id)
                    result["archived"] = archive_result
                except Exception as e:
                    # Archiving failed — the programme is still closed,
                    # but it remains in the live DB. Log the error.
                    result["archive_error"] = str(e)

            return ok(result)
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))
