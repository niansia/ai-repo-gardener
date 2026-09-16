# Changelog

All notable changes to AI Repo Gardener are recorded here. Versions follow
[PEP 440](https://peps.python.org/pep-0440/); the Git tag for `X.Y.ZaN` is
`vX.Y.Z-alpha.N`. Alpha releases may change analysis output, and any finding
schema change is called out explicitly.

## 0.1.0a12 — unreleased

Continuous maintenance: adopt the tool on a repository that already has
leftovers, gate CI on new findings only, and read results where review happens.

### Added

- `accept` command and `--accepted <ledger>` option. `accept` records the
  current findings as a sorted, content-addressed accepted-findings ledger
  (`repo-gardener-accepted.json` by default), carrying reviewer `note` fields
  forward and dropping entries that no longer reproduce. Analysis commands
  suppress accepted findings, keep them out of `--fail-on`, and report
  `metrics.accepted_findings` with the suppressed count and the unmatched
  entries worth pruning.
- `--format sarif` on every analysis command. SARIF 2.1.0 with
  repository-relative paths, per-rule metadata and help URIs, severity-mapped
  levels, definition-line regions, and stable `partialFingerprints` so GitHub
  code scanning tracks one alert across runs instead of reopening it.
- Reusable composite GitHub Action (`action.yml`). It installs the exact commit
  it is pinned to (or a published PyPI version through `version`), mirrors the
  CLI inputs, writes SARIF, prints a Markdown job summary, and outputs
  `sarif-file`, `findings`, and `exit-code`.
- `python -m repo_gardener.sarif_summary <file>`, the Markdown summarizer used
  by the action and runnable against any repo-gardener SARIF file. It ships in
  the wheel, so the action does not depend on a source checkout.
- CI dogfoods the ledger round trip and the SARIF contract, and runs the
  reusable action against this repository on every push and pull request.

### Changed

- Reviewed deletion plans are schema version `3` and pin `accepted_sha256`.
  Editing the ledger invalidates a plan reviewed against a different one, and
  plans written by earlier versions are rejected with an explicit error.
- `fix` withholds accepted findings from deletion. An accepted finding means
  "reviewed, keep it", so it can never become a deletion candidate.

### Safety

- A ledger is never loaded implicitly: a file in the repository suppresses
  nothing until `--accepted` names it on the command line.
- A ledger can only remove deletion candidates, never add them. It cannot raise
  confidence, lower risk, unlock a protected path, or authorize an apply.
- An unparseable ledger, a repeated finding ID, an unexpected schema version, or
  a malformed entry is a configuration error, not an empty ledger. `accept`
  refuses to overwrite a ledger it could not read.

## 0.1.0a11 and earlier

Earlier alphas are documented in the
[release notes](https://github.com/niansia/ai-repo-gardener/releases) and the
Git history. Highlights: deployment and packaging runtime-reference discovery,
the reviewed-plan contract with isolated-copy validation, the adversarial safety
benchmark, the labeled corpus, the portable Agent Skill bundled in the wheel,
and the review-only structure and house-style analyzers.
