"""Sealed-path policy — the shared matcher + deny resolver.

One predicate, two consumers: the executor arms runtime denies inside
the mount namespace and ``capture_executed_code`` classifies manifest
roles — both go through ``sealed_match`` so the path that would be
denied is exactly the path that gets classified ``role=sealed``.
Semantics are fnmatch: ``*`` crosses ``/``. (glob's ``*`` does NOT —
arming via glob while classifying via fnmatch would silently
under-arm nested matches; see
plan-20260926-1950Z--sealed-path-runtime-deny.)
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

# Matches at/under these prefixes would deny the interpreter, the
# namespace plumbing, or the whole filesystem — a config error, not
# a policy the sandbox can apply. Refused at launch, never skipped.
_CRITICAL_ROOTS = (
    "/", "/usr", "/bin", "/sbin", "/lib", "/lib64",
    "/etc", "/proc", "/sys", "/dev",
)
# Interpreter internals (stdlib, site-packages, sitecustomize under
# /etc/pythonX.Y) — same regex the trace classifier uses.
_INTERP_RE = re.compile(r"/(?:etc|lib|lib64)/python\d+\.\d+/")
_WILDCARD_RE = re.compile(r"[*?[]")
# Bound on filesystem entries visited while expanding a wildcard
# pattern. A pattern whose literal prefix admits more is unbounded
# enumeration — refused fail-closed rather than walked forever.
_ENUMERATION_LIMIT = 4096


def sealed_match(path: str, patterns: list[str]) -> bool:
    """True when a resolved absolute path hits the deny-list."""
    return any(fnmatch.fnmatch(path, p) for p in patterns)


def _literal_prefix(pattern: str) -> str:
    """The leading non-wildcard directory of a pattern — its
    enumeration root. "" when the pattern is relative."""
    idx = min(
        (m.start() for m in _WILDCARD_RE.finditer(pattern)),
        default=len(pattern),
    )
    prefix = pattern[:idx]
    if "/" not in prefix:
        return ""
    return prefix[: prefix.rfind("/") + 1]


def _is_critical(path: str) -> bool:
    if _INTERP_RE.search(path):
        return True
    return any(
        path == root or path.startswith(root + "/")
        for root in _CRITICAL_ROOTS
    )


def _kind(path: str) -> str:
    # A symlinked dir arms as a file deny — tmpfs onto a symlink dest
    # is not a valid mount; bind-data over a symlink works (verified).
    return "dir" if os.path.isdir(path) and not os.path.islink(path) else "file"


def resolve_sealed_denies(
    patterns: list[str],
) -> tuple[list[tuple[str, str]], list[str], str | None]:
    """Resolve sealed patterns to concrete deny mounts.

    Returns ``(denies, unmatched, error)``:

    - ``denies``: deduped ``(path, kind)`` pairs — ``"dir"`` →
      ``--tmpfs``, ``"file"`` → ``--perms 000 --bind-data``. For each
      match BOTH the visited path and its resolved form are armed, so
      a symlink alias is denied along with its target.
    - ``unmatched``: patterns (or literal paths) that armed nothing —
      recorded, never armed: a mount on a nonexistent dest would let
      bwrap *create* the mountpoint on the writable host root under
      sandbox="minimal".
    - ``error``: a launch-refusal reason (critical-root match,
      unbounded enumeration), or None.
    """
    denies: list[tuple[str, str]] = []
    unmatched: list[str] = []
    seen: set[str] = set()

    def _arm(path: str) -> str | None:
        """Arm one concrete path (+its resolved form). Error string
        on a critical-root match."""
        forms = {path}
        try:
            forms.add(str(Path(path).resolve()))
        except OSError:
            pass
        for form in forms:
            if _is_critical(form):
                return (
                    f"sealed pattern resolved to a critical path "
                    f"{form!r} — refusing to launch"
                )
            if form not in seen:
                seen.add(form)
                denies.append((form, _kind(form)))
        return None

    for pat in patterns:
        if not _WILDCARD_RE.search(pat):
            if os.path.lexists(pat):
                err = _arm(pat)
                if err:
                    return [], unmatched, err
            else:
                unmatched.append(pat)
            continue

        if not pat.startswith("/"):
            # Patterns match resolved absolute paths — a relative
            # pattern can never fire. Inert, recorded.
            unmatched.append(pat)
            continue

        prefix = _literal_prefix(pat)
        if prefix in ("", "/") or _is_critical(prefix.rstrip("/")):
            return [], unmatched, (
                f"sealed pattern {pat!r} admits unbounded or critical "
                f"enumeration (literal prefix {prefix or '<none>'!r}) "
                "— refusing to launch"
            )
        if not os.path.isdir(prefix):
            unmatched.append(pat)
            continue

        visited = 0
        found = False
        err = None
        for dirpath, dirnames, filenames in os.walk(prefix):
            for name in dirnames + filenames:
                visited += 1
                if visited > _ENUMERATION_LIMIT:
                    return [], unmatched, (
                        f"sealed pattern {pat!r} enumeration exceeded "
                        f"{_ENUMERATION_LIMIT} entries under {prefix!r} "
                        "— refusing to launch"
                    )
                cand = os.path.join(dirpath, name)
                try:
                    rp = str(Path(cand).resolve())
                except OSError:
                    continue
                # Classification matches on the RESOLVED path — arm
                # on the same predicate.
                if fnmatch.fnmatch(rp, pat):
                    found = True
                    err = _arm(cand)
                    if err:
                        return [], unmatched, err
            if err:
                break
        if not found:
            unmatched.append(pat)

    return denies, unmatched, None
