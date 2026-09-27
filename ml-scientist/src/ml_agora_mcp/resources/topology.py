"""Topology resource — lab://topology.

The static map plus live verdicts: the four channels, their roles,
targets, and current connectivity state. The answer to "what does
the lab look like" before asking how it is doing.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from ..clients.adaptors import CHANNEL_ORDER, CHANNEL_STATUS_URIS


def register(mcp, adaptors) -> None:
    """Register the lab topology resource."""

    @mcp.resource("lab://topology")
    def get_topology() -> str:
        """The lab's channel map — roles, targets, live states."""
        report = {
            e["channel"]: e for e in adaptors.connectivity_report()
        }
        channels = []
        for name in CHANNEL_ORDER:
            spec = adaptors._channels.get(name)
            entry = {
                "channel": name,
                "status_uri": CHANNEL_STATUS_URIS[name],
                "configured": spec is not None,
            }
            if spec is not None:
                live = report.get(name, {})
                # Full channel passthrough — the topology resource must
                # answer "what went wrong", not just "is it up": probe,
                # timing, and fault-attribution fields come along.
                entry.update({
                    "role": spec.role,
                    "target": spec.target,
                    "state": live.get("state", "down"),
                    "attempts": live.get("attempts"),
                    "last_error": live.get("last_error"),
                    "last_operation": live.get("last_operation"),
                    "last_failed_operation": live.get(
                        "last_failed_operation"
                    ),
                    "last_failed_at": live.get("last_failed_at"),
                    "probe": live.get("probe"),
                    "last_probe_at": live.get("last_probe_at"),
                    "last_probe_ms": live.get("last_probe_ms"),
                    "in_flight_operation": live.get(
                        "in_flight_operation"
                    ),
                    "in_flight_since": live.get("in_flight_since"),
                    "connected_at": live.get("connected_at"),
                })
            channels.append(entry)
        return json.dumps({
            "server": "ml-agora-mcp",
            "role": "status",
            "generated_at": datetime.now(
                timezone.utc
            ).isoformat(timespec="seconds"),
            "channels": channels,
        }, indent=2)
