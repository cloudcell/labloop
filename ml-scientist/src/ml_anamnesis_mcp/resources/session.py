"""Session protocol resource — claims://session.

The read-at-session-start resource for the semantic-memory server.
Anamnesis is a role-filler, not a loop (ADR-0004) — this resource
documents the capability surface (the claims API and its boundary
rules) rather than a workflow, and embeds the status digest so the
contract stays uniform with the loop servers.
"""

from __future__ import annotations

import json

from ..state.store import MemoryStore
from .status import status_digest

TOOL_CATALOG = [
    {"name": "assert_claim", "category": "claims",
     "description": "Mint a content-addressed claim (methodological, empirical, ...)"},
    {"name": "relate", "category": "claims",
     "description": "Assert a typed edge between claims (derived_from, supports, contradicts, supersedes)"},
    {"name": "get_claim", "category": "claims",
     "description": "Read one claim by id"},
    {"name": "get_claims", "category": "claims",
     "description": "Batch-read claims — one call, many claims"},
    {"name": "list_claims", "category": "claims",
     "description": "Query claims by type/superseded/expired filters"},
    {"name": "check_invariants", "category": "integrity",
     "description": "Audit the claim store against its invariants"},
    {"name": "acknowledge_violation", "category": "integrity",
     "description": "Acknowledge an open violation — clears the write gate"},
]

BOUNDARY_NOTE = (
    "Anamnesis is the semantic memory of the ecosystem — a "
    "capability, not a loop. It records what is *known* (claims "
    "distilled from episodes) and the typed edges between claims; "
    "episodic memory lives in each loop's own store. Write paths are "
    "append-only: claims are content-addressed, supersession is an "
    "edge, never an edit."
)


def register(mcp, store: MemoryStore) -> None:
    """Register the session protocol resource."""

    @mcp.resource("claims://session")
    def get_session_protocol() -> str:
        """The anamnesis session resource — read at session start."""
        payload = {
            "server": "ml-anamnesis-mcp",
            "role": "claims",
            "name": "semantic memory — what is known",
            "note": (
                "Read this resource at the start of a session to "
                "understand the claims surface. There is no loop "
                "here — the consuming loops drive; this server "
                "serves."
            ),
            "tool_catalog": TOOL_CATALOG,
            "boundary": BOUNDARY_NOTE,
            # The uniform status digest — capability posture.
            "status": status_digest(store),
        }
        return json.dumps(payload, indent=2)
