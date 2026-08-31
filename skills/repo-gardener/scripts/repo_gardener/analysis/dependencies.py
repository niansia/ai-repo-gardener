from __future__ import annotations

import ast
import configparser
import re
import tomllib
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from ..git_support import ImportMigration
from ..models import FileRecord, Finding

DEPENDENCY_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
KNOWN_IMPORT_NAMES: dict[str, tuple[str, ...]] = {
    "beautifulsoup4": ("bs4",),
    "opencv-python": ("cv2",),
    "opencv-python-headless": ("cv2",),
    "pillow": ("PIL",),
    "pyyaml": ("yaml",),
    "python-dateutil": ("dateutil",),
    "scikit-image": ("skimage",),
    "scikit-learn": ("sklearn",),
    "setuptools": ("pkg_resources", "setuptools"),
}
AMBIGUOUS_NAMESPACE_PREFIXES = (
    "azure-",
    "google-cloud-",
    "opentelemetry-",
    "sphinxcontrib-",
    "zope-",
)


@dataclass(frozen=True)
class DeclaredDependency:
    name: str
    source: str
    kind: str


def dependency_leftover_findings(
    root: Path,
    records: list[FileRecord],
    changed: set[str],
    migrations: list[ImportMigration],
) -> list[Finding]:
    declarations = _discover_dependencies(root)
    if not declarations:
        return []
    imports = _import_roots(records)
    removed_imports = {
        value.split(".", 1)[0]
        for migration in migrations
        for value in migration.removed
    }
    changed_python = sorted(path for path in changed if path.endswith(".py"))
    grouped: dict[str, list[DeclaredDependency]] = defaultdict(list)
    for declaration in declarations:
        grouped[_normalize_distribution(declaration.name)].append(declaration)

    findings: list[Finding] = []
    for normalized, items in sorted(grouped.items()):
        candidates = _import_candidates(normalized)
        if imports & set(candidates):
            continue
        declaration_paths = sorted({item.source for item in items})
        related_changed = sorted(set(declaration_paths) & changed)
        removed_candidates = sorted(set(candidates) & removed_imports)
        if changed and not related_changed and not removed_candidates:
            continue
        ambiguous_namespace = normalized.startswith(AMBIGUOUS_NAMESPACE_PREFIXES)
        confidence = 0.58 if ambiguous_namespace else 0.68
        risks = [
            "dependency_can_be_used_via_cli_plugin_or_runtime_metadata",
            "distribution_name_may_not_equal_import_name",
        ]
        if ambiguous_namespace:
            risks.append("namespace_distribution_import_is_ambiguous")
        findings.append(
            Finding(
                rule="dependency-leftover",
                category="repo-gc",
                severity="info",
                confidence=confidence,
                risk=0.88,
                path=declaration_paths[0],
                evidence=[
                    {"type": "dependency", "value": items[0].name},
                    {"type": "declared_in", "value": declaration_paths},
                    {"type": "import_candidates", "value": list(candidates)},
                    {"type": "static_imports_found", "value": []},
                    {"type": "removed_imports", "value": removed_candidates},
                    {
                        "type": "related_changed_paths",
                        "value": sorted(
                            set(related_changed)
                            | (set(changed_python) if removed_candidates else set())
                        )[:20],
                    },
                ],
                risks=risks,
                recommendation="review_only",
            ).finalize()
        )
    return findings


def _discover_dependencies(root: Path) -> list[DeclaredDependency]:
    result: list[DeclaredDependency] = []
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            with pyproject.open("rb") as handle:
                project = tomllib.load(handle).get("project", {})
            if isinstance(project, dict):
                dependencies = project.get("dependencies", [])
                if isinstance(dependencies, list):
                    result.extend(
                        DeclaredDependency(name, "pyproject.toml", "project")
                        for value in dependencies
                        if isinstance(value, str)
                        for name in [_dependency_name(value)]
                        if name
                    )
        except (OSError, tomllib.TOMLDecodeError):
            pass

    setup_cfg = root / "setup.cfg"
    if setup_cfg.is_file():
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read(setup_cfg, encoding="utf-8")
            raw = parser.get("options", "install_requires", fallback="")
            result.extend(
                DeclaredDependency(name, "setup.cfg", "install_requires")
                for line in raw.splitlines()
                for name in [_dependency_name(line)]
                if name
            )
        except (configparser.Error, OSError):
            pass

    setup_py = root / "setup.py"
    if setup_py.is_file():
        try:
            tree = ast.parse(setup_py.read_text(encoding="utf-8", errors="replace"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for keyword in node.keywords:
                    if keyword.arg != "install_requires":
                        continue
                    for value in _literal_strings(keyword.value):
                        name = _dependency_name(value)
                        if name:
                            result.append(
                                DeclaredDependency(name, "setup.py", "install_requires")
                            )
        except (OSError, SyntaxError):
            pass

    requirement_paths = [root / "requirements.txt"]
    requirements_directory = root / "requirements"
    if requirements_directory.is_dir():
        requirement_paths.extend(sorted(requirements_directory.glob("*.txt")))
    for path in requirement_paths:
        if not path.is_file():
            continue
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith(("#", "-", "git+", "http")):
                    continue
                name = _dependency_name(stripped)
                if name:
                    result.append(
                        DeclaredDependency(
                            name, path.relative_to(root).as_posix(), "requirements"
                        )
                    )
        except OSError:
            pass
    return result


def _dependency_name(specification: str) -> str | None:
    match = DEPENDENCY_NAME.match(specification)
    return match.group(1) if match else None


def _literal_strings(node: ast.AST) -> list[str]:
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return []
    return [
        item.value
        for item in node.elts
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    ]


def _normalize_distribution(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _import_candidates(normalized: str) -> tuple[str, ...]:
    known = KNOWN_IMPORT_NAMES.get(normalized)
    if known:
        return known
    return (normalized.replace("-", "_"),)


def _import_roots(records: list[FileRecord]) -> set[str]:
    roots = {
        reference.module.split(".", 1)[0]
        for record in records
        for reference in record.imports
        if reference.module
    }
    roots.update(
        value.split(":", 1)[0].split(".", 1)[0]
        for record in records
        for value in record.dynamic_refs | record.runtime_string_refs
        if value
    )
    return roots
