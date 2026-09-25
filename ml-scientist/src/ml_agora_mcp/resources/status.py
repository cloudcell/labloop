"""Status aggregate — lab://status.

The lab's machine-readable status contract: every server's
``X://status`` digest fetched live through the read-only channels,
plus a synthesized ``lab_next_actions`` ranking — the answer to
"where did I leave off, and what do I do next" across the whole lab.

Fetch order is dependency order (claims → loop0 → loop1 → loop2 —
the ``labloop start`` order): foundations are checked before the
loops that consume them. Ranking is advisory — the driving agent
decides (a-00 §5.10); agora recommends, never commands.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ..clients.adaptors import CHANNEL_ORDER, CHANNEL_STATUS_URIS

# Loop order for tie-breaking inside a rank — foundation before meta.
_LOOP_ORDER = {"claims": 0, "loop0": 1, "loop1": 2, "loop2": 3}

# Recommended actions that are explanatory posture, not work —
# anamnesis's "serve_consumers" is capability narration, never a
# lab task.
_NON_ACTIONS = {"serve_consumers"}

# Actions that advance existing open work (in-flight) vs. start new
# work — entity_refs decide, but stale-review is its own tier.
_STALE_ACTIONS = {"review_stale_programme"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def _fetch_digest(
    adaptors, name: str
) -> dict[str, Any]:
    """Fetch one channel's status digest — honest failure states."""
    spec = adaptors._channels.get(name)
    uri = CHANNEL_STATUS_URIS[name]
    if spec is None:
        return {
            "status": "not_configured",
            "status_uri": uri,
            "detail": "no [adaptors.%s] channel wired" % name,
        }
    live = getattr(adaptors, name, None)
    dead = getattr(live, "session_dead", None)
    if live is None or (dead is not None and dead()):
        return {
            "status": "unreachable",
            "status_uri": uri,
            "target": spec.target,
            "detail": spec.last_error or "channel down",
        }
    try:
        text = await live.read_resource(uri)
    except Exception as e:
        return {
            "status": "error",
            "status_uri": uri,
            "target": spec.target,
            "detail": str(e),
        }
    try:
        digest = json.loads(text) if isinstance(text, str) else text
    except (TypeError, ValueError) as e:
        return {
            "status": "error",
            "status_uri": uri,
            "target": spec.target,
            "detail": f"unparseable digest: {e}",
        }
    return {"status": "ok", "status_uri": uri, "digest": digest}


def _action_tier(rec: dict) -> int:
    """Rank tier for a recommended action:
    2 = in-flight work (advances existing entities),
    3 = stale review,
    4 = new work (no entity refs — starts something)."""
    if rec.get("action") in _STALE_ACTIONS:
        return 3
    if rec.get("entity_refs"):
        return 2
    return 4


def _lab_next_actions(servers: dict[str, dict]) -> list[dict]:
    """Synthesize the ranked lab action list.

    Tiers: (1) blockers — down channels and unreachable servers that
    prevent in-flight work anywhere; (2) in-flight work; (3) stale;
    (4) new work. Ties break lower loop first.
    """
    entries: list[tuple[int, int, dict]] = []

    for name in CHANNEL_ORDER:
        srv = servers.get(name, {})
        if srv.get("status") != "ok":
            if srv.get("status") == "unreachable":
                entries.append((
                    1, _LOOP_ORDER[name],
                    {
                        "server": name,
                        "action": "restore_channel",
                        "tool": None,
                        "reason": (
                            f"{name} is unreachable — its open work "
                            "is invisible until the channel recovers "
                            f"({srv.get('detail', '')})"
                        ),
                    },
                ))
            continue
        digest = srv["digest"]
        for b in digest.get("blockers", []):
            entries.append((
                1, _LOOP_ORDER[name],
                {
                    "server": digest.get("server", name),
                    "action": "restore_channel",
                    "tool": None,
                    "reason": (
                        f"{b.get('channel')} channel down on "
                        f"{digest.get('server', name)} — blocks "
                        f"{', '.join(b.get('blocks') or ['?'])}"
                    ),
                },
            ))
        for rec in digest.get("recommended_next", []):
            if rec.get("action") in _NON_ACTIONS:
                continue
            entries.append((
                _action_tier(rec), _LOOP_ORDER[name],
                {
                    "server": digest.get("server", name),
                    "action": rec.get("action"),
                    "tool": rec.get("tool"),
                    "entity_refs": rec.get("entity_refs", []),
                    "reason": rec.get("reason", ""),
                    "prompt_hint": (
                        f"status_report on {digest.get('server', name)}"
                    ),
                    **({"blocked": True} if rec.get("blocked") else {}),
                },
            ))

    entries.sort(key=lambda e: (e[0], e[1]))
    return [
        {"rank": i + 1, **entry}
        for i, (_, _, entry) in enumerate(entries)
    ]


async def lab_status(adaptors) -> dict:
    """The lab status aggregate — the cross-server contract shape."""
    servers: dict[str, dict] = {}
    for name in CHANNEL_ORDER:
        servers[name] = await _fetch_digest(adaptors, name)

    ok = sum(1 for s in servers.values() if s["status"] == "ok")
    return {
        "server": "ml-agora-mcp",
        "role": "status",
        "generated_at": _utc_now_iso(),
        "servers_reachable": ok,
        "servers_configured": sum(
            1 for s in servers.values()
            if s["status"] != "not_configured"
        ),
        "servers": servers,
        "lab_next_actions": _lab_next_actions(servers),
    }


def register(mcp, adaptors) -> None:
    """Register the lab status resource."""

    @mcp.resource("lab://status")
    async def get_status() -> str:
        """The lab status aggregate — per-server digests plus the
        ranked next-action list. Computed live on every read."""
        return json.dumps(await lab_status(adaptors), indent=2)
