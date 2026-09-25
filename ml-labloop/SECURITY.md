# Security Policy

## Supported Versions

ML-LabLoop is currently alpha software.

At this stage, security fixes are generally applied to the current development
version only. Formal long-term support policies may be introduced after the
project reaches stable releases.

| Version | Supported |
| ------- | --------- |
| current alpha branch | Best effort |
| older unreleased snapshots | No |

## Reporting a Vulnerability

Please do not report security vulnerabilities through public GitHub issues.

Report suspected vulnerabilities by email:

```text
alex@cloudcell.nz
```

Suggested subject:

```text
ML-LabLoop Security Report
```

Please include as much detail as reasonably possible:

- affected version, commit, or branch
- host operating system and libvirt/QEMU version
- guest image or template version where relevant
- steps to reproduce
- proof-of-concept input, file, script, or command if available
- expected behavior
- observed behavior
- security impact
- whether the issue is public or privately discovered

## Response Expectations

ML-LabLoop is an early-stage project, so response times are best effort.

The intended process is:

1. Acknowledge the report.
2. Reproduce and assess the issue.
3. Prepare a fix or mitigation where appropriate.
4. Credit the reporter if desired and appropriate.
5. Publish a security note if the issue affects public users.

## Scope

Security-relevant issues may include, but are not limited to:

- escape from the hostile experiment container into the VM driver zone
- escape from the lab VM to the host
- bypass of the nftables egress fencing applied to the hostile user
- privilege escalation via `labloop-exec`, `labloop-build`, or the sudoers rules
- access to trusted MCP state or services from the hostile zone
- exposure of secrets, credentials, or host files inside template images
- persistence of hostile artifacts across template rebuilds or clone resets
- unsafe parsing or execution of experiment artifacts on the driver side

## Out of Scope

The following are usually out of scope unless they demonstrate a concrete
security impact:

- missing hardening in alpha-only development scripts
- issues requiring an already-compromised host or already-root guest
- denial-of-service from intentionally huge inputs without a specific flaw
- reports generated only by automated scanners without analysis
- social engineering or phishing
- risks that are documented and accepted in `deploy/SECURITY-MANUAL.md` or
  `deploy/SECURITY-COMPLIANCE-NOTE.md`, unless a concrete bypass is shown

## Coordinated Disclosure

Please give the maintainers a reasonable opportunity to investigate and fix
reported vulnerabilities before public disclosure.

## No Warranty

ML-LabLoop is provided without warranty. See the project license for full terms.
