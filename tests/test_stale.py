from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from conftest import write_project
from repo_gardener.analysis import Analyzer
from repo_gardener.analysis import stale as stale_module
from repo_gardener.cli import main
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


@pytest.mark.parametrize(
    "loader",
    [
        "from importlib import import_module\nimport_module('parser_old')",
        "from importlib import import_module as im\nim('parser_old')",
        "import importlib as il\nil.import_module('parser_old')",
        (
            "from importlib import import_module\n"
            "loader = import_module\nloader2 = loader\nloader2('parser_old')"
        ),
    ],
)
def test_aliased_literal_dynamic_import_is_treated_as_live(
    tmp_path: Path, loader: str
) -> None:
    write_project(
        tmp_path,
        {
            "app.py": f"{loader}\n\nif __name__ == '__main__':\n    pass",
            "parser_old.py": "def parse(value):\n    return value.strip()",
            "parser.py": "def parse(value):\n    return value.strip()",
        },
    )

    report = Analyzer(tmp_path).report("stale")

    assert not any(finding.path == "parser_old.py" for finding in report.findings)


def test_runpy_and_module_shaped_registry_strings_keep_modules_live(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "app.py": (
                "import launcher\nimport registry\n\n"
                "BACKEND = 'registry_old:Backend'\n\n"
                "if __name__ == '__main__':\n    launcher.launch()"
            ),
            "launcher.py": (
                "import runpy\n\ndef launch():\n"
                "    return runpy.run_module('parser_old')"
            ),
            "parser_old.py": "def parse(value):\n    return value.strip()",
            "parser.py": "def parse(value):\n    return value.strip()",
            "registry_old.py": "class Backend:\n    pass",
            "registry.py": "class Backend:\n    pass",
        },
    )

    report = Analyzer(tmp_path).report("stale")

    assert not any(finding.path == "parser_old.py" for finding in report.findings)
    registry_finding = next(
        finding for finding in report.findings if finding.path == "registry_old.py"
    )
    assert registry_finding.risk >= 0.75
    assert registry_finding.recommendation == "review"
    assert any("module_shaped_string" in risk for risk in registry_finding.risks)


@pytest.mark.parametrize(
    "loader_source",
    [
        "old = eval(\"__import__('plugin_old')\")",
        "exec(\"__import__('plugin_old')\")",
        (
            "import importlib\n\ndef load(name):\n"
            "    return getattr(importlib, 'import_module')(name)"
        ),
        (
            "import builtins\nloader = builtins.__import__\n\n"
            "def load(name):\n    return loader(name)"
        ),
        (
            "from builtins import __import__ as load\n\n"
            "def discover(name):\n    return load(name)"
        ),
        "import pkgutil\nPLUGINS = list(pkgutil.iter_modules())",
        "from pkgutil import walk_packages as scan\nPLUGINS = list(scan())",
        (
            "import importlib\nil = importlib\nil2 = il\n\n"
            "def load(name):\n    return il2.import_module(name)"
        ),
        (
            "import builtins\nb = builtins\nb2 = b\nloader = b2.__import__\n\n"
            "def load(name):\n    return loader(name)"
        ),
        ("from importlib import import_module\nLOADERS = {'python': import_module}"),
        "from importlib import import_module\nLOADERS = [import_module]",
        (
            "from importlib import import_module\n\n"
            "def register(loader):\n    return loader\n\n"
            "LOADER = register(import_module)"
        ),
        (
            "from pkg_resources import iter_entry_points as iep\n"
            "PLUGINS = list(iep('demo.plugins'))"
        ),
        ("import runpy\n\ndef load(path):\n    return runpy.run_path(path)"),
        (
            "import importlib.util as util\n\ndef load(name, path):\n"
            "    return util.spec_from_file_location(name, path)"
        ),
        (
            "from importlib.machinery import SourceFileLoader\n\n"
            "def load(name, path):\n    return SourceFileLoader(name, path)"
        ),
        (
            "from importlib.machinery import SourcelessFileLoader as Loader\n\n"
            "def load(name, path):\n    return Loader(name, path)"
        ),
        (
            "import importlib\n\n"
            "def load(name):\n"
            "    return {'python': importlib.import_module}['python'](name)"
        ),
        (
            "import importlib\n\n"
            "class Loader:\n"
            "    load_module = importlib.import_module\n\n"
            "    def load(self, name):\n"
            "        return self.load_module(name)"
        ),
    ],
)
def test_opaque_runtime_discovery_disables_safe_deletion(
    tmp_path: Path, loader_source: str
) -> None:
    executable = git_executable()
    if executable is None:
        pytest.skip("git is unavailable")
    write_project(
        tmp_path,
        {
            "app.py": "import plugin_old\n\nif __name__ == '__main__':\n    pass",
            "loader.py": loader_source,
            "plugin_old.py": "def run():\n    return True",
        },
    )
    _git(executable, tmp_path, "init")
    _git(executable, tmp_path, "config", "user.email", "tests@example.com")
    _git(executable, tmp_path, "config", "user.name", "Repo Gardener Tests")
    _git(executable, tmp_path, "add", ".")
    _git(executable, tmp_path, "commit", "-m", "old plugin")
    write_project(
        tmp_path,
        {
            "app.py": "import plugin\n\nif __name__ == '__main__':\n    pass",
            "plugin.py": "def run():\n    return True",
        },
    )
    _git(executable, tmp_path, "add", ".")
    _git(executable, tmp_path, "commit", "-m", "replace plugin")

    finding = next(
        item
        for item in Analyzer(tmp_path).report("diff", "HEAD~1").findings
        if item.path == "plugin_old.py"
    )

    assert finding.confidence >= 0.85
    assert finding.risk == 1.0
    assert finding.recommendation == "review"
    assert any("opaque_dynamic_module_discovery" in risk for risk in finding.risks)


def test_nonliteral_dynamic_discovery_disables_safe_deletion(tmp_path: Path) -> None:
    executable = git_executable()
    if executable is None:
        pytest.skip("git is unavailable")
    write_project(
        tmp_path,
        {
            "app.py": "import parser_v2\n\nif __name__ == '__main__':\n    pass",
            "loader.py": "import importlib\n\ndef load(name):\n    return importlib.import_module(name)",
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

    finding = next(
        item
        for item in Analyzer(tmp_path).report("diff", "HEAD~1").findings
        if item.path == "parser_v2.py"
    )

    assert finding.risk == 1.0
    assert finding.recommendation == "review"
    assert any("opaque_dynamic_module_discovery" in risk for risk in finding.risks)


def test_repository_parse_error_disables_safe_deletion(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    executable = git_executable()
    if executable is None:
        pytest.skip("git is unavailable")
    write_project(
        tmp_path,
        {
            "app.py": "import parser_old\n\nif __name__ == '__main__':\n    pass",
            "broken.py": "import parser_old\nthis is not valid python !!!",
            "parser_old.py": "def parse(value):\n    return value.strip()",
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
    _git(executable, tmp_path, "commit", "-m", "replace parser")

    report = Analyzer(tmp_path).report("diff", "HEAD~1")
    finding = next(item for item in report.findings if item.path == "parser_old.py")

    assert report.metrics["parse_errors"] == 1
    assert finding.confidence >= 0.85
    assert finding.risk == 1.0
    assert finding.recommendation == "review"
    assert any("repository_parse_errors" in risk for risk in finding.risks)
    assert safe_candidates(report.findings) == []
    assert main(["diff", str(tmp_path), "--base", "HEAD~1"]) == 0
    pretty = capsys.readouterr().out
    assert "could not be parsed" in pretty
    assert "broken.py" in pretty
    assert main(["fix", str(tmp_path), "--base", "HEAD~1", "--dry-run"]) == 0
    assert "1 Python file(s) could not be parsed" in capsys.readouterr().out
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
    assert plan["schema_version"] == 2
    assert plan["operations"] == []
    assert any(
        "could not be parsed" in blocker
        for blocker in plan["automatic_deletion_blockers"]
    )


def test_utf8_bom_preserves_import_migration_evidence(tmp_path: Path) -> None:
    executable = git_executable()
    if executable is None:
        pytest.skip("git is unavailable")
    write_project(
        tmp_path,
        {"parser_v2.py": "def parse(value):\n    return value.strip()"},
    )
    (tmp_path / "app.py").write_text(
        "import parser_v2\n\nif __name__ == '__main__':\n    pass\n",
        encoding="utf-8-sig",
    )
    _git(executable, tmp_path, "init")
    _git(executable, tmp_path, "config", "user.email", "tests@example.com")
    _git(executable, tmp_path, "config", "user.name", "Repo Gardener Tests")
    _git(executable, tmp_path, "add", ".")
    _git(executable, tmp_path, "commit", "-m", "old parser")
    write_project(
        tmp_path,
        {"parser.py": "def parse(value):\n    return value.strip()"},
    )
    (tmp_path / "app.py").write_text(
        "import parser\n\nif __name__ == '__main__':\n    pass\n",
        encoding="utf-8-sig",
    )
    _git(executable, tmp_path, "add", ".")
    _git(executable, tmp_path, "commit", "-m", "replace parser")

    report = Analyzer(tmp_path).report("diff", "HEAD~1")
    finding = next(item for item in report.findings if item.path == "parser_v2.py")

    assert report.metrics["parse_errors"] == 0
    assert finding.replacement == "parser.py"
    assert finding.confidence >= 0.85
    assert finding.recommendation == "safe_delete_candidate"
    assert safe_candidates(report.findings) == [finding]


def test_packaging_plugin_entrypoint_is_a_graph_root(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "pyproject.toml": """
[project]
name = "demo"
version = "0.1.0"

[project.entry-points."demo.plugins"]
legacy = "plugin_old:run"
""",
            "app.py": "import plugin\n\nif __name__ == '__main__':\n    pass",
            "plugin.py": "def run():\n    return True",
            "plugin_old.py": "def run():\n    return True",
        },
    )

    analyzer = Analyzer(tmp_path)

    assert "plugin_old" in analyzer.graph.roots
    assert not any(
        finding.path == "plugin_old.py" for finding in analyzer.report("stale").findings
    )


@pytest.mark.parametrize(
    ("metadata_name", "metadata_source"),
    [
        (
            "setup.cfg",
            """
[metadata]
name = demo

[options.entry_points]
demo.plugins =
    legacy = plugin_old:run
""",
        ),
        (
            "setup.py",
            """
from setuptools import setup

setup(
    name="demo",
    entry_points={"demo.plugins": ["legacy=plugin_old:run"]},
)
""",
        ),
    ],
)
def test_legacy_packaging_entrypoint_is_a_graph_root(
    tmp_path: Path, metadata_name: str, metadata_source: str
) -> None:
    write_project(
        tmp_path,
        {
            metadata_name: metadata_source,
            "app.py": "import plugin\n\nif __name__ == '__main__':\n    pass",
            "plugin.py": "def run():\n    return True",
            "plugin_old.py": "def run():\n    return True",
        },
    )

    analyzer = Analyzer(tmp_path)

    assert "plugin_old" in analyzer.graph.roots
    assert not any(
        finding.path == "plugin_old.py" for finding in analyzer.report("stale").findings
    )


def test_nonliteral_setup_metadata_disables_safe_deletion(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "setup.py": "from setuptools import setup\nsetup(entry_points=ENTRY_POINTS)",
            "app.py": "import parser\n\nif __name__ == '__main__':\n    pass",
            "parser.py": "def parse(value):\n    return value.strip()",
            "parser_old.py": "def parse(value):\n    return value.strip()",
        },
    )

    finding = next(
        item
        for item in Analyzer(tmp_path).report("stale").findings
        if item.path == "parser_old.py"
    )

    assert finding.risk == 1.0
    assert finding.recommendation == "review"
    assert any("packaging_entrypoint_uncertainty" in risk for risk in finding.risks)


def test_setup_function_alias_entrypoint_is_a_graph_root(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "setup.py": (
                "from setuptools import setup as package\n"
                "build = package\n"
                "build(entry_points={'demo.plugins': ['legacy=plugin_old:run']})"
            ),
            "app.py": "import plugin\n\nif __name__ == '__main__':\n    pass",
            "plugin.py": "def run():\n    return True",
            "plugin_old.py": "def run():\n    return True",
        },
    )

    analyzer = Analyzer(tmp_path)

    assert "plugin_old" in analyzer.graph.roots
    assert not any(
        finding.path == "plugin_old.py" for finding in analyzer.report("stale").findings
    )


@pytest.mark.parametrize(
    ("metadata_name", "metadata_source"),
    [
        (
            "pyproject.toml",
            '[tool.setuptools]\npy-modules = ["plugin_old", "plugin"]',
        ),
        (
            "setup.cfg",
            "[options]\npy_modules =\n    plugin_old\n    plugin",
        ),
        (
            "setup.py",
            (
                "from setuptools import setup\n"
                "MODULES = ['plugin_old', 'plugin']\n"
                "setup(py_modules=MODULES)"
            ),
        ),
    ],
)
def test_packaged_public_module_is_never_an_automatic_deletion(
    tmp_path: Path, metadata_name: str, metadata_source: str
) -> None:
    executable = git_executable()
    if executable is None:
        pytest.skip("git is unavailable")
    write_project(
        tmp_path,
        {
            metadata_name: metadata_source,
            "app.py": "import plugin_old\n\nif __name__ == '__main__':\n    pass",
            "plugin_old.py": "def run():\n    return True",
        },
    )
    _git(executable, tmp_path, "init")
    _git(executable, tmp_path, "config", "user.email", "tests@example.com")
    _git(executable, tmp_path, "config", "user.name", "Repo Gardener Tests")
    _git(executable, tmp_path, "add", ".")
    _git(executable, tmp_path, "commit", "-m", "old plugin")
    write_project(
        tmp_path,
        {
            "app.py": "import plugin\n\nif __name__ == '__main__':\n    pass",
            "plugin.py": "def run():\n    return True",
        },
    )
    _git(executable, tmp_path, "add", ".")
    _git(executable, tmp_path, "commit", "-m", "replace plugin")

    analyzer = Analyzer(tmp_path)
    finding = next(
        item
        for item in analyzer.report("diff", "HEAD~1").findings
        if item.path == "plugin_old.py"
    )

    assert "plugin_old" not in analyzer.graph.roots
    assert finding.confidence >= 0.85
    assert finding.risk == 1.0
    assert finding.recommendation == "review"
    assert "packaged_public_module" in finding.risks


def test_nonliteral_setup_py_modules_disables_safe_deletion(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "setup.py": "from setuptools import setup\nsetup(py_modules=PY_MODULES)",
            "app.py": "import parser\n\nif __name__ == '__main__':\n    pass",
            "parser.py": "def parse(value):\n    return value.strip()",
            "parser_old.py": "def parse(value):\n    return value.strip()",
        },
    )

    finding = next(
        item
        for item in Analyzer(tmp_path).report("stale").findings
        if item.path == "parser_old.py"
    )

    assert finding.risk == 1.0
    assert finding.recommendation == "review"
    assert any("nonliteral-py-modules" in risk for risk in finding.risks)


def test_pep420_namespace_package_has_external_api_risk(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "pyproject.toml": """
[project]
name = "acme-tools"
version = "0.1.0"

[tool.setuptools.packages.find]
namespaces = true
""",
            "app.py": "import acme.plugin\n\nif __name__ == '__main__':\n    pass",
            "acme/plugin.py": "def run():\n    return True",
            "acme/plugin_old.py": "def run():\n    return True",
        },
    )

    finding = next(
        item
        for item in Analyzer(tmp_path).report("stale").findings
        if item.path == "acme/plugin_old.py"
    )

    assert finding.risk >= 0.45
    assert finding.recommendation == "review"


def test_implicit_pep420_namespace_package_has_external_api_risk(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "app.py": "import acme.plugin\n\nif __name__ == '__main__':\n    pass",
            "acme/plugin.py": "def run():\n    return True",
            "acme/plugin_old.py": "def run():\n    return True",
        },
    )

    finding = next(
        item
        for item in Analyzer(tmp_path).report("stale").findings
        if item.path == "acme/plugin_old.py"
    )

    assert finding.risk >= 0.45
    assert finding.recommendation == "review"


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


def test_uncommitted_replacement_is_detected_and_dirty_candidate_is_not_safe(
    tmp_path: Path,
) -> None:
    executable = git_executable()
    if executable is None:
        pytest.skip("git is unavailable")
    write_project(
        tmp_path,
        {
            "app.py": "import parser_v2\n\nif __name__ == '__main__':\n    pass",
            "parser_v2.py": "def parse(value):\n    return value.strip()",
        },
    )
    _git(executable, tmp_path, "init")
    _git(executable, tmp_path, "config", "user.email", "tests@example.com")
    _git(executable, tmp_path, "config", "user.name", "Repo Gardener Tests")
    _git(executable, tmp_path, "add", ".")
    _git(executable, tmp_path, "commit", "-m", "baseline")
    write_project(
        tmp_path,
        {
            "app.py": "import parser\n\nif __name__ == '__main__':\n    pass",
            "parser.py": "def parse(value):\n    return value.strip()",
        },
    )

    initial = next(
        item
        for item in Analyzer(tmp_path).report("diff", "HEAD").findings
        if item.path == "parser_v2.py"
    )
    assert initial.recommendation == "safe_delete_candidate"

    write_project(
        tmp_path,
        {"parser_v2.py": "def parse(value):\n    return value.strip().upper()"},
    )
    dirty = next(
        item
        for item in Analyzer(tmp_path).report("diff", "HEAD").findings
        if item.path == "parser_v2.py"
    )
    assert dirty.risk >= 0.75
    assert dirty.recommendation == "review"


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
            "repo-gardener.toml": (
                "[safety]\nallow_delete_src = true\nallow_delete_package_modules = true"
            ),
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


def test_non_src_package_module_has_external_api_risk(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "package/__init__.py": "from .parser import parse",
            "package/parser.py": "def parse(value):\n    return value.strip()",
            "package/parser_old.py": "def parse(value):\n    return value.strip()",
        },
    )

    finding = next(
        item
        for item in Analyzer(tmp_path).report("stale").findings
        if item.path == "package/parser_old.py"
    )

    assert finding.risk >= 0.45
    assert finding.recommendation == "review"


def test_relative_package_import_migration_is_resolved(tmp_path: Path) -> None:
    executable = git_executable()
    if executable is None:
        pytest.skip("git is unavailable")
    write_project(
        tmp_path,
        {
            "repo-gardener.toml": (
                "[safety]\nallow_delete_src = true\nallow_delete_package_modules = true"
            ),
            "src/pkg/__init__.py": "from .parser_v2 import parse",
            "src/pkg/parser_v2.py": "def parse(value):\n    return value.strip()",
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
            "src/pkg/__init__.py": "from .parser import parse",
            "src/pkg/parser.py": "def parse(value):\n    return value.strip()",
        },
    )
    _git(executable, tmp_path, "add", ".")
    _git(executable, tmp_path, "commit", "-m", "new parser")

    finding = next(
        item
        for item in Analyzer(tmp_path).report("diff", "HEAD~1").findings
        if item.path.endswith("parser_v2.py")
    )

    assert finding.recommendation == "safe_delete_candidate"
    assert any(item["type"] == "call_site_migration" for item in finding.evidence)


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
        (
            "from fastapi import FastAPI as F\nimport worker\napp = F()",
            "fastapi",
        ),
        (
            "import fastapi as fa\nimport worker\napp = fa.FastAPI()",
            "fastapi",
        ),
        (
            "from flask import Flask as F\nimport worker\napp = F(__name__)",
            "flask",
        ),
        (
            "from typer import Typer as T\nimport worker\napp = T()",
            "typer",
        ),
        (
            "from click import command as cmd\nimport worker\n@cmd()\ndef cli():\n    pass",
            "click",
        ),
        (
            "from fastapi import FastAPI as F\nimport worker\nAPI = F\napp = API()",
            "fastapi",
        ),
        (
            (
                "try:\n    from fastapi import FastAPI as F\n"
                "except ImportError:\n    F = object\n"
                "import worker\napp = F()"
            ),
            "fastapi",
        ),
        (
            (
                "if True:\n    from flask import Flask as F\n"
                "import worker\napp = F(__name__)"
            ),
            "flask",
        ),
        (
            ("if True:\n    from typer import Typer as T\nimport worker\napp = T()"),
            "typer",
        ),
        (
            (
                "if True:\n    from click import command as cmd\n"
                "import worker\n@cmd()\ndef cli():\n    pass"
            ),
            "click",
        ),
        (
            (
                "import worker\n\ndef create_app():\n"
                "    from flask import Flask as F\n"
                "    return F(__name__)"
            ),
            "flask",
        ),
        (
            (
                "import worker\n\ndef create_app():\n"
                "    try:\n        from fastapi import FastAPI as F\n"
                "    except ImportError:\n        F = object\n"
                "    return F()"
            ),
            "fastapi",
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


def test_ambiguous_import_alias_keeps_every_possible_module_live(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "app.py": "import foo\n\nif __name__ == '__main__':\n    print(foo.VALUE)",
            "foo.py": "VALUE = 'root'",
            "src/foo.py": "VALUE = 'src'",
        },
    )

    analyzer = Analyzer(tmp_path)

    assert {"foo", "src.foo"} <= analyzer.graph.reachable
    assert analyzer.graph.inbound_count("foo") == 1
    assert analyzer.graph.inbound_count("src.foo") == 1


@pytest.mark.parametrize(
    ("relative_path", "root_kind"),
    [
        ("sitecustomize.py", "python-runtime"),
        ("usercustomize.py", "python-runtime"),
        ("noxfile.py", "nox"),
        ("fabfile.py", "fabric"),
        ("locustfile.py", "locust"),
        ("docs/conf.py", "sphinx"),
    ],
)
def test_implicit_runtime_and_tool_entrypoints_are_roots(
    tmp_path: Path, relative_path: str, root_kind: str
) -> None:
    write_project(tmp_path, {relative_path: "VALUE = 'active'"})

    analyzer = Analyzer(tmp_path)
    record = analyzer.records[0]

    assert record.module in analyzer.graph.roots
    assert root_kind in record.framework_entrypoints


def test_assignment_only_config_replacement_is_never_auto_delete(
    tmp_path: Path,
) -> None:
    executable = git_executable()
    if executable is None:
        pytest.skip("git is unavailable")
    write_project(
        tmp_path,
        {
            "app.py": "import settings_v2\n\nif __name__ == '__main__':\n    pass",
            "settings_v2.py": (
                "API_URL = 'https://production'\nRETRIES = 9\nFEATURE_ENABLED = True"
            ),
        },
    )
    _git(executable, tmp_path, "init")
    _git(executable, tmp_path, "config", "user.email", "tests@example.com")
    _git(executable, tmp_path, "config", "user.name", "Repo Gardener Tests")
    _git(executable, tmp_path, "add", ".")
    _git(executable, tmp_path, "commit", "-m", "old settings")
    write_project(
        tmp_path,
        {
            "app.py": "import settings\n\nif __name__ == '__main__':\n    pass",
            "settings.py": (
                "API_URL = 'http://localhost'\nRETRIES = 1\nFEATURE_ENABLED = False"
            ),
        },
    )
    _git(executable, tmp_path, "add", ".")
    _git(executable, tmp_path, "commit", "-m", "replace settings")

    finding = next(
        item
        for item in Analyzer(tmp_path).report("diff", "HEAD~1").findings
        if item.path == "settings_v2.py"
    )

    assert finding.risk >= 0.55
    assert finding.recommendation == "review"
    assert "data_or_config_module_requires_literal_review" in finding.risks


def test_replacement_must_preserve_public_constants_signatures_and_class_members(
    tmp_path: Path,
) -> None:
    executable = git_executable()
    if executable is None:
        pytest.skip("git is unavailable")
    old_source = (
        "MODE = 'production'\n\n"
        "def parse(value, strict=False):\n    return value\n\n"
        "class Parser:\n"
        "    def parse(self, value):\n        return value\n"
        "    def reset(self):\n        return None\n"
    )
    new_source = (
        "MODE = 'development'\n\n"
        "def parse(value):\n    return value\n\n"
        "class Parser:\n"
        "    def parse(self, value):\n        return value\n"
    )
    write_project(
        tmp_path,
        {
            "app.py": "import parser_v2\n\nif __name__ == '__main__':\n    pass",
            "parser_v2.py": old_source,
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
            "parser.py": new_source,
        },
    )
    _git(executable, tmp_path, "add", ".")
    _git(executable, tmp_path, "commit", "-m", "replace parser")

    finding = next(
        item
        for item in Analyzer(tmp_path).report("diff", "HEAD~1").findings
        if item.path == "parser_v2.py"
    )
    evidence = {item["type"]: item["value"] for item in finding.evidence}

    assert finding.risk >= 0.55
    assert finding.recommendation == "review"
    assert set(evidence["public_contract_changed_in_replacement"]) == {
        "MODE",
        "Parser",
        "parse",
    }


@pytest.mark.parametrize("metadata_key", ["import-names", "import-namespaces"])
def test_project_import_metadata_marks_modules_public(
    tmp_path: Path, metadata_key: str
) -> None:
    write_project(
        tmp_path,
        {
            "pyproject.toml": (
                "[project]\nname = 'demo'\nversion = '0.1.0'\n"
                f"{metadata_key} = ['legacy ; private']"
            ),
            "app.py": "import current\n\nif __name__ == '__main__':\n    pass",
            "legacy.py": "VALUE = 1",
            "current.py": "VALUE = 1",
        },
    )

    analyzer = Analyzer(tmp_path)
    legacy = next(record for record in analyzer.records if record.module == "legacy")

    assert legacy.packaged_public_module is True


@pytest.mark.parametrize(
    "dynamic_key",
    ["scripts", "gui-scripts", "entry-points", "import-names", "import-namespaces"],
)
def test_dynamic_public_packaging_metadata_disables_auto_delete(
    tmp_path: Path, dynamic_key: str
) -> None:
    write_project(
        tmp_path,
        {
            "pyproject.toml": (
                "[project]\nname = 'demo'\nversion = '0.1.0'\n"
                f"dynamic = ['{dynamic_key}']"
            ),
            "app.py": "if __name__ == '__main__':\n    pass",
            "legacy.py": "VALUE = 1",
        },
    )

    analyzer = Analyzer(tmp_path)

    assert any(
        f"dynamic-{dynamic_key}" in source
        for record in analyzer.records
        for source in record.packaging_uncertainty
    )


def test_custom_setuptools_package_root_resolves_entrypoint_module(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "pyproject.toml": """
[project]
name = "demo"
version = "0.1.0"

[project.scripts]
demo = "mypkg.web:main"

[tool.setuptools.package-dir]
"" = "python"
""",
            "python/mypkg/__init__.py": "",
            "python/mypkg/web.py": "def main():\n    return True",
        },
    )

    analyzer = Analyzer(tmp_path)
    web = next(record for record in analyzer.records if record.path.name == "web.py")

    assert web.module == "mypkg.web"
    assert "mypkg.web" in analyzer.graph.roots


@pytest.mark.parametrize(
    ("metadata_name", "metadata"),
    [
        (
            "setup.cfg",
            """
[metadata]
name = demo
version = 0.1.0

[options]
package_dir =
    = python

[options.entry_points]
console_scripts =
    demo = mypkg.web:main
""",
        ),
        (
            "setup.py",
            """
from setuptools import setup

setup(
    name="demo",
    package_dir={"": "python"},
    entry_points={"console_scripts": ["demo=mypkg.web:main"]},
)
""",
        ),
        (
            "pyproject.toml",
            """
[project]
name = "demo"
version = "0.1.0"
scripts = { demo = "mypkg.web:main" }

[tool.setuptools.packages.find]
where = ["python"]
""",
        ),
    ],
)
def test_legacy_and_find_where_source_roots_resolve_entrypoints(
    tmp_path: Path, metadata_name: str, metadata: str
) -> None:
    write_project(
        tmp_path,
        {
            metadata_name: metadata,
            "python/mypkg/__init__.py": "",
            "python/mypkg/web.py": "def main():\n    return True",
        },
    )

    analyzer = Analyzer(tmp_path)
    web = next(record for record in analyzer.records if record.path.name == "web.py")

    assert web.module == "mypkg.web"
    assert "mypkg.web" in analyzer.graph.roots


def test_unchanged_files_use_external_content_addressed_parse_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "external-cache"
    repository = tmp_path / "repository"
    monkeypatch.setenv("REPO_GARDENER_CACHE_DIR", str(cache))
    write_project(
        repository,
        {"app.py": "if __name__ == '__main__':\n    print('ok')\n"},
    )

    first = Analyzer(repository).report("scan")
    second = Analyzer(repository).report("scan")
    (repository / "app.py").write_text(
        "if __name__ == '__main__':\n    print('changed')\n", encoding="utf-8"
    )
    third = Analyzer(repository).report("scan")

    assert first.metrics["parse_cache_hits"] == 0
    assert second.metrics["parse_cache_hits"] == 1
    assert third.metrics["parse_cache_hits"] == 0
    assert cache.is_dir()
    assert not (repository / ".repo-gardener").exists()


def test_parse_cache_reuses_identical_context_across_ephemeral_worktrees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "external-cache"
    first_repository = tmp_path / "agent-worktree-1"
    second_repository = tmp_path / "agent-worktree-2"
    monkeypatch.setenv("REPO_GARDENER_CACHE_DIR", str(cache))
    source = "if __name__ == '__main__':\n    print('ok')\n"
    write_project(first_repository, {"app.py": source})
    write_project(second_repository, {"app.py": source})

    first = Analyzer(first_repository).report("scan")
    second = Analyzer(second_repository).report("scan")

    assert first.metrics["parse_cache_hits"] == 0
    assert second.metrics["parse_cache_hits"] == 1


def test_discovery_never_reads_python_symlinks_outside_repository(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.py"
    outside.write_text("SECRET = 'outside'\n", encoding="utf-8")
    link = tmp_path / "outside.py"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    analyzer = Analyzer(tmp_path)

    assert analyzer.records == []


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
