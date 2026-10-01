"""Connectivity payload coherence — rc-15 F4.

The rc-15 extraction showed the payload contradicting itself under
fault: ``state: "down"`` coexisting with ``probe: "busy"`` (a stale
probe state survives the transition), ``state: "up"`` reading a stale
or null probe before the first post-connect ping, and one-line
summaries claiming "all up" while a channel was probe-busy.
"""

import pytest


def _spec():
    from ml_zetesis_mcp.clients.adaptors import ChannelSpec

    class _A:
        config = {"url": "http://peer:1/mcp"}

    return ChannelSpec("claims", "claims", "http://peer:1/mcp", _A())


class TestProbeFollowsState:
    def test_mark_down_clears_probe(self):
        """down + busy is a lie — a dead channel has no liveness."""
        s = _spec()
        s.probe = "busy"  # the stale pre-transition value
        s.mark_down("upstream session ended")
        assert s.state == "down"
        assert s.probe == "down"

    def test_mark_up_marks_pending(self):
        """up + stale-ok is optimism — a fresh connect is unprobed
        until the first supervisor ping answers."""
        s = _spec()
        s.probe = "ok"  # stale from a previous session
        s.mark_up()
        assert s.state == "up"
        assert s.probe == "pending"


class TestBusySurfaces:
    def test_check_detail_names_busy(self):
        """The one-line detail must not say "all up" while a channel
        is probe-busy — the exact payload rc-15 flagged."""
        from ml_zetesis_mcp.integrity.checks import (
            _check_upstream_connectivity,
        )

        connectivity = [
            {"channel": "claims", "state": "up", "probe": "busy"},
            {"channel": "loop0-read", "state": "up", "probe": "ok"},
        ]
        res = _check_upstream_connectivity(connectivity)
        assert res["violations"] == []  # busy is not a violation…
        assert "busy" in res["detail"]  # …but it IS in the summary
        assert "all up" not in res["detail"]

    def test_digest_verdict_degraded_on_busy(self):
        from ml_zetesis_mcp.resources.status import _upstream_summary

        class _A:
            def connectivity_report(self):
                return [
                    {"channel": "claims", "state": "up",
                     "probe": "busy"},
                ]

        summary = _upstream_summary(_A())
        assert summary["verdict"] == "degraded"
        assert summary["busy"] == 1

    def test_digest_verdict_ok_when_clean(self):
        from ml_zetesis_mcp.resources.status import _upstream_summary

        class _A:
            def connectivity_report(self):
                return [
                    {"channel": "claims", "state": "up",
                     "probe": "ok"},
                ]

        assert _upstream_summary(_A())["verdict"] == "ok"
