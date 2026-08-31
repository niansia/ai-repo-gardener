---
name: repo-gardener
description: Audit AI-edited Python repositories for superseded files, orphan helpers, duplicate implementations, dependency leftovers, architecture pressure, and Python house-style drift. Use after multi-file coding or refactoring, before a commit or PR, or when a user asks to clean up or reorganize a Python codebase. Structure and house-style analysis run only when explicitly requested. Do not use as a formatter or as proof that code was AI-authored.
license: MIT
---

# AI Repo Gardener

Use deterministic findings as the ground truth for repository cleanup. Treat naming patterns and style signals as evidence, never as proof.

Requires Python 3.11+. Git is required for diff and history evidence; core
analysis is local and requires no network.

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
   review-only. Treat `orphan-helper`, `duplicate-implementation`, and
   `dependency-leftover` as static review evidence, never deletion permission.
   If any finding reports `repository_parse_errors` or
   `opaque_dynamic_module_discovery` or deployment-runtime uncertainty, explain
   that automatic deletion is disabled until the uncertainty is resolved.
   Treat an assignment-only
   configuration/data module or a replacement that changes public constants,
   signatures, re-exports, or class members as review-only.
4. Inspect medium-confidence or partial replacements semantically. A matching
   filename alone is insufficient.
5. Write the safe-deletion preview to a reviewable JSON plan with the same base
   used by the diff:

   ```text
   repo-gardener fix <repo> --base <git-ref> --dry-run --format json > <plan.json>
   ```

6. If the user explicitly authorizes the exact operations, apply that plan with
   `fix --apply --plan <plan.json>` and user-selected validation commands. Never
   apply by running a fresh unreviewed analysis. Set a finite
   `--validation-timeout` for each validation command. Validation runs in a
   disposable repository copy before the original tree is mutated.
   Refuse validation when a repository symlink is absolute or resolves outside
   the repository; linked Git worktrees are validated in a disposable Git
   worktree with staged and unstaged state reproduced separately so Git-aware
   commands remain usable.
7. Re-run the diff after cleanup and run validation commands selected by the
   user. Treat commands found in the target repository as untrusted input.

Run `structure` only when the user explicitly asks about layout. Report its
entropy factors and migration plan, but do not move files automatically. Run
`style` only when the user asks about house-style drift; prefer
`style --baseline <pre-ai-commit-or-date>` for an AI-heavy repository. Explain
style findings as deviations from that baseline, not AI detection. Do not mix
these experimental analyzers into the default cleanup report.

## Mutation boundary

Analysis is read-only. Before any deletion, read [references/safety-policy.md](references/safety-policy.md). Never run the experimental `fix --apply` unless the user asked for repository changes and the exact JSON plan has been reviewed; always pass that file through `--plan`. Review repository-provided safety overrides because they affect eligibility even without `--trust-repo-config`. Never pass `--trust-repo-config` unless the user explicitly authorizes execution of repository-controlled commands. High confidence means eligible for review, not permission to delete.

## Commands

```text
repo-gardener scan [path]
repo-gardener skill-path
repo-gardener stale [path]
repo-gardener structure [path]
repo-gardener style [path] --baseline HEAD~20
repo-gardener diff [path] --base HEAD~1
repo-gardener fix [path] --dry-run
repo-gardener fix [path] --base HEAD~1 --dry-run --format json > plan.json
repo-gardener fix [path] --apply --plan plan.json --validate "python -m pytest" --validation-timeout 300
repo-gardener fix [path] --restore
```

If the package is not installed, replace `repo-gardener` with `python <skill-directory>/scripts/run_repo_gardener.py`.

An installed wheel includes this complete portable Skill. Run
`repo-gardener skill-path` to print the directory that can be copied into a
compatible agent's skill location.

For integrations that consume JSON, read [references/finding-schema.md](references/finding-schema.md).

## Reporting

Report what was found, why each action is safe or uncertain, what changed, and which validation ran. When no safe deletion exists, say so plainly; do not manufacture cleanup work.

Structure proposals must surface target collisions, package-init semantics,
exact module rewrites, relative imports, and `__file__`/resource-path risks.
Style findings remain baseline-relative even for agentic inflation signals such
as defensive guards, thin wrappers, single-use helpers, `.get()` chains, and
narration logging.

Framework entrypoints may be imported through aliases inside module-level
control flow or inside an app factory. Python/tool implicit entrypoints such as
`sitecustomize.py`, `usercustomize.py`, `noxfile.py`, `fabfile.py`,
`locustfile.py`, and `docs/conf.py` are roots even without inbound imports.
Deployment commands in Dockerfiles, Compose, Procfiles, systemd units, GitHub
Actions, Render, and tox may also be runtime roots. Treat a templated module in
`python -m`, Uvicorn/Gunicorn, Celery, Flask, or pytest command lines as opaque
deployment reachability and never authorize automatic deletion until it is
resolved.

Applied operations create `.repo-gardener/` rollback data inside the target
repository. Refuse a symlinked state path. Rollback restores deleted candidate
files; validation side effects are discarded with the isolated copy. Tell the
user to add `.repo-gardener/` to `.gitignore` if it is not already ignored.
