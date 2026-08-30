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
                target = self._resolve_one(dynamic.split(":", 1)[0])
                if target:
                    self.edges[module].add(target)
                    self.inbound[target].add(module)
        for configured in configured_roots:
            resolved = self._resolve_one(configured)
            if resolved:
                self.roots.add(resolved)
        self.reachable = self._reachable_from_roots()

    @staticmethod
    def _aliases(records: list[FileRecord]) -> dict[str, str]:
        aliases: dict[str, str] = {}
        ambiguous: set[str] = set()
        for record in records:
            for alias in (record.module, *record.module_aliases):
                if not alias:
                    continue
                previous = aliases.get(alias)
                if previous is not None and previous != record.module:
                    ambiguous.add(alias)
                else:
                    aliases[alias] = record.module
        for alias in ambiguous:
            aliases.pop(alias, None)
        return aliases

    def _resolve_targets(self, module: str, names: tuple[str, ...]) -> set[str]:
        targets: set[str] = set()
        direct = self._resolve_one(module)
        if direct:
            targets.add(direct)
        for name in names:
            child = self._resolve_one(f"{module}.{name}" if module else name)
            if child:
                targets.add(child)
        return targets

    def _resolve_one(self, imported: str) -> str | None:
        if imported in self.aliases:
            return self.aliases[imported]
        parts = imported.split(".")
        for end in range(len(parts) - 1, 0, -1):
            candidate = ".".join(parts[:end])
            if candidate in self.aliases:
                return self.aliases[candidate]
        return None

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
