# Labeled safe-delete corpus

Alpha.11 includes a machine-readable curated corpus of twenty repository
states in [`labeled-corpus/manifest.json`](labeled-corpus/manifest.json). Each
case declares an exact target and one ground-truth action:

- `DELETE`: the target is fully superseded and should enter the automatic
  deletion review gate.
- `KEEP`: deployment, packaging, or runtime evidence proves that the target is
  live.
- `REVIEW`: the target may be superseded, but uncertainty or a changed public
  contract makes automatic deletion unsafe.

The alpha.11 result is:

| Metric | Result |
| --- | ---: |
| Cases | 20 |
| True positives | 10 |
| False positives | 0 |
| False negatives | 0 |
| True negatives | 10 |
| Safe-delete precision | 100% |
| Safe-delete recall | 100% |

The complete machine-readable alpha.11 output is committed as
[`labeled-alpha11.json`](labeled-alpha11.json).

Reproduce the result with:

```bash
python benchmarks/run_labeled_corpus.py --output labeled-result.json
```

The runner builds two-commit Git repositories at fixed timestamps, executes
`diff --base HEAD~1`, and treats only a `stale-file` with
`safe_delete_candidate` as a positive prediction. It also checks whether each
target is present or absent as declared by the case contract.

This is a published, deterministic regression corpus, not a statistical claim
about all Python repositories. Adding realistic independently labeled cases is
more valuable than inflating the corpus with filename-only variations.
