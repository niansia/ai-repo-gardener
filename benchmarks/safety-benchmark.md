# Adversarial safety benchmark

Run on 2026-08-30 with Python 3.12 on Windows. The full regression suite passed
49 tests; one real-symlink check was skipped because this Windows account could
not create symlinks. The implementation branch is also exercised by Linux CI.
This table isolates eight cases where a normal import graph can make live or
user-modified code look unused.

| Scenario | Required outcome | Result |
| --- | --- | --- |
| `import_module` imported directly or through an alias | Keep live module | Pass |
| Non-literal `importlib.import_module(name)` | Disable automatic deletion repo-wide | Pass |
| `[project.entry-points.*]` plugin | Keep registered module | Pass |
| Importable package submodule outside `src/` | Review only by default | Pass |
| Candidate modified in the current worktree | Review only | Pass |
| Partial replacement missing public symbols | Review only | Pass |
| Monkeypatch string module path | Keep referenced module | Pass |
| Framework-discovered FastAPI/Flask/Click/Typer entrypoint | Keep reachable modules | Pass |

Eligible-deletion false positives in these adversarial cases: **0 / 8**.

This is a release-gate fixture count, not an estimate of precision on all
Python repositories. Reproduce it with:

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

The same CI also runs the suite on Windows and Ubuntu with Python 3.11 through
3.14, and validates the bundled skill with a pinned revision of the official
Agent Skills reference validator.
