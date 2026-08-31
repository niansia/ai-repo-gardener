from __future__ import annotations

import ast

import pytest
from repo_gardener import runtime_references
from repo_gardener.runtime_references import RuntimeReferenceScanner


def test_assignment_alias_extraction_is_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = ast.parse(
        "import importlib\nil = importlib\nil2 = il\nloader = il2.import_module\n"
    )
    scanner = RuntimeReferenceScanner(tree)
    original_walk = ast.walk
    walk_count = 0

    def counted_walk(node: ast.AST):
        nonlocal walk_count
        walk_count += 1
        return original_walk(node)

    monkeypatch.setattr(runtime_references.ast, "walk", counted_walk)

    first = scanner._propagate_assignment_aliases({"importlib"})
    for _ in range(11):
        assert scanner._propagate_assignment_aliases({"importlib"}) == first

    assert {"importlib", "il", "il2"} <= first
    assert walk_count == 1
