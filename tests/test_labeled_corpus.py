from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_labeled_corpus_has_no_safe_delete_classification_errors(
    tmp_path: Path,
) -> None:
    output = tmp_path / "result.json"
    subprocess.run(
        [
            sys.executable,
            "benchmarks/run_labeled_corpus.py",
            "--output",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(output.read_text(encoding="utf-8"))
    summary = result["summary"]

    assert summary["cases"] >= 20
    assert summary["true_positives"] >= 10
    assert summary["false_positives"] == 0
    assert summary["false_negatives"] == 0
    assert summary["contract_failures"] == 0
    assert summary["precision"] == 1.0
    assert summary["recall"] == 1.0
