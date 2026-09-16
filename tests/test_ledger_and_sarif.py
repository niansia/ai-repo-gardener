from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from conftest import write_project
from repo_gardener.cli import main
from repo_gardener.git_support import git_executable
from repo_gardener.sarif_summary import summarize

LEFTOVER_PROJECT = {
    "app.py": "import parser\n\nif __name__ == '__main__':\n    parser.parse('x')",
    "parser.py": (
        "def parse(value):\n"
        "    return value.strip()\n\n\n"
        "def _unused_helper(value):\n"
        "    return value"
    ),
    "parser_old.py": "def parse(value):\n    return value.strip()",
}


def test_accept_writes_a_sorted_reviewable_ledger(tmp_path: Path, capsys) -> None:
    write_project(tmp_path, LEFTOVER_PROJECT)
    ledger_path = tmp_path / "repo-gardener-accepted.json"

    assert main(["accept", str(tmp_path)]) == 0

    output = capsys.readouterr().out
    document = json.loads(ledger_path.read_text(encoding="utf-8"))
    identifiers = [entry["id"] for entry in document["entries"]]
    assert document["schema_version"] == 1
    assert document["tool"] == "repo-gardener"
    assert identifiers == sorted(identifiers)
    assert {entry["rule"] for entry in document["entries"]} == {
        "orphan-helper",
        "stale-file",
    }
    assert "Accepted 2 finding(s)" in output
    assert ledger_path.read_text(encoding="utf-8").endswith("\n")


def test_accepted_findings_are_suppressed_and_cannot_reach_fail_on(
    tmp_path: Path, capsys
) -> None:
    write_project(tmp_path, LEFTOVER_PROJECT)
    ledger_path = tmp_path / "accepted.json"
    assert main(["accept", str(tmp_path), "--output", str(ledger_path)]) == 0
    capsys.readouterr()

    assert main(["scan", str(tmp_path), "--confidence", "all", "--fail-on", "any"]) == 1
    capsys.readouterr()

    exit_code = main(
        [
            "scan",
            str(tmp_path),
            "--confidence",
            "all",
            "--fail-on",
            "any",
            "--accepted",
            str(ledger_path),
            "--format",
            "json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["findings"] == []
    assert payload["metrics"]["accepted_findings"]["entries"] == 2
    assert payload["metrics"]["accepted_findings"]["suppressed"] == 2
    assert payload["metrics"]["accepted_findings"]["unmatched"] == []


def test_a_ledger_in_the_repository_is_never_loaded_implicitly(
    tmp_path: Path, capsys
) -> None:
    write_project(tmp_path, LEFTOVER_PROJECT)
    assert main(["accept", str(tmp_path)]) == 0
    capsys.readouterr()

    exit_code = main(["scan", str(tmp_path), "--confidence", "all", "--fail-on", "any"])

    assert exit_code == 1
    assert "Accepted ledger" not in capsys.readouterr().out


def test_accept_preserves_notes_and_drops_resolved_entries(
    tmp_path: Path, capsys
) -> None:
    write_project(tmp_path, LEFTOVER_PROJECT)
    ledger_path = tmp_path / "repo-gardener-accepted.json"
    assert main(["accept", str(tmp_path)]) == 0
    capsys.readouterr()
    document = json.loads(ledger_path.read_text(encoding="utf-8"))
    kept = next(
        entry for entry in document["entries"] if entry["rule"] == "orphan-helper"
    )
    kept["note"] = "public plugin hook"
    ledger_path.write_text(json.dumps(document), encoding="utf-8")
    (tmp_path / "parser_old.py").unlink()

    assert main(["accept", str(tmp_path)]) == 0

    output = capsys.readouterr().out
    rewritten = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert [entry["id"] for entry in rewritten["entries"]] == [kept["id"]]
    assert rewritten["entries"][0]["note"] == "public plugin hook"
    assert "Carried notes: 1  Dropped entries: 1" in output


def test_unmatched_accepted_entries_are_reported_for_pruning(
    tmp_path: Path, capsys
) -> None:
    write_project(tmp_path, LEFTOVER_PROJECT)
    ledger_path = tmp_path / "accepted.json"
    ledger_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entries": [{"id": "stale-file:000000000000", "path": "gone.py"}],
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(
        ["scan", str(tmp_path), "--confidence", "all", "--accepted", str(ledger_path)]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "unmatched 1" in output
    assert "Re-run accept to prune them." in output


@pytest.mark.parametrize(
    "document",
    [
        {"schema_version": 2, "entries": []},
        {"schema_version": 1, "entries": [{"id": "stale-file:not-a-digest"}]},
        {"schema_version": 1, "entries": [{"id": "stale-file:000000000000"}] * 2},
        {"schema_version": 1, "entries": {"id": "stale-file:000000000000"}},
        {
            "schema_version": 1,
            "entries": [{"id": "stale-file:000000000000", "note": 7}],
        },
    ],
)
def test_untrustworthy_ledgers_are_rejected(
    tmp_path: Path, capsys, document: dict[str, object]
) -> None:
    write_project(tmp_path, LEFTOVER_PROJECT)
    ledger_path = tmp_path / "accepted.json"
    ledger_path.write_text(json.dumps(document), encoding="utf-8")

    exit_code = main(["scan", str(tmp_path), "--accepted", str(ledger_path)])

    assert exit_code == 2
    assert "accepted-findings ledger" in capsys.readouterr().err


def test_accept_refuses_to_overwrite_an_unreadable_ledger(
    tmp_path: Path, capsys
) -> None:
    write_project(tmp_path, LEFTOVER_PROJECT)
    ledger_path = tmp_path / "repo-gardener-accepted.json"
    ledger_path.write_text("{ not json", encoding="utf-8")

    exit_code = main(["accept", str(tmp_path)])

    assert exit_code == 2
    assert "repair or remove that file" in capsys.readouterr().err
    assert ledger_path.read_text(encoding="utf-8") == "{ not json"


def test_sarif_output_matches_the_code_scanning_contract(
    tmp_path: Path, capsys
) -> None:
    write_project(tmp_path, LEFTOVER_PROJECT)

    exit_code = main(
        ["scan", str(tmp_path), "--confidence", "all", "--format", "sarif"]
    )
    document = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert document["version"] == "2.1.0"
    run = document["runs"][0]
    rules = run["tool"]["driver"]["rules"]
    assert run["tool"]["driver"]["name"] == "AI Repo Gardener"
    assert [rule["id"] for rule in rules] == sorted(rule["id"] for rule in rules)
    assert run["automationDetails"]["id"] == "repo-gardener/scan"
    helper = next(
        result for result in run["results"] if result["ruleId"] == "orphan-helper"
    )
    for result in run["results"]:
        assert rules[result["ruleIndex"]]["id"] == result["ruleId"]
        assert result["level"] in {"error", "warning", "note"}
        assert result["message"]["text"]
        uri = result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        assert not Path(uri).is_absolute()
        assert result["partialFingerprints"]["repoGardenerFindingId/v1"]
    assert helper["locations"][0]["physicalLocation"]["region"]["startLine"] == 5
    assert helper["properties"]["recommendation"] == "review_only"


def test_sarif_respects_confidence_filtering_and_reports_suppression(
    tmp_path: Path, capsys
) -> None:
    write_project(tmp_path, LEFTOVER_PROJECT)
    ledger_path = tmp_path / "accepted.json"
    assert main(["accept", str(tmp_path), "--output", str(ledger_path)]) == 0
    capsys.readouterr()

    assert main(["scan", str(tmp_path), "--format", "sarif"]) == 0
    default_confidence = json.loads(capsys.readouterr().out)["runs"][0]

    assert (
        main(
            [
                "scan",
                str(tmp_path),
                "--confidence",
                "all",
                "--format",
                "sarif",
                "--accepted",
                str(ledger_path),
            ]
        )
        == 0
    )
    suppressed = json.loads(capsys.readouterr().out)["runs"][0]

    assert [result["ruleId"] for result in default_confidence["results"]] == [
        "orphan-helper"
    ]
    assert suppressed["results"] == []
    assert suppressed["tool"]["driver"]["rules"] == []
    assert suppressed["properties"]["acceptedFindings"]["suppressed"] == 2


def test_fix_withholds_accepted_findings_and_pins_the_ledger(
    tmp_path: Path, capsys
) -> None:
    ledger_path = tmp_path / "accepted.json"
    _stale_git_repository(tmp_path)

    assert (
        main(
            [
                "fix",
                str(tmp_path),
                "--base",
                "HEAD~1",
                "--dry-run",
                "--format",
                "json",
            ]
        )
        == 0
    )
    plan = json.loads(capsys.readouterr().out)
    assert [operation["path"] for operation in plan["operations"]] == ["parser_v2.py"]
    ledger_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entries": [
                    {
                        "id": plan["operations"][0]["finding_id"],
                        "rule": "stale-file",
                        "path": "parser_v2.py",
                        "note": "kept for the 0.2 migration window",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "fix",
            str(tmp_path),
            "--base",
            "HEAD~1",
            "--dry-run",
            "--accepted",
            str(ledger_path),
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "DELETE parser_v2.py" not in output
    assert "Withheld 1 accepted finding(s) from deletion: parser_v2.py" in output
    assert (tmp_path / "parser_v2.py").is_file()


def test_changing_the_ledger_invalidates_a_reviewed_plan(
    tmp_path: Path, capsys
) -> None:
    ledger_path = tmp_path / "accepted.json"
    plan_path = tmp_path / "reviewed-plan.json"
    _stale_git_repository(tmp_path)
    assert (
        main(
            [
                "fix",
                str(tmp_path),
                "--base",
                "HEAD~1",
                "--dry-run",
                "--format",
                "json",
            ]
        )
        == 0
    )
    plan_path.write_text(capsys.readouterr().out, encoding="utf-8")
    ledger_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entries": [{"id": "orphan-helper:000000000000", "path": "other.py"}],
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "fix",
            str(tmp_path),
            "--base",
            "HEAD~1",
            "--apply",
            "--plan",
            str(plan_path),
            "--accepted",
            str(ledger_path),
            "--validate",
            "python -c pass",
        ]
    )

    assert exit_code == 2
    assert "no longer matches" in capsys.readouterr().err
    assert (tmp_path / "parser_v2.py").is_file()


def _stale_git_repository(root: Path) -> None:
    executable = git_executable()
    if executable is None:
        pytest.skip("git is unavailable")
    write_project(
        root,
        {
            "app.py": (
                "import parser_v2\n\n"
                "if __name__ == '__main__':\n"
                "    parser_v2.parse('x')"
            ),
            "parser_v2.py": "def parse(value):\n    return value.strip()",
        },
    )
    _git(executable, root, "init")
    _git(executable, root, "config", "user.email", "tests@example.com")
    _git(executable, root, "config", "user.name", "Repo Gardener Tests")
    _git(executable, root, "add", ".")
    _git(executable, root, "commit", "-m", "old parser")
    write_project(
        root,
        {
            "app.py": (
                "import parser\n\nif __name__ == '__main__':\n    parser.parse('x')"
            ),
            "parser.py": "def parse(value):\n    return value.strip()",
        },
    )
    _git(executable, root, "add", ".")
    _git(executable, root, "commit", "-m", "new parser")


def _git(executable: str, root: Path, *arguments: str) -> None:
    environment = os.environ.copy()
    environment["GIT_AUTHOR_DATE"] = "2026-01-01T00:00:00Z"
    environment["GIT_COMMITTER_DATE"] = "2026-01-01T00:00:00Z"
    subprocess.run(
        [executable, "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        env=environment,
    )


def test_sarif_summary_renders_a_reviewable_markdown_table(
    tmp_path: Path, capsys
) -> None:
    write_project(tmp_path, LEFTOVER_PROJECT)
    assert (
        main(["scan", str(tmp_path), "--confidence", "all", "--format", "sarif"]) == 0
    )
    document = json.loads(capsys.readouterr().out)

    markdown = summarize(document)

    assert "| Level | Rule | Location | Detail |" in markdown
    assert "`orphan-helper` | `parser.py:5`" in markdown
    assert "reported **2** finding(s) across 3 Python file(s)" in markdown
    assert markdown.endswith("\n")


def test_sarif_summary_states_an_empty_run_plainly() -> None:
    markdown = summarize(
        {
            "runs": [
                {
                    "results": [],
                    "properties": {"command": "diff", "pythonFiles": 4},
                    "tool": {"driver": {"version": "0.0.0"}},
                }
            ]
        }
    )

    assert "No findings at the selected confidence level." in markdown
    assert "| Level |" not in markdown


def test_sarif_summary_surfaces_suppression_and_parse_errors() -> None:
    markdown = summarize(
        {
            "runs": [
                {
                    "results": [],
                    "properties": {
                        "command": "scan",
                        "pythonFiles": 4,
                        "parseErrors": 2,
                        "acceptedFindings": {
                            "source": "repo-gardener-accepted.json",
                            "entries": 3,
                            "suppressed": 1,
                            "unmatched": ["stale-file:000000000000"],
                        },
                    },
                    "tool": {"driver": {"version": "0.0.0"}},
                }
            ]
        }
    )

    assert "suppressed 1 finding(s)" in markdown
    assert "1 entry/entries no longer" in markdown
    assert "2 file(s) could not be parsed" in markdown
