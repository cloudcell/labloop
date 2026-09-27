"""Observability GUI tests — read-only HTML views over the claim graph."""

from __future__ import annotations

import json
import urllib.request

import pytest

from .conftest import call_tool_http


def _get(url: str, headers: dict | None = None) -> tuple[int, str]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


async def _seed(url):
    cid = await call_tool_http(url, "assert_claim", {
        "content": "gui-visible claim alpha",
        "type": "empirical",
        "confidence": 0.8,
        "evidence": [
            {"to_ref": "trial-gui", "ref_type": "trial",
             "relation": "tested_by"}
        ],
    })
    return cid["claim_id"]


class TestAnamnesisGUI:
    @pytest.mark.asyncio
    async def test_health_json(self, anamnesis_gui_url):
        code, body = _get(f"{anamnesis_gui_url}/health")
        assert code == 200
        data = json.loads(body)
        assert data["status"] == "ok" and data["mcp"] == "up"

    @pytest.mark.asyncio
    async def test_health_pill_fragment(self, anamnesis_gui_url):
        code, body = _get(
            f"{anamnesis_gui_url}/health", {"HX-Request": "true"}
        )
        assert code == 200
        assert "health-pill ok" in body
        assert 'hx-trigger="every 10s"' in body

    @pytest.mark.asyncio
    async def test_index_lists_claims(self, anamnesis_url, anamnesis_gui_url):
        cid = await _seed(anamnesis_url)
        code, body = _get(f"{anamnesis_gui_url}/")
        assert code == 200
        assert "Claims Memory" in body
        assert "gui-visible claim alpha" in body
        assert "live" in body

    @pytest.mark.asyncio
    async def test_claim_detail_shows_provenance(
        self, anamnesis_url, anamnesis_gui_url
    ):
        cid = await _seed(anamnesis_url)
        code, body = _get(f"{anamnesis_gui_url}/claim/{cid}")
        assert code == 200
        assert "tested_by" in body
        assert "trial-gui" in body
        assert "Cites" in body and "Cited by" in body

    @pytest.mark.asyncio
    async def test_claim_detail_404(self, anamnesis_gui_url):
        code, body = _get(f"{anamnesis_gui_url}/claim/claim-nope")
        assert code == 404

    @pytest.mark.asyncio
    async def test_search_fragment(self, anamnesis_url, anamnesis_gui_url):
        await _seed(anamnesis_url)
        code, body = _get(f"{anamnesis_gui_url}/search?q=gui-visible")
        assert code == 200
        assert "gui-visible claim alpha" in body
        code, body = _get(f"{anamnesis_gui_url}/search?q=nomatch-zzz")
        assert code == 200
        assert "No claims match" in body

    @pytest.mark.asyncio
    async def test_view_tabs_filter(self, anamnesis_url, anamnesis_gui_url):
        """Superseded claims hidden in live view, visible in superseded tab."""
        old = await call_tool_http(anamnesis_url, "assert_claim", {
            "content": "doomed gui claim",
            "type": "empirical",
            "confidence": 0.8,
            "evidence": [
                {"to_ref": "t", "ref_type": "trial", "relation": "tested_by"}
            ],
        })
        await call_tool_http(anamnesis_url, "assert_claim", {
            "content": "replacer gui claim",
            "type": "empirical",
            "confidence": 0.8,
            "supersedes_id": old["claim_id"],
            "evidence": [
                {"to_ref": "t", "ref_type": "trial", "relation": "tested_by"}
            ],
        })
        code, body = _get(f"{anamnesis_gui_url}/?view=live")
        assert "doomed gui claim" not in body
        code, body = _get(f"{anamnesis_gui_url}/?view=superseded")
        assert "doomed gui claim" in body
