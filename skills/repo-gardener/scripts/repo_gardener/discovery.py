from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .config import Config, matches_any

IGNORED_DIRECTORIES = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".repo-gardener",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
    "node_modules",
    "site-packages",
    "tmp",
    "venv",
}


def discover_python_files(root: Path, config: Config) -> list[Path]:
    return [
        path for path in discover_repository_files(root, config) if path.suffix == ".py"
    ]


def discover_repository_files(root: Path, config: Config) -> list[Path]:
    """Return safe, non-ignored repository files without following symlinks."""
    root_resolved = root.resolve()
    candidates = _git_files(root)
    if candidates is None:
        candidates = _walk_files(root)
    result: list[Path] = []
    for path in candidates:
        if path.is_symlink() or not path.is_file():
            continue
        try:
            path.resolve().relative_to(root_resolved)
        except (OSError, ValueError):
            continue
        relative = path.relative_to(root).as_posix()
        if config.is_excluded(relative):
            continue
        if any(
            part in IGNORED_DIRECTORIES for part in path.relative_to(root).parts[:-1]
        ):
            continue
        result.append(path)
    return sorted(set(result), key=lambda value: value.relative_to(root).as_posix())


def _git_files(root: Path) -> list[Path] | None:
    executable = os.environ.get("REPO_GARDENER_GIT") or shutil.which("git")
    if not executable:
        return None
    try:
        completed = subprocess.run(
            [
                executable,
                "-C",
                str(root),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return [
        root / item.decode("utf-8", errors="surrogateescape")
        for item in completed.stdout.split(b"\0")
        if item
    ]


def _walk_files(root: Path) -> list[Path]:
    found: list[Path] = []
    ignore_rules = _ignore_patterns(root)
    for current, directories, files in os.walk(root):
        current_path = Path(current)
        kept_directories = []
        for directory in directories:
            if directory in IGNORED_DIRECTORIES:
                continue
            kept_directories.append(directory)
        directories[:] = sorted(kept_directories)
        found.extend(
            current_path / name
            for name in sorted(files)
            if not _is_ignored(
                (current_path / name).relative_to(root).as_posix(), ignore_rules
            )
        )
    return found


def _ignore_patterns(root: Path) -> list[tuple[str, bool]]:
    path = root / ".gitignore"
    if not path.is_file():
        return []
    patterns: list[tuple[str, bool]] = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        if negated:
            line = line[1:].strip()
        if not line:
            continue
        line = line.lstrip("/")
        if line.endswith("/"):
            line = line.rstrip("/") + "/**"
        patterns.append((line, negated))
    return patterns


def _is_ignored(relative_path: str, rules: list[tuple[str, bool]]) -> bool:
    ignored = False
    for pattern, negated in rules:
        if matches_any(relative_path, [pattern]):
            ignored = not negated
    return ignored


def module_name(root: Path, path: Path, import_roots: tuple[str, ...] = ()) -> str:
    return module_names(root, path, import_roots)[0]


def module_names(
    root: Path, path: Path, import_roots: tuple[str, ...] = ()
) -> tuple[str, ...]:
    relative = path.relative_to(root)
    literal_parts = list(relative.with_suffix("").parts)
    if literal_parts and literal_parts[-1] == "__init__":
        literal_parts = literal_parts[:-1]
    literal = ".".join(literal_parts)
    roots = {"src", "lib", *import_roots}
    installed_names: list[str] = []
    for source_root in sorted(
        roots, key=lambda value: (-len(Path(value).parts), value)
    ):
        root_parts = Path(source_root).parts
        if root_parts and tuple(literal_parts[: len(root_parts)]) == root_parts:
            installed_names.append(".".join(literal_parts[len(root_parts) :]))
    names = tuple(dict.fromkeys(name for name in (*installed_names, literal) if name))
    return names or ("",)


def classify_path(root: Path, path: Path) -> str:
    relative = path.relative_to(root).as_posix().lower()
    name = path.name.lower()
    parts = set(path.relative_to(root).parts)
    if "migrations" in parts:
        return "migration"
    if "generated" in parts or name.endswith("_pb2.py"):
        return "generated"
    if "vendor" in parts or "site-packages" in parts:
        return "vendor"
    if (
        "tests" in parts
        or "test" in parts
        or name.startswith("test_")
        or name.endswith("_test.py")
    ):
        return "test"
    if name in {"conftest.py", "setup.py"} or relative.startswith("scripts/"):
        return "script"
    return "source"


def is_configured_entrypoint(
    relative_path: str,
    module: str,
    aliases: tuple[str, ...],
    config: Config,
    modules: set[str],
) -> bool:
    names = {module, *aliases}
    if any(name in modules or name + ".__main__" in modules for name in names):
        return True
    return matches_any(relative_path, list(config.entrypoint_paths))
