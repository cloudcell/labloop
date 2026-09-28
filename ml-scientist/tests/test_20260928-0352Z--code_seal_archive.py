"""R16+R17 — code:// bundles now stage seal overlays; unsealed_execution
distinguishes sealing mechanisms.

The ding2026 audit (lab-vm-rc-8-all-gaps REPORT.md threat item 3) ran a
trial sealed via capture_bundle_from_code_hash: seal_enforced=false,
0 overlays, and unsealed_execution blocked the programme close — a
false positive (the executed bytes were content-addressed by
construction) AND a real hole (live files at snippet original_paths
were readable/importable with drifted bytes — _deps covers only the
trailing 1–3 path components).

Fix:
- run_trial stages overlays for code:// bundles, filtered to targets
  that are files on the host (bwrap cannot create mountpoints inside
  a read-only namespace). File-path bundles keep the unfiltered list
  — their code delivery IS the original path, so a missing target
  must fail closed.
- unsealed_execution exempts code_ref LIKE 'code://%' rows — sealed
  via content address.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ml_episteme_mcp.clients.local_executor import LocalExecutor
from ml_episteme_mcp.state.models import (
    Bundle,
    CodeSnippet,
    Hypothesis,
    Programme,
    ProgrammeStatus,
    Trial,
    TrialStatus,
)
from ml_episteme_mcp.state.store import StateStore


@pytest.fixture
def store(tmp_path):
    s = StateStore(str(tmp_path / "state.db"))
    s.connect()
    yield s
    s.close()


def _seed(store, pid="prog-s", hid="hyp-s", tid="trial-s"):
    store.create_programme(Programme(
        id=pid, goal="g", constraints={},
        allowed_variables=["x"], budget_max_trials=10,
        budget_max_wall_time_hours=1.0,
        status=ProgrammeStatus.active,
    ))
    store.create_hypothesis(Hypothesis(
        id=hid, programme_id=pid,
        statement="s", failure_criterion="f",
        variables_involved=["x"],
    ))
    store.create_trial(Trial(
        id=tid, programme_id=pid, hypothesis_id=hid,
        config_json="{}", status=TrialStatus.designed,
    ))
    return tid


def _snippet(store, code_text: str, original_path: str | None):
    h = "sha256:" + hashlib.sha256(code_text.encode()).hexdigest()
    store.create_code_snippet(CodeSnippet(
        code_hash=h, code_text=code_text,
        original_path=original_path,
        size_bytes=len(code_text.encode()),
    ))
    return h


def _code_bundle(store, tid, primary_hash, extra_hashes=None):
    b = Bundle(
        id=f"bundle-{tid}", trial_id=tid,
        code_ref=f"code://{primary_hash}",
        code_hash=primary_hash,
        code_hash_extra_json=json.dumps(extra_hashes or []),
        env_ref="local",
        seeds_json="[1]",
        splits_json="{}",
    )
    store.create_bundle(b)
    store.link_bundle(tid, b.id)
    return b


async def _run(store, tmp_path, executor):
    """Register tools on a FakeMCP and invoke run_trial."""
    from ml_episteme_mcp.clients.adaptor import MCPAdaptor
    from ml_episteme_mcp.tools.trial import register

    adaptor = MCPAdaptor({})
    adaptor.set_executor(executor)

    class FakeMCP:
        def __init__(self):
            self.tools = {}

        def tool(self):
            def deco(fn):
                self.tools[fn.__name__] = fn
                return fn
            return deco

    mcp = FakeMCP()
    register(mcp, store, adaptor, executor_config={})
    res = await mcp.tools["run_trial"](
        programme_id="prog-s", trial_id="trial-s",
    )
    return json.loads(res.content[0].text)


@pytest.mark.skipif(
    not Path("/usr/bin/bwrap").exists(), reason="bwrap required"
)
class TestCodeSealOverlay:
    async def test_overlay_arms_when_original_path_exists(
        self, store, tmp_path
    ):
        """code:// bundle + live original_path → sealed bytes are
        staged and mounted: seal_enforced=true, overlays >= 1. The
        trial reads the original absolute path and gets the sealed
        (not drifted) content."""
        tid = _seed(store)

        # The live file was captured, then DRIFTED post-seal.
        live = tmp_path / "proj" / "dep.py"
        live.parent.mkdir(parents=True)
        sealed_dep = "MARKER = 'sealed'\n"
        live.write_text(sealed_dep)
        dep_hash = _snippet(store, sealed_dep, str(live))
        live.write_text("MARKER = 'drifted'\n")  # post-seal edit

        primary = (
            "def run_training(config):\n"
            f"    data = open({str(live)!r}).read()\n"
            "    return {'metrics': {'m': 1.0}, 'variance': {'m': 0.1},"
            "           'read': data}\n"
        )
        prim_hash = _snippet(store, primary, str(tmp_path / "proj" / "main.py"))
        # Primary's original path must exist too for its own overlay —
        # content can be anything (never executed from the path).
        (tmp_path / "proj" / "main.py").write_text("# drifted\n")

        _code_bundle(store, tid, prim_hash, [dep_hash])

        ex = LocalExecutor(timeout=30, sandbox="minimal", trace_reads="off")
        payload = await _run(store, tmp_path, ex)
        assert payload["status"] == "completed", payload
        out = json.loads(
            store.get_trial(tid).executor_output_json
        )
        assert out["seal_enforced"] is True
        assert out["sealed_overlays"] >= 1
        # The run read the SEALED bytes at the original path, not the
        # drifted live file.
        stdout = json.loads(out["stdout"].strip().splitlines()[-1])
        assert stdout["read"] == sealed_dep

    async def test_no_overlay_when_path_absent_but_runs_clean(
        self, store, tmp_path
    ):
        """original_path not on this host → no mountpoint is created
        (bwrap can't under a read-only namespace) and the run still
        completes — content address is the seal."""
        tid = _seed(store)
        primary = (
            "def run_training(config):\n"
            "    return {'metrics': {'m': 1.0}, 'variance': {'m': 0.1}}\n"
        )
        prim_hash = _snippet(
            store, primary, "/nonexistent/orig/main.py"
        )
        _code_bundle(store, tid, prim_hash)

        ex = LocalExecutor(timeout=30, sandbox="minimal", trace_reads="off")
        payload = await _run(store, tmp_path, ex)
        assert payload["status"] == "completed", payload
        out = json.loads(
            store.get_trial(tid).executor_output_json
        )
        # Honest record: no overlays armed (nothing to shadow).
        assert out["seal_enforced"] is False
        assert out["sealed_overlays"] == 0

    async def test_unsealed_execution_exempts_code_ref(self, store, tmp_path):
        """check_invariants: a code:// row with seal_enforced=false is
        sealed via content address — exempt. A file-path row in the
        same shape still flags (no over-exemption)."""
        from ml_episteme_mcp.integrity.checks import run_checks

        # code:// trial, completed, seal_enforced=false (no live paths)
        tid = _seed(store, tid="trial-codeuri")
        _code_bundle(store, tid, "sha256:" + "a" * 64)
        store.update_trial_executor_output(tid, json.dumps({
            "status": "completed", "exit_code": 0,
            "duration_seconds": 0.5,
            "seal_enforced": False, "sandbox": "minimal",
            "launch_refused": False,
        }))
        store.update_trial_status(tid, "running")
        store.update_trial_status(tid, "completed")

        # File-path trial, same executor output — must still flag.
        tid2 = _seed(
            store, pid="prog-s2", hid="hyp-s2", tid="trial-filepath"
        )
        store.create_bundle(Bundle(
            id="bundle-file", trial_id=tid2,
            code_ref="/tmp/trainer.py", code_hash=None,
            env_ref="local", seeds_json="[1]", splits_json="{}",
        ))
        store.link_bundle(tid2, "bundle-file")
        store.update_trial_executor_output(tid2, json.dumps({
            "status": "completed", "exit_code": 0,
            "duration_seconds": 0.5,
            "seal_enforced": False, "sandbox": "minimal",
            "launch_refused": False,
        }))
        store.update_trial_status(tid2, "running")
        store.update_trial_status(tid2, "completed")

        c = next(
            ch for ch in run_checks(store, executor=None)["checks"]
            if ch["name"] == "unsealed_execution"
        )
        assert not c["ok"]
        assert c["violations"] == ["trial-filepath"]

    async def test_drifted_live_dep_serves_sealed_bytes(
        self, store, tmp_path
    ):
        """Direct open() of the absolute original path inside a
        code:// run returns the sealed bytes — the overlay, not the
        drifted live file. (Covered by test_overlay_arms… via read;
        this isolates the channel through an import that _deps cannot
        express — a 4-component module path falls through its 1–3
        component reconstruction.)"""
        tid = _seed(store)

        # Dep lives at a/deep/path/mod.py — 4 trailing components:
        # _dep_relpaths materializes only mod.py, path/mod.py,
        # deep/path/mod.py — never a/deep/path/mod.py. An absolute
        # open() of the original path is the drift channel.
        deep = tmp_path / "a" / "deep" / "path" / "mod.py"
        deep.parent.mkdir(parents=True)
        sealed = "VAL = 'sealed-bytes'\n"
        deep.write_text(sealed)
        dep_hash = _snippet(store, sealed, str(deep))
        deep.write_text("VAL = 'DRIFTED'\n")

        primary = (
            "def run_training(config):\n"
            f"    val = open({str(deep)!r}).read()\n"
            "    return {'metrics': {'m': 1.0}, 'variance': {'m': 0.1},"
            "           'val': val}\n"
        )
        prim_hash = _snippet(store, primary, None)  # no host path
        _code_bundle(store, tid, prim_hash, [dep_hash])

        ex = LocalExecutor(timeout=30, sandbox="minimal", trace_reads="off")
        payload = await _run(store, tmp_path, ex)
        assert payload["status"] == "completed", payload
        out = json.loads(
            store.get_trial(tid).executor_output_json
        )
        assert out["sealed_overlays"] == 1  # dep only (primary has no path)
        stdout = json.loads(out["stdout"].strip().splitlines()[-1])
        assert stdout["val"] == sealed
