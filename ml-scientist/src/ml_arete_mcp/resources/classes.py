"""Boundary-class registry resource — improver://classes.

The ADR-0003 component classification table as a queryable surface:
the same lists the admission kernel classifies from, readable without
parsing propose_meta_change's schema docstring.
"""

from __future__ import annotations

import json

from ..enforcement.checks import (
    CLASS_CONDITIONAL,
    CLASS_IMMUTABLE,
    CLASS_MODIFIABLE,
)


def register(mcp) -> None:
    """Register the boundary-class registry resource."""

    @mcp.resource("improver://classes")
    def get_classes() -> str:
        """The boundary-class registry — what a meta-change may touch.

        immutable → rejected at ingress; conditional → promotable only
        behind a human decided_by; modifiable → admitted; anything
        unlisted classifies 'unknown' → conditional.
        """
        return json.dumps({
            "server": "ml-arete-mcp",
            "source": "ADR-0003 boundary classes",
            "classes": {
                "immutable": sorted(CLASS_IMMUTABLE),
                "conditional": sorted(CLASS_CONDITIONAL),
                "modifiable": sorted(CLASS_MODIFIABLE),
            },
            "unlisted": "unknown → conditional (human-gated)",
        }, indent=2)
