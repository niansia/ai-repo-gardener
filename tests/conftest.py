from __future__ import annotations

from pathlib import Path


def write_project(root: Path, files: dict[str, str]) -> None:
    for relative_path, source in files.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source.strip() + "\n", encoding="utf-8")
