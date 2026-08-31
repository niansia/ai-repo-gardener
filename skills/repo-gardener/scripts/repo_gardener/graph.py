from __future__ import annotations

from collections import deque

from .models import FileRecord


class ModuleGraph:
    def __init__(self, records: list[FileRecord], configured_roots: set[str]):
        self.records = {record.module: record for record in records if record.module}
        self.aliases = self._aliases(records)
        self.edges: dict[str, set[str]] = {module: set() for module in self.records}
        self.inbound: dict[str, set[str]] = {module: set() for module in self.records}
        self.roots: set[str] = set()
        for module, record in self.records.items():
            if (
                record.has_main_guard
                or record.framework_entrypoints
                or record.category in {"test", "script"}
                or record.path.name == "__init__.py"
                or record.relative_path.endswith("/__main__.py")
                or record.relative_path == "__main__.py"
            ):
                self.roots.add(module)
            for imported in record.imports:
                for target in self._resolve_targets(imported.module, imported.names):
                    self.edges[module].add(target)
                    self.inbound[target].add(module)
            for dynamic in record.dynamic_refs:
                for target in self.resolve_modules(dynamic.split(":", 1)[0]):
                    self.edges[module].add(target)
                    self.inbound[target].add(module)
        for configured in configured_roots:
            self.roots.update(self.resolve_modules(configured))
        self.reachable = self._reachable_from_roots()

    @staticmethod
    def _aliases(records: list[FileRecord]) -> dict[str, set[str]]:
        aliases: dict[str, set[str]] = {}
        for record in records:
            for alias in (record.module, *record.module_aliases):
                if not alias:
                    continue
                aliases.setdefault(alias, set()).add(record.module)
        return aliases

    def _resolve_targets(self, module: str, names: tuple[str, ...]) -> set[str]:
        targets: set[str] = set()
        targets.update(self.resolve_modules(module))
        for name in names:
            targets.update(self.resolve_modules(f"{module}.{name}" if module else name))
        return targets

    def resolve_modules(self, imported: str) -> set[str]:
        """Resolve every plausible local owner for an import spelling.

        Import roots can legitimately create the same spelling (for example
        ``foo.py`` and ``src/foo.py``).  Dropping the ambiguous alias would
        make both modules appear unused, which is unsafe for garbage collection.
        """
        if imported in self.aliases:
            return set(self.aliases[imported])
        parts = imported.split(".")
        for end in range(len(parts) - 1, 0, -1):
            candidate = ".".join(parts[:end])
            if candidate in self.aliases:
                return set(self.aliases[candidate])
        return set()

    def _reachable_from_roots(self) -> set[str]:
        reached: set[str] = set()
        queue = deque(sorted(self.roots))
        while queue:
            module = queue.popleft()
            if module in reached:
                continue
            reached.add(module)
            queue.extend(sorted(self.edges.get(module, set()) - reached))
        return reached

    def is_reachable(self, module: str) -> bool:
        return module in self.reachable

    def inbound_count(self, module: str) -> int:
        return len(self.inbound.get(module, set()))
