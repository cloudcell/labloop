"""Upstream adaptors for agora — four read-only channels.

Deliberately duplicated from ml-zetesis's clients/adaptors.py
(ADR-0001/0002). Each channel is a configured endpoint whose status
digest agora reads; the registry survives failed connects (absent
means absent, transiently) and the supervisor retries until peers
appear, so ``labloop start`` order stays irrelevant.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from .mcp_client import MCPClientAdaptor

# The status-digest URI each channel serves — the contract this hub
# aggregates. Channel order is dependency order (claims first, the
# endpoint everything consumes; loop2 last).
CHANNEL_STATUS_URIS = {
    "claims": "claims://status",
    "loop0": "protocol://status",
    "loop1": "search://status",
    "loop2": "improver://status",
}
CHANNEL_ORDER = ["claims", "loop0", "loop1", "loop2"]


class ChannelSpec:
    """A configured upstream channel — survives connection state.

    ``adaptors.<name>`` holds the live client (None when down);
    the spec is the configured intent: role, target, attempts, and
    the last error for diagnostics.
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
    """Mutable container for the lab's four upstream channels.

    Tools read ``adaptors.claims`` / ``adaptors.loop0`` /
    ``adaptors.loop1`` / ``adaptors.loop2`` at call time — a failed
    connect leaves the channel registered-but-None (transient
    absence); the connectivity supervisor retries configured
    channels until peers appear.
    """

    def __init__(self):
        self.claims: MCPClientAdaptor | None = None
        self.loop0: MCPClientAdaptor | None = None
        self.loop1: MCPClientAdaptor | None = None
        self.loop2: MCPClientAdaptor | None = None
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
    makes launch order irrelevant.

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
