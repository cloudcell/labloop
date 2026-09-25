"""Tests for Phase 3: automatic, batched archiving.

Tests that:
- close_programme triggers archiving automatically
- the programme is removed from the live DB
- the programme exists in the archive DB
- archive_registry and archive_entries are populated
- batch_size controls when archives are sealed
- sealed archives are read-only and hash-verified
- verify_archive works for both open and sealed archives
- only terminal programmes can be archived
- archives are self-contained (include code_snippets)
- archived programmes are reachable via /programme/{id} redirect
"""

import json
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from ml_episteme_mcp.archive import ArchiveConfig, Archiver
from ml_episteme_mcp.state.models import (
    Bundle,
    CodeSnippet,
    Hypothesis,
    Programme,
    ProgrammeStatus,
    Trial,
    TrialStatus,
)
from ml_episteme_mcp.state.store import StateStore


@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "test.db")
    s.connect()
    yield s
    s.close()


@pytest.fixture
def archive_dir(tmp_path):
    d = tmp_path / "archives"
    d.mkdir()
    return d


@pytest.fixture
def archiver(store, archive_dir):
    config = ArchiveConfig(enabled=True, batch_size=3, archive_dir=archive_dir)
    return Archiver(store, config)


def _create_full_programme(store, pid="prog-test"):
    """Create a programme with hypothesis, trial, bundle, and code snippet."""
    store.create_programme(Programme(
        id=pid,
        goal="test goal",
        constraints={"type": "test"},
        allowed_variables=["lr"],
        budget_max_trials=10,
        budget_max_wall_time_hours=1.0,
        status=ProgrammeStatus.active,
    ))
    store.create_hypothesis(Hypothesis(
        id=f"hyp-{pid}",
        programme_id=pid,
        statement="lr affects acc",
        failure_criterion="acc < 0.5",
        variables_involved=["lr"],
    ))
    store.create_trial(Trial(
        id=f"trial-{pid}",
        programme_id=pid,
        hypothesis_id=f"hyp-{pid}",
        config_json='{"lr": 0.01}',
        status=TrialStatus.designed,
    ))
    # Create a code snippet
    store.create_code_snippet(CodeSnippet(
        code_hash="sha256:test123",
        code_text="def run_training(c): return {}",
        language="python",
        captured_at="2026-09-14T12:00:00Z",
        original_path="/tmp/train.py",
        size_bytes=30,
    ))
    store.create_bundle(Bundle(
        id=f"bundle-{pid}",
        trial_id=f"trial-{pid}",
        code_ref="/tmp/train.py",
        code_hash="sha256:test123",
        env_ref="python:3.12",
        seeds_json="[42]",
        splits_json='{"train": 0.8}',
    ))


class TestArchiveConfig:
    def test_default_config(self):
        config = ArchiveConfig()
        assert config.enabled is True
        assert config.batch_size == 10

    def test_invalid_batch_size(self):
        config = ArchiveConfig(batch_size=0)
        assert config.validate() is not None

    def test_valid_config(self):
        config = ArchiveConfig(batch_size=5)
        assert config.validate() is None


class TestArchiving:
    def test_archive_terminal_programme(self, store, archiver, archive_dir):
        """Archiving a completed programme moves it to the archive."""
        _create_full_programme(store)
        store.update_programme_status("prog-test", "completed")

        result = archiver.archive_programme("prog-test")
        assert result["archived"] is True
        assert result["verified"] is True
        assert result["row_count"] > 0

        # Programme is marked archived in live DB (no-purge: backup copy)
        prog = store.get_programme("prog-test")
        assert prog is not None
        assert prog.status.value == "archived"
        # Trials and hypotheses are still in the live DB (backup)
        assert len(store.list_trials("prog-test")) > 0
        assert len(store.list_hypotheses("prog-test")) > 0

        # Archive file exists
        archives = list(archive_dir.glob("*.db"))
        assert len(archives) == 1

    def test_archive_creates_registry_entry(self, store, archiver):
        """Archiving creates entries in archive_registry and archive_entries."""
        _create_full_programme(store)
        store.update_programme_status("prog-test", "completed")

        result = archiver.archive_programme("prog-test")
        archive_id = result["archive_id"]

        # Check registry
        row = store._fetchone(
            "SELECT * FROM archive_registry WHERE archive_id = ?",
            (archive_id,),
        )
        assert row is not None
        assert row["programme_count"] == 1
        assert row["sealed"] == 0

        # Check entries
        entry = store._fetchone(
            "SELECT * FROM archive_entries WHERE programme_id = ?",
            ("prog-test",),
        )
        assert entry is not None
        assert entry["archive_id"] == archive_id
        assert entry["verified"] == 1

    def test_archive_rejects_active_programme(self, store, archiver):
        """Cannot archive an active programme."""
        _create_full_programme(store)
        # Don't close it — still active

        with pytest.raises(RuntimeError, match="not terminal"):
            archiver.archive_programme("prog-test")

        # Programme is still in the live DB
        assert store.get_programme("prog-test") is not None

    def test_archive_rejects_nonexistent(self, store, archiver):
        with pytest.raises(RuntimeError, match="not found"):
            archiver.archive_programme("nonexistent")

    def test_archive_includes_code_snippets(self, store, archiver, archive_dir):
        """The archive DB contains code_snippets (self-contained)."""
        _create_full_programme(store)
        store.update_programme_status("prog-test", "completed")

        result = archiver.archive_programme("prog-test")
        archive_path = Path(result["archive_path"])

        conn = sqlite3.connect(str(archive_path))
        snippets = conn.execute("SELECT * FROM code_snippets").fetchall()
        conn.close()

        assert len(snippets) == 1
        assert snippets[0][0] == "sha256:test123"  # code_hash

    def test_archive_disabled(self, store, archive_dir):
        """When disabled, archiving is a no-op."""
        config = ArchiveConfig(enabled=False, archive_dir=archive_dir)
        archiver = Archiver(store, config)

        _create_full_programme(store)
        store.update_programme_status("prog-test", "completed")

        result = archiver.archive_programme("prog-test")
        assert result["archived"] is False
        # Programme is still in the live DB
        assert store.get_programme("prog-test") is not None


class TestBatching:
    def test_batch_size_seals_archive(self, store, archive_dir):
        """When batch_size is reached, the archive is sealed."""
        config = ArchiveConfig(enabled=True, batch_size=2, archive_dir=archive_dir)
        archiver = Archiver(store, config)

        # Archive 2 programmes (reaches batch_size=2)
        for i in range(2):
            pid = f"prog-{i}"
            _create_full_programme(store, pid)
            store.update_programme_status(pid, "completed")
            result = archiver.archive_programme(pid)
            assert result["archived"] is True

        # The archive should be sealed after the 2nd programme
        archives = archiver.list_archives()
        assert len(archives) == 1
        assert archives[0]["sealed"] == 1
        assert archives[0]["programme_count"] == 2
        assert archives[0]["archive_hash"] is not None

    def test_new_archive_after_seal(self, store, archive_dir):
        """After sealing, a new archive is created for the next programme."""
        config = ArchiveConfig(enabled=True, batch_size=2, archive_dir=archive_dir)
        archiver = Archiver(store, config)

        for i in range(3):
            pid = f"prog-{i}"
            _create_full_programme(store, pid)
            store.update_programme_status(pid, "completed")
            archiver.archive_programme(pid)

        archives = archiver.list_archives()
        assert len(archives) == 2
        # First is sealed (2 programmes), second is open (1 programme)
        sealed = [a for a in archives if a["sealed"]]
        open_archives = [a for a in archives if not a["sealed"]]
        assert len(sealed) == 1
        assert len(open_archives) == 1
        assert sealed[0]["programme_count"] == 2
        assert open_archives[0]["programme_count"] == 1

    def test_sealed_archive_is_readonly(self, store, archive_dir):
        """Sealed archive files are read-only (0o444)."""
        config = ArchiveConfig(enabled=True, batch_size=1, archive_dir=archive_dir)
        archiver = Archiver(store, config)

        _create_full_programme(store)
        store.update_programme_status("prog-test", "completed")
        result = archiver.archive_programme("prog-test")

        archive_path = Path(result["archive_path"])
        # File should be read-only
        assert not os.access(archive_path, os.W_OK)


class TestVerifyArchive:
    def test_verify_open_archive(self, store, archiver):
        """verify_archive works on open archives."""
        _create_full_programme(store)
        store.update_programme_status("prog-test", "completed")
        result = archiver.archive_programme("prog-test")

        verify_result = archiver.verify_archive(result["archive_id"])
        assert verify_result["verified"] is True
        assert verify_result["sealed"] is False

    def test_verify_sealed_archive(self, store, archive_dir):
        """verify_archive recomputes hash for sealed archives."""
        config = ArchiveConfig(enabled=True, batch_size=1, archive_dir=archive_dir)
        archiver = Archiver(store, config)

        _create_full_programme(store)
        store.update_programme_status("prog-test", "completed")
        result = archiver.archive_programme("prog-test")

        verify_result = archiver.verify_archive(result["archive_id"])
        assert verify_result["verified"] is True
        assert verify_result["sealed"] is True
        assert verify_result["recorded_hash"] == verify_result["computed_hash"]

    def test_verify_nonexistent_archive(self, store, archiver):
        result = archiver.verify_archive("nonexistent")
        assert result["verified"] is False


class TestGetArchivedProgramme:
    def test_get_archived_programme(self, store, archiver):
        """get_archived_programme reads from the archive DB."""
        _create_full_programme(store)
        store.update_programme_status("prog-test", "completed")
        archiver.archive_programme("prog-test")

        result = archiver.get_archived_programme("prog-test")
        assert result is not None
        assert result["id"] == "prog-test"
        assert result["goal"] == "test goal"
        assert len(result["hypotheses"]) == 1
        assert len(result["trials"]) == 1

    def test_get_nonexistent_archived_programme(self, store, archiver):
        assert archiver.get_archived_programme("nonexistent") is None


class TestListArchives:
    def test_list_empty(self, store, archiver):
        """No archives when nothing has been archived."""
        assert archiver.list_archives() == []

    def test_list_after_archiving(self, store, archiver):
        _create_full_programme(store)
        store.update_programme_status("prog-test", "completed")
        archiver.archive_programme("prog-test")

        archives = archiver.list_archives()
        assert len(archives) == 1
        assert archives[0]["programme_count"] == 1


class TestArchivedProgrammeRedirect:
    """Tests that /programme/{id} redirects to the archive view when
    the programme is archived (not in the live DB).
    """

    def test_archived_programme_redirects(self, store, archiver):
        """The observability GUI redirects /programme/{id} to the archive
        view when the programme is archived.
        """
        from starlette.testclient import TestClient
        from ml_episteme_mcp.observability.server import create_observability_app

        _create_full_programme(store)
        store.update_programme_status("prog-test", "completed")
        archiver.archive_programme("prog-test")

        # Programme is marked archived in the live DB (no-purge)
        prog = store.get_programme("prog-test")
        assert prog is not None
        assert prog.status.value == "archived"

        # And the archive entry exists
        entry = store._fetchone(
            "SELECT * FROM archive_entries WHERE programme_id = ?",
            ("prog-test",),
        )
        assert entry is not None

        # The GUI shows the live page with an archive banner (no-purge)
        app = create_observability_app(store, archiver=archiver)
        client = TestClient(app)
        response = client.get("/programme/prog-test", follow_redirects=False)
        assert response.status_code == 200
        assert "archived" in response.text.lower()
        assert "/archive/" in response.text

    def test_nonexistent_programme_still_404(self, store, archiver):
        """A programme that doesn't exist anywhere returns 404, not a redirect."""
        from starlette.testclient import TestClient
        from ml_episteme_mcp.observability.server import create_observability_app

        app = create_observability_app(store, archiver=archiver)
        client = TestClient(app)
        response = client.get("/programme/nonexistent", follow_redirects=False)
        assert response.status_code == 404


class TestArchivedProgrammeArtifacts:
    """Tests that the archived programme view shows all artifacts with links."""

    def test_archived_programme_has_trial_links(self, store, archiver):
        """The archived programme view has links to trial detail pages."""
        from starlette.testclient import TestClient
        from ml_episteme_mcp.observability.server import create_observability_app

        _create_full_programme(store)
        store.update_programme_status("prog-test", "completed")
        archiver.archive_programme("prog-test")

        app = create_observability_app(store, archiver=archiver)
        client = TestClient(app)

        # Get the archive ID
        entry = store._fetchone(
            "SELECT * FROM archive_entries WHERE programme_id = ?",
            ("prog-test",),
        )
        archive_id = entry["archive_id"]

        # Get the archived programme page
        response = client.get(f"/archive/{archive_id}/programme/prog-test")
        assert response.status_code == 200
        html = response.text

        # Should have a link to the trial detail page
        assert f"/archive/{archive_id}/programme/prog-test/trial/trial-prog-test" in html

    def test_archived_trial_detail_page(self, store, archiver):
        """The archived trial detail page shows config, bundle, and code."""
        from starlette.testclient import TestClient
        from ml_episteme_mcp.observability.server import create_observability_app

        _create_full_programme(store)
        store.update_programme_status("prog-test", "completed")
        archiver.archive_programme("prog-test")

        app = create_observability_app(store, archiver=archiver)
        client = TestClient(app)

        entry = store._fetchone(
            "SELECT * FROM archive_entries WHERE programme_id = ?",
            ("prog-test",),
        )
        archive_id = entry["archive_id"]

        # Get the archived trial detail page
        response = client.get(
            f"/archive/{archive_id}/programme/prog-test/trial/trial-prog-test"
        )
        assert response.status_code == 200
        html = response.text

        # Should show the trial config
        assert "lr" in html
        # Should show the bundle
        assert "bundle-prog-test" in html
        # Should show the code snippet
        assert "sha256:test123" in html
        # Should show the code text
        assert "def run_training" in html

    def test_archived_programme_shows_all_sections(self, store, archiver):
        """The archived programme view shows all artifact sections."""
        from starlette.testclient import TestClient
        from ml_episteme_mcp.observability.server import create_observability_app

        _create_full_programme(store)
        store.update_programme_status("prog-test", "completed")
        archiver.archive_programme("prog-test")

        app = create_observability_app(store, archiver=archiver)
        client = TestClient(app)

        entry = store._fetchone(
            "SELECT * FROM archive_entries WHERE programme_id = ?",
            ("prog-test",),
        )
        archive_id = entry["archive_id"]

        response = client.get(f"/archive/{archive_id}/programme/prog-test")
        html = response.text

        # All sections should be present
        assert "Hypotheses" in html
        assert "Trials" in html
        assert "Bundles" in html
        assert "Code Snippets" in html
        # Code snippet content should be visible
        assert "sha256:test123" in html


class TestSearchAcrossArchives:
    """Tests that the search endpoint searches both live and archived programmes."""

    def test_search_finds_archived_programme(self, store, archiver):
        """The /search endpoint returns archived programmes matching the query."""
        from starlette.testclient import TestClient
        from ml_episteme_mcp.observability.server import create_observability_app

        _create_full_programme(store)
        store.update_programme_status("prog-test", "completed")
        archiver.archive_programme("prog-test")

        app = create_observability_app(store, archiver=archiver)
        client = TestClient(app)

        # Search for the archived programme by its goal
        response = client.get("/search?q=test%20goal")
        assert response.status_code == 200
        html = response.text
        # Should contain a link to the archived programme
        assert "/archive/" in html
        assert "prog-test" in html
        assert "Archived" in html

    def test_search_finds_live_programme(self, store, archiver):
        """The /search endpoint returns live programmes matching the query."""
        from starlette.testclient import TestClient
        from ml_episteme_mcp.observability.server import create_observability_app

        _create_full_programme(store)

        app = create_observability_app(store, archiver=archiver)
        client = TestClient(app)

        response = client.get("/search?q=test%20goal")
        assert response.status_code == 200
        html = response.text
        # Should contain a link to the live programme
        assert "/programme/prog-test" in html

    def test_search_empty_query_returns_empty(self, store, archiver):
        """Empty search query returns empty HTML."""
        from starlette.testclient import TestClient
        from ml_episteme_mcp.observability.server import create_observability_app

        app = create_observability_app(store, archiver=archiver)
        client = TestClient(app)

        response = client.get("/search?q=")
        assert response.status_code == 200
        assert response.text == ""

    def test_search_no_matches(self, store, archiver):
        """Search with no matches returns 'No programmes found.'"""
        from starlette.testclient import TestClient
        from ml_episteme_mcp.observability.server import create_observability_app

        app = create_observability_app(store, archiver=archiver)
        client = TestClient(app)

        response = client.get("/search?q=nonexistent")
        assert response.status_code == 200
        assert "No programmes found" in response.text


class TestArchivePending:
    """Tests for the archive_pending_programmes migration tool.

    Programmes that were closed (completed or abandoned) before the
    archiver was implemented should be retroactively archivable.
    """

    def test_archives_abandoned_programme(self, store, archiver):
        """An abandoned programme still in the live DB gets archived."""
        _create_full_programme(store)
        store.update_programme_status("prog-test", "abandoned")

        # Not archived yet
        entry = store._fetchone(
            "SELECT * FROM archive_entries WHERE programme_id = ?",
            ("prog-test",),
        )
        assert entry is None

        # Run the migration via the archiver directly
        # (the tool wraps this logic)
        rows = store._fetchall(
            "SELECT id, status FROM programmes WHERE status IN ('completed', 'abandoned')"
        )
        already = {e["programme_id"] for e in store._fetchall(
            "SELECT programme_id FROM archive_entries"
        )}

        for row in rows:
            if row["id"] not in already:
                archiver.archive_programme(row["id"])

        # Now it should be archived
        entry = store._fetchone(
            "SELECT * FROM archive_entries WHERE programme_id = ?",
            ("prog-test",),
        )
        assert entry is not None
        prog = store.get_programme("prog-test")
        assert prog is not None
        assert prog.status.value == "archived"  # marked archived, not purged

    def test_skips_already_archived(self, store, archiver):
        """Programmes already in archive_entries are skipped."""
        _create_full_programme(store)
        store.update_programme_status("prog-test", "completed")
        archiver.archive_programme("prog-test")  # archive it first

        # Run the migration logic again
        rows = store._fetchall(
            "SELECT id, status FROM programmes WHERE status IN ('completed', 'abandoned')"
        )
        already = {e["programme_id"] for e in store._fetchall(
            "SELECT programme_id FROM archive_entries"
        )}

        skipped = 0
        for row in rows:
            if row["id"] in already:
                skipped += 1

        # prog-test is already archived, so it's not in the live DB anymore
        # and there are no terminal programmes left to archive
        assert skipped == 0  # nothing to skip because it's already gone

    def test_archives_mixed_completed_and_abandoned(self, store, archiver):
        """Both completed and abandoned programmes get archived."""
        # Create two programmes
        _create_full_programme(store, "prog-completed")
        _create_full_programme(store, "prog-abandoned")

        store.update_programme_status("prog-completed", "completed")
        store.update_programme_status("prog-abandoned", "abandoned")

        # Run the migration
        rows = store._fetchall(
            "SELECT id, status FROM programmes WHERE status IN ('completed', 'abandoned')"
        )
        already = {e["programme_id"] for e in store._fetchall(
            "SELECT programme_id FROM archive_entries"
        )}

        for row in rows:
            if row["id"] not in already:
                archiver.archive_programme(row["id"])

        # Both should be archived
        for pid in ("prog-completed", "prog-abandoned"):
            entry = store._fetchone(
                "SELECT * FROM archive_entries WHERE programme_id = ?",
                (pid,),
            )
            assert entry is not None
            prog = store.get_programme(pid)
            assert prog is not None
            assert prog.status.value == "archived"  # marked, not purged


class TestRepairArchivedProgrammes:
    """Startup repair: fixes migration corruption and restores purged
    programmes so archived programmes stay visible/consistent in the
    live DB (backup copy per the no-purge invariant)."""

    def test_repair_fixes_status_corruption(self, store, archiver):
        """Simulated migration corruption: status reset to 'active',
        created_at clobbered with 'archived'. Repair restores both."""
        _create_full_programme(store)
        store.update_programme_status("prog-test", "completed")
        archiver.archive_programme("prog-test")

        # Simulate the VALID_STATUSES bug: restart migration would do this
        store._write(
            "UPDATE programmes SET status = 'active', created_at = 'archived' "
            "WHERE id = 'prog-test'"
        )
        prog = store.get_programme("prog-test")
        assert prog.status.value == "active"

        result = archiver.repair_archived_programmes()
        assert "prog-test" in result["repaired"]
        assert result["errors"] == []

        prog = store.get_programme("prog-test")
        assert prog.status.value == "archived"
        assert prog.created_at != "archived"  # real timestamp restored

    def test_repair_restores_purged_programme(self, store, archiver):
        """Pre-no-purge archiver deleted live rows. Repair restores the
        full row set from the archive DB (backup copy invariant)."""
        _create_full_programme(store)
        store.update_programme_status("prog-test", "completed")
        archiver.archive_programme("prog-test")

        # Simulate the old purge: delete all live rows for the programme
        store._write("DELETE FROM observations WHERE trial_id IN "
                     "(SELECT id FROM trials WHERE programme_id = 'prog-test')")
        store._write("DELETE FROM bundles WHERE trial_id IN "
                     "(SELECT id FROM trials WHERE programme_id = 'prog-test')")
        store._write("DELETE FROM trials WHERE programme_id = 'prog-test'")
        store._write("DELETE FROM hypotheses WHERE programme_id = 'prog-test'")
        store._write("DELETE FROM programmes WHERE id = 'prog-test'")
        assert store.get_programme("prog-test") is None

        result = archiver.repair_archived_programmes()
        assert "prog-test" in result["restored"]
        assert result["errors"] == []

        prog = store.get_programme("prog-test")
        assert prog is not None
        assert prog.status.value == "archived"
        assert len(store.list_trials("prog-test")) > 0
        assert len(store.list_hypotheses("prog-test")) > 0

    def test_repair_is_idempotent(self, store, archiver):
        """Running repair twice is a no-op the second time."""
        _create_full_programme(store)
        store.update_programme_status("prog-test", "completed")
        archiver.archive_programme("prog-test")

        first = archiver.repair_archived_programmes()
        assert first["repaired"] == [] and first["restored"] == []
        second = archiver.repair_archived_programmes()
        assert second == {"repaired": [], "restored": [], "errors": []}

    def test_migrate_does_not_corrupt_archived_status(self, store, tmp_path):
        """Regression: _migrate must not touch status='archived' rows.

        The old VALID_STATUSES set lacked 'archived', so every reconnect
        reset archived programmes to 'active'. Reconnecting must now be
        a no-op for them.
        """
        _create_full_programme(store)
        store.update_programme_status("prog-test", "completed")

        arch = Archiver(store, ArchiveConfig(
            enabled=True, batch_size=3, archive_dir=tmp_path / "arch"
        ))
        arch.archive_programme("prog-test")

        # Simulate a reconnect by running _migrate's rotation fix again
        # on a row with status='archived'
        row = store._fetchone(
            "SELECT status, created_at FROM programmes WHERE id = 'prog-test'"
        )
        assert row["status"] == "archived"
        assert row["created_at"] != "archived"

        # Directly re-run the migration block (as connect() would)
        store._migrate()

        row = store._fetchone(
            "SELECT status, created_at FROM programmes WHERE id = 'prog-test'"
        )
        assert row["status"] == "archived"
        assert row["created_at"] != "archived"


class TestTrialShortcut:
    """/trial/{id} resolves the programme from the globally-unique
    trial ID and redirects to the canonical nested URL."""

    def _mk_trial(self, store, pid="prog-x", tid="trial-x"):
        store.create_programme(Programme(
            id=pid, goal="g", constraints={}, allowed_variables=["x"],
            budget_max_trials=5, budget_max_wall_time_hours=1.0,
            status=ProgrammeStatus.active,
        ))
        store.create_hypothesis(Hypothesis(
            id=f"hyp-{pid}", programme_id=pid,
            statement="s", failure_criterion="f", variables_involved=["x"],
        ))
        store.create_trial(Trial(
            id=tid, programme_id=pid, hypothesis_id=f"hyp-{pid}",
            config_json="{}", status=TrialStatus.designed,
        ))

    def test_redirects_to_nested_url(self, store):
        from starlette.testclient import TestClient
        from ml_episteme_mcp.observability.server import create_observability_app

        self._mk_trial(store)
        app = create_observability_app(store)
        client = TestClient(app, follow_redirects=False)

        r = client.get("/trial/trial-x")
        assert r.status_code in (302, 307)
        assert r.headers["location"] == "/programme/prog-x/trial/trial-x"

    def test_archived_programme_redirects_to_archive(self, store, archiver):
        from starlette.testclient import TestClient
        from ml_episteme_mcp.observability.server import create_observability_app

        self._mk_trial(store, "prog-arc", "trial-arc")
        store.update_programme_status("prog-arc", "completed")
        result = archiver.archive_programme("prog-arc")
        aid = result["archive_id"]

        app = create_observability_app(store, archiver=archiver)
        client = TestClient(app, follow_redirects=False)

        r = client.get("/trial/trial-arc")
        assert r.status_code in (302, 307)
        assert r.headers["location"] == (
            f"/archive/{aid}/programme/prog-arc/trial/trial-arc"
        )

    def test_missing_trial_404(self, store):
        from starlette.testclient import TestClient
        from ml_episteme_mcp.observability.server import create_observability_app

        app = create_observability_app(store)
        client = TestClient(app, follow_redirects=False)

        r = client.get("/trial/trial-nope")
        assert r.status_code == 404

    def test_health_endpoint(self, store):
        from starlette.testclient import TestClient
        from ml_episteme_mcp.observability.server import create_observability_app

        app = create_observability_app(store)
        client = TestClient(app)
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_health_endpoint_htmx_returns_pill(self, store):
        """HTMX requests get an HTML fragment for the nav status pill."""
        from starlette.testclient import TestClient
        from ml_episteme_mcp.observability.server import create_observability_app

        app = create_observability_app(store)
        client = TestClient(app)
        r = client.get("/health", headers={"HX-Request": "true"})
        assert r.status_code == 200
        assert "health-pill" in r.text
        assert "ok" in r.text

    def test_nav_has_health_pill(self, store):
        """The index page nav must include the health pill placeholder."""
        from starlette.testclient import TestClient
        from ml_episteme_mcp.observability.server import create_observability_app

        app = create_observability_app(store)
        client = TestClient(app)
        r = client.get("/")
        assert r.status_code == 200
        assert "health-pill" in r.text
        assert "hx-get=\"/health\"" in r.text.lower().replace("'", "\"")

    def test_mcp_server_health_endpoint(self, store):
        """The MCP HTTP server must expose /health for liveness probes."""
        from starlette.testclient import TestClient
        from ml_episteme_mcp.server import create_server

        mcp = create_server(store)
        app = mcp.streamable_http_app()
        client = TestClient(app)
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_health_endpoint_probes_mcp_server(self, store):
        """When mcp_health_url is set, /health reports combined status.

        Points the probe at a port nothing is listening on, so the MCP
        server is 'down' from the GUI's perspective. The pill should
        reflect that, not just the GUI's own liveness.
        """
        from starlette.testclient import TestClient
        from ml_episteme_mcp.observability.server import create_observability_app

        app = create_observability_app(
            store, mcp_health_url="http://127.0.0.1:1/health",
        )
        client = TestClient(app)
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "down"
        assert r.json()["mcp"] == "down"

        # HTMX variant gets the down pill
        r = client.get("/health", headers={"HX-Request": "true"})
        assert r.status_code == 200
        assert "health-pill down" in r.text

    def test_log_tool_args_logs_raw_arguments(self, store, caplog):
        """--log-tool-args wraps call_tool to log raw arguments before
        validation — the SDK's 'rejected arguments' log names only the
        failing fields, not the values."""
        import asyncio
        import logging
        from ml_episteme_mcp.server import create_server

        mcp = create_server(store, log_tool_args=True)
        with caplog.at_level(logging.INFO, logger="ml_episteme_mcp.server"):
            asyncio.run(mcp.call_tool("list_active_programmes", {}))
        assert any(
            "call_tool list_active_programmes arguments={}" in r.message
            for r in caplog.records
        )

    def test_log_tool_args_off_by_default(self, store, caplog):
        """Without log_tool_args, call_tool arguments are not logged."""
        import asyncio
        import logging
        from ml_episteme_mcp.server import create_server

        mcp = create_server(store)
        with caplog.at_level(logging.INFO, logger="ml_episteme_mcp.server"):
            asyncio.run(mcp.call_tool("list_active_programmes", {}))
        assert not any("call_tool" in r.message for r in caplog.records)


class TestProgrammeSortUI:
    """Sort and status-filter controls on the index page must be real
    navigation links — filtering is server-side (a client-side filter
    over a server-paginated grid can only hide the current page)."""

    def _app(self, store):
        from ml_episteme_mcp.observability.server import create_observability_app
        return create_observability_app(store)

    def test_sort_links_are_real_links(self, store):
        import re
        from starlette.testclient import TestClient

        store.create_programme(Programme(
            id="prog-s", goal="g", constraints={}, allowed_variables=["x"],
            budget_max_trials=5, budget_max_wall_time_hours=1.0,
            status=ProgrammeStatus.active,
        ))
        client = TestClient(self._app(store))
        html = client.get("/").text

        # Sort links: real hrefs, no data-filter attributes anywhere
        for sort in ("activity_desc", "activity_asc", "created_desc"):
            m = re.search(
                rf'<a class="filter-tab[^"]*" href="/\?sort={sort}"', html
            )
            assert m is not None, f"missing sort link for {sort}"
            assert "data-filter" not in m.group(0)
        assert "data-filter" not in html

    def test_status_filter_is_server_side(self, store):
        """?status=X must filter the rendered rows, not just hide cards."""
        from starlette.testclient import TestClient

        for pid, st in (("prog-act", ProgrammeStatus.active),
                        ("prog-arc", ProgrammeStatus.archived)):
            store.create_programme(Programme(
                id=pid, goal=f"g {pid}", constraints={},
                allowed_variables=["x"], budget_max_trials=5,
                budget_max_wall_time_hours=1.0, status=st,
            ))

        client = TestClient(self._app(store))
        html = client.get("/?status=active").text
        assert "prog-act" in html
        assert "prog-arc" not in html
        # the active tab is marked and the URL carries both params
        assert 'filter-tab active" href="/?sort=created_desc&status=active"' in html

        html = client.get("/?status=archived").text
        assert "prog-arc" in html
        assert "prog-act" not in html

    def test_sort_param_changes_order(self, store):
        from starlette.testclient import TestClient

        for pid, ts in (("prog-a", "2026-09-14T00:00:00+00:00"),
                        ("prog-b", "2026-09-14T10:00:00+00:00")):
            store.create_programme(Programme(
                id=pid, goal=f"g {pid}", constraints={}, allowed_variables=["x"],
                budget_max_trials=5, budget_max_wall_time_hours=1.0,
                status=ProgrammeStatus.active,
            ))
            store._write(
                "UPDATE programmes SET created_at = ? WHERE id = ?",
                (ts, pid),
            )

        client = TestClient(self._app(store))
        r = client.get("/?sort=activity_asc")
        assert r.status_code == 200
        # prog-a (older) must appear before prog-b in the HTML
        assert r.text.index("prog-a") < r.text.index("prog-b")

        r = client.get("/?sort=activity_desc")
        assert r.text.index("prog-b") < r.text.index("prog-a")


def test_archive_into_pre_phase0_open_archive(store, archiver, archive_dir):
    """Regression: an open archive created before Phase 0 lacks
    candidate_version_id and the lineage tables. Appending a new
    programme must upgrade the archive schema, not crash.
    (Production failure: 'table programmes has no column named
    candidate_version_id' on startup auto-archive.)"""
    # Simulate an open archive written before the Phase-0 schema existed
    old = archive_dir / "archive-20260914T0000Z-old.db"
    archiver._create_archive_schema(old)
    conn = sqlite3.connect(str(old))
    conn.execute("ALTER TABLE programmes DROP COLUMN candidate_version_id")
    conn.execute("DROP TABLE evaluation_contracts")
    conn.execute("DROP TABLE candidate_versions")
    conn.commit()
    conn.close()
    store._write(
        "INSERT INTO archive_registry "
        "(archive_id, archive_path, created_at, batch_size, programme_count, sealed) "
        "VALUES (?,?,?,?,?,0)",
        ("archive-old", str(old), "2026-09-14T00:00:00Z", 10, 1),
    )

    _create_full_programme(store)
    store.update_programme_status("prog-test", "completed")
    result = archiver.archive_programme("prog-test")

    assert result["archived"] is True
    assert result["verified"] is True
    # The old archive was upgraded in place — column restored
    conn = sqlite3.connect(str(old))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(programmes)")}
    conn.close()
    assert "candidate_version_id" in cols
