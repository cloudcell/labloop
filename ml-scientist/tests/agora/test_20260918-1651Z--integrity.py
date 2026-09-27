"""Tests for the agora integrity suite — upstream_connectivity and
status_reachability, plus the JSONL audit log. Report-only: checks
never repair or mutate.
"""

from __future__ import annotations

import json

import pytest

from ml_agora_mcp.clients.adaptors import Adaptors
from ml_agora_mcp.integrity.checks import run_and_log, run_checks
from ml_agora_mcp.integrity.log import list_check_logs, read_check_log


async def test_connectivity_all_up(adaptors):
    payload = await run_checks(
        adaptors, connectivity=adaptors.connectivity_report()
    )
    assert payload["status"] == "ok"
    conn = next(
        c for c in payload["checks"]
        if c["name"] == "upstream_connectivity"
    )
    assert conn["ok"] is True
    assert conn["violations"] == []


async def test_connectivity_down_channel_violation(adaptors):
    """A configured-but-down channel is a violation carrying the
    channel/role/target/last_error."""
    adaptors.loop0 = None
    adaptors._channels["loop0"].mark_down("refused")
    payload = await run_checks(
        adaptors, connectivity=adaptors.connectivity_report()
    )
    assert payload["status"] == "violations"
    conn = next(
        c for c in payload["checks"]
        if c["name"] == "upstream_connectivity"
    )
    assert conn["ok"] is False
    assert conn["violations"][0]["channel"] == "loop0"
    assert conn["violations"][0]["last_error"] == "refused"


async def test_connectivity_unconfigured_skipped():
    """No channels at all → skipped, not violations — standalone
    mode is a supported posture."""
    a = Adaptors()
    payload = await run_checks(a, connectivity=None)
    conn = next(
        c for c in payload["checks"]
        if c["name"] == "upstream_connectivity"
    )
    assert conn["ok"] is True
    assert conn.get("skipped") is True


async def test_reachability_flags_connected_but_broken(adaptors):
    """Connected ≠ serving — a channel whose digest read fails is
    flagged even though connectivity is green."""
    adaptors.claims.fail = True
    payload = await run_checks(
        adaptors, connectivity=adaptors.connectivity_report()
    )
    reach = next(
        c for c in payload["checks"]
        if c["name"] == "status_reachability"
    )
    assert reach["ok"] is False
    assert reach["violations"][0]["channel"] == "claims"
    assert reach["violations"][0]["status_uri"] == "claims://status"


async def test_reachability_flags_unparseable_digest(adaptors):
    async def bad_read(uri):
        return "not json"

    adaptors.loop2.read_resource = bad_read
    payload = await run_checks(
        adaptors, connectivity=adaptors.connectivity_report()
    )
    reach = next(
        c for c in payload["checks"]
        if c["name"] == "status_reachability"
    )
    assert reach["ok"] is False


async def test_run_and_log_writes_audit(adaptors, tmp_path):
    log_dir = tmp_path / "logs"
    payload = await run_and_log(
        adaptors,
        connectivity=adaptors.connectivity_report(),
        log_dir=log_dir,
        trigger="tool",
    )
    assert payload["trigger"] == "tool"
    assert "log_file" in payload
    runs = list_check_logs(log_dir)
    assert len(runs) == 1
    assert runs[0]["status"] == "ok"
    day = read_check_log(log_dir, runs[0]["file"])
    assert day[0]["server"] == "ml-agora-mcp"


async def test_monitor_skips_when_interval_zero(adaptors, tmp_path):
    from ml_agora_mcp.integrity.checks import start_integrity_monitor

    task = await start_integrity_monitor(
        adaptors,
        connectivity=adaptors.connectivity_report,
        log_dir=tmp_path / "logs",
        config={"check_interval_seconds": 0},
    )
    assert task is None  # startup sweep logged, no periodic loop
    assert list_check_logs(tmp_path / "logs")
