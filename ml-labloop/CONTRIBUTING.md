# Contributing to ML-LabLoop

Thank you for your interest in contributing to ML-LabLoop.

ML-LabLoop is an early-stage project that builds a secure, reproducible,
per-user ML experimentation lab as a KVM/libvirt VM template. The project is
intended to be useful, understandable, and technically reliable before it
becomes large. Small, well-scoped contributions are preferred.

## Project Status

ML-LabLoop is alpha software. Template layouts, zone boundaries, container
definitions, and security controls may change before a stable release.

Please do not assume that a current internal implementation detail is permanent.

## Before You Contribute

Before opening a pull request, please:

1. Check existing issues and pull requests.
2. Keep the change focused.
3. Re-run the functional and security batteries where the change touches
   guest configuration.
4. Update documentation if the behavior visible to users changes.
5. Avoid mixing refactoring, formatting, and feature work in one pull request.

If you are unsure whether a change fits the project direction, open an issue
first.

## Development Setup

The build pipeline runs on a Linux host with KVM and libvirt:

```bash
git clone https://github.com/cloudcell/ml-labloop.git
cd ml-labloop

./00-build-lab-template.sh
```

The numbered scripts form the release workflow: build the template, prepare it
for cloning, create per-user clones, and upload a release image.

## Running Tests

Guest-side verification is provided by the batteries under `deploy/`:

```text
deploy/functional-battery.sh
deploy/security-battery.sh
```

Both batteries are hard gates in `create-lab-template`: a template is not
frozen unless each finishes with zero failures. Shell scripts should pass
`bash -n` and, where practical, `shellcheck`.

## Code Style

Prefer code that is:

- explicit rather than clever
- deterministic where possible
- easy to verify with the batteries
- careful about security boundaries
- conservative about dependencies

Avoid broad architectural rewrites unless they have been discussed first.

Security-sensitive changes — anything touching the hostile zone, the sudoers
rules, the nftables fence, or the build wrappers — must not weaken isolation.
When in doubt, the strictest interpretation wins.

## Commit Messages

Use clear commit messages. A good commit message explains what changed and
why.

Examples:

```text
Allow DNS egress for the hostile user under slirp4netns
Fix false-positive gateway probe in the security battery
Document the four package installation lanes
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

Unless otherwise agreed in writing, contributions to ML-LabLoop are submitted
under the same license terms that apply to the project, together with the
contributor grant described in the contributor sign-off or CLA documents.

ML-LabLoop is distributed under the Apache License 2.0 unless otherwise stated.

## Trademarks

The software license does not grant trademark rights.

Use of the `LabLoop` name and related marks is governed separately by:

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

This is especially important while ML-LabLoop is still alpha software.
