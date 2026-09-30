"""rc-7b R6 — valid_until is the agent-reachable live→expired write.

The model, column, and include_expired filter all existed; nothing
agent-reachable could set the value, so the expiration transition
was defined-but-unwritable. assert_claim now takes an optional
valid_until: ISO-8601 UTC, timezone-aware, normalized to the
+00:00 form the expiry filter compares lexicographically. The row
is never deleted — expired claims remain visible via
include_expired=true.
"""

from __future__ import annotations

import pytest

from .conftest import call_tool, make_claim


def _future() -> str:
    from datetime import datetime, timedelta, timezone
    return (
        datetime.now(timezone.utc) + timedelta(days=30)
    ).isoformat()


def _past() -> str:
    from datetime import datetime, timedelta, timezone
    return (
        datetime.now(timezone.utc) - timedelta(days=1)
    ).isoformat()


class TestValidUntilWrite:

    async def test_future_expiry_mints_live_claim(self, mcp_server):
        cid = await make_claim(
            mcp_server, content="time-boxed claim",
            valid_until=_future(),
        )
        live = await call_tool(mcp_server, "list_claims", {})
        assert cid in [c["id"] for c in live["claims"]]
        got = await call_tool(mcp_server, "get_claim", {"claim_id": cid})
        assert got["claim"]["valid_until"] is not None

    async def test_past_expiry_mints_already_expired(self, mcp_server):
        """A past timestamp mints an expired claim — allowed; the
        record says so and the row is never deleted."""
        cid = await make_claim(
            mcp_server, content="already stale", valid_until=_past(),
        )
        live = await call_tool(mcp_server, "list_claims", {})
        assert cid not in [c["id"] for c in live["claims"]]
        all_ = await call_tool(
            mcp_server, "list_claims", {"include_expired": True}
        )
        row = next(c for c in all_["claims"] if c["id"] == cid)
        assert row["valid_until"] is not None

    async def test_naive_timestamp_refused(self, mcp_server):
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "naive ts", "type": "empirical",
            "valid_until": "2027-01-01T00:00:00",
        })
        assert "error" in r
        assert "timezone" in r["error"]

    async def test_garbage_timestamp_refused(self, mcp_server):
        r = await call_tool(mcp_server, "assert_claim", {
            "content": "bad ts", "type": "empirical",
            "valid_until": "next tuesday",
        })
        assert "error" in r
        assert "ISO-8601" in r["error"]

    async def test_z_suffix_normalized(self, mcp_server):
        """Z input is stored in +00:00 form — the expiry filter's
        lexicographic compare assumes it."""
        from datetime import datetime, timedelta, timezone
        z = (
            datetime.now(timezone.utc) + timedelta(days=30)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        cid = await make_claim(
            mcp_server, content="z-suffixed", valid_until=z,
        )
        got = await call_tool(mcp_server, "get_claim", {"claim_id": cid})
        assert got["claim"]["valid_until"].endswith("+00:00")

    async def test_no_expiry_unchanged(self, mcp_server):
        cid = await make_claim(mcp_server, content="perennial")
        got = await call_tool(mcp_server, "get_claim", {"claim_id": cid})
        assert got["claim"]["valid_until"] is None
