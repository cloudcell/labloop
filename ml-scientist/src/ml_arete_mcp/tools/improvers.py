"""Improver-version tools — the champion/challenger lineage.

register_improver records one improver version: genesis (no parent)
or a child implementing an admitted/conditional meta-change proposal.
The is_champion flag is a pointer, never an inference — the first
registered improver bootstraps as champion only because a protocol
needs an incumbent; thereafter the pointer moves exclusively through
promote_policy / rollback.
"""

from __future__ import annotations

from typing import Annotated
from pydantic import Field

import json
import uuid

from ..enforcement.checks import check_digest_claim
from ..state.models import ImproverVersion, ProposalStatus
from ..state.store import ImproverStore
from .schemas import coerce_json, fail, ok, RegisterImproverOut, GetImproverLineageOut, GetImproverOut, ListImproversOut
from mcp.types import CallToolResult


async def _resolve_via_loop0(adaptors, digest: str) -> bool:
    """Resolve a content digest upstream through the loop0 channel.

    Arete holds no blob store — existence is an episteme fact, read
    through the protocol channel (describe_blob), never by importing
    episteme internals or calling the bearer-gated ingest listener.
    Raises when the channel is absent or the call fails — the caller
    treats that as 'unverifiable', not as 'exists'.
    """
    if adaptors.loop0 is None:
        raise RuntimeError("loop0 channel absent")
    payload = await adaptors.loop0.pull(
        "describe_blob", {"content_hash": digest}
    )
    data = json.loads(payload) if isinstance(payload, str) else payload
    return bool(data.get("exists"))


def _improver_json(imp: ImproverVersion) -> dict:
    return {
        "id": imp.id,
        "parent_id": imp.parent_id,
        "proposal_id": imp.proposal_id,
        "code_artifact_digest": imp.code_artifact_digest,
        "harness_artifact_digest": imp.harness_artifact_digest,
        "model_ref": imp.model_ref,
        "search_policy_ref": imp.search_policy_ref,
        "capability_profile": imp.capability_profile,
        "is_champion": imp.is_champion,
        "created_at": imp.created_at,
    }


def register(mcp, store: ImproverStore, adaptors) -> None:
    """Register improver-lineage tools on the MCP server."""

    @mcp.tool()
    async def register_improver(
        code_artifact_digest: Annotated[str, Field(description="sha256:<64 hex> digest of the version's code artifact — must resolve to a blob ingested on episteme, or 'none' to declare no code artifact.")],
        model_ref: Annotated[str, Field(description='Model identifier/reference backing this version.')],
        capability_profile: Annotated[dict | str, Field(description='Tools/roles the version can use; object or JSON-encoded.')],
        parent_id: Annotated[str | None, Field(description='Parent version ID — omit for genesis; must name an existing row if given.')] = None,
        proposal_id: Annotated[str | None, Field(description='The admitted/conditional proposal this improver realizes — requires parent_id, and the proposer must be that parent.')] = None,
        harness_artifact_digest: Annotated[str | None, Field(description="sha256:<64 hex> digest of the harness/evaluation artifact — must resolve to a blob ingested on episteme; omit when there is none.")] = None,
        search_policy_ref: Annotated[str | None, Field(description='Reference to the search policy this version uses, if any.')] = None,
    ) -> Annotated[CallToolResult, RegisterImproverOut]:
        """Register one improver version in the lineage.

        Genesis improvers take no parent. A child improver names its
        parent and — when it implements a meta-change — the admitted
        or conditional proposal it realizes; proposal_id requires
        parent_id, and the proposal's proposer must be that parent
        (a child implements its parent's own proposal, nobody
        else's).

        The FIRST registered improver becomes champion by bootstrap —
        the protocol needs an incumbent. After that, is_champion moves
        only through promote_policy/rollback: never "latest wins".
        """
        try:
            # Enforcement: digest claims must resolve upstream via
            # loop0, or be 'none' — a hash of nothing is a claim with
            # no referent. An absent/failed channel refuses digests
            # (degraded-honest; 'none' still passes).
            for field, value in (
                ("code_artifact_digest", code_artifact_digest),
                ("harness_artifact_digest", harness_artifact_digest),
            ):
                err = await check_digest_claim(
                    field, value,
                    lambda d: _resolve_via_loop0(adaptors, d),
                )
                if err:
                    return fail(json.dumps({"error": err}))
            if not model_ref.strip():
                return fail(json.dumps({"error": "model_ref must be non-empty"}))
            capability_profile = coerce_json(
                capability_profile, dict, "capability_profile"
            )

            if parent_id is not None:
                if store.get_improver(parent_id) is None:
                    return fail(json.dumps({
                        "error": f"Parent improver not found: {parent_id}. "
                        "Register parents before children — the lineage "
                        "is the tournament's ancestry proof."
                    }))
            if proposal_id is not None:
                if parent_id is None:
                    return fail(json.dumps({
                        "error": "proposal_id requires parent_id — a "
                        "genesis improver implements no meta-change."
                    }))
                proposal = store.get_proposal(proposal_id)
                if proposal is None:
                    return fail(json.dumps({
                        "error": f"Proposal not found: {proposal_id}."
                    }))
                if proposal.status not in {
                    ProposalStatus.admitted, ProposalStatus.conditional
                }:
                    return fail(json.dumps({
                        "error": f"Proposal {proposal_id} is "
                        f"{proposal.status.value} — only admitted or "
                        "conditional proposals can be implemented."
                    }))
                if proposal.proposer_improver_id != parent_id:
                    return fail(json.dumps({
                        "error": f"Proposal {proposal_id} was made by "
                        f"{proposal.proposer_improver_id}, not by parent "
                        f"{parent_id}. A child implements its parent's "
                        "own proposal."
                    }))

            imp = ImproverVersion(
                id=f"imp-{uuid.uuid4().hex[:8]}",
                parent_id=parent_id,
                proposal_id=proposal_id,
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
                is_champion=store.count_champions() == 0,
            )
            store.create_improver(imp)
            return ok({
                "improver_id": imp.id,
                "is_champion": imp.is_champion,
                "parent_id": imp.parent_id,
                "proposal_id": imp.proposal_id,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def get_improver(improver_id: Annotated[str, Field(description='ID of the target improver version.')]) -> Annotated[CallToolResult, GetImproverOut]:
        """Read one improver version with its decisions and policies."""
        try:
            imp = store.get_improver(improver_id)
            if imp is None:
                return fail(json.dumps({
                    "error": f"Improver not found: {improver_id}"
                }))
            decisions = store.list_meta_decisions(improver_id)
            policies = store.list_policy_versions(improver_id)
            tournaments = store.list_tournaments_for_improver(improver_id)
            return ok({
                "improver": _improver_json(imp),
                "decisions": [
                    {
                        "id": d.id, "verdict": d.verdict.value,
                        "decided_by": d.decided_by,
                        "tournament_id": d.tournament_id,
                        "created_at": d.created_at,
                    }
                    for d in decisions
                ],
                "policy_versions": [
                    {
                        "id": p.id, "status": p.status.value,
                        "decision_id": p.decision_id,
                        "created_at": p.created_at,
                    }
                    for p in policies
                ],
                "tournaments": [
                    {
                        "id": t.id,
                        "role": (
                            "candidate"
                            if t.candidate_improver_id == improver_id
                            else "parent"
                        ),
                        "status": t.status.value,
                        "recursive_gain": t.recursive_gain,
                    }
                    for t in tournaments
                ],
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def list_improvers(
        champion_only: Annotated[bool, Field(description='Filter to the incumbent pointer (normally one row).')] = False,
        limit: Annotated[int, Field(description='Max rows to return (pagination).')] = 50,
        offset: Annotated[int, Field(description='Rows to skip before returning (pagination).')] = 0,
    ) -> Annotated[CallToolResult, ListImproversOut]:
        """List improver versions, newest first.

        champion_only filters to the incumbent pointer — normally a
        single row; zero before bootstrap, more than one is an
        integrity violation the checker reports.
        """
        try:
            imps, total = store.list_improvers(
                champion_only=champion_only, limit=limit, offset=offset
            )
            return ok({
                "improvers": [_improver_json(i) for i in imps],
                "total": total,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def get_improver_lineage(improver_id: Annotated[str, Field(description='ID of the target improver version.')]) -> Annotated[CallToolResult, GetImproverLineageOut]:
        """Walk the parent chain back to genesis, oldest first.

        The ancestry proof the ancestral tournament enforces — a
        candidate's claim to descend from the parent arm is exactly
        this chain.
        """
        try:
            if store.get_improver(improver_id) is None:
                return fail(json.dumps({
                    "error": f"Improver not found: {improver_id}"
                }))
            chain = store.lineage(improver_id)
            return ok({
                "improver_id": improver_id,
                "depth": len(chain) - 1,
                "lineage": [_improver_json(i) for i in chain],
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))
