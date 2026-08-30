---
name: repo-gardener
description: Audit AI-edited Python repositories for superseded iteration files and newly-created orphan files, then safely preview evidence-backed cleanup. Use after multi-file coding or refactoring, before a commit or PR, or when a user asks to clean up a Python codebase. Experimental structure and house-style analysis are available only when explicitly requested. Do not use as a formatter or as proof that code was AI-authored.
---

# Repo Gardener

Use deterministic findings as the ground truth for repository cleanup. Treat naming patterns and style signals as evidence, never as proof.

## Workflow

1. Identify the repository root and preserve unrelated user changes.
2. When Git history is available, choose the commit immediately before the
   agent iteration and run the bundled diff audit in JSON mode:

   ```text
   python <skill-directory>/scripts/run_repo_gardener.py diff <repo> --base <git-ref> --format json --confidence all
   ```

   Use `stale <repo>` only when no usable Git base exists. It intentionally
   cannot produce Git-backed automatic deletion confidence.

3. Prioritize `stale-file` findings with a replacement, multiple independent
   evidence items, high confidence, and low risk. Treat every `orphan-file` as
   review-only.
4. Inspect medium-confidence or partial replacements semantically. A matching
   filename alone is insufficient.
5. Preview safe deletions with the same base used by the diff:

   ```text
   repo-gardener fix <repo> --base <git-ref> --dry-run
   ```

6. Re-run the diff after cleanup and run the repository's own tests or
   configured validation commands.

Run `structure` only when the user explicitly asks about layout. Run `style`
only when the user asks about house-style drift; prefer
`style --baseline <pre-ai-commit-or-date>` for an AI-heavy repository. Explain
style findings as deviations from that baseline, not AI detection. Do not mix
these experimental analyzers into the default cleanup report.

## Mutation boundary

Analysis is read-only. Before any deletion, read [references/safety-policy.md](references/safety-policy.md). Never run `fix --apply` unless the user asked for repository changes and the exact deletion plan has been reviewed. High confidence means eligible for review, not permission to delete.

## Commands

```text
repo-gardener scan [path]
repo-gardener stale [path]
repo-gardener structure [path]
repo-gardener style [path] --baseline HEAD~20
repo-gardener diff [path] --base HEAD~1
repo-gardener fix [path] --base HEAD~1 --dry-run
repo-gardener fix [path] --base HEAD~1 --apply --validate "python -m pytest"
repo-gardener fix [path] --restore
```

If the package is not installed, replace `repo-gardener` with `python <skill-directory>/scripts/run_repo_gardener.py`.

For integrations that consume JSON, read [references/finding-schema.md](references/finding-schema.md).

## Reporting

Report what was found, why each action is safe or uncertain, what changed, and which validation ran. When no safe deletion exists, say so plainly; do not manufacture cleanup work.

Applied operations create `.repo-gardener/` rollback data inside the target
repository. Tell the user to add `.repo-gardener/` to `.gitignore` if it is not
already ignored.
