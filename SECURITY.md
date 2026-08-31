# Security policy

## Supported versions

AI Repo Gardener is currently an alpha. Only the latest GitHub prerelease receives safety and security fixes.

## Reporting a vulnerability

Use GitHub's **Report a vulnerability** form in the repository Security tab for confidential reports. Do not open a public issue when a report contains an exploitable deletion path, repository escape, command-execution issue, credential, or private source code.

Include the affected version, operating system, Python version, a minimal sanitized repository, the exact command, and the JSON finding or reviewed plan when available. Please state whether the original repository was modified.

Non-confidential false-positive regressions may use the public **Safety regression** issue template.

You should receive an acknowledgement within 72 hours. A validated destructive-safety issue blocks the next release until a regression test and fix are available. Please allow time for a coordinated fix before public disclosure.

## Safety boundary

Analysis is local and read-only. Deletion requires an explicitly reviewed JSON plan and validation in an isolated workspace. Repository configuration and validation commands are untrusted input. See the portable Skill's `references/safety-policy.md` for the complete mutation protocol.

Official release wheels are built once, tested across the supported platform/Python matrix, provenance-attested, and attached to an immutable GitHub Release. Verify an artifact with:

```text
gh attestation verify <wheel> --repo niansia/ai-repo-gardener
```
