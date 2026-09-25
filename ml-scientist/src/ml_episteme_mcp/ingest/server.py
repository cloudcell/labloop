"""Artifact ingest HTTP surface.

POST /artifacts        raw bytes → content-addressed blob (artifact_files)
GET  /artifacts/{hash} blob bytes + metadata headers
HEAD /artifacts/{hash} existence/size only

Controls (per plan-20260921-0322Z):
- bearer token required on all artifact routes; no token → 401
- request body read with a byte counter — oversized → 413, never
  buffered unbounded
- sha256 computed server-side; client-supplied hashes ignored
- no listing, no deletion, no path parameters that touch the
  filesystem; {hash} must match sha256:[0-9a-f]{64}
- no execution, no imports, no path resolution during ingest
"""

from __future__ import annotations

import hashlib
import hmac
import re
from pathlib import PurePosixPath

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ..state.models import _utc_now
from ..state.store import StateStore

DEFAULT_MAX_BYTES = 16 * 1024 * 1024  # 16 MiB

_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def create_ingest_app(
    store: StateStore,
    token: str | None,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Starlette:
    """Create the artifact-ingest Starlette app.

    Args:
        store: The shared StateStore (write access, artifact_files only).
        token: Bearer token required on artifact routes. If falsy every
            artifact route denies — fail closed even if the listener is
            started by mistake.
        max_bytes: Maximum accepted object size.
    """

    def _authorized(request: Request) -> bool:
        if not token:
            return False
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return False
        return hmac.compare_digest(auth[7:], token)

    async def put_artifact(request: Request) -> Response:
        if not _authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        # Stream the body with a hard byte counter — request.body()
        # would buffer unbounded before we could enforce the cap.
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > max_bytes:
                return JSONResponse(
                    {"error": f"artifact exceeds {max_bytes} bytes"},
                    status_code=413,
                )
            chunks.append(chunk)
        content = b"".join(chunks)

        # Filename is metadata only — strip any path components so a
        # client can never smuggle a filesystem reference through.
        raw_name = request.headers.get("x-artifact-name", "artifact")
        filename = PurePosixPath(raw_name).name or "artifact"
        media_type = request.headers.get("content-type")

        content_hash = "sha256:" + hashlib.sha256(content).hexdigest()
        store.create_artifact_file(
            content_hash=content_hash,
            filename=filename,
            content=content,
            content_type=media_type,
            captured_at=_utc_now(),
            original_path=None,
        )
        return JSONResponse(
            {
                "hash": content_hash,
                "size": size,
                "media_type": media_type,
            },
            status_code=201,
        )

    def _lookup(request: Request) -> dict | Response:
        """Validate the hash param and fetch; returns row or Response."""
        content_hash = request.path_params["hash"]
        if not _HASH_RE.match(content_hash):
            return JSONResponse(
                {"error": "hash must be sha256:[0-9a-f]{64}"},
                status_code=400,
            )
        row = store.get_artifact_file(content_hash)
        if row is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return row

    async def get_artifact(request: Request) -> Response:
        if not _authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        row = _lookup(request)
        if isinstance(row, Response):
            return row
        # Integrity: re-hash the stored blob on read — the content
        # address must match the bytes it names. DB corruption or a
        # tampered blob surfaces as 500, never as wrong bytes served
        # under a trusted hash.
        if ("sha256:" + hashlib.sha256(row["content"]).hexdigest()
                != row["content_hash"]):
            return JSONResponse(
                {"error": "artifact integrity check failed"},
                status_code=500,
            )
        return Response(
            content=row["content"],
            media_type=row["content_type"] or "application/octet-stream",
            headers={
                "X-Artifact-Name": row["filename"],
                "X-Artifact-Hash": row["content_hash"],
            },
        )

    async def head_artifact(request: Request) -> Response:
        if not _authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        row = _lookup(request)
        if isinstance(row, Response):
            return row
        return Response(
            status_code=200,
            headers={
                "Content-Length": str(row["size_bytes"]),
                "X-Artifact-Name": row["filename"],
                "X-Artifact-Hash": row["content_hash"],
            },
        )

    def health(request: Request) -> Response:
        return JSONResponse({"status": "ok", "surface": "ingest"})

    return Starlette(
        routes=[
            Route("/artifacts", put_artifact, methods=["POST"]),
            # HEAD before GET: Starlette auto-adds HEAD to GET routes,
            # so the GET route would shadow head_artifact — the cheap
            # existence check must match first.
            Route("/artifacts/{hash}", head_artifact, methods=["HEAD"]),
            Route("/artifacts/{hash}", get_artifact, methods=["GET"]),
            Route("/health", health, methods=["GET"]),
        ]
    )
