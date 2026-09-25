"""Audit log for integrity-check runs.

Every ``check_invariants`` / ``/health/deep`` invocation appends one
JSON line to ``<db_dir>/logs/check-<YYYY-MM-DD>.jsonl`` — a new file
at each UTC midnight. The check trail is itself evidence; an
unrecorded audit is the same dishonesty as an unrecorded control.
Retention is configured by ``[integrity] log_max_files`` (0 disables
logging entirely) and bounds the number of *day files* kept.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def write_check_log(
    log_dir: Path, payload: dict, max_files: int
) -> Path | None:
    """Append one check-run record to today's file; prune oldest past
    the cap. Returns the file appended to, or None when logging is
    disabled (``max_files <= 0``)."""
    if max_files <= 0:
        return None
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"check-{_utc_day()}.jsonl"
    with open(path, "a") as f:
        f.write(json.dumps(payload) + "\n")
    _prune(log_dir, max_files)
    return path


def _log_files(log_dir: Path) -> list[Path]:
    """All check-log files, newest first."""
    if not log_dir.is_dir():
        return []
    files = list(log_dir.glob("check-*.jsonl"))
    files += log_dir.glob("check-*.json")
    return sorted(files, reverse=True)


def _prune(log_dir: Path, max_files: int) -> None:
    files = _log_files(log_dir)  # newest-first — prune the tail
    for f in files[max_files:]:
        f.unlink(missing_ok=True)


def _iter_runs(path: Path):
    """Yield (index, payload) for each recorded run in a file —
    JSONL day files yield every line; legacy .json files yield one."""
    try:
        if path.suffix == ".jsonl":
            for i, line in enumerate(path.read_text().splitlines()):
                if line.strip():
                    try:
                        yield i, json.loads(line)
                    except ValueError:
                        yield i, {"status": "unreadable"}
        else:
            try:
                yield 0, json.loads(path.read_text())
            except ValueError:
                yield 0, {"status": "unreadable"}
    except OSError:
        return


def list_check_logs(log_dir: Path, limit: int = 50) -> list[dict]:
    """Per-run summaries, newest first across all files."""
    out = []
    for p in _log_files(log_dir):
        runs = list(_iter_runs(p))
        for i, data in reversed(runs):
            out.append({
                "file": p.name,
                "index": i,
                "status": data.get("status"),
                "trigger": data.get("trigger"),
                "checked_at": data.get("checked_at"),
                "violations": sum(
                    len(c.get("violations", []))
                    for c in data.get("checks", [])
                ),
            })
        if len(out) >= limit:
            break
    return out[:limit]


def read_check_log(log_dir: Path, name: str) -> list[dict] | None:
    """All runs recorded in one file (guards against path traversal)."""
    if "/" in name or ".." in name:
        return None
    p = log_dir / name
    if not p.is_file():
        return None
    runs = [data for _, data in _iter_runs(p)]
    return runs or None


def read_run(log_dir: Path, name: str, index: int) -> dict | None:
    """One recorded run by file + line index."""
    if "/" in name or ".." in name:
        return None
    p = log_dir / name
    if not p.is_file():
        return None
    for i, data in _iter_runs(p):
        if i == index:
            return data
    return None
