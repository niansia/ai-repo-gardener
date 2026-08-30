from __future__ import annotations

import io
import os
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

from .discovery import module_names
from .models import FileRecord
from .parsing import import_modules


@dataclass(frozen=True)
class ImportMigration:
    changed_path: str
    removed: frozenset[str]
    added: frozenset[str]


@dataclass(frozen=True)
class FileBirth:
    commit: str
    timestamp: int


def git_executable() -> str | None:
    configured = os.environ.get("REPO_GARDENER_GIT")
    if configured and Path(configured).is_file():
        return configured
    return shutil.which("git")


def is_git_repository(root: Path) -> bool:
    return _run(root, ["rev-parse", "--is-inside-work-tree"]) is not None


def changed_paths(root: Path, base: str) -> set[str]:
    outputs = [
        _run(
            root,
            ["diff", "--name-only", "--diff-filter=ACDMRTUXB", f"{base}...HEAD"],
        ),
        _run(root, ["diff", "--name-only", "--diff-filter=ACDMRTUXB", "HEAD"]),
        _run(root, ["ls-files", "--others", "--exclude-standard"]),
    ]
    return {
        line.strip().replace("\\", "/")
        for output in outputs
        if output is not None
        for line in output.splitlines()
        if line.strip()
    }


def file_birth(root: Path, relative_path: str) -> FileBirth | None:
    output = _run(
        root,
        ["log", "--diff-filter=A", "--format=%H%x09%ct", "--", relative_path],
    )
    if not output:
        return None
    for line in output.splitlines():
        commit, separator, timestamp = line.partition("\t")
        if separator and commit and timestamp.isdigit():
            return FileBirth(commit=commit, timestamp=int(timestamp))
    return None


def file_added_later(root: Path, earlier: FileBirth, later: FileBirth) -> bool:
    if earlier.commit == later.commit:
        return False
    relation = _is_ancestor(root, earlier.commit, later.commit)
    if relation is not None:
        return relation
    return later.timestamp > earlier.timestamp


def import_migrations(
    root: Path, base: str, records: list[FileRecord]
) -> list[ImportMigration]:
    by_path = {record.relative_path: record for record in records}
    migrations: list[ImportMigration] = []
    for relative_path in sorted(changed_paths(root, base)):
        if not relative_path.endswith(".py"):
            continue
        before = _run(root, ["show", f"{base}:{relative_path}"])
        if before is None:
            continue
        after = by_path.get(relative_path).source if relative_path in by_path else ""
        module = module_names(root, root / relative_path)[0]
        is_package = Path(relative_path).name == "__init__.py"
        previous_imports = import_modules(before, module, is_package)
        current_imports = import_modules(after, module, is_package)
        removed = previous_imports - current_imports
        added = current_imports - previous_imports
        if removed or added:
            migrations.append(
                ImportMigration(
                    changed_path=relative_path,
                    removed=frozenset(removed),
                    added=frozenset(added),
                )
            )
    return migrations


def resolve_commit(root: Path, value: str) -> str | None:
    direct = resolve_git_ref(root, value)
    if direct and direct.strip():
        return direct
    before = _run(root, ["rev-list", "-1", f"--before={value}", "HEAD"])
    return before.splitlines()[0].strip() if before and before.strip() else None


def resolve_git_ref(root: Path, value: str) -> str | None:
    direct = _run(root, ["rev-parse", "--verify", f"{value}^{{commit}}"])
    return direct.splitlines()[0].strip() if direct and direct.strip() else None


def python_sources_at_commit(root: Path, commit: str) -> dict[str, str]:
    executable = git_executable()
    if executable is None:
        return {}
    try:
        completed = subprocess.run(
            [executable, "-C", str(root), "archive", "--format=tar", commit],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return {}
    sources: dict[str, str] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(completed.stdout), mode="r:") as archive:
            for member in archive.getmembers():
                if not member.isfile() or not member.name.endswith(".py"):
                    continue
                handle = archive.extractfile(member)
                if handle is not None:
                    sources[member.name.replace("\\", "/")] = handle.read().decode(
                        "utf-8", errors="replace"
                    )
    except tarfile.TarError:
        return {}
    return dict(sorted(sources.items()))


def _run(root: Path, arguments: list[str]) -> str | None:
    executable = git_executable()
    if executable is None:
        return None
    try:
        completed = subprocess.run(
            [executable, "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return completed.stdout


def _is_ancestor(root: Path, earlier: str, later: str) -> bool | None:
    executable = git_executable()
    if executable is None:
        return None
    try:
        completed = subprocess.run(
            [
                executable,
                "-C",
                str(root),
                "merge-base",
                "--is-ancestor",
                earlier,
                later,
            ],
            check=False,
            capture_output=True,
        )
    except OSError:
        return None
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    return None
