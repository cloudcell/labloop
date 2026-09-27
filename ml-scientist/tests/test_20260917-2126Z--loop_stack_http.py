"""Cross-loop HTTP battery — the whole stack wired real.

Four real subprocesses: ml_episteme_mcp (Loop 0), ml_anamnesis_mcp
(semantic memory), ml_zetesis_mcp (Loop 1, evidence+promotion+claims
channels all wired), and ml_arete_mcp (Loop 2, loop0/loop1/claims
channels all wired). Every link in the chain is a real server with a
real store — no stubs, no fakes.

This is the permanent home of the manual live-verification script the
promotion increment was proven with: candidate lineage → roster →
campaign → attribution gate → results → close → upstream verdict →
incumbent re-derivation → anamnesis claim → arete reading Loop-1
promotion state through its whitelist. The per-server batteries prove
each link against stubs or a single real upstream; this proves the
composed stack.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import call_tool_http, find_free_port, wait_for_port

REPO_ROOT = Path(__file__).parent.parent


def _spawn(cmd: list[str], log_path: str, procs, logs):
    lf = open(log_path, "w")
    logs.append((lf, log_path))
    procs.append(subprocess.Popen(
        cmd,
        stdout=lf, stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        cwd=str(REPO_ROOT),
    ))


@pytest.fixture(scope="session")
def loop_stack():
    """Launch the real four-server stack on ephemeral ports.

    Yields dicts of base URLs: {"loop0", "anamnesis", "zetesis",
    "arete"} (each is the MCP endpoint root; append /mcp inside
    call_tool_http).
    """
    ts = f"{int(time.time())}-{os.getpid()}"
    tmp = Path(f"/tmp/loop-stack-{ts}")
    tmp.mkdir(parents=True, exist_ok=True)

    ports = {
        "loop0": find_free_port(),
        "anamnesis": find_free_port(),
        "zetesis": find_free_port(),
        "arete": find_free_port(),
    }
    procs: list[subprocess.Popen] = []
    logs: list[tuple] = []
    try:
        # Loop 0 — no upstreams.
        _spawn(
            [
                sys.executable, "-m", "ml_episteme_mcp",
                "--transport", "http", "--port", str(ports["loop0"]),
                "--stateless",
                "--db-path", str(tmp / "state.db"),
            ],
            str(tmp / "loop0.log"), procs, logs,
        )
        wait_for_port(ports["loop0"], timeout=20.0)

        # Anamnesis — no upstreams.
        _spawn(
            [
                sys.executable, "-m", "ml_anamnesis_mcp",
                "--transport", "http", "--port", str(ports["anamnesis"]),
                "--stateless",
                "--db-path", str(tmp / "memory.db"),
            ],
            str(tmp / "anamnesis.log"), procs, logs,
        )
        wait_for_port(ports["anamnesis"], timeout=20.0)

        # Zetesis — evidence + promotion to Loop 0, claims to anamnesis.
        (tmp / "zetesis.toml").write_text(f'''
db_path = "{tmp / "search.db"}"

[adaptors.evidence]
transport = "streamable-http"
url = "http://127.0.0.1:{ports["loop0"]}/mcp"

[adaptors.promotion]
transport = "streamable-http"
url = "http://127.0.0.1:{ports["loop0"]}/mcp"

[adaptors.claims]
transport = "streamable-http"
url = "http://127.0.0.1:{ports["anamnesis"]}/mcp"
''')
        _spawn(
            [
                sys.executable, "-m", "ml_zetesis_mcp",
                "--transport", "http", "--port", str(ports["zetesis"]),
                "--stateless",
                "--db-path", str(tmp / "search.db"),
                "--config", str(tmp / "zetesis.toml"),
            ],
            str(tmp / "zetesis.log"), procs, logs,
        )
        wait_for_port(ports["zetesis"], timeout=20.0)

        # Arete — loop0 + loop1 + claims all real.
        (tmp / "arete.toml").write_text(f'''
db_path = "{tmp / "improver.db"}"

[adaptors.loop0]
transport = "streamable-http"
url = "http://127.0.0.1:{ports["loop0"]}/mcp"

[adaptors.loop1]
transport = "streamable-http"
url = "http://127.0.0.1:{ports["zetesis"]}/mcp"

[adaptors.claims]
transport = "streamable-http"
url = "http://127.0.0.1:{ports["anamnesis"]}/mcp"
''')
        _spawn(
            [
                sys.executable, "-m", "ml_arete_mcp",
                "--transport", "http", "--port", str(ports["arete"]),
                "--stateless",
                "--db-path", str(tmp / "improver.db"),
                "--config", str(tmp / "arete.toml"),
            ],
            str(tmp / "arete.log"), procs, logs,
        )
        wait_for_port(ports["arete"], timeout=20.0)
    except TimeoutError:
        for p in procs:
            p.terminate()
        for lf, lp in logs:
            lf.close()
            print(f"LOG {lp}:", Path(lp).read_text())
        raise

    yield {k: f"http://127.0.0.1:{p}" for k, p in ports.items()}

    for p in procs:
        p.terminate()
    for p in procs:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
    for lf, lp in logs:
        lf.close()
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


async def _register_candidate(loop0, digest, parent_id=None):
    args = {
        "code_artifact_digest": digest,
        "model_ref": f"model-{digest[-4:]}",
        "capability_profile": {},
    }
    if parent_id:
        args["parent_id"] = parent_id
    r = await call_tool_http(loop0, "register_candidate", args)
    assert "error" not in r, r
    return r["candidate_id"]


class TestFullStackPromotion:
    """The live-verified lifecycle, end to end over real servers."""

    async def test_promotion_to_meta_evidence(self, loop_stack):
        loop0 = loop_stack["loop0"]
        zet = loop_stack["zetesis"]
        arete = loop_stack["arete"]

        # 1. Upstream lineage: champion promoted, challenger registered.
        champ = await _register_candidate(loop0, "none")
        chall = await _register_candidate(
            loop0, "none", parent_id=champ,
        )
        r = await call_tool_http(loop0, "record_promotion_decision", {
            "candidate_id": champ, "verdict": "promote",
            "evidence_refs": ["trial-stack"], "rationale": "champion",
            "decided_by": "human:stack",
        })
        assert "error" not in r, r
        r = await call_tool_http(loop0, "get_incumbent", {})
        assert r["candidate_id"] == champ

        # 2-3. Roster refresh adopts; challenger marked.
        r = await call_tool_http(zet, "refresh_roster", {"dry_run": False})
        assert "error" not in r, r
        assert champ in r["adopted"] and chall in r["adopted"]
        r = await call_tool_http(zet, "register_challenger", {
            "candidate_id": chall,
        })
        assert r.get("derived_status") == "challenger"

        # 4. Programmes per arm + an evaluation contract upstream.
        prog_c = (await call_tool_http(loop0, "create_programme", {
            "goal": f"stack champion arm {champ[-4:]}",
            "constraints": {}, "allowed_variables": ["lr"],
            "budget": {"max_trials": 5, "max_wall_time_hours": 1.0},
            "candidate_version_id": champ,
        }))["programme_id"]
        prog_x = (await call_tool_http(loop0, "create_programme", {
            "goal": f"stack challenger arm {chall[-4:]}",
            "constraints": {}, "allowed_variables": ["lr"],
            "budget": {"max_trials": 5, "max_wall_time_hours": 1.0},
            "candidate_version_id": chall,
        }))["programme_id"]
        contract = (await call_tool_http(
            loop0, "create_evaluation_contract", {
                "programme_id": prog_c,
                "metrics": {"hits": "maximize"},
                "promotion_policy": {},
            },
        ))["contract_id"]

        # 5. Campaign opens against the derived champion.
        r = await call_tool_http(zet, "open_campaign", {
            "contract_id": contract, "challenger_id": chall,
            "budget": {"seeds": 3},
        })
        assert "error" not in r, r
        assert r["champion_id"] == champ
        cid = r["campaign_id"]

        # 6. Attribution gate: champion's programme can't post as the
        # challenger arm.
        r = await call_tool_http(zet, "record_campaign_result", {
            "campaign_id": cid, "arm": "challenger",
            "programme_id": prog_c, "metrics": {"hits": 999},
        })
        assert "error" in r

        # 7. Real results on both arms.
        for arm, prog, hits in (
            ("champion", prog_c, 80), ("challenger", prog_x, 120),
        ):
            r = await call_tool_http(zet, "record_campaign_result", {
                "campaign_id": cid, "arm": arm,
                "programme_id": prog, "metrics": {"hits": hits},
            })
            assert "error" not in r, r

        # 8. Campaign evidence + close → frozen promotion score.
        ev = await call_tool_http(zet, "pull_campaign_evidence", {
            "campaign_id": cid, "source": "loop0",
            "tool": "get_candidate_scorecard",
            "args": {"candidate_id": chall},
        })
        assert "error" not in ev, ev
        r = await call_tool_http(zet, "close_campaign", {
            "campaign_id": cid,
        })
        assert r["promotion_score"] == pytest.approx(1.5)

        # 9. Verdict writes upstream; incumbent re-derives; the claim
        # mints against REAL anamnesis — the inline-evidence path the
        # stub batteries can't exercise (anamnesis caps confidence
        # above the prior ceiling without edges in the same call).
        r = await call_tool_http(zet, "record_promotion_verdict", {
            "campaign_id": cid, "verdict": "promote",
            "decided_by": "human:stack",
            "evidence_ref_ids": [ev["evidence_ref_id"]],
        })
        assert "error" not in r, r
        assert r["decision_id"].startswith("decision-")
        assert r["claim_status"] == "minted"
        assert r["claim_id"].startswith("claim-")

        inc = await call_tool_http(loop0, "get_incumbent", {})
        assert inc["candidate_id"] == chall

        # The minted claim is really in anamnesis, with its edge.
        claim = await call_tool_http(
            loop_stack["anamnesis"], "get_claim",
            {"claim_id": r["claim_id"]},
        )
        assert "error" not in claim, claim

        # 10. Arete reads Loop-1 promotion state through its whitelist —
        # under a live context (proposals/tournaments scope pulls).
        imp = (await call_tool_http(arete, "register_improver", {
            "code_artifact_digest": "none",
            "model_ref": "stack-improver",
            "capability_profile": {},
        }))["improver_id"]
        prop = await call_tool_http(arete, "propose_meta_change", {
            "proposer_improver_id": imp,
            "spec_delta": {"change": "read loop1 promotion state"},
            "class_map": {"search_policy": "class-2"},
            "expected_benefit": "observe real campaign outcomes",
            "falsification": "campaigns unreachable via loop1",
            "rollback_plan": "no mutation — read-only pull",
        })
        assert "error" not in prop, prop
        pid = prop["proposal_id"]

        r = await call_tool_http(arete, "pull_evidence", {
            "context_type": "proposal", "context_id": pid,
            "source": "loop1", "tool": "get_campaign",
            "args": {"campaign_id": cid},
        })
        assert "error" not in r, r
        assert cid in str(r.get("result", r))

        r = await call_tool_http(arete, "pull_evidence", {
            "context_type": "proposal", "context_id": pid,
            "source": "loop1", "tool": "get_incumbent",
            "args": {},
        })
        assert "error" not in r, r
        assert chall in str(r.get("result", r))
