"""Investigation tool handlers — the Loop-1 search-loop lifecycle.

open_investigation → pull_evidence → record_finding →
conclude_investigation. The server holds state and enforces the loop;
the driving client does the reasoning. Every upstream pull is logged
as an evidence_ref — the trail of what was consulted is the
provenance of the research itself.
"""

from __future__ import annotations

import json
import re
import uuid

from ..enforcement.checks import (
    PRIOR_CONFIDENCE_MAX,
    check_conclude_allowed,
    check_evidence_refs_exist,
    check_finding_confidence,
    check_investigation_open,
    check_source_valid,
    check_tool_whitelisted,
    check_verdict_valid,
)
from ..state.models import (
    EvidenceRef,
    EvidenceSource,
    Finding,
    FindingStatus,
    Investigation,
    InvestigationStatus,
    InvestigationVerdict,
)
from ..state.store import SearchStore
from .schemas import coerce_json, fail, ok, ConcludeInvestigationOut, GetInvestigationOut, ListInvestigationsOut, OpenInvestigationOut, RecordFindingOut, AbandonInvestigationOut, DropFindingOut, PullEvidenceOut
from typing import Annotated, Literal
from pydantic import Field
from mcp.types import CallToolResult

# Entity-id prefixes recognized when extracting referenced ids from an
# upstream payload. The set mirrors Loop-0 + anamnesis id conventions;
# anything else in a payload is not a cross-server entity reference.
_REF_ID_RE = re.compile(
    r"\b(?:prog|hyp|trial|obs|conc|bundle|cand|contract|decision|belief"
    r"|claim|edge|archive|data-ref)-[0-9A-Za-z_-]{3,}\b"
)

# Prefix → claim_edges ref_type for minted derived_from edges.
# Unrecognized prefixes are external by honesty, not by guesswork.
_REF_TYPE_BY_PREFIX = {
    "trial-": "trial",
    "obs-": "observation",
    "conc-": "conclusion",
    "prog-": "programme",
    "claim-": "claim",
}


def _extract_ref_ids(payload: str) -> list[str]:
    """Find entity ids referenced anywhere in an upstream payload.

    Runs on the raw text so ids nested inside JSON-encoded strings are
    caught too. Order-preserving dedup.
    """
    seen: dict[str, None] = {}
    for m in _REF_ID_RE.finditer(payload):
        seen[m.group(0)] = None
    return list(seen)


def _ref_type_for(ref_id: str) -> str:
    for prefix, ref_type in _REF_TYPE_BY_PREFIX.items():
        if ref_id.startswith(prefix):
            return ref_type
    return "external"


def _investigation_json(inv: Investigation, store: SearchStore) -> dict:
    findings = store.list_findings(inv.id)
    refs = store.list_evidence_refs(inv.id)
    return {
        "investigation": {
            "id": inv.id,
            "question": inv.question,
            "scope": inv.scope,
            "budget": inv.budget,
            "status": inv.status.value,
            "verdict": inv.verdict.value if inv.verdict else None,
            "summary": inv.summary,
            "implications": inv.implications,
            "created_at": inv.created_at,
            "concluded_at": inv.concluded_at,
        },
        "evidence_refs": [
            {
                "id": r.id, "source": r.source.value, "tool": r.tool,
                "args": r.args, "ref_ids": r.ref_ids,
                "created_at": r.created_at,
            }
            for r in refs
        ],
        "findings": [
            {
                "id": f.id, "content": f.content,
                "confidence": f.confidence, "status": f.status.value,
                "claim_id": f.claim_id, "created_at": f.created_at,
                "evidence_ref_ids": [
                    r.id for r in store.finding_evidence_refs(f.id)
                ],
            }
            for f in findings
        ],
    }


def register(
    mcp,
    store: SearchStore,
    adaptors,
    prior_confidence_max: float | None = None,
) -> None:
    """Register investigation-loop tools.

    prior_confidence_max is the configured prior ceiling
    ([integrity] prior_confidence_max) gating unevidenced findings."""
    _prior_max = (
        PRIOR_CONFIDENCE_MAX
        if prior_confidence_max is None
        else prior_confidence_max
    )
    """Register the investigation-loop tools on the MCP server.

    `adaptors` is an Adaptors container read at call time — a channel
    set to None after a failed connect disables it live: pulls fail
    with a clear error, minting reports claim_status 'disabled', and
    the server still serves its own state.
    """

    @mcp.tool()
    def open_investigation(
        question: Annotated[str, Field(description='What the investigation asks.')],
        scope: Annotated[dict | str, Field(description='Declared domain — programme_ids, candidate_ids, claim types, or time windows the inquiry claims to cover (the Duhem-Quine boundary); may be JSON-encoded.')],
        budget: Annotated[dict | str | None, Field(description='Optional limits {max_pulls, max_wall_time_hours} — recorded, not yet enforced. May be JSON-encoded.')] = None,
    ) -> Annotated[CallToolResult, OpenInvestigationOut]:
        """Open a bounded inquiry into how research is done.

        question: what the investigation asks (e.g. "which prompting
        strategy produced better programme outcomes in programme X?").
        scope: the declared domain — programme_ids, candidate_ids,
        claim types, or time windows the inquiry may consult. scope is
        the Duhem-Quine boundary of the investigation: evidence pulled
        outside it is legal (the whitelist governs *how*), but the
        declared scope records what the investigation *claims* to cover.
        budget: optional limits ({max_pulls, max_wall_time_hours}) —
        recorded, not yet enforced.
        """
        try:
            if not question.strip():
                return fail(json.dumps({"error": "question must be non-empty"}))
            scope = coerce_json(scope, dict, "scope")
            if budget is not None:
                budget = coerce_json(budget, dict, "budget")
            inv = Investigation(
                id=f"inv-{uuid.uuid4().hex[:8]}",
                question=question,
                scope=scope,
                budget=budget,
            )
            store.create_investigation(inv)
            return ok({
                "investigation_id": inv.id,
                "status": "open",
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    async def pull_evidence(
        investigation_id: Annotated[str, Field(description='ID of the target investigation.')],
        source: Annotated[Literal['loop0', 'anamnesis'], Field(description='Evidence source — the upstream read surface to pull through.')],
        tool: Annotated[Literal['assess_programme', 'get_archive', 'get_archived_programme', 'get_campaign', 'get_candidate', 'get_candidate_lineage', 'get_candidate_scorecard', 'get_claim', 'get_evaluation_contract', 'get_incumbent', 'get_investigation', 'get_trial_status', 'list_active_programmes', 'list_archives', 'list_campaigns', 'list_candidates', 'list_claims', 'list_hypotheses', 'list_investigations', 'list_programmes', 'list_promotion_decisions', 'list_trials', 'recall'], Field(description="Upstream read tool to call — must be on the source's read whitelist (the evidence channel is read-only).")],
        args: Annotated[dict | str | None, Field(description='Arguments forwarded to the upstream tool; object or JSON-encoded.')] = None,
    ) -> Annotated[CallToolResult, PullEvidenceOut]:
        """Read upstream evidence through the adaptor — and log it.

        source: "loop0" (ml-episteme read surface) or "anamnesis"
        (claims memory reads). tool must be on the source's read
        whitelist — the evidence channel is read-only; writes to Loop 0
        belong to the promotion pipeline, not this tool.

        Every pull is recorded as an evidence_ref — what was consulted,
        with which args, returning which entity ids. The trail is the
        provenance of the research itself.
        """
        try:
            err, _inv = check_investigation_open(store, investigation_id)
            if err:
                return fail(json.dumps({"error": err}))
            for e in (check_source_valid(source),
                      check_tool_whitelisted(source, tool)):
                if e:
                    return fail(json.dumps({"error": e}))
            args = coerce_json(args, dict, "args") if args else {}

            adaptor = (adaptors.evidence if source == "loop0"
                       else adaptors.claims)
            if adaptor is None:
                channel = "evidence" if source == "loop0" else "claims"
                if channel in getattr(adaptors, "_channels", {}):
                    return fail(json.dumps({
                        "error": f"{source} channel is configured but "
                        "not yet connected — the connectivity "
                        "supervisor is retrying the upstream; try "
                        "again shortly."
                    }))
                return fail(json.dumps({
                    "error": f"No {source} adaptor configured — cannot "
                    "pull evidence. Wire [adaptors."
                    + channel
                    + "] in ml-zetesis.toml."
                }))

            payload = await adaptor.pull(tool, args)
            ref_ids = _extract_ref_ids(payload)

            ref = EvidenceRef(
                id=f"eref-{uuid.uuid4().hex[:8]}",
                investigation_id=investigation_id,
                source=EvidenceSource(source),
                tool=tool,
                args=args,
                ref_ids=ref_ids,
            )
            store.create_evidence_ref(ref)
            try:
                result = json.loads(payload) if isinstance(payload, str) else payload
            except ValueError:
                result = payload  # non-JSON upstream payload — pass through
            return ok({
                "evidence_ref_id": ref.id,
                "ref_ids": ref_ids,
                "result": result,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def record_finding(
        investigation_id: Annotated[str, Field(description='ID of the target investigation.')],
        content: Annotated[str, Field(description='The finding text.')],
        confidence: Annotated[float, Field(description='0–1 — above the prior ceiling (0.3) requires ≥1 evidence_ref_id from pull_evidence.')],
        evidence_ref_ids: Annotated[list | str | None, Field(description='Evidence_ref IDs minted by pull_evidence/pull_arm_evidence calls; list or JSON-encoded.')] = None,
    ) -> Annotated[CallToolResult, RecordFindingOut]:
        """Record a provisional methodological finding.

        A finding is a proto-claim: it becomes a `methodological` claim
        in anamnesis only at conclude_investigation. Evidence rule:
        confidence above the prior ceiling (0.3) requires ≥1
        evidence_ref_id from pull_evidence — an assertion the
        investigator never grounded cannot pretend to be earned.
        """
        try:
            err, _inv = check_investigation_open(store, investigation_id)
            if err:
                return fail(json.dumps({"error": err}))
            if not content.strip():
                return fail(json.dumps({"error": "content must be non-empty"}))
            if not (0.0 <= confidence <= 1.0):
                return fail(json.dumps({"error": "confidence must be in [0, 1]"}))
            if evidence_ref_ids is not None:
                evidence_ref_ids = coerce_json(
                    evidence_ref_ids, list, "evidence_ref_ids"
                )
            evidence_ref_ids = evidence_ref_ids or []

            for e in (
                check_finding_confidence(
                    confidence, evidence_ref_ids, prior_max=_prior_max
                ),
                check_evidence_refs_exist(
                    store, investigation_id, evidence_ref_ids
                ),
            ):
                if e:
                    return fail(json.dumps({"error": e}))

            finding = Finding(
                id=f"find-{uuid.uuid4().hex[:8]}",
                investigation_id=investigation_id,
                content=content,
                confidence=confidence,
            )
            with store.transaction():
                store.create_finding(finding)
                for ref_id in evidence_ref_ids:
                    store.add_finding_evidence(finding.id, ref_id)

            return ok({
                "finding_id": finding.id,
                "status": "provisional",
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def drop_finding(finding_id: Annotated[str, Field(description='ID of the provisional finding to drop.')]) -> Annotated[CallToolResult, DropFindingOut]:
        """Drop a provisional finding — it did not survive scrutiny.

        A dropped finding is recorded, not deleted: "examined and
        rejected" is part of the research trail. Only provisional
        findings can be dropped; asserted findings are already claims.
        """
        try:
            finding = store.get_finding(finding_id)
            if finding is None:
                return fail(json.dumps({"error": f"Finding not found: {finding_id}"}))
            if finding.status != FindingStatus.provisional:
                return fail(json.dumps({
                    "error": f"Finding {finding_id} is "
                    f"{finding.status.value} — only provisional "
                    "findings can be dropped."
                }))
            store.set_finding_status(finding_id, FindingStatus.dropped)
            return ok({"finding_id": finding_id, "status": "dropped"})
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    async def conclude_investigation(
        investigation_id: Annotated[str, Field(description='ID of the target investigation.')],
        verdict: Annotated[Literal['findings', 'null_result'], Field(description="findings | null_result — 'findings' mints live findings to anamnesis; 'null_result' closes honestly with nothing asserted.")],
        summary: Annotated[str, Field(description="The investigation's distilled conclusion text.")],
        implications: Annotated[dict | str | None, Field(description='Loop-0 handoff — a proposed programme/hypothesis/strategy the client can enact; object or JSON-encoded.')] = None,
    ) -> Annotated[CallToolResult, ConcludeInvestigationOut]:
        """Close the inquiry and distill its findings into memory.

        verdict "findings": every live provisional finding is minted to
        anamnesis as a `methodological` claim (source_id = this
        investigation) with `derived_from` edges to the Loop-0 entities
        its evidence refs consulted. Minting failure is reported as
        claim_status "failed"/"disabled" and never blocks the verdict —
        memory is a byproduct, not a gate.

        verdict "null_result": the investigation is closed honestly and
        nothing is minted — "investigated X, found no systematic
        pattern" is recorded on the investigation, not asserted as
        knowledge.

        implications: the Loop-0 handoff — a proposed programme,
        hypothesis, or strategy the driving client can enact via
        create_programme/formulate_hypothesis. Zetesis proposes; Loop 0
        disposes.
        """
        try:
            err, inv = check_investigation_open(store, investigation_id)
            if err:
                return fail(json.dumps({"error": err}))
            for e in (check_verdict_valid(verdict),
                      check_conclude_allowed(store, inv, verdict)):
                if e:
                    return fail(json.dumps({"error": e}))
            if not summary.strip():
                return fail(json.dumps({"error": "summary must be non-empty"}))
            if implications is not None:
                implications = coerce_json(
                    implications, dict, "implications"
                )

            claim_ids: list[str] = []
            edges_created = 0
            claim_status = "skipped"

            if verdict == InvestigationVerdict.findings.value:
                provisional = store.list_findings(
                    investigation_id, status="provisional"
                )
                if adaptors.claims is None:
                    claim_status = "disabled"
                else:
                    try:
                        for f in provisional:
                            consulted = {
                                ref_id
                                for r in store.finding_evidence_refs(f.id)
                                for ref_id in r.ref_ids
                            }
                            # Evidence edges mint inline — anamnesis caps
                            # unevidenced claims at the prior ceiling, and
                            # post-hoc relate hits the chicken-and-egg.
                            minted = await adaptors.claims.assert_claim(
                                content=f.content,
                                type="methodological",
                                confidence=f.confidence,
                                evidence=[
                                    {
                                        "to_ref": ref_id,
                                        "ref_type": _ref_type_for(ref_id),
                                        "relation": "derived_from",
                                    }
                                    for ref_id in sorted(consulted)
                                ],
                                source_id=investigation_id,
                            )
                            cid = minted["claim_id"]
                            claim_ids.append(cid)
                            edges_created += len(consulted)
                            store.set_finding_claim(f.id, cid)
                            store.set_finding_status(
                                f.id, FindingStatus.asserted
                            )
                        claim_status = "minted"
                    except Exception:
                        claim_status = "failed"

            with store.transaction():
                store.conclude_investigation(
                    investigation_id,
                    InvestigationVerdict(verdict),
                    summary,
                    implications,
                )

            return ok({
                "investigation_id": investigation_id,
                "status": "concluded",
                "verdict": verdict,
                "claim_ids": claim_ids,
                "claim_status": claim_status,
                "findings_minted": len(claim_ids),
                "edges_created": edges_created,
                "implications": implications,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def abandon_investigation(investigation_id: Annotated[str, Field(description='ID of the target investigation.')]) -> Annotated[CallToolResult, AbandonInvestigationOut]:
        """Abandon an open investigation — recorded, not deleted.

        Stale opens are visible in the GUI and in list_investigations;
        abandonment is a status, never an erasure.
        """
        try:
            err, _inv = check_investigation_open(store, investigation_id)
            if err:
                return fail(json.dumps({"error": err}))
            store.abandon_investigation(investigation_id)
            return ok({
                "investigation_id": investigation_id,
                "status": "abandoned",
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def get_investigation(investigation_id: Annotated[str, Field(description='ID of the target investigation.')]) -> Annotated[CallToolResult, GetInvestigationOut]:
        """Read an investigation with its full evidence trail and findings.

        Returns the record plus every pull (evidence_refs) and every
        finding with its grounding refs and minted claim_id — never a
        bare summary; the provenance travels with it.
        """
        try:
            inv = store.get_investigation(investigation_id)
            if inv is None:
                return fail(json.dumps({
                    "error": f"Investigation not found: {investigation_id}"
                }))
            return ok(_investigation_json(inv, store))
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    def list_investigations(
        status: Annotated[Literal['open', 'concluded', 'abandoned'] | None, Field(description='Optional status filter (default lists all, including stale opens).')] = None,
        limit: Annotated[int, Field(description='Max rows to return (pagination).')] = 50,
        offset: Annotated[int, Field(description='Rows to skip before returning (pagination).')] = 0,
    ) -> Annotated[CallToolResult, ListInvestigationsOut]:
        """List investigations, newest first.

        status filter: open | concluded | abandoned. Default lists all —
        including stale opens, which are the visible debt of the loop.
        """
        try:
            if status is not None and status not in {
                s.value for s in InvestigationStatus
            }:
                return fail(json.dumps({
                    "error": "status must be one of "
                    f"{'|'.join(s.value for s in InvestigationStatus)}, "
                    f"got: {status}"
                }))
            invs, total = store.list_investigations(
                status=status, limit=limit, offset=offset
            )
            return ok({
                "investigations": [
                    {
                        "id": i.id,
                        "question": i.question,
                        "status": i.status.value,
                        "verdict": i.verdict.value if i.verdict else None,
                        "created_at": i.created_at,
                        "concluded_at": i.concluded_at,
                    }
                    for i in invs
                ],
                "total": total,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))
