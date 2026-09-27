"""Sealed-path runtime deny — sealed_path_patterns enforced inside the
trial's mount namespace (plan-20260926-1950Z).

Unit tests need no bwrap; the sandbox integration battery is skipped
when bubblewrap is absent (same convention as test_sandbox.py).
"""
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from ml_episteme_mcp.clients.local_executor import LocalExecutor
from ml_episteme_mcp.sealed_paths import resolve_sealed_denies, sealed_match
from ml_episteme_mcp.state.models import (
    Hypothesis, Programme, ProgrammeStatus, Trial, TrialStatus,
)
from ml_episteme_mcp.state.store import StateStore
from ml_episteme_mcp.integrity import checks as ichecks

HAS_BWRAP = shutil.which("bwrap") is not None


@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "state.db")
    s.connect()
    yield s
    s.close()


@pytest.fixture
def artifact_dir(tmp_path):
    d = tmp_path / "artifacts"
    d.mkdir()
    return d


def _sha(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


def _seed_trial(store, trial_id="trial-s1", status=TrialStatus.completed):
    store.create_programme(Programme(
        id="prog-s", goal="g", constraints={}, allowed_variables=["x"],
        budget_max_trials=10, budget_max_wall_time_hours=1.0,
        status=ProgrammeStatus.active,
    ))
    store.create_hypothesis(Hypothesis(
        id="hyp-s", programme_id="prog-s",
        statement="s", failure_criterion="f", variables_involved=["x"],
    ))
    store.create_trial(Trial(
        id=trial_id, programme_id="prog-s", hypothesis_id="hyp-s",
        config_json="{}", status=TrialStatus.designed,
    ))
    store.update_trial_status(trial_id, "running")
    store.update_trial_executor_output(
        trial_id, json.dumps({"status": "completed",
                              "stdout": "{}", "exit_code": 0}))
    store.update_trial_status(trial_id, status.value)
    return trial_id


def _attach_manifest_blob(store, trial_id, manifest: dict):
    content = json.dumps(manifest).encode()
    digest = _sha(content)
    store.create_artifact_file(
        content_hash=digest, filename="executed_code.json",
        content=content, content_type="application/json",
        captured_at="2026-09-26T00:00:00+00:00", original_path="/x",
    )
    store.create_trial_artifact(
        ta_id=f"ta-{trial_id}", trial_id=trial_id,
        content_hash=digest, filename="executed_code.json",
        artifact_type="other",
        created_at="2026-09-26T00:00:00+00:00",
    )


# ---- shared matcher -------------------------------------------------


def test_sealed_match_fnmatch_star_crosses_sep():
    """The R1 regression pin: fnmatch '*' crosses '/' — the armed set
    and the classified set must be the same predicate."""
    assert sealed_match("/x/a/b/holdout.csv", ["/x/*/holdout*"])
    assert sealed_match("/x/holdout.csv", ["/x/*/holdout*"]) is False


# ---- resolver (D1) --------------------------------------------------


def test_resolve_literal_file_and_dir(tmp_path):
    f = tmp_path / "holdout.csv"
    f.write_text("secret")
    d = tmp_path / "holddir"
    d.mkdir()
    denies, unmatched, err = resolve_sealed_denies([str(f), str(d)])
    assert err is None
    assert (str(f), "file") in denies
    assert (str(d), "dir") in denies
    assert unmatched == []


def test_resolve_wildcard_arms_nested_matches(tmp_path):
    """fnmatch '*' crosses '/' — a nested holdout must be armed, not
    merely classified. This is the hole the shared matcher closes."""
    (tmp_path / "prog").mkdir()
    (tmp_path / "prog" / "holdout.csv").write_text("x")
    (tmp_path / "prog" / "sub").mkdir()
    (tmp_path / "prog" / "sub" / "holdout.csv").write_text("x")
    denies, unmatched, err = resolve_sealed_denies(
        [str(tmp_path / "*" / "holdout*")]
    )
    assert err is None and unmatched == []
    armed = {p for p, _ in denies}
    assert str(tmp_path / "prog" / "holdout.csv") in armed
    assert str(tmp_path / "prog" / "sub" / "holdout.csv") in armed


def test_resolve_arms_symlink_and_resolved_forms(tmp_path):
    real = tmp_path / "real.csv"
    real.write_text("x")
    link = tmp_path / "link.csv"
    link.symlink_to(real)
    denies, _, err = resolve_sealed_denies([str(tmp_path / "*.csv")])
    assert err is None
    armed = {p for p, _ in denies}
    assert str(link) in armed and str(real) in armed


def test_resolve_unmatched_and_relative(tmp_path):
    denies, unmatched, err = resolve_sealed_denies(
        [str(tmp_path / "absent.csv"), "relative*", str(tmp_path / "x*")]
    )
    assert err is None
    assert denies == []
    assert set(unmatched) == {
        str(tmp_path / "absent.csv"), "relative*", str(tmp_path / "x*")
    }


def test_resolve_refuses_critical_and_unbounded(tmp_path):
    # Literal critical path
    _, _, err = resolve_sealed_denies(["/usr"])
    assert err and "refusing" in err
    # Wildcard whose literal prefix is '/'
    _, _, err = resolve_sealed_denies(["/*/holdout*"])
    assert err and "refusing" in err
    # Wildcard rooted inside a critical prefix
    _, _, err = resolve_sealed_denies(["/etc/*/x"])
    assert err and "refusing" in err


def test_resolve_enumeration_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "ml_episteme_mcp.sealed_paths._ENUMERATION_LIMIT", 3
    )
    for i in range(5):
        (tmp_path / f"f{i}.csv").write_text("x")
    _, _, err = resolve_sealed_denies([str(tmp_path / "*.csv")])
    assert err and "enumeration exceeded" in err


# ---- argv integration (D2) ------------------------------------------


def test_sandbox_argv_denies_applied_last(tmp_path):
    """Deny ops shadow every earlier bind — including the code-seal
    overlays and the artifact-dir rw bind."""
    ex = LocalExecutor(sandbox="full")
    ex._has_bwrap = True
    ex._sandbox = "full"
    ad = tmp_path / "art"
    staged = tmp_path / "staged_code.py"
    staged.write_text("x = 1\n")  # overlay sources must exist to bind
    argv = ex._sandbox_argv(
        ["python", "/w.py"], ad, tmp_path / "w.py",
        overlay_ro=[(str(staged), "/live/code.py")],
        deny_paths=[("/h/dir", "dir"), ("/h/file.csv", "file")],
        deny_fd=7,
    )
    dd = argv.index("--tmpfs", argv.index("/live/code.py"))
    df = argv.index("--bind-data")
    assert argv[dd + 1] == "/h/dir"
    assert argv[df - 1] == "000" and argv[df + 1] == "7"
    assert argv[df + 2] == "/h/file.csv"
    # deny ops come after the artifact-dir bind + chdir
    assert dd > argv.index("--chdir")
    assert df > argv.index("--chdir")


async def test_sandbox_none_plus_patterns_refuses(artifact_dir):
    """deny + no namespace → fail closed, never run unenforced."""
    ex = LocalExecutor(
        sandbox="none", sealed_path_patterns=["/x/holdout*"],
    )
    r = json.loads(await ex.execute_code("print(1)", artifact_dir=artifact_dir))
    assert r["status"] == "failed"
    assert "sealed_path_patterns" in r["error"]
    assert r["sealed_enforcement"] == "deny"
    assert r["seal_enforced"] is False


async def test_audit_mode_allows_sandbox_none(artifact_dir):
    """sealed_enforcement='audit' is the explicit trace-only opt-down."""
    ex = LocalExecutor(
        sandbox="none", sealed_path_patterns=["/x/holdout*"],
        sealed_enforcement="audit",
    )
    r = json.loads(await ex.execute_code("print(1)", artifact_dir=artifact_dir))
    assert r["status"] == "completed"
    assert r["sealed_enforcement"] == "audit"


async def test_no_patterns_reports_none(artifact_dir):
    ex = LocalExecutor(sandbox="none")
    r = json.loads(await ex.execute_code("print(1)", artifact_dir=artifact_dir))
    assert r["sealed_enforcement"] == "none"
    assert r["sealed_denies"] == []


async def test_conflict_with_artifact_dir_refuses(artifact_dir):
    """A deny path that is an ancestor of a required mount refuses at
    launch with the pair named — never a mid-flight EACCES."""
    ex = LocalExecutor(
        sandbox="full",
        sealed_path_patterns=[str(artifact_dir.parent / "*")],
    )
    if not HAS_BWRAP:
        pytest.skip("bubblewrap not installed")
    r = json.loads(await ex.execute_code("print(1)", artifact_dir=artifact_dir))
    assert r["status"] == "failed"
    assert "conflicts" in r["error"]
    assert str(artifact_dir) in r["error"]


# ---- capture + check (D3/D4) ----------------------------------------


def test_capture_records_denied_attempt(store, tmp_path):
    """A failed open on a sealed path lands in the manifest as
    role=sealed + denied — the attempt is evidence, not noise."""
    _seed_trial(store)
    art = tmp_path / "art"
    art.mkdir()
    holdout = tmp_path / "holdout.csv"
    trace = art / "t1_readtrace.strace"
    trace.write_text(
        f'123 openat(AT_FDCWD, "{holdout}", O_RDONLY|O_CLOEXEC)'
        " = -1 EACCES (Permission denied)\n"
        f'124 openat(AT_FDCWD, "{tmp_path}/noise.txt", O_RDONLY)'
        " = -1 ENOENT (No such file or directory)\n"
    )
    result = store.capture_executed_code(
        "t1", art, sealed_patterns=[str(tmp_path / "holdout*")],
    )
    sealed = [f for f in result["captured"] if f["role"] == "sealed"]
    assert len(sealed) == 1
    assert sealed[0]["path"] == str(holdout)
    assert sealed[0]["denied"] is True
    assert "EACCES" in sealed[0]["reason"]
    # failed open on a non-sealed path is not recorded
    assert not any("noise" in f["path"] for f in result["captured"])


def test_capture_audit_mode_exclusion_unchanged(store, tmp_path):
    """A SUCCESSFUL open on a sealed path (audit mode) keeps the
    'excluded by policy' row — denied stays absent."""
    _seed_trial(store)
    art = tmp_path / "art"
    art.mkdir()
    holdout = tmp_path / "holdout.csv"
    holdout.write_text("x")
    trace = art / "t1_readtrace.strace"
    trace.write_text(
        f'123 openat(AT_FDCWD, "{holdout}", O_RDONLY) = 3\n'
    )
    result = store.capture_executed_code(
        "t1", art, sealed_patterns=[str(tmp_path / "holdout*")],
    )
    sealed = [f for f in result["captured"] if f["role"] == "sealed"]
    assert len(sealed) == 1
    assert "denied" not in sealed[0]
    assert sealed[0]["reason"] == "excluded by policy"


def test_sealed_access_attempts_check(store, tmp_path):
    _seed_trial(store)
    _attach_manifest_blob(store, "trial-s1", {
        "schema_version": 2,
        "files": [
            {"path": "/x/holdout.csv", "role": "sealed",
             "sha256": None, "denied": True,
             "reason": "denied at runtime (EACCES)"},
        ],
    })
    res = ichecks._check_sealed_access_attempts(
        store, sealed_patterns=["/x/*"])
    assert res["ok"] is False
    assert res["violations"][0]["trial_id"] == "trial-s1"


def test_sealed_access_attempts_clean(store, tmp_path):
    _seed_trial(store)
    _attach_manifest_blob(store, "trial-s1", {
        "schema_version": 2,
        "files": [
            {"path": "/x/train.csv", "role": "input_data",
             "sha256": "sha256:" + "a" * 64, "size_bytes": 3},
        ],
    })
    res = ichecks._check_sealed_access_attempts(
        store, sealed_patterns=["/x/*"])
    assert res["ok"] is True and res["violations"] == []


def test_sealed_access_attempts_unconfigured_is_skipped(store):
    """No deny-list configured → 'skipped', not a vacuous green: an
    ok verdict would be indistinguishable from an armed list that
    held (rc-5 A4)."""
    res = ichecks._check_sealed_access_attempts(store)
    assert res["ok"] is True
    assert res["violations"] == []
    assert "skipped" in res["detail"]
    assert "no sealed_path_patterns" in res["detail"]


# ---- rc-5 A3: deleted sealed code refuses launch -------------------


@pytest.mark.skipif(not HAS_BWRAP, reason="bubblewrap not installed")
async def test_deleted_overlay_target_refuses_launch(
    artifact_dir, tmp_path
):
    """A sealed code path deleted after capture → a NAMED launch
    refusal, not an opaque bwrap mount failure deep in the child —
    'sealed bytes or no run' is the seal's guarantee."""
    ex = LocalExecutor(sandbox="minimal")
    staged = tmp_path / "staged.py"
    staged.write_text("x = 1\n")
    missing = tmp_path / "gone.py"  # the deleted host path
    r = json.loads(await ex.execute_code(
        "print(1)", artifact_dir=artifact_dir,
        overlay_ro=[(str(staged), str(missing))],
    ))
    assert r["status"] == "failed"
    assert r["launch_refused"] is True
    assert str(missing) in r["error"]
    # Truthful fields: nothing ran — the seal was never enforced on
    # an execution, so seal_enforced must not claim otherwise.
    assert r["seal_enforced"] is False


def test_refused_launch_not_unsealed_execution(store):
    """A refused launch never ran — flagging it as 'unsealed
    execution' would be a false violation; the refusal record is its
    own evidence."""
    _seed_trial(store, status=TrialStatus.failed)
    store.update_trial_executor_output("trial-s1", json.dumps({
        "status": "failed", "sandbox": "minimal",
        "seal_enforced": False, "launch_refused": True,
        "error": "sealed code path(s) missing on host",
    }))
    res = ichecks._check_unsealed_execution(store)
    assert res["ok"] is True
    assert res["violations"] == []


# ---- live sandbox battery (needs bwrap) ------------------------------


@pytest.mark.skipif(not HAS_BWRAP, reason="bubblewrap not installed")
class TestRuntimeDeny:
    async def test_file_deny_eacces(self, artifact_dir):
        """A denied file inside a writable parent: open → EACCES and
        the mountpoint cannot be removed (EBUSY)."""
        holdout = artifact_dir / "holdout.csv"
        holdout.write_text("SECRET")
        code = (
            "import os\n"
            f'p = {str(holdout)!r}\n'
            "try:\n"
            "    open(p).read()\n"
            "    print('LEAKED')\n"
            "except OSError as e:\n"
            "    print('DENIED', e.errno)\n"
            "os.system(f'rm -f {p} 2>/dev/null')\n"
            "try:\n"
            "    open(p).read()\n"
            "    print('LEAKED-AFTER-RM')\n"
            "except OSError:\n"
            "    print('STILL-DENIED')\n"
        )
        ex = LocalExecutor(
            timeout=30, sandbox="full",
            sealed_path_patterns=[str(holdout)],
        )
        r = json.loads(
            await ex.execute_code(code, artifact_dir=artifact_dir)
        )
        assert r["status"] == "completed", r
        assert "LEAKED" not in r["stdout"]
        assert "DENIED 13" in r["stdout"]  # EACCES
        assert "STILL-DENIED" in r["stdout"]
        assert str(holdout) in r["sealed_denies"]
        assert r["sealed_enforcement"] == "deny"

    async def test_dir_deny_enoent(self, artifact_dir):
        holddir = artifact_dir / "holdout_data"
        holddir.mkdir()
        (holddir / "x.csv").write_text("SECRET")
        code = (
            "import os\n"
            f'p = {str(holddir / "x.csv")!r}\n'
            "try:\n"
            "    open(p).read()\n"
            "    print('LEAKED')\n"
            "except FileNotFoundError:\n"
            "    print('DENIED-ENOENT')\n"
            "except OSError as e:\n"
            "    print('DENIED-OTHER', e.errno)\n"
        )
        ex = LocalExecutor(
            timeout=30, sandbox="full",
            sealed_path_patterns=[str(holddir)],
        )
        r = json.loads(
            await ex.execute_code(code, artifact_dir=artifact_dir)
        )
        assert r["status"] == "completed", r
        assert "LEAKED" not in r["stdout"]
        assert "DENIED-ENOENT" in r["stdout"]

    async def test_permitted_path_still_readable(self, artifact_dir):
        """A path NOT matching the pattern stays readable — the deny
        is a deny-list, not an allow-list."""
        allow = artifact_dir / "train.csv"
        allow.write_text("ok-data")
        code = (
            f'print("READ:", open({str(allow)!r}).read().strip())'
        )
        ex = LocalExecutor(
            timeout=30, sandbox="full",
            sealed_path_patterns=[str(artifact_dir / "holdout*")],
        )
        r = json.loads(
            await ex.execute_code(code, artifact_dir=artifact_dir)
        )
        assert r["status"] == "completed", r
        assert "READ: ok-data" in r["stdout"]

    async def test_minimal_mode_deny_survives_rm(self, artifact_dir):
        """minimal's writable root cannot bypass a mountpoint deny."""
        holdout = artifact_dir / "holdout.csv"
        holdout.write_text("SECRET")
        code = (
            "import os\n"
            f'p = {str(holdout)!r}\n'
            "os.system(f'rm -f {p} 2>/dev/null')\n"
            "try:\n"
            "    open(p).read()\n"
            "    print('LEAKED')\n"
            "except OSError:\n"
            "    print('DENIED')\n"
        )
        ex = LocalExecutor(
            timeout=30, sandbox="minimal",
            sealed_path_patterns=[str(holdout)],
        )
        r = json.loads(
            await ex.execute_code(code, artifact_dir=artifact_dir)
        )
        assert r["status"] == "completed", r
        assert "DENIED" in r["stdout"]
        assert "LEAKED" not in r["stdout"]
