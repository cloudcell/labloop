"""Execution-time code capture — strace read-trace of what the trial
actually opened, stored as evidence (distinct from the sealed bundle's
static closure).
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

from ml_episteme_mcp.clients.local_executor import LocalExecutor
from ml_episteme_mcp.state.store import StateStore

pytestmark = pytest.mark.skipif(
    shutil.which("strace") is None, reason="strace not installed"
)


@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "test.db")
    s.connect()
    yield s
    s.close()


@pytest.fixture
def artifact_dir(tmp_path):
    d = tmp_path / "artifacts"
    d.mkdir()
    return d


async def test_strace_log_written_and_result_marks_trace(artifact_dir):
    ex = LocalExecutor(timeout=30, sandbox="none", trace_reads="on")
    r = json.loads(await ex.execute_code(
        "print('hi')", artifact_dir=artifact_dir, trial_id="trial-t1"
    ))
    assert r["status"] == "completed", r
    assert r["read_trace"] == "strace"
    logs = list(artifact_dir.glob("*_readtrace.strace"))
    assert len(logs) == 1
    assert "openat" in logs[0].read_text()


async def test_trace_reads_off(artifact_dir):
    ex = LocalExecutor(timeout=30, sandbox="none", trace_reads="off")
    r = json.loads(await ex.execute_code(
        "print('hi')", artifact_dir=artifact_dir, trial_id="trial-t2"
    ))
    assert r["status"] == "completed", r
    assert r["read_trace"] == "none"
    assert not list(artifact_dir.glob("*_readtrace.strace"))


async def test_traced_run_captures_spawned_py(store, artifact_dir, tmp_path):
    """A subprocess-spawned .py outside the artifact dir — invisible to
    static capture unless declared — is captured by the read-trace."""
    helper = tmp_path / "spawned_helper.py"
    helper.write_text("print('from helper')\n")
    code = (
        "import subprocess, sys\n"
        f"subprocess.run([sys.executable, '{helper}'], check=True)\n"
    )
    ex = LocalExecutor(timeout=30, sandbox="none", trace_reads="on")
    r = json.loads(await ex.execute_code(
        code, artifact_dir=artifact_dir, trial_id="trial-t3"
    ))
    assert r["status"] == "completed", r

    result = store.capture_executed_code("trial-t3", artifact_dir)
    assert result["traced"] is True
    code_entries = [c for c in result["captured"] if c["role"] == "code"]
    paths = [c["original_path"] for c in code_entries]
    assert str(helper.resolve()) in paths
    # snippet is content-addressed and retrievable
    entry = next(c for c in code_entries if "spawned_helper" in c["original_path"])
    snip = store.get_code_snippet(entry["code_hash"])
    assert snip is not None
    assert "from helper" in snip.code_text
    # manifest written into the artifact dir → captured as artifact
    manifest = json.loads((artifact_dir / "executed_code.json").read_text())
    assert manifest["traced"] is True
    code_files = [f for f in manifest["files"] if f["role"] == "code"]
    assert any("spawned_helper" in f["original_path"] for f in code_files)


def test_capture_executed_code_filters(store, tmp_path):
    """Parser keeps real code, drops stdlib/site-packages/artifact-dir
    internals and failed opens."""
    artifact = tmp_path / "art"
    artifact.mkdir()
    user_code = tmp_path / "user_code.py"
    user_code.write_text("x = 1\n")
    wrapper = artifact / "t_wrapper.py"
    wrapper.write_text("pass\n")
    sp_dir = tmp_path / "lib" / "python3.11" / "site-packages"
    sp_dir.mkdir(parents=True)
    dep = sp_dir / "torch.py"
    dep.write_text("y = 2\n")
    # A DIFFERENT interpreter's stdlib (uv-managed python in the trial
    # venv) — must be excluded even though it isn't the server's stdlib.
    uv_std = tmp_path / "uvpy" / "lib" / "python3.11" / "concurrent"
    uv_std.mkdir(parents=True)
    uv_std_mod = uv_std / "process.py"
    uv_std_mod.write_text("z = 3\n")
    missing = tmp_path / "gone.py"

    (artifact / "t_2026_readtrace.strace").write_text(
        f'100 openat(AT_FDCWD, "{user_code}", O_RDONLY|O_CLOEXEC) = 3\n'
        f'100 openat(AT_FDCWD, "{dep}", O_RDONLY|O_CLOEXEC) = 4\n'
        f'100 openat(AT_FDCWD, "{wrapper}", O_RDONLY|O_CLOEXEC) = 5\n'
        f'100 openat(AT_FDCWD, "{uv_std_mod}", O_RDONLY|O_CLOEXEC) = 6\n'
        f'100 openat(AT_FDCWD, "{missing}", O_RDONLY) = -1 ENOENT\n'
        f'101 execve("{sys.executable}", ["python", "{user_code}"], '
        '0x55 /* 3 vars */) = 0\n'
    )
    result = store.capture_executed_code("t", artifact)
    paths = [c["original_path"] for c in result["captured"]]
    assert str(user_code.resolve()) in paths
    assert not any("site-packages" in p for p in paths)
    assert not any("wrapper" in p for p in paths)
    assert not any("process.py" in p for p in paths)
    assert not any("gone" in p for p in paths)


def test_capture_executed_code_no_log(store, tmp_path):
    assert store.capture_executed_code("t", tmp_path)["traced"] is False


def test_trace_auto_degrades_without_strace(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda c: "/x" if c == "bwrap" else None)
    ex = LocalExecutor(trace_reads="auto")
    assert ex._trace == "none"
    ex2 = LocalExecutor(trace_reads="on")
    assert ex2._trace == "none"  # requested but unavailable — never fake


def test_stage_sealed_code(store, tmp_path):
    """run_trial stages sealed bytes for overlay: each snippet lands in
    _sealed/, hash-verified, mapped to its original path."""
    import json as _json
    from ml_episteme_mcp.tools.trial import _stage_sealed_code
    from ml_episteme_mcp.state.models import Bundle

    art = tmp_path / "art"
    art.mkdir()
    (tmp_path / "a.py").write_text("A = 1\n")
    (tmp_path / "b.py").write_text("B = 2\n")
    a = store.capture_code_from_path(str(tmp_path / "a.py"))
    b = store.capture_code_from_path(str(tmp_path / "b.py"))
    bundle = Bundle(
        id="b1", trial_id="t1", code_ref=str(tmp_path / "a.py"),
        env_ref="x", seeds_json="[1]", splits_json="{}",
        code_hash=a, code_hash_extra_json=_json.dumps([b]),
        created_at="2026-01-01T00:00:00Z",
    )
    overlays = _stage_sealed_code(store, bundle, art)
    targets = {t for _, t in overlays}
    assert targets == {str((tmp_path / "a.py").resolve()),
                       str((tmp_path / "b.py").resolve())}
    for staged, _ in overlays:
        assert staged.startswith(str(art / "_sealed"))
        assert Path(staged).exists()
