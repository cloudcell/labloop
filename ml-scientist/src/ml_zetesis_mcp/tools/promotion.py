"""Promotion tool handlers — the Loop-1 population machinery.

The roster mirrors upstream Loop-0 candidate_versions; campaigns run
champion/challenger comparisons under a pre-registered contract; the
verdict writes back upstream through the promotion adaptor — the only
write channel in the ecosystem, whitelisted to the three insert-only
lineage surfaces. Decisions are records; rollback is a new verdict,
never a deletion.

Campaigns are an *optional* path — multi-programme orchestration for
tournaments that need a governed spawn/verdict loop. Direct
record_tournament_result on arete is equally first-class: field
evidence (plan-20260926-0438Z, review R6) shows agents drive the
direct path by default; these tools orchestrate when campaigns are
actually wanted, not because tournaments require them.
"""

from __future__ import annotations

import json
import uuid

from ..enforcement.checks import (
    check_campaign_evidence_refs,
    check_campaign_exists,
    check_campaign_open,
    check_close_budget_audit,
    check_promotion_tool_whitelisted,
    check_roster_entry,
    check_source_valid,
    check_spawn_cap,
    check_spawn_scoped_result,
    check_tool_whitelisted,
    check_verdict_decided_by,
)
from ..state.models import (
    CampaignArm,
    CampaignResult,
    CampaignSpawn,
    CampaignStatus,
    DerivedStatus,
    EvidenceRef,
    EvidenceSource,
    PolicyStatus,
    PromotionCampaign,
    RosterEntry,
    SearchPolicy,
    SpawnStatus,
)
from ..state.store import SearchStore
from .investigation import _extract_ref_ids, _ref_type_for
from mcp.types import CallToolResult
from .schemas import coerce_json, fail, ok, CloseCampaignOut, GetCampaignOut, ListCampaignsOut, ListSearchPoliciesOut, OpenCampaignOut, PullCampaignEvidenceOut, RecordCampaignResultOut, RecordPromotionVerdictOut, RefreshRosterOut, RegisterChallengerOut, RegisterSearchPolicyOut, SpawnCampaignProgrammeOut, GetIncumbentOut, ListCandidatesOut
from typing import Annotated, Literal
from pydantic import Field

VERDICTS = frozenset({"promote", "retain", "rollback"})


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


def _resolve_contract_direction(
    contract: dict, primary: str | None
) -> tuple[str | None, str | None]:
    """Resolve the programme's metric_direction from the upstream
    evaluation contract — authoritative, never caller-supplied. Two
    contract spellings are honoured: an explicit `direction` key, or
    the name→direction map form where the primary metric's value is
    the direction. Anything else fails loudly rather than defaulting
    to maximize — a wrong-signed programme silently ranks garbage."""
    metrics = contract.get("metrics") or {}
    raw = metrics.get("direction")
    if raw is None and primary:
        raw = metrics.get(primary)
    if raw in ("min", "minimize"):
        return "minimize", None
    if raw in ("max", "maximize"):
        return "maximize", None
    return None, (
        f"contract {contract.get('id')} carries no resolvable "
        "direction — declare metrics.direction (or use the "
        "name→direction map form) before spawning programmes."
    )


def _campaign_json(c: PromotionCampaign, store: SearchStore) -> dict:
    results = store.list_campaign_results(c.id)
    refs = store.list_evidence_refs(campaign_id=c.id)
    spawns = store.list_campaign_spawns(c.id)
    return {
        "campaign": {
            "id": c.id,
            "contract_id": c.contract_id,
            "champion_id": c.champion_id,
            "challenger_id": c.challenger_id,
            "primary_metric": c.primary_metric,
            "budget": c.budget,
            "seeds": c.seeds,
            "status": c.status.value,
            "promotion_score": c.promotion_score,
            "decision_id": c.decision_id,
            "claim_id": c.claim_id,
            "created_at": c.created_at,
            "closed_at": c.closed_at,
        },
        "results": [
            {
                "id": r.id, "arm": r.arm.value,
                "programme_id": r.programme_id,
                "metrics": r.metrics, "created_at": r.created_at,
            }
            for r in results
        ],
        "spawns": [
            {
                "id": s.id, "arm": s.arm.value,
                "programme_id": s.programme_id,
                "budget": s.budget, "status": s.status.value,
                "created_at": s.created_at,
            }
            for s in spawns
        ],
        "evidence_refs": [
            {
                "id": r.id, "source": r.source.value, "tool": r.tool,
                "args": r.args, "ref_ids": r.ref_ids,
                "created_at": r.created_at,
            }
            for r in refs
        ],
    }


def register(
    mcp,
    store: SearchStore,
    adaptors,
) -> None:
    """Register the promotion-lifecycle tools on the MCP server."""

    @mcp.tool()
    async def refresh_roster(
        dry_run: Annotated[bool, Field(description='When true, report what would change without mutating the roster. Required — pass false to apply.')]
    ) -> Annotated[CallToolResult, RefreshRosterOut]:
        """Reconcile the local roster with upstream Loop-0 candidates.

        Pulls list_candidates through the evidence channel and adopts
        every untracked candidate as a roster entry (status `candidate`);
        entries already present keep their annotated status. Then the
        champion pointer is reconciled against get_incumbent: entries
        marked champion that no longer match the upstream derivation
        are demoted, and the incumbent is annotated champion if
        tracked. This is the drift-healing step: upstream state that
        moved via paths the roster never saw shows up after refresh
        rather than diverging silently.

        dry_run=true performs the read-only pulls and reports
        {would_adopt, would_reconcile} without writing the roster.
        """
        try:
            if adaptors.evidence is None:
                return fail(json.dumps({
                    "error": "No evidence adaptor configured — cannot "
                    "refresh the roster. Wire [adaptors.evidence] in "
                    "ml-zetesis.toml."
                }))
            payload = await adaptors.evidence.pull("list_candidates", {})
            data, err = _parse_upstream(payload)
            if err:
                return fail(json.dumps({"error": err}))
            candidates = data.get("candidates", []) if data else []

            adopted, present = [], []
            for c in candidates:
                cid = c["id"]
                if store.get_roster_entry(cid) is None:
                    if not dry_run:
                        with store.transaction():
                            store.upsert_roster_entry(RosterEntry(
                                id=cid,
                                parent_id=c.get("parent_id"),
                                derived_status=DerivedStatus.candidate,
                                notes={"adopted_by": "refresh_roster"},
                            ))
                    adopted.append(cid)
                else:
                    present.append(cid)

            # Incumbent reconciliation: the roster champion mirrors
            # the upstream single-incumbent derivation. When the
            # pointer moved upstream via a path this server never
            # saw (a manual record_promotion_decision), healing means
            # re-pointing the annotation — adoption alone cannot.
            demoted: list[str] = []
            champion: str | None = None
            data, err = _parse_upstream(
                await adaptors.evidence.pull("get_incumbent", {})
            )
            incumbent_id = (
                None if err else (data or {}).get("candidate_id")
            )
            if incumbent_id is not None:
                stale = [
                    e for e in store.list_roster_entries(
                        status="champion"
                    )[0]
                    if e.id != incumbent_id
                ]
                entry = store.get_roster_entry(incumbent_id)
                promote = (
                    entry is not None
                    and entry.derived_status
                    is not DerivedStatus.champion
                )
                if dry_run:
                    demoted = [e.id for e in stale]
                    champion = incumbent_id if promote else None
                else:
                    with store.transaction():
                        for e in stale:
                            store.upsert_roster_entry(RosterEntry(
                                id=e.id,
                                parent_id=e.parent_id,
                                derived_status=DerivedStatus.candidate,
                                notes={
                                    "superseded_by": incumbent_id,
                                    "reconciled_by": "refresh_roster",
                                },
                            ))
                            demoted.append(e.id)
                        if promote:
                            store.upsert_roster_entry(RosterEntry(
                                id=incumbent_id,
                                parent_id=entry.parent_id,
                                derived_status=DerivedStatus.champion,
                                notes={"promoted_by": "refresh_roster"},
                            ))
                            champion = incumbent_id
            if dry_run:
                return ok({
                    "dry_run": True,
                    "upstream_candidates": len(candidates),
                    "would_adopt": adopted,
                    "already_tracked": len(present),
                    "incumbent": incumbent_id,
                    "would_reconcile": {
                        "demoted": demoted, "champion": champion
                    },
                })
            return ok({
                "upstream_candidates": len(candidates),
                "adopted": adopted,
                "already_tracked": len(present),
                "incumbent": incumbent_id,
                "reconciled": {
                    "demoted": demoted, "champion": champion
                },
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    async def register_challenger(
        candidate_id: Annotated[str | None, Field(description='Mode 1: ID of an existing upstream candidate to annotate as challenger (verified through the evidence channel).')] = None,
        parent_id: Annotated[str | None, Field(description='Parent version ID — omit for genesis; must name an existing row if given.')] = None,
        notes: Annotated[dict | str | None, Field(description='Free-form roster annotations for the challenger; object or JSON-encoded.')] = None,
        code_artifact_digest: Annotated[str | None, Field(description="sha256:<64 hex> digest of the version's code artifact — enforced upstream: must resolve to an ingested blob, or 'none'.")] = None,
        model_ref: Annotated[str | None, Field(description='Model identifier/reference backing this version.')] = None,
        capability_profile: Annotated[dict | str | None, Field(description='Tools/roles the version can use; object or JSON-encoded.')] = None,
        harness_artifact_digest: Annotated[str | None, Field(description="sha256:<64 hex> digest of the harness/evaluation artifact — enforced upstream: must resolve to an ingested blob; omit when there is none.")] = None,
        search_policy_ref: Annotated[str | None, Field(description='Reference to the search policy this version uses, if any.')] = None,
    ) -> Annotated[CallToolResult, RegisterChallengerOut]:
        """Mark a candidate as a challenger on the roster.

        Two modes. Given `candidate_id`: annotate an existing upstream
        candidate (verified through the evidence channel). Given the
        registration fields instead (code_artifact_digest, model_ref,
        capability_profile): create the candidate upstream through the
        promotion channel's whitelisted register_candidate, then
        annotate. Either way the roster annotates real lineage — it
        never mints candidates on its own.
        """
        try:
            if notes is not None:
                notes = coerce_json(notes, dict, "notes")
            if capability_profile is not None:
                capability_profile = coerce_json(
                    capability_profile, dict, "capability_profile"
                )

            if candidate_id is None:
                # Create path — the promotion channel's one mutating
                # role outside verdicts.
                missing = [
                    f for f, v in (
                        ("code_artifact_digest", code_artifact_digest),
                        ("model_ref", model_ref),
                        ("capability_profile", capability_profile),
                    ) if v is None
                ]
                if missing:
                    return fail(json.dumps({
                        "error": "registering a new challenger is missing "
                        f"required fields: {'|'.join(missing)} — or pass "
                        "candidate_id to annotate an existing upstream "
                        "candidate."
                    }))
                if adaptors.promotion is None:
                    return fail(json.dumps({
                        "error": "No promotion adaptor configured — "
                        "cannot register the candidate upstream. Wire "
                        "[adaptors.promotion] in ml-zetesis.toml."
                    }))
                if e := check_promotion_tool_whitelisted(
                    "register_candidate"
                ):
                    return _err(e)
                args = {
                    "code_artifact_digest": code_artifact_digest,
                    "model_ref": model_ref,
                    "capability_profile": capability_profile,
                }
                if parent_id is not None:
                    args["parent_id"] = parent_id
                if harness_artifact_digest is not None:
                    args["harness_artifact_digest"] = harness_artifact_digest
                if search_policy_ref is not None:
                    args["search_policy_ref"] = search_policy_ref
                data, err = _parse_upstream(
                    await adaptors.promotion.push("register_candidate", args)
                )
                if err:
                    return fail(json.dumps({"error": err}))
                candidate_id = (data or {}).get("candidate_id")
                if not candidate_id:
                    return fail(json.dumps({
                        "error": "upstream register_candidate returned "
                        "no candidate_id"
                    }))
                upstream_parent = parent_id
            else:
                # Annotate path — verify the candidate exists upstream.
                if adaptors.evidence is None:
                    return fail(json.dumps({
                        "error": "No evidence adaptor configured — "
                        "cannot verify the candidate exists upstream."
                    }))
                payload = await adaptors.evidence.pull(
                    "get_candidate", {"candidate_id": candidate_id}
                )
                data, err = _parse_upstream(payload)
                if err:
                    return fail(json.dumps({"error": err}))
                upstream_parent = (
                    data.get("candidate") or {}
                ).get("parent_id")
                if parent_id is not None and upstream_parent != parent_id:
                    return fail(json.dumps({
                        "error": "parent_id mismatch — upstream records "
                        f"{upstream_parent} as the parent of "
                        f"{candidate_id}; the roster annotates lineage, "
                        "it does not rewrite it."
                    }))

            store.upsert_roster_entry(RosterEntry(
                id=candidate_id,
                parent_id=upstream_parent,
                derived_status=DerivedStatus.challenger,
                notes=notes or {},
            ))
            return ok({
                "candidate_id": candidate_id,
                "derived_status": "challenger",
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def register_search_policy(
        name: Annotated[str, Field(description='Policy name — each call bumps the version and retires the previous active row.')],
        policy: Annotated[dict | str, Field(description='Policy definition object — versions are append-only under its name; may be JSON-encoded.')],
    ) -> Annotated[CallToolResult, RegisterSearchPolicyOut]:
        """Register a new version of a named search policy.

        Versions are append-only: each call bumps the version and
        retires the previous active row. This is the registry a Loop-2
        policy_version will eventually activate against — Loop 2
        legislates, Loop 1 executes.
        """
        try:
            if not name.strip():
                return fail(json.dumps({"error": "name must be non-empty"}))
            policy = coerce_json(policy, dict, "policy")
            version = store.next_policy_version(name)
            with store.transaction():
                store.retire_active_policies(name)
                row = SearchPolicy(
                    id=f"spol-{uuid.uuid4().hex[:8]}",
                    name=name,
                    version=version,
                    policy=policy,
                    status=PolicyStatus.active,
                )
                store.create_search_policy(row)
            return ok({
                "policy_id": row.id,
                "name": name,
                "version": version,
                "status": "active",
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def list_search_policies(name: Annotated[str | None, Field(description='Optional name filter.')] = None) -> Annotated[CallToolResult, ListSearchPoliciesOut]:
        """List registered search-policy versions."""
        try:
            rows = store.list_search_policies(name)
            return ok({
                "policies": [
                    {
                        "id": p.id, "name": p.name, "version": p.version,
                        "status": p.status.value, "policy": p.policy,
                        "created_at": p.created_at,
                    }
                    for p in rows
                ],
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def list_candidates(
        status: Annotated[Literal['champion', 'challenger', 'candidate', 'rolled_back'] | None, Field(description='Optional status filter.')] = None,
        limit: Annotated[int, Field(description='Max rows to return (pagination).')] = 50,
        offset: Annotated[int, Field(description='Rows to skip before returning (pagination).')] = 0,
    ) -> Annotated[CallToolResult, ListCandidatesOut]:
        """List the roster — Loop 1's annotated view of upstream lineage.

        status filter: candidate | challenger | champion | rolled_back.
        """
        try:
            if status is not None and status not in {
                s.value for s in DerivedStatus
            }:
                return fail(json.dumps({
                    "error": "status must be one of "
                    f"{'|'.join(s.value for s in DerivedStatus)}, "
                    f"got: {status}"
                }))
            entries, total = store.list_roster_entries(
                status=status, limit=limit, offset=offset
            )
            return ok({
                "candidates": [
                    {
                        "id": e.id, "parent_id": e.parent_id,
                        "derived_status": e.derived_status.value,
                        "annotated_at": e.annotated_at,
                        "notes": e.notes,
                    }
                    for e in entries
                ],
                "total": total,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    async def get_incumbent() -> Annotated[CallToolResult, GetIncumbentOut]:
        """The current Loop-0 incumbent — derived upstream from the
        promotion-decision trail, never stored as a mutable pointer.
        """
        try:
            if adaptors.evidence is None:
                return fail(json.dumps({
                    "error": "No evidence adaptor configured — cannot "
                    "derive the incumbent."
                }))
            payload = await adaptors.evidence.pull("get_incumbent", {})
            data, err = _parse_upstream(payload)
            if err:
                return fail(json.dumps({"error": err}))
            return ok(data)
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    async def open_campaign(
        contract_id: Annotated[str, Field(description='Upstream evaluation contract declaring the metric the campaign is scored under — must exist.')],
        challenger_id: Annotated[str, Field(description="Roster ID of the challenger arm's candidate.")],
        budget: Annotated[dict | str, Field(description='Budget carried from the orchestrating tournament, verbatim. May be JSON-encoded.')],
        seeds: Annotated[list | str | None, Field(description='Seed set for the arm (list of ints); may be JSON-encoded.')] = None,
    ) -> Annotated[CallToolResult, OpenCampaignOut]:
        """Open a champion/challenger promotion campaign.

        The contract must exist upstream — it declares the metric the
        campaign is scored under. The champion is derived upstream
        (get_incumbent); the challenger must be on the roster and
        distinct from the champion. No incumbent means no campaign —
        a lineage with no champion has nothing to promote against.
        """
        try:
            budget = coerce_json(budget, dict, "budget")
            if seeds is not None:
                seeds = coerce_json(seeds, list, "seeds")
            seeds = seeds or []
            if adaptors.evidence is None:
                return fail(json.dumps({
                    "error": "No evidence adaptor configured — cannot "
                    "verify contract or incumbent."
                }))
            if e := check_roster_entry(store, challenger_id):
                return _err(e)

            data, err = _parse_upstream(await adaptors.evidence.pull(
                "get_evaluation_contract", {"contract_id": contract_id}
            ))
            if err:
                return fail(json.dumps({"error": err}))
            contract = data["contract"]
            metrics = contract.get("metrics") or {}
            # Upstream metrics is a directive map — either an explicit
            # {"primary_metric": name, "secondary_metrics": [...]} or a
            # name→direction map {"hits": "maximize"}. Primary is the
            # declared primary, else the first declared metric.
            primary = metrics.get("primary_metric")
            if not primary:
                named = [
                    k for k in metrics
                    if k not in ("primary_metric", "secondary_metrics")
                ]
                primary = named[0] if named else None
            if not primary:
                return fail(json.dumps({
                    "error": f"Contract {contract_id} declares no "
                    "metrics — nothing to score the campaign under."
                }))

            data, err = _parse_upstream(
                await adaptors.evidence.pull("get_incumbent", {})
            )
            if err:
                return fail(json.dumps({"error": err}))
            champion_id = (data or {}).get("candidate_id")
            if not champion_id:
                return fail(json.dumps({
                    "error": "No upstream incumbent — the lineage has "
                    "no champion to promote against."
                }))
            if champion_id == challenger_id:
                return fail(json.dumps({
                    "error": "challenger must differ from the incumbent."
                }))

            campaign = PromotionCampaign(
                id=f"camp-{uuid.uuid4().hex[:8]}",
                contract_id=contract_id,
                champion_id=champion_id,
                challenger_id=challenger_id,
                primary_metric=primary,
                budget=budget,
                seeds=seeds,
            )
            store.create_campaign(campaign)
            return ok({
                "campaign_id": campaign.id,
                "champion_id": champion_id,
                "challenger_id": challenger_id,
                "primary_metric": primary,
                "status": "open",
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    async def spawn_campaign_programme(
        campaign_id: Annotated[str, Field(description='ID of the target campaign.')],
        arm: Annotated[Literal['champion', 'challenger'], Field(description="Campaign arm the programme spawns for: 'champion' | 'challenger'.")],
        goal: Annotated[str, Field(description='Research goal for the spawned programme.')],
        constraints: Annotated[dict | str, Field(description='Typed constraint fields {gpu_memory_gb, max_training_hours_per_trial, max_parameter_count} for the spawned programme; may be JSON-encoded.')],
        allowed_variables: Annotated[list | str, Field(description='Variables the programme may search/vary; list or JSON-encoded list.')],
        budget: Annotated[dict | str | None, Field(description='Per-programme override {max_trials, max_wall_time_hours} — may only shrink the carried values. May be JSON-encoded.')] = None,
    ) -> Annotated[CallToolResult, SpawnCampaignProgrammeOut]:
        """Spawn a descendant Loop-0 programme under an open campaign arm.

        The Loop-2 orchestration verb: Arete's open_arm_campaign pushed
        the tournament's budget into the campaign; this tool turns one
        unit of that budget into a real upstream programme. The spawn
        is durable — a campaign_spawns row is written so results can
        only ever be reported against programmes this campaign created.

        Budget is carried, never invented: each spawned programme gets
        {"max_trials": trials_per_programme} (plus max_wall_time_hours
        when carried). A caller may pass a per-programme override that
        can only shrink the carried values.

        metric_direction is resolved from the campaign's upstream
        evaluation contract — a min-direction contract produces
        minimize programmes; a contract with no resolvable direction
        fails loudly.
        """
        try:
            campaign = store.get_campaign(campaign_id)
            if e := check_campaign_open(store, campaign_id):
                return _err(e)
            try:
                arm_e = CampaignArm(arm)
            except ValueError:
                return fail(json.dumps({
                    "error": "arm must be one of champion|challenger, "
                    f"got: {arm}"
                }))
            constraints = coerce_json(constraints, dict, "constraints")
            allowed_variables = coerce_json(
                allowed_variables, list, "allowed_variables"
            )
            if budget is not None:
                budget = coerce_json(budget, dict, "budget")

            if e := check_spawn_cap(store, campaign_id, arm, campaign.budget):
                return _err(e)

            carried = campaign.budget or {}
            tpp = carried.get("trials_per_programme")
            if tpp is None:
                return fail(json.dumps({
                    "error": f"campaign {campaign_id} carries no "
                    "trials_per_programme budget — the spawn cannot "
                    "be budgeted."
                }))
            spawn_budget: dict = {"max_trials": tpp}
            if "max_wall_time_hours" in carried:
                spawn_budget["max_wall_time_hours"] = carried[
                    "max_wall_time_hours"
                ]
            if budget:
                for k, v in budget.items():
                    if k not in spawn_budget:
                        return fail(json.dumps({
                            "error": f"budget override key '{k}' is "
                            "not a carried budget key — overrides can "
                            "only shrink carried values, not invent "
                            "new ones."
                        }))
                    try:
                        if float(v) > float(spawn_budget[k]):
                            return fail(json.dumps({
                                "error": f"budget override '{k}={v}' "
                                f"exceeds the carried "
                                f"{spawn_budget[k]} — overrides can "
                                "only shrink."
                            }))
                    except (TypeError, ValueError):
                        return fail(json.dumps({
                            "error": f"budget override '{k}={v}' is "
                            "not comparable to the carried value."
                        }))
                    spawn_budget[k] = v

            # Direction from the authoritative contract, through the
            # read-only evidence channel — never from the caller.
            if adaptors.evidence is None:
                return fail(json.dumps({
                    "error": "No evidence adaptor configured — cannot "
                    "resolve the contract's metric direction."
                }))
            data, err = _parse_upstream(await adaptors.evidence.pull(
                "get_evaluation_contract",
                {"contract_id": campaign.contract_id},
            ))
            if err:
                return fail(json.dumps({"error": err}))
            contract = data["contract"]
            direction, err = _resolve_contract_direction(
                contract, campaign.primary_metric
            )
            if err:
                return fail(json.dumps({"error": err}))

            arm_candidate = (
                campaign.champion_id if arm_e is CampaignArm.champion
                else campaign.challenger_id
            )
            if adaptors.promotion is None:
                return fail(json.dumps({
                    "error": "No promotion adaptor configured — the "
                    "spawn cannot reach Loop 0. Wire "
                    "[adaptors.promotion] in ml-zetesis.toml."
                }))
            if e := check_promotion_tool_whitelisted("create_programme"):
                return _err(e)

            data, err = _parse_upstream(await adaptors.promotion.push(
                "create_programme",
                {
                    "goal": goal,
                    "constraints": constraints,
                    "allowed_variables": allowed_variables,
                    "budget": spawn_budget,
                    "metric_direction": direction,
                    "candidate_version_id": arm_candidate,
                },
            ))
            if err:
                return fail(json.dumps({"error": err}))
            programme_id = (data or {}).get("programme_id")
            if not programme_id:
                return fail(json.dumps({
                    "error": "upstream create_programme returned no "
                    "programme_id"
                }))

            spawn = CampaignSpawn(
                id=f"spawn-{uuid.uuid4().hex[:8]}",
                campaign_id=campaign_id,
                arm=arm_e,
                programme_id=programme_id,
                budget=spawn_budget,
            )
            store.create_campaign_spawn(spawn)
            return ok({
                "spawn_id": spawn.id,
                "campaign_id": campaign_id,
                "arm": arm,
                "programme_id": programme_id,
                "candidate_version_id": arm_candidate,
                "metric_direction": direction,
                "budget": spawn_budget,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    async def record_campaign_result(
        campaign_id: Annotated[str, Field(description='ID of the target campaign.')],
        arm: Annotated[Literal['champion', 'challenger'], Field(description="Campaign arm the result belongs to: 'champion' | 'challenger' — attribution is verified upstream.")],
        programme_id: Annotated[str, Field(description='ID of the target research programme.')],
        metrics: Annotated[dict | str, Field(description="Result metrics — must carry the contract's primary_metric; may be JSON-encoded.")],
    ) -> Annotated[CallToolResult, RecordCampaignResultOut]:
        """Attach an executed-arm result to an open campaign.

        Attribution is verified upstream: the programme must list the
        arm's candidate as its candidate_version_id — a result can
        only count for the arm that actually produced it. Results are
        insert-only; a contested result is contested by a new record,
        never edited.
        """
        try:
            campaign = store.get_campaign(campaign_id)
            if e := check_campaign_open(store, campaign_id):
                return _err(e)
            try:
                arm_e = CampaignArm(arm)
            except ValueError:
                return fail(json.dumps({
                    "error": "arm must be one of champion|challenger, "
                    f"got: {arm}"
                }))
            metrics = coerce_json(metrics, dict, "metrics")

            arm_candidate = (
                campaign.champion_id if arm_e is CampaignArm.champion
                else campaign.challenger_id
            )
            if adaptors.evidence is None:
                return fail(json.dumps({
                    "error": "No evidence adaptor configured — cannot "
                    "verify programme attribution."
                }))
            data, err = _parse_upstream(await adaptors.evidence.pull(
                "list_programmes",
                {"candidate_version_id": arm_candidate, "limit": 200},
            ))
            if err:
                return fail(json.dumps({"error": err}))
            attributed = {
                p["programme_id"] for p in data.get("programmes", [])
            }
            if programme_id not in attributed:
                return fail(json.dumps({
                    "error": f"Programme {programme_id} is not "
                    f"attributed to {arm_candidate} upstream — a result "
                    "can only count for the arm that produced it."
                }))
            if e := check_spawn_scoped_result(
                store, campaign_id, arm, programme_id
            ):
                return _err(e)

            result = CampaignResult(
                id=f"cres-{uuid.uuid4().hex[:8]}",
                campaign_id=campaign_id,
                arm=arm_e,
                programme_id=programme_id,
                metrics=metrics,
            )
            store.create_campaign_result(result)
            spawn = store.get_spawn_for_programme(programme_id)
            if spawn is not None:
                store.set_spawn_status(spawn.id, SpawnStatus.completed)
            return ok({
                "result_id": result.id,
                "campaign_id": campaign_id,
                "arm": arm,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    async def pull_campaign_evidence(
        campaign_id: Annotated[str, Field(description='ID of the target campaign.')],
        source: Annotated[Literal['loop0', 'anamnesis'], Field(description='Evidence source — the upstream read surface to pull through.')],
        tool: Annotated[Literal['assess_programme', 'get_archive', 'get_archived_programme', 'get_campaign', 'get_candidate', 'get_candidate_lineage', 'get_candidate_scorecard', 'get_claim', 'get_evaluation_contract', 'get_incumbent', 'get_investigation', 'get_trial_status', 'list_active_programmes', 'list_archives', 'list_campaigns', 'list_candidates', 'list_claims', 'list_hypotheses', 'list_investigations', 'list_programmes', 'list_promotion_decisions', 'list_trials', 'recall'], Field(description="Upstream read tool to call — must be on the source's read whitelist (the evidence channel is read-only).")],
        args: Annotated[dict | str | None, Field(description='Arguments forwarded to the upstream tool; object or JSON-encoded.')] = None,
    ) -> Annotated[CallToolResult, PullCampaignEvidenceOut]:
        """Read upstream evidence under a campaign context — same
        read-only channel as pull_evidence, logged against the campaign
        instead of an investigation. The promotion verdict cites these
        refs."""
        try:
            if e := check_campaign_open(store, campaign_id):
                return _err(e)
            for e in (check_source_valid(source),
                      check_tool_whitelisted(source, tool)):
                if e:
                    return _err(e)
            args = coerce_json(args, dict, "args") if args else {}
            adaptor = (adaptors.evidence if source == "loop0"
                       else adaptors.claims)
            if adaptor is None:
                return fail(json.dumps({
                    "error": f"No {source} adaptor configured — cannot "
                    "pull evidence."
                }))
            payload = await adaptor.pull(tool, args)
            ref_ids = _extract_ref_ids(payload)
            ref = EvidenceRef(
                id=f"eref-{uuid.uuid4().hex[:8]}",
                campaign_id=campaign_id,
                source=EvidenceSource(source),
                tool=tool,
                args=args,
                ref_ids=ref_ids,
            )
            store.create_evidence_ref(ref)
            try:
                result = (json.loads(payload)
                          if isinstance(payload, str) else payload)
            except ValueError:
                result = payload
            return ok({
                "evidence_ref_id": ref.id,
                "ref_ids": ref_ids,
                "result": result,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def close_campaign(campaign_id: Annotated[str, Field(description='ID of the target campaign.')]) -> Annotated[CallToolResult, CloseCampaignOut]:
        """Close an open campaign and freeze its promotion score.

        Requires at least one result per arm — a campaign that never
        ran both arms has nothing to compare. The score is the
        challenger/champion ratio on the contract's primary metric
        (mean across recorded results); equal arms score 1.0.
        """
        try:
            campaign = store.get_campaign(campaign_id)
            if e := check_campaign_open(store, campaign_id):
                return _err(e)
            # Orchestrated campaigns (those with spawn records) carry
            # the tournament's budget — close audits recorded spend
            # against it before the score freezes.
            if store.list_campaign_spawns(campaign_id):
                if e := check_close_budget_audit(
                    store, campaign_id, campaign.budget
                ):
                    return _err(e)
            results = store.list_campaign_results(campaign_id)
            by_arm: dict[CampaignArm, list[float]] = {
                CampaignArm.champion: [],
                CampaignArm.challenger: [],
            }
            for r in results:
                if campaign.primary_metric in r.metrics:
                    by_arm[r.arm].append(
                        float(r.metrics[campaign.primary_metric])
                    )
            missing = [a.value for a, v in by_arm.items() if not v]
            if missing:
                return fail(json.dumps({
                    "error": f"Campaign has no {campaign.primary_metric} "
                    f"results for arm(s) {'|'.join(missing)} — both "
                    "arms must run before close."
                }))
            champion_mean = (
                sum(by_arm[CampaignArm.champion])
                / len(by_arm[CampaignArm.champion])
            )
            challenger_mean = (
                sum(by_arm[CampaignArm.challenger])
                / len(by_arm[CampaignArm.challenger])
            )
            score = (
                challenger_mean / champion_mean
                if champion_mean != 0 else 0.0
            )
            store.close_campaign(campaign_id, score)
            return ok({
                "campaign_id": campaign_id,
                "status": "closed",
                "promotion_score": score,
                "champion_mean": champion_mean,
                "challenger_mean": challenger_mean,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    async def record_promotion_verdict(
        campaign_id: Annotated[str, Field(description='ID of the target campaign.')],
        verdict: Annotated[Literal['promote', 'retain', 'rollback'], Field(description="promote | retain | rollback — 'rollback' requires decided_by starting with 'human:'.")],
        decided_by: Annotated[str, Field(description="Attributable decider — 'rollback' requires 'human:<name>' (corrective verdicts need human authority).")],
        evidence_ref_ids: Annotated[list | str | None, Field(description='Evidence_ref IDs minted by pull_evidence/pull_arm_evidence calls; list or JSON-encoded.')] = None,
        rationale: Annotated[str | None, Field(description='Non-empty justification — accountability is first-class.')] = None,
    ) -> Annotated[CallToolResult, RecordPromotionVerdictOut]:
        """Write the campaign's verdict upstream as a promotion decision.

        The campaign must be closed — the score is the evidence the
        verdict interprets. verdict promote|retain|rollback; rollback
        requires decided_by starting with 'human:' (corrective
        verdicts need human authority). At least one campaign-scoped
        evidence ref is required — a verdict with no consulted trail
        is a bare assertion.

        The write goes through the promotion adaptor — whitelisted to
        Loop 0's insert-only record_promotion_decision. On 'promote'
        the roster champion pointer moves to the challenger; on
        'rollback' it returns to the challenger arm's parent chain —
        both as new annotations, never deletion.
        """
        try:
            campaign = store.get_campaign(campaign_id)
            if e := check_campaign_exists(store, campaign_id):
                return _err(e)
            if campaign.status is not CampaignStatus.closed:
                return fail(json.dumps({
                    "error": f"Campaign {campaign_id} is still open — "
                    "close_campaign freezes the score a verdict "
                    "interprets."
                }))
            if campaign.decision_id is not None:
                return fail(json.dumps({
                    "error": f"Campaign {campaign_id} already has "
                    f"decision {campaign.decision_id} — verdicts are "
                    "insert-only; record a new campaign to revisit."
                }))
            if verdict not in VERDICTS:
                return fail(json.dumps({
                    "error": "verdict must be one of "
                    f"{'|'.join(sorted(VERDICTS))}, got: {verdict}"
                }))
            if e := check_verdict_decided_by(verdict, decided_by):
                return _err(e)
            if evidence_ref_ids is not None:
                evidence_ref_ids = coerce_json(
                    evidence_ref_ids, list, "evidence_ref_ids"
                )
            evidence_ref_ids = evidence_ref_ids or []
            if not evidence_ref_ids:
                return fail(json.dumps({
                    "error": "A promotion verdict requires at least one "
                    "campaign-scoped evidence ref — pull_campaign_"
                    "evidence first."
                }))
            if e := check_campaign_evidence_refs(
                store, campaign_id, evidence_ref_ids
            ):
                return _err(e)

            if adaptors.promotion is None:
                return fail(json.dumps({
                    "error": "No promotion adaptor configured — the "
                    "verdict cannot reach Loop 0. Wire "
                    "[adaptors.promotion] in ml-zetesis.toml."
                }))
            if e := check_promotion_tool_whitelisted(
                "record_promotion_decision"
            ):
                return _err(e)

            # Map the campaign verdict onto the upstream decision enum:
            # promote → promote the challenger; retain → reject the
            # challenger (the champion stays); rollback → rollback the
            # challenger (the prior incumbent resurfaces upstream).
            upstream_verdict = {
                "promote": "promote",
                "retain": "reject",
                "rollback": "rollback",
            }[verdict]
            # The upstream decision names the campaign's evidence
            # trail — eref ids are the provenance of what was
            # consulted.
            rationale_text = (
                rationale
                or f"Campaign {campaign_id} closed at promotion_score "
                f"{campaign.promotion_score} on "
                f"{campaign.primary_metric}."
            )
            payload = await adaptors.promotion.push(
                "record_promotion_decision",
                {
                    "candidate_id": campaign.challenger_id,
                    "verdict": upstream_verdict,
                    "evidence_refs": evidence_ref_ids,
                    "rationale": rationale_text,
                    "decided_by": decided_by,
                    "contract_id": campaign.contract_id,
                },
            )
            data, err = _parse_upstream(payload)
            if err:
                return fail(json.dumps({"error": err}))
            decision_id = (data or {}).get("decision_id")
            store.set_campaign_decision(campaign_id, decision_id)

            # Roster annotation follows the verdict — annotations, not
            # deletions.
            with store.transaction():
                if verdict == "promote":
                    # The roster mirrors a single global incumbent —
                    # demote EVERY entry carrying 'champion', not just
                    # the campaign's recorded one. The upstream
                    # incumbent can move via paths the roster never
                    # saw (a manual record_promotion_decision between
                    # campaign open and verdict), leaving a stale
                    # champion the campaign never named.
                    demote_ids = {
                        e.id
                        for e in store.list_roster_entries(
                            status="champion"
                        )[0]
                    }
                    demote_ids.add(campaign.champion_id)
                    demote_ids.discard(campaign.challenger_id)
                    for rid in sorted(demote_ids):
                        existing = store.get_roster_entry(rid)
                        store.upsert_roster_entry(RosterEntry(
                            id=rid,
                            parent_id=(
                                existing.parent_id if existing else None
                            ),
                            derived_status=DerivedStatus.candidate,
                            notes={
                                "superseded_by": campaign.challenger_id
                            },
                        ))
                    store.upsert_roster_entry(RosterEntry(
                        id=campaign.challenger_id,
                        parent_id=campaign.champion_id,
                        derived_status=DerivedStatus.champion,
                        notes={"promoted_by": campaign_id},
                    ))
                elif verdict == "retain":
                    store.upsert_roster_entry(RosterEntry(
                        id=campaign.challenger_id,
                        parent_id=campaign.champion_id,
                        derived_status=DerivedStatus.candidate,
                        notes={"retained_by": campaign_id},
                    ))
                # rollback: challenger arm already annotated by the
                # campaign trail; the upstream decision restores the
                # champion — annotate the challenger as rolled_back.
                elif verdict == "rollback":
                    store.upsert_roster_entry(RosterEntry(
                        id=campaign.challenger_id,
                        parent_id=campaign.champion_id,
                        derived_status=DerivedStatus.rolled_back,
                        notes={"rolled_back_by": campaign_id},
                    ))

            # Mint the methodological claim — memory is a byproduct,
            # never a gate on the decision. Evidence edges go in the
            # same assert_claim call: anamnesis caps unevidenced claims
            # at the prior ceiling, and edges minted inline satisfy the
            # gate (post-hoc relate would hit the chicken-and-egg).
            claim_id = None
            claim_status = "skipped"
            if adaptors.claims is None:
                claim_status = "disabled"
            else:
                try:
                    consulted = {
                        ref_id
                        for rid in evidence_ref_ids
                        for ref_id in (
                            store.get_evidence_ref(rid).ref_ids
                            if store.get_evidence_ref(rid) else []
                        )
                    }
                    minted = await adaptors.claims.assert_claim(
                        content=(
                            f"Promotion campaign {campaign_id}: "
                            f"{verdict} {campaign.challenger_id} under "
                            "contract "
                            f"{campaign.contract_id} (score "
                            f"{campaign.promotion_score})."
                            + (f" {rationale}" if rationale else "")
                        ),
                        type="methodological",
                        confidence=min(
                            1.0, abs(campaign.promotion_score or 0.0)
                        ),
                        evidence=[
                            {
                                "to_ref": ref_id,
                                "ref_type": _ref_type_for(ref_id),
                                "relation": "derived_from",
                            }
                            for ref_id in sorted(consulted)
                        ],
                        source_id=campaign_id,
                    )
                    claim_id = minted["claim_id"]
                    store.set_campaign_claim(campaign_id, claim_id)
                    claim_status = "minted"
                except Exception:
                    claim_status = "failed"

            return ok({
                "campaign_id": campaign_id,
                "verdict": verdict,
                "decision_id": decision_id,
                "claim_id": claim_id,
                "claim_status": claim_status,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def get_campaign(campaign_id: Annotated[str, Field(description='ID of the target campaign.')]) -> Annotated[CallToolResult, GetCampaignOut]:
        """Read a campaign with its results and evidence trail."""
        try:
            c = store.get_campaign(campaign_id)
            if c is None:
                return fail(json.dumps({
                    "error": f"Campaign not found: {campaign_id}"
                }))
            return ok(_campaign_json(c, store))
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def list_campaigns(
        status: Annotated[Literal['open', 'closed'] | None, Field(description='Optional status filter.')] = None,
        limit: Annotated[int, Field(description='Max rows to return (pagination).')] = 50,
        offset: Annotated[int, Field(description='Rows to skip before returning (pagination).')] = 0,
    ) -> Annotated[CallToolResult, ListCampaignsOut]:
        """List promotion campaigns, newest first."""
        try:
            if status is not None and status not in {
                s.value for s in CampaignStatus
            }:
                return fail(json.dumps({
                    "error": "status must be one of "
                    f"{'|'.join(s.value for s in CampaignStatus)}, "
                    f"got: {status}"
                }))
            campaigns, total = store.list_campaigns(
                status=status, limit=limit, offset=offset
            )
            return ok({
                "campaigns": [
                    {
                        "id": c.id, "contract_id": c.contract_id,
                        "champion_id": c.champion_id,
                        "challenger_id": c.challenger_id,
                        "primary_metric": c.primary_metric,
                        "status": c.status.value,
                        "promotion_score": c.promotion_score,
                        "decision_id": c.decision_id,
                        "created_at": c.created_at,
                        "closed_at": c.closed_at,
                    }
                    for c in campaigns
                ],
                "total": total,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))
