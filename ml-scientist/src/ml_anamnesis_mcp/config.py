"""Configuration — minimal loader for the [integrity] table.

anamnesis is otherwise CLI-configured; the only config-file surface is
integrity-log retention. Precedence follows the ecosystem convention:
./ml-anamnesis.toml then ~/.ml-anamnesis/config.toml. Absent file or
table → defaults (log_max_files = 100).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def load_config() -> dict[str, Any]:
    """Read the full anamnesis config file (./ml-anamnesis.toml, then
    ~/.ml-anamnesis/config.toml). Absent or malformed file → {}."""
    for candidate in [
        Path.cwd() / "ml-anamnesis.toml",
        Path.home() / ".ml-anamnesis" / "config.toml",
    ]:
        if not candidate.exists():
            continue
        try:
            import tomllib

            with open(candidate, "rb") as f:
                return tomllib.load(f)
        except Exception:
            return {}
    return {}


def load_integrity_config() -> dict[str, Any]:
    """Read the [integrity] table from the anamnesis config file."""
    return load_config().get("integrity", {})
