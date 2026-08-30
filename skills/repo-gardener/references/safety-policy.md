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

## Apply protocol

1. Run `fix --base <ref> --dry-run` with the same base used by `diff`, then
   review the exact paths.
2. Confirm the user's request authorizes deletion, not merely analysis.
3. Run `fix --apply` with at least one meaningful validation command.
4. If validation fails, Repo Gardener restores the snapshot automatically. Verify the failure report.
5. Use `fix --restore` to restore the last successful operation when needed.

Do not treat a clean test run as proof that an unreferenced plugin or public API is unused.

Fix snapshots live under `.repo-gardener/`. Keep that path in `.gitignore` so
rollback data does not become part of the user's source history.
