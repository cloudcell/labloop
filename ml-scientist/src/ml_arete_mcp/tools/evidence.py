"""pull_evidence — the read-only upstream evidence channel.

Every pull is logged as an evidence_ref owned by a live context (a
proposal in admitted/conditional state, or a tournament) — what was
consulted, with which args, returning which entity ids. The trail is
the provenance of the meta-decision that cites it.
"""

from __future__ import annotations

import json
import re
import uuid

from ..enforcement.checks import (
    check_evidence_context,
    check_source_valid,
    check_tool_whitelisted,
)
from ..state.models import EvidenceContext, EvidenceRef, EvidenceSource
from ..state.store import ImproverStore
from .schemas import coerce_json, fail, ok, PullEvidenceOut
from typing import Annotated, Literal
from pydantic import Field
from mcp.types import CallToolResult

# Entity-id prefixes recognized when extracting referenced ids from
# an upstream payload — the union of Loop-0, Loop-1, anamnesis, and
# this server's own id conventions.
_REF_ID_RE = re.compile(
    r"\b(?:prog|hyp|trial|obs|conc|bundle|cand|contract|decision|belief"
    r"|claim|edge|archive|data-ref|inv|find|eref"
    r"|imp|tourn|tres|mcp|mcontract|mdec|pol|canary)"
    r"-[0-9A-Za-z_-]{3,}\b"
)


def _extract_ref_ids(payload: str) -> list[str]:
    """Find entity ids referenced anywhere in an upstream payload.

    Runs on the raw text so ids nested inside JSON-encoded strings are
    caught too. Order-preserving dedup.
    """
    seen: dict[str, None] = {}
    for m in _REF_ID_RE.finditer(payload):
        seen[m.group(0)] = None
    return list(seen)


def register(mcp, store: ImproverStore, adaptors) -> None:
    """Register the evidence-pull tool on the MCP server."""

    @mcp.tool()
    async def pull_evidence(
        context_type: Annotated[Literal['proposal', 'tournament'], Field(description="Pull context — pulls belong to a live context, never ambient.")],
        context_id: Annotated[str, Field(description='ID of the proposal or tournament the pull serves.')],
        source: Annotated[Literal['loop0', 'loop1', 'anamnesis'], Field(description='Evidence source — the upstream read surface to pull through.')],
        tool: Annotated[Literal['assess_programme', 'get_archive', 'get_archived_programme', 'get_campaign', 'get_candidate_lineage', 'get_claim', 'get_incumbent', 'get_investigation', 'get_trial_status', 'list_active_programmes', 'list_archives', 'list_campaigns', 'list_candidates', 'list_claims', 'list_hypotheses', 'list_investigations', 'list_trials', 'recall'], Field(description="Upstream read tool to call — must be on the source's read whitelist (the evidence channel is read-only).")],
        args: Annotated[dict | str | None, Field(description='Arguments forwarded to the upstream tool; object or JSON-encoded.')] = None,
    ) -> Annotated[CallToolResult, PullEvidenceOut]:
        """Read upstream evidence through an adaptor — and log it.

        context_type: 'proposal' or 'tournament' — pulls belong to a
        live context, never ambient. source: 'loop0' (ml-episteme),
        'loop1' (ml-zetesis), or 'anamnesis' (claims memory). tool
        must be on the source's read whitelist — the evidence channel
        is read-only; writes to the lower loops belong to the
        promotion pipeline, not this tool.

        Every pull is recorded as an evidence_ref — what was
        consulted, with which args, returning which entity ids.
        Meta-decisions cite these refs; ungrounded decisions are
        rejected at record_meta_decision.
        """
        try:
            if err := check_evidence_context(
                store, context_type, context_id
            ):
                return fail(json.dumps({"error": err}))
            for e in (check_source_valid(source),
                      check_tool_whitelisted(source, tool)):
                if e:
                    return fail(json.dumps({"error": e}))
            args = coerce_json(args, dict, "args") if args else {}

            adaptor = {
                "loop0": adaptors.loop0,
                "loop1": adaptors.loop1,
                "anamnesis": adaptors.claims,
            }.get(source)
            if adaptor is None:
                channel = ("claims" if source == "anamnesis"
                           else source)
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
                    + "] in ml-arete.toml."
                }))

            payload = await adaptor.pull(tool, args)
            ref_ids = _extract_ref_ids(payload)

            ref = EvidenceRef(
                id=f"eref-{uuid.uuid4().hex[:8]}",
                context_type=EvidenceContext(context_type),
                context_id=context_id,
                source=EvidenceSource(source),
                tool=tool,
                args=args,
                ref_ids=ref_ids,
            )
            store.create_evidence_ref(ref)
            try:
                result = (
                    json.loads(payload)
                    if isinstance(payload, str) else payload
                )
            except ValueError:
                result = payload  # non-JSON upstream — pass through
            return ok({
                "evidence_ref_id": ref.id,
                "ref_ids": ref_ids,
                "result": result,
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))
