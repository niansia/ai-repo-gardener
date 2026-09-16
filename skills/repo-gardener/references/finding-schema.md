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

`orphan-file`, `orphan-helper`, and `dependency-leftover` always have
`recommendation: review_only`. `duplicate-implementation` has
`recommendation: consolidation_review`; its optional replacement identifies a
canonical implementation, not a deletion target. None are eligible for
automatic deletion. `flat-directory` and `style-drift` findings are likewise
non-mutating.

Structure reports include `metrics.structure_entropy`, with a 0–100 pressure
score, factor breakdown, per-directory measurements, and estimated proposal
delta. `migration_plan` evidence contains proposed moves, exact old/new module
rewrite mappings, target collisions, package-init semantics, relative-import
and resource-path files, string-reference evidence, risk, and
`apply_supported: false`.

When supplied, `metrics.style_baseline_commit` is the resolved commit used for
historical style peers. `metrics.style_baseline_mode` is `repository-peers` or
`pre-ai-git` and remains available even when style produces no findings.
Ratio-style evidence may include `support` and `baseline_supported_files`;
consumers should not discard these observation counts.
`metrics.experimental_analysis` records whether structure/style were added to
a `scan` or `diff` run.
`metrics.parse_errors` is the count of Python files that could not be parsed;
`metrics.parse_error_files` lists their repository-relative paths. Any nonzero
count disables automatic deletion and is also surfaced in pretty output.
`metrics.parse_cache_hits` counts files whose derived extraction metadata was
reused from the external cache. The source is still parsed into an AST on a hit.
Cache identity includes relative parsing context, source content, Python
version, and an automatically derived analysis ABI, allowing reuse across
equivalent ephemeral worktrees without preserving stale analyzer results.

`metrics.deployment_runtime_references` maps module spellings found in known
deployment/automation files to their repository-relative evidence files.
`metrics.deployment_reference_files` lists scanned deployment metadata, and
`metrics.deployment_reference_uncertainty` lists templated, oversized, or
unreadable runtime-command sources. Any nonempty uncertainty list disables
automatic deletion repository-wide.

Stale-file evidence may include
`public_surface_missing_from_replacement` or
`public_contract_changed_in_replacement`. The latter covers signatures,
class-member surfaces, re-exports, and public constant contracts. Either keeps
the result out of the automatic-deletion gate. Assignment-only data/config
modules expose the `data_or_config_module_requires_literal_review` risk for the
same reason.

Finding IDs are derived from the rule, normalized path, replacement, and evidence fingerprint. Do not assume IDs survive a schema-version change.

## Accepted-findings ledger

`accept` writes a JSON ledger of reviewed findings, and `--accepted <file>`
suppresses them on a later run:

```json
{
  "schema_version": 1,
  "tool": "repo-gardener",
  "entries": [
    {
      "id": "stale-file:0123456789ab",
      "rule": "stale-file",
      "path": "parser_v2.py",
      "note": "kept for the 0.2 migration window"
    }
  ]
}
```

`entries` is sorted by `id`, which is the finding ID above; `rule`, `path`, and
`note` are review context. Because the ID includes the evidence fingerprint, a
ledger suppresses a finding only while its evidence is unchanged, and it must be
generated with the same command, `--base`, and flags the later run uses.

A ledger is never loaded implicitly. When `--accepted` is supplied, the report
adds `metrics.accepted_findings` with `source`, `entries`, `suppressed`, and the
`unmatched` IDs that no current finding reproduces. Suppressed findings are
removed from `findings`, from the summary counts, and from `--fail-on`. A
malformed ledger, a repeated ID, or an unexpected `schema_version` is a
configuration error (exit code `2`), never an empty ledger.

## SARIF output

`--format sarif` emits SARIF 2.1.0 for GitHub code scanning and comparable
consumers. Paths stay repository-relative, results keep the report's
deterministic order, and `tool.driver.rules` lists only the rules present in the
run, so `ruleIndex` always indexes that array. `partialFingerprints` carries
`repoGardenerFindingId/v1`, the finding ID, so an alert is tracked rather than
reopened. `level` maps finding `severity` (`warning` to `warning`, anything else
to `note`); confidence, risk, recommendation, category, replacement, risks, and
evidence types stay in `properties`. Regions are emitted only where a finding
carries `definition_line` or `definition_lines` evidence. `runs[0].properties`
repeats the command, summary, Python file count, parse errors, experimental
flag, base, and accepted-ledger metrics.

`fix --dry-run --format json` emits reviewed-plan schema version `3`. It
contains `plan_id`, `base_ref`, `base_sha`, `head_sha`, `config_sha256`,
`accepted_sha256`, automatic-deletion blockers, and deletion operations with
candidate, replacement, and call-site evidence hashes. `accepted_sha256` is the
digest of the accepted-findings ledger in effect (the digest of an empty ledger
when none was supplied), so changing the ledger invalidates a plan that was
reviewed against a different one. Accepted findings are withheld from the
operation list; a ledger can only remove operations, never add them. Plans from
earlier schema versions are rejected rather than upgraded.
`fix --apply --plan <json>` re-analyzes the repository and requires the current
plan ID to match exactly before deletion. Candidate, replacement, and call-site
evidence hashes are checked again at the final mutation boundary. The plan is a
machine-readable preview, not permission to mutate the repository.
