"""SQLite-backed state store for durable continuants.

The SQLite file is the **carrier** (a material entity, §2.1.1 of the ontology).
The rows are the **information content** (generically dependent continuants).
The store never conflates them: "the database" means the carrier; "the
state" means the content (b-01 §2.4).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, TypeVar

from .models import (
    Belief,
    Bundle,
    CandidateVersion,
    CodeSnippet,
    Conclusion,
    DataRef,
    EvaluationContract,
    Hypothesis,
    Observation,
    Programme,
    PromotionDecision,
    Trial,
    verify_mece,
)

T = TypeVar("T")


def _find_local_imports(
    tree: "ast.AST", base_dir: Path, roots: "tuple[Path, ...]" = ()
) -> list[Path]:
    """Find imports in an AST that resolve to local .py files.

    Returns a list of resolved .py file paths for local imports.
    Skips stdlib and third-party imports (they are environment deps,
    tracked by env_ref, not code deps).

    Handles:
    - import foo            → base_dir/foo.py
    - import foo.bar        → base_dir/foo/bar.py (package)
    - from foo import x     → base_dir/foo.py
    - from foo.bar import x → base_dir/foo/bar.py
    - from .foo import x    → base_dir/foo.py (relative)
    - from . import foo     → base_dir/foo.py (relative)
    """
    import ast

    local_files: list[Path] = []
    seen_paths: set[str] = set()

    # sys.path.insert(0, os.path.join(_ROOT, "contrib")) — directories a
    # file adds to the import path become resolution roots for that
    # file's imports. This is the dominant pattern for top-level
    # imports of vendored modules (import sascorer) that no
    # base_dir/roots search can see.
    extra_roots = _find_sys_path_dirs(tree, base_dir, roots)
    all_roots = (*extra_roots, *roots)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod_name = alias.name
                _try_resolve_local(mod_name, base_dir, local_files, seen_paths, all_roots)
        elif isinstance(node, ast.ImportFrom):
            if node.module is None and node.level and node.level > 0:
                # from . import foo — relative import with no module
                for alias in node.names:
                    _try_resolve_local(alias.name, base_dir, local_files, seen_paths, all_roots)
            elif node.module is not None:
                if node.level and node.level > 0:
                    # from .foo import x — relative import
                    _try_resolve_local(node.module, base_dir, local_files, seen_paths, all_roots)
                else:
                    # from foo import x — absolute import
                    _try_resolve_local(node.module, base_dir, local_files, seen_paths, all_roots)
                # from pkg import sub — the alias may itself be a
                # submodule (from src.mol import ga → src/mol/ga.py);
                # if it's a symbol the resolve just finds nothing.
                for alias in node.names:
                    _try_resolve_local(
                        f"{node.module}.{alias.name}", base_dir,
                        local_files, seen_paths, all_roots,
                    )

    return local_files


def _literal_suffix(node: "ast.AST") -> str | None:
    """Extract a joined literal path suffix from an os.path.join call or
    a bare string constant. Returns None if nothing usable."""
    import ast

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Call):
        fn = node.func
        is_join = (
            (isinstance(fn, ast.Attribute) and fn.attr == "join")
            or (isinstance(fn, ast.Name) and fn.id == "join")
        )
        if is_join:
            parts = [
                a.value for a in node.args
                if isinstance(a, ast.Constant) and isinstance(a.value, str)
            ]
            if parts:
                return "/".join(p.strip("/") for p in parts if p != "/")
    return None


def _find_sys_path_dirs(
    tree: "ast.AST", base_dir: Path, roots: "tuple[Path, ...]" = ()
) -> "tuple[Path, ...]":
    """Directories added via sys.path.insert/append in this file.

    Mirrors what Python actually does: a file that extends sys.path
    makes its inserted dirs import roots for its own imports.
    """
    import ast

    dirs: list[Path] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        is_path_mut = (
            isinstance(fn, ast.Attribute)
            and fn.attr in ("insert", "append")
            and isinstance(fn.value, ast.Attribute)
            and fn.value.attr == "path"
        )
        if not is_path_mut or not node.args:
            continue
        suffix = _literal_suffix(node.args[-1])
        if not suffix:
            continue
        for root in (base_dir, *roots):
            cand = (root / suffix)
            if cand.is_dir():
                dirs.append(cand)
                break
            # The suffix may already resolve inside a nested dir —
            # also try treating the parent chain via each root's rglob
            # only when unambiguous? No — keep it simple; ambiguous
            # sys.path edits are rare and not worth guessing.
    return tuple(dirs)


def _find_spawned_scripts(
    tree: "ast.AST", base_dir: Path, roots: "tuple[Path, ...]" = ()
) -> list[Path]:
    """Find local .py files referenced by path literals.

    Import-following cannot see the dominant runner pattern:

        RUNNER = os.path.join(ROOT, 'scripts', 'train_exec.py')
        subprocess.run([VENV_PY, RUNNER, cfg_path])

    The spawned script IS the experiment — commitment 5 requires the
    bundle to contain it. This scans string constants ending in .py
    and os.path.join(...) calls with literal parts (the leading
    non-literal anchor like ROOT is dropped and the literal suffix
    joined), resolving candidates against the referencing file's
    directory. A bare basename literal that doesn't resolve directly
    is searched under base_dir (rglob) — all matches are captured;
    capturing a file that existed is never wrong.

    Returns resolved .py paths (dedup'd, capped at 8 matches per
    literal to bound rglob over large trees).
    """
    import ast

    candidates: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.endswith(".py"):
                candidates.add(node.value)
        elif isinstance(node, ast.Call):
            fn = node.func
            is_join = (
                (isinstance(fn, ast.Attribute) and fn.attr == "join")
                or (isinstance(fn, ast.Name) and fn.id == "join")
            )
            if is_join:
                parts = [
                    a.value for a in node.args
                    if isinstance(a, ast.Constant) and isinstance(a.value, str)
                ]
                if parts:
                    joined = "/".join(p.strip("/") for p in parts if p != "/")
                    if joined.endswith(".py"):
                        candidates.add(joined)

    found: list[Path] = []
    seen: set[str] = set()
    for cand in candidates:
        p = Path(cand)
        resolved: list[Path] = []
        if p.is_absolute():
            if p.exists():
                resolved.append(p)
        else:
            for root in (base_dir, *roots):
                direct = root / cand
                if direct.exists():
                    resolved.append(direct)
            if not resolved:
                # Basename fallback only when UNAMBIGUOUS — a literal
                # matching several files can't be statically resolved to
                # the one the run used, and capturing all of them would
                # poison same-stem materialization on rerun.
                for root in (base_dir, *roots):
                    try:
                        matches = [
                            m for m in root.rglob(p.name) if m.is_file()
                        ]
                    except OSError:
                        matches = []
                    if len(matches) == 1:
                        resolved.append(matches[0])
        for r in resolved:
            key = str(r.resolve())
            if key not in seen:
                seen.add(key)
                found.append(r)
    return found


_SHARED_SCRATCH_PREFIXES = ("/tmp", "/var/tmp", "/dev/shm")


def lint_shared_paths(source: str, filename: str) -> list[dict]:
    """Scan Python source for hardcoded shared-scratch path literals.

    Trials may execute concurrently; a fixed path under /tmp, /var/tmp,
    or /dev/shm lets one trial clobber another's inputs/outputs (the
    executor contract requires per-trial paths — $ML_SCI_ARTIFACT_DIR,
    tempfile, or $ML_SCI_TRIAL_ID-derived names).

    Returns advisory warnings (never blocks capture):
    [{"file": ..., "line": ..., "literal": ..., "warning": ...}]
    """
    import ast

    warnings: list[dict] = []
    try:
        tree = ast.parse(source, filename=filename)
    except Exception:
        return warnings
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and any(
                node.value == p or node.value.startswith(p + "/")
                for p in _SHARED_SCRATCH_PREFIXES
            )
        ):
            warnings.append({
                "file": filename,
                "line": getattr(node, "lineno", None),
                "literal": node.value,
                "warning": (
                    "hardcoded shared-scratch path — concurrent trials "
                    "can clobber each other; use $ML_SCI_ARTIFACT_DIR, "
                    "tempfile (TMPDIR is per-trial), or a "
                    "$ML_SCI_TRIAL_ID-derived name"
                ),
            })
    return warnings


def _try_resolve_local(
    mod_name: str,
    base_dir: Path,
    out: list[Path],
    seen: set[str],
    roots: "tuple[Path, ...]" = (),
) -> None:
    """Try to resolve a module name to a local .py file.

    Checks:
    1. base_dir/mod_name.py (direct module)
    2. base_dir/mod_name/__init__.py (package)
    3. base_dir/mod_name split on dots for nested modules
    """
    parts = mod_name.replace(".", "/")
    # Scripts spawned from the entry file's project root import
    # root-relative (from src.x import y) even though they live in a
    # subdirectory — the entry dir is a legitimate resolution root.
    for root in (base_dir, *roots):
        for cand in (root / f"{parts}.py", root / parts / "__init__.py"):
            if cand.exists() and cand.is_file():
                if str(cand) not in seen:
                    seen.add(str(cand))
                    out.append(cand)
                # Importing a.b.c executes every package __init__.py
                # along the path (a/__init__.py, a/b/__init__.py) —
                # they are executed code and belong in the bundle.
                try:
                    rel = cand.relative_to(root).parent
                except ValueError:
                    rel = None
                if rel is not None:
                    for i in range(1, len(rel.parts) + 1):
                        init = root.joinpath(*rel.parts[:i]) / "__init__.py"
                        if init.is_file() and str(init) not in seen:
                            seen.add(str(init))
                            out.append(init)
                return


# --- State transition maps (b-01 §2.5) ---

_TRIAL_TRANSITIONS: dict[str, set[str]] = {
    "designed": {"running", "abandoned"},
    "running": {"completed", "failed", "retryable"},
    "completed": set(),    # terminal
    "failed": set(),       # terminal
    "retryable": set(),    # terminal — infrastructure failure, can rerun
    "abandoned": set(),   # terminal — designed trial never run, abandoned with programme
}

# Keys that constitute an executor record — a verdict emitted by the
# execution layer (outer exit/status, captured streams, or the returned
# payload). 'corrections' alone is bookkeeping, not execution evidence.
_EXECUTOR_RECORD_KEYS = frozenset({
    "exit_code", "status", "stdout", "stderr",
    "timed_out", "error", "metrics", "output",
})


def _has_executor_record(raw: str | None) -> bool:
    """Whether executor_output_json evidences an actual execution —
    a parseable non-empty object carrying a verdict key. An absent,
    empty, or corrections-only record does not."""
    if not raw:
        return False
    try:
        out = json.loads(raw)
    except (ValueError, TypeError):
        return False
    return isinstance(out, dict) and any(
        k in out for k in _EXECUTOR_RECORD_KEYS
    )

_HYPOTHESIS_TRANSITIONS: dict[str, set[str]] = {
    "proposed": {"under_test", "abandoned"},
    "under_test": {"accepted", "rejected", "inconclusive", "abandoned"},
    "accepted": set(),      # terminal
    "rejected": set(),      # terminal
    "inconclusive": set(),  # terminal
    "abandoned": set(),     # terminal
}

_PROGRAMME_TRANSITIONS: dict[str, set[str]] = {
    "active": {"completed", "abandoned"},
    "completed": {"archived"},    # → archived (copied to archive DB)
    "abandoned": {"archived"},    # → archived (copied to archive DB)
    "archived": set(),            # terminal — live DB copy is a backup
}


def _validate_transition(
    entity: str,
    from_status: str,
    to_status: str,
    allowed: dict[str, set[str]],
) -> None:
    """Raise ValueError if the transition is illegal (b-01 §2.5)."""
    legal = allowed.get(from_status)
    if legal is None:
        raise ValueError(
            f"Illegal {entity} status transition: unknown source status "
            f"'{from_status}'"
        )
    if to_status not in legal:
        dest = sorted(legal) if legal else "(terminal — no transitions)"
        raise ValueError(
            f"Illegal {entity} status transition: {from_status} → {to_status}. "
            f"Allowed from '{from_status}': {dest}"
        )


# --- Schema ---


SCHEMA = """
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

CREATE TABLE IF NOT EXISTS hypotheses (
    id TEXT PRIMARY KEY,
    programme_id TEXT NOT NULL,
    statement TEXT NOT NULL,
    failure_criterion TEXT NOT NULL,
    variables_involved_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (programme_id) REFERENCES programmes(id)
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
    created_at TEXT NOT NULL,
    FOREIGN KEY (programme_id) REFERENCES programmes(id),
    FOREIGN KEY (hypothesis_id) REFERENCES hypotheses(id)
);

CREATE TABLE IF NOT EXISTS observations (
    id TEXT PRIMARY KEY,
    trial_id TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    variance_json TEXT NOT NULL,
    spatiotemporal_region TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (trial_id) REFERENCES trials(id)
);

CREATE TABLE IF NOT EXISTS beliefs (
    id TEXT PRIMARY KEY,
    programme_id TEXT NOT NULL,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (programme_id) REFERENCES programmes(id)
);

CREATE TABLE IF NOT EXISTS conclusions (
    id TEXT PRIMARY KEY,
    hypothesis_id TEXT NOT NULL,
    programme_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    evidence_ref TEXT NOT NULL,
    evidence_summary TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (hypothesis_id) REFERENCES hypotheses(id),
    FOREIGN KEY (programme_id) REFERENCES programmes(id)
);

CREATE TABLE IF NOT EXISTS bundles (
    id TEXT PRIMARY KEY,
    trial_id TEXT NOT NULL,
    code_ref TEXT NOT NULL,
    env_ref TEXT NOT NULL,
    seeds_json TEXT NOT NULL,
    splits_json TEXT NOT NULL,
    data_refs_json TEXT,
    baseline_ref TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (trial_id) REFERENCES trials(id)
);

CREATE TABLE IF NOT EXISTS data_refs (
    id TEXT PRIMARY KEY,
    split TEXT NOT NULL,
    regime TEXT NOT NULL,
    generator_code_ref TEXT,
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

CREATE TABLE IF NOT EXISTS archive_registry (
    archive_id TEXT PRIMARY KEY,
    archive_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sealed_at TEXT,
    batch_size INTEGER NOT NULL,
    programme_count INTEGER NOT NULL DEFAULT 0,
    sealed INTEGER NOT NULL DEFAULT 0,
    archive_hash TEXT,
    archive_size_bytes INTEGER
);

CREATE TABLE IF NOT EXISTS archive_entries (
    id TEXT PRIMARY KEY,
    archive_id TEXT NOT NULL,
    programme_id TEXT NOT NULL,
    programme_goal TEXT NOT NULL,
    archived_at TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (archive_id) REFERENCES archive_registry(archive_id)
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

CREATE TABLE IF NOT EXISTS candidate_versions (
    id TEXT PRIMARY KEY,
    parent_id TEXT,
    code_artifact_digest TEXT NOT NULL,
    harness_artifact_digest TEXT,
    model_ref TEXT NOT NULL,
    search_policy_ref TEXT,
    capability_profile_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (parent_id) REFERENCES candidate_versions(id)
);

CREATE TABLE IF NOT EXISTS evaluation_contracts (
    id TEXT PRIMARY KEY,
    programme_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    metrics_json TEXT NOT NULL,
    holdouts_json TEXT,
    budget_json TEXT,
    promotion_policy_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (programme_id) REFERENCES programmes(id)
);

CREATE TABLE IF NOT EXISTS promotion_decisions (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    contract_id TEXT,
    verdict TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    rationale TEXT NOT NULL,
    decided_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (candidate_id) REFERENCES candidate_versions(id),
    FOREIGN KEY (contract_id) REFERENCES evaluation_contracts(id)
);
"""


class StateStore:
    """SQLite-backed persistence for the state layer.

    The file is the carrier; the rows are the content. Backing up the file
    preserves the carrier; the content survives because it is generically
    dependent (can be re-encoded in another carrier).
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    def __enter__(self) -> "StateStore":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def connect(self) -> None:
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # Concurrency pragmas — the standard SQLite concurrency recipe.
        # WAL: readers don't block writers; multiple concurrent readers.
        # busy_timeout: wait up to 5s on lock instead of erroring immediately.
        # synchronous=NORMAL: safe with WAL, faster than FULL.
        # foreign_keys=ON: enforce FK constraints (off by default in SQLite).
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Add columns to existing databases that predate a schema change."""
        cols = {
            r[1] for r in self._conn.execute("PRAGMA table_info(programmes)")
        }
        if "metric_direction" not in cols:
            self._conn.execute(
                "ALTER TABLE programmes ADD COLUMN metric_direction TEXT NOT NULL DEFAULT 'maximize'"
            )
            self._conn.commit()
        if "candidate_version_id" not in cols:
            self._conn.execute(
                "ALTER TABLE programmes ADD COLUMN candidate_version_id TEXT"
            )
            self._conn.commit()

        # Fix data corruption from the positional INSERT bug.
        #
        # On migrated databases, ALTER TABLE added metric_direction at the END,
        # but the old positional INSERT expected it in the MIDDLE. This caused a
        # 3-way rotation:
        #   status column      ← got metric_direction value ("minimize"/"maximize")
        #   created_at column  ← got status value ("active")
        #   metric_direction    ← got created_at value (timestamp)
        #
        # A previous 2-way swap fix made it worse by moving the timestamp into
        # status. This fix identifies each value by its type and restores all
        # three columns correctly.
        VALID_STATUSES = {"active", "completed", "abandoned", "archived"}
        VALID_DIRECTIONS = {"minimize", "maximize"}

        try:
            rows = self._conn.execute(
                "SELECT id, status, metric_direction, created_at FROM programmes"
            ).fetchall()
            for row in rows:
                status_val = row["status"]
                direction_val = row["metric_direction"]
                created_val = row["created_at"]

                # If all three are already correct, skip
                if (status_val in VALID_STATUSES
                    and direction_val in VALID_DIRECTIONS
                    and created_val not in VALID_STATUSES
                    and created_val not in VALID_DIRECTIONS):
                    continue

                # Identify each value by its type
                values = [status_val, direction_val, created_val]
                status_correct = next(
                    (v for v in values if v in VALID_STATUSES), "active"
                )
                direction_correct = next(
                    (v for v in values if v in VALID_DIRECTIONS), "maximize"
                )
                # The timestamp is the value that's neither a status nor a direction
                timestamp_correct = next(
                    (v for v in values
                     if v not in VALID_STATUSES and v not in VALID_DIRECTIONS),
                    created_val,
                )

                self._conn.execute(
                    "UPDATE programmes SET status = ?, metric_direction = ?, "
                    "created_at = ? WHERE id = ?",
                    (status_correct, direction_correct, timestamp_correct, row["id"]),
                )
            self._conn.commit()
        except Exception:
            pass  # Table doesn't exist yet or other issue — skip

        # Add duration_seconds column to trials (for wall-time budget tracking)
        trial_cols = {
            r[1] for r in self._conn.execute("PRAGMA table_info(trials)")
        }
        if "duration_seconds" not in trial_cols:
            self._conn.execute(
                "ALTER TABLE trials ADD COLUMN duration_seconds REAL"
            )
            self._conn.commit()

        # Add artifact_path column to trials (for artifact workspaces)
        if "artifact_path" not in trial_cols:
            self._conn.execute(
                "ALTER TABLE trials ADD COLUMN artifact_path TEXT"
            )
            self._conn.commit()

        # Add executor_output_json column to trials (durable record of
        # the executor result — the in-memory async cache dies on restart)
        if "executor_output_json" not in trial_cols:
            self._conn.execute(
                "ALTER TABLE trials ADD COLUMN executor_output_json TEXT"
            )
            self._conn.commit()

        # Add started_at/finished_at columns to trials (the execution
        # window — created_at is design time, not run time)
        for col in ("started_at", "finished_at"):
            if col not in trial_cols:
                self._conn.execute(
                    f"ALTER TABLE trials ADD COLUMN {col} TEXT"
                )
        self._conn.commit()

        # Add data_refs_json column to bundles (for structured data provenance)
        bundle_cols = {
            r[1] for r in self._conn.execute("PRAGMA table_info(bundles)")
        }
        if "data_refs_json" not in bundle_cols:
            self._conn.execute(
                "ALTER TABLE bundles ADD COLUMN data_refs_json TEXT"
            )
            self._conn.commit()

        # Add code_hash column to bundles (Phase 0: content-addressed code)
        if "code_hash" not in bundle_cols:
            self._conn.execute(
                "ALTER TABLE bundles ADD COLUMN code_hash TEXT"
            )
            self._conn.commit()

        # Add code_hash_extra_json column to bundles (multi-file capture)
        if "code_hash_extra_json" not in bundle_cols:
            self._conn.execute(
                "ALTER TABLE bundles ADD COLUMN code_hash_extra_json TEXT"
            )
            self._conn.commit()

        # Add generator_code_hash column to data_refs (Phase 0)
        data_ref_cols = {
            r[1] for r in self._conn.execute("PRAGMA table_info(data_refs)")
        }
        if "generator_code_hash" not in data_ref_cols:
            self._conn.execute(
                "ALTER TABLE data_refs ADD COLUMN generator_code_hash TEXT"
            )
            self._conn.commit()

        # Phase 1: FK indexes for query performance.
        # Idempotent (CREATE INDEX IF NOT EXISTS). These prevent full table
        # scans on FK lookups (list_trials(programme_id), etc.).
        self._conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_hypotheses_programme
            ON hypotheses(programme_id);
        CREATE INDEX IF NOT EXISTS idx_trials_programme
            ON trials(programme_id);
        CREATE INDEX IF NOT EXISTS idx_trials_hypothesis
            ON trials(hypothesis_id);
        CREATE INDEX IF NOT EXISTS idx_trials_status
            ON trials(status);
        CREATE INDEX IF NOT EXISTS idx_observations_trial
            ON observations(trial_id);
        CREATE INDEX IF NOT EXISTS idx_beliefs_programme
            ON beliefs(programme_id);
        CREATE INDEX IF NOT EXISTS idx_conclusions_programme
            ON conclusions(programme_id);
        CREATE INDEX IF NOT EXISTS idx_conclusions_hypothesis
            ON conclusions(hypothesis_id);
        CREATE INDEX IF NOT EXISTS idx_bundles_trial
            ON bundles(trial_id);
        CREATE INDEX IF NOT EXISTS idx_programmes_status
            ON programmes(status);
        CREATE INDEX IF NOT EXISTS idx_programmes_created
            ON programmes(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_archive_entries_archive
            ON archive_entries(archive_id);
        """)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Store not connected; call connect() first")
        return self._conn

    @property
    def state_dir(self) -> Path:
        """The directory containing the SQLite file (for artifact storage)."""
        return self.path.parent

    def _fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        """Execute a read query and return a single row as a dict.

        Holds the lock during fetch AND dict conversion to avoid
        thread-safety issues: sqlite3.Row objects reference the
        connection's internal cursor buffer, which gets corrupted
        when concurrent reads run on the same connection.
        """
        with self._lock:
            row = self.conn.execute(sql, params).fetchone()
            return dict(row) if row is not None else None

    def _fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        """Execute a read query and return all rows as dicts.

        Holds the lock during fetch AND dict conversion to avoid
        thread-safety issues: sqlite3.Row objects reference the
        connection's internal cursor buffer, which gets corrupted
        when concurrent reads run on the same connection.
        """
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def _write(self, sql: str, params: tuple = ()) -> None:
        """Execute a write statement and commit, under the write lock.

        All writes go through this method to serialize in-process writes
        and prevent 'database is locked' errors. Reads stay lock-free
        (WAL mode allows concurrent readers).

        Retries on 'database is locked' with exponential backoff, up to
        5 attempts. This handles transient locks from other connections
        (e.g. the observability GUI reading while the archiver writes).
        """
        import time as _time

        with self._lock:
            for attempt in range(5):
                try:
                    self.conn.execute(sql, params)
                    self.conn.commit()
                    return
                except sqlite3.OperationalError as e:
                    if "locked" in str(e) and attempt < 4:
                        _time.sleep(0.05 * (2 ** attempt))
                        continue
                    raise

    # --- Programme ---

    def create_programme(self, p: Programme) -> None:
        verify_mece(p)
        self._write(
            "INSERT INTO programmes "
            "(id, goal, constraints_json, allowed_variables_json, "
            "budget_max_trials, budget_max_wall_time_hours, metric_direction, "
            "candidate_version_id, status, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                p.id,
                p.goal,
                json.dumps(p.constraints),
                json.dumps(p.allowed_variables),
                p.budget_max_trials,
                p.budget_max_wall_time_hours,
                p.metric_direction,
                p.candidate_version_id,
                p.status.value,
                p.created_at,
            ),
        )

    def get_programme(self, programme_id: str) -> Programme | None:
        row = self._fetchone(
            "SELECT * FROM programmes WHERE id = ?", (programme_id,)
        )
        if row is None:
            return None
        return Programme(
            id=row["id"],
            goal=row["goal"],
            constraints=json.loads(row["constraints_json"]),
            allowed_variables=json.loads(row["allowed_variables_json"]),
            budget_max_trials=row["budget_max_trials"],
            budget_max_wall_time_hours=row["budget_max_wall_time_hours"],
            metric_direction=row["metric_direction"],
            candidate_version_id=row["candidate_version_id"],
            status=row["status"],
            created_at=row["created_at"],
        )

    # --- Hypothesis ---

    def create_hypothesis(self, h: Hypothesis) -> None:
        verify_mece(h)
        self._write(
            "INSERT INTO hypotheses VALUES (?,?,?,?,?,?,?)",
            (
                h.id,
                h.programme_id,
                h.statement,
                h.failure_criterion,
                json.dumps(h.variables_involved),
                h.status.value,
                h.created_at,
            ),
        )

    def get_hypothesis(self, hypothesis_id: str) -> Hypothesis | None:
        row = self._fetchone(
            "SELECT * FROM hypotheses WHERE id = ?", (hypothesis_id,)
        )
        if row is None:
            return None
        return Hypothesis(
            id=row["id"],
            programme_id=row["programme_id"],
            statement=row["statement"],
            failure_criterion=row["failure_criterion"],
            variables_involved=json.loads(row["variables_involved_json"]),
            status=row["status"],
            created_at=row["created_at"],
        )

    def list_hypotheses(self, programme_id: str) -> list[Hypothesis]:
        rows = self._fetchall(
            "SELECT * FROM hypotheses WHERE programme_id = ?", (programme_id,)
        )
        return [
            Hypothesis(
                id=r["id"],
                programme_id=r["programme_id"],
                statement=r["statement"],
                failure_criterion=r["failure_criterion"],
                variables_involved=json.loads(r["variables_involved_json"]),
                status=r["status"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # --- Bundle ---

    def create_bundle(self, b: Bundle) -> None:
        verify_mece(b)
        self._write(
            "INSERT INTO bundles "
            "(id, trial_id, code_ref, code_hash, code_hash_extra_json, env_ref, "
            "seeds_json, splits_json, data_refs_json, baseline_ref, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                b.id,
                b.trial_id,
                b.code_ref,
                b.code_hash,
                b.code_hash_extra_json,
                b.env_ref,
                b.seeds_json,
                b.splits_json,
                b.data_refs_json,
                b.baseline_ref,
                b.created_at,
            ),
        )

    def get_bundle(self, bundle_id: str) -> Bundle | None:
        row = self._fetchone(
            "SELECT * FROM bundles WHERE id = ?", (bundle_id,)
        )
        if row is None:
            return None
        return Bundle(
            id=row["id"],
            trial_id=row["trial_id"],
            code_ref=row["code_ref"],
            code_hash=row["code_hash"],
            code_hash_extra_json=row["code_hash_extra_json"] if "code_hash_extra_json" in row.keys() else None,
            env_ref=row["env_ref"],
            seeds_json=row["seeds_json"],
            splits_json=row["splits_json"],
            data_refs_json=row["data_refs_json"],
            baseline_ref=row["baseline_ref"],
            created_at=row["created_at"],
        )

    # --- Trial ---

    def create_trial(self, t: Trial) -> None:
        verify_mece(t)
        self._write(
            "INSERT INTO trials "
            "(id, programme_id, hypothesis_id, config_json, bundle_id, "
            "status, duration_seconds, artifact_path, executor_output_json, "
            "started_at, finished_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                t.id,
                t.programme_id,
                t.hypothesis_id,
                t.config_json,
                t.bundle_id,
                t.status.value,
                t.duration_seconds,
                t.artifact_path,
                t.executor_output_json,
                t.started_at,
                t.finished_at,
                t.created_at,
            ),
        )

    def get_trial(self, trial_id: str) -> Trial | None:
        row = self._fetchone(
            "SELECT * FROM trials WHERE id = ?", (trial_id,)
        )
        if row is None:
            return None
        return Trial(
            id=row["id"],
            programme_id=row["programme_id"],
            hypothesis_id=row["hypothesis_id"],
            config_json=row["config_json"],
            bundle_id=row["bundle_id"],
            status=row["status"],
            duration_seconds=row["duration_seconds"],
            artifact_path=row["artifact_path"],
            executor_output_json=row["executor_output_json"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            created_at=row["created_at"],
        )

    def update_trial_status(self, trial_id: str, status: str) -> None:
        with self._lock:
            trial = self.get_trial(trial_id)
            if trial is None:
                raise ValueError(f"Trial not found: {trial_id}")
            _validate_transition(
                "trial", trial.status.value, status, _TRIAL_TRANSITIONS
            )
            # 'completed' is a claim that the trial ran and produced a
            # recorded result — it requires an executor record. Refuse
            # to write an unevidenced completion.
            if status == "completed" and not _has_executor_record(
                trial.executor_output_json
            ):
                raise ValueError(
                    f"Cannot mark {trial_id} 'completed': no executor "
                    "record. Persist executor_output_json first via "
                    "update_trial_executor_output, or mark 'failed' — "
                    "an unevidenced completion is a mislabeled record."
                )
            # Stamp the execution window: started_at on →running,
            # finished_at on any terminal transition. First-write-wins —
            # a re-finalization must not move the recorded boundary.
            from .models import _utc_now
            now = _utc_now()
            if status == "running":
                self.conn.execute(
                    "UPDATE trials SET status = ?, "
                    "started_at = COALESCE(started_at, ?) WHERE id = ?",
                    (status, now, trial_id),
                )
            elif status in ("completed", "failed", "retryable", "abandoned"):
                self.conn.execute(
                    "UPDATE trials SET status = ?, "
                    "finished_at = COALESCE(finished_at, ?) WHERE id = ?",
                    (status, now, trial_id),
                )
            else:
                self.conn.execute(
                    "UPDATE trials SET status = ? WHERE id = ?", (status, trial_id)
                )
            self.conn.commit()

    def correct_trial_status(
        self, trial_id: str, status: str, reason: str
    ) -> dict:
        """Recorded repair: rewrite a terminal trial's status.

        Not a normal FSM transition — both ends are terminal, so this
        bypasses _TRIAL_TRANSITIONS deliberately. The act is recorded in
        executor_output_json under 'corrections' so the audit trail
        preserves what was claimed, what it was corrected to, and why.
        Repair is an explicit recorded act, never a silent rewrite.

        Source must be 'completed' or 'failed' (a trial that ran and
        has an executor record to reinterpret); target may be
        'completed', 'failed', or 'retryable'.
        """
        import json as _json
        with self._lock:
            trial = self.get_trial(trial_id)
            if trial is None:
                raise ValueError(f"Trial not found: {trial_id}")
            if trial.status.value not in ("completed", "failed"):
                raise ValueError(
                    f"Only completed or failed trials can be corrected "
                    f"(status: {trial.status.value}). Running trials use "
                    f"cancel_trial/mark_retryable; designed trials are "
                    f"abandoned with the programme."
                )
            if status not in ("completed", "failed", "retryable"):
                raise ValueError(
                    f"Correction target must be completed|failed|"
                    f"retryable, got {status!r}"
                )
            # Same invariant as update_trial_status: a correction TO
            # 'completed' must be backed by an executor record — the
            # record must already carry a verdict (corrections alone
            # are bookkeeping, not evidence of execution).
            if status == "completed" and not _has_executor_record(
                trial.executor_output_json
            ):
                raise ValueError(
                    f"Cannot correct {trial_id} to 'completed': the "
                    "record carries no executor output. Nothing "
                    "evidences the run — 'failed' is the honest "
                    "disposition for an unevidenced record."
                )
            from .models import _utc_now
            now = _utc_now()
            try:
                out = _json.loads(trial.executor_output_json or "{}")
                if not isinstance(out, dict):
                    out = {"unparsed_output": str(trial.executor_output_json)}
            except ValueError:
                out = {"unparsed_output": str(trial.executor_output_json)}
            out.setdefault("corrections", []).append({
                "from": trial.status.value,
                "to": status,
                "reason": reason,
                "corrected_at": now,
            })
            self.conn.execute(
                "UPDATE trials SET status = ?, executor_output_json = ? "
                "WHERE id = ?",
                (status, _json.dumps(out), trial_id),
            )
            self.conn.commit()
            return {
                "trial_id": trial_id,
                "from": trial.status.value,
                "to": status,
                "corrected_at": now,
            }

    def update_hypothesis_status(self, hypothesis_id: str, status: str) -> None:
        with self._lock:
            hypothesis = self.get_hypothesis(hypothesis_id)
            if hypothesis is None:
                raise ValueError(f"Hypothesis not found: {hypothesis_id}")
            _validate_transition(
                "hypothesis", hypothesis.status.value, status, _HYPOTHESIS_TRANSITIONS
            )
            self.conn.execute(
                "UPDATE hypotheses SET status = ? WHERE id = ?", (status, hypothesis_id)
            )
            self.conn.commit()

    def promote_hypothesis_if_proposed(self, hypothesis_id: str) -> None:
        """Atomically move proposed → under_test; idempotent.

        design_experiment checks the hypothesis on a snapshot read, so
        two parallel calls both see "proposed" and both try the
        transition — the loser previously hit under_test → under_test,
        an illegal transition that failed the call AFTER its trial row
        was already created (reported failure, existing trial).
        Re-reading under the lock makes the promote a no-op for the
        second caller; any other status still fails closed.
        """
        with self._lock:
            hypothesis = self.get_hypothesis(hypothesis_id)
            if hypothesis is None:
                raise ValueError(f"Hypothesis not found: {hypothesis_id}")
            current = hypothesis.status.value
            if current == "under_test":
                return
            if current != "proposed":
                raise ValueError(
                    f"Illegal hypothesis transition: {current} -> under_test"
                )
            _validate_transition(
                "hypothesis", current, "under_test", _HYPOTHESIS_TRANSITIONS
            )
            self.conn.execute(
                "UPDATE hypotheses SET status = ? WHERE id = ?",
                ("under_test", hypothesis_id),
            )
            self.conn.commit()

    def update_programme_direction(self, programme_id: str, direction: str) -> None:
        """Update the metric direction for a programme (minimize or maximize)."""
        self._write(
            "UPDATE programmes SET metric_direction = ? WHERE id = ?",
            (direction, programme_id),
        )

    def update_programme_status(self, programme_id: str, status: str) -> None:
        """Update the programme status (active → completed | abandoned → archived)."""
        with self._lock:
            programme = self.get_programme(programme_id)
            if programme is None:
                raise ValueError(f"Programme not found: {programme_id}")
            _validate_transition(
                "programme", programme.status.value, status, _PROGRAMME_TRANSITIONS
            )
            self.conn.execute(
                "UPDATE programmes SET status = ? WHERE id = ?", (status, programme_id)
            )
            self.conn.commit()

    def link_bundle(self, trial_id: str, bundle_id: str) -> None:
        self._write(
            "UPDATE trials SET bundle_id = ? WHERE id = ?", (bundle_id, trial_id)
        )

    def list_trials(self, programme_id: str) -> list[Trial]:
        rows = self._fetchall(
            "SELECT * FROM trials WHERE programme_id = ?", (programme_id,)
        )
        return [
            Trial(
                id=r["id"],
                programme_id=r["programme_id"],
                hypothesis_id=r["hypothesis_id"],
                config_json=r["config_json"],
                bundle_id=r["bundle_id"],
                status=r["status"],
                duration_seconds=r["duration_seconds"],
                artifact_path=r["artifact_path"],
                executor_output_json=r["executor_output_json"],
                started_at=r["started_at"],
                finished_at=r["finished_at"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # --- Pagination helpers (Phase 2) ---

    # Correlated subquery: latest timestamp across everything attached
    # to a programme (creation, hypotheses, trials, observations,
    # belief updates, conclusions, archiving). Always selected so the
    # GUI can display it; also usable as ORDER BY key.
    _LAST_ACTIVITY_SQL = """
        (SELECT MAX(v) FROM (
            SELECT p.created_at AS v
            UNION ALL
            SELECT h.created_at FROM hypotheses h
                WHERE h.programme_id = p.id
            UNION ALL
            SELECT t.created_at FROM trials t
                WHERE t.programme_id = p.id
            UNION ALL
            SELECT o.created_at FROM observations o
                JOIN trials tt ON o.trial_id = tt.id
                WHERE tt.programme_id = p.id
            UNION ALL
            SELECT b.updated_at FROM beliefs b
                WHERE b.programme_id = p.id
            UNION ALL
            SELECT c.created_at FROM conclusions c
                WHERE c.programme_id = p.id
            UNION ALL
            SELECT ae.archived_at FROM archive_entries ae
                WHERE ae.programme_id = p.id
        ))
    """

    def list_programmes_paginated(
        self,
        page: int = 1,
        per_page: int = 50,
        status_filter: str | None = None,
        sort: str = "created_desc",
        candidate_version_id: str | None = None,
    ) -> list[dict]:
        """Return a page of programme rows (raw dicts, newest first).

        Args:
            page: 1-based page number.
            per_page: rows per page.
            status_filter: optional status to filter by
                (active/completed/abandoned/archived).
            sort: 'created_desc' (default), 'activity_desc', or
                'activity_asc'. Activity = latest timestamp across the
                programme's hypotheses, trials, observations, beliefs,
                conclusions, and archive entries.

        Each row includes a computed `last_activity` column.
        """
        offset = (page - 1) * per_page
        order = {
            "created_desc": "p.created_at DESC",
            "activity_desc": "last_activity DESC",
            "activity_asc": "last_activity ASC",
        }.get(sort, "p.created_at DESC")

        activity = self._LAST_ACTIVITY_SQL
        clauses, params = [], []
        if status_filter:
            clauses.append("p.status = ?")
            params.append(status_filter)
        if candidate_version_id:
            clauses.append("p.candidate_version_id = ?")
            params.append(candidate_version_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params += [per_page, offset]
        sql = (
            f"SELECT p.*, {activity} AS last_activity "
            f"FROM programmes p {where} "
            f"ORDER BY {order} LIMIT ? OFFSET ?"
        )
        rows = self._fetchall(sql, params)
        return [dict(r) for r in rows]

    def count_programmes(
        self,
        status_filter: str | None = None,
        candidate_version_id: str | None = None,
    ) -> int:
        """Count programmes, optionally filtered by status/candidate."""
        clauses, params = [], []
        if status_filter:
            clauses.append("status = ?")
            params.append(status_filter)
        if candidate_version_id:
            clauses.append("candidate_version_id = ?")
            params.append(candidate_version_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        row = self._fetchone(
            f"SELECT COUNT(*) as n FROM programmes{where}", tuple(params)
        )
        return row["n"] if row else 0

    def list_trials_paginated(
        self,
        programme_id: str,
        page: int = 1,
        per_page: int = 50,
    ) -> list[Trial]:
        """Return a page of trials for a programme (newest first)."""
        offset = (page - 1) * per_page
        rows = self._fetchall(
            "SELECT * FROM trials WHERE programme_id = ? "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (programme_id, per_page, offset),
        )
        return [
            Trial(
                id=r["id"],
                programme_id=r["programme_id"],
                hypothesis_id=r["hypothesis_id"],
                config_json=r["config_json"],
                bundle_id=r["bundle_id"],
                status=r["status"],
                duration_seconds=r["duration_seconds"],
                artifact_path=r["artifact_path"],
                executor_output_json=r["executor_output_json"],
                started_at=r["started_at"],
                finished_at=r["finished_at"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def count_trials(self, programme_id: str) -> int:
        """Count trials for a programme."""
        row = self._fetchone(
            "SELECT COUNT(*) as n FROM trials WHERE programme_id = ?",
            (programme_id,),
        )
        return row["n"] if row else 0

    def update_trial_duration(
        self, trial_id: str, duration_seconds: float
    ) -> None:
        """Record the execution duration of a trial (for wall-time budget)."""
        self._write(
            "UPDATE trials SET duration_seconds = ? WHERE id = ?",
            (duration_seconds, trial_id),
        )

    def update_trial_artifact_path(
        self, trial_id: str, artifact_path: str
    ) -> None:
        """Record the artifact workspace path for a trial."""
        self._write(
            "UPDATE trials SET artifact_path = ? WHERE id = ?",
            (artifact_path, trial_id),
        )

    def update_trial_executor_output(
        self, trial_id: str, output: str
    ) -> None:
        """Persist the raw executor output JSON for a trial.

        The executor's async result cache is in-memory and dies on
        restart; the trial row is the durable record.
        """
        self._write(
            "UPDATE trials SET executor_output_json = ? WHERE id = ?",
            (output, trial_id),
        )

    # --- Observation ---

    def create_observation(self, o: Observation) -> None:
        verify_mece(o)
        self._write(
            "INSERT INTO observations VALUES (?,?,?,?,?,?)",
            (
                o.id,
                o.trial_id,
                o.metrics_json,
                o.variance_json,
                o.spatiotemporal_region,
                o.created_at,
            ),
        )

    def get_observation(self, observation_id: str) -> Observation | None:
        row = self._fetchone(
            "SELECT * FROM observations WHERE id = ?", (observation_id,)
        )
        if row is None:
            return None
        return Observation(
            id=row["id"],
            trial_id=row["trial_id"],
            metrics_json=row["metrics_json"],
            variance_json=row["variance_json"],
            spatiotemporal_region=row["spatiotemporal_region"],
            created_at=row["created_at"],
        )

    def list_observations(self, trial_id: str) -> list[Observation]:
        rows = self._fetchall(
            "SELECT * FROM observations WHERE trial_id = ?", (trial_id,)
        )
        return [
            Observation(
                id=r["id"],
                trial_id=r["trial_id"],
                metrics_json=r["metrics_json"],
                variance_json=r["variance_json"],
                spatiotemporal_region=r["spatiotemporal_region"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # --- Belief ---

    def create_belief(self, b: Belief) -> None:
        verify_mece(b)
        self._write(
            "INSERT INTO beliefs VALUES (?,?,?,?)",
            (b.id, b.programme_id, b.state_json, b.updated_at),
        )

    def get_belief(self, programme_id: str) -> Belief | None:
        row = self._fetchone(
            "SELECT * FROM beliefs WHERE programme_id = ? ORDER BY updated_at DESC LIMIT 1",
            (programme_id,),
        )
        if row is None:
            return None
        return Belief(
            id=row["id"],
            programme_id=row["programme_id"],
            state_json=row["state_json"],
            updated_at=row["updated_at"],
        )

    def list_beliefs(self, programme_id: str) -> list[Belief]:
        """Return all beliefs for a programme, ordered by update time."""
        rows = self._fetchall(
            "SELECT * FROM beliefs WHERE programme_id = ? ORDER BY updated_at ASC",
            (programme_id,),
        )
        return [
            Belief(
                id=row["id"],
                programme_id=row["programme_id"],
                state_json=row["state_json"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    # --- Conclusion ---

    def create_conclusion(self, c: Conclusion) -> None:
        verify_mece(c)
        self._write(
            "INSERT INTO conclusions VALUES (?,?,?,?,?,?,?)",
            (
                c.id,
                c.hypothesis_id,
                c.programme_id,
                c.verdict.value,
                c.evidence_ref,
                c.evidence_summary,
                c.created_at,
            ),
        )

    def get_conclusion(self, conclusion_id: str) -> Conclusion | None:
        row = self._fetchone(
            "SELECT * FROM conclusions WHERE id = ?", (conclusion_id,)
        )
        if row is None:
            return None
        return Conclusion(
            id=row["id"],
            hypothesis_id=row["hypothesis_id"],
            programme_id=row["programme_id"],
            verdict=row["verdict"],
            evidence_ref=row["evidence_ref"],
            evidence_summary=row["evidence_summary"],
            created_at=row["created_at"],
        )

    def list_conclusions(self, programme_id: str) -> list[Conclusion]:
        rows = self._fetchall(
            "SELECT * FROM conclusions WHERE programme_id = ?", (programme_id,)
        )
        return [
            Conclusion(
                id=r["id"],
                hypothesis_id=r["hypothesis_id"],
                programme_id=r["programme_id"],
                verdict=r["verdict"],
                evidence_ref=r["evidence_ref"],
                evidence_summary=r["evidence_summary"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # --- DataRef ---

    def create_data_ref(self, d: DataRef) -> None:
        verify_mece(d)
        self._write(
            "INSERT INTO data_refs "
            "(id, split, regime, generator_code_ref, generator_code_hash, "
            "generator_seed, "
            "generator_params_json, source_uri, content_hash, version, "
            "capture_window_start, capture_window_end, "
            "capture_source_metadata_json, schema_hash, size_bytes, "
            "num_samples, storage_uri, reproducibility_risk, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                d.id,
                d.split,
                d.regime,
                d.generator_code_ref,
                d.generator_code_hash,
                d.generator_seed,
                json.dumps(d.generator_params) if d.generator_params else None,
                d.source_uri,
                d.content_hash,
                d.version,
                d.capture_window_start,
                d.capture_window_end,
                json.dumps(d.capture_source_metadata) if d.capture_source_metadata else None,
                d.schema_hash,
                d.size_bytes,
                d.num_samples,
                d.storage_uri,
                d.reproducibility_risk,
                d.created_at,
            ),
        )

    def get_data_ref(self, data_ref_id: str) -> DataRef | None:
        row = self._fetchone(
            "SELECT * FROM data_refs WHERE id = ?", (data_ref_id,)
        )
        if row is None:
            return None
        return DataRef(
            id=row["id"],
            split=row["split"],
            regime=row["regime"],
            generator_code_ref=row["generator_code_ref"],
            generator_code_hash=row["generator_code_hash"],
            generator_seed=row["generator_seed"],
            generator_params=json.loads(row["generator_params_json"]) if row["generator_params_json"] else None,
            source_uri=row["source_uri"],
            content_hash=row["content_hash"],
            version=row["version"],
            capture_window_start=row["capture_window_start"],
            capture_window_end=row["capture_window_end"],
            capture_source_metadata=json.loads(row["capture_source_metadata_json"]) if row["capture_source_metadata_json"] else None,
            schema_hash=row["schema_hash"],
            size_bytes=row["size_bytes"],
            num_samples=row["num_samples"],
            storage_uri=row["storage_uri"],
            reproducibility_risk=row["reproducibility_risk"],
            created_at=row["created_at"],
        )

    def list_data_refs(self) -> list[DataRef]:
        rows = self._fetchall("SELECT * FROM data_refs")
        return [
            DataRef(
                id=row["id"],
                split=row["split"],
                regime=row["regime"],
                generator_code_ref=row["generator_code_ref"],
                generator_code_hash=row["generator_code_hash"],
                generator_seed=row["generator_seed"],
                generator_params=json.loads(row["generator_params_json"]) if row["generator_params_json"] else None,
                source_uri=row["source_uri"],
                content_hash=row["content_hash"],
                version=row["version"],
                capture_window_start=row["capture_window_start"],
                capture_window_end=row["capture_window_end"],
                capture_source_metadata=json.loads(row["capture_source_metadata_json"]) if row["capture_source_metadata_json"] else None,
                schema_hash=row["schema_hash"],
                size_bytes=row["size_bytes"],
                num_samples=row["num_samples"],
                storage_uri=row["storage_uri"],
                reproducibility_risk=row["reproducibility_risk"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    # --- CodeSnippet ---

    def create_code_snippet(self, cs: CodeSnippet) -> None:
        """Insert a code snippet if not already present (content-addressed dedup).

        Uses INSERT OR IGNORE so duplicate hashes are silently skipped.
        """
        verify_mece(cs)
        self._write(
            "INSERT OR IGNORE INTO code_snippets "
            "(code_hash, code_text, language, captured_at, original_path, size_bytes) "
            "VALUES (?,?,?,?,?,?)",
            (
                cs.code_hash,
                cs.code_text,
                cs.language,
                cs.captured_at,
                cs.original_path,
                cs.size_bytes,
            ),
        )

    def get_code_snippet(self, code_hash: str) -> CodeSnippet | None:
        row = self._fetchone(
            "SELECT * FROM code_snippets WHERE code_hash = ?", (code_hash,)
        )
        if row is None:
            return None
        return CodeSnippet(
            code_hash=row["code_hash"],
            code_text=row["code_text"],
            language=row["language"],
            captured_at=row["captured_at"],
            original_path=row["original_path"],
            size_bytes=row["size_bytes"],
        )

    def list_code_snippets(self) -> list[CodeSnippet]:
        rows = self._fetchall("SELECT * FROM code_snippets ORDER BY captured_at DESC")
        return [
            CodeSnippet(
                code_hash=row["code_hash"],
                code_text=row["code_text"],
                language=row["language"],
                captured_at=row["captured_at"],
                original_path=row["original_path"],
                size_bytes=row["size_bytes"],
            )
            for row in rows
        ]

    # --- CandidateVersion / EvaluationContract / PromotionDecision (RSI Phase 0) ---

    def create_candidate_version(self, c: CandidateVersion) -> None:
        verify_mece(c)
        self._write(
            "INSERT INTO candidate_versions "
            "(id, parent_id, code_artifact_digest, harness_artifact_digest, "
            "model_ref, search_policy_ref, capability_profile_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                c.id,
                c.parent_id,
                c.code_artifact_digest,
                c.harness_artifact_digest,
                c.model_ref,
                c.search_policy_ref,
                json.dumps(c.capability_profile),
                c.created_at,
            ),
        )

    def get_candidate_version(self, candidate_id: str) -> CandidateVersion | None:
        row = self._fetchone(
            "SELECT * FROM candidate_versions WHERE id = ?", (candidate_id,)
        )
        if row is None:
            return None
        return self._candidate_from_row(row)

    def get_candidate_lineage(self, candidate_id: str) -> list[CandidateVersion]:
        """Walk the parent chain from candidate to genesis (self first)."""
        lineage: list[CandidateVersion] = []
        current = self.get_candidate_version(candidate_id)
        while current is not None:
            lineage.append(current)
            current = (
                self.get_candidate_version(current.parent_id)
                if current.parent_id
                else None
            )
        return lineage

    def create_evaluation_contract(self, e: EvaluationContract) -> None:
        verify_mece(e)
        self._write(
            "INSERT INTO evaluation_contracts "
            "(id, programme_id, version, metrics_json, holdouts_json, "
            "budget_json, promotion_policy_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                e.id,
                e.programme_id,
                e.version,
                json.dumps(e.metrics),
                json.dumps(e.holdouts) if e.holdouts is not None else None,
                json.dumps(e.budget) if e.budget is not None else None,
                json.dumps(e.promotion_policy),
                e.created_at,
            ),
        )

    def get_evaluation_contract(self, contract_id: str) -> EvaluationContract | None:
        row = self._fetchone(
            "SELECT * FROM evaluation_contracts WHERE id = ?", (contract_id,)
        )
        if row is None:
            return None
        return self._contract_from_row(row)

    def list_evaluation_contracts(self, programme_id: str) -> list[EvaluationContract]:
        rows = self._fetchall(
            "SELECT * FROM evaluation_contracts WHERE programme_id = ? "
            "ORDER BY version",
            (programme_id,),
        )
        return [self._contract_from_row(r) for r in rows]

    def _contract_from_row(self, row) -> EvaluationContract:
        return EvaluationContract(
            id=row["id"],
            programme_id=row["programme_id"],
            version=row["version"],
            metrics=json.loads(row["metrics_json"]),
            holdouts=json.loads(row["holdouts_json"]) if row["holdouts_json"] else None,
            budget=json.loads(row["budget_json"]) if row["budget_json"] else None,
            promotion_policy=json.loads(row["promotion_policy_json"]),
            created_at=row["created_at"],
        )

    def create_promotion_decision(self, d: PromotionDecision) -> None:
        verify_mece(d)
        self._write(
            "INSERT INTO promotion_decisions "
            "(id, candidate_id, contract_id, verdict, evidence_refs_json, "
            "rationale, decided_by, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                d.id,
                d.candidate_id,
                d.contract_id,
                d.verdict.value,
                json.dumps(d.evidence_refs),
                d.rationale,
                d.decided_by,
                d.created_at,
            ),
        )

    def get_promotion_decision(self, decision_id: str) -> PromotionDecision | None:
        row = self._fetchone(
            "SELECT * FROM promotion_decisions WHERE id = ?", (decision_id,)
        )
        if row is None:
            return None
        return self._decision_from_row(row)

    def list_promotion_decisions(self, candidate_id: str) -> list[PromotionDecision]:
        rows = self._fetchall(
            "SELECT * FROM promotion_decisions WHERE candidate_id = ? "
            "ORDER BY created_at",
            (candidate_id,),
        )
        return [self._decision_from_row(r) for r in rows]

    def _decision_from_row(self, row) -> PromotionDecision:
        return PromotionDecision(
            id=row["id"],
            candidate_id=row["candidate_id"],
            contract_id=row["contract_id"],
            verdict=row["verdict"],
            evidence_refs=json.loads(row["evidence_refs_json"]),
            rationale=row["rationale"],
            decided_by=row["decided_by"],
            created_at=row["created_at"],
        )

    def list_candidates(
        self, limit: int = 50, offset: int = 0
    ) -> list[CandidateVersion]:
        """Enumerate the population, newest first."""
        rows = self._fetchall(
            "SELECT * FROM candidate_versions "
            "ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [self._candidate_from_row(r) for r in rows]

    def count_candidates(self) -> int:
        row = self._fetchone(
            "SELECT COUNT(*) AS n FROM candidate_versions"
        )
        return row["n"]

    def _candidate_from_row(self, row) -> CandidateVersion:
        return CandidateVersion(
            id=row["id"],
            parent_id=row["parent_id"],
            code_artifact_digest=row["code_artifact_digest"],
            harness_artifact_digest=row["harness_artifact_digest"],
            model_ref=row["model_ref"],
            search_policy_ref=row["search_policy_ref"],
            capability_profile=json.loads(row["capability_profile_json"]),
            created_at=row["created_at"],
        )

    def get_incumbent(self) -> str | None:
        """Derive the incumbent candidate from insert-only decisions.

        Newest-first scan: the first 'promote' with no later 'rollback'
        for the same candidate is the incumbent. Orphaned rollbacks
        (accepted upstream without a prior promote) are skipped. A
        rollback resurfaces the previous incumbent naturally.
        """
        rows = self._fetchall(
            "SELECT candidate_id, verdict FROM promotion_decisions "
            "ORDER BY created_at DESC, rowid DESC"
        )
        rolled_back: set[str] = set()
        for r in rows:
            if r["verdict"] == "rollback":
                rolled_back.add(r["candidate_id"])
            elif (
                r["verdict"] == "promote"
                and r["candidate_id"] not in rolled_back
            ):
                return r["candidate_id"]
        return None

    def get_candidate_scorecard(self, candidate_id: str) -> dict:
        """Aggregate descendant quality over attributed programmes.

        For each programme created under this candidate (the RSI Phase-0
        `candidate_version_id` correlation), reports trial/observation
        counts and the best observed value per metric — 'best' read
        against the programme's own metric_direction. The contract-
        scoped interpretation is the caller's job (which metric is
        primary); this returns the raw per-programme picture.
        """
        progs = self._fetchall(
            "SELECT id, status, metric_direction FROM programmes "
            "WHERE candidate_version_id = ? ORDER BY created_at",
            (candidate_id,),
        )
        per_programme = []
        for p in progs:
            trials = self.list_trials(p["id"])
            completed = [t for t in trials if t.status.value == "completed"]
            metric_values: dict[str, list[float]] = {}
            n_obs = 0
            for t in completed:
                for o in self.list_observations(t.id):
                    n_obs += 1
                    for k, v in json.loads(o.metrics_json).items():
                        if isinstance(v, (int, float)):
                            metric_values.setdefault(k, []).append(v)
            maximize = p["metric_direction"] == "maximize"
            best = {
                k: (max(vs) if maximize else min(vs))
                for k, vs in metric_values.items()
            }
            per_programme.append({
                "programme_id": p["id"],
                "status": p["status"],
                "candidate_version_id": candidate_id,
                "trials": len(trials),
                "completed_trials": len(completed),
                "observations": n_obs,
                "conclusions": len(self.list_conclusions(p["id"])),
                "best_metrics": best,
            })
        return {
            "candidate_id": candidate_id,
            "programme_count": len(per_programme),
            "programmes": per_programme,
        }

    def capture_code_from_path(self, code_ref: str) -> str:
        """Read a code file, store it as a CodeSnippet, return the code_hash.

        This is the content-addressing entry point: given a file path (carrier),
        read the text (ICE), compute the SHA-256, store in code_snippets, and
        return the content address (code_hash).

        If the file is already captured (same hash), it's a no-op (dedup).
        """
        from .models import _utc_now

        path = Path(code_ref)
        if not path.exists():
            raise FileNotFoundError(f"code_ref does not exist: {code_ref}")

        code_text = path.read_text(encoding="utf-8")
        import hashlib
        code_hash = "sha256:" + hashlib.sha256(code_text.encode("utf-8")).hexdigest()

        # Dedup: only store if not already present
        existing = self.get_code_snippet(code_hash)
        if existing is None:
            snippet = CodeSnippet(
                code_hash=code_hash,
                code_text=code_text,
                language="python",
                captured_at=_utc_now(),
                original_path=str(path.resolve()),
                size_bytes=len(code_text.encode("utf-8")),
            )
            self.create_code_snippet(snippet)

        return code_hash

    def capture_code_with_imports(
        self,
        code_ref: str,
        _seen: set[str] | None = None,
        _root: Path | None = None,
    ) -> tuple[str, list[str]]:
        """Capture a code file AND its local imports recursively.

        This is the multi-file capture path (commitment 2 + 5). A bundle
        that only captures the executor but not its local imports cannot
        be rerun from archive — the inlined code would fail on missing
        modules.

        Returns (primary_hash, [extra_hashes]) where primary_hash is the
        content address of the code_ref file, and extra_hashes are the
        content addresses of all locally-imported .py files discovered by
        AST parsing.

        Only imports that resolve to a .py file relative to the code_ref's
        directory (or a package subdirectory) are captured. Stdlib and
        third-party imports are skipped (they are environment dependencies,
        tracked by env_ref, not code dependencies).
        """
        import ast

        if _seen is None:
            _seen = set()

        primary_hash = self.capture_code_from_path(code_ref)
        extra_hashes: list[str] = []

        code_path = Path(code_ref).resolve()
        base_dir = code_path.parent
        _seen.add(str(code_path))
        # The entry file's directory is the project root: spawned
        # scripts living in subdirs import root-relative. First call
        # seeds it; recursion shares it.
        if _root is None:
            _root = base_dir
        extra_roots = (_root,) if _root != base_dir else ()

        try:
            source = code_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(code_path))
        except Exception:
            # If we can't parse it, just return the primary — the file
            # itself was already captured by capture_code_from_path.
            return primary_hash, extra_hashes

        # Import-following misses subprocess-spawned scripts — the
        # runner pattern (os.path.join(ROOT, 'scripts', 'x.py') fed to
        # subprocess) carries the actual experiment code. Path-literal
        # discovery is equally required for commitment 5.
        local_modules = _find_local_imports(tree, base_dir, extra_roots) + \
            _find_spawned_scripts(tree, base_dir, extra_roots)

        for mod_path in local_modules:
            mod_str = str(mod_path.resolve())
            if mod_str in _seen:
                continue
            _seen.add(mod_str)

            # Recursively capture the imported module's imports too
            sub_hash, sub_extras = self.capture_code_with_imports(
                str(mod_path), _seen, _root
            )
            if sub_hash not in extra_hashes:
                extra_hashes.append(sub_hash)
            for e in sub_extras:
                if e not in extra_hashes:
                    extra_hashes.append(e)

        return primary_hash, extra_hashes

    # ------------------------------------------------------------------
    # Artifact files — content-addressed, gzip-compressed BLOB storage
    # ------------------------------------------------------------------

    def promote_artifact_to_snippet(
        self, content_hash: str
    ) -> "CodeSnippet | None":
        """Materialize an ingested artifact into code_snippets.

        The artifact-ingest surface stores bytes under artifact_files;
        capture_bundle_from_code_hash consumes code_snippets. This is
        the promotion step between them: hash-addressed in, same hash
        out — the content address is unchanged because the content is.

        Returns the (possibly already-existing) CodeSnippet, or None if
        no artifact exists under this hash. Raises ValueError if the
        artifact exists but is not UTF-8-decodable text — it is not
        code.
        """
        from .models import _utc_now

        existing = self.get_code_snippet(content_hash)
        if existing is not None:
            return existing
        artifact = self.get_artifact_file(content_hash)
        if artifact is None:
            return None
        try:
            code_text = artifact["content"].decode("utf-8")
        except UnicodeDecodeError as e:
            raise ValueError(
                f"artifact {content_hash} is not code "
                f"(not UTF-8 text)"
            ) from e
        snippet = CodeSnippet(
            code_hash=content_hash,
            code_text=code_text,
            language="python",
            captured_at=_utc_now(),
            original_path=None,
            size_bytes=artifact["size_bytes"],
        )
        self.create_code_snippet(snippet)
        return snippet

    def create_artifact_file(
        self,
        content_hash: str,
        filename: str,
        content: bytes,
        content_type: str | None,
        captured_at: str,
        original_path: str | None,
    ) -> None:
        """Store an artifact file's bytes (gzip-compressed) in artifact_files.

        Dedup by content_hash: if the hash already exists, this is a no-op.
        """
        import gzip as _gzip

        compressed = _gzip.compress(content, compresslevel=6)
        self._write(
            "INSERT OR IGNORE INTO artifact_files "
            "(content_hash, filename, content, content_type, size_bytes, "
            "compressed_size_bytes, captured_at, original_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                content_hash,
                filename,
                compressed,
                content_type,
                len(content),
                len(compressed),
                captured_at,
                original_path,
            ),
        )

    def describe_blob(self, content_hash: str) -> dict:
        """Describe a content-addressed blob across all stores — no bytes.

        The resolution sites for a digest claim: artifact_files
        (the HTTP ingest surface), code_snippets (captured code), and
        bundles.code_hash (captured bundles). A hash may resolve in
        more than one store — ``resolved_in`` names them all.
        Read-only: existence + metadata, never content.
        """
        resolved_in: list[str] = []
        out: dict = {"exists": False, "resolved_in": resolved_in}
        af = self._fetchone(
            "SELECT size_bytes, content_type, captured_at "
            "FROM artifact_files WHERE content_hash = ?",
            (content_hash,),
        )
        if af is not None:
            resolved_in.append("artifact_files")
            out.update({
                "size_bytes": af["size_bytes"],
                "content_type": af["content_type"],
                "captured_at": af["captured_at"],
            })
        cs = self._fetchone(
            "SELECT size_bytes, captured_at "
            "FROM code_snippets WHERE code_hash = ?",
            (content_hash,),
        )
        if cs is not None:
            resolved_in.append("code_snippets")
            out.setdefault("size_bytes", cs["size_bytes"])
            out.setdefault("captured_at", cs["captured_at"])
        bundle = self._fetchone(
            "SELECT created_at FROM bundles WHERE code_hash = ?",
            (content_hash,),
        )
        if bundle is not None:
            resolved_in.append("bundles.code_hash")
            out.setdefault("captured_at", bundle["created_at"])
        out["exists"] = bool(resolved_in)
        return out

    def has_blob(self, content_hash: str) -> bool:
        """True when the digest resolves to held bytes in any store."""
        return self.describe_blob(content_hash)["exists"]

    def get_artifact_file(self, content_hash: str) -> dict | None:
        """Get an artifact file's metadata + decompressed content by hash.

        Transparently decompresses the gzip BLOB on read.
        """
        import gzip as _gzip

        row = self._fetchone(
            "SELECT * FROM artifact_files WHERE content_hash = ?",
            (content_hash,),
        )
        if row is None:
            return None
        return {
            "content_hash": row["content_hash"],
            "filename": row["filename"],
            "content": _gzip.decompress(row["content"]),
            "content_type": row["content_type"],
            "size_bytes": row["size_bytes"],
            "compressed_size_bytes": row["compressed_size_bytes"],
            "captured_at": row["captured_at"],
            "original_path": row["original_path"],
        }

    def create_trial_artifact(
        self,
        ta_id: str,
        trial_id: str,
        content_hash: str,
        filename: str,
        artifact_type: str,
        created_at: str,
    ) -> None:
        """Record a trial→artifact_file mapping."""
        self._write(
            "INSERT OR REPLACE INTO trial_artifacts "
            "(id, trial_id, content_hash, filename, artifact_type, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ta_id, trial_id, content_hash, filename, artifact_type, created_at),
        )

    def list_trial_artifacts(self, trial_id: str) -> list[dict]:
        """List all artifact files for a trial (metadata only, no content)."""
        rows = self._fetchall(
            "SELECT ta.id, ta.trial_id, ta.content_hash, ta.filename, "
            "ta.artifact_type, ta.created_at, "
            "af.size_bytes, af.compressed_size_bytes, af.content_type "
            "FROM trial_artifacts ta "
            "JOIN artifact_files af ON ta.content_hash = af.content_hash "
            "WHERE ta.trial_id = ? "
            "ORDER BY ta.filename",
            (trial_id,),
        )
        return [dict(r) for r in rows]

    def capture_artifacts_from_dir(
        self,
        trial_id: str,
        artifact_dir: str,
        max_file_size_bytes: int = 50 * 1024 * 1024,
        cleanup_after: bool = True,
    ) -> dict:
        """Read all files from an artifact directory and store them.

        The on-disk artifacts/ directory is transient staging only
        (Rule 5.4): the executor writes there during run_trial, this
        method captures everything into SQLite, then the staging files
        are deleted. artifact_files.original_path keeps the original
        path as provenance metadata — it is NOT a live reference.

        cleanup_after: when True (default), delete each file after it
        is successfully stored in SQLite, and remove the (now-empty)
        staging directory. Files that fail to capture stay on disk so
        the failure can be retried. Set False for conservative
        migration (e.g. capture_pending_artifacts) where the user may
        want to keep the originals.

        Returns: {captured: [...], oversized: [...], lost: [...]}
        """
        import hashlib
        import mimetypes
        import shutil
        from .models import _utc_now

        path = Path(artifact_dir)
        if not path.exists() or not path.is_dir():
            return {"captured": [], "oversized": [], "lost": [], "error": "dir not found"}

        captured = []
        oversized = []
        lost = []

        for fp in sorted(path.rglob("*")):
            if not fp.is_file():
                continue
            try:
                size = fp.stat().st_size
            except OSError:
                lost.append({"filename": fp.name, "reason": "stat failed"})
                continue

            if size > max_file_size_bytes:
                oversized.append({"filename": fp.name, "size_bytes": size})
                continue

            try:
                content = fp.read_bytes()
            except OSError:
                lost.append({"filename": fp.name, "reason": "read failed"})
                continue

            content_hash = "sha256:" + hashlib.sha256(content).hexdigest()
            content_type, _ = mimetypes.guess_type(fp.name)
            if content_type is None:
                content_type = "application/octet-stream"

            # Determine artifact type from filename
            name_lower = fp.name.lower()
            if "stderr" in name_lower:
                art_type = "stderr"
            elif "stdout" in name_lower:
                art_type = "stdout"
            elif "manifest" in name_lower:
                art_type = "manifest"
            elif "wrapper" in name_lower or fp.suffix == ".py":
                art_type = "wrapper"
            else:
                art_type = "other"

            # Record path relative to the staging dir so nested files
            # (e.g. figures/loss.png) keep their subdirectory structure.
            rel_name = str(fp.relative_to(path))

            now = _utc_now()
            self.create_artifact_file(
                content_hash=content_hash,
                filename=rel_name,
                content=content,
                content_type=content_type,
                captured_at=now,
                original_path=str(fp.resolve()),
            )

            import uuid
            ta_id = f"ta-{uuid.uuid4().hex[:8]}"
            self.create_trial_artifact(
                ta_id=ta_id,
                trial_id=trial_id,
                content_hash=content_hash,
                filename=rel_name,
                artifact_type=art_type,
                created_at=now,
            )

            captured.append({
                "filename": rel_name,
                "content_hash": content_hash,
                "artifact_type": art_type,
                "size_bytes": size,
            })

            # Staging cleanup: delete the file now that its content is
            # durably stored in SQLite. Failures are ignored — the file
            # just stays on disk as a stray copy.
            if cleanup_after:
                try:
                    fp.unlink()
                except OSError:
                    pass

        # Remove the staging directory if everything was captured and
        # no files remain (oversized/lost files stay, keeping the dir).
        if cleanup_after and not oversized and not lost:
            try:
                shutil.rmtree(path)
            except OSError:
                pass  # leave the dir; it's empty or has strays

        return {"captured": captured, "oversized": oversized, "lost": lost}

    def capture_executed_code(
        self,
        trial_id: str,
        artifact_dir: str | Path,
    ) -> dict:
        """Capture the code files a trial ACTUALLY read, from the strace
        read-trace the executor left in the artifact dir.

        This is execution-time evidence — the authoritative record of
        what ran — distinct from the sealed bundle's static closure
        (capture_code_with_imports), which is a pre-run prediction.
        Both are kept: the bundle says what was intended to be enough;
        this manifest says what was actually enough.

        Every observed .py file outside interpreter internals
        (stdlib, site-packages) and outside the artifact dir itself is
        content-addressed into code_snippets. An executed_code.json
        manifest is written into the artifact dir so the subsequent
        artifact capture stores it as trial evidence.
        """
        import json as _json
        import re
        import sysconfig
        from .models import _utc_now

        path = Path(artifact_dir)
        if not path.is_dir():
            return {"traced": False, "captured": []}
        logs = sorted(path.glob("*_readtrace.strace"))
        if not logs:
            return {"traced": False, "captured": []}

        stdlib = Path(sysconfig.get_path("stdlib")).resolve()
        artifact_resolved = path.resolve()
        # Any interpreter's internals — stdlib + site-packages — share
        # the /lib/pythonX.Y/ layout (uv-managed, system, venv alike).
        # The trial may run a different interpreter than the server, so
        # a prefix check against OUR stdlib is insufficient.
        interp_re = re.compile(r"/lib/python\d+\.\d+/")

        # openat(AT_FDCWD, "/x.py", O_RDONLY|O_CLOEXEC) = 3
        # also matches `<... openat resumed> ... = 3` continuations.
        call_re = re.compile(
            r'(?:openat2?|open)\([^)]*"([^"]+\.pyc?)"[^)]*\)\s*=\s*(\d+)'
        )
        exec_re = re.compile(r'execve\([^=]*=\s*\d+')
        str_re = re.compile(r'"([^"]+\.py)"')
        # An opened .pyc means the source ran — Python only stats the
        # .py (untraced) when the bytecode cache is fresh, so a file can
        # execute without ever opening its own .py. Derive it.
        pyc_re = re.compile(r"^(.*)/__pycache__/([^/]+)\.cpython-\d+\.pyc$")

        observed: set[Path] = set()
        for log in logs:
            try:
                text = log.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                for m in call_re.finditer(line):
                    if int(m.group(2)) < 0:
                        continue  # failed open — nothing was read
                    p = Path(m.group(1))
                    pm = pyc_re.match(str(p))
                    if pm:
                        observed.add(Path(pm.group(1)) / (pm.group(2) + ".py"))
                    elif p.suffix == ".py":
                        observed.add(p)
                if "execve(" in line and exec_re.search(line):
                    for s in str_re.findall(line):
                        if s.endswith(".py"):
                            observed.add(Path(s))

        captured = []
        for p in sorted(observed):
            if not p.is_absolute():
                p = artifact_resolved / p
            try:
                rp = p.resolve()
            except OSError:
                continue
            if not rp.is_file():
                continue
            if rp.suffix != ".py":
                continue
            if interp_re.search(str(rp)):
                continue  # interpreter internals — env, not experiment
            try:
                if rp.is_relative_to(artifact_resolved):
                    continue  # wrapper script — captured as an artifact
            except ValueError:
                pass
            try:
                code_hash = self.capture_code_from_path(str(rp))
            except (FileNotFoundError, OSError, UnicodeDecodeError):
                continue
            captured.append({
                "original_path": str(rp),
                "code_hash": code_hash,
                "size_bytes": rp.stat().st_size,
            })

        manifest = {
            "trial_id": trial_id,
            "traced": True,
            "created_at": _utc_now(),
            "note": (
                "Code files actually opened by the trial's process tree "
                "(strace). Evidence of what ran; the sealed bundle's "
                "static closure is the pre-run prediction of the same."
            ),
            "files": captured,
        }
        try:
            (path / "executed_code.json").write_text(
                _json.dumps(manifest, indent=2)
            )
        except OSError:
            pass
        return {"traced": True, "captured": captured}
