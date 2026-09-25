"""Upstream adaptors for zetesis — the loop's two protocol channels.

- ``EvidenceAdaptor`` — read-only MCP client of the Loop-0 experiment
  server (ml-episteme). It wraps only the upstream *read* surface:
  commands flow down, evidence flows up, and nothing here can write.
  The tool-name whitelist lives in enforcement/checks.py; this adaptor
  is a dumb passthrough.
- ``ClaimsAdaptor`` — the ClaimsRole client (anamnesis). Reads go
  through the same evidence channel (list_claims/get_claim/recall);
  minting uses typed assert_claim/relate calls at conclusion.

Both are optional at startup: absent means absent — a pull against an
unwired adaptor fails with a clear error; the server still serves its
own state.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from .mcp_client import MCPClientAdaptor


class UpstreamAdaptor(MCPClientAdaptor):
    """Generic MCP client of an upstream server.

    Returns the upstream payload as-is (text). Callers parse JSON.
    """

    async def pull(self, tool: str, args: dict[str, Any]) -> str:
        return await self.call_tool(tool, args)


class EvidenceAdaptor(UpstreamAdaptor):
    """Read-only client of the Loop-0 server (ml-episteme)."""


class PromotionAdaptor(UpstreamAdaptor):
    """The promotion channel — writes to Loop 0's insert-only lineage
    surfaces (register_candidate, create_evaluation_contract,
    record_promotion_decision).

    Kept separate from EvidenceAdaptor so the read path can never grow
    a write: a different object, a different configured endpoint if
    desired, and its own tool whitelist enforced upstream of the call.
    """

    async def push(self, tool: str, args: dict[str, Any]) -> str:
        return await self.call_tool(tool, args)


class ClaimsAdaptor(UpstreamAdaptor):
    """Client of the claims-memory server (anamnesis).

    Doubles as the `anamnesis` pull source (reads) and the minting
    channel (assert_claim/relate at conclude_investigation).
    """

    @staticmethod
    def _parse(result: Any, tool: str) -> dict[str, Any]:
        data = json.loads(result) if isinstance(result, str) else result
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(f"Claims server rejected {tool}: {data['error']}")
        return data

    async def assert_claim(
        self,
        content: str,
        type: str,
        confidence: float,
        evidence: list[dict[str, Any]] | None = None,
        source_id: str | None = None,
    ) -> dict[str, Any]:
        return self._parse(await self.call_tool("assert_claim", {
            "content": content,
            "type": type,
            "confidence": confidence,
            "evidence": evidence,
            "source_id": source_id,
        }), "assert_claim")

    async def relate(
        self,
        from_claim: str,
        to_ref: str,
        ref_type: str,
        relation: str,
        source_id: str | None = None,
    ) -> dict[str, Any]:
        return self._parse(await self.call_tool("relate", {
            "from_claim": from_claim,
            "to_ref": to_ref,
            "ref_type": ref_type,
            "relation": relation,
            "source_id": source_id,
        }), "relate")


class ChannelSpec:
    """A configured upstream channel — the registry entry survives a
    dead peer so the connectivity supervisor can keep retrying.

    ``adaptor`` is the reconnectable client object; the live slot
    (``adaptors.<name>``) stays None until a connect succeeds — tools
    keep their "absent means absent" read, but absence is now
    transient rather than terminal.
    """

    def __init__(
        self, name: str, role: str, target: str, adaptor: "MCPClientAdaptor"
    ) -> None:
        self.name = name
        self.role = role
        self.target = target
        self.adaptor = adaptor
        self.state = "down"  # "down" | "up" — set by the supervisor
        self.attempts = 0
        self.last_error: str | None = None
        self.connected_at: str | None = None

    def mark_up(self) -> None:
        self.state = "up"
        self.last_error = None
        self.connected_at = datetime.now(
            timezone.utc
        ).isoformat(timespec="seconds")

    def mark_down(self, error: str | None) -> None:
        self.state = "down"
        self.last_error = error


def _target_of(config: dict[str, Any]) -> str:
    if "url" in config:
        return config["url"]
    return f"stdio:{config.get('command', '?')}"


class Adaptors:
    """Mutable container for the loop's three upstream channels.

    Tools read ``adaptors.evidence`` / ``adaptors.promotion`` /
    ``adaptors.claims`` at call time — a failed connect leaves the
    channel registered-but-None (transient absence); the connectivity
    supervisor retries configured channels until peers appear, so
    ``run-ml-*.sh`` order stops mattering.
    """

    def __init__(self):
        self.evidence: EvidenceAdaptor | None = None
        self.promotion: PromotionAdaptor | None = None
        self.claims: ClaimsAdaptor | None = None
        self._channels: dict[str, ChannelSpec] = {}

    def register_channel(
        self, name: str, role: str, adaptor: "MCPClientAdaptor"
    ) -> None:
        """Register a configured channel. Does NOT set the live slot —
        that happens only when connect() succeeds."""
        self._channels[name] = ChannelSpec(
            name, role, _target_of(adaptor.config), adaptor
        )

    def connectivity_report(self) -> list[dict[str, Any]]:
        """Snapshot of who is connected in what role — the integrity
        layer's ``upstream_connectivity`` input."""
        report = []
        for name, spec in self._channels.items():
            live = getattr(self, name, None)
            dead = getattr(live, "session_dead", None)
            up = live is not None and not (dead and dead())
            report.append({
                "channel": name,
                "role": spec.role,
                "target": spec.target,
                "state": "up" if up else "down",
                "attempts": spec.attempts,
                "last_error": spec.last_error,
                "connected_at": spec.connected_at,
            })
        return report



async def run_connectivity_supervisor(
    adaptors: Adaptors,
    interval_seconds: float,
    *,
    log=print,
) -> None:
    """Keep configured channels connected — the convergence path that
    makes ``run-ml-*.sh`` launch order irrelevant.

    Each tick: a live channel whose session died (the upstream exiting
    kills the client task — passive detection, no probing) is dropped
    back to the registry; a down channel gets a fresh ``connect()``.
    Runs until cancelled.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        for name, spec in adaptors._channels.items():
            live = getattr(adaptors, name, None)
            if live is not None:
                dead = getattr(live, "session_dead", None)
                if dead is not None and dead():
                    try:
                        await live.disconnect()
                    except Exception:
                        pass
                    setattr(adaptors, name, None)
                    spec.mark_down("upstream session ended")
                    log(
                        f"WARNING: {name} ({spec.role}) channel lost — "
                        "will retry"
                    )
                continue
            spec.attempts += 1
            try:
                await spec.adaptor.connect()
            except Exception as e:
                spec.mark_down(str(e))
                continue
            setattr(adaptors, name, spec.adaptor)
            spec.mark_up()
            log(f"{name} ({spec.role}) adaptor connected")
