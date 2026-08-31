from __future__ import annotations

import ast


def dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def same_scope_nodes(body: list[ast.stmt]):
    """Walk statements without crossing a nested function, class, or lambda."""

    def visit(node: ast.AST):
        yield node
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
        ):
            return
        for child in ast.iter_child_nodes(node):
            yield from visit(child)

    for statement in body:
        yield from visit(statement)
