from __future__ import annotations

from pathlib import Path

import pytest
from conftest import write_project
from repo_gardener.cli import main
from repo_gardener.config import load_config


@pytest.mark.parametrize(
    "source",
    [
        'schema = "1"',
        "entrypoints = []",
        '[entrypoints]\nmodules = "app"',
        "[entrypoints]\npaths = [1]",
        '[safety]\nprotected = "**/vendor/**"',
        '[safety]\nallow_delete_src = "false"',
        "[safety]\nallow_delete_package_modules = 0",
        '[analysis]\nexclude = "build/**"',
        '[analysis]\nflat_directory_threshold = "15"',
        '[analysis]\nmin_similarity = "0.62"',
        '[validation]\ncommands = "pytest"',
    ],
)
def test_config_rejects_incorrect_toml_types(tmp_path: Path, source: str) -> None:
    write_project(tmp_path, {"repo-gardener.toml": source})

    with pytest.raises(ValueError):
        load_config(tmp_path)


def test_cli_reports_string_false_as_invalid_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_project(
        tmp_path,
        {
            "repo-gardener.toml": '[safety]\nallow_delete_src = "false"',
            "app.py": "print('hello')",
        },
    )

    assert main(["scan", str(tmp_path)]) == 2
    assert "safety.allow_delete_src must be a boolean" in capsys.readouterr().err
