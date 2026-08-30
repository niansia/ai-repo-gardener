from __future__ import annotations

import os
import shutil
import subprocess
import tomllib
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
    candidates = _git_files(root)
    if candidates is None:
        candidates = _walk_files(root)
    result: list[Path] = []
    for path in candidates:
        if path.suffix != ".py" or not path.is_file():
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
    ignore_patterns = _ignore_patterns(root)
    for current, directories, files in os.walk(root):
        current_path = Path(current)
        kept_directories = []
        for directory in directories:
            candidate = current_path / directory
            relative = candidate.relative_to(root).as_posix()
            if directory in IGNORED_DIRECTORIES or matches_any(
                relative + "/", ignore_patterns
            ):
                continue
            kept_directories.append(directory)
        directories[:] = sorted(kept_directories)
        found.extend(
            current_path / name
            for name in sorted(files)
            if not matches_any(
                (current_path / name).relative_to(root).as_posix(), ignore_patterns
            )
        )
    return found


def _ignore_patterns(root: Path) -> list[str]:
    path = root / ".gitignore"
    if not path.is_file():
        return []
    patterns: list[str] = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "!")):
            continue
        patterns.append(line.rstrip("/"))
        if line.endswith("/"):
            patterns.append(line.rstrip("/") + "/**")
    return patterns


def module_name(root: Path, path: Path) -> str:
    return module_names(root, path)[0]


def module_names(root: Path, path: Path) -> tuple[str, ...]:
    relative = path.relative_to(root)
    literal_parts = list(relative.with_suffix("").parts)
    if literal_parts and literal_parts[-1] == "__init__":
        literal_parts = literal_parts[:-1]
    literal = ".".join(literal_parts)
    installed_parts = list(literal_parts)
    if installed_parts and installed_parts[0] in {"src", "lib"}:
        installed_parts = installed_parts[1:]
    installed = ".".join(installed_parts)
    names = tuple(dict.fromkeys(name for name in (installed, literal) if name))
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


def configured_entrypoint_modules(root: Path, config: Config) -> set[str]:
    modules = {
        value.split(":", 1)[0].strip()
        for value in config.entrypoint_modules
        if value.strip()
    }
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            with pyproject.open("rb") as handle:
                raw = tomllib.load(handle)
            project = raw.get("project", {})
            if not isinstance(project, dict):
                return modules
            entry_points = project.get("entry-points", {})
            entrypoint_tables = [
                project.get("scripts", {}),
                project.get("gui-scripts", {}),
                *(entry_points.values() if isinstance(entry_points, dict) else ()),
            ]
            for table in entrypoint_tables:
                if not isinstance(table, dict):
                    continue
                for value in table.values():
                    if not isinstance(value, str):
                        continue
                    module = value.split(":", 1)[0].strip()
                    if module:
                        modules.add(module)
        except (OSError, tomllib.TOMLDecodeError):
            pass
    return modules


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
