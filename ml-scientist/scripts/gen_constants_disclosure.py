#!/usr/bin/env python3
"""Regenerate constants/disclosure.md from the grounded registry.

Loads the canonical constants/grounded_constants.py, scans src/ for
call sites, and renders the disclosure table (plan-20260929-1514Z W4).
A test fails if the checked-in table drifts from this output.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "constants" / "grounded_constants.py"
DISCLOSURE_PATH = ROOT / "constants" / "disclosure.md"


def _load_registry():
    spec = importlib.util.spec_from_file_location(
        "grounded_constants", REGISTRY_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["grounded_constants"] = mod
    spec.loader.exec_module(mod)
    return mod


def _call_sites(names: set[str]) -> dict[str, list[str]]:
    """Map each registered name to `path:line` uses under src/."""
    sites: dict[str, list[str]] = {n: [] for n in names}
    src = ROOT / "src"
    pat = re.compile(r"_gc\.([A-Z][A-Z0-9_]+)")
    for py in sorted(src.rglob("*.py")):
        if py.name == "_grounded_constants.py":
            continue
        rel = py.relative_to(ROOT)
        for i, line in enumerate(py.read_text().splitlines(), 1):
            for m in pat.finditer(line):
                if m.group(1) in sites:
                    sites[m.group(1)].append(f"{rel}:{i}")
    return sites


def _last_commit(path_line: str) -> str:
    path = path_line.split(":")[0]
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%h", "--", path],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        return out.stdout.strip() or "—"
    except subprocess.CalledProcessError:
        return "—"


def render(reg) -> str:
    names = set(reg.REGISTRY)
    sites = _call_sites(names)
    lines = [
        "# Constants disclosure — generated",
        "",
        "Regenerate: `uv run python scripts/gen_constants_disclosure.py`.",
        "Do not edit by hand — the checked-in copy is pinned by test.",
        "",
        "| Constant | Value / procedure | Class | Status | Scoring | Decision-load | Grounding | Call sites | Last-changed |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for c in reg.REGISTRY.values():
        val = (
            f"`{c.procedure}(…)`" if c.procedure
            else f"`{c.value}`" if c.value is not None
            else "—"
        )
        if c.source_key and c.source_key in reg.SOURCES:
            src = reg.SOURCES[c.source_key]
            grounding = f"{c.status} — {src.filename or 'unarchived'}"
            if c.locator:
                grounding += f" ({c.locator})"
        elif c.citation:
            grounding = f"{c.status} — {c.citation}"
        else:
            grounding = c.status
        if c.transitional:
            grounding += f" ⚠ transitional → {c.pending}"
        site_str = ", ".join(sites[c.name]) or "—"
        commits = sorted(
            {_last_commit(s) for s in sites[c.name]}
        )
        commit_str = ", ".join(commits) if commits else "—"
        lines.append(
            f"| `{c.name}` | {val} | {c.cls} | {c.status} | "
            f"{'yes' if c.scoring_path else 'no'} | "
            f"{'yes' if c.decision_load else 'no'} | "
            f"{grounding} | {site_str} | {commit_str} |"
        )
    lines += [
        "",
        "## Sources",
        "",
        "| Key | Citation | Corpus file | sha256 |",
        "| --- | --- | --- | --- |",
    ]
    for key, s in reg.SOURCES.items():
        lines.append(
            f"| `{key}` | {s.citation} | "
            f"{s.filename or '— (not archived)'} | "
            f"`{(s.sha256 or '—')[:12]}` |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    reg = _load_registry()
    text = render(reg)
    if "--check" in sys.argv:
        current = DISCLOSURE_PATH.read_text()
        if current != text:
            print(
                "disclosure.md is stale — run "
                "scripts/gen_constants_disclosure.py",
                file=sys.stderr,
            )
            return 1
        print("disclosure.md is current")
        return 0
    DISCLOSURE_PATH.write_text(text)
    print(f"wrote {DISCLOSURE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
