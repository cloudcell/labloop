"""rc-7 regressions — anamnesis.

Q6  read_resource reaches claims:// resources and errors clearly on
    unknown or foreign-scheme URIs.
"""

from __future__ import annotations

import json

from .conftest import call_tool


class TestReadResource:
    async def test_reads_claims_status(self, mcp_server):
        r = await call_tool(mcp_server, "read_resource", {
            "uri": "claims://status",
        })
        assert "error" not in r, r
        body = json.loads(r["contents"][0]["content"])
        assert body["server"] == "ml-anamnesis-mcp"

    async def test_unknown_uri_errors(self, mcp_server):
        r = await call_tool(mcp_server, "read_resource", {
            "uri": "claims://nonexistent",
        })
        assert "error" in r

    async def test_foreign_scheme_errors(self, mcp_server):
        """Ownership: anamnesis cannot read another loop's
        resources."""
        r = await call_tool(mcp_server, "read_resource", {
            "uri": "search://status",
        })
        assert "error" in r
