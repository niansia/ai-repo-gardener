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

## Documentation and translations

`README.md` is the authoritative English landing page. The complete public
translations are `README.zh-TW.md`, `README.zh-CN.md`, and `README.ja.md`.
When a pull request changes installation commands, supported hosts, release
status, safety boundaries, benchmark numbers, or links, update all four files
in the same pull request. Keep commands, version numbers, paths, and measured
results identical across languages; translate the explanation, not the
contract.

The README hero is the source-controlled `docs/hero.svg`. Keep it accessible,
self-contained, and renderable by GitHub without external fonts, scripts, or
assets. Avoid replacing measured claims with unqualified marketing claims.

## Security-sensitive changes

Changes to deletion eligibility, runtime references, parsing, validation,
rollback, or release workflows require a regression fixture and code-owner
review. Do not place exploitable reproductions or private source in a public
issue; use the private process in [SECURITY.md](SECURITY.md).

The pinned real-world benchmark can be run locally with
`python benchmarks/run_real_world.py --output benchmark-result.json`. Treat its
timings as environment-specific and never describe review-only findings as
automatic deletion candidates.

Run `python benchmarks/run_labeled_corpus.py --output labeled-result.json` when
changing deletion confidence, risk, reachability, or runtime-reference logic.
Every corpus case has an explicit DELETE, KEEP, or REVIEW label; a safe-delete
false positive or false negative fails the runner.
