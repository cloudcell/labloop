"""Blob retrieval + input-data digests (plan-20260926-0554Z).

C1 — get_blob: verified read-back out of the content stores.
C2 — capture_executed_code schema v2: every opened file recorded with
     a role and a digest (or an explicit, auditable refusal).
C3 — produced evidence pinned into the trial manifest.
C4 — the input_data_undigested invariant check, and strace_divergence
     reading the manifest from the blob store (the staging dir is
     deleted at finalize).
"""
import gzip
import hashlib
import json
import sys
from pathlib import Path

import pytest

from ml_episteme_mcp.state.models import (
    Hypothesis, Programme, ProgrammeStatus, Trial, TrialStatus,
)
from ml_episteme_mcp.state.store import StateStore


def _sha(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "state.db")
    s.connect()
    yield s
    s.close()


def _seed_trial(store, trial_id="trial-t1", status=TrialStatus.completed):
    store.create_programme(Programme(
        id="prog-t", goal="g", constraints={}, allowed_variables=["x"],
        budget_max_trials=10, budget_max_wall_time_hours=1.0,
        status=ProgrammeStatus.active,
    ))
    store.create_hypothesis(Hypothesis(
        id="hyp-t", programme_id="prog-t",
        statement="s", failure_criterion="f", variables_involved=["x"],
    ))
    store.create_trial(Trial(
        id=trial_id, programme_id="prog-t", hypothesis_id="hyp-t",
        config_json="{}", status=TrialStatus.designed,
    ))
    if status != TrialStatus.designed:
        store.update_trial_status(trial_id, "running")
    if status == TrialStatus.completed:
        # Enforcement: completed requires an executor record.
        store.update_trial_executor_output(
            trial_id, json.dumps({"status": "completed",
                                  "stdout": "{}", "exit_code": 0}))
    if status not in (TrialStatus.designed, TrialStatus.running):
        store.update_trial_status(trial_id, status.value)
    return trial_id


def _attach_manifest_blob(store, trial_id, manifest: dict):
    """Store an executed_code.json blob + link as trial evidence —
    the shape capture_artifacts_from_dir produces."""
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


# ---- C1: get_blob ---------------------------------------------------


def test_read_blob_roundtrip(store):
    """Digest → verified bytes."""
    payload = b"print('sealed code')\n" * 50
    digest = _sha(payload)
    store.create_artifact_file(
        content_hash=digest, filename="run.py", content=payload,
        content_type="text/x-python",
        captured_at="2026-09-26T00:00:00+00:00", original_path="/x/run.py",
    )
    r = store.read_blob(digest)
    assert r["ok"] is True
    assert r["content"] == payload
    assert r["served_from"] == "artifact_files"
    assert _sha(r["content"]) == digest


def test_read_blob_snippet(store, tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("VALUE = 42\n")
    digest = store.capture_code_from_path(str(f))
    r = store.read_blob(digest)
    assert r["ok"] is True
    assert r["content"] == b"VALUE = 42\n"
    assert r["served_from"] == "code_snippets"


def test_read_blob_not_found(store):
    """NEGATIVE: well-formed absent digest → not_found, never bytes."""
    absent = "sha256:" + "a" * 64
    r = store.read_blob(absent)
    assert r["ok"] is False
    assert r["error"] == "not_found"


def test_read_blob_malformed(store):
    r = store.read_blob("sha256:ZZZ")
    assert r["ok"] is False
    assert r["error"] == "malformed_digest"


def test_read_blob_digest_mismatch(store):
    """NEGATIVE: stored bytes that don't match their key are refused —
    the computed digest is reported, bytes withheld."""
    digest = _sha(b"correct bytes")
    store.create_artifact_file(
        content_hash=digest, filename="evil.bin",
        content=b"WRONG BYTES", content_type="application/octet-stream",
        captured_at="2026-09-26T00:00:00+00:00", original_path="/x",
    )
    r = store.read_blob(digest)
    assert r["ok"] is False
    assert r["error"] == "digest_mismatch"
    assert r["computed_digest"] == _sha(b"WRONG BYTES")
    assert "content" not in r


async def test_get_blob_tool(store, tmp_path):
    """The tool surface: round-trip + too_large bound."""
    from ml_episteme_mcp.server import create_server
    import base64

    mcp = create_server(
        store, enforcement_config={"recurrent_protocol": False})
    payload = b"hello sealed world"
    digest = _sha(payload)
    store.create_artifact_file(
        content_hash=digest, filename="x.bin", content=payload,
        content_type="application/octet-stream",
        captured_at="2026-09-26T00:00:00+00:00", original_path="/x",
    )

    r = await mcp.call_tool("get_blob", {"content_hash": digest})
    out = json.loads(r.content[0].text)
    assert base64.b64decode(out["content_b64"]) == payload
    assert out["served_from"] == "artifact_files"

    # over the bound → too_large with metadata, no bytes
    r = await mcp.call_tool(
        "get_blob", {"content_hash": digest, "max_bytes": 4})
    out = json.loads(r.content[0].text)
    assert out["error"] == "too_large"
    assert "content_b64" not in out

    # absent digest → error, not empty bytes, not exit 0
    r = await mcp.call_tool("get_blob", {"content_hash": "sha256:" + "b" * 64})
    out = json.loads(r.content[0].text)
    assert out["error"] == "not_found"


# ---- C2/C3: capture_executed_code v2 + manifest pinning ------------


def _fake_trace(artifact: Path, lines: list[str]) -> Path:
    log = artifact / "t1_2026_readtrace.strace"
    log.write_text("\n".join(lines) + "\n")
    return log


def test_capture_records_input_data(store, tmp_path):
    """A trial that reads a data file: the manifest carries it with a
    real digest and role=input_data — 'it was opened' is not enough."""
    art = tmp_path / "art"
    art.mkdir()
    code = tmp_path / "train.py"
    code.write_text("import json\n")
    data = tmp_path / "exchange" / "cap-timing.json"
    data.parent.mkdir()
    data.write_text('{"cap": 1}\n')
    _fake_trace(art, [
        f'10 openat(AT_FDCWD, "{code}", O_RDONLY|O_CLOEXEC) = 3',
        f'10 openat(AT_FDCWD, "{data}", O_RDONLY|O_CLOEXEC) = 4',
        f'10 execve("{sys.executable}", ["python", "{code}"], 0x0) = 0',
    ])

    result = store.capture_executed_code("t1", art)
    files = {f["path"]: f for f in result["captured"]}

    entry = files[str(data.resolve())]
    assert entry["role"] == "input_data"
    assert entry["sha256"] == _sha(data.read_bytes())
    assert entry["sha256"].startswith("sha256:")
    assert files[str(code.resolve())]["role"] == "code"

    manifest = json.loads((art / "executed_code.json").read_text())
    assert manifest["schema_version"] == 2
    mentry = next(
        f for f in manifest["files"] if f["path"] == str(data.resolve()))
    assert mentry["sha256"] == entry["sha256"]


def test_capture_sealed_path_never_hashed(store, tmp_path):
    """NEGATIVE: a deny-listed path records role=sealed, sha256=null,
    reason — and its digest is never computed or stored anywhere."""
    art = tmp_path / "art"
    art.mkdir()
    sealed = tmp_path / "holdout" / "secret.json"
    sealed.parent.mkdir()
    secret = b'{"labels": [0,1,0]}\n'
    sealed.write_bytes(secret)
    secret_digest = _sha(secret)
    _fake_trace(art, [
        f'10 openat(AT_FDCWD, "{sealed}", O_RDONLY|O_CLOEXEC) = 3',
    ])

    result = store.capture_executed_code(
        "t1", art, sealed_patterns=[f"{tmp_path}/holdout/*"])
    entry = result["captured"][0]
    assert entry["role"] == "sealed"
    assert entry["sha256"] is None
    assert "policy" in entry["reason"]

    # The refusal is auditable and the digest provably absent —
    # count, don't assume: the secret's hash appears nowhere.
    manifest_text = (art / "executed_code.json").read_text()
    assert secret_digest not in manifest_text
    rows = store._fetchall(
        "SELECT content_hash FROM artifact_files WHERE content_hash = ?",
        (secret_digest,))
    assert rows == []
    rows = store._fetchall(
        "SELECT code_hash FROM code_snippets WHERE code_hash = ?",
        (secret_digest,))
    assert rows == []


def test_capture_vanished_input_recorded(store, tmp_path):
    """A file read mid-trial then removed: digest null + reason —
    honest gap, exactly what the invariant flags."""
    art = tmp_path / "art"
    art.mkdir()
    gone = tmp_path / "tmp-input.json"  # never created
    _fake_trace(art, [
        f'10 openat(AT_FDCWD, "{gone}", O_RDONLY|O_CLOEXEC) = 3',
    ])
    result = store.capture_executed_code("t1", art)
    entry = result["captured"][0]
    assert entry["role"] == "input_data"
    assert entry["sha256"] is None
    assert entry["reason"]


def test_capture_write_opened_is_other(store, tmp_path):
    art = tmp_path / "art"
    art.mkdir()
    out = tmp_path / "output.csv"
    out.write_text("a,b\n1,2\n")
    _fake_trace(art, [
        f'10 openat(AT_FDCWD, "{out}", O_WRONLY|O_CREAT|O_TRUNC) = 3',
    ])
    result = store.capture_executed_code("t1", art)
    entry = result["captured"][0]
    assert entry["role"] == "other"
    assert entry["sha256"] == _sha(out.read_bytes())


def test_manifest_pinned(store, tmp_path):
    """Every produced artifact is pinned: wrapper/stdout/stderr/
    readtrace/executed_code — count computed, never hardcoded."""
    from ml_episteme_mcp.clients.local_executor import _write_manifest

    art = tmp_path / "art"
    art.mkdir()
    code = tmp_path / "c.py"
    code.write_text("x=1\n")
    _fake_trace(art, [
        f'10 openat(AT_FDCWD, "{code}", O_RDONLY|O_CLOEXEC) = 3',
    ])
    # Pre-existing manifest as _write_artifacts produces
    manifest = art / "t1_2026_manifest.json"
    manifest.write_text(json.dumps({
        "trial_id": "t1", "artifacts": [
            {"filename": "t1_w.py", "sha256": _sha(b"w")},
        ],
    }))
    store.capture_executed_code("t1", art)

    amended = json.loads(manifest.read_text())
    pinned = {a["filename"] for a in amended["artifacts"]}
    produced = {
        "t1_w.py",
        "t1_2026_readtrace.strace",
        "executed_code.json",
    }
    missing = produced - pinned
    assert missing == set(), f"unpinned artifacts: {missing}"
    # every pinned artifact carries a real digest
    assert all(
        (a.get("sha256") or "").startswith("sha256:")
        for a in amended["artifacts"]
    ), "a pinned artifact lacks a digest with no reason"


# ---- C4: the invariant check + migrated divergence ------------------


def test_input_data_undigested_fires(store):
    """NEGATIVE: a v2 manifest with an undigested input_data file must
    trip the check — prove it fires before trusting it."""
    from ml_episteme_mcp.integrity.checks import run_checks

    tid = _seed_trial(store)
    _attach_manifest_blob(store, tid, {
        "schema_version": 2,
        "files": [
            {"path": "/exchange/data.json", "role": "input_data",
             "sha256": None, "reason": "not readable at finalize"},
        ],
    })
    res = run_checks(store)
    chk = next(c for c in res["checks"]
               if c["name"] == "input_data_undigested")
    assert chk["ok"] is False
    assert chk["violations"], "check examined nothing — vacuous pass"
    assert any(v["trial_id"] == tid for v in chk["violations"])


def test_input_data_digested_clean(store):
    from ml_episteme_mcp.integrity.checks import run_checks

    tid = _seed_trial(store)
    _attach_manifest_blob(store, tid, {
        "schema_version": 2,
        "files": [
            {"path": "/exchange/data.json", "role": "input_data",
             "sha256": "sha256:" + "c" * 64, "size_bytes": 10},
            {"path": "/holdout/secret.json", "role": "sealed",
             "sha256": None, "reason": "excluded by policy"},
        ],
    })
    res = run_checks(store)
    chk = next(c for c in res["checks"]
               if c["name"] == "input_data_undigested")
    assert chk["ok"] is True


def test_input_data_v1_manifest_unrecorded(store):
    """Pre-v2 manifests are unrecorded provenance gaps — reported in
    detail, never flagged (a digest not taken isn't reconstructed)."""
    from ml_episteme_mcp.integrity.checks import run_checks

    tid = _seed_trial(store)
    _attach_manifest_blob(store, tid, {
        "trial_id": tid, "traced": True, "files": [
            {"original_path": "/x/y.py", "code_hash": "sha256:" + "d" * 64},
        ],
    })
    res = run_checks(store)
    chk = next(c for c in res["checks"]
               if c["name"] == "input_data_undigested")
    assert chk["ok"] is True
    assert "unrecorded" in chk["detail"]


def test_strace_divergence_reads_blob(store):
    """The divergence check works off the stored manifest — the
    staging dir is deleted at finalize and must not be needed."""
    from ml_episteme_mcp.integrity.checks import run_checks

    tid = _seed_trial(store)
    sealed_hash = "sha256:" + "e" * 64
    escaped_hash = "sha256:" + "f" * 64
    # bundle seals sealed_hash; manifest shows an extra file ran
    store.conn.execute(
        "INSERT INTO bundles (id, trial_id, code_ref, code_hash, "
        "env_ref, seeds_json, splits_json, created_at) "
        "VALUES ('b1', ?, '/x/m.py', ?, 'e', '[1]', '{}', '2026-01-01')",
        (tid, sealed_hash))
    _attach_manifest_blob(store, tid, {
        "schema_version": 2,
        "files": [
            {"path": "/x/m.py", "original_path": "/x/m.py",
             "code_hash": sealed_hash, "role": "code"},
            {"path": "/x/escape.py", "original_path": "/x/escape.py",
             "code_hash": escaped_hash, "role": "code"},
        ],
    })
    res = run_checks(store)
    chk = next(c for c in res["checks"]
               if c["name"] == "strace_divergence")
    assert chk["ok"] is False
    assert any("/x/escape.py" in v["paths"] for v in chk["violations"])


def test_strace_divergence_ignores_interpreter_files(store):
    """Interpreter/environment files (sitecustomize, stdlib) are env,
    not experiment code — they can never be bundle members and must
    not flag."""
    from ml_episteme_mcp.integrity.checks import run_checks

    tid = _seed_trial(store)
    sealed_hash = "sha256:" + "e" * 64
    env_hash = "sha256:" + "a" * 64
    store.conn.execute(
        "INSERT INTO bundles (id, trial_id, code_ref, code_hash, "
        "env_ref, seeds_json, splits_json, created_at) "
        "VALUES ('b1', ?, '/x/m.py', ?, 'e', '[1]', '{}', '2026-01-01')",
        (tid, sealed_hash))
    _attach_manifest_blob(store, tid, {
        "files": [
            {"original_path": "/x/m.py", "code_hash": sealed_hash},
            {"original_path": "/etc/python3.12/sitecustomize.py",
             "code_hash": env_hash},
            {"original_path":
             "/opt/uv/python/cpython-3.11.15/lib/"
             "python3.11/multiprocessing/spawn.py",
             "code_hash": env_hash},
        ],
    })
    res = run_checks(store)
    chk = next(c for c in res["checks"]
               if c["name"] == "strace_divergence")
    assert chk["ok"] is True
    assert chk["violations"] == []
