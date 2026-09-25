"""Archive support — automatic, batched archiving of terminal programmes.

When a programme reaches a terminal state (completed or abandoned), it is
copied to the current archive SQLite file and removed from the live DB.
Multiple programmes share one archive file until batch_size is reached,
then the archive is sealed (read-only) and a new one is created.

Design principles (from plan-20260914-1314Z):
- Archiving is automatic (fires inside close_programme/abandon_programme)
- Archives are batched (configurable batch_size, default 10)
- Archives are timestamped files in one directory
- Copy-then-delete with integrity verification
- Archives are immutable once sealed
- The live DB retains registry metadata (archive_registry + archive_entries)
- Archives are self-contained (include code_snippets from Phase 0)

Ontological category: process (archiving is a process that produces carriers)
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .state.store import StateStore


def _utc_timestamp() -> str:
    """UTC timestamp in ISO format with Z suffix (e.g. 20260914T1314Z)."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y%m%dT%H%MZ")


def _utc_iso() -> str:
    """UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


class ArchiveConfig:
    """Archive configuration loaded from config."""

    def __init__(
        self,
        enabled: bool = True,
        batch_size: int = 10,
        archive_dir: str | Path | None = None,
    ):
        self.enabled = enabled
        self.batch_size = batch_size
        if archive_dir is None:
            archive_dir = Path.home() / ".ml-episteme" / "archives"
        self.archive_dir = Path(archive_dir)

    @classmethod
    def from_config(cls, config: dict[str, Any] | None = None) -> "ArchiveConfig":
        """Load from a config dict (e.g. from ml-episteme.toml)."""
        if config is None:
            return cls()
        archive_cfg = config.get("archive", {})
        return cls(
            enabled=archive_cfg.get("enabled", True),
            batch_size=archive_cfg.get("batch_size", 10),
            archive_dir=archive_cfg.get("archive_dir"),
        )

    def validate(self) -> str | None:
        """Validate config. Returns error message or None if valid."""
        if self.batch_size < 1:
            return f"archive.batch_size must be >= 1, got {self.batch_size}"
        return None


class Archiver:
    """Manages automatic, batched archiving of terminal programmes.

    The Archiver is called by close_programme/abandon_programme after
    the programme status is updated. It:
    1. Finds or creates the current (open) archive file
    2. Copies the programme + all related rows to the archive
    3. Verifies integrity (row counts, hashes)
    4. Deletes the programme from the live DB
    5. Seals the archive if it reaches batch_size
    """

    def __init__(self, store: StateStore, config: ArchiveConfig | None = None):
        self.store = store
        self.config = config or ArchiveConfig()
        self.config.archive_dir.mkdir(parents=True, exist_ok=True)
        # Clean up orphan zero-length archive files left by failed
        # _create_archive_schema calls (registry write failed after
        # the file was created). Only removes files NOT referenced by
        # any registry entry.
        self._cleanup_orphan_files()

    def _cleanup_orphan_files(self) -> None:
        """Remove zero-length archive files not in the registry."""
        try:
            registered_paths = {
                Path(row["archive_path"]).name
                for row in self.store._fetchall(
                    "SELECT archive_path FROM archive_registry"
                )
            }
        except Exception:
            return  # registry not ready (e.g. during schema bootstrap)
        for f in self.config.archive_dir.glob("archive-*.db"):
            if f.stat().st_size == 0 and f.name not in registered_paths:
                try:
                    f.unlink()
                except Exception:
                    pass

    def archive_programme(self, programme_id: str) -> dict[str, Any]:
        """Archive a terminal programme. Called automatically by close_programme.

        Returns: {archive_id, programme_id, row_count, verified, sealed}
        Raises: RuntimeError on integrity failure (live DB is not modified)
        """
        if not self.config.enabled:
            return {"archived": False, "reason": "archiving disabled"}

        # Validate config
        err = self.config.validate()
        if err:
            raise RuntimeError(err)

        # Get the programme
        programme = self.store.get_programme(programme_id)
        if programme is None:
            raise RuntimeError(f"Programme not found: {programme_id}")

        if programme.status.value not in ("completed", "abandoned"):
            raise RuntimeError(
                f"Programme {programme_id} is not terminal (status={programme.status.value}). "
                f"Only completed/abandoned programmes can be archived."
            )

        # Defense-in-depth: no running trials (Rule 5.6 — a running
        # trial is an occurrent that hasn't reached its end-boundary)
        trials = self.store.list_trials(programme_id)
        running = [t.id for t in trials if t.status.value == "running"]
        if running:
            raise RuntimeError(
                f"Programme {programme_id} has running trials: {running}. "
                f"Cannot archive a programme with in-flight processes. "
                f"Cancel or wait for them first."
            )

        # Find or create the current (open) archive
        archive_id, archive_path = self._get_or_create_open_archive()

        # Collect all rows for this programme
        rows = self._collect_programme_rows(programme_id)
        row_count = sum(len(v) for v in rows.values())

        # Copy to the archive
        self._copy_to_archive(archive_path, rows)

        # Verify: reopen the archive and check row counts
        verified = self._verify_archive(archive_path, programme_id, rows)

        if not verified:
            raise RuntimeError(
                f"Archive verification failed for programme {programme_id}. "
                f"Live DB was not modified."
            )

        # Mark the programme as archived BEFORE recording the registry entry.
        # This is the commit point: a single UPDATE that transitions the
        # programme from completed/abandoned → archived. If this fails,
        # no registry entry is recorded — the programme stays terminal
        # and can be re-archived later. No zombie state is possible.
        self._mark_programme_archived(programme_id)

        # Record in the registry (only after successful mark)
        self._record_archive_entry(archive_id, programme_id, programme.goal, row_count)

        # Check if the archive should be sealed
        sealed = False
        archive_info = self._get_archive_info(archive_id)
        if archive_info["programme_count"] >= self.config.batch_size:
            self._seal_archive(archive_id)
            sealed = True

        return {
            "archived": True,
            "archive_id": archive_id,
            "archive_path": str(archive_path),
            "programme_id": programme_id,
            "row_count": row_count,
            "verified": verified,
            "sealed": sealed,
        }

    def _get_or_create_open_archive(self) -> tuple[str, Path]:
        """Find the current open archive, or create a new one."""
        # Look for an unsealed archive
        row = self.store._fetchone(
            "SELECT * FROM archive_registry WHERE sealed = 0 ORDER BY created_at ASC"
        )
        if row is not None:
            return row["archive_id"], Path(row["archive_path"])

        # Create a new archive with a collision-resistant name.
        # Timestamp + short UUID ensures uniqueness even if multiple
        # archives are created in the same second.
        archive_id = f"archive-{uuid.uuid4().hex[:8]}"
        timestamp = _utc_timestamp()
        short_id = uuid.uuid4().hex[:4]
        archive_path = self.config.archive_dir / f"archive-{timestamp}-{short_id}.db"

        # Create the archive DB with the same schema (tables only, no data)
        self._create_archive_schema(archive_path)

        # Record in the registry. If this fails, remove the orphan file
        # so we don't accumulate zero-length garbage.
        try:
            self.store._write(
                "INSERT INTO archive_registry "
                "(archive_id, archive_path, created_at, batch_size, programme_count, sealed) "
                "VALUES (?,?,?,?,?,?)",
                (archive_id, str(archive_path), _utc_iso(), self.config.batch_size, 0, 0),
            )
        except Exception:
            try:
                archive_path.unlink()
            except Exception:
                pass
            raise

        return archive_id, archive_path

    def _create_archive_schema(self, archive_path: Path) -> None:
        """Create an empty archive DB with the same table structure."""
        conn = sqlite3.connect(str(archive_path))
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS programmes (
            id TEXT PRIMARY KEY,
            goal TEXT NOT NULL,
            constraints_json TEXT NOT NULL,
            allowed_variables_json TEXT NOT NULL,
            budget_max_trials INTEGER NOT NULL,
            budget_max_wall_time_hours REAL NOT NULL,
            metric_direction TEXT NOT NULL DEFAULT 'maximize',
            candidate_version_id TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS candidate_versions (
            id TEXT PRIMARY KEY,
            parent_id TEXT,
            code_artifact_digest TEXT NOT NULL,
            harness_artifact_digest TEXT,
            model_ref TEXT NOT NULL,
            search_policy_ref TEXT,
            capability_profile_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS evaluation_contracts (
            id TEXT PRIMARY KEY,
            programme_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            metrics_json TEXT NOT NULL,
            holdouts_json TEXT,
            budget_json TEXT,
            promotion_policy_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS hypotheses (
            id TEXT PRIMARY KEY,
            programme_id TEXT NOT NULL,
            statement TEXT NOT NULL,
            failure_criterion TEXT NOT NULL,
            variables_involved_json TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS trials (
            id TEXT PRIMARY KEY,
            programme_id TEXT NOT NULL,
            hypothesis_id TEXT NOT NULL,
            config_json TEXT NOT NULL,
            bundle_id TEXT,
            status TEXT NOT NULL,
            duration_seconds REAL,
            artifact_path TEXT,
            executor_output_json TEXT,
            started_at TEXT,
            finished_at TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS observations (
            id TEXT PRIMARY KEY,
            trial_id TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            variance_json TEXT NOT NULL,
            spatiotemporal_region TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS beliefs (
            id TEXT PRIMARY KEY,
            programme_id TEXT NOT NULL,
            state_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conclusions (
            id TEXT PRIMARY KEY,
            hypothesis_id TEXT NOT NULL,
            programme_id TEXT NOT NULL,
            verdict TEXT NOT NULL,
            evidence_ref TEXT NOT NULL,
            evidence_summary TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS bundles (
            id TEXT PRIMARY KEY,
            trial_id TEXT NOT NULL,
            code_ref TEXT NOT NULL,
            code_hash TEXT,
            code_hash_extra_json TEXT,
            env_ref TEXT NOT NULL,
            seeds_json TEXT NOT NULL,
            splits_json TEXT NOT NULL,
            data_refs_json TEXT,
            baseline_ref TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS data_refs (
            id TEXT PRIMARY KEY,
            split TEXT NOT NULL,
            regime TEXT NOT NULL,
            generator_code_ref TEXT,
            generator_code_hash TEXT,
            generator_seed INTEGER,
            generator_params_json TEXT,
            source_uri TEXT,
            content_hash TEXT,
            version TEXT,
            capture_window_start TEXT,
            capture_window_end TEXT,
            capture_source_metadata_json TEXT,
            schema_hash TEXT,
            size_bytes INTEGER,
            num_samples INTEGER,
            storage_uri TEXT,
            reproducibility_risk TEXT NOT NULL DEFAULT 'none',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS code_snippets (
            code_hash TEXT PRIMARY KEY,
            code_text TEXT NOT NULL,
            language TEXT NOT NULL DEFAULT 'python',
            captured_at TEXT NOT NULL,
            original_path TEXT,
            size_bytes INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS artifact_files (
            content_hash TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            content BLOB NOT NULL,
            content_type TEXT,
            size_bytes INTEGER NOT NULL,
            compressed_size_bytes INTEGER NOT NULL,
            captured_at TEXT NOT NULL,
            original_path TEXT
        );
        CREATE TABLE IF NOT EXISTS trial_artifacts (
            id TEXT PRIMARY KEY,
            trial_id TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            filename TEXT NOT NULL,
            artifact_type TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (trial_id) REFERENCES trials(id),
            FOREIGN KEY (content_hash) REFERENCES artifact_files(content_hash)
        );
        """)
        conn.commit()
        conn.close()

    def _collect_programme_rows(self, programme_id: str) -> dict[str, list[dict]]:
        """Collect all rows related to a programme from the live DB."""
        rows: dict[str, list[dict]] = {}

        rows["programmes"] = [dict(r) for r in self.store._fetchall(
            "SELECT * FROM programmes WHERE id = ?", (programme_id,)
        )]

        rows["hypotheses"] = [dict(r) for r in self.store._fetchall(
            "SELECT * FROM hypotheses WHERE programme_id = ?", (programme_id,)
        )]

        rows["trials"] = [dict(r) for r in self.store._fetchall(
            "SELECT * FROM trials WHERE programme_id = ?", (programme_id,)
        )]

        trial_ids = [r["id"] for r in rows["trials"]]

        if trial_ids:
            placeholders = ",".join("?" * len(trial_ids))
            rows["observations"] = [dict(r) for r in self.store._fetchall(
                f"SELECT * FROM observations WHERE trial_id IN ({placeholders})",
                tuple(trial_ids),
            )]
            rows["bundles"] = [dict(r) for r in self.store._fetchall(
                f"SELECT * FROM bundles WHERE trial_id IN ({placeholders})",
                tuple(trial_ids),
            )]
        else:
            rows["observations"] = []
            rows["bundles"] = []

        rows["beliefs"] = [dict(r) for r in self.store._fetchall(
            "SELECT * FROM beliefs WHERE programme_id = ?", (programme_id,)
        )]

        rows["conclusions"] = [dict(r) for r in self.store._fetchall(
            "SELECT * FROM conclusions WHERE programme_id = ?", (programme_id,)
        )]

        # RSI Phase 0: contracts are programme-scoped evidence; the
        # candidate manifest travels with the programme so its results
        # remain attributable (reproducible from its manifest).
        rows["evaluation_contracts"] = [dict(r) for r in self.store._fetchall(
            "SELECT * FROM evaluation_contracts WHERE programme_id = ?",
            (programme_id,),
        )]

        candidate_id = (
            rows["programmes"][0].get("candidate_version_id")
            if rows["programmes"]
            else None
        )
        if candidate_id:
            rows["candidate_versions"] = [dict(r) for r in self.store._fetchall(
                "SELECT * FROM candidate_versions WHERE id = ?", (candidate_id,)
            )]
        else:
            rows["candidate_versions"] = []

        # Collect data_refs referenced by bundles
        data_ref_ids: set[str] = set()
        for bundle in rows["bundles"]:
            if bundle.get("data_refs_json"):
                for ref_id in json.loads(bundle["data_refs_json"]):
                    data_ref_ids.add(ref_id)

        if data_ref_ids:
            placeholders = ",".join("?" * len(data_ref_ids))
            rows["data_refs"] = [dict(r) for r in self.store._fetchall(
                f"SELECT * FROM data_refs WHERE id IN ({placeholders})",
                tuple(data_ref_ids),
            )]
        else:
            rows["data_refs"] = []

        # Collect code_snippets referenced by bundles and data_refs
        code_hashes: set[str] = set()
        for bundle in rows["bundles"]:
            if bundle.get("code_hash"):
                code_hashes.add(bundle["code_hash"])
            # Multi-file capture: also collect extra code hashes
            if bundle.get("code_hash_extra_json"):
                try:
                    for h in json.loads(bundle["code_hash_extra_json"]):
                        code_hashes.add(h)
                except Exception:
                    pass
        for ref in rows["data_refs"]:
            if ref.get("generator_code_hash"):
                code_hashes.add(ref["generator_code_hash"])

        if code_hashes:
            placeholders = ",".join("?" * len(code_hashes))
            rows["code_snippets"] = [dict(r) for r in self.store._fetchall(
                f"SELECT * FROM code_snippets WHERE code_hash IN ({placeholders})",
                tuple(code_hashes),
            )]
        else:
            rows["code_snippets"] = []

        # Collect trial_artifacts and their artifact_files (content-addressed
        # BLOBs: paper PDFs, manifests, stdout/stderr, etc.)
        if trial_ids:
            placeholders = ",".join("?" * len(trial_ids))
            rows["trial_artifacts"] = [dict(r) for r in self.store._fetchall(
                f"SELECT * FROM trial_artifacts WHERE trial_id IN ({placeholders})",
                tuple(trial_ids),
            )]
        else:
            rows["trial_artifacts"] = []

        # Collect artifact_files referenced by trial_artifacts
        artifact_hashes: set[str] = set()
        for ta in rows["trial_artifacts"]:
            if ta.get("content_hash"):
                artifact_hashes.add(ta["content_hash"])

        if artifact_hashes:
            placeholders = ",".join("?" * len(artifact_hashes))
            rows["artifact_files"] = [dict(r) for r in self.store._fetchall(
                f"SELECT * FROM artifact_files WHERE content_hash IN ({placeholders})",
                tuple(artifact_hashes),
            )]
        else:
            rows["artifact_files"] = []

        return rows

    def _ensure_archive_schema(self, archive_path: Path) -> None:
        """Bring an open archive's schema up to date before writing.

        Open (unsealed) archives are append targets that may have been
        created before newer tables/columns existed — e.g. the RSI Phase-0
        entities. CREATE TABLE IF NOT EXISTS adds missing tables; columns
        added to existing tables need explicit ALTERs.
        """
        self._create_archive_schema(archive_path)
        conn = sqlite3.connect(str(archive_path))
        try:
            prog_cols = {
                r[1] for r in conn.execute("PRAGMA table_info(programmes)")
            }
            if "candidate_version_id" not in prog_cols:
                conn.execute(
                    "ALTER TABLE programmes "
                    "ADD COLUMN candidate_version_id TEXT"
                )
                conn.commit()
        finally:
            conn.close()

    def _copy_to_archive(self, archive_path: Path, rows: dict[str, list[dict]]) -> None:
        """Copy rows from the live DB to the archive DB."""
        self._ensure_archive_schema(archive_path)
        conn = sqlite3.connect(str(archive_path))

        for table_name, table_rows in rows.items():
            if not table_rows:
                continue
            columns = list(table_rows[0].keys())
            # Upgrade older open archives to the live schema: if the
            # archive predates a column the live DB now has (e.g.
            # executor_output_json), add it rather than dropping data.
            # SQLite column types are advisory, so TEXT is a safe
            # forward-compatible choice.
            existing = {
                r[1] for r in conn.execute(
                    f"PRAGMA table_info({table_name})"
                )
            }
            for col in columns:
                if col not in existing:
                    conn.execute(
                        f"ALTER TABLE {table_name} ADD COLUMN {col} TEXT"
                    )
            placeholders = ",".join("?" * len(columns))
            col_list = ",".join(columns)
            for row in table_rows:
                values = [row[c] for c in columns]
                conn.execute(
                    f"INSERT OR IGNORE INTO {table_name} ({col_list}) VALUES ({placeholders})",
                    values,
                )
        conn.commit()
        conn.close()

    def _verify_archive(
        self,
        archive_path: Path,
        programme_id: str,
        expected_rows: dict[str, list[dict]],
    ) -> bool:
        """Verify that the archive contains the expected rows."""
        conn = sqlite3.connect(str(archive_path))

        for table_name, expected in expected_rows.items():
            if not expected:
                continue
            # Count rows in the archive for this programme
            if table_name == "programmes":
                count = conn.execute(
                    "SELECT COUNT(*) FROM programmes WHERE id = ?", (programme_id,)
                ).fetchone()[0]
                if count != len(expected):
                    conn.close()
                    return False
            elif table_name in ("hypotheses", "trials", "beliefs", "conclusions",
                                "evaluation_contracts"):
                count = conn.execute(
                    f"SELECT COUNT(*) FROM {table_name} WHERE programme_id = ?",
                    (programme_id,),
                ).fetchone()[0]
                if count != len(expected):
                    conn.close()
                    return False
            elif table_name == "candidate_versions":
                ids = [r["id"] for r in expected]
                if not ids:
                    continue
                placeholders = ",".join("?" * len(ids))
                count = conn.execute(
                    f"SELECT COUNT(*) FROM candidate_versions WHERE id IN ({placeholders})",
                    tuple(ids),
                ).fetchone()[0]
                if count != len(expected):
                    conn.close()
                    return False
            elif table_name in ("observations", "bundles"):
                trial_ids = [r["id"] for r in expected_rows.get("trials", [])]
                if not trial_ids:
                    continue
                placeholders = ",".join("?" * len(trial_ids))
                count = conn.execute(
                    f"SELECT COUNT(*) FROM {table_name} WHERE trial_id IN ({placeholders})",
                    tuple(trial_ids),
                ).fetchone()[0]
                if count != len(expected):
                    conn.close()
                    return False
            elif table_name in ("data_refs", "code_snippets"):
                # These are shared; just check they exist
                ids = [r["id"] if table_name == "data_refs" else r["code_hash"] for r in expected]
                if not ids:
                    continue
                placeholders = ",".join("?" * len(ids))
                col = "id" if table_name == "data_refs" else "code_hash"
                count = conn.execute(
                    f"SELECT COUNT(*) FROM {table_name} WHERE {col} IN ({placeholders})",
                    tuple(ids),
                ).fetchone()[0]
                if count != len(expected):
                    conn.close()
                    return False
            elif table_name == "trial_artifacts":
                # Check by trial_id (FK to trials)
                trial_ids = [r["id"] for r in expected_rows.get("trials", [])]
                if not trial_ids:
                    continue
                placeholders = ",".join("?" * len(trial_ids))
                count = conn.execute(
                    f"SELECT COUNT(*) FROM trial_artifacts WHERE trial_id IN ({placeholders})",
                    tuple(trial_ids),
                ).fetchone()[0]
                if count != len(expected):
                    conn.close()
                    return False
            elif table_name == "artifact_files":
                # Check by content_hash (PK)
                hashes = [r["content_hash"] for r in expected]
                if not hashes:
                    continue
                placeholders = ",".join("?" * len(hashes))
                count = conn.execute(
                    f"SELECT COUNT(*) FROM artifact_files WHERE content_hash IN ({placeholders})",
                    tuple(hashes),
                ).fetchone()[0]
                if count != len(expected):
                    conn.close()
                    return False

        conn.close()
        return True

    def _record_archive_entry(
        self,
        archive_id: str,
        programme_id: str,
        programme_goal: str,
        row_count: int,
    ) -> None:
        """Record the archive entry in the registry."""
        entry_id = f"ae-{uuid.uuid4().hex[:8]}"
        self.store._write(
            "INSERT INTO archive_entries "
            "(id, archive_id, programme_id, programme_goal, archived_at, row_count, verified) "
            "VALUES (?,?,?,?,?,?,?)",
            (entry_id, archive_id, programme_id, programme_goal, _utc_iso(), row_count, 1),
        )
        # Increment programme_count in the registry
        self.store._write(
            "UPDATE archive_registry SET programme_count = programme_count + 1 "
            "WHERE archive_id = ?",
            (archive_id,),
        )

    def _mark_programme_archived(self, programme_id: str) -> None:
        """Mark a terminal programme as archived in the live DB.

        This is a single UPDATE that transitions the programme from
        completed/abandoned → archived. The programme's rows (trials,
        hypotheses, bundles, observations, etc.) stay in the live DB
        as a backup copy. The archive DB is the authoritative record.

        This replaces the previous _delete_programme_from_live which
        issued 6+ DELETE statements and was prone to lock contention.
        """
        self.store.update_programme_status(programme_id, "archived")

    def repair_archived_programmes(self) -> dict:
        """Repair live rows for programmes recorded in archive_entries.

        Fixes two failure modes:

        1. **Status corruption** — a migration bug (VALID_STATUSES missing
           'archived') reset archived programmes to 'active' and clobbered
           created_at with the literal string 'archived' on every restart.
           Restore status='archived' and recover the real created_at from
           the archive DB (the archive preserves the pre-archive value).

        2. **Purged programmes** — the pre-no-purge archiver deleted live
           rows entirely. Those programmes are invisible on the GUI index
           (which lists the live table) and only reachable via /archives.
           Restore the full row set from the archive DB so the live DB is
           again a complete backup copy; restored programmes get
           status='archived' (the archive stores the pre-archive status).

        Idempotent — safe to run on every startup.

        Returns: {repaired: [...], restored: [...], errors: [...]}
        """
        repaired = []
        restored = []
        errors = []

        try:
            entries = self.store._fetchall(
                "SELECT programme_id, archive_id, archived_at FROM archive_entries"
            )
        except Exception:
            return {"repaired": [], "restored": [], "errors": []}

        for entry in entries:
            pid = entry["programme_id"]
            aid = entry["archive_id"]
            try:
                live = self.store._fetchone(
                    "SELECT id, status, created_at FROM programmes WHERE id = ?",
                    (pid,),
                )
                archive_path = self._get_archive_path(aid)

                if live is None:
                    # Fully purged — restore everything from the archive
                    self._restore_programme_to_live(archive_path, pid)
                    restored.append(pid)
                elif live["status"] != "archived" or not self._looks_like_timestamp(live["created_at"]):
                    # Corrupted — fix status, recover created_at
                    created_at = self._read_archived_created_at(
                        archive_path, pid
                    ) or entry["archived_at"]
                    self.store._write(
                        "UPDATE programmes SET status = 'archived', "
                        "created_at = ? WHERE id = ?",
                        (created_at, pid),
                    )
                    repaired.append(pid)
            except Exception as e:
                errors.append({"programme_id": pid, "error": str(e)})

        return {"repaired": repaired, "restored": restored, "errors": errors}

    @staticmethod
    def _looks_like_timestamp(value: str | None) -> bool:
        """True if the value looks like an ISO timestamp, not a status word."""
        if not value or len(value) < 10:
            return False
        # ISO timestamps start with a 4-digit year followed by '-'
        return value[:4].isdigit() and value[4] == "-"

    def _get_archive_path(self, archive_id: str) -> Path | None:
        """Resolve an archive_id to its DB path, or None."""
        row = self.store._fetchone(
            "SELECT archive_path FROM archive_registry WHERE archive_id = ?",
            (archive_id,),
        )
        return Path(row["archive_path"]) if row else None

    def _read_archived_created_at(
        self, archive_path: Path | None, programme_id: str
    ) -> str | None:
        """Read the programme's original created_at from the archive DB."""
        if archive_path is None or not archive_path.exists():
            return None
        try:
            conn = sqlite3.connect(str(archive_path))
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT created_at FROM programmes WHERE id = ?",
                (programme_id,),
            ).fetchone()
            conn.close()
            if row and self._looks_like_timestamp(row["created_at"]):
                return row["created_at"]
        except Exception:
            pass
        return None

    def _restore_programme_to_live(
        self, archive_path: Path | None, programme_id: str
    ) -> None:
        """Restore a fully-purged programme from its archive DB into live.

        Mirrors _collect_programme_rows in reverse: reads every
        programme-scoped row from the archive and INSERT OR IGNOREs into
        live. The programmes row gets status='archived' (the archive
        stores the pre-archive status; live invariant is 'archived').
        """
        if archive_path is None or not archive_path.exists():
            raise FileNotFoundError(
                f"Archive DB not found for {programme_id}: {archive_path}"
            )

        aconn = sqlite3.connect(str(archive_path))
        aconn.row_factory = sqlite3.Row
        try:
            # Older archives predate trial_artifacts/artifact_files —
            # check before querying them.
            archive_tables = {
                r["name"] for r in aconn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }

            def afetch(sql, params=()):
                return [dict(r) for r in aconn.execute(sql, params).fetchall()]

            prog_rows = afetch(
                "SELECT * FROM programmes WHERE id = ?", (programme_id,)
            )
            if not prog_rows:
                raise ValueError(f"{programme_id} not found in {archive_path}")
            prog_rows[0]["status"] = "archived"  # live invariant

            hyp_rows = afetch(
                "SELECT * FROM hypotheses WHERE programme_id = ?", (programme_id,)
            )
            trial_rows = afetch(
                "SELECT * FROM trials WHERE programme_id = ?", (programme_id,)
            )
            trial_ids = [r["id"] for r in trial_rows]
            tph = ",".join("?" * len(trial_ids)) if trial_ids else ""

            obs_rows = afetch(
                f"SELECT * FROM observations WHERE trial_id IN ({tph})",
                tuple(trial_ids),
            ) if trial_ids else []
            bundle_rows = afetch(
                f"SELECT * FROM bundles WHERE trial_id IN ({tph})",
                tuple(trial_ids),
            ) if trial_ids else []
            ta_rows = (
                afetch(
                    f"SELECT * FROM trial_artifacts WHERE trial_id IN ({tph})",
                    tuple(trial_ids),
                )
                if trial_ids and "trial_artifacts" in archive_tables
                else []
            )

            belief_rows = afetch(
                "SELECT * FROM beliefs WHERE programme_id = ?", (programme_id,)
            )
            concl_rows = afetch(
                "SELECT * FROM conclusions WHERE programme_id = ?", (programme_id,)
            )

            # RSI Phase 0 entities — absent in pre-lineage archives.
            contract_rows = (
                afetch(
                    "SELECT * FROM evaluation_contracts WHERE programme_id = ?",
                    (programme_id,),
                )
                if "evaluation_contracts" in archive_tables
                else []
            )
            cand_rows = (
                afetch(
                    "SELECT * FROM candidate_versions WHERE id = ?",
                    (prog_rows[0].get("candidate_version_id"),),
                )
                if "candidate_versions" in archive_tables
                and prog_rows[0].get("candidate_version_id")
                else []
            )

            # Content-addressed rows referenced by this programme
            data_ref_ids: set[str] = set()
            code_hashes: set[str] = set()
            artifact_hashes: set[str] = set()
            for b in bundle_rows:
                if b.get("data_refs_json"):
                    try:
                        data_ref_ids.update(json.loads(b["data_refs_json"]))
                    except Exception:
                        pass
                if b.get("code_hash"):
                    code_hashes.add(b["code_hash"])
                if b.get("code_hash_extra_json"):
                    try:
                        code_hashes.update(json.loads(b["code_hash_extra_json"]))
                    except Exception:
                        pass
            for ta in ta_rows:
                if ta.get("content_hash"):
                    artifact_hashes.add(ta["content_hash"])

            dref_rows = afetch(
                "SELECT * FROM data_refs WHERE id IN ({})".format(
                    ",".join("?" * len(data_ref_ids))
                ),
                tuple(data_ref_ids),
            ) if data_ref_ids else []
            for ref in dref_rows:
                if ref.get("generator_code_hash"):
                    code_hashes.add(ref["generator_code_hash"])

            code_rows = afetch(
                "SELECT * FROM code_snippets WHERE code_hash IN ({})".format(
                    ",".join("?" * len(code_hashes))
                ),
                tuple(code_hashes),
            ) if code_hashes else []
            af_rows = (
                afetch(
                    "SELECT * FROM artifact_files WHERE content_hash IN ({})".format(
                        ",".join("?" * len(artifact_hashes))
                    ),
                    tuple(artifact_hashes),
                )
                if artifact_hashes and "artifact_files" in archive_tables
                else []
            )
        finally:
            aconn.close()

        # INSERT OR IGNORE: scoped rows (trials, hypotheses, ...) have
        # unique IDs so they're inserted fresh; shared content-addressed
        # rows (code_snippets, data_refs, artifact_files) may already
        # exist from other programmes — don't clobber them.
        all_rows = {
            "programmes": prog_rows,
            "hypotheses": hyp_rows,
            "trials": trial_rows,
            "observations": obs_rows,
            "bundles": bundle_rows,
            "beliefs": belief_rows,
            "conclusions": concl_rows,
            "evaluation_contracts": contract_rows,
            "candidate_versions": cand_rows,
            "data_refs": dref_rows,
            "code_snippets": code_rows,
            "trial_artifacts": ta_rows,
            "artifact_files": af_rows,
        }
        for table_name, table_rows in all_rows.items():
            if not table_rows:
                continue
            columns = list(table_rows[0].keys())
            placeholders = ",".join("?" * len(columns))
            col_list = ",".join(columns)
            for row in table_rows:
                self.store._write(
                    f"INSERT OR IGNORE INTO {table_name} ({col_list}) "
                    f"VALUES ({placeholders})",
                    tuple(row[c] for c in columns),
                )


    def _get_archive_info(self, archive_id: str) -> dict[str, Any]:
        """Get archive registry info."""
        row = self.store._fetchone(
            "SELECT * FROM archive_registry WHERE archive_id = ?", (archive_id,)
        )
        return dict(row) if row else {}

    def _seal_archive(self, archive_id: str) -> None:
        """Seal an archive: compute hash, set read-only, mark as sealed."""
        info = self._get_archive_info(archive_id)
        archive_path = Path(info["archive_path"])

        # Compute SHA-256 of the archive file
        h = hashlib.sha256()
        with open(archive_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        archive_hash = "sha256:" + h.hexdigest()
        archive_size = archive_path.stat().st_size

        # Update the registry
        self.store._write(
            "UPDATE archive_registry SET sealed = 1, sealed_at = ?, "
            "archive_hash = ?, archive_size_bytes = ? WHERE archive_id = ?",
            (_utc_iso(), archive_hash, archive_size, archive_id),
        )

        # Set the file to read-only
        os.chmod(archive_path, 0o444)

    # --- Read methods for GUI/tools ---

    def list_archives(self) -> list[dict[str, Any]]:
        """List all archives (open and sealed)."""
        rows = self.store._fetchall(
            "SELECT * FROM archive_registry ORDER BY created_at DESC"
        )
        return [dict(r) for r in rows]

    def get_archive(self, archive_id: str) -> dict[str, Any] | None:
        """Get archive details including its programmes."""
        row = self.store._fetchone(
            "SELECT * FROM archive_registry WHERE archive_id = ?", (archive_id,)
        )
        if row is None:
            return None
        archive = dict(row)
        entries = self.store._fetchall(
            "SELECT * FROM archive_entries WHERE archive_id = ? ORDER BY archived_at DESC",
            (archive_id,),
        )
        archive["programmes"] = [dict(e) for e in entries]
        return archive

    def get_archived_programme(self, programme_id: str) -> dict[str, Any] | None:
        """Get an archived programme's details from its archive file."""
        entry = self.store._fetchone(
            "SELECT * FROM archive_entries WHERE programme_id = ?",
            (programme_id,),
        )
        if entry is None:
            return None

        archive = self.store._fetchone(
            "SELECT * FROM archive_registry WHERE archive_id = ?",
            (entry["archive_id"],),
        )
        if archive is None:
            return None

        archive_path = Path(archive["archive_path"])
        if not archive_path.exists():
            return {"error": f"Archive file not found: {archive_path}"}

        # Read the programme from the archive DB
        conn = sqlite3.connect(str(archive_path))
        conn.row_factory = sqlite3.Row
        prog_row = conn.execute(
            "SELECT * FROM programmes WHERE id = ?", (programme_id,)
        ).fetchone()
        if prog_row is None:
            conn.close()
            return None

        result = dict(prog_row)
        result["archive_id"] = entry["archive_id"]
        result["archive_path"] = str(archive_path)
        result["archived_at"] = entry["archived_at"]
        result["row_count"] = entry["row_count"]

        # Get hypotheses, trials, etc. from the archive
        result["hypotheses"] = [dict(r) for r in conn.execute(
            "SELECT * FROM hypotheses WHERE programme_id = ?", (programme_id,)
        ).fetchall()]
        result["trials"] = [dict(r) for r in conn.execute(
            "SELECT * FROM trials WHERE programme_id = ?", (programme_id,)
        ).fetchall()]
        result["beliefs"] = [dict(r) for r in conn.execute(
            "SELECT * FROM beliefs WHERE programme_id = ?", (programme_id,)
        ).fetchall()]
        result["conclusions"] = [dict(r) for r in conn.execute(
            "SELECT * FROM conclusions WHERE programme_id = ?", (programme_id,)
        ).fetchall()]

        # RSI Phase 0 entities — absent in archives written before the
        # lineage schema existed, so check sqlite_master first.
        archive_tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "evaluation_contracts" in archive_tables:
            result["evaluation_contracts"] = [dict(r) for r in conn.execute(
                "SELECT * FROM evaluation_contracts WHERE programme_id = ?",
                (programme_id,),
            ).fetchall()]
        if "candidate_versions" in archive_tables:
            result["candidate_versions"] = [dict(r) for r in conn.execute(
                "SELECT * FROM candidate_versions WHERE id = ?",
                (result.get("candidate_version_id"),),
            ).fetchall()]

        # Observations — need trial IDs first
        trial_ids = [t["id"] for t in result["trials"]]
        if trial_ids:
            placeholders = ",".join("?" * len(trial_ids))
            result["observations"] = [dict(r) for r in conn.execute(
                f"SELECT * FROM observations WHERE trial_id IN ({placeholders})",
                tuple(trial_ids),
            ).fetchall()]
            result["bundles"] = [dict(r) for r in conn.execute(
                f"SELECT * FROM bundles WHERE trial_id IN ({placeholders})",
                tuple(trial_ids),
            ).fetchall()]
        else:
            result["observations"] = []
            result["bundles"] = []

        # DataRefs referenced by bundles
        data_ref_ids: set[str] = set()
        for bundle in result["bundles"]:
            if bundle.get("data_refs_json"):
                try:
                    for ref_id in json.loads(bundle["data_refs_json"]):
                        data_ref_ids.add(ref_id)
                except Exception:
                    pass
        if data_ref_ids:
            placeholders = ",".join("?" * len(data_ref_ids))
            result["data_refs"] = [dict(r) for r in conn.execute(
                f"SELECT * FROM data_refs WHERE id IN ({placeholders})",
                tuple(data_ref_ids),
            ).fetchall()]
        else:
            result["data_refs"] = []

        # Code snippets referenced by bundles and data_refs
        code_hashes: set[str] = set()
        for bundle in result["bundles"]:
            if bundle.get("code_hash"):
                code_hashes.add(bundle["code_hash"])
            # Multi-file capture: also collect extra code hashes
            if bundle.get("code_hash_extra_json"):
                try:
                    for h in json.loads(bundle["code_hash_extra_json"]):
                        code_hashes.add(h)
                except Exception:
                    pass
        for ref in result["data_refs"]:
            if ref.get("generator_code_hash"):
                code_hashes.add(ref["generator_code_hash"])
        if code_hashes:
            placeholders = ",".join("?" * len(code_hashes))
            result["code_snippets"] = [dict(r) for r in conn.execute(
                f"SELECT * FROM code_snippets WHERE code_hash IN ({placeholders})",
                tuple(code_hashes),
            ).fetchall()]
        else:
            result["code_snippets"] = []

        # Trial artifacts and their content-addressed files (paper PDFs,
        # manifests, stdout/stderr, etc.)
        if trial_ids:
            placeholders = ",".join("?" * len(trial_ids))
            try:
                result["trial_artifacts"] = [dict(r) for r in conn.execute(
                    f"SELECT * FROM trial_artifacts WHERE trial_id IN ({placeholders})",
                    tuple(trial_ids),
                ).fetchall()]
            except Exception:
                result["trial_artifacts"] = []  # old archive without the table
        else:
            result["trial_artifacts"] = []

        artifact_hashes = {ta["content_hash"] for ta in result["trial_artifacts"] if ta.get("content_hash")}
        if artifact_hashes:
            placeholders = ",".join("?" * len(artifact_hashes))
            try:
                result["artifact_files"] = [dict(r) for r in conn.execute(
                    f"SELECT * FROM artifact_files WHERE content_hash IN ({placeholders})",
                    tuple(artifact_hashes),
                ).fetchall()]
            except Exception:
                result["artifact_files"] = []  # old archive without the table
        else:
            result["artifact_files"] = []

        conn.close()
        return result

    def get_archived_trial(
        self, programme_id: str, trial_id: str
    ) -> dict[str, Any] | None:
        """Get a single archived trial's full details from its archive file.

        Returns the trial row, its bundle, observations, and referenced
        code snippets / data refs.
        """
        entry = self.store._fetchone(
            "SELECT * FROM archive_entries WHERE programme_id = ?",
            (programme_id,),
        )
        if entry is None:
            return None

        archive = self.store._fetchone(
            "SELECT * FROM archive_registry WHERE archive_id = ?",
            (entry["archive_id"],),
        )
        if archive is None:
            return None

        archive_path = Path(archive["archive_path"])
        if not archive_path.exists():
            return {"error": f"Archive file not found: {archive_path}"}

        conn = sqlite3.connect(str(archive_path))
        conn.row_factory = sqlite3.Row

        trial_row = conn.execute(
            "SELECT * FROM trials WHERE id = ? AND programme_id = ?",
            (trial_id, programme_id),
        ).fetchone()
        if trial_row is None:
            conn.close()
            return None

        result = dict(trial_row)
        result["archive_id"] = entry["archive_id"]

        # Observations for this trial
        result["observations"] = [dict(r) for r in conn.execute(
            "SELECT * FROM observations WHERE trial_id = ?", (trial_id,)
        ).fetchall()]

        # Bundle for this trial
        result["bundle"] = [dict(r) for r in conn.execute(
            "SELECT * FROM bundles WHERE trial_id = ?", (trial_id,)
        ).fetchall()]

        # DataRefs referenced by the bundle
        data_ref_ids: set[str] = set()
        for bundle in result["bundle"]:
            if bundle.get("data_refs_json"):
                try:
                    for ref_id in json.loads(bundle["data_refs_json"]):
                        data_ref_ids.add(ref_id)
                except Exception:
                    pass
        if data_ref_ids:
            placeholders = ",".join("?" * len(data_ref_ids))
            result["data_refs"] = [dict(r) for r in conn.execute(
                f"SELECT * FROM data_refs WHERE id IN ({placeholders})",
                tuple(data_ref_ids),
            ).fetchall()]
        else:
            result["data_refs"] = []

        # Code snippets referenced by bundle and data_refs
        code_hashes: set[str] = set()
        for bundle in result["bundle"]:
            if bundle.get("code_hash"):
                code_hashes.add(bundle["code_hash"])
            # Multi-file capture: also collect extra code hashes
            if bundle.get("code_hash_extra_json"):
                try:
                    for h in json.loads(bundle["code_hash_extra_json"]):
                        code_hashes.add(h)
                except Exception:
                    pass
        for ref in result["data_refs"]:
            if ref.get("generator_code_hash"):
                code_hashes.add(ref["generator_code_hash"])
        if code_hashes:
            placeholders = ",".join("?" * len(code_hashes))
            result["code_snippets"] = [dict(r) for r in conn.execute(
                f"SELECT * FROM code_snippets WHERE code_hash IN ({placeholders})",
                tuple(code_hashes),
            ).fetchall()]
        else:
            result["code_snippets"] = []

        # Trial artifacts (metadata only — content served via route)
        try:
            result["trial_artifacts"] = [dict(r) for r in conn.execute(
                "SELECT ta.id, ta.trial_id, ta.content_hash, ta.filename, "
                "ta.artifact_type, ta.created_at, "
                "af.size_bytes, af.compressed_size_bytes, af.content_type "
                "FROM trial_artifacts ta "
                "JOIN artifact_files af ON ta.content_hash = af.content_hash "
                "WHERE ta.trial_id = ? "
                "ORDER BY ta.filename",
                (trial_id,),
            ).fetchall()]
        except Exception:
            result["trial_artifacts"] = []  # table may not exist in old archives

        conn.close()
        return result

    def verify_archive(self, archive_id: str) -> dict[str, Any]:
        """Verify an archive's integrity.

        For sealed archives: recomputes SHA-256, compares to recorded hash.
        For open archives: verifies structural integrity (DB opens, row counts match).
        """
        info = self._get_archive_info(archive_id)
        if not info:
            return {"verified": False, "error": f"Archive not found: {archive_id}"}

        archive_path = Path(info["archive_path"])
        if not archive_path.exists():
            return {"verified": False, "error": f"Archive file not found: {archive_path}"}

        if info["sealed"]:
            # Recompute hash and compare
            h = hashlib.sha256()
            with open(archive_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            computed = "sha256:" + h.hexdigest()
            recorded = info.get("archive_hash")
            return {
                "verified": computed == recorded,
                "recorded_hash": recorded,
                "computed_hash": computed,
                "sealed": True,
            }
        else:
            # Open archive: verify DB can be opened and row counts match
            try:
                conn = sqlite3.connect(str(archive_path))
                # Check that the DB has the expected tables
                tables = [r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()]
                conn.close()
                expected_tables = {
                    "programmes", "hypotheses", "trials", "observations",
                    "beliefs", "conclusions", "bundles", "data_refs", "code_snippets"
                }
                missing = expected_tables - set(tables)
                if missing:
                    return {
                        "verified": False,
                        "error": f"Missing tables: {missing}",
                        "sealed": False,
                    }
                # Verify entry counts match registry
                entries = self.store._fetchall(
                    "SELECT * FROM archive_entries WHERE archive_id = ?",
                    (archive_id,),
                )
                if len(entries) != info["programme_count"]:
                    return {
                        "verified": False,
                        "error": "Entry count mismatch",
                        "sealed": False,
                    }
                return {"verified": True, "sealed": False}
            except Exception as e:
                return {"verified": False, "error": str(e), "sealed": False}
