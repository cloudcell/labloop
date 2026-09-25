"""Tests for the artifact-ingest HTTP surface and artifact→snippet promotion."""

import gzip

import pytest
from starlette.testclient import TestClient

from ml_episteme_mcp.ingest.server import create_ingest_app
from ml_episteme_mcp.state.store import StateStore


@pytest.fixture
def store(tmp_path):
    s = StateStore(str(tmp_path / "test.db"))
    s.connect()
    yield s
    s.close()


def _client(store, token="test-token", **kw):
    return TestClient(
        create_ingest_app(store, token=token, **kw), raise_server_exceptions=False
    )


AUTH = {"Authorization": "Bearer test-token"}
CODE = b"def run_training(config):\n    return {'metrics': {}, 'variance': {}}\n"


def test_post_returns_content_address(store):
    r = _client(store).post(
        "/artifacts", content=CODE, headers=AUTH,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["hash"].startswith("sha256:")
    assert body["size"] == len(CODE)


def test_post_requires_token(store):
    assert _client(store).post("/artifacts", content=CODE).status_code == 401
    assert _client(store).post(
        "/artifacts", content=CODE,
        headers={"Authorization": "Bearer wrong"},
    ).status_code == 401


def test_no_token_denies_everything(store):
    """Fail closed: an app built without a token rejects all writes."""
    assert _client(store, token=None).post(
        "/artifacts", content=CODE, headers=AUTH
    ).status_code == 401


def test_dedup_same_content_same_hash(store):
    c = _client(store)
    h1 = c.post("/artifacts", content=CODE, headers=AUTH).json()["hash"]
    h2 = c.post("/artifacts", content=CODE, headers=AUTH).json()["hash"]
    assert h1 == h2


def test_get_roundtrip(store):
    c = _client(store)
    h = c.post(
        "/artifacts", content=CODE,
        headers={**AUTH, "X-Artifact-Name": "executor.py",
                 "Content-Type": "text/x-python"},
    ).json()["hash"]
    r = c.get(f"/artifacts/{h}", headers=AUTH)
    assert r.status_code == 200
    assert r.content == CODE
    assert r.headers["x-artifact-name"] == "executor.py"


def test_head(store):
    c = _client(store)
    h = c.post("/artifacts", content=CODE, headers=AUTH).json()["hash"]
    r = c.head(f"/artifacts/{h}", headers=AUTH)
    assert r.status_code == 200
    assert int(r.headers["content-length"]) == len(CODE)


def test_get_rejects_corrupted_blob(store):
    """Stored bytes that don't match their content address → 500."""
    c = _client(store)
    h = c.post("/artifacts", content=CODE, headers=AUTH).json()["hash"]
    store._write(
        "UPDATE artifact_files SET content = ? WHERE content_hash = ?",
        (gzip.compress(b"tampered"), h),
    )
    r = c.get(f"/artifacts/{h}", headers=AUTH)
    assert r.status_code == 500
    assert "integrity" in r.json()["error"]
    # HEAD is existence/size only — no re-hash; it must not be
    # shadowed by the GET route (Starlette auto-adds HEAD to GET).
    r = c.head(f"/artifacts/{h}", headers=AUTH)
    assert r.status_code == 200
    assert int(r.headers["content-length"]) == len(CODE)


def test_get_404_and_bad_hash(store):
    c = _client(store)
    assert c.get(
        "/artifacts/sha256:" + "0" * 64, headers=AUTH
    ).status_code == 404
    # malformed hash → rejected by the sha256 regex, never reaches store
    assert c.get("/artifacts/not-a-hash", headers=AUTH).status_code == 400
    # path traversal is normalized away by the router — no route matches
    assert c.get(
        "/artifacts/../../etc/passwd", headers=AUTH
    ).status_code == 404


def test_filename_is_metadata_only(store):
    """Path components in X-Artifact-Name are stripped."""
    c = _client(store)
    h = c.post(
        "/artifacts", content=CODE,
        headers={**AUTH, "X-Artifact-Name": "/home/lab/evil.py"},
    ).json()["hash"]
    row = store.get_artifact_file(h)
    assert row["filename"] == "evil.py"


def test_size_cap(store):
    c = _client(store, max_bytes=64)
    r = c.post("/artifacts", content=b"x" * 100, headers=AUTH)
    assert r.status_code == 413


def test_promotion_artifact_to_snippet(store):
    """capture_bundle_from_code_hash's promotion path."""
    h = _client(store).post(
        "/artifacts", content=CODE, headers=AUTH
    ).json()["hash"]
    snippet = store.promote_artifact_to_snippet(h)
    assert snippet is not None
    assert snippet.code_hash == h
    assert snippet.code_text == CODE.decode("utf-8")
    assert snippet.original_path is None
    # Idempotent — second promotion returns the same row
    assert store.promote_artifact_to_snippet(h).code_hash == h


def test_promotion_binary_raises(store):
    h = _client(store).post(
        "/artifacts", content=b"\x89PNG\r\n\x1a\n\xff\xfe", headers=AUTH
    ).json()["hash"]
    with pytest.raises(ValueError, match="not code"):
        store.promote_artifact_to_snippet(h)


def test_promotion_missing_returns_none(store):
    assert store.promote_artifact_to_snippet("sha256:" + "f" * 64) is None
