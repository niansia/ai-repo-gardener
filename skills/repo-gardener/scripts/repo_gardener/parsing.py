from __future__ import annotations

import ast
import io
import keyword
import re
import statistics
import tokenize
from collections import Counter
from hashlib import sha256
from pathlib import Path

from .ast_utils import dotted_name
from .models import FileRecord, ImportRef, StyleMetrics, SymbolRecord
from .runtime_references import RuntimeReferenceScanner

NARRATION_PREFIXES = (
    "first",
    "then",
    "next",
    "finally",
    "now",
    "step ",
    "initialize",
    "create the",
    "get the",
    "set the",
    "loop through",
    "check if",
    "return the",
)
TEMPORARY_NAMES = {
    "data",
    "result",
    "value",
    "item",
    "temp",
    "tmp",
    "response",
    "output",
    "final_result",
}
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]+")
SNAKE_CASE_RE = re.compile(r"^_?[a-z][a-z0-9_]*$")


def parse_file(
    path: Path,
    relative_path: str,
    module: str,
    category: str,
    module_aliases: tuple[str, ...] = (),
    collect_style: bool = False,
) -> FileRecord:
    source = path.read_text(encoding="utf-8", errors="replace")
    return parse_source(
        source,
        path,
        relative_path,
        module,
        category,
        module_aliases,
        path.stat().st_mtime,
        collect_style,
    )


def parse_source(
    source: str,
    path: Path,
    relative_path: str,
    module: str,
    category: str,
    module_aliases: tuple[str, ...] = (),
    mtime: float = 0.0,
    collect_style: bool = False,
) -> FileRecord:
    record = FileRecord(
        path=path,
        relative_path=relative_path,
        module=module,
        category=category,
        source=source,
        module_aliases=module_aliases,
        mtime=mtime,
    )
    try:
        tree = ast.parse(source, filename=relative_path)
    except SyntaxError as exc:
        record.parse_error = f"{exc.msg} at line {exc.lineno}"
        record.style.loc = len(source.splitlines())
        return record

    record.tree = tree
    record.imports = _imports(tree, module, path.name == "__init__.py")
    record.symbol_details = _symbol_details(tree)
    record.symbols = {symbol.name for symbol in record.symbol_details}
    record.exported_symbols = _public_exports(tree)
    runtime_references = RuntimeReferenceScanner(tree).scan()
    record.dynamic_refs = set(runtime_references.modules)
    record.runtime_string_refs = set(runtime_references.possible_modules)
    record.opaque_dynamic_discovery = runtime_references.opaque_discovery
    record.has_main_guard = _has_main_guard(tree)
    record.framework_entrypoints = _framework_entrypoints(tree, relative_path)
    record.declares_public_api = (
        _declares_public_api(tree) or path.name == "__init__.py"
    )
    record.vocabulary = _vocabulary(module, record.symbols)
    if collect_style:
        record.style = _style_metrics(tree, source)
    return record


def populate_style(record: FileRecord) -> None:
    if record.parse_error or record.style.loc:
        return
    tree = record.tree
    if tree is None:
        return
    record.style = _style_metrics(tree, record.source)


def structural_tokens(source: str) -> tuple[str, ...]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ()
    return tuple(_structural_tokens(tree))


def normalized_tokens(source: str) -> tuple[str, ...]:
    return tuple(_normalized_tokens(source))


def _imports(tree: ast.AST, current_module: str, is_package: bool) -> list[ImportRef]:
    result: list[ImportRef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                result.append(ImportRef(module=alias.name))
        elif isinstance(node, ast.ImportFrom):
            target = _resolve_relative(
                current_module, node.module or "", node.level, is_package
            )
            result.append(
                ImportRef(
                    module=target, names=tuple(alias.name for alias in node.names)
                )
            )
    return result


def import_modules(source: str, current_module: str, is_package: bool) -> set[str]:
    """Return absolute import targets for current or historical source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    result: set[str] = set()
    for reference in _imports(tree, current_module, is_package):
        if reference.module:
            result.add(reference.module)
        result.update(
            f"{reference.module}.{name}" if reference.module else name
            for name in reference.names
            if name != "*"
        )
    return result


def _resolve_relative(current: str, imported: str, level: int, is_package: bool) -> str:
    if level == 0:
        return imported
    package_parts = current.split(".") if is_package else current.split(".")[:-1]
    keep = max(0, len(package_parts) - (level - 1))
    parts = package_parts[:keep]
    if imported:
        parts.extend(imported.split("."))
    return ".".join(part for part in parts if part)


def _has_main_guard(tree: ast.Module) -> bool:
    for node in tree.body:
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        text = ast.dump(node.test, include_attributes=False)
        if "__name__" in text and "__main__" in text:
            return True
    return False


def _framework_entrypoints(tree: ast.Module, relative_path: str) -> tuple[str, ...]:
    frameworks: set[str] = set()
    constructors = {
        "FastAPI": "fastapi",
        "fastapi.FastAPI": "fastapi",
        "Flask": "flask",
        "flask.Flask": "flask",
        "Typer": "typer",
        "typer.Typer": "typer",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            framework = constructors.get(dotted_name(node.func))
            if framework:
                frameworks.add(framework)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decorators = {_decorator_name(item) for item in node.decorator_list}
            if any(name in {"click.command", "click.group"} for name in decorators):
                frameworks.add("click")
    name = Path(relative_path).name.lower()
    if name in {"asgi.py", "settings.py", "urls.py", "wsgi.py"}:
        frameworks.add("django")
    return tuple(sorted(frameworks))


def _declares_public_api(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            target = (
                node.targets[0]
                if isinstance(node, ast.Assign) and node.targets
                else node.target
            )
            if isinstance(target, ast.Name) and target.id == "__all__":
                return True
    return False


def _public_exports(tree: ast.Module) -> set[str]:
    exports: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in targets
        ):
            continue
        value = node.value
        if not isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            continue
        exports.update(
            item.value
            for item in value.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        )
    return exports


def _symbol_details(tree: ast.Module) -> tuple[SymbolRecord, ...]:
    details: list[SymbolRecord] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        tokens = (
            list(_normalized_symbol_tokens(node))
            if not isinstance(node, ast.ClassDef)
            else []
        )
        payload = "\x1f".join(tokens)
        details.append(
            SymbolRecord(
                name=node.name,
                kind="class" if isinstance(node, ast.ClassDef) else "function",
                lineno=node.lineno,
                end_lineno=node.end_lineno or node.lineno,
                private=node.name.startswith("_") and not node.name.startswith("__"),
                decorated=bool(node.decorator_list),
                normalized_body_hash=(
                    sha256(payload.encode("utf-8")).hexdigest() if payload else ""
                ),
                body_nodes=sum(token.startswith("node:") for token in tokens),
                parameter_count=(
                    0
                    if isinstance(node, ast.ClassDef)
                    else len(node.args.posonlyargs)
                    + len(node.args.args)
                    + len(node.args.kwonlyargs)
                ),
            )
        )
    return tuple(details)


def _normalized_symbol_tokens(value: object, field_name: str = ""):
    if isinstance(value, ast.Constant):
        yield "node:Constant"
        if value.value is None or isinstance(value.value, bool):
            yield repr(value.value)
        else:
            yield f"constant:{type(value.value).__name__}"
        return
    if isinstance(value, ast.AST):
        yield f"node:{type(value).__name__}"
        for child_field, child in ast.iter_fields(value):
            if child_field == "decorator_list":
                continue
            yield f"field:{child_field}"
            if (
                child_field == "id"
                and isinstance(value, ast.Name)
                or child_field == "arg"
                and isinstance(value, ast.arg)
                or child_field == "name"
                and isinstance(
                    value, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                )
                or child_field == "asname"
            ):
                yield "<identifier>"
            else:
                yield from _normalized_symbol_tokens(child, child_field)
        return
    if isinstance(value, list):
        yield f"list:{len(value)}"
        for item in value:
            yield from _normalized_symbol_tokens(item, field_name)
        return
    if isinstance(value, str):
        yield value
    elif value is not None:
        yield repr(value)


def _vocabulary(module: str, symbols: set[str]) -> set[str]:
    words: set[str] = set()
    for value in [module.rsplit(".", 1)[-1], *symbols]:
        expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
        words.update(
            part.lower()
            for part in WORD_RE.findall(expanded)
            for part in part.split("_")
            if len(part) > 2
        )
    return words


def _structural_tokens(node: ast.AST):
    yield type(node).__name__
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        yield f"args:{len(node.args.args)}"
    elif isinstance(node, ast.Constant):
        yield f"const:{type(node.value).__name__}"
    elif isinstance(node, (ast.operator, ast.unaryop, ast.boolop, ast.cmpop)):
        yield type(node).__name__
    for child in ast.iter_child_nodes(node):
        yield from _structural_tokens(child)


def _normalized_tokens(source: str):
    try:
        stream = tokenize.generate_tokens(io.StringIO(source).readline)
        for token_info in stream:
            token_type, text = token_info.type, token_info.string
            if token_type in {
                tokenize.ENCODING,
                tokenize.ENDMARKER,
                tokenize.INDENT,
                tokenize.DEDENT,
                tokenize.NEWLINE,
                tokenize.NL,
                tokenize.COMMENT,
            }:
                continue
            if token_type == tokenize.NAME and not keyword.iskeyword(text):
                yield "NAME"
            elif token_type == tokenize.STRING:
                yield "STRING"
            elif token_type == tokenize.NUMBER:
                yield "NUMBER"
            else:
                yield text
    except (tokenize.TokenError, IndentationError):
        return


def _style_metrics(tree: ast.Module, source: str) -> StyleMetrics:
    lines = source.splitlines()
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    function_lengths = [
        max(1, (node.end_lineno or node.lineno) - node.lineno + 1) for node in functions
    ]
    top_level_functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    complexities = [_function_complexity(node) for node in functions]
    docstring_lines = 0
    for node in [
        *functions,
        *(item for item in ast.walk(tree) if isinstance(item, ast.ClassDef)),
    ]:
        doc = ast.get_docstring(node, clean=False)
        if doc:
            docstring_lines += len(doc.splitlines())
    comments: list[str] = []
    try:
        comments = [
            item.string.lstrip("#").strip().lower()
            for item in tokenize.generate_tokens(io.StringIO(source).readline)
            if item.type == tokenize.COMMENT
        ]
    except (tokenize.TokenError, IndentationError):
        pass
    names = Counter(
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    )
    annotations = sum(
        1
        for node in functions
        for annotation in [
            node.returns,
            *(
                argument.annotation
                for argument in (
                    *node.args.posonlyargs,
                    *node.args.args,
                    *node.args.kwonlyargs,
                )
            ),
        ]
        if annotation is not None
    )
    annotation_nodes = _annotation_nodes(tree)
    builtin_generics = {"dict", "frozenset", "list", "set", "tuple", "type"}
    legacy_generics = {
        "Dict",
        "FrozenSet",
        "List",
        "Set",
        "Tuple",
        "Type",
        "typing.Dict",
        "typing.FrozenSet",
        "typing.List",
        "typing.Set",
        "typing.Tuple",
        "typing.Type",
    }
    legacy_unions = {"Optional", "Union", "typing.Optional", "typing.Union"}
    builtin_generic_annotations = sum(
        1
        for annotation in annotation_nodes
        for node in ast.walk(annotation)
        if isinstance(node, ast.Subscript)
        and dotted_name(node.value) in builtin_generics
    )
    legacy_generic_annotations = sum(
        1
        for annotation in annotation_nodes
        for node in ast.walk(annotation)
        if isinstance(node, ast.Subscript)
        and dotted_name(node.value) in legacy_generics
    )
    pep604_unions = sum(
        1
        for annotation in annotation_nodes
        for node in ast.walk(annotation)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)
    )
    legacy_optional_unions = sum(
        1
        for annotation in annotation_nodes
        for node in ast.walk(annotation)
        if isinstance(node, ast.Subscript) and dotted_name(node.value) in legacy_unions
    )
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    os_path_uses = sum(dotted_name(node.func).startswith("os.path.") for node in calls)
    pathlib_uses = sum(
        dotted_name(node.func) in {"Path", "pathlib.Path"} for node in calls
    )
    logging_methods = {"debug", "info", "warning", "error", "exception", "critical"}
    logging_calls = sum(
        dotted_name(node.func).startswith("logging.")
        or (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in logging_methods
            and dotted_name(node.func.value).lower() in {"log", "logger"}
        )
        for node in calls
    )
    comprehensions = sum(
        isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp))
        for node in ast.walk(tree)
    )
    for_loops = sum(
        isinstance(node, (ast.For, ast.AsyncFor)) for node in ast.walk(tree)
    )
    structured_models = sum(
        _is_structured_model(node)
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    )
    bare_dict_annotations = sum(
        1
        for annotation in annotation_nodes
        for node in ast.walk(annotation)
        if isinstance(node, ast.Subscript)
        and dotted_name(node.value) in {"dict", "Dict", "typing.Dict"}
    )
    return StyleMetrics(
        loc=len(lines),
        functions=len(functions),
        docstring_lines=docstring_lines,
        narration_comments=sum(
            comment.startswith(NARRATION_PREFIXES) for comment in comments
        ),
        comments=len(comments),
        broad_exceptions=sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler)
            and (
                node.type is None
                or (
                    isinstance(node.type, ast.Name)
                    and node.type.id in {"Exception", "BaseException"}
                )
            )
        ),
        print_calls=sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and dotted_name(node.func) == "print"
        ),
        nested_dicts=sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.Dict)
            and any(isinstance(value, ast.Dict) for value in node.values)
        ),
        temporary_names=sum(
            count for name, count in names.items() if name.lower() in TEMPORARY_NAMES
        ),
        annotations=annotations,
        function_median_loc=float(statistics.median(function_lengths))
        if function_lengths
        else 0.0,
        builtin_generic_annotations=builtin_generic_annotations,
        legacy_generic_annotations=legacy_generic_annotations,
        pep604_unions=pep604_unions,
        legacy_optional_unions=legacy_optional_unions,
        pathlib_uses=pathlib_uses,
        os_path_uses=os_path_uses,
        comprehensions=comprehensions,
        for_loops=for_loops,
        structured_models=structured_models,
        bare_dict_annotations=bare_dict_annotations,
        logging_calls=logging_calls,
        branch_points=sum(max(0, complexity - 1) for complexity in complexities),
        cyclomatic_complexity=sum(complexities),
        high_complexity_functions=sum(complexity >= 10 for complexity in complexities),
        top_level_functions=len(top_level_functions),
        private_helpers=sum(
            node.name.startswith("_") and not node.name.startswith("__")
            for node in top_level_functions
        ),
        snake_case_functions=sum(
            bool(SNAKE_CASE_RE.fullmatch(node.name)) for node in functions
        ),
        function_name_words=sum(_name_word_count(node.name) for node in functions),
    )


def _annotation_nodes(tree: ast.Module) -> list[ast.AST]:
    result: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result.extend(
                annotation
                for annotation in [
                    node.returns,
                    *(
                        argument.annotation
                        for argument in (
                            *node.args.posonlyargs,
                            *node.args.args,
                            *node.args.kwonlyargs,
                        )
                    ),
                ]
                if annotation is not None
            )
        elif isinstance(node, ast.AnnAssign):
            result.append(node.annotation)
    return result


def _is_structured_model(node: ast.ClassDef) -> bool:
    decorators = {_decorator_name(decorator) for decorator in node.decorator_list}
    bases = {dotted_name(base) for base in node.bases}
    return bool(
        decorators & {"dataclass", "dataclasses.dataclass"}
        or bases & {"TypedDict", "typing.TypedDict"}
    )


def _decorator_name(node: ast.AST) -> str:
    return dotted_name(node.func) if isinstance(node, ast.Call) else dotted_name(node)


def _function_complexity(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    complexity = 1
    for child in ast.walk(node):
        if isinstance(child, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.IfExp)):
            complexity += 1
        elif isinstance(child, ast.BoolOp):
            complexity += max(1, len(child.values) - 1)
        elif isinstance(child, ast.Try):
            complexity += len(child.handlers) + bool(child.orelse)
        elif isinstance(child, ast.Match):
            complexity += max(1, len(child.cases) - 1)
        elif isinstance(child, ast.comprehension):
            complexity += len(child.ifs)
    return complexity


def _name_word_count(name: str) -> int:
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name.strip("_"))
    return max(1, len([part for part in expanded.split("_") if part]))
