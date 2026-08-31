from __future__ import annotations

import ast
import atexit
import io
import json
import keyword
import os
import re
import sqlite3
import statistics
import sys
import threading
import tokenize
from collections import Counter
from dataclasses import asdict
from functools import cache
from hashlib import sha256
from itertools import pairwise
from pathlib import Path

from . import __version__
from .ast_utils import dotted_name, same_scope_nodes
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
PARSE_CACHE_SCHEMA = 2
FRAMEWORK_IMPORT_ROOTS = {"click", "fastapi", "flask", "typer"}
DYNAMIC_RUNTIME_MARKERS = (
    "SourceFileLoader",
    "SourcelessFileLoader",
    "__import__",
    "entry_points",
    "eval",
    "exec",
    "import_module",
    "importlib",
    "iter_modules",
    "monkeypatch",
    "patch",
    "pkg_resources",
    "pkgutil",
    "run_module",
    "run_path",
    "runpy",
    "spec_from_file_location",
    "walk_packages",
)
_CACHE_LOCK = threading.RLock()
_CACHE_CONNECTIONS: dict[Path, sqlite3.Connection] = {}
_CACHE_MEMORY: dict[tuple[Path, str], dict[str, object]] = {}
_CACHE_DIRTY: set[tuple[Path, str]] = set()


def parse_file(
    path: Path,
    relative_path: str,
    module: str,
    category: str,
    module_aliases: tuple[str, ...] = (),
    collect_style: bool = False,
) -> FileRecord:
    source = path.read_text(encoding="utf-8-sig", errors="replace")
    cache_key = _parse_cache_key(
        relative_path, module, category, module_aliases, source
    )
    cached = (
        _read_parse_cache(
            cache_key,
            path,
            relative_path,
            module,
            category,
            module_aliases,
            source,
        )
        if not collect_style
        else None
    )
    if cached is not None:
        return cached
    record = parse_source(
        source,
        path,
        relative_path,
        module,
        category,
        module_aliases,
        path.stat().st_mtime,
        collect_style,
    )
    if not collect_style:
        _write_parse_cache(cache_key, record)
    return record


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
    source = source.removeprefix("\ufeff")
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
    record.public_surface = _public_surface(tree)
    record.public_assignments = _public_assignment_names(tree)
    runtime_scanner = RuntimeReferenceScanner(tree)
    runtime_references = (
        runtime_scanner.scan()
        if any(marker in source for marker in DYNAMIC_RUNTIME_MARKERS)
        else runtime_scanner.scan_strings_only()
    )
    record.dynamic_refs = set(runtime_references.modules)
    record.runtime_string_refs = set(runtime_references.possible_modules)
    record.opaque_dynamic_discovery = runtime_references.opaque_discovery
    record.has_main_guard = _has_main_guard(tree)
    record.framework_entrypoints = _framework_entrypoints(tree, relative_path, source)
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


def _parse_cache_key(
    relative_path: str,
    module: str,
    category: str,
    module_aliases: tuple[str, ...],
    source: str,
) -> str:
    payload = "\x1f".join(
        (
            str(PARSE_CACHE_SCHEMA),
            _analysis_cache_abi(),
            f"{sys.version_info.major}.{sys.version_info.minor}",
            relative_path,
            module,
            category,
            *module_aliases,
            sha256(source.encode("utf-8")).hexdigest(),
        )
    )
    return sha256(payload.encode("utf-8")).hexdigest()


@cache
def _analysis_cache_abi() -> str:
    """Fingerprint every implementation file that shapes a cached parse record.

    This removes the upgrade-correctness dependency on a developer remembering
    to bump a lone integer whenever extraction behavior changes.
    """

    digest = sha256(f"repo-gardener:{__version__}".encode())
    package = Path(__file__).resolve().parent
    for name in ("ast_utils.py", "models.py", "parsing.py", "runtime_references.py"):
        digest.update(name.encode())
        try:
            digest.update((package / name).read_bytes())
        except OSError:
            # Installed source distributions normally expose these files.  The
            # package version remains a safe fallback in unusual importers.
            digest.update(__version__.encode())
    return digest.hexdigest()


def _parse_cache_root() -> Path | None:
    if os.environ.get("REPO_GARDENER_DISABLE_CACHE", "").lower() in {
        "1",
        "true",
        "yes",
    }:
        return None
    configured = os.environ.get("REPO_GARDENER_CACHE_DIR")
    if configured:
        return Path(configured).expanduser() / "parse-v2"
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "repo-gardener" / "cache" / "parse-v2"
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg_cache).expanduser() if xdg_cache else Path.home() / ".cache"
    return base / "repo-gardener" / "parse-v2"


def _read_parse_cache(
    cache_key: str,
    path: Path,
    relative_path: str,
    module: str,
    category: str,
    module_aliases: tuple[str, ...],
    source: str,
) -> FileRecord | None:
    root = _parse_cache_root()
    if root is None:
        return None
    try:
        payload = _cached_payload(root, cache_key)
        if not isinstance(payload, dict) or payload.get("schema") != PARSE_CACHE_SCHEMA:
            return None
        tree = ast.parse(source, filename=relative_path)
        imports = tuple(payload["imports"])
        symbols = tuple(payload["symbol_details"])
        if not all(isinstance(item, dict) for item in (*imports, *symbols)):
            return None
        record = FileRecord(
            path=path,
            relative_path=relative_path,
            module=module,
            category=category,
            source=source,
            module_aliases=module_aliases,
            mtime=path.stat().st_mtime,
            tree=tree,
            imports=[
                ImportRef(
                    module=str(item["module"]),
                    names=tuple(str(name) for name in item.get("names", [])),
                    conditional=bool(item.get("conditional", False)),
                    type_checking=bool(item.get("type_checking", False)),
                )
                for item in imports
            ],
            symbol_details=tuple(SymbolRecord(**item) for item in symbols),
            exported_symbols=set(map(str, payload["exported_symbols"])),
            public_surface={
                str(name): str(fingerprint)
                for name, fingerprint in dict(payload["public_surface"]).items()
            },
            public_assignments=set(map(str, payload["public_assignments"])),
            dynamic_refs=set(map(str, payload["dynamic_refs"])),
            runtime_string_refs=set(map(str, payload["runtime_string_refs"])),
            opaque_dynamic_discovery=bool(payload["opaque_dynamic_discovery"]),
            has_main_guard=bool(payload["has_main_guard"]),
            framework_entrypoints=tuple(map(str, payload["framework_entrypoints"])),
            declares_public_api=bool(payload["declares_public_api"]),
            vocabulary=set(map(str, payload["vocabulary"])),
            parse_cache_hit=True,
        )
        record.symbols = {symbol.name for symbol in record.symbol_details}
        return record
    except (
        OSError,
        sqlite3.Error,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
        SyntaxError,
    ):
        return None


def _write_parse_cache(cache_key: str, record: FileRecord) -> None:
    if record.parse_error:
        return
    root = _parse_cache_root()
    if root is None:
        return
    payload = {
        "schema": PARSE_CACHE_SCHEMA,
        "imports": [asdict(item) for item in record.imports],
        "symbol_details": [asdict(item) for item in record.symbol_details],
        "exported_symbols": sorted(record.exported_symbols),
        "public_surface": dict(sorted(record.public_surface.items())),
        "public_assignments": sorted(record.public_assignments),
        "dynamic_refs": sorted(record.dynamic_refs),
        "runtime_string_refs": sorted(record.runtime_string_refs),
        "opaque_dynamic_discovery": record.opaque_dynamic_discovery,
        "has_main_guard": record.has_main_guard,
        "framework_entrypoints": list(record.framework_entrypoints),
        "declares_public_api": record.declares_public_api,
        "vocabulary": sorted(record.vocabulary),
    }
    with _CACHE_LOCK:
        _CACHE_MEMORY[(root, cache_key)] = payload
        _CACHE_DIRTY.add((root, cache_key))


def _cached_payload(root: Path, cache_key: str) -> dict[str, object] | None:
    identity = (root, cache_key)
    with _CACHE_LOCK:
        if identity in _CACHE_MEMORY:
            return _CACHE_MEMORY[identity]
        connection = _cache_connection(root)
        row = connection.execute(
            "SELECT payload FROM parse_records WHERE cache_key = ?", (cache_key,)
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(row[0])
        if isinstance(payload, dict):
            _CACHE_MEMORY[identity] = payload
            return payload
    return None


def _cache_connection(root: Path) -> sqlite3.Connection:
    connection = _CACHE_CONNECTIONS.get(root)
    if connection is not None:
        return connection
    root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(root / "records.sqlite3", check_same_thread=False)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS parse_records ("
        "cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
    )
    _CACHE_CONNECTIONS[root] = connection
    return connection


def flush_parse_cache() -> None:
    """Persist pending records in one transaction."""
    with _CACHE_LOCK:
        roots = {root for root, _ in _CACHE_DIRTY}
        for root in roots:
            connection = _CACHE_CONNECTIONS.get(root)
            try:
                connection = connection or _cache_connection(root)
                rows = [
                    (
                        key,
                        json.dumps(
                            _CACHE_MEMORY[(root, key)],
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    )
                    for dirty_root, key in _CACHE_DIRTY
                    if dirty_root == root
                ]
                if rows:
                    connection.executemany(
                        "INSERT OR REPLACE INTO parse_records(cache_key, payload) "
                        "VALUES (?, ?)",
                        rows,
                    )
                    connection.commit()
            except (OSError, sqlite3.Error):
                pass
        _CACHE_DIRTY.clear()
        _CACHE_MEMORY.clear()


def _close_parse_cache() -> None:
    flush_parse_cache()
    with _CACHE_LOCK:
        for connection in _CACHE_CONNECTIONS.values():
            connection.close()
        _CACHE_CONNECTIONS.clear()


atexit.register(_close_parse_cache)


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


def _framework_entrypoints(
    tree: ast.Module, relative_path: str, source: str
) -> tuple[str, ...]:
    frameworks: set[str] = set()
    constructors = {
        "FastAPI": "fastapi",
        "fastapi.FastAPI": "fastapi",
        "Flask": "flask",
        "flask.Flask": "flask",
        "Typer": "typer",
        "typer.Typer": "typer",
    }
    if any(root in source for root in FRAMEWORK_IMPORT_ROOTS):
        _scan_framework_scope(tree.body, {}, frameworks, constructors)
    name = Path(relative_path).name.lower()
    if name in {"asgi.py", "settings.py", "urls.py", "wsgi.py"}:
        frameworks.add("django")
    if name in {"sitecustomize.py", "usercustomize.py"}:
        frameworks.add("python-runtime")
    if name == "noxfile.py":
        frameworks.add("nox")
    if name == "fabfile.py":
        frameworks.add("fabric")
    if name == "locustfile.py":
        frameworks.add("locust")
    relative = Path(relative_path)
    if name == "conf.py" and any(
        part.lower() in {"doc", "docs"} for part in relative.parts[:-1]
    ):
        frameworks.add("sphinx")
    return tuple(sorted(frameworks))


def _scan_framework_scope(
    body: list[ast.stmt],
    inherited: dict[str, set[str]],
    frameworks: set[str],
    constructors: dict[str, str],
) -> None:
    nodes = list(same_scope_nodes(body))
    bindings = _import_bindings(nodes, inherited)
    for node in nodes:
        if isinstance(node, ast.Call):
            frameworks.update(
                constructors[name]
                for name in _canonical_dotted_values(node.func, bindings)
                if name in constructors
            )
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decorators = {
                name
                for item in node.decorator_list
                for name in _canonical_dotted_values(
                    item.func if isinstance(item, ast.Call) else item, bindings
                )
            }
            if any(name in {"click.command", "click.group"} for name in decorators):
                frameworks.add("click")
            for expression in _function_header_expressions(node):
                for child in ast.walk(expression):
                    if isinstance(child, ast.Call):
                        frameworks.update(
                            constructors[name]
                            for name in _canonical_dotted_values(child.func, bindings)
                            if name in constructors
                        )
            _scan_framework_scope(node.body, bindings, frameworks, constructors)
        elif isinstance(node, ast.ClassDef):
            for expression in (*node.decorator_list, *node.bases):
                for child in ast.walk(expression):
                    if isinstance(child, ast.Call):
                        frameworks.update(
                            constructors[name]
                            for name in _canonical_dotted_values(child.func, bindings)
                            if name in constructors
                        )
            _scan_framework_scope(node.body, bindings, frameworks, constructors)


def _function_header_expressions(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[ast.expr, ...]:
    values: list[ast.expr] = [*node.decorator_list, *node.args.defaults]
    values.extend(value for value in node.args.kw_defaults if value is not None)
    values.extend(
        value
        for value in (
            node.returns,
            node.args.vararg.annotation if node.args.vararg else None,
            node.args.kwarg.annotation if node.args.kwarg else None,
            *(argument.annotation for argument in node.args.posonlyargs),
            *(argument.annotation for argument in node.args.args),
            *(argument.annotation for argument in node.args.kwonlyargs),
        )
        if value is not None
    )
    return tuple(values)


def _import_bindings(
    nodes: list[ast.AST], inherited: dict[str, set[str]] | None = None
) -> dict[str, set[str]]:
    bindings: dict[str, set[str]] = {
        name: set(values) for name, values in (inherited or {}).items()
    }
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] not in FRAMEWORK_IMPORT_ROOTS:
                    continue
                local = alias.asname or alias.name.split(".", 1)[0]
                bindings.setdefault(local, set()).add(
                    alias.name if alias.asname else local
                )
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".", 1)[0] not in FRAMEWORK_IMPORT_ROOTS:
                continue
            for alias in node.names:
                if alias.name != "*":
                    bindings.setdefault(alias.asname or alias.name, set()).add(
                        f"{node.module}.{alias.name}"
                    )

    # Follow simple constructor aliases such as ``API = FastAPI``.  This is
    # intentionally bounded to plain names; it does not attempt full data flow.
    for _ in range(max(1, len(nodes))):
        changed = False
        for node in nodes:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if not isinstance(value, (ast.Name, ast.Attribute)):
                continue
            source_name = dotted_name(value)
            if not source_name or source_name.partition(".")[0] not in bindings:
                continue
            canonical = _canonical_dotted_values(value, bindings)
            if not canonical:
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    before = set(bindings.get(target.id, set()))
                    bindings.setdefault(target.id, set()).update(canonical)
                    changed = changed or bindings[target.id] != before
        if not changed:
            break
    return bindings


def _canonical_dotted_values(node: ast.AST, bindings: dict[str, set[str]]) -> set[str]:
    name = dotted_name(node)
    if not name:
        return set()
    head, separator, tail = name.partition(".")
    canonical = bindings.get(head, {head})
    return {f"{value}.{tail}" if separator else value for value in canonical}


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


def _public_assignment_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names.update(
            target.id
            for target in targets
            if isinstance(target, ast.Name)
            and not target.id.startswith("_")
            and target.id != "__all__"
        )
    return names


def _public_surface(tree: ast.Module) -> dict[str, str]:
    """Return a conservative fingerprint of externally visible module shape."""

    surface: dict[str, str] = {}
    explicit = _public_exports(tree)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_") or node.name in explicit:
                surface[node.name] = _surface_hash(
                    "async-function"
                    if isinstance(node, ast.AsyncFunctionDef)
                    else "function",
                    node.args,
                    node.decorator_list,
                )
        elif isinstance(node, ast.ClassDef):
            if not node.name.startswith("_") or node.name in explicit:
                members: list[object] = []
                for member in node.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                        not member.name.startswith("_") or member.name.startswith("__")
                    ):
                        members.extend(
                            (member.name, member.args, member.decorator_list)
                        )
                    elif isinstance(member, (ast.Assign, ast.AnnAssign)):
                        targets = (
                            member.targets
                            if isinstance(member, ast.Assign)
                            else [member.target]
                        )
                        members.extend(
                            target.id
                            for target in targets
                            if isinstance(target, ast.Name)
                            and not target.id.startswith("_")
                        )
                surface[node.name] = _surface_hash(
                    "class", node.bases, node.keywords, members
                )
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            for target in targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id != "__all__"
                    and (not target.id.startswith("_") or target.id in explicit)
                ):
                    surface[target.id] = _surface_hash(
                        "assignment",
                        node.annotation if isinstance(node, ast.AnnAssign) else None,
                        value,
                    )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".", 1)[0]
                if not local.startswith("_") or local in explicit:
                    surface[local] = _surface_hash("import", alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                if not local.startswith("_") or local in explicit:
                    surface[local] = _surface_hash(
                        "import-from", node.module, alias.name, node.level
                    )
    for name in explicit:
        surface.setdefault(name, _surface_hash("explicit-export"))
    return surface


def _surface_hash(*values: object) -> str:
    payload = "\x1f".join(_surface_value(value) for value in values)
    return sha256(payload.encode("utf-8")).hexdigest()


def _surface_value(value: object) -> str:
    if isinstance(value, ast.AST):
        return ast.dump(value, include_attributes=False)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_surface_value(item) for item in value) + "]"
    return repr(value)


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
    logging_call_nodes = [node for node in calls if _is_logging_call(node)]
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
        defensive_guards=sum(_is_defensive_guard(node) for node in ast.walk(tree)),
        single_use_tiny_helpers=sum(
            (node.end_lineno or node.lineno) - node.lineno + 1 <= 6
            and _loaded_name_count(tree, node.name) <= 1
            for node in top_level_functions
        ),
        wrapper_functions=sum(_is_wrapper_function(node) for node in functions),
        log_then_reraise_handlers=sum(
            _logs_then_reraises(node)
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler)
        ),
        redundant_temp_returns=_redundant_temp_returns(tree),
        mapping_get_calls=sum(
            isinstance(node.func, ast.Attribute) and node.func.attr == "get"
            for node in calls
        ),
        narration_logging_calls=sum(
            bool(node.args)
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value.strip().lower().startswith(NARRATION_PREFIXES)
            for node in logging_call_nodes
        ),
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


def _is_logging_call(node: ast.Call) -> bool:
    name = dotted_name(node.func)
    return name.startswith("logging.") or (
        isinstance(node.func, ast.Attribute)
        and node.func.attr
        in {"debug", "info", "warning", "error", "exception", "critical"}
        and dotted_name(node.func.value).lower() in {"log", "logger"}
    )


def _is_defensive_guard(node: ast.AST) -> bool:
    if not isinstance(node, (ast.If, ast.While, ast.Assert)):
        return False
    test = node.test
    if not isinstance(test, ast.BoolOp) or not isinstance(test.op, ast.And):
        return False
    checks = list(test.values)
    if len(checks) < 3:
        return False
    return any(
        isinstance(child, ast.Constant)
        and child.value in {None, ""}
        or isinstance(child, ast.Call)
        and dotted_name(child.func) == "len"
        for check in checks
        for child in ast.walk(check)
    )


def _loaded_name_count(tree: ast.Module, name: str) -> int:
    return sum(
        isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id == name
        for node in ast.walk(tree)
    )


def _is_wrapper_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    body = list(node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    if len(body) != 1 or not isinstance(body[0], ast.Return):
        return False
    value = body[0].value
    return isinstance(value, ast.Call) or (
        isinstance(value, ast.Await) and isinstance(value.value, ast.Call)
    )


def _logs_then_reraises(node: ast.ExceptHandler) -> bool:
    return any(
        isinstance(child, ast.Raise) and child.exc is None for child in ast.walk(node)
    ) and any(
        isinstance(child, ast.Call) and _is_logging_call(child)
        for child in ast.walk(node)
    )


def _redundant_temp_returns(tree: ast.Module) -> int:
    count = 0
    for node in ast.walk(tree):
        for _, value in ast.iter_fields(node):
            if not isinstance(value, list):
                continue
            for previous, current in pairwise(value):
                if (
                    isinstance(previous, ast.Assign)
                    and len(previous.targets) == 1
                    and isinstance(previous.targets[0], ast.Name)
                    and isinstance(current, ast.Return)
                    and isinstance(current.value, ast.Name)
                    and current.value.id == previous.targets[0].id
                ):
                    count += 1
    return count
