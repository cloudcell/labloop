"""Tournament tools — the paired ancestral comparison.

open_tournament pairs parent and candidate arms under one budget B
(the equal-budget requirement is structural: there is one budget
object, not two). record_tournament_result appends what each arm
produced — the driving client runs descendant evaluations through
the lower loops and records back; the server does not spawn runs.
close_tournament seals the record and computes recursive_gain.

If the Loop-1 population substrate is absent, tournaments simply
have no results — and close refuses loudly rather than fabricating
a comparison.
"""

from __future__ import annotations

import json
import uuid

from ..enforcement.checks import (
    check_ancestry,
    check_arm_valid,
    check_tournament_open,
    compute_recursive_gain,
)
from ..state.models import (
    Tournament,
    TournamentArm,
    TournamentResult,
    TournamentStatus,
)
from ..state.store import ImproverStore
from .schemas import coerce_json, fail, ok, CloseTournamentOut, CorrectTournamentResultOut, ListTournamentsOut, OpenTournamentOut, RecordTournamentResultOut, GetTournamentOut, VoidTournamentOut
from typing import Annotated, Literal
from pydantic import Field
from mcp.types import CallToolResult


def _result_json(r: TournamentResult) -> dict:
    return {
        "id": r.id,
        "arm": r.arm.value,
        "descendant_spec": r.descendant_spec,
        "metrics": r.metrics,
        "corrections": r.corrections,
        "created_at": r.created_at,
    }


def _tournament_json(store: ImproverStore, t: Tournament) -> dict:
    results = store.list_tournament_results(t.id)
    return {
        "id": t.id,
        "contract_id": t.contract_id,
        "parent_improver_id": t.parent_improver_id,
        "candidate_improver_id": t.candidate_improver_id,
        "budget": t.budget,
        "seeds": t.seeds,
        "status": t.status.value,
        "recursive_gain": t.recursive_gain,
        "created_at": t.created_at,
        "closed_at": t.closed_at,
        "void": t.void,
        "results": {
            "parent": [
                _result_json(r) for r in results
                if r.arm == TournamentArm.parent
            ],
            "candidate": [
                _result_json(r) for r in results
                if r.arm == TournamentArm.candidate
            ],
        },
        "evidence_refs": [
            {
                "id": e.id, "source": e.source.value, "tool": e.tool,
                "ref_ids": e.ref_ids, "created_at": e.created_at,
            }
            for e in store.list_evidence_refs("tournament", t.id)
        ],
    }


def register(mcp, store: ImproverStore, adaptors) -> None:
    """Register tournament tools on the MCP server."""

    @mcp.tool()
    def open_tournament(
        contract_id: Annotated[str, Field(description='Meta-evaluation contract version the tournament freezes and runs under.')],
        parent_improver_id: Annotated[str, Field(description='The incumbent-side improver the candidate must descend from.')],
        candidate_improver_id: Annotated[str, Field(description='The candidate improver — must descend from the parent (ancestry check).')],
        budget: Annotated[dict | str, Field(description='Single budget governing BOTH arms — equal spend is structural. May be JSON-encoded.')],
        seeds: Annotated[list | str | None, Field(description='Seed set for the arm (list of ints); may be JSON-encoded.')] = None,
    ) -> Annotated[CallToolResult, OpenTournamentOut]:
        """Open a paired ancestral tournament.

        One budget object B governs BOTH arms — equal spend is
        structural, not a promise. The candidate must descend from
        the parent (ancestry check: the tournament compares an
        improver against its own child, not two strangers). Opening
        freezes the contract — the metric a tournament runs under
        cannot drift mid-comparison.

        The server records; it does not orchestrate. Descendant
        evaluations are enacted by the driving client through the
        lower loops and recorded back via record_tournament_result.
        """
        try:
            contract = store.get_meta_contract(contract_id)
            if contract is None:
                return fail(json.dumps({
                    "error": f"Meta-contract not found: {contract_id}."
                }))
            for label, iid in (
                ("parent", parent_improver_id),
                ("candidate", candidate_improver_id),
            ):
                if store.get_improver(iid) is None:
                    return fail(json.dumps({
                        "error": f"{label} improver not found: {iid}."
                    }))
            if err := check_ancestry(
                store, parent_improver_id, candidate_improver_id
            ):
                return fail(json.dumps({"error": err}))

            budget = coerce_json(budget, dict, "budget")
            if not budget:
                return fail(json.dumps({
                    "error": "budget must be a non-empty object — the "
                    "paired B (e.g. {\"descendant_runs\": 3, "
                    "\"calls_per_run\": 1500}). One budget governs "
                    "both arms."
                }))
            if seeds is not None:
                seeds = coerce_json(seeds, list, "seeds")

            tournament = Tournament(
                id=f"tourn-{uuid.uuid4().hex[:8]}",
                contract_id=contract_id,
                parent_improver_id=parent_improver_id,
                candidate_improver_id=candidate_improver_id,
                budget=budget,
                seeds=seeds,
            )
            with store.transaction():
                store.create_tournament(tournament)
                store.freeze_meta_contract(contract_id)

            return ok({
                "tournament_id": tournament.id,
                "status": "open",
                "contract_frozen": True,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def record_tournament_result(
        tournament_id: Annotated[str, Field(description='ID of the target tournament.')],
        arm: Annotated[Literal['parent', 'candidate'], Field(description="Tournament arm: 'parent' (incumbent) | 'candidate' (the child being evaluated).")],
        descendant_spec: Annotated[dict | str, Field(description='What the arm produced — the descendant generated under the shared budget; object or JSON-encoded.')],
        metrics: Annotated[dict | str, Field(description="Result metrics — must carry the contract's primary_metric; may carry 'seed' (per-seed bests feed E[best descendant]); may be JSON-encoded.")],
    ) -> Annotated[CallToolResult, RecordTournamentResultOut]:
        """Append one result to a tournament arm — insert-only.

        arm: 'parent' | 'candidate'. descendant_spec: what the arm
        produced (the Loop-1 descendant it generated under B).
        metrics: must carry the contract's primary_metric (and may
        carry 'seed' — per-seed bests are what E[best descendant]
        averages over at close). Results on a closed tournament are
        refused: the record is sealed.
        """
        try:
            err, tournament = check_tournament_open(store, tournament_id)
            if err:
                return fail(json.dumps({"error": err}))
            if err := check_arm_valid(arm):
                return fail(json.dumps({"error": err}))
            descendant_spec = coerce_json(
                descendant_spec, dict, "descendant_spec"
            )
            metrics = coerce_json(metrics, dict, "metrics")

            contract = store.get_meta_contract(tournament.contract_id)
            primary = contract.metrics["primary_metric"]
            if primary not in metrics:
                return fail(json.dumps({
                    "error": f"metrics must carry the contract's "
                    f"primary_metric '{primary}' — a result that "
                    "cannot enter the recursive_gain computation is "
                    "not a tournament result."
                }))

            seed = metrics.get("seed", descendant_spec.get("seed"))
            if seed is not None and not isinstance(
                seed, (str, int, float)
            ):
                return fail(json.dumps({
                    "error": "'seed' must be a scalar (str/int/float) "
                    "naming one replicate — per-seed results are "
                    "recorded one row per seed, not as aggregates. "
                    f"Got {type(seed).__name__}: "
                    f"{json.dumps(seed, default=str)[:120]}"
                }))

            result = TournamentResult(
                id=f"tres-{uuid.uuid4().hex[:8]}",
                tournament_id=tournament_id,
                arm=TournamentArm(arm),
                descendant_spec=descendant_spec,
                metrics=metrics,
            )
            store.create_tournament_result(result)
            return ok({
                "result_id": result.id,
                "tournament_id": tournament_id,
                "arm": arm,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def correct_tournament_result(
        result_id: Annotated[str, Field(description='ID of the tournament result row to correct (tres-*).')],
        reason: Annotated[str, Field(description="Why the correction is recorded — mandatory, appended to the row's audit trail.")],
        metrics: Annotated[dict | str | None, Field(description="Replacement metrics object — e.g. a canonical per-seed shape. May be JSON-encoded.")] = None,
        descendant_spec: Annotated[dict | str | None, Field(description="Replacement descendant_spec object. May be JSON-encoded.")] = None,
    ) -> Annotated[CallToolResult, CorrectTournamentResultOut]:
        """Correct a tournament result row — the recorded repair act.

        For malformed records: e.g. a non-scalar 'seed' (dict/list
        aggregate) that close_tournament cannot group. The correction
        is appended to the row's corrections audit trail with the
        previous values and the reason — never a silent rewrite, never
        a direct DB edit. Only possible while the tournament is open;
        a sealed record stays sealed. metrics and/or descendant_spec
        must be given.
        """
        try:
            if not reason or not reason.strip():
                return fail(json.dumps({
                    "error": "reason is required — a correction "
                             "without a stated basis is a silent "
                             "rewrite",
                }))
            if metrics is None and descendant_spec is None:
                return fail(json.dumps({
                    "error": "nothing to correct — pass metrics "
                             "and/or descendant_spec",
                }))
            if metrics is not None:
                metrics = coerce_json(metrics, dict, "metrics")
            if descendant_spec is not None:
                descendant_spec = coerce_json(
                    descendant_spec, dict, "descendant_spec"
                )
            seed_src = metrics if metrics is not None else descendant_spec
            seed = (seed_src or {}).get("seed")
            if seed is not None and not isinstance(
                seed, (str, int, float)
            ):
                return fail(json.dumps({
                    "error": "corrected 'seed' must be a scalar "
                    "(str/int/float) naming one replicate — got "
                    f"{type(seed).__name__}",
                }))
            result = store.correct_tournament_result(
                result_id, reason,
                metrics=metrics, descendant_spec=descendant_spec,
            )
            return ok(result)
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def close_tournament(tournament_id: Annotated[str, Field(description='ID of the target tournament.')]) -> Annotated[CallToolResult, CloseTournamentOut]:
        """Seal the tournament and compute recursive_gain.

        recursive_gain is the per-seed-best ratio on the contract's
        primary metric, direction-normalized so >1 always means the
        candidate improver produces better descendants per unit
        budget (the promote case) — under direction=min the ratio is
        parent/candidate. Both arms need ≥1 result carrying the
        primary metric — closing an unpaired tournament is refused
        loudly rather than fabricating a comparison.
        """
        try:
            tournament = store.get_tournament(tournament_id)
            if tournament is None:
                return fail(json.dumps({
                    "error": f"Tournament not found: {tournament_id}."
                }))
            if tournament.status != TournamentStatus.open:
                return fail(json.dumps({
                    "error": f"Tournament {tournament_id} is already "
                    f"{tournament.status.value}."
                }))
            contract = store.get_meta_contract(tournament.contract_id)
            parent_results = store.list_tournament_results(
                tournament_id, arm="parent"
            )
            candidate_results = store.list_tournament_results(
                tournament_id, arm="candidate"
            )
            gain, err = compute_recursive_gain(
                contract, parent_results, candidate_results
            )
            if err:
                return fail(json.dumps({"error": err}))

            store.close_tournament(tournament_id, gain)
            return ok({
                "tournament_id": tournament_id,
                "status": "closed",
                "recursive_gain": gain,
                "primary_metric": contract.metrics["primary_metric"],
                "interpretation": (
                    "recursive_gain > 1 means the candidate improver "
                    "produces better descendants per unit budget — "
                    "the promote case. The meta_decision still "
                    "requires evidence, rationale, and attribution."
                ),
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def void_tournament(
        tournament_id: Annotated[str, Field(description='ID of the target tournament.')],
        rationale: Annotated[str, Field(description='Non-empty reason the tournament is dead — recorded on the record; a void without a stated basis is a silent delete.')],
        decided_by: Annotated[str, Field(description="Attributable decider — 'agent:<name>', 'human:<name>', or an improver id.")],
    ) -> Annotated[CallToolResult, VoidTournamentOut]:
        """Void an unpaired open tournament — the dead-record exit.

        open → voided: terminal, computes no recursive_gain, implies
        no comparison. For tournaments that were opened but can never
        honestly close — runs that never completed, smoke opens,
        abandoned evaluations. Refuses a paired tournament (both arms
        ≥1 result): that record has an honest exit — close_tournament.
        The rationale and decided_by are recorded on the row, so the
        record carries why it was abandoned rather than vanishing or
        sitting open forever.
        """
        try:
            tournament = store.get_tournament(tournament_id)
            if tournament is None:
                return fail(json.dumps({
                    "error": f"Tournament not found: {tournament_id}."
                }))
            if tournament.status != TournamentStatus.open:
                return fail(json.dumps({
                    "error": f"Tournament {tournament_id} is "
                             f"{tournament.status.value} — already "
                             "terminal.",
                }))
            if not rationale or not rationale.strip():
                return fail(json.dumps({
                    "error": "rationale is required — a void without "
                             "a stated basis is a silent delete",
                }))
            if not decided_by or not decided_by.strip():
                return fail(json.dumps({
                    "error": "decided_by is required — attribution "
                             "is mandatory",
                }))
            results = store.list_tournament_results(tournament_id)
            parent_n = sum(1 for r in results if r.arm == "parent")
            cand_n = sum(1 for r in results if r.arm == "candidate")
            if parent_n >= 1 and cand_n >= 1:
                return fail(json.dumps({
                    "error": f"Tournament {tournament_id} is paired "
                             f"({parent_n} parent, {cand_n} candidate "
                             "results) — close_tournament is the "
                             "honest exit; voiding would discard "
                             "computable evidence.",
                }))
            voided_at = store.void_tournament(
                tournament_id, rationale.strip(), decided_by.strip()
            )
            return ok({
                "tournament_id": tournament_id,
                "status": "voided",
                "voided_at": voided_at,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def get_tournament(tournament_id: Annotated[str, Field(description='ID of the target tournament.')]) -> Annotated[CallToolResult, GetTournamentOut]:
        """Read a tournament with both arms' results and its evidence
        trail."""
        try:
            t = store.get_tournament(tournament_id)
            if t is None:
                return fail(json.dumps({
                    "error": f"Tournament not found: {tournament_id}"
                }))
            return ok({"tournament": _tournament_json(store, t)})
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def list_tournaments(
        status: Annotated[Literal['open', 'closed', 'voided'] | None, Field(description='Optional status filter.')] = None,
        limit: Annotated[int, Field(description='Max rows to return (pagination).')] = 50,
        offset: Annotated[int, Field(description='Rows to skip before returning (pagination).')] = 0,
    ) -> Annotated[CallToolResult, ListTournamentsOut]:
        """List tournaments, newest first. status: open | closed."""
        try:
            if status is not None and status not in {
                s.value for s in TournamentStatus
            }:
                return fail(json.dumps({
                    "error": "status must be 'open' or 'closed', "
                    f"got: {status}"
                }))
            tournaments, total = store.list_tournaments(
                status=status, limit=limit, offset=offset
            )
            return ok({
                "tournaments": [
                    {
                        "id": t.id,
                        "contract_id": t.contract_id,
                        "parent_improver_id": t.parent_improver_id,
                        "candidate_improver_id": t.candidate_improver_id,
                        "status": t.status.value,
                        "recursive_gain": t.recursive_gain,
                        "created_at": t.created_at,
                        "closed_at": t.closed_at,
                    }
                    for t in tournaments
                ],
                "total": total,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))
