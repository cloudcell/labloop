"""Artifact ingest — byte transport for remote agents.

A deliberately tiny HTTP surface on its own port (separate from the
read-only observability GUI): POST raw bytes, get back a content
address. The hash is the only meaningful reference — filenames and
media types are recorded as metadata, never trusted.

Ingest never executes. Promotion into code_snippets happens at
consumption time inside capture_bundle_from_code_hash.
"""

from .server import create_ingest_app

__all__ = ["create_ingest_app"]
