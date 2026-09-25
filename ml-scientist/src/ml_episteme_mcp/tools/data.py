"""Data tool handlers — prepare_data, verify_data.

These tools provide the agent with the ability to prepare datasets
(generated or captured) and verify their provenance.
"""

from __future__ import annotations

import json
import uuid

from ..state.models import DataRef
from ..state.store import StateStore
from ..clients.adaptor import MCPAdaptor
from .schemas import coerce_json, fail, ok, VerifyDataOut, PrepareDataOut
from typing import Annotated, Literal
from pydantic import Field
from mcp.types import CallToolResult


def register(mcp, store: StateStore, adaptor: MCPAdaptor) -> None:
    """Register data-related tools on the MCP server."""

    @mcp.tool()
    async def prepare_data(
        split: Annotated[Literal['train', 'validation', 'test'], Field(description='Dataset split: train | validation | test.')],
        regime: Annotated[Literal['generated', 'captured'], Field(description='generated (run generator via executor; needs generator_code_ref + generator_seed) | captured (register external URI; needs source_uri).')],
        generator_code_ref: Annotated[str | None, Field(description="Path/ref to generator code — required for regime='generated'.")] = None,
        generator_seed: Annotated[int | None, Field(description="Generator seed — required for regime='generated'.")] = None,
        generator_params: Annotated[dict | str | None, Field(description='Generator parameters; object or JSON-encoded.')] = None,
        source_uri: Annotated[str | None, Field(description="External data URI — required for regime='captured'.")] = None,
        version: Annotated[str | None, Field(description='Version tag for the captured data source.')] = None,
        capture_window_start: Annotated[str | None, Field(description='Temporal bound for stream capture (captured + temporal).')] = None,
        capture_window_end: Annotated[str | None, Field(description='Temporal bound for stream capture (captured + temporal).')] = None,
        capture_source_metadata: Annotated[dict | str | None, Field(description='Extra provenance about the captured source; object or JSON-encoded.')] = None,
    ) -> Annotated[CallToolResult, PrepareDataOut]:
        """Prepare a dataset and return a DataRef ID.

        Two regimes:
        - generated: runs the generator (via executor), stores output,
          computes hash. Requires generator_code_ref, generator_seed.
        - captured: registers an external URI, computes hash if accessible.
          Requires source_uri.

        For stream capture (captured + temporal), provide capture_window_start
        and capture_window_end.

        The DataRef is stored in the state DB and the data is stored in
        data storage (separate from code storage). Access is read-only.

        Returns: {"data_ref_id": "data-ref-...", "split": ..., "regime": ...}

        generator_params and capture_source_metadata may be sent as
        JSON-encoded strings.
        """
        try:
            if generator_params is not None:
                generator_params = coerce_json(generator_params, dict, "generator_params")
            if capture_source_metadata is not None:
                capture_source_metadata = coerce_json(
                    capture_source_metadata, dict, "capture_source_metadata"
                )
            if regime not in ("generated", "captured"):
                return fail(json.dumps({
                    "error": f"regime must be 'generated' or 'captured', got: {regime}",
                }))

            if split not in ("train", "validation", "test"):
                return fail(json.dumps({
                    "error": f"split must be 'train', 'validation', or 'test', got: {split}",
                }))

            # Prepare the data via the Data Source role
            data_ref_id = await adaptor.data_source.prepare_data(
                split=split,
                regime=regime,
                generator_code_ref=generator_code_ref,
                generator_seed=generator_seed,
                generator_params=generator_params,
                source_uri=source_uri,
                version=version,
                capture_window_start=capture_window_start,
                capture_window_end=capture_window_end,
                capture_source_metadata=capture_source_metadata,
            )

            # Get the metadata from the data source
            metadata = await adaptor.data_source.get_data_ref(data_ref_id)

            # Phase 0: capture generator code content (content-addressed)
            # For generated data, the generator code is part of the provenance.
            # Store it in code_snippets so the data ref is self-contained.
            generator_code_hash = None
            if regime == "generated" and generator_code_ref:
                try:
                    generator_code_hash = store.capture_code_from_path(generator_code_ref)
                except Exception as e:
                    return fail(json.dumps({"error": f"Failed to capture generator code: {e}"}))

            # Create a DataRef record in the state DB
            data_ref = DataRef(
                id=data_ref_id,
                split=split,
                regime=regime,
                generator_code_ref=generator_code_ref,
                generator_code_hash=generator_code_hash,
                generator_seed=generator_seed,
                generator_params=generator_params,
                source_uri=source_uri,
                version=version,
                capture_window_start=capture_window_start,
                capture_window_end=capture_window_end,
                capture_source_metadata=capture_source_metadata,
                content_hash=metadata.get("content_hash"),
                size_bytes=metadata.get("size_bytes"),
                storage_uri=metadata.get("storage_uri"),
                reproducibility_risk=metadata.get("reproducibility_risk", "none"),
            )
            store.create_data_ref(data_ref)

            return ok({
                "data_ref_id": data_ref_id,
                "split": split,
                "regime": regime,
                "content_hash": metadata.get("content_hash"),
                "reproducibility_risk": metadata.get("reproducibility_risk"),
                "storage_uri": metadata.get("storage_uri"),
            })
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))

    @mcp.tool()
    async def verify_data(data_ref_id: Annotated[str, Field(description='ID of the target DataRef (from prepare_data).')]) -> Annotated[CallToolResult, VerifyDataOut]:
        """Verify data provenance by re-computing the hash.

        Returns: {"verified": bool, "recorded_hash": ..., "computed_hash": ...}

        If verified is false, the data was modified after preparation — a
        provenance violation that invalidates any trial using this data.
        """
        try:
            result = await adaptor.data_source.verify_data(data_ref_id)
            # Normalize to verdict semantics: a missing `verified` key
            # (e.g. the adaptor reporting a bare {error} for an unknown
            # ref) means the ref could not be verified — same verdict
            # shape verify_archive returns.
            if "verified" not in result:
                result = {"verified": False, **result}
            return ok(result)
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))
