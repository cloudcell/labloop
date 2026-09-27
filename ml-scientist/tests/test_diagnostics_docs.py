"""Diagnostics documentation pins (rc-5 D1).

The lab VM's labloop-export only accepts paths under
/srv/lab/exchange — a prompt staging anywhere else silently falls
back to an unmanifested tarball. These tests pin the convention so
the next prompt edit can't regress it.
"""

from pathlib import Path

DIAGNOSTICS_DIR = Path(__file__).parent.parent / "diagnostics"


def _prompt_files():
    return sorted(DIAGNOSTICS_DIR.glob("*.md"))


def test_diagnostics_dir_nonempty():
    files = _prompt_files()
    assert files, "no diagnostic prompts found — wrong path?"
    assert DIAGNOSTICS_DIR.joinpath("README.md").exists()


def test_no_prompt_stages_outside_exchange():
    """'/home/lab/workspace' staging paths must never come back —
    labloop-export refuses them (rc-5 doc bug)."""
    for f in _prompt_files():
        assert "/home/lab/workspace" not in f.read_text(), (
            f"{f.name} stages diagnostics outside /srv/lab/exchange"
        )


def test_every_prompt_uses_exchange_root():
    """Every diagnostic prompt stages its export under
    /srv/lab/exchange/diagnostics-out/<slug>/."""
    files = [f for f in _prompt_files() if f.name != "README.md"]
    assert files
    for f in files:
        text = f.read_text()
        assert "/srv/lab/exchange/diagnostics-out/" in text, (
            f"{f.name} is missing the exchange-root export path"
        )


def test_readme_documents_the_pin():
    """The README must explain WHY the path is pinned — otherwise a
    future edit 'fixes' it back to a workspace path."""
    text = DIAGNOSTICS_DIR.joinpath("README.md").read_text()
    assert "/srv/lab/exchange" in text
