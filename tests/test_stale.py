from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from conftest import write_project
from repo_gardener.analysis import Analyzer
from repo_gardener.analysis import stale as stale_module
from repo_gardener.fixes import safe_candidates
from repo_gardener.git_support import (
    changed_paths,
    file_added_later,
    file_birth,
    git_executable,
)


def test_stale_file_uses_git_and_call_site_migration(tmp_path: Path) -> None:
    executable = git_executable()
    if executable is None:
        pytest.skip("git is unavailable")
    write_project(
        tmp_path,
        {
            "app.py": "import parser_v2\n\nif __name__ == '__main__':\n    print(parser_v2.parse('x'))",
            "parser_v2.py": "def parse(value: str) -> str:\n    return value.strip().lower()",
        },
    )
    _git(executable, tmp_path, "init")
    _git(executable, tmp_path, "config", "user.email", "tests@example.com")
    _git(executable, tmp_path, "config", "user.name", "Repo Gardener Tests")
    _git(executable, tmp_path, "add", ".")
    _git(executable, tmp_path, "commit", "-m", "old parser")
    write_project(
        tmp_path,
        {
            "app.py": "import parser\n\nif __name__ == '__main__':\n    print(parser.parse('x'))",
            "parser.py": "def parse(value: str) -> str:\n    return value.strip().lower()",
        },
    )
    _git(executable, tmp_path, "add", ".")
    _git(executable, tmp_path, "commit", "-m", "replace parser")

    report = Analyzer(tmp_path).report("diff", "HEAD~1")

    findings = [finding for finding in report.findings if finding.rule == "stale-file"]
    assert len(findings) == 1
    finding = findings[0]
    assert finding.path == "parser_v2.py"
    assert finding.replacement == "parser.py"
    assert finding.confidence >= 0.85
    assert finding.recommendation == "safe_delete_candidate"
    assert any(item["type"] == "call_site_migration" for item in finding.evidence)


def test_literal_dynamic_import_is_treated_as_live(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "app.py": "import importlib\n\nif __name__ == '__main__':\n    importlib.import_module('parser_old')",
            "parser_old.py": "def parse(value):\n    return value.strip()",
            "parser.py": "def parse(value):\n    return value.strip()",
        },
    )

    report = Analyzer(tmp_path).report("stale")

    assert not any(finding.path == "parser_old.py" for finding in report.findings)


def test_src_prefix_import_resolves_to_installed_module(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "main.py": (
                "from src.service import handle\n\n"
                "if __name__ == '__main__':\n"
                "    handle()"
            ),
            "src/service.py": "from src.parser import parse\n\ndef handle():\n    return parse('x')",
            "src/parser.py": "def parse(value):\n    return value.strip()",
        },
    )

    analyzer = Analyzer(tmp_path)

    assert analyzer.graph.edges["main"] == {"service"}
    assert {"main", "service", "parser"} <= analyzer.graph.reachable


def test_diff_merges_committed_worktree_and_untracked_paths(tmp_path: Path) -> None:
    executable = git_executable()
    if executable is None:
        pytest.skip("git is unavailable")
    write_project(tmp_path, {"app.py": "VALUE = 1"})
    _git(executable, tmp_path, "init")
    _git(executable, tmp_path, "config", "user.email", "tests@example.com")
    _git(executable, tmp_path, "config", "user.name", "Repo Gardener Tests")
    _git(executable, tmp_path, "add", ".")
    _git(executable, tmp_path, "commit", "-m", "baseline")
    write_project(
        tmp_path,
        {
            "app.py": "VALUE = 2",
            "parser.py": "def parse(value):\n    return value.strip()",
        },
    )

    paths = changed_paths(tmp_path, "HEAD")
    report = Analyzer(tmp_path).report("diff", "HEAD")

    assert paths == {"app.py", "parser.py"}
    assert report.metrics["changed_files"] == 2


def test_changed_unreachable_file_without_replacement_is_review_only(
    tmp_path: Path,
) -> None:
    executable = git_executable()
    if executable is None:
        pytest.skip("git is unavailable")
    write_project(
        tmp_path,
        {"app.py": "if __name__ == '__main__':\n    print('ok')"},
    )
    _git(executable, tmp_path, "init")
    _git(executable, tmp_path, "config", "user.email", "tests@example.com")
    _git(executable, tmp_path, "config", "user.name", "Repo Gardener Tests")
    _git(executable, tmp_path, "add", ".")
    _git(executable, tmp_path, "commit", "-m", "baseline")
    write_project(
        tmp_path,
        {"exporter.py": "def export(rows):\n    return list(rows)"},
    )

    report = Analyzer(tmp_path).report("diff", "HEAD")

    finding = next(item for item in report.findings if item.path == "exporter.py")
    assert finding.rule == "orphan-file"
    assert finding.recommendation == "review_only"
    assert not safe_candidates(report.findings)


def test_replacement_prefilter_skips_unrelated_large_candidate_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = {
        "app.py": "import live\n\nif __name__ == '__main__':\n    live.start()",
        "live.py": "def start():\n    return True",
    }
    files.update(
        {
            f"unused_{index}.py": f"def unique_{index}():\n    return {index}"
            for index in range(300)
        }
    )
    write_project(tmp_path, files)
    calls = 0
    original = stale_module.compare

    def counted_compare(left, right):
        nonlocal calls
        calls += 1
        return original(left, right)

    monkeypatch.setattr(stale_module, "compare", counted_compare)

    Analyzer(tmp_path).report("stale")

    assert calls < 20


def test_git_birth_does_not_follow_old_path_and_orders_same_second_commits(
    tmp_path: Path,
) -> None:
    executable = git_executable()
    if executable is None:
        pytest.skip("git is unavailable")
    write_project(tmp_path, {"parser_v2.py": "def parse():\n    return 1"})
    _git(executable, tmp_path, "init")
    _git(executable, tmp_path, "config", "user.email", "tests@example.com")
    _git(executable, tmp_path, "config", "user.name", "Repo Gardener Tests")
    _git(executable, tmp_path, "add", ".")
    _git(executable, tmp_path, "commit", "-m", "old path")
    write_project(tmp_path, {"parser.py": "def parse():\n    return 1"})
    _git(executable, tmp_path, "add", ".")
    _git(executable, tmp_path, "commit", "-m", "new path")

    old_birth = file_birth(tmp_path, "parser_v2.py")
    new_birth = file_birth(tmp_path, "parser.py")

    assert old_birth is not None
    assert new_birth is not None
    assert old_birth.timestamp == new_birth.timestamp
    assert old_birth.commit != new_birth.commit
    assert file_added_later(tmp_path, old_birth, new_birth)


def test_git_birth_of_renamed_destination_is_the_rename_commit(tmp_path: Path) -> None:
    executable = git_executable()
    if executable is None:
        pytest.skip("git is unavailable")
    write_project(tmp_path, {"parser_v2.py": "def parse():\n    return 1"})
    _git(executable, tmp_path, "init")
    _git(executable, tmp_path, "config", "user.email", "tests@example.com")
    _git(executable, tmp_path, "config", "user.name", "Repo Gardener Tests")
    _git(executable, tmp_path, "add", ".")
    _git_at(
        executable,
        tmp_path,
        "2026-01-01T00:00:00Z",
        "commit",
        "-m",
        "old name",
    )
    (tmp_path / "parser_v2.py").rename(tmp_path / "parser.py")
    _git(executable, tmp_path, "add", "-A")
    _git_at(
        executable,
        tmp_path,
        "2026-01-02T00:00:00Z",
        "commit",
        "-m",
        "rename parser",
    )

    birth = file_birth(tmp_path, "parser.py")

    assert birth is not None
    assert birth.timestamp == 1767312000


def test_src_layout_module_is_not_an_automatic_delete(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "src/example/__init__.py": "from .parser import parse",
            "src/example/parser.py": "def parse(value):\n    return value.strip()",
            "src/example/parser_old.py": "def parse(value):\n    return value.strip()",
        },
    )

    report = Analyzer(tmp_path).report("stale")

    finding = next(
        finding for finding in report.findings if finding.path.endswith("parser_old.py")
    )
    assert finding.risk >= 0.25
    assert finding.recommendation == "review"


def test_src_layout_delete_risk_can_be_explicitly_overridden(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "repo-gardener.toml": "[safety]\nallow_delete_src = true",
            "src/example/__init__.py": "from .parser import parse",
            "src/example/parser.py": "def parse(value):\n    return value.strip()",
            "src/example/parser_old.py": "def parse(value):\n    return value.strip()",
        },
    )

    report = Analyzer(tmp_path).report("stale")

    finding = next(
        finding for finding in report.findings if finding.path.endswith("parser_old.py")
    )
    assert finding.risk == 0.0


def test_config_entrypoint_accepts_object_suffix(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "repo-gardener.toml": '[entrypoints]\nmodules = ["package.web:app"]',
            "src/package/__init__.py": "",
            "src/package/web.py": "from . import worker\napp = object()",
            "src/package/worker.py": "def run():\n    return True",
        },
    )

    analyzer = Analyzer(tmp_path)

    assert "package.web" in analyzer.graph.roots
    assert "package.worker" in analyzer.graph.reachable


@pytest.mark.parametrize(
    ("app_source", "framework"),
    [
        (
            "from fastapi import FastAPI\nimport worker\napp = FastAPI()",
            "fastapi",
        ),
        (
            "from flask import Flask\nimport worker\napp = Flask(__name__)",
            "flask",
        ),
        (
            "import typer\nimport worker\napp = typer.Typer()",
            "typer",
        ),
        (
            "import click\nimport worker\n@click.command()\ndef cli():\n    pass",
            "click",
        ),
    ],
)
def test_framework_entrypoints_keep_imported_modules_live(
    tmp_path: Path, app_source: str, framework: str
) -> None:
    write_project(
        tmp_path,
        {
            "web.py": app_source,
            "worker.py": "def run():\n    return True",
            "worker_old.py": "def run():\n    return True",
        },
    )

    analyzer = Analyzer(tmp_path)

    assert "web" in analyzer.graph.roots
    assert "worker" in analyzer.graph.reachable
    assert analyzer.records[0].framework_entrypoints or any(
        framework in record.framework_entrypoints for record in analyzer.records
    )


def _git(executable: str, root: Path, *arguments: str) -> None:
    _git_at(executable, root, "2026-01-01T00:00:00Z", *arguments)


def _git_at(executable: str, root: Path, date: str, *arguments: str) -> None:
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = date
    env["GIT_COMMITTER_DATE"] = date
    subprocess.run(
        [executable, "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        env=env,
    )
