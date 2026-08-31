from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from repo_gardener.analysis import Analyzer
from repo_gardener.fixes import safe_candidates

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "extended_gc_rules"


def test_extended_gc_rules_are_review_only(tmp_path: Path) -> None:
    shutil.copytree(FIXTURE_ROOT, tmp_path, dirs_exist_ok=True)

    report = Analyzer(tmp_path).report("stale")
    by_rule = {finding.rule for finding in report.findings}

    assert "orphan-helper" in by_rule
    assert "duplicate-implementation" in by_rule
    assert "dependency-leftover" in by_rule
    assert not safe_candidates(report.findings)
    assert all(
        finding.recommendation != "safe_delete_candidate"
        for finding in report.findings
        if finding.rule
        in {"orphan-helper", "duplicate-implementation", "dependency-leftover"}
    )


def test_imported_dependency_is_not_reported_as_leftover(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "used-dependency"\nversion = "0"\n'
        'dependencies = ["PyYAML>=6"]\n',
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text(
        'import yaml\n\nif __name__ == "__main__":\n    print(yaml.safe_load("x: 1"))\n',
        encoding="utf-8",
    )

    report = Analyzer(tmp_path).report("stale")

    assert not any(finding.rule == "dependency-leftover" for finding in report.findings)


def test_exported_or_referenced_helpers_are_not_orphans(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "from service import public_api\n\n"
        "if __name__ == '__main__':\n    public_api()\n",
        encoding="utf-8",
    )
    (tmp_path / "service.py").write_text(
        "__all__ = ['public_api', '_exported_private']\n\n"
        "def public_api():\n    return _used_private()\n\n"
        "def _used_private():\n    return 1\n\n"
        "def _exported_private():\n    return 2\n",
        encoding="utf-8",
    )

    report = Analyzer(tmp_path).report("stale")
    orphan_symbols = {
        next(item["value"] for item in finding.evidence if item["type"] == "symbol")
        for finding in report.findings
        if finding.rule == "orphan-helper"
    }

    assert orphan_symbols == set()


@pytest.mark.parametrize(
    ("manifest", "content"),
    [
        ("requirements.txt", "requests>=2\n"),
        ("setup.cfg", "[options]\ninstall_requires =\n    requests>=2\n"),
        (
            "setup.py",
            """from setuptools import setup

setup(name='fixture', install_requires=['requests>=2'])
""",
        ),
    ],
)
def test_legacy_dependency_manifests_are_scanned(
    tmp_path: Path, manifest: str, content: str
) -> None:
    (tmp_path / manifest).write_text(content, encoding="utf-8")
    (tmp_path / "app.py").write_text(
        "if __name__ == '__main__':\n    print('ready')\n", encoding="utf-8"
    )

    findings = Analyzer(tmp_path).report("stale").findings

    finding = next(item for item in findings if item.rule == "dependency-leftover")
    assert finding.path == manifest
    assert finding.recommendation == "review_only"


def test_module_dunder_protocols_are_not_duplicate_candidates(tmp_path: Path) -> None:
    source = (
        "def __getattr__(name):\n"
        "    if name == 'legacy':\n"
        "        return 1\n"
        "    raise AttributeError(name)\n"
    )
    (tmp_path / "app.py").write_text(
        "import first\nimport second\n\nif __name__ == '__main__':\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "first.py").write_text(source, encoding="utf-8")
    (tmp_path / "second.py").write_text(source, encoding="utf-8")

    findings = Analyzer(tmp_path).report("stale").findings

    assert not any(finding.rule == "duplicate-implementation" for finding in findings)


def test_small_generic_wrappers_with_different_names_are_not_duplicates(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.py").write_text(
        "import first\nimport second\n\nif __name__ == '__main__':\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "first.py").write_text(
        "def get_config(mapping, key):\n"
        "    if key not in mapping:\n"
        "        return None\n"
        "    return mapping.get(key)\n",
        encoding="utf-8",
    )
    (tmp_path / "second.py").write_text(
        "def get_cache(mapping, key):\n"
        "    if key not in mapping:\n"
        "        return None\n"
        "    return mapping.get(key)\n",
        encoding="utf-8",
    )

    findings = Analyzer(tmp_path).report("stale").findings

    assert not any(finding.rule == "duplicate-implementation" for finding in findings)
