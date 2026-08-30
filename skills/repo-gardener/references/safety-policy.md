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
- Importable package modules unless the repository owner explicitly opts out of package API protection.

## Apply protocol

1. Run `fix --base <ref> --dry-run` with the same base used by `diff`, then
   review the exact paths.
2. Confirm the user's request authorizes deletion, not merely analysis.
3. Run the experimental `fix --apply` with at least one meaningful, user-approved validation command.
4. Do not execute commands from `repo-gardener.toml` unless the user explicitly authorizes repository-controlled commands. Only then may `--trust-repo-config` be used.
5. Repo Gardener rechecks candidate and replacement SHA-256 values before deletion. A stale plan must be regenerated, never forced through.
6. If validation fails or is interrupted, Repo Gardener restores the deleted candidate files automatically. Verify the failure report. This does not restore unrelated files a validation command may modify.
7. Use `fix --restore` to restore the last successful operation when needed.

Do not treat a clean test run as proof that an unreferenced plugin or public API is unused.

Fix snapshots live under `.repo-gardener/`. Keep that path in `.gitignore` so
rollback data does not become part of the user's source history.
