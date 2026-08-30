from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import write_project
from repo_gardener import fixes as fixes_module
from repo_gardener.analysis import Analyzer
from repo_gardener.cli import main
from repo_gardener.fixes import FixError, apply_deletions
from repo_gardener.git_support import git_executable
from repo_gardener.models import Finding


def test_validation_failure_restores_deleted_file(tmp_path: Path) -> None:
    source = "def parse(value):\n    return value.strip()\n"
    write_project(tmp_path, {"parser_old.py": source})
    finding = Finding(
        rule="stale-file",
        category="repo-gc",
        severity="warning",
        confidence=0.95,
        risk=0.0,
        path="parser_old.py",
        replacement="parser.py",
        recommendation="safe_delete_candidate",
    ).finalize()
    command = f'"{sys.executable}" -c "import sys; sys.exit(1)"'

    with pytest.raises(FixError, match="restored"):
        apply_deletions(tmp_path, [finding], [command])

    assert (tmp_path / "parser_old.py").read_text(encoding="utf-8") == source


def test_successful_apply_can_be_restored(tmp_path: Path) -> None:
    source = "def parse(value):\n    return value.strip()\n"
    write_project(tmp_path, {"parser_old.py": source})
    finding = Finding(
        rule="stale-file",
        category="repo-gc",
        severity="warning",
        confidence=0.95,
        risk=0.0,
        path="parser_old.py",
        replacement="parser.py",
        recommendation="safe_delete_candidate",
    ).finalize()
    command = f'"{sys.executable}" -c "import sys; sys.exit(0)"'

    manifest = apply_deletions(tmp_path, [finding], [command])
    assert manifest["status"] == "applied"
    assert not (tmp_path / "parser_old.py").exists()

    from repo_gardener.fixes import restore_last

    restored = restore_last(tmp_path)
    assert restored["status"] == "restored_by_user"
    assert (tmp_path / "parser_old.py").read_text(encoding="utf-8") == source


def test_keyboard_interrupt_during_validation_restores_deleted_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = "def parse(value):\n    return value.strip()\n"
    write_project(tmp_path, {"parser_old.py": source})
    finding = Finding(
        rule="stale-file",
        category="repo-gc",
        severity="warning",
        confidence=0.95,
        risk=0.0,
        path="parser_old.py",
        replacement="parser.py",
        recommendation="safe_delete_candidate",
    ).finalize()

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(fixes_module.subprocess, "run", interrupt)

    with pytest.raises(KeyboardInterrupt):
        apply_deletions(tmp_path, [finding], ["test command"])

    assert (tmp_path / "parser_old.py").read_text(encoding="utf-8") == source


def test_apply_rejects_a_stale_content_hash(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "parser_old.py": "def parse():\n    return 'changed'",
            "parser.py": "def parse():\n    return 'new'",
        },
    )
    finding = Finding(
        rule="stale-file",
        category="repo-gc",
        severity="warning",
        confidence=0.95,
        risk=0.0,
        path="parser_old.py",
        replacement="parser.py",
        evidence=[{"type": "candidate_sha256", "value": "0" * 64}],
        recommendation="safe_delete_candidate",
    ).finalize()

    with pytest.raises(FixError, match="stale plan"):
        apply_deletions(tmp_path, [finding], ["ignored"])

    assert (tmp_path / "parser_old.py").is_file()


def test_apply_rejects_path_escape(tmp_path: Path) -> None:
    finding = Finding(
        rule="stale-file",
        category="repo-gc",
        severity="warning",
        confidence=0.95,
        risk=0.0,
        path="../outside.py",
        replacement="parser.py",
        recommendation="safe_delete_candidate",
    ).finalize()

    with pytest.raises(FixError, match="escapes repository root"):
        apply_deletions(tmp_path, [finding], ["ignored"])


def test_apply_rejects_symlink_candidate(tmp_path: Path) -> None:
    target = tmp_path / "target.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    link = tmp_path / "parser_old.py"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    finding = Finding(
        rule="stale-file",
        category="repo-gc",
        severity="warning",
        confidence=0.95,
        risk=0.0,
        path="parser_old.py",
        replacement="parser.py",
        recommendation="safe_delete_candidate",
    ).finalize()

    with pytest.raises(FixError, match="symlink"):
        apply_deletions(tmp_path, [finding], ["ignored"])

    assert target.read_text(encoding="utf-8") == "VALUE = 1\n"


def test_cli_emits_versioned_json(tmp_path: Path, capsys) -> None:
    write_project(tmp_path, {"app.py": "if __name__ == '__main__':\n    print('ok')"})

    exit_code = main(["scan", str(tmp_path), "--format", "json", "--confidence", "all"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1
    assert payload["metrics"]["python_files"] == 1


def test_diff_fails_loudly_for_non_git_and_invalid_base(tmp_path: Path, capsys) -> None:
    write_project(tmp_path, {"app.py": "VALUE = 1"})
    assert main(["diff", str(tmp_path)]) == 2
    assert "requires a Git repository" in capsys.readouterr().err

    executable = git_executable()
    if executable is None:
        return
    _git(executable, tmp_path, "init")
    _git(executable, tmp_path, "config", "user.email", "tests@example.com")
    _git(executable, tmp_path, "config", "user.name", "Repo Gardener Tests")
    _git(executable, tmp_path, "add", ".")
    _git(executable, tmp_path, "commit", "-m", "baseline")
    assert main(["diff", str(tmp_path), "--base", "DOES_NOT_EXIST"]) == 2
    assert "cannot be resolved" in capsys.readouterr().err


def test_fail_on_returns_ci_exit_codes(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "app.py": "import parser\n\nif __name__ == '__main__':\n    pass",
            "parser.py": "def parse(value):\n    return value.strip()",
            "parser_old.py": "def parse(value):\n    return value.strip()",
        },
    )

    assert main(["scan", str(tmp_path), "--fail-on", "medium"]) == 1
    assert main(["scan", str(tmp_path), "--fail-on", "high"]) == 0


def test_scan_requires_explicit_opt_in_for_experimental_rules(tmp_path: Path) -> None:
    files = {
        "repo-gardener.toml": "[analysis]\nflat_directory_threshold = 4",
        "app.py": "import auth\nimport search\n\nif __name__ == '__main__':\n    pass",
        "auth.py": "import token_store\n\ndef login():\n    return token_store.load()",
        "token_store.py": "def load():\n    return 'token'",
        "search.py": "import vector_store\n\ndef query():\n    return vector_store.lookup()",
        "vector_store.py": "def lookup():\n    return []",
    }
    write_project(tmp_path, files)

    stable = Analyzer(tmp_path).report("scan")
    experimental = Analyzer(tmp_path).report("scan", experimental=True)

    assert not any(item.rule == "flat-directory" for item in stable.findings)
    assert any(item.rule == "flat-directory" for item in experimental.findings)


def test_finding_ids_and_order_are_deterministic(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "app.py": "import parser\n\nif __name__ == '__main__':\n    parser.parse('x')",
            "parser.py": "def parse(value):\n    return value.strip()",
            "parser_old.py": "def parse(value):\n    return value.strip()",
        },
    )

    first = Analyzer(tmp_path).report("stale").to_dict()["findings"]
    second = Analyzer(tmp_path).report("stale").to_dict()["findings"]

    assert first == second
    assert first[0]["id"].startswith("stale-file:")
    assert first[0]["confidence"] < 0.85
    assert first[0]["recommendation"] == "review"


def test_fix_uses_the_same_git_base_as_diff(tmp_path: Path, capsys) -> None:
    executable = git_executable()
    if executable is None:
        pytest.skip("git is unavailable")
    write_project(
        tmp_path,
        {
            "app.py": (
                "import parser_v2\n\n"
                "if __name__ == '__main__':\n"
                "    parser_v2.parse('x')"
            ),
            "parser_v2.py": "def parse(value):\n    return value.strip()",
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
            "app.py": (
                "import parser\n\nif __name__ == '__main__':\n    parser.parse('x')"
            ),
            "parser.py": "def parse(value):\n    return value.strip()",
        },
    )
    _git(executable, tmp_path, "add", ".")
    _git(executable, tmp_path, "commit", "-m", "new parser")

    exit_code = main(["fix", str(tmp_path), "--base", "HEAD~1", "--dry-run"])

    assert exit_code == 0
    assert "DELETE parser_v2.py" in capsys.readouterr().out

    exit_code = main(
        ["fix", str(tmp_path), "--base", "HEAD~1", "--dry-run", "--format", "json"]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["plan_id"]
    assert payload["operations"][0]["candidate_sha256"]

    validation = (
        f"\"{sys.executable}\" -c \"import parser; assert parser.parse(' x ') == 'x'\""
    )
    exit_code = main(
        [
            "fix",
            str(tmp_path),
            "--base",
            "HEAD~1",
            "--apply",
            "--validate",
            validation,
        ]
    )
    assert exit_code == 0
    assert not (tmp_path / "parser_v2.py").exists()
    capsys.readouterr()

    assert main(["fix", str(tmp_path), "--restore"]) == 0
    assert (tmp_path / "parser_v2.py").is_file()


def test_repo_validation_commands_require_explicit_trust(
    tmp_path: Path, capsys
) -> None:
    executable = git_executable()
    if executable is None:
        pytest.skip("git is unavailable")
    write_project(
        tmp_path,
        {
            "repo-gardener.toml": (
                '[validation]\ncommands = ["echo ran > repo-command-ran.txt"]'
            ),
            "app.py": "import parser_v2\n\nif __name__ == '__main__':\n    pass",
            "parser_v2.py": "def parse(value):\n    return value.strip()",
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
            "app.py": "import parser\n\nif __name__ == '__main__':\n    pass",
            "parser.py": "def parse(value):\n    return value.strip()",
        },
    )
    _git(executable, tmp_path, "add", ".")
    _git(executable, tmp_path, "commit", "-m", "new parser")

    exit_code = main(["fix", str(tmp_path), "--base", "HEAD~1", "--apply"])

    assert exit_code == 2
    assert not (tmp_path / "repo-command-ran.txt").exists()
    assert "untrusted" in capsys.readouterr().err


def test_gitignore_is_respected_without_a_git_repository(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            ".gitignore": "ignored.py",
            "app.py": "if __name__ == '__main__':\n    print('ok')",
            "ignored.py": "def stale():\n    return True",
        },
    )

    report = Analyzer(tmp_path).report("scan")

    assert report.metrics["python_files"] == 1


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
