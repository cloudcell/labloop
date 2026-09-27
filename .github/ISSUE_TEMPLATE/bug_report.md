---
name: Bug report
about: Something in the lab pipeline, the VM, or the MCP services misbehaved
title: ""
labels: [bug]
assignees: []
---

## What happened

<!-- Expected vs. actual behavior, one paragraph. -->

## Where

<!-- What was affected: template build (`create-lab-template`), cloning
     (`02-create-vm-from-template.sh`), a running clone (`lab-vm-*`),
     the MCP servers, the hostile-zone container, the host-side
     scripts? Give the VM/domain name if relevant. -->

## Reproduce

<!-- Smallest sequence that triggers it. Commands run on the host or
     inside the guest — say which. -->

## Evidence

<!--
Inside a lab VM, paste the tail of:

    bash ~/Desktop/check-lab-ready.sh

(PASS/WARN/FAIL counts and the failing check names.)
On the host, paste the failing script's output.
Do NOT paste secrets, tokens, or OTP files — issues are public.
-->

## Impact

<!-- Blocked entirely / degraded / cosmetic. -->
