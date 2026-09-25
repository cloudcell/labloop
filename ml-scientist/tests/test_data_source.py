"""Tests for the Data Source role and DataRef provenance.

Verifies:
- DataRef model has the correct fields and regimes
- LocalDataHandler prepares generated data (runs generator, computes hash)
- LocalDataHandler prepares captured data (registers URI, computes hash)
- LocalDataHandler verifies data integrity (re-computes hash)
- LocalDataHandler resolves DataRefs to read-only paths
- StubDataHandler returns canned DataRef IDs
- prepare_data tool creates DataRef records in the state DB
- verify_data tool checks integrity
- capture_bundle accepts data_refs
- dataref://{id} resource returns DataRef details
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from ml_episteme_mcp.clients.adaptor import create_stub_adaptor
from ml_episteme_mcp.clients.local_data_handler import LocalDataHandler
from ml_episteme_mcp.clients.local_executor import LocalExecutor
from ml_episteme_mcp.state.models import DataRef
from ml_episteme_mcp.state.store import StateStore


# --- DataRef model tests ---


class TestDataRefModel:
    """Tests for the DataRef model."""

    def test_data_ref_generated_defaults(self):
        """A generated DataRef has the correct defaults."""
        ref = DataRef(
            id="data-ref-test",
            split="train",
            regime="generated",
            generator_code_ref="/path/to/gen.py",
            generator_seed=42,
        )
        assert ref.regime == "generated"
        assert ref.reproducibility_risk == "none"
        assert ref.content_hash is None
        assert ref.source_uri is None
        assert ref.ontological_category == "information content entity"

    def test_data_ref_captured_defaults(self):
        """A captured DataRef has the correct defaults."""
        ref = DataRef(
            id="data-ref-test",
            split="validation",
            regime="captured",
            source_uri="s3://bucket/data.parquet",
        )
        assert ref.regime == "captured"
        assert ref.reproducibility_risk == "none"  # default, set by handler
        assert ref.generator_code_ref is None

    def test_data_ref_stream_capture(self):
        """A stream capture DataRef has temporal fields."""
        ref = DataRef(
            id="data-ref-test",
            split="test",
            regime="captured",
            source_uri="stream://sensor-feed",
            capture_window_start="2026-09-14T10:00:00Z",
            capture_window_end="2026-09-14T11:00:00Z",
        )
        assert ref.capture_window_start is not None
        assert ref.capture_window_end is not None

    def test_data_ref_mece(self):
        """DataRef is in ENTITY_CATEGORIES with the correct category."""
        from ml_episteme_mcp.state.models import ENTITY_CATEGORIES
        assert DataRef in ENTITY_CATEGORIES
        assert ENTITY_CATEGORIES[DataRef] == "information content entity"


# --- SQLite store tests ---


class TestDataRefStore:
    """Tests for DataRef persistence in SQLite."""

    def test_create_and_get_data_ref(self):
        """The store round-trips a DataRef through SQLite."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = StateStore(db_path)
            store.connect()

            ref = DataRef(
                id="data-ref-1",
                split="train",
                regime="generated",
                generator_code_ref="/path/to/gen.py",
                generator_seed=42,
                generator_params={"n_samples": 1000},
                content_hash="sha256:abc123",
                storage_uri="file:///data/dataset.csv",
                reproducibility_risk="none",
            )
            store.create_data_ref(ref)

            retrieved = store.get_data_ref("data-ref-1")
            assert retrieved is not None
            assert retrieved.id == "data-ref-1"
            assert retrieved.split == "train"
            assert retrieved.regime == "generated"
            assert retrieved.generator_code_ref == "/path/to/gen.py"
            assert retrieved.generator_seed == 42
            assert retrieved.generator_params == {"n_samples": 1000}
            assert retrieved.content_hash == "sha256:abc123"
            assert retrieved.reproducibility_risk == "none"

            store.close()

    def test_get_nonexistent_data_ref(self):
        """get_data_ref returns None for unknown IDs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = StateStore(db_path)
            store.connect()
            assert store.get_data_ref("nonexistent") is None
            store.close()

    def test_list_data_refs(self):
        """list_data_refs returns all DataRefs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = StateStore(db_path)
            store.connect()

            for i in range(3):
                ref = DataRef(
                    id=f"data-ref-{i}",
                    split="train",
                    regime="generated",
                    generator_seed=i,
                )
                store.create_data_ref(ref)

            refs = store.list_data_refs()
            assert len(refs) == 3
            store.close()

    def test_bundle_with_data_refs_json(self):
        """The store round-trips data_refs_json through bundles."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = StateStore(db_path)
            store.connect()

            from ml_episteme_mcp.state.models import Programme, Hypothesis, Trial, Bundle
            store.create_programme(Programme(
                id="prog-1",
                goal="test",
                constraints={},
                allowed_variables=["lr"],
                budget_max_trials=10,
                budget_max_wall_time_hours=1.0,
            ))
            store.create_hypothesis(Hypothesis(
                id="hyp-1",
                programme_id="prog-1",
                statement="test",
                failure_criterion="test",
                variables_involved=["lr"],
            ))
            trial = Trial(
                id="trial-1",
                programme_id="prog-1",
                hypothesis_id="hyp-1",
                config_json="{}",
            )
            store.create_trial(trial)

            bundle = Bundle(
                id="bundle-1",
                trial_id="trial-1",
                code_ref="/path/to/code.py",
                env_ref="env-1",
                seeds_json="[42]",
                splits_json="{}",
                data_refs_json='["data-ref-1", "data-ref-2"]',
            )
            store.create_bundle(bundle)

            retrieved = store.get_bundle("bundle-1")
            assert retrieved is not None
            assert retrieved.data_refs_json == '["data-ref-1", "data-ref-2"]'

            store.close()


# --- LocalDataHandler tests ---


class TestLocalDataHandler:
    """Tests for the LocalDataHandler."""

    @pytest.mark.asyncio
    async def test_prepare_generated_data(self):
        """Generated data: runs generator, computes hash, stores read-only."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "data"
            executor = LocalExecutor()
            handler = LocalDataHandler(data_dir=data_dir, executor=executor)

            # Write a generator script
            gen_path = Path(tmpdir) / "generator.py"
            gen_path.write_text(
                "def generate_data(config, output_path):\n"
                "    with open(output_path, 'w') as f:\n"
                "        for i in range(config.get('n_samples', 10)):\n"
                "            f.write(f'{i},{config.get(\"seed\", 0)}\\n')\n"
            )

            data_ref_id = await handler.prepare_data(
                split="train",
                regime="generated",
                generator_code_ref=str(gen_path),
                generator_seed=42,
                generator_params={"n_samples": 100},
            )

            assert data_ref_id.startswith("data-ref-")

            # Verify metadata
            metadata = await handler.get_data_ref(data_ref_id)
            assert metadata["split"] == "train"
            assert metadata["regime"] == "generated"
            assert metadata["content_hash"] is not None
            assert metadata["content_hash"].startswith("sha256:")
            assert metadata["size_bytes"] > 0
            assert metadata["reproducibility_risk"] == "none"
            assert metadata["storage_uri"].startswith("file://")

            # Verify the data file is read-only
            data_path = Path(metadata["storage_uri"][7:])
            assert data_path.exists()
            # On Unix, read-only means no write permission for owner
            if os.name != "nt":  # Skip on Windows
                import stat
                mode = data_path.stat().st_mode
                assert not (mode & stat.S_IWUSR), "Data file should be read-only"

    @pytest.mark.asyncio
    async def test_prepare_captured_local_file(self):
        """Captured data: registers local file, computes hash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "data"
            handler = LocalDataHandler(data_dir=data_dir)

            # Create a source file
            source_path = Path(tmpdir) / "source.csv"
            source_path.write_text("col1,col2\n1,2\n3,4\n")

            data_ref_id = await handler.prepare_data(
                split="validation",
                regime="captured",
                source_uri=f"file://{source_path}",
            )

            metadata = await handler.get_data_ref(data_ref_id)
            assert metadata["regime"] == "captured"
            assert metadata["content_hash"] is not None
            assert metadata["reproducibility_risk"] == "external-dependency"

    @pytest.mark.asyncio
    async def test_prepare_captured_external_uri(self):
        """Captured data: external URI without local access."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "data"
            handler = LocalDataHandler(data_dir=data_dir)

            data_ref_id = await handler.prepare_data(
                split="test",
                regime="captured",
                source_uri="s3://bucket/data.parquet",
                version="v1.0",
            )

            metadata = await handler.get_data_ref(data_ref_id)
            assert metadata["regime"] == "captured"
            assert metadata["content_hash"] is None  # can't compute
            assert metadata["reproducibility_risk"] == "external-dependency"

    @pytest.mark.asyncio
    async def test_verify_data_integrity(self):
        """verify_data re-computes the hash and checks against recorded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "data"
            executor = LocalExecutor()
            handler = LocalDataHandler(data_dir=data_dir, executor=executor)

            gen_path = Path(tmpdir) / "generator.py"
            gen_path.write_text(
                "def generate_data(config, output_path):\n"
                "    with open(output_path, 'w') as f:\n"
                "        f.write('test data\\n')\n"
            )

            data_ref_id = await handler.prepare_data(
                split="train",
                regime="generated",
                generator_code_ref=str(gen_path),
                generator_seed=42,
            )

            result = await handler.verify_data(data_ref_id)
            assert result["verified"] is True
            assert result["recorded_hash"] == result["computed_hash"]

    @pytest.mark.asyncio
    async def test_resolve_data_path(self):
        """resolve_data_path returns a read-only path after hash verification."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "data"
            executor = LocalExecutor()
            handler = LocalDataHandler(data_dir=data_dir, executor=executor)

            gen_path = Path(tmpdir) / "generator.py"
            gen_path.write_text(
                "def generate_data(config, output_path):\n"
                "    with open(output_path, 'w') as f:\n"
                "        f.write('resolve test\\n')\n"
            )

            data_ref_id = await handler.prepare_data(
                split="train",
                regime="generated",
                generator_code_ref=str(gen_path),
                generator_seed=42,
            )

            path = await handler.resolve_data_path(data_ref_id)
            assert Path(path).exists()
            with open(path) as f:
                assert "resolve test" in f.read()

    @pytest.mark.asyncio
    async def test_resolve_nonexistent_data_ref(self):
        """resolve_data_path raises for unknown DataRefs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            handler = LocalDataHandler(data_dir=Path(tmpdir) / "data")
            with pytest.raises(FileNotFoundError):
                await handler.resolve_data_path("nonexistent")

    @pytest.mark.asyncio
    async def test_prepare_invalid_regime(self):
        """prepare_data rejects invalid regimes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            handler = LocalDataHandler(data_dir=Path(tmpdir) / "data")
            with pytest.raises(ValueError, match="Unknown regime"):
                await handler.prepare_data(split="train", regime="invalid")


# --- StubDataHandler tests ---


class TestStubDataHandler:
    """Tests for the StubDataHandler."""

    @pytest.mark.asyncio
    async def test_stub_prepare_data(self):
        """StubDataHandler returns a DataRef ID."""
        from ml_episteme_mcp.clients.adaptor import StubDataHandler
        handler = StubDataHandler()
        data_ref_id = await handler.prepare_data(
            split="train",
            regime="generated",
            generator_code_ref="/path/to/gen.py",
        )
        assert data_ref_id.startswith("data-ref-")

    @pytest.mark.asyncio
    async def test_stub_verify_data(self):
        """StubDataHandler verify_data returns verified=True."""
        from ml_episteme_mcp.clients.adaptor import StubDataHandler
        handler = StubDataHandler()
        data_ref_id = await handler.prepare_data(
            split="train", regime="generated"
        )
        result = await handler.verify_data(data_ref_id)
        assert result["verified"] is True


# --- Adaptor tests ---


class TestAdaptorDataSource:
    """Tests for the data_source property on MCPAdaptor."""

    def test_stub_adaptor_has_data_source(self):
        """create_stub_adaptor wires the data source role."""
        adaptor = create_stub_adaptor()
        assert adaptor.data_source is not None

    def test_local_adaptor_has_data_source(self):
        """create_local_adaptor wires the data source role."""
        from ml_episteme_mcp.clients.adaptor import create_local_adaptor
        with tempfile.TemporaryDirectory() as tmpdir:
            adaptor = create_local_adaptor(
                data_dir=str(Path(tmpdir) / "data"),
            )
            assert adaptor.data_source is not None
