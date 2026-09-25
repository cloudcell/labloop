"""Archive tool handlers — list_archives, get_archive, get_archived_programme,
verify_archive, archive_pending_programmes.

These tools provide read-only access to archived programmes. Archiving
itself is automatic (triggered by close_programme/abandon_programme).

The one exception is archive_pending_programmes, which retroactively
archives terminal programmes that were closed before the archiver was
implemented. This is a migration tool, not a regular operation.
"""

from __future__ import annotations

from typing import Annotated
from pydantic import Field

import json

from ..archive import Archiver
from ..state.store import StateStore
from ..clients.adaptor import MCPAdaptor
from .schemas import fail, ok, ArchivePendingProgrammesOut, CapturePendingArtifactsOut, GetArchiveOut, GetArchivedProgrammeOut, ListArchivesOut, VerifyArchiveOut
from mcp.types import CallToolResult


def register(mcp, store: StateStore, adaptor: MCPAdaptor, archiver: Archiver | None = None) -> None:
    """Register archive-related tools on the MCP server."""

    @mcp.tool()
    async def list_archives() -> Annotated[CallToolResult, ListArchivesOut]:
        """List all archive files (open and sealed).

        Returns a list of archives with their IDs, paths, programme counts,
        and sealed status. Sealed archives are read-only; open archives
        are still accepting new programmes.
        """
        if archiver is None:
            return fail(json.dumps({"error": "Archiving is not enabled"}))
        archives = archiver.list_archives()
        return ok({"archives": archives})

    @mcp.tool()
    async def get_archive(archive_id: Annotated[str, Field(description='ID of the target archive.')]) -> Annotated[CallToolResult, GetArchiveOut]:
        """Get details about an archive, including its programmes.

        Returns the archive metadata (path, sealed status, hash) and the
        list of programmes archived in it.
        """
        if archiver is None:
            return fail(json.dumps({"error": "Archiving is not enabled"}))
        archive = archiver.get_archive(archive_id)
        if archive is None:
            return fail(json.dumps({"error": f"Archive not found: {archive_id}"}))
        return ok({"archive": archive})

    @mcp.tool()
    async def get_archived_programme(programme_id: Annotated[str, Field(description='ID of the archived programme to read (read-only — cannot be restored).')]) -> Annotated[CallToolResult, GetArchivedProgrammeOut]:
        """Get an archived programme's details from its archive file.

        Returns the programme metadata, hypotheses, trials, beliefs, and
        conclusions from the archive SQLite file. The programme is read-only
        — it cannot be modified or restored to the live DB.
        """
        if archiver is None:
            return fail(json.dumps({"error": "Archiving is not enabled"}))
        result = archiver.get_archived_programme(programme_id)
        if result is None:
            return fail(json.dumps({"error": f"Archived programme not found: {programme_id}"}))
        return ok(result)

    @mcp.tool()
    async def verify_archive(archive_id: Annotated[str, Field(description='ID of the target archive.')]) -> Annotated[CallToolResult, VerifyArchiveOut]:
        """Verify an archive's integrity.

        For sealed archives: recomputes SHA-256 of the .db file, compares to
        the recorded hash.

        For open archives: verifies structural integrity (DB can be opened,
        row counts match the registry).

        Returns: {verified: bool, recorded_hash, computed_hash (sealed only)}
        """
        if archiver is None:
            return fail(json.dumps({"error": "Archiving is not enabled"}))
        result = archiver.verify_archive(archive_id)
        return ok(result)

    @mcp.tool()
    async def archive_pending_programmes(
        dry_run: Annotated[bool, Field(description='When true, report what would be archived without mutating. Required — pass false to apply.')]
    ) -> Annotated[CallToolResult, ArchivePendingProgrammesOut]:
        """Archive terminal programmes that are still in the live DB.

        This is a migration tool for programmes that were closed (completed
        or abandoned) before the automatic archiver was implemented. It
        scans the live DB for terminal programmes not yet archived and
        archives them.

        dry_run=true reports the scan result ({would_archive,
        would_mark_archived, skipped}) without writing anything.

        Under normal operation, this is a no-op — close_programme already
        archives automatically. This tool only catches programmes that
        slipped through (e.g. abandoned before Phase 3 was deployed).

        Returns: {archived: [...], errors: [...], skipped: int}
        """
        if archiver is None:
            return fail(json.dumps({"error": "Archiving is not enabled"}))

        # Find terminal programmes still in the live DB
        rows = store._fetchall(
            "SELECT id, status FROM programmes WHERE status IN ('completed', 'abandoned')"
        )

        # Filter out programmes already in archive_entries
        already_archived = set()
        entries = store._fetchall("SELECT programme_id FROM archive_entries")
        for e in entries:
            already_archived.add(e["programme_id"])

        if dry_run:
            return ok({
                "dry_run": True,
                "would_archive": [r["id"] for r in rows if r["id"] not in already_archived],
                "would_mark_archived": [r["id"] for r in rows if r["id"] in already_archived],
            })

        archived = []
        errors = []
        skipped = 0
        marked = 0

        for row in rows:
            pid = row["id"]
            if pid in already_archived:
                # Already in archive_entries but still completed/abandoned
                # in the live DB — mark as archived (fixes pre-no-purge zombies)
                try:
                    store.update_programme_status(pid, "archived")
                    marked += 1
                except Exception as e:
                    errors.append({"programme_id": pid, "error": str(e)})
                skipped += 1
                continue

            try:
                result = archiver.archive_programme(pid)
                archived.append({
                    "programme_id": pid,
                    "status": row["status"],
                    "archive_id": result.get("archive_id"),
                    "verified": result.get("verified"),
                })
            except Exception as e:
                errors.append({
                    "programme_id": pid,
                    "error": str(e),
                })

        return ok({
            "archived": archived,
            "marked_archived": marked,
            "errors": errors,
            "skipped": skipped,
        })

    @mcp.tool()
    async def capture_pending_artifacts(
        dry_run: Annotated[bool, Field(description='When true, report which trials would be captured without mutating. Required — pass false to apply.')],
        cleanup: Annotated[bool, Field(description='Delete staging files after successful capture (the SQLite copy becomes authoritative).')] = False,
    ) -> Annotated[CallToolResult, CapturePendingArtifactsOut]:
        """Capture artifact files for trials that predate artifact capture.

        Scans all trials with an artifact_path but no rows in
        trial_artifacts. Reads files from disk and stores them in
        SQLite (gzip-compressed). Files over 50MB are skipped and
        marked oversized. Files that no longer exist on disk are
        marked lost.

        dry_run=true reports the pending trial ids without reading or
        storing anything.

        cleanup: when True, staging files are deleted after successful
        capture (the SQLite copy becomes authoritative). Default False
        keeps the originals — conservative for migration of old trials.

        Under normal operation this is a no-op — _finalize_trial
        already captures artifacts automatically. This tool only
        catches trials that slipped through (finalized before
        artifact capture was implemented).

        Returns: {captured: [...], oversized: [...], lost: [...],
                  trials_scanned: int, trials_with_existing: int}
        """
        # Find all trials with an artifact_path
        rows = store._fetchall(
            "SELECT id, artifact_path FROM trials WHERE artifact_path IS NOT NULL"
        )

        if dry_run:
            pending = [
                r["id"] for r in rows
                if not store.list_trial_artifacts(r["id"])
            ]
            return ok({
                "dry_run": True,
                "trials_pending": pending,
                "trials_with_existing": len(rows) - len(pending),
            })

        captured = []
        oversized = []
        lost = []
        scanned = 0
        existing = 0

        for row in rows:
            tid = row["id"]
            artifact_path = row["artifact_path"]

            # Check if already captured
            existing_arts = store.list_trial_artifacts(tid)
            if existing_arts:
                existing += 1
                continue

            scanned += 1
            try:
                result = store.capture_artifacts_from_dir(
                    tid, artifact_path, cleanup_after=cleanup
                )
                captured.extend(result.get("captured", []))
                oversized.extend(result.get("oversized", []))
                lost.extend(result.get("lost", []))
            except Exception as e:
                lost.append({
                    "trial_id": tid,
                    "filename": "—",
                    "reason": str(e),
                })

        return ok({
            "trials_scanned": scanned,
            "trials_with_existing": existing,
            "captured": captured,
            "oversized": oversized,
            "lost": lost,
        })
