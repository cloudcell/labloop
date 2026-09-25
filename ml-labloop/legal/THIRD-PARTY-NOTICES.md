# Third-Party Notices

ML-LabLoop includes third-party components that are licensed separately from
ML-LabLoop. These components remain under their respective licenses.

Unless otherwise stated, original ML-LabLoop code and documentation are
licensed under the Apache License 2.0. Third-party components are not relicensed under
the ML-LabLoop Apache-2.0 license. They are included, distributed, or referenced under
the terms of their own licenses.

This file is provided for attribution and license-notice purposes. It is not a
substitute for the full license texts included with each third-party component.

## Components fetched during image construction

The ML-LabLoop build pipeline downloads and installs the following components
into the lab VM template. Their license texts ship inside the built image.

### uv

- License: Apache License 2.0 OR MIT License
- Copyright: Astral
- Installed by: `containers/experiment/Containerfile` (pinned release)

### micromamba

- License: BSD 3-Clause License
- Copyright: QuantStack / mamba contributors
- Installed by: `containers/experiment/Containerfile` (pinned release)

### VSCodium

- License: MIT License
- Copyright: VSCodium contributors; built from Microsoft's MIT-licensed
  vscode sources
- Installed by: `create-lab-template` (VSCodium deb repository)

### opencode

- License: MIT License
- Copyright: SST / opencode contributors
- Installed by: `create-lab-template` (npm package `@opencode/cli`, pinned)

## Operating system and distribution packages

The guest image is built on Linux Mint (Ubuntu/Debian base) and installs
distribution packages including Podman (Apache-2.0), nftables (GPL-2.0), and
TeX Live (LPPL and assorted free licenses). Operating-system packages retain
their own licenses and ship their copyright files inside the image under
`/usr/share/doc/<package>/copyright`.

The `python:3.12-slim` container base image is used for the experiment and MCP
containers; it contains Debian packages and the Python runtime (PSF license),
each under its own license.

## Bundled assets

Any third-party fonts, icons, or similar assets vendored directly into this
repository carry their own license files alongside the asset.
