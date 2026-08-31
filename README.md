![AI Repo Gardener — Find files the AI forgot to delete](https://raw.githubusercontent.com/niansia/ai-repo-gardener/main/docs/hero.svg)

<p align="center">
  <strong>English</strong> ·
  <a href="https://github.com/niansia/ai-repo-gardener/blob/main/README.zh-TW.md">繁體中文</a> ·
  <a href="https://github.com/niansia/ai-repo-gardener/blob/main/README.zh-CN.md">简体中文</a> ·
  <a href="https://github.com/niansia/ai-repo-gardener/blob/main/README.ja.md">日本語</a>
</p>

<p align="center">
  <a href="https://github.com/niansia/ai-repo-gardener/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/niansia/ai-repo-gardener/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://pypi.org/project/repo-gardener/"><img alt="PyPI" src="https://img.shields.io/pypi/v/repo-gardener?include_prereleases"></a>
  <a href="https://pypi.org/project/repo-gardener/"><img alt="Python 3.11–3.14" src="https://img.shields.io/pypi/pyversions/repo-gardener"></a>
  <a href="https://github.com/niansia/ai-repo-gardener/releases/tag/v0.1.0-alpha.11"><img alt="GitHub prerelease" src="https://img.shields.io/github/v/release/niansia/ai-repo-gardener?include_prereleases&label=release"></a>
</p>

AI Repo Gardener is a deterministic garbage collector and review Skill for
AI-edited Python repositories. It finds superseded files, forgotten helpers,
duplicate implementations, dependency leftovers, folder pressure, and code
that drifts away from the repository's own Python style—without calling a
model, uploading source, or turning weak guesses into deletions.

> **Release status:** `0.1.0a11` is the eleventh alpha in the **v0.1** line.
> Stable `0.1.0` has not been released yet. Repo GC is the core alpha feature;
> architecture and house-style analysis are experimental and review-only.

## Run it in 30 seconds

Python 3.11+ is required.

```bash
python -m pip install "repo-gardener==0.1.0a11"
repo-gardener diff .
repo-gardener fix . --dry-run
```

Or run it once without installing:

```bash
uvx --from "repo-gardener==0.1.0a11" repo-gardener diff .
```

`diff` and `fix` both default to `--base HEAD`, so the evidence shown in the
review naturally carries into the dry-run. Use the same explicit Git ref on
both commands when auditing another range.

![A real diff and safe dry-run from AI Repo Gardener](https://raw.githubusercontent.com/niansia/ai-repo-gardener/main/docs/demo.gif)

## Three evidence systems

| System | What it answers | Status | Start here |
| --- | --- | --- | --- |
| **Repo GC** | What did the latest AI iteration replace, abandon, duplicate, or leave declared? | Core alpha | `repo-gardener diff .` |
| **Architecture Gardener** | Is one directory carrying too much unrelated code, and what move plan is plausible? | Experimental, review-only | `repo-gardener structure . --confidence all` |
| **House-style Gardener** | Which Python files drift from this repository's own baseline? | Experimental, review-only | `repo-gardener style . --baseline HEAD~20 --confidence all` |

Repo GC includes `stale-file`, `orphan-file`, `orphan-helper`,
`duplicate-implementation`, and `dependency-leftover`. Structure produces
scored, non-mutating migration proposals. Style compares Python-specific
signals—typing, paths, comprehensions, output, complexity, wrappers, defensive
guards, naming, and more—against repository peers or a pre-AI commit/date. A
style finding is drift evidence, never proof that AI wrote a file.

## Use it as an Agent Skill

The PyPI wheel contains the complete portable Skill. Install the CLI first,
then locate its bundled copy:

```bash
python -m pip install "repo-gardener==0.1.0a11"
repo-gardener skill-path
```

Choose **one** target for the agent you use. Project-level targets use the same
directory names inside a repository.

| Agent | Personal Skill target | Project Skill target | Invoke |
| --- | --- | --- | --- |
| [OpenAI Codex](https://developers.openai.com/codex/skills/) | `~/.agents/skills/repo-gardener` | `.agents/skills/repo-gardener` | `$repo-gardener` or `/skills` |
| [Claude Code](https://code.claude.com/docs/en/skills) | `~/.claude/skills/repo-gardener` | `.claude/skills/repo-gardener` | `/repo-gardener` |
| [Cursor](https://cursor.com/docs/skills) | `~/.cursor/skills/repo-gardener` | `.cursor/skills/repo-gardener` | `/repo-gardener` |

### macOS / Linux

```bash
SKILL_SOURCE="$(repo-gardener skill-path)"

# OpenAI Codex
mkdir -p "$HOME/.agents/skills/repo-gardener"
cp -R "$SKILL_SOURCE"/. "$HOME/.agents/skills/repo-gardener/"

# Claude Code — use this instead when Claude is your host
mkdir -p "$HOME/.claude/skills/repo-gardener"
cp -R "$SKILL_SOURCE"/. "$HOME/.claude/skills/repo-gardener/"

# Cursor — use this instead when Cursor is your host
mkdir -p "$HOME/.cursor/skills/repo-gardener"
cp -R "$SKILL_SOURCE"/. "$HOME/.cursor/skills/repo-gardener/"
```

### Windows PowerShell

```powershell
$SkillSource = repo-gardener skill-path

# Pick one target:
$Target = "$HOME\.agents\skills\repo-gardener" # OpenAI Codex
# $Target = "$HOME\.claude\skills\repo-gardener" # Claude Code
# $Target = "$HOME\.cursor\skills\repo-gardener" # Cursor

New-Item -ItemType Directory -Force $Target | Out-Null
Get-ChildItem -Force $SkillSource | Copy-Item -Destination $Target -Recurse -Force
```

Do not install duplicate copies into several locations scanned by the same
agent. The repository copy at `skills/repo-gardener/` is also self-contained
and follows the open [Agent Skills specification](https://agentskills.io/specification).

## Review first, apply exactly what you reviewed

Read-only commands never mutate the repository. A deletion requires an exact
JSON plan, a second matching analysis, and a validation command:

```bash
repo-gardener fix . --dry-run --format json > reviewed-plan.json

# Review reviewed-plan.json, then apply that exact plan:
repo-gardener fix . --apply \
  --plan reviewed-plan.json \
  --validate "python -m pytest" \
  --validation-timeout 300
```

On PowerShell, either keep the apply command on one line or replace each `\`
with a PowerShell backtick. Apply validates the proposed deletion in an
isolated copy before touching the original, then reverifies plan identity and
file hashes. Failed validation leaves the original unchanged. Successful
operations retain a recoverable snapshot for `repo-gardener fix . --restore`.

The plan pins the base ref and SHA, HEAD SHA, effective configuration,
operation set, candidate/replacement hashes, and evidence-file hashes. A
changed repository produces a different plan and is refused.

## Commands

| Command | Purpose | Mutates files? |
| --- | --- | --- |
| `scan .` | Run the supported Repo GC rules | No |
| `stale .` | Focus on file-, symbol-, duplicate-, and dependency-level GC | No |
| `diff . [--base <ref>]` | Audit committed, staged, worktree, and untracked iteration changes | No |
| `fix . --dry-run` | Preview eligible high-confidence deletions | No |
| `fix . --dry-run --format json` | Create the reviewed plan contract | No |
| `fix . --apply --plan <json> --validate <cmd>` | Validate and apply the exact reviewed plan | **Yes** |
| `fix . --restore` | Restore the latest deletion operation | **Yes** |
| `structure . --confidence all` | Run explicit architecture analysis | No |
| `style . --baseline <ref-or-date> --confidence all` | Run explicit baseline-relative style analysis | No |
| `scan . --experimental` | Add structure and style to the full scan | No |
| `skill-path` | Print the portable Skill bundled in the wheel | No |

All reporting commands support stable JSON for agents and CI. `--fail-on high`,
`--fail-on medium`, and `--fail-on any` return exit code `1` when the threshold
is reached; tool and configuration errors return `2`.

## Evidence, not vibes

These numbers describe published, reproducible gates—not population-wide
accuracy claims.

| Published gate | Current result |
| --- | --- |
| Source suite | **182 tests** |
| Destructive-safety variants | **0 / 59 eligible-deletion false positives** |
| Curated labeled corpus | **10 TP, 0 FP, 0 FN, 10 TN**; precision and recall 100% in that corpus |
| Release wheel | The same wheel tested on **12 OS/Python combinations**: Ubuntu, Windows, macOS × Python 3.11–3.14 |
| Pinned real repositories | requests, Flask, pandas, Django, FastAPI, pytest, and Pydantic; **0 automatic-deletion candidates** |
| Hosted style benchmark | 159.11s → 97.51s from alpha.10 to alpha.11, **38.7% faster** with identical findings |

Read the exact fixtures, commits, machine details, and caveats:

- [Adversarial safety gate](https://github.com/niansia/ai-repo-gardener/blob/main/benchmarks/safety-benchmark.md)
- [Labeled precision/recall corpus](https://github.com/niansia/ai-repo-gardener/blob/main/benchmarks/labeled-corpus.md)
- [Pinned real-world smoke and performance runs](https://github.com/niansia/ai-repo-gardener/blob/main/benchmarks/real-world-smoke.md)

## Safety boundary

AI Repo Gardener is intentionally conservative:

- Parse errors, opaque/dynamic loading, unresolved packaging metadata, and
  templated deployment commands disable automatic deletion repository-wide.
- Framework roots, packaging entry points, public/package APIs, plugins,
  runtime strings, generated code, migrations, and partial replacements are
  protected or review-only.
- By default, only files at the repository root can pass the automatic-delete
  risk gate. Files under `app/`, `src/`, packages, and namespace packages stay
  review-only unless the repository owner explicitly changes both relevant
  safety settings.
- Architecture and style findings never move or delete files.
- The tool has zero runtime dependencies, makes no model or network call, and
  sends no source code or telemetry.

For package-internal applications that have been reviewed, the two explicit
overrides are:

```toml
[safety]
allow_delete_src = true
allow_delete_package_modules = true
```

Copy [`repo-gardener.toml.example`](https://github.com/niansia/ai-repo-gardener/blob/main/repo-gardener.toml.example) to
`repo-gardener.toml` for entrypoints, protected paths, exclusions, validation,
and thresholds. Prefer explicit `--validate` commands. Repository-provided
validation commands are ignored unless `--trust-repo-config` is supplied.

Applied operations write recoverable state under `.repo-gardener/`; add it to
the target repository's `.gitignore`:

```gitignore
.repo-gardener/
```

For the complete mutation policy and JSON contract, see the
[safety policy](https://github.com/niansia/ai-repo-gardener/blob/main/skills/repo-gardener/references/safety-policy.md) and
[finding schema](https://github.com/niansia/ai-repo-gardener/blob/main/skills/repo-gardener/references/finding-schema.md). Report a
safety issue through [`SECURITY.md`](https://github.com/niansia/ai-repo-gardener/blob/main/SECURITY.md).

## Development

```bash
git clone https://github.com/niansia/ai-repo-gardener.git
cd ai-repo-gardener
python -m pip install -e ".[dev]"
python -m pytest
ruff check .
ruff format --check .
python skills/repo-gardener/scripts/run_repo_gardener.py scan . --confidence all
```

The demo asset is reproducible with Pillow installed:

```bash
python scripts/render_demo.py
```

Contributions are welcome. Every false-positive fix or new evidence rule must
include a reusable fixture. Read [`CONTRIBUTING.md`](https://github.com/niansia/ai-repo-gardener/blob/main/CONTRIBUTING.md) before
opening a pull request.

## License

[MIT](https://github.com/niansia/ai-repo-gardener/blob/main/LICENSE)
