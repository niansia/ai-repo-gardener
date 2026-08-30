# Repo Gardener

**Find files the AI forgot to delete.**

Repo Gardener is a deterministic garbage collector for AI-edited Python
repositories. It focuses v0.1 on one job: finding superseded iteration files
and newly-created orphan files without turning weak guesses into deletions.

![Repo Gardener diff and safe dry-run demo](docs/demo.gif)

```text
$ repo-gardener diff . --base HEAD~1

[HIGH] stale-file  parser_v2.py -> parser.py
  confidence 100%  risk 0%  action safe_delete_candidate
  - call_site_migration: app.py
  - git_chronology: replacement_added_later
  - inbound_imports: 0

$ repo-gardener fix . --base HEAD~1 --dry-run
DELETE parser_v2.py  (replacement: parser.py, confidence: 100%)
```

The analyzer and fixer deliberately use the same `--base`, so a candidate shown
by `diff` cannot disappear merely because `fix` discarded its Git evidence.

## What v0.1 supports

- `stale-file`: a whole Python file has a reachable, structurally similar
  replacement plus evidence such as call-site migration or Git chronology.
- `orphan-file`: a file changed in the current iteration is unreachable, has
  zero inbound imports, and has no plausible replacement. This is always
  review-only.
- Safe deletion: snapshot, SHA-256 verification, symlink and path-escape
  refusal, stale-plan rejection, required validation, deleted-file restore on
  failure or interruption, and manual restore. Apply remains experimental.
- Both conventional `src/package` imports and literal `src.package` imports.
- Worktree, staged, committed-range, and untracked changes in `diff` mode.
- Built-in entrypoint recognition for FastAPI, Flask, Django, Click, and Typer.
- Alias-aware dynamic import detection, opaque dynamic-discovery blocking, and
  Python packaging script, GUI script, and plugin entry-point roots.
- Stable versioned JSON for agents and CI.
- Python standard library only at runtime; no model call, account, telemetry,
  API key, or network access.

This is not a generic dead-code scanner and does not yet collect unused helper
functions, unused dependencies, or arbitrary duplicate implementations.

## Try the alpha

Python 3.11+ is required.

```bash
python -m pip install .
repo-gardener diff /path/to/project --base HEAD~1
repo-gardener fix /path/to/project --base HEAD~1 --dry-run
```

The bundled Agent Skill is self-contained and can run without installation:

```bash
python skills/repo-gardener/scripts/run_repo_gardener.py diff /path/to/project --base HEAD~1
```

Copy `skills/repo-gardener/` into a compatible agent's skill directory. It
follows the open [Agent Skills specification](https://agentskills.io/specification).

## Commands

| Command | Purpose | Mutation |
| --- | --- | --- |
| `scan` | Run the supported repo-GC rules | Never |
| `stale` | Find superseded Python files | Never |
| `diff [--base <ref>]` | Audit committed, worktree, staged, and untracked iteration changes; base defaults to `HEAD` | Never |
| `fix --base <ref> --dry-run` | Preview matching high-confidence deletions | Never |
| `fix --base <ref> --dry-run --format json` | Emit a machine-readable plan ID and content hashes | Never |
| `fix --base <ref> --apply --validate <cmd>` | Experimental: snapshot, delete, validate, and restore deleted files on failure/interruption | Yes |
| `fix --restore` | Restore the latest operation | Yes |
| `structure` | Run experimental directory analysis explicitly | Never |
| `style --baseline <ref-or-date>` | Run experimental repo-relative Python style analysis | Never |
| `scan --experimental` | Add structure and style to a full scan | Never |

### Experimental analyzers

Structure and style are available for research, but they are not part of the
v0.1 release claim and do not run in `scan` or `diff` unless
`--experimental` is supplied.

Structure findings report directory load and only expose cluster proposals when
the import graph yields at least two credible groups. One giant connected
component is not presented as an architecture plan. A directory over the
configured module threshold is still reported as a factual load warning even
when no reliable clusters exist.

Style findings are deviations from a baseline, never proof of AI authorship.
For repositories already dominated by generated code, provide a pre-AI commit
or date:

```bash
repo-gardener style . --baseline HEAD~20 --confidence all
repo-gardener style . --baseline 2026-01-15 --confidence all
```

The extractor includes Python-specific conventions: builtin vs `typing`
generics, `X | None` vs `Optional[X]`, `Path` vs `os.path`, comprehension vs
manual-loop preference, dataclass/TypedDict vs bare dictionaries, and logging
vs `print`. Fewer than five baseline files produces no finding; fewer than 20
cannot produce high confidence.

## Safety and configuration

Every mutating run requires validation. Protected paths, generated code,
migrations, plugin paths, package APIs, dynamic references, partial
replacements, orphan findings, structure findings, and style findings are never
automatic deletion candidates.

Copy [`repo-gardener.toml.example`](repo-gardener.toml.example) to
`repo-gardener.toml` to declare entrypoints, protected paths, exclusions,
validation commands, and analysis thresholds. `src/` modules remain protected
from automatic deletion by default. Any module inside an importable package is
also treated as a possible external API. A repository owner must explicitly set
both relevant safety overrides after confirming the package is not public.

Validation commands read from the target repository are untrusted and ignored
by default. Prefer explicit `--validate` arguments. `--trust-repo-config` is an
opt-in to execute commands supplied by that repository and should only be used
after review.

Applied fixes store recoverable snapshots under `.repo-gardener/`. Add this
line to the target repository's `.gitignore`:

```gitignore
.repo-gardener/
```

Rollback restores the deleted candidate files. It does not revert unrelated
files that a validation command may modify, and it cannot recover from process
termination that prevents cleanup code from running.

The JSON contract is documented in
[`skills/repo-gardener/references/finding-schema.md`](skills/repo-gardener/references/finding-schema.md).

## Alpha limits

The fixture suite covers stale replacements, partial replacements, dynamic
string paths, plugins, literal `src.*` imports, agent-created orphans, rename
chronology, style baselines, and flat-domain examples. Public-repository
smoke results and exact commit hashes are recorded in
[`benchmarks/real-world-smoke.md`](benchmarks/real-world-smoke.md). That run is
not a labeled precision benchmark. The separate adversarial safety gate is
recorded in [`benchmarks/safety-benchmark.md`](benchmarks/safety-benchmark.md);
it reports eligible-deletion false positives rather than claiming population
precision.

For CI, `--fail-on high`, `--fail-on medium`, and `--fail-on any` use exit codes
`1` for a reached finding threshold and `2` for tool/configuration errors.

## Development

```bash
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

Contributions must add or update a reusable fixture for every false positive or
new evidence rule.

## License

MIT
