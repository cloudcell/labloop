# Contributing to ML-Scientist

Thank you for your interest in contributing to ML-Scientist.

ML-Scientist is an early-stage scientific experimentation MCP ecosystem: a set
of servers covering the hypothesis → experiment → evidence → conclusion loop.
The project is intended to be useful, understandable, and technically reliable
before it becomes large. Small, well-scoped contributions are preferred.

## Project Status

ML-Scientist is alpha software. MCP tool surfaces, resource URIs, state
formats, and internal module boundaries may change before a stable release.

Please do not assume that a current internal implementation detail is
permanent.

## Before You Contribute

Before opening a pull request, please:

1. Check existing issues and pull requests.
2. Keep the change focused.
3. Add or update tests where appropriate.
4. Update documentation if the behavior visible to users changes.
5. Avoid mixing refactoring, formatting, and feature work in one pull request.

If you are unsure whether a change fits the project direction, open an issue
first.

## Development Setup

A typical local setup is:

```bash
git clone https://github.com/cloudcell/ml-scientist.git
cd ml-scientist

uv sync
```

This creates a `.venv` and installs all locked dependencies.

## Running the Servers

Each server has a foreground launcher:

```bash
./run-ml-episteme.sh
```

Ports come from `ports.env` — edit that file to move ports, never the scripts.
The `./labloop` script manages the whole stack as a lifecycle layer
(`start`, `stop`, `restart`, `status`, `logs`).

## Running Tests

```bash
uv run pytest -q        # parallel (pytest-xdist)
uv run pytest -n0 -q    # serial fallback
```

Pull requests should keep the test suite passing.

## Code Style

Prefer code that is:

- explicit rather than clever
- deterministic where possible
- easy to test
- careful about MCP tool and state boundaries
- conservative about dependencies

Avoid broad architectural rewrites unless they have been discussed first.

## Commit Messages

Use clear commit messages. A good commit message explains what changed and
why.

Examples:

```text
Add claims provenance check to anamnesis write path
Fix session digest to include adaptor reconnect state
Document the status_report convention
```

## Contributor Sign-off and CLA

Contributions require contributor sign-off.

Small individual contributions may use the lightweight contributor sign-off
process described in:

```text
legal/CONTRIBUTOR-SIGNOFFS.md
```

For substantial contributions, corporate contributions, or contributions where
Cloudcell Limited requests it, a signed contributor license agreement may be
required:

```text
legal/CONTRIBUTOR-CLA.md
```

By contributing, you represent that you have the right to submit the
contribution and that your contribution does not knowingly violate third-party
rights.

## License of Contributions

Unless otherwise agreed in writing, contributions to ML-Scientist are
submitted under the same license terms that apply to the project, together
with the contributor grant described in the contributor sign-off or CLA
documents.

ML-Scientist is distributed under the Apache License 2.0 unless otherwise stated.

## Trademarks

The software license does not grant trademark rights.

ML-Scientist is distributed as a component of the LabLoop package. Use of
the `LabLoop` name and related marks is governed by the LabLoop trademark
policy; see:

```text
legal/TRADEMARKS.md
```

## Security Issues

Please do not report security vulnerabilities through public GitHub issues.

See:

```text
SECURITY.md
```

## Maintainer Discretion

Maintainers may decline contributions that are out of scope, too large to
review safely, insufficiently tested, inconsistent with project direction, or
likely to create long-term maintenance burden.

This is especially important while ML-Scientist is still alpha software.
