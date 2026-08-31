# Regression fixtures

These repositories exercise the precision boundaries promised by Repo Gardener.
Tests that need Git history copy the corresponding file shape into a temporary
repository and create deterministic commits there.

- `stale_v2`: a call site migrated from a suffixed implementation.
- `false_positive_plugin`: plugin modules must remain review-only.
- `partial_replacement`: a replacement does not cover every old symbol.
- `rename_not_stale`: a true rename is not a leftover pair.
- `monkeypatch_string_path`: string-based references keep modules live.
- `agent_diff_orphan`: a newly-created, unreachable file has no replacement.
- `src_prefix_import`: literal `src.*` imports resolve alongside installed names.
- `style_human_baseline`: a human baseline and one deliberately drifting file.
- `flat_four_domains`: four disconnected responsibility groups for experimental analysis.
- `extended_gc_rules`: review-only orphan-helper, duplicate implementation, and dependency-leftover evidence.
