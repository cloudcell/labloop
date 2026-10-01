"""Foreign evidence refs must be marked, not silently trusted — rc-15 F3.

claim-mint-loop2-reftypes: `["eref-doesnotexist99"]` was accepted and
persisted as decision-70c251e7 with nothing distinguishing it from a
ref that ever resolved. Zetesis legitimately forwards real eref- ids
into record_promotion_decision, so flat refusal would break the
promotion pipeline — the fix is honest marking: refs the server
cannot verify land in the record's unverified_refs. claim- ids are
probed on the claims channel when wired.
"""

import json
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def store():
    from ml_episteme_mcp.state.store import StateStore

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    s = StateStore(path)
    s.connect()
    yield s
    s.close()
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def client(store):
    from mcp.client import Client

    from ml_episteme_mcp.server import create_server

    return Client(create_server(store))


@pytest.fixture
def claims_client(store):
    """A server whose claims channel resolves only claim-known1."""
    from mcp.client import Client

    from ml_episteme_mcp.server import create_server

    class _StubClaims:
        async def get_claim(self, claim_id):
            if claim_id == "claim-known1":
                return {"claim_id": claim_id}
            raise KeyError(f"not found: {claim_id}")

    from ml_episteme_mcp.clients.adaptor import create_stub_adaptor

    adaptor = create_stub_adaptor()
    adaptor.set_claims(_StubClaims())
    return Client(create_server(store, adaptor=adaptor))


async def call_tool(client, name: str, args: dict) -> dict:
    result = await client.call_tool(name, args)
    text = result.content[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        if result.is_error:
            return {"error": text}
        raise


async def _register_candidate(client) -> str:
    r = await call_tool(client, "register_candidate", {
        "code_artifact_digest": "none",
        "model_ref": "test-model",
        "capability_profile": {"tools": ["t1"]},
    })
    assert "error" not in r, r
    return r["candidate_id"]


@pytest.mark.asyncio
class TestUnverifiedEvidenceRefs:
    async def test_fabricated_eref_marked_unverified(self, client):
        """The rc-15 repro: a foreign ref that cannot be checked is
        still recorded — but the record says so."""
        async with client:
            cand = await _register_candidate(client)
            r = await call_tool(client, "record_promotion_decision", {
                "candidate_id": cand,
                "verdict": "promote",
                "evidence_refs": ["eref-doesnotexist99"],
                "rationale": "upstream evidence",
                "decided_by": "human",
            })
            assert "error" not in r, r
            assert r["unverified_refs"] == ["eref-doesnotexist99"]

            lst = await call_tool(client, "list_promotion_decisions", {
                "candidate_id": cand,
            })
            assert "error" not in lst, lst
            assert lst["decisions"][0]["unverified_refs"] == [
                "eref-doesnotexist99"
            ]

    async def test_local_ref_never_unverified(self, client):
        """A Loop-0 id that passed _LOOP0_GETTERS is resolved by
        construction — it must not appear in unverified_refs."""
        async with client:
            cand = await _register_candidate(client)
            r = await call_tool(client, "record_promotion_decision", {
                "candidate_id": cand,
                "verdict": "promote",
                "evidence_refs": [cand],  # cand- is a local mint
                "rationale": "replicated improvement",
                "decided_by": "human",
            })
            assert "error" not in r, r
            assert r["unverified_refs"] == []

    async def test_unknown_prefix_still_refused(self, client):
        async with client:
            cand = await _register_candidate(client)
            r = await call_tool(client, "record_promotion_decision", {
                "candidate_id": cand,
                "verdict": "promote",
                "evidence_refs": ["evr-invented1"],
                "rationale": "invented prefix",
                "decided_by": "human",
            })
            assert "error" in r
            assert "unrecognised" in r["error"]

    async def test_claim_ref_resolves_over_claims_channel(
            self, claims_client):
        """claim- ids are probed on the wired claims channel — a hit
        is verified, a miss lands in unverified_refs."""
        async with claims_client:
            cand = await _register_candidate(claims_client)
            r = await call_tool(
                claims_client, "record_promotion_decision", {
                    "candidate_id": cand,
                    "verdict": "promote",
                    "evidence_refs": ["claim-known1", "claim-missing9"],
                    "rationale": "claim-backed verdict",
                    "decided_by": "human",
                })
            assert "error" not in r, r
            assert r["unverified_refs"] == ["claim-missing9"]

    async def test_claim_ref_unverified_when_channel_absent(
            self, client):
        """No claims channel → claim- ids are unverifiable by
        construction — marked, not trusted."""
        async with client:
            cand = await _register_candidate(client)
            r = await call_tool(client, "record_promotion_decision", {
                "candidate_id": cand,
                "verdict": "hold",
                "evidence_refs": ["claim-known1"],
                "rationale": "defer",
                "decided_by": "human",
            })
            assert "error" not in r, r
            assert r["unverified_refs"] == ["claim-known1"]
