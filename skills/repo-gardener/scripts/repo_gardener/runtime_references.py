from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from .ast_utils import dotted_name

MODULE_REFERENCE = re.compile(
    r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?::[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)?"
)


@dataclass(frozen=True)
class RuntimeReferences:
    modules: frozenset[str]
    possible_modules: frozenset[str]
    opaque_discovery: bool


class RuntimeReferenceScanner:
    """Conservatively find runtime module references without executing code."""

    def __init__(self, tree: ast.Module):
        self.tree = tree
        self._nodes = tuple(ast.walk(tree))
        self._assignment_cache: list[tuple[list[str], str]] | None = None

    def scan(self) -> RuntimeReferences:
        (
            dynamic_calls,
            discovery_calls,
            execution_calls,
            file_loader_calls,
            reflection_owners,
        ) = self._known_callables()
        dynamic_calls = self._propagate_assignment_aliases(dynamic_calls)
        discovery_calls = self._propagate_assignment_aliases(discovery_calls)
        execution_calls = self._propagate_assignment_aliases(execution_calls)
        file_loader_calls = self._propagate_assignment_aliases(file_loader_calls)
        modules: set[str] = set()
        possible_modules = self._module_shaped_strings()
        dangerous_callables = (
            dynamic_calls | discovery_calls | execution_calls | file_loader_calls
        )
        opaque = self._dangerous_callable_escapes(dangerous_callables)
        for node in self._nodes:
            if not isinstance(node, ast.Call):
                continue
            name = dotted_name(node.func)
            if name in execution_calls or name in file_loader_calls:
                opaque = True
                continue
            if name == "getattr" and _is_loader_reflection(node, reflection_owners):
                opaque = True
                continue
            if name in discovery_calls or name.endswith(
                (
                    ".entry_points",
                    ".iter_entry_points",
                    ".iter_modules",
                    ".walk_packages",
                    ".run_path",
                    ".spec_from_file_location",
                    ".SourceFileLoader",
                    ".SourcelessFileLoader",
                    ".exec_module",
                )
            ):
                opaque = True
                continue
            if name in dynamic_calls:
                if not node.args:
                    opaque = True
                    continue
                reference = _module_reference(node.args[0])
                if reference:
                    modules.add(reference)
                else:
                    opaque = True
                continue
            if name in {"patch", "mock.patch", "monkeypatch.setattr"} and node.args:
                reference = _module_reference(node.args[0])
                if reference:
                    modules.add(reference)
        return RuntimeReferences(
            frozenset(modules),
            frozenset(possible_modules - modules),
            opaque,
        )

    def _known_callables(
        self,
    ) -> tuple[set[str], set[str], set[str], set[str], set[str]]:
        importlib_aliases = {"importlib"}
        util_aliases = {"importlib.util"}
        machinery_aliases = {"importlib.machinery"}
        import_module_aliases: set[str] = set()
        spec_from_file_aliases: set[str] = set()
        file_loader_aliases: set[str] = set()
        metadata_aliases = {"importlib.metadata"}
        entry_points_aliases: set[str] = set()
        runpy_aliases = {"runpy"}
        run_module_aliases: set[str] = set()
        run_path_aliases: set[str] = set()
        builtins_aliases = {"builtins"}
        import_aliases: set[str] = set()
        eval_aliases: set[str] = set()
        exec_aliases: set[str] = set()
        pkgutil_aliases = {"pkgutil"}
        pkgutil_discovery_aliases: set[str] = set()
        pkg_resources_aliases = {"pkg_resources"}
        pkg_resources_discovery_aliases: set[str] = set()
        for node in self._nodes:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "importlib":
                        importlib_aliases.add(alias.asname or alias.name)
                    elif alias.name == "importlib.metadata":
                        if alias.asname:
                            metadata_aliases.add(alias.asname)
                    elif alias.name == "importlib.util":
                        if alias.asname:
                            util_aliases.add(alias.asname)
                    elif alias.name == "importlib.machinery":
                        if alias.asname:
                            machinery_aliases.add(alias.asname)
                    elif alias.name == "runpy":
                        runpy_aliases.add(alias.asname or alias.name)
                    elif alias.name == "builtins":
                        builtins_aliases.add(alias.asname or alias.name)
                    elif alias.name == "pkgutil":
                        pkgutil_aliases.add(alias.asname or alias.name)
                    elif alias.name == "pkg_resources":
                        pkg_resources_aliases.add(alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module == "importlib":
                    import_module_aliases.update(
                        alias.asname or alias.name
                        for alias in node.names
                        if alias.name == "import_module"
                    )
                    util_aliases.update(
                        alias.asname or alias.name
                        for alias in node.names
                        if alias.name == "util"
                    )
                    machinery_aliases.update(
                        alias.asname or alias.name
                        for alias in node.names
                        if alias.name == "machinery"
                    )
                    metadata_aliases.update(
                        alias.asname or alias.name
                        for alias in node.names
                        if alias.name == "metadata"
                    )
                elif node.module == "importlib.util":
                    spec_from_file_aliases.update(
                        alias.asname or alias.name
                        for alias in node.names
                        if alias.name == "spec_from_file_location"
                    )
                elif node.module == "importlib.machinery":
                    file_loader_aliases.update(
                        alias.asname or alias.name
                        for alias in node.names
                        if alias.name in {"SourceFileLoader", "SourcelessFileLoader"}
                    )
                elif node.module == "importlib.metadata":
                    entry_points_aliases.update(
                        alias.asname or alias.name
                        for alias in node.names
                        if alias.name == "entry_points"
                    )
                elif node.module == "runpy":
                    run_module_aliases.update(
                        alias.asname or alias.name
                        for alias in node.names
                        if alias.name == "run_module"
                    )
                    run_path_aliases.update(
                        alias.asname or alias.name
                        for alias in node.names
                        if alias.name == "run_path"
                    )
                elif node.module == "builtins":
                    import_aliases.update(
                        alias.asname or alias.name
                        for alias in node.names
                        if alias.name == "__import__"
                    )
                    eval_aliases.update(
                        alias.asname or alias.name
                        for alias in node.names
                        if alias.name == "eval"
                    )
                    exec_aliases.update(
                        alias.asname or alias.name
                        for alias in node.names
                        if alias.name == "exec"
                    )
                elif node.module == "pkgutil":
                    pkgutil_discovery_aliases.update(
                        alias.asname or alias.name
                        for alias in node.names
                        if alias.name in {"iter_modules", "walk_packages"}
                    )
                elif node.module == "pkg_resources":
                    pkg_resources_discovery_aliases.update(
                        alias.asname or alias.name
                        for alias in node.names
                        if alias.name == "iter_entry_points"
                    )
        importlib_aliases = self._propagate_assignment_aliases(importlib_aliases)
        runpy_aliases = self._propagate_assignment_aliases(runpy_aliases)
        builtins_aliases = self._propagate_assignment_aliases(builtins_aliases)
        pkgutil_aliases = self._propagate_assignment_aliases(pkgutil_aliases)
        pkg_resources_aliases = self._propagate_assignment_aliases(
            pkg_resources_aliases
        )
        util_aliases.update(f"{alias}.util" for alias in importlib_aliases)
        machinery_aliases.update(f"{alias}.machinery" for alias in importlib_aliases)
        metadata_aliases.update(f"{alias}.metadata" for alias in importlib_aliases)
        util_aliases = self._propagate_assignment_aliases(util_aliases)
        machinery_aliases = self._propagate_assignment_aliases(machinery_aliases)
        metadata_aliases = self._propagate_assignment_aliases(metadata_aliases)
        dynamic_calls = {
            "__import__",
            *import_aliases,
            *import_module_aliases,
            *run_module_aliases,
            *(f"{alias}.import_module" for alias in importlib_aliases),
            *(f"{alias}.run_module" for alias in runpy_aliases),
            *(f"{alias}.__import__" for alias in builtins_aliases),
        }
        discovery_calls = {
            *entry_points_aliases,
            *pkgutil_discovery_aliases,
            *pkg_resources_discovery_aliases,
            "importlib.metadata.entry_points",
            "pkg_resources.iter_entry_points",
            *(f"{alias}.entry_points" for alias in metadata_aliases),
            *(f"{alias}.iter_modules" for alias in pkgutil_aliases),
            *(f"{alias}.walk_packages" for alias in pkgutil_aliases),
            *(f"{alias}.iter_entry_points" for alias in pkg_resources_aliases),
        }
        execution_calls = {
            "eval",
            "exec",
            *eval_aliases,
            *exec_aliases,
            *(f"{alias}.eval" for alias in builtins_aliases),
            *(f"{alias}.exec" for alias in builtins_aliases),
        }
        file_loader_calls = {
            *run_path_aliases,
            *spec_from_file_aliases,
            *file_loader_aliases,
            *(f"{alias}.run_path" for alias in runpy_aliases),
            *(f"{alias}.spec_from_file_location" for alias in util_aliases),
            *(f"{alias}.SourceFileLoader" for alias in machinery_aliases),
            *(f"{alias}.SourcelessFileLoader" for alias in machinery_aliases),
        }
        reflection_owners = {
            *importlib_aliases,
            *util_aliases,
            *machinery_aliases,
            *metadata_aliases,
            *runpy_aliases,
            *builtins_aliases,
            *pkgutil_aliases,
            *pkg_resources_aliases,
        }
        return (
            dynamic_calls,
            discovery_calls,
            execution_calls,
            file_loader_calls,
            reflection_owners,
        )

    def _dangerous_callable_escapes(self, known: set[str]) -> bool:
        for node in self._nodes:
            if isinstance(node, ast.Assign):
                if _contains_callable_value(node.value, known) and not (
                    dotted_name(node.value) in known
                    and all(isinstance(target, ast.Name) for target in node.targets)
                ):
                    return True
            elif isinstance(node, ast.AnnAssign):
                if (
                    node.value is not None
                    and _contains_callable_value(node.value, known)
                    and not (
                        dotted_name(node.value) in known
                        and isinstance(node.target, ast.Name)
                    )
                ):
                    return True
            elif isinstance(node, (ast.Return, ast.Yield, ast.YieldFrom)):
                if node.value is not None and _contains_callable_value(
                    node.value, known
                ):
                    return True
            elif isinstance(node, ast.NamedExpr):
                if _contains_callable_value(node.value, known):
                    return True
            elif isinstance(node, ast.Call):
                values = (*node.args, *(item.value for item in node.keywords))
                if any(_contains_callable_value(value, known) for value in values):
                    return True
            elif isinstance(node, ast.arguments):
                defaults = (*node.defaults, *node.kw_defaults)
                if any(
                    value is not None and _contains_callable_value(value, known)
                    for value in defaults
                ):
                    return True
        return False

    def _propagate_assignment_aliases(self, known: set[str]) -> set[str]:
        assignments = self._assignment_pairs()
        expanded = set(known)
        changed = True
        while changed:
            changed = False
            for targets, source in assignments:
                if source not in expanded:
                    continue
                for target in targets:
                    if target not in expanded:
                        expanded.add(target)
                        changed = True
        return expanded

    def _assignment_pairs(self) -> list[tuple[list[str], str]]:
        if self._assignment_cache is not None:
            return self._assignment_cache
        assignments: list[tuple[list[str], str]] = []
        for node in self._nodes:
            if isinstance(node, ast.Assign):
                targets = [
                    target.id for target in node.targets if isinstance(target, ast.Name)
                ]
                assignments.append((targets, dotted_name(node.value)))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                assignments.append(
                    ([node.target.id], dotted_name(node.value) if node.value else "")
                )
        self._assignment_cache = assignments
        return assignments

    def _module_shaped_strings(self) -> set[str]:
        modules: set[str] = set()
        for node in self._nodes:
            reference = _module_reference(node)
            if reference:
                modules.add(reference)
        return modules


def _module_reference(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        return None
    value = node.value.strip()
    if not MODULE_REFERENCE.fullmatch(value):
        return None
    return value.split(":", 1)[0]


def _contains_callable_value(node: ast.AST, known: set[str]) -> bool:
    """Return whether a value stores or transports a known dangerous callable."""
    if dotted_name(node) in known:
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return any(_contains_callable_value(item, known) for item in node.elts)
    if isinstance(node, ast.Dict):
        return any(
            item is not None and _contains_callable_value(item, known)
            for item in (*node.keys, *node.values)
        )
    if isinstance(node, ast.Starred):
        return _contains_callable_value(node.value, known)
    if isinstance(node, ast.IfExp):
        return _contains_callable_value(node.body, known) or _contains_callable_value(
            node.orelse, known
        )
    return False


def _is_loader_reflection(node: ast.Call, owners: set[str]) -> bool:
    if len(node.args) < 2 or dotted_name(node.args[0]) not in owners:
        return False
    attribute = node.args[1]
    if not isinstance(attribute, ast.Constant) or not isinstance(attribute.value, str):
        return True
    return attribute.value in {
        "SourceFileLoader",
        "SourcelessFileLoader",
        "__import__",
        "entry_points",
        "exec_module",
        "import_module",
        "iter_entry_points",
        "iter_modules",
        "run_module",
        "run_path",
        "spec_from_file_location",
        "walk_packages",
    }
