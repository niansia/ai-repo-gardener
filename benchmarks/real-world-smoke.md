# Real-world smoke run

Run on 2026-08-30 with the v0.1 working tree on Windows. Timings are local
wall-clock measurements and are not a cross-machine performance guarantee.
A reviewer-reported Linux run measured the same 1,519-file pandas scan at
18.1 seconds, illustrating that these local timings should not be compared as
cross-platform performance guarantees.

A later alias-safety regression caused a reviewer-measured pandas scan to rise
to 44.7 seconds because assignment aliases were extracted by twelve full AST
walks per file. Caching the tree-dependent assignment pairs restored that same
reviewer's copy to 21.0 seconds with unchanged findings. These are reviewer
measurements, not a replacement for the pinned local table below.

Command: `repo-gardener scan <repo> --confidence all`

| Repository | Commit | Python files | Time | Stale findings |
| --- | --- | ---: | ---: | --- |
| requests | `5460f467b02e49471c0fd6cfc9ca0adab6351f98` | 37 | 0.31 s | 0 |
| Flask | `d318b683471101618febed18996405ad26462110` | 83 | 0.45 s | 0 |
| pandas | `6ba566ed44260c4a7cac8810be405bcaaee655c6` | 1,519 | 10.52 s | 1 low-confidence finding at 36.2%; hidden by the default threshold |

The audit that triggered this regression work reported 82.6 seconds on a
1,519-file pandas checkout before candidate indexing and lazy similarity/style
extraction. Because its exact commit was not recorded, that number is context,
not a direct before/after benchmark. A separate synthetic run with 1,521 small
Python files completed in 1.34 seconds.

Experimental `structure` runs on requests and Flask produced no cluster
proposal. Each returned only a 60% `review_directory_load` fact with an empty
cluster list, which is hidden by the default medium-confidence threshold.

## Alpha.7 expanded GC smoke

The roadmap rules were rerun on fresh shallow checkouts on 2026-08-31. These
timings include file-, symbol-, duplicate-, dependency-, and runtime-reference
analysis. They remain local wall-clock measurements.

| Repository | Commit | Python files | Time | Findings at `--confidence all` |
| --- | --- | ---: | ---: | --- |
| requests | `5460f467b02e49471c0fd6cfc9ca0adab6351f98` | 37 | 0.58 s | 0 |
| Flask | `d318b683471101618febed18996405ad26462110` | 83 | 0.83 s | 1 exact duplicate + 1 unreferenced private helper, both review-only |
| pandas | `4dfc8c7dfbe477ec3c50b6088e0ba34018f9fec4` | 1,519 | 23.20 s | 1 low-confidence stale-file review; hidden by default |

The same requests and Flask checkouts produced no architecture partition:
their entropy scores were 40.8 and 30.5, with factual
`review_directory_load` findings only. Runtime-reference analysis caches one
whole-tree AST walk per file; duplicate fingerprints are computed without AST
deep copies. A cold/loaded pandas run can still be slower, so 23.20 seconds is
not an upper-bound guarantee.

This is a regression smoke test, not a labeled precision benchmark. It does not
justify a precision percentage.
