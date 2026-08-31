# Pinned real-world benchmark

Alpha.10 was measured by the hosted
[`Pinned real-world benchmark`](https://github.com/niansia/ai-repo-gardener/actions/runs/33360806211)
workflow on 2026-08-31. The runner used Ubuntu/Azure
(`Linux 6.17.0-1022-azure`, x86-64), Python 3.12.14, and Repo Gardener
0.1.0a10. Wall-clock values are useful for regression tracking in that
environment, not as cross-machine guarantees.

The benchmark uses the exact commits in
[`real-world-repos.json`](real-world-repos.json). A complete depth-two shallow
checkout is materialized before timing so Git network/blob loading is not
charged to analysis. `scan-cold` starts with an empty Repo Gardener cache;
later modes reuse the derived extraction metadata. Every mode uses
`--confidence all`.

## Repository GC scan

| Repository | Commit | Python files | Cold | Warm | Findings by rule | Safe-delete |
| --- | --- | ---: | ---: | ---: | --- | ---: |
| Django | `73cc09f` | 2,929 | 36.15 s | 27.65 s | 72 stale-file low; 6 duplicate; 1 orphan-helper; 1 dependency | 0 |
| FastAPI | `4903347` | 1,138 | 5.03 s | 2.89 s | 25 duplicate implementations in tutorial variants | 0 |
| Flask | `d318b68` | 83 | 0.93 s | 0.49 s | 1 duplicate; 1 orphan-helper | 0 |
| pandas | `4dfc8c7` | 1,519 | 31.25 s | 20.91 s | 1 stale-file low | 0 |
| Pydantic | `f512b08` | 439 | 8.17 s | 4.94 s | 3 duplicate implementations across compatibility/internal code | 0 |
| pytest | `5d85267` | 272 | 19.69 s | 18.25 s | 0 | 0 |
| requests | `5460f46` | 37 | 0.47 s | 0.31 s | 0 | 0 |

All scan findings above are review-only. FastAPI's HIGH findings are exact
normalized-AST matches among documentation tutorial variants; Pydantic's are
between v1 compatibility and internal implementations. They are
`consolidation_review` with elevated risk, never deletion operations. Flask's
two review findings were manually checked in the earlier smoke audit: one exact
duplicate and one unreferenced private helper.

Django contains one deliberately invalid syntax fixture,
`tests/test_runner_apps/tagged/tests_syntax_error.py`. Repo Gardener reports the
parse error and disables automatic deletion for that repository as designed.

## Iteration diff

| Repository | Time | Findings at pinned `HEAD~1` | Safe-delete |
| --- | ---: | --- | ---: |
| Django | 28.30 s | 0 | 0 |
| FastAPI | 2.96 s | 0 | 0 |
| Flask | 0.48 s | 0 | 0 |
| pandas | 21.51 s | 0 | 0 |
| Pydantic | 4.99 s | 0 | 0 |
| pytest | 18.22 s | 1 medium orphan-file review | 0 |
| requests | 0.35 s | 0 | 0 |

## Experimental analyzers

| Repository | Structure | Structure findings | Style | Style findings |
| --- | ---: | ---: | ---: | ---: |
| Django | 12.44 s | 5 | 51.71 s | 70 |
| FastAPI | 2.00 s | 5 | 16.67 s | 61 |
| Flask | 0.30 s | 1 | 1.37 s | 3 |
| pandas | 14.07 s | 4 | 65.55 s | 49 |
| Pydantic | 2.40 s | 3 | 15.03 s | 13 |
| pytest | 1.45 s | 1 | 7.90 s | 8 |
| requests | 0.21 s | 1 | 0.88 s | 2 |

Structure and style are proposal/review systems. Their counts are not deletion
candidates or claims that code was AI-authored. Style uses repository peers in
this run; an AI-heavy repository should supply a pre-AI baseline.

Warm runs reused extraction metadata for every parseable file (2,928 of 2,929
for Django and all files elsewhere). A cache hit still performs Python AST
parsing for safety, so it is not expected to reduce runtime to zero.

The complete machine-readable artifact is committed as
[`real-world-alpha10.json`](real-world-alpha10.json). Reproduce or extend it
with:

```bash
python benchmarks/run_real_world.py --output benchmark-result.json
```

This is a longitudinal smoke benchmark, not a labeled corpus and not a
population precision estimate. The separate destructive-safety fixture gate is
documented in [`safety-benchmark.md`](safety-benchmark.md).
