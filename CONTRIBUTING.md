# Contributing

Repo Gardener optimizes for explainability and false-positive control. A new rule is useful only when users can understand why it fired and when it must stay out of the way.

## Before opening a pull request

1. Add or update a synthetic repository test that demonstrates the behavior.
2. Include a false-positive case for dynamic imports, public APIs, framework discovery, or other relevant risks.
3. Keep analysis deterministic and local. An optional model may review ambiguity later, but it cannot be the source of deletion confidence.
4. Keep architecture and style actions proposal-only.
5. Run:

   ```bash
   ruff check .
   ruff format --check .
   pytest
   python skills/repo-gardener/scripts/run_repo_gardener.py scan . --confidence all
   ```

Finding schema changes require an explicit `schema_version` decision and updated tests.
