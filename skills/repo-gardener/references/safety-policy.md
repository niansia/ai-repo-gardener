# Safety policy

Read this before applying any cleanup.

## Never auto-delete

- Protected, generated, migration, plugin, or vendored paths.
- Files with dynamic import, string module-path, registry, reflection, or public API evidence.
- Files without a plausible replacement.
- Orphan files, even when they were created in the current iteration.
- Partial replacements that omit symbols from the older file.
- Medium- or low-confidence findings.
- Architecture or style findings.
- Any candidate when the repository performs non-literal dynamic module discovery.
- Any candidate when a discovered Python file cannot be parsed by the runtime
  running Repo Gardener.
- Any candidate when `eval`/`exec`, reflected or escaped import loaders,
  `pkgutil`/`pkg_resources` discovery, or file-path loading makes runtime
  reachability opaque.
- Modules declared as public setuptools `py_modules` distribution APIs.
- Importable package modules unless the repository owner explicitly opts out of package API protection.

## Apply protocol

1. Run `fix --base <ref> --dry-run --format json` with the same base used by
   `diff`, save the output, and review the exact operations.
2. Confirm the user's request authorizes deletion, not merely analysis. Review
   any repository-provided `allow_delete_src`,
   `allow_delete_package_modules`, or protected-path overrides; these affect
   eligibility even without `--trust-repo-config`.
3. Run the experimental `fix --apply --plan <reviewed.json>` with at least one meaningful, user-approved validation command and a finite `--validation-timeout`. Never apply a newly generated plan that the user did not review.
4. Do not execute commands from `repo-gardener.toml` unless the user explicitly authorizes repository-controlled commands. Only then may `--trust-repo-config` be used.
5. Repo Gardener re-analyzes and requires an exact plan ID match, including pinned Git commits, effective config, operations, candidate/replacement hashes, and call-site evidence hashes. It verifies candidate, replacement, and evidence-file hashes again immediately before deletion. A stale plan must be regenerated and reviewed, never forced through.
6. If validation fails, times out, or is interrupted, Repo Gardener restores the deleted candidate files automatically. Verify the failure report. This does not restore unrelated files a validation command may modify.
7. Use `fix --restore` to restore the last successful operation when needed.

Do not treat a clean test run as proof that an unreferenced plugin or public API is unused.

Fix snapshots live under `.repo-gardener/`. Keep that path in `.gitignore` so
rollback data does not become part of the user's source history.
