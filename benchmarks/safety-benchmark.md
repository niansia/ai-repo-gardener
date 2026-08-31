# Adversarial safety benchmark

Run on 2026-08-31 with Python 3.12. The alpha.10 source suite contains 167
tests. The local Windows gate passes 162 and skips five real-symlink tests when
the account cannot create symlinks. Linux and macOS alpha.10 status must come
from the hosted exact-wheel matrix after push. This table isolates forty-six
adversarial variants where a
normal import graph can make live or user-modified code look unused.

| Scenario | Required outcome | Result |
| --- | --- | --- |
| `import_module` imported directly, imported with an alias, or assigned through two aliases | Keep live module | Pass |
| Non-literal `importlib.import_module(name)` | Disable automatic deletion repo-wide | Pass |
| Repository Python parse error | Keep findings but disable automatic deletion repo-wide | Pass |
| UTF-8 BOM on a Python importer | Preserve import migration evidence and the correct stale-file conclusion | Pass |
| `eval` or `exec` runtime execution | Disable automatic deletion repo-wide | Pass |
| `getattr(importlib, "import_module")` reflection | Disable automatic deletion repo-wide | Pass |
| `builtins.__import__`, including assigned/imported aliases | Disable automatic deletion repo-wide | Pass |
| `pkgutil.iter_modules` or `walk_packages` discovery | Disable automatic deletion repo-wide | Pass |
| Module-owner aliases such as `il = importlib` and `b = builtins` | Disable automatic deletion when the eventual loader target is non-literal | Pass |
| Known loader callable stored in a dict/list or passed as a value | Disable automatic deletion repo-wide | Pass |
| Known loader callable invoked inline from a container or stored on a class | Disable automatic deletion repo-wide | Pass |
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
| Framework constructors/decorators imported through module, symbol, or assignment aliases | Keep reachable modules | Pass |
| FastAPI/Flask/Typer/Click aliases inside module-level `try` or conditional control flow | Keep reachable modules | Pass |
| Flask/FastAPI constructors imported locally inside app factories | Keep reachable modules | Pass |
| `sitecustomize.py`, `usercustomize.py`, `noxfile.py`, `fabfile.py`, `locustfile.py`, and `docs/conf.py` | Treat implicit runtime/tool modules as roots | Pass |
| Ambiguous import spelling shared by a root module and configured source root | Keep every possible local target reachable | Pass |
| Reflective `__dict__`, `vars`, or `__getattribute__` loader lookup | Disable automatic deletion repo-wide | Pass |
| PyPA import-name metadata, dynamic public metadata, and custom setuptools source roots | Protect public modules or disable automatic deletion when unresolved | Pass |
| Replacement changes a public constant, function signature, or class member | Review only; never auto-delete | Pass |
| Assignment-only data/configuration replacement changes literal values | Review only; never auto-delete | Pass |
| Python symlink resolves outside the repository during read-only discovery | Ignore it; never read external source | Pass |

Eligible-deletion false positives in these adversarial variants: **0 / 46**.

Transactional gates also verify that `--apply` refuses to run without a reviewed
plan and rejects a repository whose current operation set has grown since the
plan was reviewed. Candidate, replacement, and call-site evidence hashes are
rechecked at the final mutation boundary.

Validation failure, timeout, and side-effect tests verify that commands run in
an isolated copy and leave the original tree unchanged. State-root and nested
rollback symlink tests verify that `.repo-gardener` cannot escape the repository.
Linked-worktree tests verify that isolated validation retains functional Git
metadata and preserves the staged/unstaged distinction. Failed validation
reports a bounded diagnostic with exit code and stderr/stdout.
Repository-symlink tests verify that relative validation effects
cannot escape the disposable workspace. Snapshot corruption is rejected before
any working-tree file is restored, and malformed rollback state returns a
controlled CLI error rather than a traceback.

Malformed configuration types are rejected before analysis rather than coerced
into safety overrides.

This is a release-gate fixture count, not an estimate of precision on all
Python repositories. Reproduce it with:

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

The hosted CI matrix is configured to run the suite on Windows, Ubuntu, and
macOS with Python 3.11 through 3.14, and validates the bundled skill with a
pinned revision of the official Agent Skills reference validator. The
release-candidate workflow builds one wheel and tests that exact artifact
across the same 12-job matrix on pull requests, `main`, and manual dispatch.
Tag runs additionally emit a provenance attestation and publish that same
wheel.
Hosted status must come from the workflow rather than being inferred here.
