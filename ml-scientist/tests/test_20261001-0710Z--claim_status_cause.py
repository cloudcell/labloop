"""claim_status "disabled"/"failed" must name the cause — rc-15 F5.

The extraction showed a decision succeeding with
``claim_id: null, claim_status: "disabled", edges_created: 0`` and
nothing saying *why* — a caller not cross-referencing the blockers
list reads it as policy, not as "the claims channel was down". Now
the payload carries claim_error naming the cause: unwired
("not configured") vs down ("claims channel down: <last_error>").
"""

import json

import pytest


def _zstore(tmp_path):
    from ml_zetesis_mcp.state.store import SearchStore

    ss = SearchStore(str(tmp_path / "s.db"))
    ss.connect()
    return ss


async def _zcall(mcp, name, args):
    result = await mcp.call_tool(name, args)
    text = result.content[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"error": text}


def _zserver(ss, adaptors):
    from ml_zetesis_mcp.server import create_server

    return create_server(ss, adaptors=adaptors)


async def _concluded_inv(mcp):
    inv = await _zcall(mcp, "open_investigation", {
        "question": "q", "scope": {},
    })
    assert "error" not in inv, inv
    await _zcall(mcp, "record_finding", {
        "investigation_id": inv["investigation_id"],
        "content": "f",
    })
    return await _zcall(mcp, "conclude_investigation", {
        "investigation_id": inv["investigation_id"],
        "verdict": "findings", "summary": "s",
    })


@pytest.mark.asyncio
class TestClaimStatusNamesCause:
    async def test_disabled_names_unconfigured(self, tmp_path):
        """No claims channel registered → the payload says so."""
        from ml_zetesis_mcp.clients.adaptors import Adaptors

        ss = _zstore(tmp_path)
        try:
            r = await _concluded_inv(_zserver(ss, Adaptors()))
            assert "error" not in r, r
            assert r["claim_status"] == "disabled"
            assert r["claim_error"] == "claims channel not configured"
        finally:
            ss.close()

    async def test_disabled_names_down(self, tmp_path):
        """A registered-but-down channel reports down + last_error."""
        from ml_zetesis_mcp.clients.adaptors import Adaptors

        class _Dead:
            config = {"url": "http://claims:9/mcp"}

            async def connect(self):
                raise RuntimeError("unused")

        ss = _zstore(tmp_path)
        try:
            adaptors = Adaptors()
            adaptors.register_channel("claims", "claims", _Dead())
            spec = adaptors._channels["claims"]
            spec.mark_down("connect timed out — not answering")
            r = await _concluded_inv(_zserver(ss, adaptors))
            assert "error" not in r, r
            assert r["claim_status"] == "disabled"
            assert "claims channel down" in r["claim_error"]
            assert "connect timed out" in r["claim_error"]
        finally:
            ss.close()

    async def test_arete_disabled_names_cause(self):
        """Arete's shared mint path returns the cause in the tuple
        (persisted onto the decision row as claim_error)."""
        from ml_arete_mcp.clients.adaptors import Adaptors
        from ml_arete_mcp.tools.decisions import _mint_decision_claim

        adaptors = Adaptors()  # no claims channel registered
        _, status, err, _, _ = await _mint_decision_claim(
            store=None, adaptors=adaptors,
            decision=type("D", (), {"evidence_refs": [], "id": "d"})(),
        )
        assert status == "disabled"
        assert err == "claims channel not configured"
