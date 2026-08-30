from __future__ import annotations

import ast
import re
from dataclasses import dataclass

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

    def scan(self) -> RuntimeReferences:
        dynamic_calls, discovery_calls, execution_calls, reflection_owners = (
            self._known_callables()
        )
        dynamic_calls = self._propagate_assignment_aliases(dynamic_calls)
        discovery_calls = self._propagate_assignment_aliases(discovery_calls)
        execution_calls = self._propagate_assignment_aliases(execution_calls)
        modules: set[str] = set()
        possible_modules = self._module_shaped_strings()
        opaque = False
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node.func)
            if name in execution_calls:
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
    ) -> tuple[set[str], set[str], set[str], set[str]]:
        importlib_aliases = {"importlib"}
        import_module_aliases: set[str] = set()
        metadata_aliases: set[str] = set()
        entry_points_aliases: set[str] = set()
        runpy_aliases = {"runpy"}
        run_module_aliases: set[str] = set()
        builtins_aliases = {"builtins"}
        import_aliases: set[str] = set()
        eval_aliases: set[str] = set()
        exec_aliases: set[str] = set()
        pkgutil_aliases = {"pkgutil"}
        pkgutil_discovery_aliases: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "importlib":
                        importlib_aliases.add(alias.asname or alias.name)
                    elif alias.name == "importlib.metadata":
                        metadata_aliases.add(alias.asname or alias.name)
                    elif alias.name == "runpy":
                        runpy_aliases.add(alias.asname or alias.name)
                    elif alias.name == "builtins":
                        builtins_aliases.add(alias.asname or alias.name)
                    elif alias.name == "pkgutil":
                        pkgutil_aliases.add(alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module == "importlib":
                    import_module_aliases.update(
                        alias.asname or alias.name
                        for alias in node.names
                        if alias.name == "import_module"
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
            "importlib.metadata.entry_points",
            "pkg_resources.iter_entry_points",
            *(f"{alias}.entry_points" for alias in metadata_aliases),
            *(f"{alias}.iter_modules" for alias in pkgutil_aliases),
            *(f"{alias}.walk_packages" for alias in pkgutil_aliases),
        }
        execution_calls = {
            "eval",
            "exec",
            *eval_aliases,
            *exec_aliases,
            *(f"{alias}.eval" for alias in builtins_aliases),
            *(f"{alias}.exec" for alias in builtins_aliases),
        }
        reflection_owners = {*importlib_aliases, *builtins_aliases}
        return dynamic_calls, discovery_calls, execution_calls, reflection_owners

    def _propagate_assignment_aliases(self, known: set[str]) -> set[str]:
        assignments: list[tuple[list[str], str]] = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign):
                targets = [
                    target.id for target in node.targets if isinstance(target, ast.Name)
                ]
                assignments.append((targets, _call_name(node.value)))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                assignments.append(
                    ([node.target.id], _call_name(node.value) if node.value else "")
                )
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

    def _module_shaped_strings(self) -> set[str]:
        modules: set[str] = set()
        for node in ast.walk(self.tree):
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


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _is_loader_reflection(node: ast.Call, owners: set[str]) -> bool:
    if len(node.args) < 2 or _call_name(node.args[0]) not in owners:
        return False
    attribute = node.args[1]
    if not isinstance(attribute, ast.Constant) or not isinstance(attribute.value, str):
        return True
    return attribute.value in {"import_module", "__import__"}
