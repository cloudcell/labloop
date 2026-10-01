"""Staleness gates must not strand their own remediation — rc-15 F2.

The rc-15 extraction reproduced gates blocking the very writes their
violation text names as the exit:

- arete ``stale_open_tournaments`` measures last_activity off
  tournament_results / evidence_refs / created_at — so
  ``record_tournament_result`` and the evidence pulls ARE the
  remediation. They were not in REMEDY_TOOLS: the gate refused them,
  leaving acknowledge_violation as the only exit.
- zetesis ``incomplete_campaigns`` tells the operator to "populate
  the missing side … or abandon_campaign" — then refused
  record_campaign_result, close_campaign AND abandon_campaign.

Regression: a synthetic open violation must not gate the tools that
remediate it, while still gating unrelated mutators.
"""

import pytest


def _zstore(tmp_path):
    from ml_zetesis_mcp.state.store import SearchStore

    ss = SearchStore(str(tmp_path / "s.db"))
    ss.connect()
    return ss


def _astore(tmp_path):
    from ml_arete_mcp.state.store import ImproverStore

    st = ImproverStore(str(tmp_path / "i.db"))
    st.connect()
    return st


def _log_violation(store, log_dir_for, write_check_log,
                   check_name, violation):
    write_check_log(log_dir_for(store), {
        "server": "test",
        "checked_at": "2026-01-01T00:00:00+00:00",
        "checks": [{
            "name": check_name, "ok": False,
            "violations": [violation],
            "detail": "synthetic",
        }],
    }, max_files=30)


class TestStaleOpenTournamentRemedies:
    """arete: staleness is last_activity-derived — every write that
    refreshes it (a result, a pulled evidence ref) plus both disposal
    exits must pass the gate."""

    def test_result_and_pulls_pass_gate(self, tmp_path):
        from ml_arete_mcp.enforcement.recurrence import (
            check_no_open_violations,
        )
        from ml_arete_mcp.integrity.checks import log_dir_for
        from ml_arete_mcp.integrity.log import write_check_log

        st = _astore(tmp_path)
        try:
            _log_violation(st, log_dir_for, write_check_log,
                           "stale_open_tournaments",
                           {"tournament_id": "tourn-x"})
            for remedy in (
                "record_tournament_result", "pull_evidence",
                "pull_arm_evidence", "close_tournament",
                "void_tournament",
            ):
                assert check_no_open_violations(st, remedy) is None, remedy
            # …but the gate still bites unrelated mutators.
            assert check_no_open_violations(
                st, "register_improver") is not None
            assert check_no_open_violations(
                st, "open_tournament") is not None
        finally:
            st.close()


class TestIncompleteCampaignRemedies:
    """zetesis: the check names fill-or-abandon as the exits — every
    named verb must pass its own gate."""

    def test_fill_and_abandon_pass_gate(self, tmp_path):
        from ml_zetesis_mcp.enforcement.recurrence import (
            check_no_open_violations,
        )
        from ml_zetesis_mcp.integrity.checks import log_dir_for
        from ml_zetesis_mcp.integrity.log import write_check_log

        ss = _zstore(tmp_path)
        try:
            _log_violation(ss, log_dir_for, write_check_log,
                           "incomplete_campaigns",
                           {"campaign_id": "camp-x"})
            for remedy in (
                "spawn_arm_programme", "spawn_campaign_programme",
                "record_campaign_result", "close_campaign",
                "abandon_campaign",
            ):
                assert check_no_open_violations(ss, remedy) is None, remedy
            # …but the gate still bites unrelated mutators.
            assert check_no_open_violations(
                ss, "open_campaign") is not None
            assert check_no_open_violations(
                ss, "open_investigation") is not None
        finally:
            ss.close()
