"""Candidate tool handlers — register_candidate, get_candidate_lineage,
create_evaluation_contract, record_promotion_decision (RSI Phase 0).

The candidate_version entity makes the thing *doing* the research a
durable, auditable object with parent lineage. Contracts and decisions
are insert-only: immutability is enforced by surface, not by a flag.
"""

from __future__ import annotations

import asyncio
import json
import uuid

from ..state.models import (
    CandidateVersion,
    EvaluationContract,
    PromotionDecision,
)
from ..state.store import StateStore
from .. import _grounded_constants as _gc
from ..clients.adaptor import MCPAdaptor
from ..enforcement.commitments import (
    FOREIGN_ID_PREFIXES,
    check_candidate_parent_exists,
    check_contract_metrics,
    check_contract_programme_exists,
    check_decision_valid,
    check_digest_claim,
    check_not_foreign_id,
    check_programme_active,
    check_promotion_policy_power,
)
from .schemas import coerce_json, fail, ok, CandidateScorecardOut, RegisterCandidateOut, CreateEvaluationContractOut, GetCandidateLineageOut, GetCandidateOut, GetEvaluationContractOut, GetIncumbentOut, ListCandidatesOut, ListPromotionDecisionsOut, RecordPromotionDecisionOut
from typing import Annotated, Literal
from pydantic import Field
from mcp.types import CallToolResult


async def _classify_unverified_refs(
    adaptor: MCPAdaptor, evidence_refs: list[str]
) -> list[str]:
    """Foreign-prefix refs this server cannot verify, marked honestly.

    ``check_decision_valid`` resolves Loop-0 ids against the store and
    refuses invented prefixes, but refs minted by a peer loop pass
    through opaquely — the owner lives on another server. ``claim-``
    ids are probed over the claims channel when wired; a failed or
    missing channel leaves them unverified. Prefixes with no wired
    channel at all (eref-, camp-, mdec-, …) are unverifiable by
    construction — they are still accepted (zetesis legitimately
    forwards erefs into record_promotion_decision) but land in the
    record's unverified_refs so fabricated residue stays auditable.
    """
    claims = getattr(adaptor, "claims", None)
    unverified = []
    for ref in evidence_refs:
        if not any(ref.startswith(p) for p in FOREIGN_ID_PREFIXES):
            continue  # Loop-0 id — existence already enforced upstream
        if ref.startswith("claim-") and claims is not None:
            try:
                await asyncio.wait_for(claims.get_claim(ref), timeout=5.0)
                continue  # resolved on the owning server
            except Exception:
                pass  # not found, or channel error — mark, never guess
        unverified.append(ref)
    return unverified


def register(mcp, store: StateStore, adaptor: MCPAdaptor) -> None:
    """Register candidate/contract/decision tools on the MCP server."""

    @mcp.tool()
    def register_candidate(
        code_artifact_digest: Annotated[str, Field(description="sha256:<64 hex> digest of the version's code artifact — must resolve to an ingested blob (describe_blob), or 'none' to declare no code artifact.")],
        model_ref: Annotated[str, Field(description='Model identifier/reference backing this version.')],
        capability_profile: Annotated[dict | str, Field(description='Tools/roles the version can use; object or JSON-encoded.')],
        parent_id: Annotated[str | None, Field(description='Parent version ID — omit for genesis; must name an existing row if given.')] = None,
        harness_artifact_digest: Annotated[str | None, Field(description="sha256:<64 hex> digest of the harness/evaluation artifact — must resolve to an ingested blob; omit when there is none.")] = None,
        search_policy_ref: Annotated[str | None, Field(description='Reference to the search policy this version uses, if any.')] = None,
    ) -> Annotated[CallToolResult, RegisterCandidateOut]:
        """Register a researcher version with parent lineage (RSI Phase 0).

        The thing doing the research becomes a durable object. parent_id
        links this candidate to its parent — omit for a genesis
        candidate. Lineage is never "latest wins": every candidate keeps
        its ancestry.

        capability_profile describes the tools/roles available; it may
        be sent as a JSON-encoded string.

        Enforcement: parent_id must name an existing candidate;
        code/harness digests must resolve to an ingested blob or be
        'none' — a hash of nothing is a claim with no referent.
        """
        try:
            capability_profile = coerce_json(
                capability_profile, dict, "capability_profile"
            )
            # Enforcement: digest claims must resolve or be 'none'
            for field, value in (
                ("code_artifact_digest", code_artifact_digest),
                ("harness_artifact_digest", harness_artifact_digest),
            ):
                err = check_digest_claim(field, value, store.has_blob)
                if err:
                    return fail(json.dumps({"error": err}))

            # Enforcement: lineage integrity — parent must exist
            err = check_candidate_parent_exists(parent_id, store)
            if err:
                return fail(json.dumps({"error": err}))

            candidate = CandidateVersion(
                id=f"cand-{uuid.uuid4().hex[:8]}",
                parent_id=parent_id,
                code_artifact_digest=code_artifact_digest,
                # 'none' normalizes to NULL on the optional field —
                # one absence representation, not two.
                harness_artifact_digest=(
                    None
                    if harness_artifact_digest == "none"
                    else harness_artifact_digest
                ),
                model_ref=model_ref,
                search_policy_ref=search_policy_ref,
                capability_profile=capability_profile,
            )
            store.create_candidate_version(candidate)
            return ok({
                "candidate_id": candidate.id,
                "status": "registered",
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def list_candidates(limit: Annotated[int, Field(description='Max rows to return (pagination).')] = 50, offset: Annotated[int, Field(description='Rows to skip before returning (pagination).')] = 0) -> Annotated[CallToolResult, ListCandidatesOut]:
        """Enumerate the candidate population, newest first (read-only).

        The population listing the Loop-1 roster pulls — candidates are
        the researchers; champion/challenger status is derived from
        promotion_decisions (see get_incumbent), never stored.
        """
        try:
            candidates = store.list_candidates(limit=limit, offset=offset)
            return ok({
                "candidates": [
                    {
                        "id": c.id,
                        "parent_id": c.parent_id,
                        "code_artifact_digest": c.code_artifact_digest,
                        "harness_artifact_digest": c.harness_artifact_digest,
                        "model_ref": c.model_ref,
                        "search_policy_ref": c.search_policy_ref,
                        "capability_profile": c.capability_profile,
                        "created_at": c.created_at,
                    }
                    for c in candidates
                ],
                "total": store.count_candidates(),
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def get_candidate(candidate_id: Annotated[str, Field(description='Candidate version ID to read.')]) -> Annotated[CallToolResult, GetCandidateOut]:
        """Read one candidate version (read-only)."""
        try:
            if e := check_not_foreign_id(candidate_id, "cand-"):
                return fail(json.dumps({"error": e}))
            c = store.get_candidate_version(candidate_id)
            if c is None:
                return fail(json.dumps({
                    "error": f"Candidate not found: {candidate_id}"
                }))
            return ok({
                "candidate": {
                    "id": c.id,
                    "parent_id": c.parent_id,
                    "code_artifact_digest": c.code_artifact_digest,
                    "harness_artifact_digest": c.harness_artifact_digest,
                    "model_ref": c.model_ref,
                    "search_policy_ref": c.search_policy_ref,
                    "capability_profile": c.capability_profile,
                    "created_at": c.created_at,
                }
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def get_incumbent() -> Annotated[CallToolResult, GetIncumbentOut]:
        """Derive the incumbent candidate from decisions (read-only).

        Champion/challenger is derived from promotion_decisions, never
        stored: the latest 'promote' not superseded by a 'rollback' for
        the same candidate is the incumbent. Empty until the first
        promote — registered-but-undecided candidates are candidates,
        not the champion.
        """
        try:
            incumbent = store.get_incumbent()
            result: dict = {"candidate_id": incumbent}
            if incumbent is not None:
                c = store.get_candidate_version(incumbent)
                if c is not None:
                    result["candidate"] = {
                        "id": c.id,
                        "parent_id": c.parent_id,
                        "model_ref": c.model_ref,
                        "search_policy_ref": c.search_policy_ref,
                        "created_at": c.created_at,
                    }
            return ok(result)
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def list_promotion_decisions(candidate_id: Annotated[str, Field(description='Candidate whose verdict trail to list.')]) -> Annotated[CallToolResult, ListPromotionDecisionsOut]:
        """List a candidate's verdict trail, oldest first (read-only).

        Decisions are insert-only — the trail is the record; reversal
        is a new 'rollback' verdict, never a mutation.
        """
        try:
            if e := check_not_foreign_id(candidate_id, "cand-"):
                return fail(json.dumps({"error": e}))
            if store.get_candidate_version(candidate_id) is None:
                return fail(json.dumps({
                    "error": f"Candidate not found: {candidate_id}"
                }))
            decisions = store.list_promotion_decisions(candidate_id)
            return ok({
                "decisions": [
                    {
                        "id": d.id,
                        "candidate_id": d.candidate_id,
                        "contract_id": d.contract_id,
                        "verdict": d.verdict,
                        "evidence_refs": d.evidence_refs,
                        "unverified_refs": d.unverified_refs,
                        "rationale": d.rationale,
                        "decided_by": d.decided_by,
                        "declared_rung": d.declared_rung,
                        "claimed_rung": d.claimed_rung,
                        "computed_rung": d.computed_rung,
                        "bf_2ln": d.bf_2ln,
                        "created_at": d.created_at,
                    }
                    for d in decisions
                ]
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def get_candidate_scorecard(
        candidate_id: Annotated[str, Field(description='Candidate whose attributed programmes are scored.')], contract_id: Annotated[str | None, Field(description="Optional — scopes the report to that contract's declared metrics (primary + secondary).")] = None
    ) -> Annotated[CallToolResult, CandidateScorecardOut]:
        """Descendant quality over attributed programmes (read-only).

        For each programme created under this candidate (the Phase-0
        `candidate_version_id` correlation), reports trial/observation
        counts and the best observed value per metric — 'best' read
        against the programme's metric_direction.

        contract_id scopes the report to that contract's declared
        metrics (primary + secondary); without it all recorded metrics
        are reported per programme.
        """
        try:
            if e := check_not_foreign_id(candidate_id, "cand-"):
                return fail(json.dumps({"error": e}))
            if store.get_candidate_version(candidate_id) is None:
                return fail(json.dumps({
                    "error": f"Candidate not found: {candidate_id}"
                }))
            card = store.get_candidate_scorecard(candidate_id)
            if contract_id is not None:
                contract = store.get_evaluation_contract(contract_id)
                if contract is None:
                    return fail(json.dumps({
                        "error": f"Evaluation contract not found: "
                                 f"{contract_id}"
                    }))
                declared = {
                    contract.metrics.get("primary_metric"),
                    *(
                        contract.metrics.get("secondary_metrics")
                        or []
                    ),
                } - {None}
                for p in card["programmes"]:
                    p["best_metrics"] = {
                        k: v for k, v in p["best_metrics"].items()
                        if k in declared
                    }
                card["contract_id"] = contract_id
            return ok(card)
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def get_candidate_lineage(candidate_id: Annotated[str, Field(description='Candidate whose parent chain to walk to genesis.')]) -> Annotated[CallToolResult, GetCandidateLineageOut]:
        """Walk a candidate's parent chain to genesis (self first).

        Returns {"lineage": [{id, parent_id, model_ref,
        code_artifact_digest, ...}, ...]} — the full ancestry that any
        result attributed to this candidate inherits.
        """
        try:
            if e := check_not_foreign_id(candidate_id, "cand-"):
                return fail(json.dumps({"error": e}))
            lineage = store.get_candidate_lineage(candidate_id)
            if not lineage:
                return fail(json.dumps({
                    "error": f"Candidate not found: {candidate_id}"
                }))
            return ok({
                "lineage": [
                    {
                        "id": c.id,
                        "parent_id": c.parent_id,
                        "code_artifact_digest": c.code_artifact_digest,
                        "harness_artifact_digest": c.harness_artifact_digest,
                        "model_ref": c.model_ref,
                        "search_policy_ref": c.search_policy_ref,
                        "capability_profile": c.capability_profile,
                        "created_at": c.created_at,
                    }
                    for c in lineage
                ]
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def create_evaluation_contract(
        programme_id: Annotated[str, Field(description='ID of the target research programme.')],
        metrics: Annotated[dict | str, Field(description="Declares 'primary_metric' (+ optional 'direction': max|min, secondaries); may be JSON-encoded.")],
        promotion_policy: Annotated[dict | str, Field(description='Pre-registered promotion rule (confidence bar, safety gates); object or JSON-encoded.')],
        holdouts: Annotated[dict | str | None, Field(description='Held-out evaluation slices the contract declares; object or JSON-encoded.')] = None,
        budget: Annotated[dict | str | None, Field(description='Budget recorded on the contract; object or JSON-encoded string.')] = None,
    ) -> Annotated[CallToolResult, CreateEvaluationContractOut]:
        """Create an evaluation contract for a programme (RSI Phase 0).

        Contracts are insert-only and versioned per programme: each new
        contract for the same programme gets version = max+1 and
        supersedes the previous. metrics and promotion_policy may be
        sent as JSON-encoded strings.

        The powered-policy gate enforces presence of the design keys
        (sesoi_d, target_power, min_evidence_rung), not adequacy —
        target_power: 0.5 pre-registers fine; adequacy surfaces
        downstream as underpowered / power_acknowledged on the
        campaign, not as a mint refusal.

        Enforcement: programme must exist.
        """
        try:
            metrics = coerce_json(metrics, dict, "metrics")
            promotion_policy = coerce_json(
                promotion_policy, dict, "promotion_policy"
            )
            if holdouts is not None:
                holdouts = coerce_json(holdouts, dict, "holdouts")
            if budget is not None:
                budget = coerce_json(budget, dict, "budget")

            # Enforcement: contract must attach to a real programme
            err = check_contract_programme_exists(programme_id, store)
            if err:
                return fail(json.dumps({"error": err}))

            # Enforcement: commitment 1 — a closed programme is immutable
            err = check_programme_active(store.get_programme(programme_id))
            if err:
                return fail(json.dumps({"error": err}))

            # Enforcement: a contract that cannot resolve a primary
            # metric and direction can never spawn a programme —
            # refuse at mint rather than after a campaign pins it.
            if err := check_contract_metrics(metrics):
                return fail(json.dumps({"error": err}))

            # Enforcement: a contract without declared SESOI/power is
            # not a preregistration — the policy must carry the design
            # inputs before any campaign can open under it.
            if err := check_promotion_policy_power(promotion_policy):
                return fail(json.dumps({"error": err}))

            existing = store.list_evaluation_contracts(programme_id)
            version = (max(c.version for c in existing) + 1) if existing else 1
            contract = EvaluationContract(
                id=f"contract-{uuid.uuid4().hex[:8]}",
                programme_id=programme_id,
                version=version,
                metrics=metrics,
                holdouts=holdouts,
                budget=budget,
                promotion_policy=promotion_policy,
            )
            store.create_evaluation_contract(contract)
            return ok({
                "contract_id": contract.id,
                "version": version,
                "status": "created",
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def get_evaluation_contract(contract_id: Annotated[str, Field(description='ID of the evaluation contract to read.')]) -> Annotated[CallToolResult, GetEvaluationContractOut]:
        """Read one evaluation contract (read-only).

        Contracts are insert-only and versioned per programme — the
        promotion pipeline reads them to learn which metrics a
        campaign is scored under.
        """
        try:
            c = store.get_evaluation_contract(contract_id)
            if c is None:
                return fail(json.dumps({
                    "error": f"Evaluation contract not found: {contract_id}"
                }))
            return ok({
                "contract": {
                    "id": c.id,
                    "programme_id": c.programme_id,
                    "version": c.version,
                    "metrics": c.metrics,
                    "holdouts": c.holdouts,
                    "budget": c.budget,
                    "promotion_policy": c.promotion_policy,
                    "created_at": c.created_at,
                }
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    async def record_promotion_decision(
        candidate_id: Annotated[str, Field(description='Candidate the verdict applies to; must exist.')],
        verdict: Annotated[Literal['promote', 'reject', 'hold', 'rollback'], Field(description="promote | reject | hold | rollback — insert-only; reversal is a new 'rollback' decision.")],
        evidence_refs: Annotated[list[str] | str, Field(description='Evidence_ref IDs (from pull_evidence) the decision cites — ≥1 required; may be a JSON-encoded list.')],
        rationale: Annotated[str, Field(description='Non-empty justification — accountability is first-class.')],
        decided_by: Annotated[str, Field(description="Attributable decider (e.g. 'human:<name>' or a protocol/improver id).")],
        contract_id: Annotated[str | None, Field(description='Optional contract the decision was scored under; must exist if given.')] = None,
        claimed_rung: Annotated[str | None, Field(description="Evidence rung this verdict claims (not_worth|positive|strong|very_strong — the Kass–Raftery ladder). Required on 'promote' when the cited contract declares min_evidence_rung.")] = None,
        computed_rung: Annotated[str | None, Field(description="Evidence rung the promotion channel measured (forwarded by zetesis record_promotion_verdict — the rung close computed from the arm data). Stored beside claimed for audit; upstream cannot recompute it.")] = None,
        bf_2ln: Annotated[float | None, Field(description="The measured 2 ln BF bound forwarded with computed_rung. Stored for audit.")] = None,
    ) -> Annotated[CallToolResult, RecordPromotionDecisionOut]:
        """Record an attributed verdict on a candidate (RSI Phase 0).

        verdict: promote | reject | hold | rollback. Decisions are
        insert-only — reversal is a new decision ('rollback'), never a
        mutation. evidence_refs may be sent as a JSON-encoded string
        list. decided_by is required: attribution is first-class.

        Enforcement: verdict enum, non-empty rationale/decided_by,
        candidate (and contract, if given) must exist.
        """
        try:
            evidence_refs = coerce_json(evidence_refs, list, "evidence_refs")
            # Enforcement: valid verdict + attribution + real references
            err = check_decision_valid(
                verdict, rationale, decided_by, candidate_id, contract_id,
                store, evidence_refs,
            )
            if err:
                return fail(json.dumps({"error": err}))

            # Foreign-prefix refs are accepted opaquely (their owner
            # is another loop's server) — but whether a ref ever
            # resolved must be auditable, not silent (rc-15 F3).
            # claim- ids are probed on the claims channel when wired;
            # everything else is marked unverified rather than
            # guessed at. Zetesis legitimately forwards eref- ids —
            # refusal is not the fix, honest marking is.
            unverified_refs = await _classify_unverified_refs(
                adaptor, evidence_refs
            )

            # Declared-vs-claimed rung (plan-20260929-1641Z W4): a
            # promote under a rung-declaring contract must claim an
            # evidence rung at or above the contract minimum.
            declared_rung = None
            if contract_id is not None:
                c = store.get_evaluation_contract(contract_id)
                declared_rung = (c.promotion_policy or {}).get(
                    "min_evidence_rung"
                )
            if claimed_rung is not None and (
                claimed_rung not in _gc.EVIDENCE_RUNGS
            ):
                return fail(json.dumps({
                    "error": f"claimed_rung must be one of "
                    f"{'|'.join(_gc.EVIDENCE_RUNGS)}, got: "
                    f"{claimed_rung!r}"
                }))
            if computed_rung is not None and (
                computed_rung not in _gc.EVIDENCE_RUNGS
            ):
                return fail(json.dumps({
                    "error": f"computed_rung must be one of "
                    f"{'|'.join(_gc.EVIDENCE_RUNGS)}, got: "
                    f"{computed_rung!r}"
                }))
            if verdict == "promote" and declared_rung is not None:
                if claimed_rung is None:
                    return fail(json.dumps({
                        "error": f"promote under contract "
                        f"{contract_id} must claim an evidence rung — "
                        f"the contract declares min_evidence_rung="
                        f"'{declared_rung}'. Claim the rung the "
                        "evidence reaches, or the verdict is bare."
                    }))
                if not _gc.rung_at_least(claimed_rung, declared_rung):
                    return fail(json.dumps({
                        "error": f"claimed_rung '{claimed_rung}' is "
                        f"below the contract minimum '{declared_rung}' "
                        f"(Kass–Raftery 2 ln BF "
                        f"{_gc.min_evidence_rung(claimed_rung):.0f} < "
                        f"{_gc.min_evidence_rung(declared_rung):.0f}) "
                        "— a promote cannot assert less evidence than "
                        "the contract requires."
                    }))

            decision = PromotionDecision(
                id=f"decision-{uuid.uuid4().hex[:8]}",
                candidate_id=candidate_id,
                contract_id=contract_id,
                verdict=verdict,
                evidence_refs=evidence_refs,
                unverified_refs=unverified_refs,
                rationale=rationale,
                decided_by=decided_by,
                declared_rung=declared_rung,
                claimed_rung=claimed_rung,
                computed_rung=computed_rung,
                bf_2ln=bf_2ln,
            )
            store.create_promotion_decision(decision)
            return ok({
                "decision_id": decision.id,
                "status": "recorded",
                "declared_rung": declared_rung,
                "claimed_rung": claimed_rung,
                "computed_rung": computed_rung,
                "unverified_refs": unverified_refs,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))
