"""Loop-1 campaign orchestration — the six governed verbs.

A tournament arm drives a descendant Loop-1 campaign through the
dedicated ``loop1_orchestration`` channel — whitelisted to the
campaign-lifecycle verbs, separate from the read-only ``loop1``
evidence channel. Each verb resolves the (tournament, arm) →
campaign link recorded at open time; the link row is the audit
trail that the tournament *did* orchestrate this campaign, not
client-asserted prose.

When the channel is unwired these tools fail clearly and the
client-driven tournament path (record_tournament_result, etc.) is
untouched.
"""

from __future__ import annotations

import json
import uuid

from ..enforcement.checks import (
    check_arm_valid,
    check_orchestration_tool_whitelisted,
    check_tournament_budget_carryable,
    check_tournament_open,
)
from ..state.models import (
    EvidenceContext,
    EvidenceRef,
    EvidenceSource,
    TournamentArm,
    TournamentCampaign,
)
from ..state.store import ImproverStore
from .evidence import _extract_ref_ids
from mcp.types import CallToolResult
from .schemas import coerce_json, fail, ok, OpenArmCampaignOut, CloseArmCampaignOut, PullArmEvidenceOut, RecordArmResultOut, RecordArmVerdictOut, SpawnArmProgrammeOut
from typing import Annotated, Literal
from pydantic import Field


def _err(e: str | None) -> CallToolResult | None:
    if e:
        return fail(json.dumps({"error": e}))
    return None


def _parse_upstream(payload: str):
    """Parse an upstream JSON payload; error payloads return (None, err)."""
    try:
        data = json.loads(payload) if isinstance(payload, str) else payload
    except ValueError:
        return None, "upstream returned a non-JSON payload"
    if isinstance(data, dict) and data.get("error"):
        return None, str(data["error"])
    return data, None


def _channel(adaptors):
    """The live loop1_orchestration adaptor, or an error string —
    distinguishing never-wired from configured-but-still-down."""
    adaptor = adaptors.loop1_orchestration
    if adaptor is not None:
        return adaptor, None
    if "loop1_orchestration" in getattr(adaptors, "_channels", {}):
        return None, (
            "loop1_orchestration channel is configured but not yet "
            "connected — the connectivity supervisor is retrying the "
            "upstream; try again shortly."
        )
    return None, (
        "No loop1_orchestration adaptor configured — orchestrated "
        "campaigns require [adaptors.loop1_orchestration] in "
        "ml-arete.toml. The client-driven tournament path remains "
        "available without it."
    )


def _link_or_err(store: ImproverStore, tournament_id: str, arm: str):
    """Resolve the (tournament, arm) → campaign link; (link, err)."""
    link = store.get_tournament_campaign(tournament_id, arm)
    if link is None:
        return None, (
            f"No campaign linked to tournament {tournament_id} arm "
            f"'{arm}' — open_arm_campaign first."
        )
    return link, None


async def _push(adaptors, tool: str, args: dict):
    """Whitelist check + channel resolution + upstream call, parsed."""
    if e := check_orchestration_tool_whitelisted(tool):
        return None, e
    adaptor, err = _channel(adaptors)
    if err:
        return None, err
    return _parse_upstream(await adaptor.push(tool, args))


def register(mcp, store: ImproverStore, adaptors) -> None:
    """Register the Loop-1 orchestration tools on the MCP server."""

    @mcp.tool()
    async def open_arm_campaign(
        tournament_id: Annotated[str, Field(description='ID of the target tournament.')],
        arm: Annotated[Literal['parent', 'candidate'], Field(description="Tournament arm whose descendant campaign to open: 'parent' | 'candidate'. One campaign per arm.")],
        upstream_contract_id: Annotated[str, Field(description="The upstream evaluation contract the arm's campaign is scored under.")],
        challenger_id: Annotated[str, Field(description="Roster ID of the challenger arm's candidate.")],
        seeds: Annotated[list | str | None, Field(description='Seed set for the arm (list of ints); may be JSON-encoded.')] = None,
    ) -> Annotated[CallToolResult, OpenArmCampaignOut]:
        """Open the descendant Loop-1 campaign for a tournament arm.

        Pushes open_campaign through the orchestration channel with
        the tournament's budget carried verbatim — the caller cannot
        redefine it. Records a tournament_campaigns link so the
        correlation is a data row, not prose. One campaign per arm:
        a retry on an already-linked arm returns the existing link
        rather than multiplying campaigns upstream.
        """
        try:
            if e := check_arm_valid(arm):
                return _err(e)
            e, tournament = check_tournament_open(store, tournament_id)
            if e:
                return _err(e)
            if e := check_tournament_budget_carryable(tournament.budget):
                return _err(e)
            if seeds is not None:
                seeds = coerce_json(seeds, list, "seeds")

            existing = store.get_tournament_campaign(tournament_id, arm)
            if existing is not None:
                return ok({
                    "campaign_id": existing.campaign_id,
                    "tournament_id": tournament_id,
                    "arm": arm,
                    "link_id": existing.id,
                    "status": "already_linked",
                })

            data, err = await _push(adaptors, "open_campaign", {
                "contract_id": upstream_contract_id,
                "challenger_id": challenger_id,
                "budget": tournament.budget,
                "seeds": seeds if seeds is not None else (
                    tournament.seeds or []
                ),
            })
            if err:
                return fail(json.dumps({"error": err}))
            campaign_id = (data or {}).get("campaign_id")
            if not campaign_id:
                return fail(json.dumps({
                    "error": "upstream open_campaign returned no "
                    "campaign_id"
                }))

            link = TournamentCampaign(
                id=f"tcamp-{uuid.uuid4().hex[:8]}",
                tournament_id=tournament_id,
                arm=TournamentArm(arm),
                campaign_id=campaign_id,
                upstream_contract_id=upstream_contract_id,
                challenger_id=challenger_id,
            )
            store.create_tournament_campaign(link)
            return ok({
                "campaign_id": campaign_id,
                "tournament_id": tournament_id,
                "arm": arm,
                "link_id": link.id,
                "status": "opened",
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    async def spawn_arm_programme(
        tournament_id: Annotated[str, Field(description='ID of the target tournament.')],
        arm: Annotated[Literal['parent', 'candidate'], Field(description="Tournament arm to spawn under: 'parent' | 'candidate'.")],
        goal: Annotated[str, Field(description='Research goal for the spawned programme.')],
        constraints: Annotated[dict | str, Field(description='Typed constraint fields {gpu_memory_gb, max_training_hours_per_trial, max_parameter_count} for the spawned programme; may be JSON-encoded.')],
        allowed_variables: Annotated[list | str, Field(description='Variables the programme may search/vary; list or JSON-encoded list.')],
        campaign_arm: Annotated[Literal['champion', 'challenger'], Field(description="Which side of the upstream campaign the programme spawns for (default challenger — the arm's descendant).")] = "challenger",
    ) -> Annotated[CallToolResult, SpawnArmProgrammeOut]:
        """Spawn a descendant Loop-0 programme for a tournament arm.

        Resolves the arm's linked campaign and pushes
        spawn_campaign_programme upstream — Loop 1 enforces the
        programmes_per_arm cap, resolves metric_direction from the
        campaign's evaluation contract (never caller-supplied), and
        records the durable campaign_spawns row. campaign_arm selects
        which side of the upstream campaign the programme spawns for
        (default challenger — the arm's descendant).
        """
        try:
            if e := check_arm_valid(arm):
                return _err(e)
            if e := check_tournament_open(store, tournament_id)[0]:
                return _err(e)
            link, err = _link_or_err(store, tournament_id, arm)
            if err:
                return fail(json.dumps({"error": err}))
            constraints = coerce_json(constraints, dict, "constraints")
            allowed_variables = coerce_json(
                allowed_variables, list, "allowed_variables"
            )
            if campaign_arm not in ("champion", "challenger"):
                return fail(json.dumps({
                    "error": "campaign_arm must be one of "
                    f"champion|challenger, got: {campaign_arm}"
                }))

            data, err = await _push(
                adaptors, "spawn_campaign_programme", {
                    "campaign_id": link.campaign_id,
                    "arm": campaign_arm,
                    "goal": goal,
                    "constraints": constraints,
                    "allowed_variables": allowed_variables,
                },
            )
            if err:
                return fail(json.dumps({"error": err}))
            return ok({
                "tournament_id": tournament_id,
                "arm": arm,
                "campaign_id": link.campaign_id,
                "spawn": data,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    async def pull_arm_evidence(
        tournament_id: Annotated[str, Field(description='ID of the target tournament.')],
        arm: Annotated[Literal['parent', 'candidate'], Field(description="Tournament arm the pull is scoped to: 'parent' | 'candidate'.")],
        source: Annotated[Literal['loop0', 'loop1', 'anamnesis'], Field(description='Evidence source — the upstream read surface to pull through.')],
        tool: Annotated[Literal['assess_programme', 'get_archive', 'get_archived_programme', 'get_campaign', 'get_candidate_lineage', 'get_claim', 'get_incumbent', 'get_investigation', 'get_trial_status', 'list_active_programmes', 'list_archives', 'list_campaigns', 'list_candidates', 'list_claims', 'list_hypotheses', 'list_investigations', 'list_trials', 'recall'], Field(description="Upstream read tool to call — must be on the source's read whitelist (the evidence channel is read-only).")],
        args: Annotated[dict | str | None, Field(description='Arguments forwarded to the upstream tool; object or JSON-encoded.')] = None,
    ) -> Annotated[CallToolResult, PullArmEvidenceOut]:
        """Campaign-scoped evidence pull for a tournament arm.

        Pushes pull_campaign_evidence through the orchestration
        channel — the upstream call is logged as a campaign-scoped
        evidence ref in Loop 1, and this tool additionally logs a
        tournament-scoped evidence_ref locally so meta-decisions can
        cite the consultation.
        """
        try:
            if e := check_arm_valid(arm):
                return _err(e)
            link, err = _link_or_err(store, tournament_id, arm)
            if err:
                return fail(json.dumps({"error": err}))
            args = coerce_json(args, dict, "args") if args else {}

            data, err = await _push(
                adaptors, "pull_campaign_evidence", {
                    "campaign_id": link.campaign_id,
                    "source": source,
                    "tool": tool,
                    "args": args,
                },
            )
            if err:
                return fail(json.dumps({"error": err}))

            upstream_ref = (data or {}).get("evidence_ref_id")
            ref_ids = list((data or {}).get("ref_ids") or [])
            if upstream_ref and upstream_ref not in ref_ids:
                ref_ids.insert(0, upstream_ref)
            ref = EvidenceRef(
                id=f"eref-{uuid.uuid4().hex[:8]}",
                context_type=EvidenceContext.tournament,
                context_id=tournament_id,
                source=EvidenceSource.loop1,
                tool="pull_campaign_evidence",
                args={
                    "campaign_id": link.campaign_id,
                    "source": source,
                    "tool": tool,
                    "args": args,
                },
                ref_ids=ref_ids,
            )
            store.create_evidence_ref(ref)
            return ok({
                "evidence_ref_id": ref.id,
                "upstream_evidence_ref_id": upstream_ref,
                "ref_ids": ref_ids,
                "result": (data or {}).get("result"),
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    async def record_arm_result(
        tournament_id: Annotated[str, Field(description='ID of the target tournament.')],
        arm: Annotated[Literal['parent', 'candidate'], Field(description="Tournament arm the result belongs to: 'parent' | 'candidate'.")],
        programme_id: Annotated[str, Field(description='ID of the target research programme.')],
        metrics: Annotated[dict | str, Field(description="Result metrics pushed to the arm's campaign — must carry the contract's primary_metric; may be JSON-encoded.")],
        campaign_arm: Annotated[Literal['champion', 'challenger'], Field(description="Which side of the upstream campaign the programme spawns for (default challenger — the arm's descendant).")] = "challenger",
    ) -> Annotated[CallToolResult, RecordArmResultOut]:
        """Report an executed descendant programme to the arm's campaign.

        Pushes record_campaign_result upstream — Loop 1 rejects any
        programme that was not spawned under this campaign (and this
        campaign-side arm), so a result can only ever count for the
        programme the orchestrated campaign created.
        """
        try:
            if e := check_arm_valid(arm):
                return _err(e)
            link, err = _link_or_err(store, tournament_id, arm)
            if err:
                return fail(json.dumps({"error": err}))
            metrics = coerce_json(metrics, dict, "metrics")
            if campaign_arm not in ("champion", "challenger"):
                return fail(json.dumps({
                    "error": "campaign_arm must be one of "
                    f"champion|challenger, got: {campaign_arm}"
                }))

            data, err = await _push(
                adaptors, "record_campaign_result", {
                    "campaign_id": link.campaign_id,
                    "arm": campaign_arm,
                    "programme_id": programme_id,
                    "metrics": metrics,
                },
            )
            if err:
                return fail(json.dumps({"error": err}))
            return ok({
                "tournament_id": tournament_id,
                "arm": arm,
                "campaign_id": link.campaign_id,
                "result": data,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    async def close_arm_campaign(
        tournament_id: Annotated[str, Field(description='ID of the target tournament.')], arm: Annotated[Literal['parent', 'candidate'], Field(description="Tournament arm whose campaign to close: 'parent' | 'candidate'.")]
    ) -> Annotated[CallToolResult, CloseArmCampaignOut]:
        """Close the arm's linked campaign and freeze its score.

        Pushes close_campaign upstream — Loop 1 audits the recorded
        spend against the carried budget before freezing; an
        overspent campaign rejects the close. Returns the upstream
        promotion_score for the arm.
        """
        try:
            if e := check_arm_valid(arm):
                return _err(e)
            link, err = _link_or_err(store, tournament_id, arm)
            if err:
                return fail(json.dumps({"error": err}))

            data, err = await _push(
                adaptors, "close_campaign",
                {"campaign_id": link.campaign_id},
            )
            if err:
                return fail(json.dumps({"error": err}))
            return ok({
                "tournament_id": tournament_id,
                "arm": arm,
                "campaign_id": link.campaign_id,
                "result": data,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    async def record_arm_verdict(
        tournament_id: Annotated[str, Field(description='ID of the target tournament.')],
        arm: Annotated[Literal['parent', 'candidate'], Field(description="Tournament arm the verdict closes out: 'parent' | 'candidate'.")],
        verdict: Annotated[Literal['promote', 'retain', 'rollback'], Field(description="promote | retain | rollback — the descendant candidate's promotion verdict (Loop-1 lineage).")],
        decided_by: Annotated[str, Field(description="Attributable decider (e.g. 'human:<name>' or a protocol/improver id).")],
        evidence_ref_ids: Annotated[list | str | None, Field(description='Evidence_ref IDs minted by pull_evidence/pull_arm_evidence calls; list or JSON-encoded.')] = None,
        rationale: Annotated[str | None, Field(description='Non-empty justification — accountability is first-class.')] = None,
    ) -> Annotated[CallToolResult, RecordArmVerdictOut]:
        """Record the descendant campaign's promotion verdict.

        Pushes record_promotion_verdict through the orchestration
        channel — the decision concerns the descendant candidate
        (Loop-1 lineage), NOT this tournament's improver. Arete
        improver promotion stays governed by record_meta_decision /
        promote_policy; this verb closes out the descendant side.
        evidence_ref_ids are the campaign-scoped erefs minted
        upstream by pull_arm_evidence calls.
        """
        try:
            if e := check_arm_valid(arm):
                return _err(e)
            link, err = _link_or_err(store, tournament_id, arm)
            if err:
                return fail(json.dumps({"error": err}))
            if evidence_ref_ids is not None:
                evidence_ref_ids = coerce_json(
                    evidence_ref_ids, list, "evidence_ref_ids"
                )

            data, err = await _push(
                adaptors, "record_promotion_verdict", {
                    "campaign_id": link.campaign_id,
                    "verdict": verdict,
                    "decided_by": decided_by,
                    "evidence_ref_ids": evidence_ref_ids,
                    "rationale": rationale,
                },
            )
            if err:
                return fail(json.dumps({"error": err}))
            return ok({
                "tournament_id": tournament_id,
                "arm": arm,
                "campaign_id": link.campaign_id,
                "result": data,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))
