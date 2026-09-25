# Security Policy

## Supported Versions

ML-Scientist is currently alpha software.

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
ML-Scientist Security Report
```

Please include as much detail as reasonably possible:

- affected version, commit, or branch
- operating system and Python version
- which server or component is affected (anamnesis, episteme, zetesis,
  arete, agora, or labloop tooling)
- steps to reproduce
- proof-of-concept input, MCP request, file, or command if available
- expected behavior
- observed behavior
- security impact
- whether the issue is public or privately discovered

## Response Expectations

ML-Scientist is an early-stage project, so response times are best effort.

The intended process is:

1. Acknowledge the report.
2. Reproduce and assess the issue.
3. Prepare a fix or mitigation where appropriate.
4. Credit the reporter if desired and appropriate.
5. Publish a security note if the issue affects public users.

## Scope

Security-relevant issues may include, but are not limited to:

- arbitrary code execution through MCP tool inputs
- unsafe loading, parsing, or deserialization of experiment data
- path traversal outside designated state or workspace directories
- command injection through tool parameters or shell wrappers
- tampering with scientific state, provenance records, or claims stores
- exposure of credentials, environment variables, or local files
- cross-server privilege escalation between trusted and untrusted components
- denial-of-service issues caused by malformed MCP requests
- dependency vulnerabilities with practical impact on ML-Scientist

## Out of Scope

The following are usually out of scope unless they demonstrate a concrete
security impact:

- missing hardening in alpha-only development scripts
- issues requiring already-compromised local machines
- denial-of-service from intentionally huge inputs without a specific parser or
  validation flaw
- reports generated only by automated scanners without analysis
- social engineering or phishing
- risks inherent to running ML-Scientist inside a properly deployed LabLoop
  environment that are already documented in the LabLoop security manual

## Coordinated Disclosure

Please give the maintainers a reasonable opportunity to investigate and fix
reported vulnerabilities before public disclosure.

## No Warranty

ML-Scientist is provided without warranty. See the project license for full
terms.
