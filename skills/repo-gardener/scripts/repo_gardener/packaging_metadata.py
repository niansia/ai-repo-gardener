from __future__ import annotations

import ast
import configparser
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .ast_utils import dotted_name
from .config import Config

MODULE_NAME = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")


@dataclass(frozen=True)
class PackagingMetadata:
    entrypoint_modules: frozenset[str]
    public_modules: frozenset[str]
    uncertainty_sources: tuple[str, ...]
    namespace_roots: tuple[str, ...]

    def is_namespace_module(self, root: Path, relative_path: str) -> bool:
        parts = PurePosixPath(relative_path).parts
        for declared_root in self.namespace_roots:
            root_parts = PurePosixPath(declared_root).parts if declared_root else ()
            if root_parts and parts[: len(root_parts)] != root_parts:
                continue
            remainder = parts[len(root_parts) :]
            if len(remainder) >= 2:
                return True
        parent_parts = parts[:-1]
        if parent_parts and parent_parts[0] in {"src", "lib"}:
            parent_parts = parent_parts[1:]
            source_root = root / parts[0]
        else:
            source_root = root
        if not parent_parts or not all(part.isidentifier() for part in parent_parts):
            return False
        current = source_root
        for part in parent_parts:
            current /= part
            if not (current / "__init__.py").is_file():
                return True
        return False


def discover_packaging_metadata(root: Path, config: Config) -> PackagingMetadata:
    modules = {
        value.split(":", 1)[0].strip()
        for value in config.entrypoint_modules
        if value.strip()
    }
    uncertainty: set[str] = set()
    namespace_roots: set[str] = set()
    public_modules: set[str] = set()
    _read_pyproject(
        root / "pyproject.toml",
        modules,
        public_modules,
        uncertainty,
        namespace_roots,
    )
    _read_setup_cfg(root / "setup.cfg", modules, public_modules, uncertainty)
    _read_setup_py(root / "setup.py", modules, public_modules, uncertainty)
    return PackagingMetadata(
        entrypoint_modules=frozenset(modules),
        public_modules=frozenset(public_modules),
        uncertainty_sources=tuple(sorted(uncertainty)),
        namespace_roots=tuple(sorted(namespace_roots)),
    )


def _read_pyproject(
    path: Path,
    modules: set[str],
    public_modules: set[str],
    uncertainty: set[str],
    namespace_roots: set[str],
) -> None:
    if not path.is_file():
        return
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        uncertainty.add("pyproject.toml:unreadable")
        return
    project = raw.get("project", {})
    if isinstance(project, dict):
        entry_points = project.get("entry-points", {})
        tables = [
            project.get("scripts", {}),
            project.get("gui-scripts", {}),
            *(entry_points.values() if isinstance(entry_points, dict) else ()),
        ]
        for table in tables:
            if not isinstance(table, dict):
                uncertainty.add("pyproject.toml:nonliteral-entry-points")
                continue
            for value in table.values():
                if not isinstance(value, str) or not _add_object_reference(
                    value, modules
                ):
                    uncertainty.add("pyproject.toml:nonliteral-entry-point")
    tool = raw.get("tool", {})
    setuptools = tool.get("setuptools", {}) if isinstance(tool, dict) else {}
    if (
        isinstance(setuptools, dict)
        and "py-modules" in setuptools
        and not _collect_public_modules(setuptools["py-modules"], public_modules)
    ):
        uncertainty.add("pyproject.toml:invalid-py-modules")
    packages = setuptools.get("packages", {}) if isinstance(setuptools, dict) else {}
    find = packages.get("find", {}) if isinstance(packages, dict) else {}
    if isinstance(find, dict) and find.get("namespaces") is True:
        where = find.get("where", ["."])
        values = [where] if isinstance(where, str) else where
        if isinstance(values, list):
            for value in values:
                if isinstance(value, str):
                    namespace_roots.add(_normalize_root(value))
                else:
                    uncertainty.add("pyproject.toml:namespace-root")
        else:
            uncertainty.add("pyproject.toml:namespace-root")


def _read_setup_cfg(
    path: Path,
    modules: set[str],
    public_modules: set[str],
    uncertainty: set[str],
) -> None:
    if not path.is_file():
        return
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            parser.read_file(handle)
    except (OSError, configparser.Error):
        uncertainty.add("setup.cfg:unreadable")
        return
    if parser.has_section("options") and "py_modules" in parser["options"]:
        values = [
            item.strip()
            for line in parser["options"]["py_modules"].splitlines()
            for item in line.split(",")
            if item.strip() and not item.lstrip().startswith(("#", ";"))
        ]
        if not _collect_public_modules(values, public_modules):
            uncertainty.add("setup.cfg:invalid-py-modules")
    if parser.has_section("options.entry_points"):
        for value in parser["options.entry_points"].values():
            for line in value.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith(("#", ";")):
                    continue
                target = (
                    stripped.split("=", 1)[1].strip() if "=" in stripped else stripped
                )
                if not _add_object_reference(target, modules):
                    uncertainty.add("setup.cfg:nonliteral-entry-point")


def _read_setup_py(
    path: Path,
    modules: set[str],
    public_modules: set[str],
    uncertainty: set[str],
) -> None:
    if not path.is_file():
        return
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        uncertainty.add("setup.py:unreadable")
        return
    assignments: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments[target.id] = node.value
    setup_callables = _setup_callables(tree)
    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.Call)
            or dotted_name(node.func) not in setup_callables
        ):
            continue
        has_entry_points = False
        has_py_modules = False
        for keyword in node.keywords:
            if keyword.arg == "entry_points":
                has_entry_points = True
                value = (
                    assignments.get(keyword.value.id)
                    if isinstance(keyword.value, ast.Name)
                    else keyword.value
                )
                try:
                    literal = ast.literal_eval(value) if value is not None else None
                except (
                    ValueError,
                    TypeError,
                    SyntaxError,
                    MemoryError,
                    RecursionError,
                ):
                    uncertainty.add("setup.py:nonliteral-entry-points")
                    continue
                if not _collect_entrypoint_value(literal, modules):
                    uncertainty.add("setup.py:nonliteral-entry-points")
            elif keyword.arg == "py_modules":
                has_py_modules = True
                value = (
                    assignments.get(keyword.value.id)
                    if isinstance(keyword.value, ast.Name)
                    else keyword.value
                )
                try:
                    literal = ast.literal_eval(value) if value is not None else None
                except (
                    ValueError,
                    TypeError,
                    SyntaxError,
                    MemoryError,
                    RecursionError,
                ):
                    uncertainty.add("setup.py:nonliteral-py-modules")
                    continue
                if not _collect_public_modules(literal, public_modules):
                    uncertainty.add("setup.py:nonliteral-py-modules")
            elif keyword.arg is None:
                uncertainty.add("setup.py:expanded-keyword-metadata")
        if not has_entry_points and any(
            keyword.arg is None for keyword in node.keywords
        ):
            uncertainty.add("setup.py:entry-points-may-be-dynamic")
        if not has_py_modules and any(keyword.arg is None for keyword in node.keywords):
            uncertainty.add("setup.py:py-modules-may-be-dynamic")


def _collect_entrypoint_value(value: Any, modules: set[str]) -> bool:
    if isinstance(value, str):
        target = value.split("=", 1)[1].strip() if "=" in value else value.strip()
        return _add_object_reference(target, modules)
    if isinstance(value, dict):
        return all(_collect_entrypoint_value(item, modules) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return all(_collect_entrypoint_value(item, modules) for item in value)
    return False


def _collect_public_modules(value: Any, modules: set[str]) -> bool:
    if not isinstance(value, (list, tuple, set)):
        return False
    if not all(isinstance(item, str) and MODULE_NAME.fullmatch(item) for item in value):
        return False
    modules.update(value)
    return True


def _add_object_reference(value: str, modules: set[str]) -> bool:
    target = value.split("[", 1)[0].strip()
    module = target.split(":", 1)[0].strip()
    if not MODULE_NAME.fullmatch(module):
        return False
    modules.add(module)
    return True


def _setup_callables(tree: ast.Module) -> set[str]:
    callables = {"setup", "setuptools.setup", "distutils.core.setup"}
    assignments: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"setuptools", "distutils.core"}:
                    callables.add(f"{alias.asname or alias.name}.setup")
        elif isinstance(node, ast.ImportFrom) and node.module in {
            "setuptools",
            "distutils.core",
        }:
            callables.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "setup"
            )
        elif isinstance(node, ast.Assign) and isinstance(
            node.value, (ast.Name, ast.Attribute)
        ):
            source = dotted_name(node.value)
            assignments.extend(
                (target.id, source)
                for target in node.targets
                if isinstance(target, ast.Name)
            )
    changed = True
    while changed:
        changed = False
        for target, source in assignments:
            if source in callables and target not in callables:
                callables.add(target)
                changed = True
    return callables


def _normalize_root(value: str) -> str:
    normalized = value.replace("\\", "/").strip().strip("/")
    return "" if normalized in {"", "."} else normalized
