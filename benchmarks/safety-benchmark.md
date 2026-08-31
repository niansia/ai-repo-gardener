# Adversarial safety benchmark

Run on 2026-08-31 with Python 3.12. The suite passes 98 tests on Windows, where
three real-symlink tests are skipped when the account cannot create symlinks,
and all 101 tests on Linux. This table isolates twenty-four cases where a normal
import graph can make live or user-modified code look unused.

| Scenario | Required outcome | Result |
| --- | --- | --- |
| `import_module` imported directly, imported with an alias, or assigned through two aliases | Keep live module | Pass |
| Non-literal `importlib.import_module(name)` | Disable automatic deletion repo-wide | Pass |
| Repository Python parse error | Keep findings but disable automatic deletion repo-wide | Pass |
| `eval` or `exec` runtime execution | Disable automatic deletion repo-wide | Pass |
| `getattr(importlib, "import_module")` reflection | Disable automatic deletion repo-wide | Pass |
| `builtins.__import__`, including assigned/imported aliases | Disable automatic deletion repo-wide | Pass |
| `pkgutil.iter_modules` or `walk_packages` discovery | Disable automatic deletion repo-wide | Pass |
| Module-owner aliases such as `il = importlib` and `b = builtins` | Disable automatic deletion when the eventual loader target is non-literal | Pass |
| Known loader callable stored in a dict/list or passed as a value | Disable automatic deletion repo-wide | Pass |
| Aliased `pkg_resources.iter_entry_points` discovery | Disable automatic deletion repo-wide | Pass |
| `runpy.run_path`, `spec_from_file_location`, or importlib file loaders | Disable automatic deletion repo-wide | Pass |
| `[project.entry-points.*]` plugin | Keep registered module | Pass |
| `setup.cfg` plugin entry point | Keep registered module | Pass |
| Literal `setup.py` plugin entry point, including assigned setup aliases | Keep registered module | Pass |
| Setuptools `py-modules`/`py_modules` public distribution module | Review only; never auto-delete | Pass |
| Non-literal `setup.py` packaging metadata | Disable automatic deletion repo-wide | Pass |
| `runpy.run_module("module")` | Keep referenced module | Pass |
| Framework/registry module-shaped string | Keep matching repository module | Pass |
| Importable package submodule outside `src/` | Review only by default | Pass |
| Declared or implicit PEP 420 namespace package submodule | Review only by default | Pass |
| Candidate modified in the current worktree | Review only | Pass |
| Partial replacement missing public symbols | Review only | Pass |
| Monkeypatch string module path | Keep referenced module | Pass |
| Framework-discovered FastAPI/Flask/Click/Typer entrypoint | Keep reachable modules | Pass |

Eligible-deletion false positives in these adversarial cases: **0 / 24**.

Transactional gates also verify that `--apply` refuses to run without a reviewed
plan and rejects a repository whose current operation set has grown since the
plan was reviewed. Candidate, replacement, and call-site evidence hashes are
rechecked at the final mutation boundary.

Validation failure, timeout, and side-effect tests verify that commands run in
an isolated copy and leave the original tree unchanged. State-root and nested
rollback symlink tests verify that `.repo-gardener` cannot escape the repository.
Malformed configuration types are rejected before analysis rather than coerced
into safety overrides.

This is a release-gate fixture count, not an estimate of precision on all
Python repositories. Reproduce it with:

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

The same CI also runs the suite on Windows and Ubuntu with Python 3.11 through
3.14, and validates the bundled skill with a pinned revision of the official
Agent Skills reference validator.
