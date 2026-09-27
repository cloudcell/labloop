"""Observability GUI tests — the read-only projection over search.db."""

from __future__ import annotations

import urllib.request

import pytest

from .conftest import call_tool_http


def _get(url: str) -> tuple[int, str]:
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


async def test_gui_index_and_detail(zetesis_http_server):
    url, gui, _ev, _cl = zetesis_http_server

    inv_id = await call_tool_http(url, "open_investigation", {
        "question": "does the GUI render?",
        "scope": {"k": "v"},
    })
    inv_id = inv_id["investigation_id"]
    await call_tool_http(url, "pull_evidence", {
        "investigation_id": inv_id, "source": "loop0",
        "tool": "list_active_programmes",
    })

    status, body = _get(gui + "/")
    assert status == 200
    assert "Zetesis" in body
    assert inv_id in body

    status, body = _get(f"{gui}/investigation/{inv_id}")
    assert status == 200
    assert "does the GUI render?" in body
    assert "Evidence trail (1)" in body
    assert "list_active_programmes" in body

    status, body = _get(f"{gui}/investigation/inv-nope")
    assert status == 404


async def test_gui_health(zetesis_http_server):
    _url, gui, _ev, _cl = zetesis_http_server
    status, body = _get(gui + "/health")
    assert status == 200
    assert '"ok"' in body


async def test_gui_promotion_pages(zetesis_promotion_server):
    """The /promotion index and /campaign/<id> detail render the
    Loop-1 promotion state read-only — roster entries, campaign rows,
    per-arm results."""
    zet, gui, loop0, _claims = zetesis_promotion_server

    # Seed a campaign through the real path.
    champion = (await call_tool_http(loop0, "register_candidate", {
        "code_artifact_digest": "none",
        "model_ref": "gui-model",
        "capability_profile": {},
    }))["candidate_id"]
    challenger = (await call_tool_http(loop0, "register_candidate", {
        "code_artifact_digest": "none",
        "model_ref": "gui-model-2",
        "capability_profile": {},
        "parent_id": champion,
    }))["candidate_id"]
    await call_tool_http(loop0, "record_promotion_decision", {
        "candidate_id": champion, "verdict": "promote",
        "evidence_refs": ["trial-g"], "rationale": "gui seed",
        "decided_by": "human:gui",
    })
    await call_tool_http(zet, "refresh_roster", {"dry_run": False})
    await call_tool_http(zet, "register_challenger", {
        "candidate_id": challenger,
    })
    prog = (await call_tool_http(loop0, "create_programme", {
        "goal": "gui arm", "constraints": {},
        "allowed_variables": ["lr"],
        "budget": {"max_trials": 5, "max_wall_time_hours": 1.0},
        "candidate_version_id": champion,
    }))["programme_id"]
    contract = (await call_tool_http(
        loop0, "create_evaluation_contract", {
            "programme_id": prog,
            "metrics": {"hits": "maximize"},
            "promotion_policy": {},
        },
    ))["contract_id"]
    cid = (await call_tool_http(zet, "open_campaign", {
        "contract_id": contract, "challenger_id": challenger,
        "budget": {},
    }))["campaign_id"]

    status, body = _get(gui + "/promotion")
    assert status == 200
    assert "Promotion" in body
    assert challenger in body
    assert cid in body
    # Roster rows carry id anchors and link to the candidate page.
    assert f'id="{champion}"' in body
    assert f'id="{challenger}"' in body
    assert f'href="/candidate/{challenger}"' in body

    status, body = _get(f"{gui}/campaign/{cid}")
    assert status == 200
    assert champion in body and challenger in body
    assert "hits" in body
    assert f'href="/candidate/{champion}"' in body
    assert f'href="/candidate/{challenger}"' in body

    status, body = _get(f"{gui}/campaign/camp-nope")
    assert status == 404

    # The candidate page: roster entry + lineage + campaigns
    # where the candidate ran as challenger.
    status, body = _get(f"{gui}/candidate/{challenger}")
    assert status == 200
    assert challenger in body
    assert champion in body  # parent link
    assert cid in body       # its campaign row
    assert "challenger" in body

    status, body = _get(f"{gui}/candidate/cand-ghost")
    assert status == 404
