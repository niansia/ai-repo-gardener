from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "skills" / "repo-gardener" / "scripts"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from repo_gardener import __version__
from repo_gardener.analysis import Analyzer

MANIFEST = Path(__file__).with_name("labeled-corpus") / "manifest.json"


@dataclass(frozen=True)
class CaseResult:
    id: str
    label: str
    target: str
    predicted_safe_delete: bool
    finding_present: bool
    recommendation: str | None
    confidence: float | None
    risk: float | None
    outcome: str


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the labeled safety corpus.")
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    payload = json.loads(arguments.manifest.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("cases"), list):
        raise SystemExit("unsupported labeled corpus manifest")
    executable = shutil.which("git")
    if executable is None:
        raise SystemExit("git is required for the labeled corpus")
    with tempfile.TemporaryDirectory(prefix="repo-gardener-labeled-") as raw:
        workspace = Path(raw)
        results = [
            _run_case(executable, workspace / str(case["id"]), case)
            for case in payload["cases"]
        ]
    summary = _summary(results)
    try:
        manifest_name = arguments.manifest.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        manifest_name = str(arguments.manifest.resolve())
    document = {
        "schema_version": 1,
        "repo_gardener_version": __version__,
        "manifest": manifest_name,
        "summary": summary,
        "cases": [asdict(result) for result in results],
    }
    rendered = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if arguments.output:
        arguments.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return (
        1
        if summary["false_positives"]
        or summary["false_negatives"]
        or summary["contract_failures"]
        else 0
    )


def _run_case(executable: str, root: Path, case: dict[str, object]) -> CaseResult:
    root.mkdir(parents=True)
    _write_files(root, _file_map(case, "before"))
    _git(executable, root, "init")
    _git(executable, root, "config", "user.email", "corpus@example.com")
    _git(executable, root, "config", "user.name", "Repo Gardener Corpus")
    _git(executable, root, "add", ".")
    _git(
        executable,
        root,
        "commit",
        "-m",
        "before iteration",
        date="2026-01-01T00:00:00Z",
    )
    _write_files(root, _file_map(case, "after"))
    _git(executable, root, "add", "-A")
    _git(
        executable, root, "commit", "-m", "agent iteration", date="2026-01-02T00:00:00Z"
    )
    report = Analyzer(root).report("diff", "HEAD~1")
    target = str(case["target"])
    finding = next((item for item in report.findings if item.path == target), None)
    predicted = bool(
        finding
        and finding.rule == "stale-file"
        and finding.recommendation == "safe_delete_candidate"
    )
    label = str(case["label"])
    expected_finding = bool(case.get("expected_finding", False))
    if label == "DELETE":
        outcome = "TP" if predicted else "FN"
    else:
        outcome = "FP" if predicted else "TN"
    if bool(finding) != expected_finding:
        outcome += "+CONTRACT_FAILURE"
    return CaseResult(
        id=str(case["id"]),
        label=label,
        target=target,
        predicted_safe_delete=predicted,
        finding_present=finding is not None,
        recommendation=finding.recommendation if finding else None,
        confidence=finding.confidence if finding else None,
        risk=finding.risk if finding else None,
        outcome=outcome,
    )


def _file_map(case: dict[str, object], key: str) -> dict[str, str | None]:
    value = case.get(key)
    if not isinstance(value, dict) or not all(
        isinstance(path, str) and (isinstance(source, str) or source is None)
        for path, source in value.items()
    ):
        raise ValueError(f"case {case.get('id')!r} has invalid {key} files")
    return value


def _write_files(root: Path, files: dict[str, str | None]) -> None:
    for relative, source in files.items():
        path = root / relative
        if source is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


def _git(executable: str, root: Path, *arguments: str, date: str | None = None) -> None:
    environment = os.environ.copy()
    if date:
        environment["GIT_AUTHOR_DATE"] = date
        environment["GIT_COMMITTER_DATE"] = date
    subprocess.run(
        [executable, "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        env=environment,
    )


def _summary(results: list[CaseResult]) -> dict[str, int | float | None]:
    true_positives = sum(result.outcome.startswith("TP") for result in results)
    false_positives = sum(result.outcome.startswith("FP") for result in results)
    false_negatives = sum(result.outcome.startswith("FN") for result in results)
    true_negatives = sum(result.outcome.startswith("TN") for result in results)
    predicted = true_positives + false_positives
    actual = true_positives + false_negatives
    return {
        "cases": len(results),
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "true_negatives": true_negatives,
        "precision": true_positives / predicted if predicted else None,
        "recall": true_positives / actual if actual else None,
        "contract_failures": sum(
            "CONTRACT_FAILURE" in result.outcome for result in results
        ),
    }


if __name__ == "__main__":
    raise SystemExit(main())
