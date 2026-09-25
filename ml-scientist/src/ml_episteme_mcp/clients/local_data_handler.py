"""Local data handler — provides read-only datasets with provenance.

This is a real data handler that:
- Prepares data (generated or captured)
- Computes content hashes (SHA-256) for provenance verification
- Records timestamps at process boundaries
- Provides read-only access to datasets
- Verifies data integrity on access

For generated data: runs the generator via the executor role, stores
output in data storage, computes hash, sets read-only.

For captured data: registers the external URI, computes hash if
accessible, records provenance.

For stream capture: captures the window snapshot, stores it, computes
hash and timestamp.

The handler enforces:
- Commitment 2 (controlled bundles): data is immutable once prepared
- Commitment 5 (reproducibility): hash + timestamp enable verification
- Data-code separation: data lives in data storage, not code storage
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .roles import DataSourceRole, ExecutorRole


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    """Compute the SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def _sha256_bytes(data: bytes) -> str:
    """Compute the SHA-256 hash of bytes."""
    h = hashlib.sha256(data)
    return f"sha256:{h.hexdigest()}"


def _set_readonly(path: Path) -> None:
    """Set a file to read-only (chmod 444)."""
    os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


class LocalDataHandler(DataSourceRole):
    """Local filesystem data handler.

    Prepares, stores, and provides read-only access to datasets.
    Computes hashes for provenance verification. Records timestamps
    at process boundaries.

    Data storage layout:
        {data_dir}/
            {data_ref_id}/
                dataset.{ext}    # the actual data file
                metadata.json    # provenance metadata
    """

    def __init__(self, data_dir: str | Path, executor: ExecutorRole | None = None):
        """Initialize the local data handler.

        Args:
            data_dir: Root directory for data storage.
            executor: Executor role for running generators (required for
                      generated data; not needed for captured-only use).
        """
        self._data_dir = Path(data_dir)
        self._executor = executor

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    async def prepare_data(
        self,
        split: str,
        regime: str,
        generator_code_ref: str | None = None,
        generator_seed: int | None = None,
        generator_params: dict[str, Any] | None = None,
        source_uri: str | None = None,
        version: str | None = None,
        capture_window_start: str | None = None,
        capture_window_end: str | None = None,
        capture_source_metadata: dict[str, Any] | None = None,
    ) -> str:
        """Prepare a dataset and return a DataRef ID."""
        data_ref_id = f"data-ref-{uuid.uuid4().hex[:8]}"
        storage_dir = self._data_dir / data_ref_id
        storage_dir.mkdir(parents=True, exist_ok=True)

        if regime == "generated":
            return await self._prepare_generated(
                data_ref_id, storage_dir, split,
                generator_code_ref, generator_seed, generator_params,
            )
        elif regime == "captured":
            return await self._prepare_captured(
                data_ref_id, storage_dir, split,
                source_uri, version,
                capture_window_start, capture_window_end,
                capture_source_metadata,
            )
        else:
            raise ValueError(f"Unknown regime: {regime}. Must be 'generated' or 'captured'.")

    async def _prepare_generated(
        self,
        data_ref_id: str,
        storage_dir: Path,
        split: str,
        generator_code_ref: str | None,
        generator_seed: int | None,
        generator_params: dict[str, Any] | None,
    ) -> str:
        """Prepare generated data by running the generator."""
        if generator_code_ref is None:
            raise ValueError("generator_code_ref is required for generated data")
        if self._executor is None:
            raise RuntimeError("Executor is required for generated data but none was provided")

        # Generate the wrapper code that calls generate_data(config, output_path)
        output_path = storage_dir / "dataset.csv"
        config = {
            "seed": generator_seed,
            **(generator_params or {}),
        }
        config_json = json.dumps(config)

        wrapper_code = (
            f"import json, sys, importlib.util\n"
            f"\n"
            f"spec = importlib.util.spec_from_file_location('generator', {repr(generator_code_ref)})\n"
            f"module = importlib.util.module_from_spec(spec)\n"
            f"spec.loader.exec_module(module)\n"
            f"\n"
            f"if not hasattr(module, 'generate_data'):\n"
            f"    print(json.dumps({{'error': 'generator does not expose generate_data(config, output_path)'}}))\n"
            f"    sys.exit(1)\n"
            f"\n"
            f"config = {config_json}\n"
            f"output_path = {repr(str(output_path))}\n"
            f"module.generate_data(config, output_path)\n"
        )

        result_str = await self._executor.execute_code(
            wrapper_code,
            extra_ro_paths=[generator_code_ref],
            extra_rw_paths=[storage_dir],
        )
        result = json.loads(result_str)

        if result.get("status") != "completed":
            raise RuntimeError(f"Generator failed: {result.get('error', result.get('stderr', 'unknown'))}")

        # Compute hash
        content_hash = _sha256_file(output_path)
        size_bytes = output_path.stat().st_size

        # Set read-only
        _set_readonly(output_path)

        # Determine reproducibility risk
        reproducibility_risk = "none"  # generated data is fully reproducible

        # Write metadata
        metadata = {
            "id": data_ref_id,
            "split": split,
            "regime": "generated",
            "generator_code_ref": generator_code_ref,
            "generator_seed": generator_seed,
            "generator_params": generator_params,
            "content_hash": content_hash,
            "size_bytes": size_bytes,
            "storage_uri": f"file://{output_path}",
            "reproducibility_risk": reproducibility_risk,
            "created_at": _utc_now(),
        }
        metadata_path = storage_dir / "metadata.json"
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2)
        _set_readonly(metadata_path)

        return data_ref_id

    async def _prepare_captured(
        self,
        data_ref_id: str,
        storage_dir: Path,
        split: str,
        source_uri: str | None,
        version: str | None,
        capture_window_start: str | None,
        capture_window_end: str | None,
        capture_source_metadata: dict[str, Any] | None,
    ) -> str:
        """Prepare captured data by registering the source."""
        if source_uri is None:
            raise ValueError("source_uri is required for captured data")

        # Determine if this is a stream capture
        is_stream = capture_window_start is not None or capture_window_end is not None

        # Try to compute hash if the source is a local file
        content_hash = None
        size_bytes = None
        storage_uri = source_uri

        if source_uri.startswith("file://"):
            local_path = Path(source_uri[7:])
            if local_path.exists():
                content_hash = _sha256_file(local_path)
                size_bytes = local_path.stat().st_size
                # Copy to data storage for read-only access
                dest = storage_dir / local_path.name
                dest.write_bytes(local_path.read_bytes())
                _set_readonly(dest)
                storage_uri = f"file://{dest}"

        # Determine reproducibility risk
        if is_stream:
            reproducibility_risk = "temporal-source"
        elif content_hash is not None:
            reproducibility_risk = "external-dependency"
        else:
            reproducibility_risk = "external-dependency"

        # Write metadata
        metadata = {
            "id": data_ref_id,
            "split": split,
            "regime": "captured",
            "source_uri": source_uri,
            "version": version,
            "capture_window_start": capture_window_start,
            "capture_window_end": capture_window_end,
            "capture_source_metadata": capture_source_metadata,
            "content_hash": content_hash,
            "size_bytes": size_bytes,
            "storage_uri": storage_uri,
            "reproducibility_risk": reproducibility_risk,
            "created_at": _utc_now(),
        }
        metadata_path = storage_dir / "metadata.json"
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2)
        _set_readonly(metadata_path)

        return data_ref_id

    async def get_data_ref(self, data_ref_id: str) -> dict[str, Any]:
        """Return the DataRef details."""
        metadata_path = self._data_dir / data_ref_id / "metadata.json"
        if not metadata_path.exists():
            return {"error": f"DataRef not found: {data_ref_id}"}
        with open(metadata_path) as f:
            return json.load(f)

    async def verify_data(self, data_ref_id: str) -> dict[str, Any]:
        """Re-compute the hash and check against the recorded hash."""
        metadata = await self.get_data_ref(data_ref_id)
        if "error" in metadata:
            return metadata

        recorded_hash = metadata.get("content_hash")
        if recorded_hash is None:
            return {
                "data_ref_id": data_ref_id,
                "verified": False,
                "error": "No content hash recorded (external data not accessible)",
            }

        storage_uri = metadata.get("storage_uri", "")
        if not storage_uri.startswith("file://"):
            return {
                "data_ref_id": data_ref_id,
                "verified": False,
                "error": f"Cannot verify: storage_uri is not local ({storage_uri})",
            }

        local_path = Path(storage_uri[7:])
        if not local_path.exists():
            return {
                "data_ref_id": data_ref_id,
                "verified": False,
                "error": f"Data file not found: {local_path}",
            }

        computed_hash = _sha256_file(local_path)
        verified = computed_hash == recorded_hash

        return {
            "data_ref_id": data_ref_id,
            "verified": verified,
            "recorded_hash": recorded_hash,
            "computed_hash": computed_hash,
            "checked_at": _utc_now(),
        }

    async def resolve_data_path(self, data_ref_id: str) -> str:
        """Resolve a DataRef to a read-only filesystem path.

        Verifies the hash before returning the path.
        """
        metadata = await self.get_data_ref(data_ref_id)
        if "error" in metadata:
            raise FileNotFoundError(metadata["error"])

        storage_uri = metadata.get("storage_uri", "")
        if not storage_uri.startswith("file://"):
            raise ValueError(
                f"Cannot resolve to local path: storage_uri is {storage_uri}. "
                f"Data is external (reproducibility_risk: {metadata.get('reproducibility_risk')})"
            )

        local_path = Path(storage_uri[7:])
        if not local_path.exists():
            raise FileNotFoundError(f"Data file not found: {local_path}")

        # Verify hash if recorded
        recorded_hash = metadata.get("content_hash")
        if recorded_hash is not None:
            computed_hash = _sha256_file(local_path)
            if computed_hash != recorded_hash:
                raise RuntimeError(
                    f"Data integrity violation: hash mismatch. "
                    f"Recorded: {recorded_hash}, Computed: {computed_hash}"
                )

        return str(local_path)
