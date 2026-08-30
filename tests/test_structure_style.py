from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from conftest import write_project
from repo_gardener.analysis import Analyzer
from repo_gardener.git_support import git_executable


def test_flat_directory_reports_import_affinity_clusters(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "repo-gardener.toml": "[analysis]\nflat_directory_threshold = 4",
            "app.py": "import auth\nimport search\n\nif __name__ == '__main__':\n    pass",
            "auth.py": "import token_store\n\ndef login():\n    return token_store.load()",
            "token_store.py": "def load():\n    return 'token'",
            "search.py": "import vector_store\n\ndef query():\n    return vector_store.lookup()",
            "vector_store.py": "def lookup():\n    return []",
        },
    )

    report = Analyzer(tmp_path).report("structure")

    finding = next(
        finding for finding in report.findings if finding.rule == "flat-directory"
    )
    clusters = next(
        item["value"]
        for item in finding.evidence
        if item["type"] == "probable_clusters"
    )
    assert len(clusters) >= 2
    assert finding.recommendation == "proposal_only"


def test_large_cohesive_package_gets_factual_directory_load_finding(
    tmp_path: Path,
) -> None:
    files = {
        "repo-gardener.toml": "[analysis]\nflat_directory_threshold = 4",
        "app.py": "import module_0\n\nif __name__ == '__main__':\n    module_0.work()",
    }
    for index in range(5):
        next_import = f"import module_{index + 1}\n" if index < 4 else ""
        files[f"module_{index}.py"] = f"{next_import}\ndef work():\n    return {index}"
    write_project(tmp_path, files)

    report = Analyzer(tmp_path).report("structure")

    finding = next(item for item in report.findings if item.rule == "flat-directory")
    assert finding.recommendation == "review_directory_load"
    assert finding.confidence < 0.65


def test_one_giant_component_is_not_presented_as_a_cluster_proposal(
    tmp_path: Path,
) -> None:
    files = {
        "repo-gardener.toml": "[analysis]\nflat_directory_threshold = 4",
        "app.py": "import module_0\n\nif __name__ == '__main__':\n    module_0.work()",
        "utils.py": "def normalize(value):\n    return value",
    }
    for index in range(5):
        imports = []
        if index < 4:
            imports.append(f"import module_{index + 1}")
        if index == 4:
            imports.append("import utils")
        files[f"module_{index}.py"] = (
            "\n".join(imports) + f"\n\ndef work():\n    return {index}"
        )
    write_project(tmp_path, files)

    report = Analyzer(tmp_path).report("structure")

    finding = next(item for item in report.findings if item.rule == "flat-directory")
    clusters = next(
        item["value"]
        for item in finding.evidence
        if item["type"] == "probable_clusters"
    )
    assert clusters == []
    assert finding.confidence < 0.65
    assert finding.recommendation == "review_directory_load"


def test_twenty_flat_independent_modules_are_reported_without_fake_clusters(
    tmp_path: Path,
) -> None:
    files = {
        "repo-gardener.toml": "[analysis]\nflat_directory_threshold = 12",
        **{
            f"feature_{index}.py": f"def feature_{index}():\n    return {index}"
            for index in range(20)
        },
    }
    write_project(tmp_path, files)

    finding = next(
        item
        for item in Analyzer(tmp_path).report("structure").findings
        if item.rule == "flat-directory"
    )

    clusters = next(
        item["value"]
        for item in finding.evidence
        if item["type"] == "probable_clusters"
    )
    assert clusters == []
    assert finding.recommendation == "review_directory_load"


def test_style_drift_is_relative_to_repository_baseline(tmp_path: Path) -> None:
    files = {
        "app.py": "import clean_0\n\nif __name__ == '__main__':\n    clean_0.work(1)",
        **{
            f"clean_{index}.py": f"def work(number):\n    return number + {index}"
            for index in range(5)
        },
        "generated_feature.py": '''
def work(number: int) -> dict[str, object]:
    """This function performs a very comprehensive operation.

    It accepts the input, performs the required processing, catches all
    possible failures, and returns a richly structured response payload.
    """
    # First, initialize the result data.
    # Then, check if the value is valid.
    # Finally, return the processed result.
    data = number
    result = None
    try:
        result = {"payload": {"value": data}}
        print(result)
    except Exception:
        result = {"payload": {"value": None}}
    return result
''',
    }
    write_project(tmp_path, files)

    report = Analyzer(tmp_path).report("style")

    finding = next(
        finding for finding in report.findings if finding.path == "generated_feature.py"
    )
    evidence_types = {item["type"] for item in finding.evidence}
    assert "docstring_lines_per_function" in evidence_types
    assert "broad_exceptions_per_function" in evidence_types
    assert "ai_authorship_proof" in evidence_types
    assert finding.recommendation == "agent_review"


def test_small_style_baseline_cannot_produce_high_confidence(tmp_path: Path) -> None:
    files = {
        **{
            f"human_{index}.py": f"def work(number):\n    return number + {index}"
            for index in range(5)
        },
        "ai.py": '''
def work(number):
    """Perform the complete operation.

    This documentation deliberately expands a tiny implementation into a
    large explanation with input, output, and failure details.
    """
    # First, initialize the result.
    # Then, process the value.
    # Finally, return the result.
    try:
        result = {"payload": {"value": number}}
        print(result)
        return result
    except Exception:
        return {"payload": {"value": None}}
''',
    }
    write_project(tmp_path, files)

    report = Analyzer(tmp_path).report("style")

    finding = next(item for item in report.findings if item.path == "ai.py")
    assert finding.confidence < 0.85
    baseline = next(
        item["value"] for item in finding.evidence if item["type"] == "baseline_files"
    )
    assert baseline < 8


def test_python_specific_style_features_are_extracted(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "legacy.py": """
from typing import Dict, List, Optional
import os.path

def collect(values: List[Optional[str]]) -> Dict[str, str]:
    result = {}
    for value in values:
        if value is not None:
            result[value] = os.path.join("root", value)
    return result
""",
            "modern.py": """
from dataclasses import dataclass
from pathlib import Path

@dataclass
class Entry:
    name: str

def collect(values: list[str] | None) -> list[Path]:
    return [Path(value) for value in (values or [])]
""",
        },
    )

    analyzer = Analyzer(tmp_path)
    analyzer.report("style")
    records = {record.path.name: record for record in analyzer.records}
    legacy = records["legacy.py"].style
    modern = records["modern.py"].style

    assert legacy.legacy_generic_annotations >= 2
    assert legacy.legacy_optional_unions >= 1
    assert legacy.os_path_uses == 1
    assert legacy.for_loops == 1
    assert modern.builtin_generic_annotations >= 2
    assert modern.pep604_unions >= 1
    assert modern.pathlib_uses == 1
    assert modern.comprehensions == 1
    assert modern.structured_models == 1


def test_pre_ai_git_baseline_detects_drift_in_an_all_ai_current_repo(
    tmp_path: Path,
) -> None:
    executable = git_executable()
    if executable is None:
        pytest.skip("git is unavailable")
    human = {
        f"module_{index}.py": f"def work(number):\n    return number + {index}"
        for index in range(8)
    }
    write_project(tmp_path, human)
    _git(executable, tmp_path, "init")
    _git(executable, tmp_path, "config", "user.email", "tests@example.com")
    _git(executable, tmp_path, "config", "user.name", "Repo Gardener Tests")
    _git(executable, tmp_path, "add", ".")
    _git(executable, tmp_path, "commit", "-m", "human baseline")
    ai_source = '''
def work(number):
    """Perform a comprehensive and robust processing operation.

    Args:
        number: The value to process.

    Returns:
        A nested response dictionary.

    Raises:
        Exception: If processing fails.
    """
    # First, initialize the result.
    # Then, process the supplied value.
    # Finally, return the result.
    try:
        result = {"payload": {"value": number}}
        print(result)
        return result
    except Exception:
        return {"payload": {"value": None}}
'''
    write_project(tmp_path, {name: ai_source for name in human})
    _git(executable, tmp_path, "add", ".")
    _git(executable, tmp_path, "commit", "-m", "agent rewrite")

    without_baseline = Analyzer(tmp_path).report("style")
    with_baseline = Analyzer(tmp_path).report("style", style_baseline="HEAD~1")

    assert not without_baseline.findings
    assert with_baseline.findings
    assert with_baseline.metrics["style_baseline_commit"]
    assert any(
        item["type"] == "baseline_mode" and item["value"] == "pre-ai-git"
        for item in with_baseline.findings[0].evidence
    )


def _git(executable: str, root: Path, *arguments: str) -> None:
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = "2026-01-01T00:00:00Z"
    env["GIT_COMMITTER_DATE"] = "2026-01-01T00:00:00Z"
    subprocess.run(
        [executable, "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        env=env,
    )
