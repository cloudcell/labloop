"""Role interfaces — protocol-level abstractions the server depends on.

The system depends on roles, not products. Each role defines a uniform
interface; the adaptor layer maps each role to a concrete MCP server via
configuration. Products are swappable; roles are not.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any


class OptimizerRole(ABC):
    """The optimizer role: studies, samplers, ask/tell, HPO."""

    @abstractmethod
    async def create_study(
        self, programme_id: str, variables: list[str], direction: str = "maximize"
    ) -> str:
        """Initialize the optimization study. Returns a study_id.

        Args:
            programme_id: The programme this study belongs to.
            variables: The search space variables.
            direction: 'minimize' or 'maximize' — which direction is better.
        """

    @abstractmethod
    async def ask(self, programme_id: str) -> dict[str, Any]:
        """Get the next trial configuration from the optimizer."""

    @abstractmethod
    async def tell(self, programme_id: str, trial_id: str, result: dict[str, float]) -> None:
        """Report the trial result to the optimizer."""

    @abstractmethod
    async def best_trials(
        self, programme_id: str, direction: str | None = None
    ) -> list[dict[str, Any]]:
        """Read the Pareto front / best results.

        Args:
            programme_id: The programme to query.
            direction: Override direction ('minimize' or 'maximize').
                       If None, uses the direction set at create_study.
        """

    @abstractmethod
    async def param_importance(self, programme_id: str) -> dict[str, float]:
        """Read which variables matter most."""


class ExecutorRole(ABC):
    """The executor role: edit/execute code, inspect outputs."""

    @abstractmethod
    async def execute_code(
        self,
        code: str,
        artifact_dir: Any = None,
        trial_id: str | None = None,
        programme_id: str | None = None,
        bundle_id: str | None = None,
        extra_ro_paths: list[str] | None = None,
        extra_rw_paths: list[str] | None = None,
        python_exe: str | None = None,
        overlay_ro: list[tuple[str, str]] | None = None,
    ) -> str:
        """Execute code and return the output.

        ``overlay_ro`` is a list of ``(host_source, target_path)`` pairs:
        inside a sandbox the host_source file is bind-mounted over
        target_path so the trial sees the sealed bytes, not whatever
        currently lives at target_path. Executors without mount
        namespaces ignore it (seal unenforced).

        When ``artifact_dir`` is provided, the executor should write
        durable artifacts (wrapper script, stdout, stderr, manifest)
        to that directory instead of using a temp file.
        """

    @abstractmethod
    async def read_cell_output(self, cell_id: str) -> str:
        """Read the output from a previously executed cell."""

    async def execute_code_async(
        self,
        trial_id: str,
        code: str,
        artifact_dir: Any = None,
        programme_id: str | None = None,
        bundle_id: str | None = None,
        extra_ro_paths: list[str] | None = None,
        extra_rw_paths: list[str] | None = None,
        python_exe: str | None = None,
        overlay_ro: list[tuple[str, str]] | None = None,
    ) -> str:
        """Start execution in the background. Returns {'status': 'running'}.

        Default implementation falls back to synchronous execution.
        Subclasses can override for true async behavior.
        """
        return await self.execute_code(
            code,
            artifact_dir=artifact_dir,
            trial_id=trial_id,
            programme_id=programme_id,
            bundle_id=bundle_id,
            extra_ro_paths=extra_ro_paths,
            extra_rw_paths=extra_rw_paths,
            python_exe=python_exe,
            overlay_ro=overlay_ro,
        )

    def get_async_status(self, trial_id: str) -> str:
        """Check the status of an async execution.

        Default implementation returns 'unknown' (no async support).
        Subclasses can override to track background tasks.
        """
        return '{"status": "unknown"}'

    async def cancel_async(self, trial_id: str) -> str:
        """Cancel a running async execution.

        Default implementation returns an error (no async support).
        Subclasses can override to cancel background tasks.
        """
        return json.dumps({
            "status": "failed",
            "error": "This executor does not support async cancellation",
        })


class DataSourceRole(ABC):
    """The data source role: provides read-only datasets with provenance.

    The data source is the authoritative provider of
    training/validation/test data. It prepares data (generated or
    captured), computes content hashes, records timestamps, and
    provides read-only access.

    Ontological category: realizable entity (function)
    The provision function is realized when data is requested; it
    exists between calls. The bearer is a concrete data handler
    (local filesystem, S3, HuggingFace, stream capturer, etc.).

    Enforces:
    - Commitment 2 (controlled bundles): data is immutable once prepared
    - Commitment 5 (reproducibility): hash + timestamp enable verification
    - Data-code separation: data lives in data storage, not code storage
    """

    @abstractmethod
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
        """Prepare a dataset and return a DataRef ID.

        For generated data: runs the generator (via executor), stores
        output in data storage, computes hash and timestamp.

        For captured data: registers the external URI, computes hash
        if accessible, records provenance.

        For stream capture: captures the window snapshot, stores it,
        computes hash and timestamp.

        Returns: DataRef ID (data-ref-{uuid})
        """

    @abstractmethod
    async def get_data_ref(self, data_ref_id: str) -> dict[str, Any]:
        """Return the DataRef details (hash, timestamp, storage_uri, risk)."""

    @abstractmethod
    async def verify_data(self, data_ref_id: str) -> dict[str, Any]:
        """Re-compute the hash and check against the recorded hash.

        Returns: {"verified": bool, "recorded_hash": ..., "computed_hash": ...}
        """

    @abstractmethod
    async def resolve_data_path(self, data_ref_id: str) -> str:
        """Resolve a DataRef to a read-only filesystem path.

        Called by run_trial to inject data paths into the training
        config. Verifies the hash before returning the path. Enforces
        read-only access (filesystem permissions where possible).

        Returns: filesystem path (read-only)
        """


class ClaimsRole(ABC):
    """The claims role: semantic memory — what the system *knows*.

    Distinct from episodic memory (what happened), which is owned by
    each loop's own state store — never a role (ADR-0005). Claims are
    distilled assertions with typed evidence edges and provenance,
    shared across loops via a peer memory server (anamnesis today).
    """

    @abstractmethod
    async def assert_claim(
        self,
        content: str,
        type: str,
        confidence: float,
        evidence: list[dict[str, Any]] | None = None,
        source_id: str | None = None,
    ) -> str:
        """Assert a claim into semantic memory. Returns a claim_id.

        Idempotent — asserting the same content again returns the
        existing claim's id (dedup by content+type).
        """

    @abstractmethod
    async def relate(
        self,
        from_claim: str,
        to_ref: str,
        ref_type: str,
        relation: str,
    ) -> str:
        """Link a claim to evidence or another claim. Returns an edge_id."""

    @abstractmethod
    async def get_claim(self, claim_id: str) -> dict[str, Any]:
        """Read a claim with its full provenance bundle."""

    @abstractmethod
    async def list_claims(
        self, type: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """List claims, newest first."""
