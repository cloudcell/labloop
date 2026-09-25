"""Sandbox tests — bubblewrap isolation of trial subprocesses.

minimal: private /tmp per trial (hardcoded shared-scratch paths
become harmless). full: read-only root — the trial can only durably
write inside its artifact dir. When bwrap is absent, modes that need
it FAIL the run ("missing") — isolation is never silently degraded;
'none' is the explicit opt-out.
"""
import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from ml_episteme_mcp.clients.local_executor import LocalExecutor

pytestmark = pytest.mark.skipif(
    shutil.which("bwrap") is None, reason="bubblewrap not installed"
)

_PROBE = "/tmp/ml_sci_sandbox_probe.txt"


@pytest.fixture
def artifact_dir(tmp_path):
    d = tmp_path / "artifacts"
    d.mkdir()
    return d


async def test_minimal_tmp_is_private(artifact_dir):
    """A hardcoded /tmp write inside a minimal sandbox does not reach
    the host /tmp — each trial sees its own private tmpfs."""
    code = (
        f'open("{_PROBE}", "w").write("x")\n'
        f'print(json.dumps({{"exists": os.path.exists("{_PROBE}")}}))'
    )
    code = "import json, os\n" + code
    ex = LocalExecutor(timeout=30, sandbox="minimal")
    try:
        r = json.loads(await ex.execute_code(code, artifact_dir=artifact_dir))
        assert r["status"] == "completed", r
        assert r["sandbox"] == "minimal"
        assert json.loads(r["stdout"])["exists"] is True
        # private tmpfs — nothing leaked to the host
        assert not os.path.exists(_PROBE)
    finally:
        if os.path.exists(_PROBE):
            os.unlink(_PROBE)


async def test_none_mode_writes_to_host_tmp(artifact_dir):
    """sandbox="none" provides no isolation (control case)."""
    code = f'open("{_PROBE}", "w").write("x")\nprint("ok")'
    ex = LocalExecutor(timeout=30, sandbox="none")
    try:
        r = json.loads(await ex.execute_code(code, artifact_dir=artifact_dir))
        assert r["status"] == "completed", r
        assert r["sandbox"] == "none"
        assert os.path.exists(_PROBE)
    finally:
        if os.path.exists(_PROBE):
            os.unlink(_PROBE)


async def test_full_blocks_writes_outside_artifact_dir(artifact_dir):
    """Under the read-only-root policy, only the artifact dir (and the
    private /tmp) is writable — a write to $HOME is refused."""
    code = (
        "import json, os\n"
        "out = {}\n"
        "try:\n"
        "    open(os.path.expanduser('~/ml_sci_escape.txt'), 'w')\n"
        "    out['home'] = 'allowed'\n"
        "except OSError:\n"
        "    out['home'] = 'blocked'\n"
        "open('inner.txt', 'w')\n"
        "out['artifact'] = 'allowed'\n"
        "print(json.dumps(out))\n"
    )
    ex = LocalExecutor(timeout=30, sandbox="full")
    r = json.loads(await ex.execute_code(code, artifact_dir=artifact_dir))
    assert r["status"] == "completed", r
    assert r["sandbox"] == "full"
    out = json.loads(r["stdout"])
    assert out["home"] == "blocked"
    assert out["artifact"] == "allowed"
    assert (artifact_dir / "inner.txt").exists()
    assert not os.path.exists(os.path.expanduser("~/ml_sci_escape.txt"))


async def test_full_shares_existing_host_caches(artifact_dir, tmp_path, monkeypatch):
    """Under full, an existing host cache dir (e.g. HF_HOME) is
    bind-mounted rw at its real path — not redirected to a cold
    per-trial dir that would re-download multi-GB models every run."""
    hf = tmp_path / "hf_cache"
    hf.mkdir()
    monkeypatch.setenv("HF_HOME", str(hf))
    ex = LocalExecutor(timeout=30, sandbox="full")
    r = json.loads(await ex.execute_code(
        "import json, os\n"
        "print(json.dumps({'hf': os.environ.get('HF_HOME')}))",
        artifact_dir=artifact_dir,
    ))
    assert r["status"] == "completed", r
    assert json.loads(r["stdout"])["hf"] == str(hf)
    # the host cache is bound rw at its real path (via extra_rw_paths)
    argv = ex._sandbox_argv(["python", "/x/w.py"], artifact_dir,
                            Path("/x/w.py"), extra_rw_paths=[str(hf)])
    i = [j for j, a in enumerate(argv) if a == "--bind"]
    assert any(argv[k + 1] == str(hf) == argv[k + 2] for k in i)


async def test_full_redirects_missing_host_cache(artifact_dir, tmp_path, monkeypatch):
    """No host cache to share → per-trial redirect keeps a writable
    location under the read-only root."""
    missing = tmp_path / "no_such_cache"
    monkeypatch.setenv("HF_HOME", str(missing))
    ex = LocalExecutor(timeout=30, sandbox="full")
    r = json.loads(await ex.execute_code(
        "import json, os\n"
        "print(json.dumps({'hf': os.environ.get('HF_HOME')}))",
        artifact_dir=artifact_dir,
    ))
    assert r["status"] == "completed", r
    assert json.loads(r["stdout"])["hf"].startswith(str(artifact_dir))


async def test_full_shares_declared_caches(artifact_dir, tmp_path):
    """[executor] shared_caches: an existing dir is bind-mounted rw at
    its real path — a write inside it reaches the host. A missing dir
    is skipped and reported, never silently created."""
    shared = tmp_path / "shared_cache"
    shared.mkdir()
    missing = tmp_path / "declared_but_absent"
    ex = LocalExecutor(
        timeout=30, sandbox="full",
        shared_caches=[str(shared), str(missing)],
    )
    r = json.loads(await ex.execute_code(
        "import json\n"
        f"open('{shared}/inside.txt', 'w')\n"
        "print(json.dumps({'wrote': True}))",
        artifact_dir=artifact_dir,
    ))
    assert r["status"] == "completed", r
    assert json.loads(r["stdout"])["wrote"] is True
    assert (shared / "inside.txt").exists()
    sc = r["shared_caches"]
    assert str(shared) in sc["bound"]
    assert str(missing) in sc["missing"]
    assert not missing.exists()


async def test_full_declared_caches_absent_when_not_configured(
    artifact_dir,
):
    """No declared caches → the report still lists what was bound."""
    ex = LocalExecutor(timeout=30, sandbox="full")
    r = json.loads(await ex.execute_code(
        "print('ok')", artifact_dir=artifact_dir,
    ))
    assert r["status"] == "completed", r
    assert r["shared_caches"]["missing"] == []


async def test_minimal_allows_writes_outside_artifact_dir(artifact_dir):
    """minimal only privatizes /tmp — other absolute paths still work
    (it enforces the observed bug class, not the whole contract)."""
    target = Path(tempfile.gettempdir()).parent / "tmp"  # /tmp itself
    code = (
        "import json\n"
        f"p = '{target}/ml_sci_min_escape.txt'\n"
        "open(p, 'w')\n"
        "print(json.dumps({'wrote': True}))\n"
    )
    ex = LocalExecutor(timeout=30, sandbox="minimal")
    # writes to private /tmp are allowed but invisible to the host —
    # use $HOME-relative path instead to prove non-/tmp writes pass
    code = (
        "import json, os\n"
        "p = os.path.expanduser('~/ml_sci_min_escape.txt')\n"
        "open(p, 'w')\n"
        "print(json.dumps({'wrote': os.path.exists(p)}))\n"
    )
    try:
        r = json.loads(await ex.execute_code(code, artifact_dir=artifact_dir))
        assert r["status"] == "completed", r
        assert json.loads(r["stdout"])["wrote"] is True
    finally:
        p = os.path.expanduser("~/ml_sci_min_escape.txt")
        if os.path.exists(p):
            os.unlink(p)


def test_auto_resolves_by_bwrap_presence(monkeypatch):
    """auto → full when bwrap exists. Without bwrap, any mode that
    requires it resolves to 'missing' — the run FAILS rather than
    silently executing unsealed. 'none' is the only opt-out."""
    monkeypatch.setattr(shutil, "which", lambda c: "/usr/bin/bwrap")
    assert LocalExecutor(sandbox="auto")._sandbox == "full"

    monkeypatch.setattr(shutil, "which", lambda c: None)
    assert LocalExecutor(sandbox="auto")._sandbox == "missing"
    assert LocalExecutor(sandbox="minimal")._sandbox == "missing"
    assert LocalExecutor(sandbox="full")._sandbox == "missing"
    assert LocalExecutor(sandbox="none")._sandbox == "none"
    assert LocalExecutor(sandbox="bogus")._sandbox == "missing"


async def test_missing_bwrap_fails_the_run(artifact_dir, monkeypatch):
    """Requested isolation without bwrap: failed run, never a silent
    unsealed execution."""
    monkeypatch.setattr(shutil, "which", lambda c: None)
    ex = LocalExecutor(timeout=30, sandbox="auto")
    r = json.loads(await ex.execute_code(
        "print('should never run')", artifact_dir=artifact_dir
    ))
    assert r["status"] == "failed"
    assert "bubblewrap" in r["error"]
    assert r["seal_enforced"] is False


def test_sandbox_argv_structure(artifact_dir):
    """The bwrap command line: private tmpfs + artifact re-bind +
    chdir, ro-root only under full."""
    ex = LocalExecutor(sandbox="minimal")
    argv = ex._sandbox_argv(["/usr/bin/python3", "/x/w.py"], artifact_dir, Path("/x/w.py"))
    assert argv[0] == "bwrap"
    assert "--tmpfs" in argv and "/tmp" in argv
    i = argv.index("--bind")
    assert argv[i + 1] == str(artifact_dir) == argv[i + 2]
    assert "--chdir" in argv
    assert argv[-2:] == ["/usr/bin/python3", "/x/w.py"]
    # minimal ro-binds only the script file — never the whole root
    assert "--ro-bind" in argv
    i = argv.index("--ro-bind")
    assert argv[i + 1] == "/x/w.py" == argv[i + 2]
    assert ["/", "/"] != argv[i + 1 : i + 3]

    ex2 = LocalExecutor(sandbox="full")
    argv2 = ex2._sandbox_argv(["/usr/bin/python3", "/x/w.py"], artifact_dir, Path("/x/w.py"))
    assert "--ro-bind" in argv2
    assert "--dev-bind" in argv2  # /dev for GPU access

    ex3 = LocalExecutor(sandbox="none")
    assert ex3._sandbox_argv(["a", "b"], artifact_dir, Path("/x/w.py")) == ["a", "b"]


async def test_python_exe_overrides_interpreter(tmp_path):
    """execute_code(python_exe=...) runs the wrapper under that
    interpreter — this is what env_ref resolution feeds."""
    import json as _json
    import subprocess
    import sys

    # Real venv so sys.executable inside the child differs.
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(tmp_path / "v")],
        check=True,
    )
    venv_py = tmp_path / "v" / "bin" / "python"
    ex = LocalExecutor()
    out = _json.loads(await ex.execute_code(
        "import sys, json; print(json.dumps({'exe': sys.executable}))",
        python_exe=str(venv_py),
        # tmp_path lives under /tmp — shadowed by the private tmpfs;
        # the env root must be bound like run_trial does for env_ref.
        extra_ro_paths=[str(tmp_path / "v")],
    ))
    assert out["status"] == "completed", out["stderr"]
    assert out["python_exe"] == str(venv_py)
    # sys.executable in a venv reports the venv path, not the real binary
    assert _json.loads(out["stdout"])["exe"].startswith(str(tmp_path / "v"))


async def test_overlay_ro_serves_sealed_bytes(artifact_dir, tmp_path):
    """The sealed-bytes overlay: a file edited after capture still runs
    the captured content inside the sandbox — the seal is enforced,
    not just audited."""
    target = tmp_path / "mod.py"
    target.write_text("MARKER = 'tampered-after-seal'\n")
    sealed = tmp_path / "sealed_copy.py"
    sealed.write_text("MARKER = 'sealed-content'\n")
    code = (
        "import json, importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('m', '{target}')\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
        "print(json.dumps({'marker': m.MARKER}))\n"
    )
    ex = LocalExecutor(timeout=30, sandbox="minimal")
    r = json.loads(await ex.execute_code(
        code, artifact_dir=artifact_dir,
        extra_ro_paths=[str(target)],
        overlay_ro=[(str(sealed), str(target))],
    ))
    assert r["status"] == "completed", r
    assert json.loads(r["stdout"])["marker"] == "sealed-content"
    assert r["seal_enforced"] is True
    assert r["sealed_overlays"] == 1


async def test_overlay_unenforced_without_sandbox(artifact_dir, tmp_path):
    """sandbox='none' has no mount namespace — overlays can't apply;
    the result records seal_enforced=false (never faked)."""
    target = tmp_path / "mod.py"
    target.write_text("MARKER = 'live'\n")
    sealed = tmp_path / "sealed_copy.py"
    sealed.write_text("MARKER = 'sealed'\n")
    code = f"print(open('{target}').read().strip())"
    ex = LocalExecutor(timeout=30, sandbox="none")
    r = json.loads(await ex.execute_code(
        code, artifact_dir=artifact_dir,
        overlay_ro=[(str(sealed), str(target))],
    ))
    assert r["status"] == "completed", r
    assert r["seal_enforced"] is False
    assert r["stdout"].strip() == "MARKER = 'live'"
