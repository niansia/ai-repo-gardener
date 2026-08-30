# Finding JSON contract

The top-level document contains:

- `schema_version`: currently `1`.
- `command`, `root`, and optional `base`.
- `summary`: deterministic counts by confidence and rule.
- `metrics`: repository-level measurements.
- `findings`: stable-sorted finding objects.

Each finding contains `id`, `rule`, `category`, `severity`, `confidence`, `risk`, `path`, optional `replacement`, `evidence`, `risks`, and `recommendation`.

`confidence` and `risk` are numbers from 0 to 1. Consumers may suggest automatic deletion only when all of these are true:

- `rule` is `stale-file`.
- `confidence >= 0.85`.
- `risk <= 0.20`.
- `recommendation` is `safe_delete_candidate`.
- `replacement` is present.

`orphan-file` always has `recommendation: review_only` and is never eligible
for automatic deletion. Experimental `flat-directory` and `style-drift`
findings are likewise non-mutating.

When supplied, `metrics.style_baseline_commit` is the resolved commit used for
historical style peers. `metrics.style_baseline_mode` is `repository-peers` or
`pre-ai-git` and remains available even when style produces no findings.
`metrics.experimental_analysis` records whether structure/style were added to
a `scan` or `diff` run.

Finding IDs are derived from the rule, normalized path, replacement, and evidence fingerprint. Do not assume IDs survive a schema-version change.

`fix --dry-run --format json` emits reviewed-plan schema version `2`. It
contains `plan_id`, `base_ref`, `base_sha`, `head_sha`, `config_sha256`,
automatic-deletion blockers, and deletion operations with candidate,
replacement, and call-site evidence hashes.
`fix --apply --plan <json>` re-analyzes the repository and requires the current
plan ID to match exactly before deletion. Candidate, replacement, and call-site
evidence hashes are checked again at the final mutation boundary. The plan is a
machine-readable preview, not permission to mutate the repository.
