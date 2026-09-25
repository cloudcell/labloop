"""Session protocol resource — search://session.

The read-at-session-start resource for driving clients: the enforced
loop steps, the tool catalog, and the currently-open investigations.
Mirror of ml-episteme's protocol://session, one loop up — this loop
investigates how research is done; it never runs experiments itself.
"""

from __future__ import annotations

import json

from ..state.store import SearchStore
from .status import status_digest

LOOP_STEPS = [
    {
        "step": 1,
        "name": "open_investigation",
        "tool": "open_investigation",
        "description": (
            "Open a bounded inquiry into how research is done. The "
            "question must be answerable by consulting evidence — "
            "declared scope records what the investigation covers."
        ),
        "inputs": ["question", "scope", "budget?"],
        "outputs": ["investigation_id"],
    },
    {
        "step": 2,
        "name": "pull_evidence",
        "tool": "pull_evidence",
        "description": (
            "Read upstream evidence through the adaptor (source: "
            "loop0|anamnesis; tool must be on the read whitelist). "
            "Every pull is logged as an evidence_ref — the trail of "
            "what was consulted is the provenance of the research."
        ),
        "inputs": ["investigation_id", "source", "tool", "args?"],
        "outputs": ["evidence_ref_id", "ref_ids", "result"],
        "repeat": "As many times as the question requires",
    },
    {
        "step": 3,
        "name": "record_finding",
        "tool": "record_finding",
        "description": (
            "Record a provisional methodological finding grounded in "
            "the pulls. Confidence above the prior ceiling (0.3) "
            "requires ≥1 evidence_ref_id — an assertion the "
            "investigator never grounded cannot pretend to be earned."
        ),
        "inputs": ["investigation_id", "content", "confidence",
                   "evidence_ref_ids?"],
        "outputs": ["finding_id"],
        "repeat": "Once per distinct finding",
    },
    {
        "step": 3.5,
        "name": "drop_finding",
        "tool": "drop_finding",
        "description": (
            "Drop a provisional finding that did not survive scrutiny — "
            "recorded, not deleted."
        ),
        "inputs": ["finding_id"],
        "outputs": ["status"],
    },
    {
        "step": 4,
        "name": "conclude_investigation",
        "tool": "conclude_investigation",
        "description": (
            "Close the inquiry. verdict 'findings' mints each live "
            "finding as a `methodological` claim in anamnesis with "
            "derived_from edges to the consulted Loop-0 entities; "
            "'null_result' mints nothing — absence of a pattern is "
            "recorded, not asserted. implications is the Loop-0 "
            "handoff: zetesis proposes, Loop 0 disposes."
        ),
        "inputs": ["investigation_id", "verdict", "summary",
                   "implications?"],
        "outputs": ["claim_ids", "claim_status"],
    },
]

TOOL_CATALOG = [
    {"uri": "search://session",
     "description": "This resource — the session protocol"},
]

READ_ONLY_NOTE = (
    "Zetesis is read-only with respect to Loop 0: pull_evidence "
    "enforces a tool whitelist, the evidence adaptor exposes no write "
    "methods, and promotion decisions belong to a later increment. "
    "To act on implications, drive ml-episteme's own tools directly."
)


def _get_open_investigations(store: SearchStore) -> list[dict]:
    invs, _ = store.list_investigations(status="open", limit=50)
    return [
        {"id": i.id, "question": i.question, "created_at": i.created_at}
        for i in invs
    ]


def register(mcp, store: SearchStore, adaptors=None) -> None:
    """Register the session protocol resource."""

    @mcp.resource("search://session")
    def get_session_protocol() -> str:
        """The zetesis session protocol — read at session start."""
        payload = {
            "server": "ml-zetesis-mcp",
            "loop": 1,
            "name": "search loop — improves the researcher",
            "steps": LOOP_STEPS,
            "note": (
                "Read this resource at the start of a session to "
                "understand the loop and where you are in it. A pull "
                "is not a finding and a finding is not a claim — "
                "claims exist only after conclusion, in anamnesis."
            ),
            "read_only_boundary": READ_ONLY_NOTE,
            "open_investigations": _get_open_investigations(store),
            # The actionable digest — same payload as
            # search://status, embedded so the recovery resource
            # stays self-sufficient.
            "status": status_digest(store, adaptors),
        }
        return json.dumps(payload, indent=2)
