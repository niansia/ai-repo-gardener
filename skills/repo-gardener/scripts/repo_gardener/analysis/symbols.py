from __future__ import annotations

import ast
from collections import defaultdict

from ..ast_utils import dotted_name
from ..graph import ModuleGraph
from ..models import FileRecord, Finding, SymbolRecord


def symbol_gc_findings(
    records: list[FileRecord],
    graph: ModuleGraph,
    changed: set[str],
) -> list[Finding]:
    """Return conservative, review-only symbol-level GC findings."""
    references, wildcard_modules = _reference_index(records, graph)
    duplicates = _duplicate_implementations(records, graph, changed, references)
    duplicate_symbols = {
        (
            finding.path,
            next(
                item["value"]
                for item in finding.evidence
                if item["type"] == "duplicate_symbol"
            ),
        )
        for finding in duplicates
    }
    orphans = _orphan_helpers(records, graph, changed, references, wildcard_modules)
    orphans = [
        finding
        for finding in orphans
        if (
            finding.path,
            next(
                item["value"] for item in finding.evidence if item["type"] == "symbol"
            ),
        )
        not in duplicate_symbols
    ]
    return [*orphans, *duplicates]


def _orphan_helpers(
    records: list[FileRecord],
    graph: ModuleGraph,
    changed: set[str],
    references: dict[tuple[str, str], set[str]],
    wildcard_modules: set[str],
) -> list[Finding]:
    findings: list[Finding] = []
    for record in records:
        if (
            record.category != "source"
            or record.parse_error
            or not graph.is_reachable(record.module)
        ):
            continue
        for symbol in record.symbol_details:
            users = references.get((record.module, symbol.name), set())
            if (
                users
                or record.module in wildcard_modules
                or symbol.decorated
                or symbol.name in record.exported_symbols
                or symbol.name.startswith("__")
            ):
                continue
            changed_in_iteration = record.relative_path in changed
            if not changed_in_iteration and not symbol.private:
                continue
            confidence = (
                0.78
                if changed_in_iteration and symbol.private
                else 0.68
                if symbol.private
                else 0.62
            )
            risk = 0.42 if symbol.private else 0.78
            risks = ["dynamic_or_external_symbol_use_may_be_unknown"]
            if not symbol.private:
                risks.append("public_symbol_may_be_external_api")
            findings.append(
                Finding(
                    rule="orphan-helper",
                    category="repo-gc",
                    severity="info",
                    confidence=confidence,
                    risk=risk,
                    path=record.relative_path,
                    evidence=[
                        {"type": "symbol", "value": symbol.name},
                        {"type": "symbol_kind", "value": symbol.kind},
                        {"type": "definition_line", "value": symbol.lineno},
                        {"type": "static_reference_files", "value": []},
                        {
                            "type": "changed_in_iteration",
                            "value": changed_in_iteration,
                        },
                        {"type": "private_symbol", "value": symbol.private},
                    ],
                    risks=risks,
                    recommendation="review_only",
                ).finalize()
            )
    return findings


def _duplicate_implementations(
    records: list[FileRecord],
    graph: ModuleGraph,
    changed: set[str],
    references: dict[tuple[str, str], set[str]],
) -> list[Finding]:
    groups: dict[tuple[str, str, int], list[tuple[FileRecord, SymbolRecord]]] = (
        defaultdict(list)
    )
    for record in records:
        if record.category != "source" or record.parse_error:
            continue
        for symbol in record.symbol_details:
            minimum_nodes = 12 if symbol.kind == "function" else 20
            minimum_lines = 4 if symbol.kind == "function" else 6
            if (
                symbol.decorated
                or symbol.name.startswith("__")
                or symbol.body_nodes < minimum_nodes
                or symbol.end_lineno - symbol.lineno + 1 < minimum_lines
            ):
                continue
            groups[
                (symbol.kind, symbol.normalized_body_hash, symbol.parameter_count)
            ].append((record, symbol))

    findings: list[Finding] = []
    for (_, fingerprint, _), members in sorted(groups.items()):
        distinct_files = {record.relative_path for record, _ in members}
        if len(distinct_files) < 2:
            continue
        ordered = sorted(
            members,
            key=lambda item: (
                -len(references.get((item[0].module, item[1].name), set())),
                not graph.is_reachable(item[0].module),
                item[0].relative_path in changed,
                item[0].relative_path,
                item[1].lineno,
            ),
        )
        canonical_record, canonical_symbol = ordered[0]
        for record, symbol in ordered[1:]:
            same_name = symbol.name.lstrip("_") == canonical_symbol.name.lstrip("_")
            shortest_lines = min(
                symbol.end_lineno - symbol.lineno + 1,
                canonical_symbol.end_lineno - canonical_symbol.lineno + 1,
            )
            if not same_name and (
                shortest_lines < 8
                or min(symbol.body_nodes, canonical_symbol.body_nodes) < 30
            ):
                continue
            related_changed = sorted(
                path
                for path in {record.relative_path, canonical_record.relative_path}
                if path in changed
            )
            if changed and not related_changed:
                continue
            public_surface = not symbol.private or not canonical_symbol.private
            findings.append(
                Finding(
                    rule="duplicate-implementation",
                    category="repo-gc",
                    severity="warning",
                    confidence=0.88,
                    risk=0.78 if public_surface else 0.55,
                    path=record.relative_path,
                    replacement=canonical_record.relative_path,
                    evidence=[
                        {"type": "duplicate_symbol", "value": symbol.name},
                        {
                            "type": "canonical_symbol",
                            "value": canonical_symbol.name,
                        },
                        {
                            "type": "normalized_ast_sha256",
                            "value": fingerprint,
                        },
                        {
                            "type": "definition_lines",
                            "value": [symbol.lineno, symbol.end_lineno],
                        },
                        {
                            "type": "related_changed_paths",
                            "value": related_changed,
                        },
                        {"type": "exact_normalized_ast_match", "value": True},
                        {"type": "same_symbol_name", "value": same_name},
                    ],
                    risks=[
                        "equivalent_code_can_serve_different_domain_contracts",
                        "public_callers_may_exist_outside_repository",
                    ],
                    recommendation="consolidation_review",
                ).finalize()
            )
    return findings


def _reference_index(
    records: list[FileRecord], graph: ModuleGraph
) -> tuple[dict[tuple[str, str], set[str]], set[str]]:
    references: dict[tuple[str, str], set[str]] = defaultdict(set)
    wildcard_modules: set[str] = set()
    known_symbols = {
        record.module: {symbol.name for symbol in record.symbol_details}
        for record in records
    }
    for record in records:
        if record.parse_error:
            continue
        tree = record.tree
        if tree is None:
            continue
        for symbol in _local_symbol_references(
            tree, known_symbols.get(record.module, set())
        ):
            references[(record.module, symbol)].add(record.relative_path)

        for imported in record.imports:
            target = _canonical_module(imported.module, graph)
            if not target:
                continue
            for name in imported.names:
                if name == "*":
                    wildcard_modules.add(target)
                elif name in known_symbols.get(target, set()):
                    references[(target, name)].add(record.relative_path)

        bindings = _module_bindings(tree, graph)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            dotted = dotted_name(node)
            for prefix, target in bindings.items():
                if not dotted.startswith(prefix + "."):
                    continue
                remainder = dotted[len(prefix) + 1 :]
                symbol = remainder.split(".", 1)[0]
                if symbol in known_symbols.get(target, set()):
                    references[(target, symbol)].add(record.relative_path)
    return references, wildcard_modules


def _local_symbol_references(tree: ast.Module, symbols: set[str]) -> set[str]:
    result: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            loaded = {
                child.id
                for child in ast.walk(node)
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
            }
            loaded.discard(node.name)
            result.update(loaded & symbols)
        else:
            result.update(
                child.id
                for child in ast.walk(node)
                if isinstance(child, ast.Name)
                and isinstance(child.ctx, ast.Load)
                and child.id in symbols
            )
    return result


def _module_bindings(tree: ast.Module, graph: ModuleGraph) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Import):
            continue
        for alias in node.names:
            target = _canonical_module(alias.name, graph)
            if target:
                bindings[alias.asname or alias.name] = target
    return bindings


def _canonical_module(module: str, graph: ModuleGraph) -> str | None:
    if module in graph.aliases:
        return graph.aliases[module]
    parts = module.split(".")
    for end in range(len(parts) - 1, 0, -1):
        candidate = ".".join(parts[:end])
        if candidate in graph.aliases:
            return graph.aliases[candidate]
    return None
