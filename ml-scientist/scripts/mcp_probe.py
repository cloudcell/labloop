#!/usr/bin/env python3
"""Live-stack surface probe — audit the running five-server stack.

Same probe logic as tests/test_surface_clarity.py (the regression
form), run against the deployed servers on their ports.env ports.
Catches deployment drift the in-process test can't — stale servers,
config mismatches, transport-level oddities.

Safety invariant: no call can mutate state (reads get valid/sentinel
args; writes get {} only — a guaranteed schema rejection).

Usage:
    uv run python scripts/mcp_probe.py            # report to stdout
    uv run python scripts/mcp_probe.py --write    # also write
        scripts/mcp_probe_report.{json,md}
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
from surface_probe import probe_client, summarize  # noqa: E402

# ports.env — the canonical port map (38xxx block).
SERVERS = {
    "ml-agora": "http://localhost:38050/mcp",
    "ml-arete": "http://localhost:38060/mcp",
    "ml-zetesis": "http://localhost:38070/mcp",
    "ml-episteme": "http://localhost:38080/mcp",
    "ml-anamnesis": "http://localhost:38090/mcp",
}


async def probe_http(name: str, url: str) -> dict:
    try:
        async with streamable_http_client(url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await probe_client(session, name)
    except Exception as e:
        return {"server": name, "tools": [], "resources": [],
                "unreachable": True, "error": str(e)[:300]}


async def main() -> int:
    reports = []
    for name, url in SERVERS.items():
        print(f"probing {name} ...", file=sys.stderr)
        reports.append(await probe_http(name, url))

    md = summarize(reports)
    if "--write" in sys.argv:
        out = Path(__file__).parent
        (out / "mcp_probe_report.json").write_text(
            json.dumps(reports, indent=2))
        (out / "mcp_probe_report.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
