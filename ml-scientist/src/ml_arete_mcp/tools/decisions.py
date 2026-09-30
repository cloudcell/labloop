"""Meta-decision, promotion, canary, and rollback tools.

record_meta_decision is the Loop-2 sibling of Loop-0's
record_promotion_decision: insert-only, attribution required,
verdict ∈ {promote, reject, hold, rollback} — reversal is a new
decision, never an edit. promote_policy moves the champion pointer
only behind a recorded promote decision; rollback restores the prior
champion and records its own decision row.

Decisions mint `methodological` claims to anamnesis with derived_from
edges to the entities their evidence consulted — minting failure is
reported, never blocks the decision (memory is a byproduct, not a
gate).
"""

from __future__ import annotations

import json
import uuid

from ..enforcement.checks import (
    check_decision_evidence,
    check_decision_inputs,
    check_promote_allowed,
    check_promote_authority,
)
from ..state.models import (
    CanaryDeployment,
    CanaryStatus,
    DecisionVerdict,
    MetaDecision,
    PolicyStatus,
    PolicyVersion,
    TournamentStatus,
)
from ..state.store import ImproverStore
from .. import _grounded_constants as _gc
from .schemas import coerce_json, fail, ok, PromotePolicyOut, RecordMetaDecisionOut, CloseCanaryOut, ListDecisionsOut, RecordCanaryOut, RollbackOut
from typing import Annotated, Literal
from pydantic import Field
from mcp.types import CallToolResult

# Prefix → claim_edges ref_type for minted derived_from edges —
# the union of Loop-0/Loop-1/anamnesis id conventions and this
# server's own. Unrecognized prefixes are external by honesty.
_REF_TYPE_BY_PREFIX = {
    "trial-": "trial",
    "obs-": "observation",
    "conc-": "conclusion",
    "prog-": "programme",
    "claim-": "claim",
    "inv-": "investigation",
    "find-": "finding",
    "archive-": "archive",
    "imp-": "improver",
    "tourn-": "tournament",
    "tres-": "tournament_result",
    "mcp-": "proposal",
    "mcontract-": "meta_contract",
    "mdec-": "meta_decision",
    "pol-": "policy_version",
    "canary-": "canary_deployment",
    "cand-": "candidate",
    "contract-": "contract",
    "decision-": "decision",
    "bundle-": "bundle",
    "data-ref-": "dataref",
    "hyp-": "hypothesis",
    "eref-": "evidence_ref",
    "camp-": "campaign",
    "spawn-": "campaign_spawn",
}

# Anamnesis's RefType vocabulary, mirrored locally (ADR-0001/0002: no
# cross-package imports — the test suite pins this mirror to the real
# enum). A mapped value outside this set degrades to external rather
# than emitting a ref_type the claim graph rejects.
_CLAIM_REF_TYPES = frozenset({
    "claim", "trial", "observation", "conclusion", "programme",
    "investigation", "finding", "archive", "improver", "tournament",
    "tournament_result", "proposal", "meta_contract", "meta_decision",
    "policy_version", "canary_deployment", "candidate", "contract",
    "decision", "bundle", "dataref", "reference", "external",
    "hypothesis", "evidence_ref", "campaign", "campaign_spawn",
})


def _ref_type_for(ref_id: str) -> str:
    for prefix, ref_type in _REF_TYPE_BY_PREFIX.items():
        if ref_id.startswith(prefix):
            return (
                ref_type
                if ref_type in _CLAIM_REF_TYPES
                else "external"
            )
    return "external"


async def _mint_decision_claim(
    store, adaptors, decision,
    confidence: float | None = None,
    confidence_basis: str | None = None,
) -> tuple:
    """Mint the methodological claim for a meta-decision row.

    Shared by record_meta_decision and rollback — every insert-only
    mdec- row carries the same grounding contract. Returns
    (claim_id, claim_status, claim_error, edges_created); failures are
    reported, never raised — a claims outage must not block a decision.

    confidence/confidence_basis come from the caller's evidence
    resolution (plan-20260929-1642Z): the posterior bound when the
    cited tournament carries the statistic, else the grounded prior
    ceiling labelled weakly_grounded — never a bare literal.
    """
    claim_status = "skipped"
    claim_id = None
    claim_error = None
    edges_created = 0
    if adaptors.claims is None:
        return None, "disabled", None, 0
    try:
        upstream_ids: set[str] = set()
        for eref_id in decision.evidence_refs:
            eref = store.get_evidence_ref(eref_id)
            if eref is not None:
                upstream_ids.update(eref.ref_ids)
        consulted = sorted(upstream_ids)
        # Evidence edges mint inline — anamnesis caps
        # unevidenced claims at the prior ceiling, and
        # post-hoc relate hits the chicken-and-egg.
        minted = await adaptors.claims.assert_claim(
            content=(
                f"Loop-2 meta-decision '{decision.verdict.value}' on "
                f"improver {decision.candidate_improver_id}: "
                f"{decision.rationale}"
            ),
            type="methodological",
            confidence=(
                confidence
                if confidence is not None
                else _gc.PRIOR_CONFIDENCE_MAX.value
            ),
            confidence_basis=(
                confidence_basis or "weakly_grounded"
            ),
            evidence=[
                {
                    "to_ref": ref_id,
                    "ref_type": _ref_type_for(ref_id),
                    "relation": "derived_from",
                }
                for ref_id in consulted
            ],
            source_id=decision.id,
        )
        claim_id = minted["claim_id"]
        edges_created += len(consulted)
        claim_status = "minted"
    except Exception as exc:
        # The mint reason must survive — a bare "failed"
        # is undiagnosable (rc-7 Q2).
        claim_status = "failed"
        claim_error = str(exc)
    return claim_id, claim_status, claim_error, edges_created


def _decision_json(d: MetaDecision) -> dict:
    return {
        "id": d.id,
        "candidate_improver_id": d.candidate_improver_id,
        "contract_id": d.contract_id,
        "tournament_id": d.tournament_id,
        "verdict": d.verdict.value,
        "evidence_refs": d.evidence_refs,
        "rationale": d.rationale,
        "decided_by": d.decided_by,
        "claim_id": d.claim_id,
        "claim_error": d.claim_error,
        "declared_rung": d.declared_rung,
        "claimed_rung": d.claimed_rung,
        "computed_rung": d.computed_rung,
        "confidence_basis": d.confidence_basis,
        "created_at": d.created_at,
    }


def register(mcp, store: ImproverStore, adaptors) -> None:
    """Register decision/promotion/rollback tools on the MCP server."""

    @mcp.tool()
    async def record_meta_decision(
        candidate_improver_id: Annotated[str, Field(description='ID of the candidate improver version this call targets.')],
        verdict: Annotated[Literal['promote', 'reject', 'hold', 'rollback'], Field(description='promote | reject | hold | rollback — insert-only; reversal is a new decision, never an edit.')],
        evidence_refs: Annotated[list | str, Field(description='Evidence_ref IDs (from pull_evidence) the decision cites — ≥1 required; may be a JSON-encoded list.')],
        rationale: Annotated[str, Field(description='Non-empty justification — accountability is first-class.')],
        decided_by: Annotated[str, Field(description="Attributable decider — 'human:<name>' required when the candidate implements a conditional proposal; 'arete:protocol' or an improver id otherwise.")],
        contract_id: Annotated[str | None, Field(description='Optional contract the decision cites; must exist if given.')] = None,
        tournament_id: Annotated[str | None, Field(description='ID of the target tournament.')] = None,
        claimed_rung: Annotated[str | None, Field(description="Evidence rung this decision claims (not_worth|positive|strong|very_strong — the Kass–Raftery ladder). Required on 'promote' when the cited contract declares min_evidence_rung.")] = None,
    ) -> Annotated[CallToolResult, RecordMetaDecisionOut]:
        """Record a governed meta-decision — insert-only.

        verdict: promote | reject | hold | rollback. Every decision
        requires ≥1 evidence_ref (accountability), a non-empty
        rationale, and an attributable decided_by ('human:<name>' for
        human authority — required when the candidate implements a
        conditional proposal; 'arete:protocol' or an improver id
        otherwise). Reversal is a NEW decision, never an edit.

        Mints a `methodological` claim to anamnesis (source_id = the
        decision) with derived_from edges to the upstream entities
        the cited evidence consulted. claim_status is reported:
        'minted' | 'failed' | 'disabled' — never blocks the decision.
        """
        try:
            candidate = store.get_improver(candidate_improver_id)
            if candidate is None:
                return fail(json.dumps({
                    "error": f"Candidate improver not found: "
                    f"{candidate_improver_id}."
                }))
            evidence_refs = coerce_json(
                evidence_refs, list, "evidence_refs"
            )
            if err := check_decision_inputs(
                verdict, rationale, decided_by, evidence_refs
            ):
                return fail(json.dumps({"error": err}))
            # The conditional-proposal human gate fires at the write,
            # not only at the pointer move — an agent-authored promote
            # must never reach the insert-only record or mint a claim.
            if verdict == "promote" and (
                err := check_promote_authority(
                    store, candidate, decided_by
                )
            ):
                return fail(json.dumps({"error": err}))
            if err := check_decision_evidence(
                store, candidate_improver_id, evidence_refs
            ):
                return fail(json.dumps({"error": err}))

            declared_rung = None
            if contract_id is not None:
                contract = store.get_meta_contract(contract_id)
                if contract is None:
                    return fail(json.dumps({
                        "error": f"Meta-contract not found: "
                        f"{contract_id}."
                    }))
                declared_rung = (contract.promotion_policy or {}).get(
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
            # Declared-vs-claimed (plan-20260929-1641Z W4): a promote
            # under a rung-declaring contract must claim a rung at or
            # above the contract minimum — the record of which rung
            # the decider asserts. The BF-producing statistic that
            # makes claimed evidential is the posterior plan's job.
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
            tournament = None
            if tournament_id is not None:
                t = store.get_tournament(tournament_id)
                if t is None:
                    return fail(json.dumps({
                        "error": f"Tournament not found: {tournament_id}."
                    }))
                if candidate_improver_id not in {
                    t.parent_improver_id, t.candidate_improver_id
                }:
                    return fail(json.dumps({
                        "error": f"Tournament {tournament_id} does not "
                        f"involve {candidate_improver_id} — the "
                        "decision must cite the comparison it rests "
                        "on."
                    }))
                if t.status == TournamentStatus.voided:
                    return fail(json.dumps({
                        "error": f"Tournament {tournament_id} is "
                        "voided — decisions rest on comparisons, and "
                        "a voided record is not a comparison."
                    }))
                tournament = t

            # Evidence-vs-ladder ceiling (plan-20260929-1642Z): when
            # the cited tournament closed with a measured bound, a
            # promote may not claim past it — declared ≤ claimed ≤
            # computed. When no statistic could be computed
            # (n_achieved < 2) the ceiling falls back to the cited
            # contract's declared minimum: a verdict on unmeasured
            # work asserts the declared rung and no more (rc-12).
            computed_rung = None
            if tournament is not None and tournament.bf_2ln is not None:
                computed_rung = _gc.rung_for_2lnbf(tournament.bf_2ln)
            ceiling_rung = (
                computed_rung if computed_rung is not None
                else declared_rung
            )
            if (
                verdict == "promote"
                and claimed_rung is not None
                and ceiling_rung is not None
                and not _gc.rung_at_least(ceiling_rung, claimed_rung)
            ):
                if computed_rung is not None:
                    msg = (
                        f"claimed_rung '{claimed_rung}' exceeds "
                        f"the evidence — tournament {tournament_id} "
                        f"measured 2 ln BF = {tournament.bf_2ln:.2f}, "
                        f"reaching '{computed_rung}' (the oracle bound); "
                        f"claiming '{claimed_rung}' asserts more than "
                        "the data's own bound. Claim what the evidence "
                        "reaches."
                    )
                else:
                    measured_state = (
                        f"computed (n_achieved={tournament.n_achieved})"
                        if tournament is not None
                        else "computed for this decision"
                    )
                    msg = (
                        f"claimed_rung '{claimed_rung}' exceeds the "
                        "ceiling on unmeasured work — no statistic was "
                        f"{measured_state}, "
                        f"so the bound is the contract's declared "
                        f"minimum '{declared_rung}'. Claiming "
                        f"'{claimed_rung}' asserts evidence that does "
                        "not exist."
                    )
                return fail(json.dumps({
                    "error": msg,
                    "computed_rung": computed_rung,
                    "bf_2ln": tournament.bf_2ln if tournament else None,
                }))

            decision = MetaDecision(
                id=f"mdec-{uuid.uuid4().hex[:8]}",
                candidate_improver_id=candidate_improver_id,
                contract_id=contract_id,
                tournament_id=tournament_id,
                verdict=DecisionVerdict(verdict),
                evidence_refs=evidence_refs,
                rationale=rationale,
                decided_by=decided_by,
                declared_rung=declared_rung,
                claimed_rung=claimed_rung,
                computed_rung=computed_rung,
            )

            # Confidence the minted claim carries (plan-20260929-1642Z):
            # when the cited tournament measured a p, promote mints the
            # NAP D-1 posterior bound; reject/rollback mint the
            # conservative H₀ bound; hold asserts neither and mints the
            # prior ceiling. No statistic → the ceiling, labelled
            # weakly_grounded — evidence was consulted, no likelihood
            # exists on a meta-decision.
            confidence = None
            confidence_basis = None
            if (
                tournament is not None
                and tournament.bf_2ln is not None
                and verdict in ("promote", "reject", "rollback")
            ):
                prior = (
                    tournament.prior
                    if tournament.prior is not None
                    else _gc.PRIOR_CONFIDENCE_MAX.value
                )
                # Posterior straight from the persisted 2 ln BF —
                # the p → z round-trip underflows to p=0 at
                # extreme z, losing the statistic.
                posterior = _gc.posterior_from_2lnbf(
                    prior, tournament.bf_2ln
                )
                confidence = (
                    posterior if verdict == "promote" else 1 - posterior
                )
                confidence_basis = "grounded"
            else:
                confidence = _gc.PRIOR_CONFIDENCE_MAX.value
                confidence_basis = "weakly_grounded"
            decision.confidence_basis = confidence_basis

            # Mint the methodological claim — the claim's source_id
            # names the decision record being inserted next.
            (
                claim_id,
                claim_status,
                claim_error,
                edges_created,
            ) = await _mint_decision_claim(
                store, adaptors, decision,
                confidence=confidence,
                confidence_basis=confidence_basis,
            )

            decision.claim_id = claim_id
            decision.claim_error = claim_error
            store.create_meta_decision(decision)

            return ok({
                "decision_id": decision.id,
                "verdict": verdict,
                "claim_id": claim_id,
                "claim_status": claim_status,
                "claim_error": claim_error,
                "edges_created": edges_created,
                "declared_rung": declared_rung,
                "claimed_rung": claimed_rung,
                "computed_rung": computed_rung,
                "confidence_basis": confidence_basis,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def list_decisions(
        candidate_improver_id: Annotated[str | None, Field(description='ID of the candidate improver version this call targets.')] = None,
    ) -> Annotated[CallToolResult, ListDecisionsOut]:
        """List meta-decisions, oldest first — the insert-only record.

        Optionally filtered to one candidate. The full history is the
        point: promote → rollback chains are the governance trail.
        """
        try:
            decisions = store.list_meta_decisions(candidate_improver_id)
            return ok({
                "decisions": [_decision_json(d) for d in decisions],
                "total": len(decisions),
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def promote_policy(
        candidate_improver_id: Annotated[str, Field(description='ID of the candidate improver version this call targets.')],
        policy: Annotated[dict | str, Field(description='Policy body to mint as the active policy_version; may be JSON-encoded.')],
    ) -> Annotated[CallToolResult, PromotePolicyOut]:
        """Promote a candidate to champion and mint its policy_version.

        Requires the candidate's LATEST meta_decision to be 'promote'
        — a later reject/hold/rollback supersedes. If the candidate
        implements a conditional proposal, that decision's decided_by
        must name a human authority ('human:<name>').

        One transaction: mint policy_version (status 'active',
        recording the displaced champion), move the champion pointer.
        Installing the policy into Loop-1's population machinery is a
        later increment — this server governs the record.
        """
        try:
            err, decision = check_promote_allowed(
                store, candidate_improver_id
            )
            if err:
                return fail(json.dumps({"error": err}))
            policy = coerce_json(policy, dict, "policy")
            if not policy:
                return fail(json.dumps({
                    "error": "policy must be a non-empty object — the "
                    "versioned policy object written down to Loop 1 "
                    "in a later increment."
                }))

            previous = store.get_champion()
            pv = PolicyVersion(
                id=f"pol-{uuid.uuid4().hex[:8]}",
                improver_version_id=candidate_improver_id,
                decision_id=decision.id,
                policy=policy,
                previous_champion_id=previous.id if previous else None,
                status=PolicyStatus.active,
            )
            with store.transaction():
                store.create_policy_version(pv)
                if previous is not None:
                    store.set_champion_flag(previous.id, False)
                store.set_champion_flag(candidate_improver_id, True)

            return ok({
                "policy_version_id": pv.id,
                "champion": candidate_improver_id,
                "displaced_champion": (
                    previous.id if previous else None
                ),
                "status": "active",
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    async def rollback(
        candidate_improver_id: Annotated[str, Field(description='ID of the candidate improver version this call targets.')],
        rationale: Annotated[str, Field(description='Non-empty justification — accountability is first-class.')],
        decided_by: Annotated[str, Field(description="Attributable decider (e.g. 'human:<name>' or a protocol/improver id).")],
        evidence_refs: Annotated[list | str | None, Field(description='Evidence_ref IDs (from pull_evidence) the decision cites — ≥1 required; may be a JSON-encoded list.')] = None,
    ) -> Annotated[CallToolResult, RollbackOut]:
        """Roll a candidate back — a decision row plus pointer restore.

        Always records a 'rollback' meta_decision (attribution and
        evidence rules apply — cite the canary/tournament evidence
        that motivates it) and mints the methodological claim to
        anamnesis, exactly as record_meta_decision does — claim_status
        is reported, never blocks the rollback. If the candidate is
        the current champion,
        the pointer restores to the champion it displaced (recorded
        on its policy_version; falling back to its parent) and the
        policy is marked rolled_back. Reversal is an event, not an
        edit — the promote decision stays in the record.
        """
        try:
            candidate = store.get_improver(candidate_improver_id)
            if candidate is None:
                return fail(json.dumps({
                    "error": f"Improver not found: "
                    f"{candidate_improver_id}."
                }))
            evidence_refs = coerce_json(
                evidence_refs, list, "evidence_refs"
            ) if evidence_refs else []
            if err := check_decision_inputs(
                "rollback", rationale, decided_by, evidence_refs
            ):
                return fail(json.dumps({"error": err}))
            if err := check_decision_evidence(
                store, candidate_improver_id, evidence_refs
            ):
                return fail(json.dumps({"error": err}))

            decision = MetaDecision(
                id=f"mdec-{uuid.uuid4().hex[:8]}",
                candidate_improver_id=candidate_improver_id,
                verdict=DecisionVerdict.rollback,
                evidence_refs=evidence_refs,
                rationale=rationale,
                decided_by=decided_by,
            )

            # Same grounding contract as record_meta_decision — every
            # mdec- row gets its claim, so the insert-only record is
            # uniformly attributable.
            (
                claim_id,
                claim_status,
                claim_error,
                edges_created,
            ) = await _mint_decision_claim(store, adaptors, decision)
            decision.claim_id = claim_id
            decision.claim_error = claim_error

            restored = None
            if candidate.is_champion:
                active = store.get_active_policy(candidate_improver_id)
                restore_id = (
                    active.previous_champion_id if active else None
                ) or candidate.parent_id
                with store.transaction():
                    store.create_meta_decision(decision)
                    store.set_champion_flag(candidate_improver_id, False)
                    if active is not None:
                        store.set_policy_status(
                            active.id, PolicyStatus.rolled_back
                        )
                    if restore_id and store.get_improver(restore_id):
                        store.set_champion_flag(restore_id, True)
                        restored = restore_id
            else:
                store.create_meta_decision(decision)

            return ok({
                "decision_id": decision.id,
                "verdict": "rollback",
                "was_champion": candidate.is_champion,
                "restored_champion": restored,
                "claim_id": claim_id,
                "claim_status": claim_status,
                "claim_error": claim_error,
                "edges_created": edges_created,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def record_canary(
        policy_version_id: Annotated[str, Field(description='ID of the minted policy version this canary activates.')],
        scope: Annotated[dict | str, Field(description='The scope the canary ran under (traffic slice/config); may be JSON-encoded.')],
    ) -> Annotated[CallToolResult, RecordCanaryOut]:
        """Record a scoped canary activation of a policy version.

        A canary is a process record — the scaffold has no traffic-
        shaping fabric; this captures that a scoped activation was
        run, under what scope, so a later regression can motivate a
        rollback decision with real evidence.
        """
        try:
            pv = store.get_policy_version(policy_version_id)
            if pv is None:
                return fail(json.dumps({
                    "error": f"Policy version not found: "
                    f"{policy_version_id}."
                }))
            scope = coerce_json(scope, dict, "scope")
            canary = CanaryDeployment(
                id=f"canary-{uuid.uuid4().hex[:8]}",
                policy_version_id=policy_version_id,
                scope=scope,
            )
            store.create_canary(canary)
            return ok({
                "canary_id": canary.id,
                "status": "open",
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def close_canary(canary_id: Annotated[str, Field(description='ID of the canary record to close.')], status: Annotated[Literal['passed', 'regressed'], Field(description="passed | regressed — 'regressed' motivates a rollback decision.")]) -> Annotated[CallToolResult, CloseCanaryOut]:
        """Close a canary record: status 'passed' | 'regressed'.

        'regressed' marks the record — the governed response is a
        rollback decision (rollback tool), which is where the pointer
        actually moves.
        """
        try:
            canary = store.get_canary(canary_id)
            if canary is None:
                return fail(json.dumps({
                    "error": f"Canary not found: {canary_id}."
                }))
            if canary.status != CanaryStatus.open:
                return fail(json.dumps({
                    "error": f"Canary {canary_id} is already "
                    f"{canary.status.value}."
                }))
            if status not in {"passed", "regressed"}:
                return fail(json.dumps({
                    "error": "status must be 'passed' or 'regressed', "
                    f"got: {status}"
                }))
            store.close_canary(canary_id, CanaryStatus(status))
            payload = {
                "canary_id": canary_id,
                "status": status,
            }
            if status == "regressed":
                payload["next_step"] = (
                    "Record a rollback decision citing this canary's "
                    "evidence — the pointer moves through rollback, "
                    "not through this record."
                )
            return ok(payload)
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))
