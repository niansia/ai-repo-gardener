from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .discovery import discover_repository_files

MAX_DEPLOYMENT_FILE_BYTES = 1_000_000
MODULE = r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*"
OBJECT = r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*"

MODULE_PATTERNS = (
    re.compile(
        rf"\b(?:python(?:\d+(?:\.\d+)*)?|py(?:\s+-\d+(?:\.\d+)?)?)\b"
        rf"[^\r\n;]{{0,120}}?\s-m\s+(?P<module>{MODULE})\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:celery|flask)\b[^\r\n;]{{0,240}}?"
        rf"(?:-A|--app)(?:=|\s)+(?P<module>{MODULE})(?::{OBJECT})?\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\bpytest\b[^\r\n;]{{0,240}}?--pyargs\s+(?P<module>{MODULE})\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:DJANGO_SETTINGS_MODULE|FLASK_APP|FASTAPI_APP|UVICORN_APP|"
        rf"GUNICORN_APP|CELERY_APP)\s*(?:=|:)\s*(?P<module>{MODULE})(?::{OBJECT})?\b",
        re.IGNORECASE,
    ),
    re.compile(rf"(?<![\w.])(?P<module>{MODULE}):{OBJECT}\b"),
)

DYNAMIC_COMMAND = re.compile(
    r"(?:\bpython(?:\d+(?:\.\d+)*)?\s+-m|"
    r"\b(?:uvicorn|gunicorn|hypercorn|daphne|celery|flask)\b|"
    r"\bpytest\b[^\r\n;]{0,120}?--pyargs|"
    r"\b(?:DJANGO_SETTINGS_MODULE|FLASK_APP|FASTAPI_APP|UVICORN_APP|"
    r"GUNICORN_APP|CELERY_APP)\s*(?:=|:))"
    r"[^\r\n;]{0,240}?(?:\$\{?\w+\}?|%\w+%|\{\{[^}\r\n]+\}\})",
    re.IGNORECASE,
)

ROOT_NAMES = {
    "app.json",
    "app.yaml",
    "app.yml",
    "compose.yaml",
    "compose.yml",
    "docker-compose.yaml",
    "docker-compose.yml",
    "fly.toml",
    "heroku.yaml",
    "heroku.yml",
    "netlify.toml",
    "procfile",
    "railway.json",
    "render.yaml",
    "render.yml",
    "serverless.yaml",
    "serverless.yml",
    "supervisord.conf",
    "tox.ini",
}
DEPLOYMENT_DIRECTORIES = {"deploy", "deployment", "helm", "infra", "k8s", "kubernetes"}


@dataclass(frozen=True)
class DeploymentReferences:
    references: dict[str, tuple[str, ...]]
    uncertainty_sources: tuple[str, ...]
    scanned_files: tuple[str, ...]

    @property
    def modules(self) -> set[str]:
        return set(self.references)


def discover_deployment_references(root: Path, config: Config) -> DeploymentReferences:
    references: dict[str, set[str]] = {}
    uncertainties: set[str] = set()
    scanned: list[str] = []
    for path in discover_repository_files(root, config):
        relative = path.relative_to(root).as_posix()
        if not _is_deployment_file(relative):
            continue
        scanned.append(relative)
        try:
            if path.stat().st_size > MAX_DEPLOYMENT_FILE_BYTES:
                uncertainties.add(f"{relative}:oversized")
                continue
            source = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            uncertainties.add(f"{relative}:unreadable")
            continue
        command_view = _command_view(source)
        for pattern in MODULE_PATTERNS:
            for match in pattern.finditer(command_view):
                module = match.group("module")
                references.setdefault(module, set()).add(relative)
        if DYNAMIC_COMMAND.search(command_view):
            uncertainties.add(f"{relative}:dynamic-runtime-command")
    return DeploymentReferences(
        references={
            module: tuple(sorted(files)) for module, files in sorted(references.items())
        },
        uncertainty_sources=tuple(sorted(uncertainties)),
        scanned_files=tuple(sorted(scanned)),
    )


def _command_view(source: str) -> str:
    # JSON-array and YAML-list command forms become ordinary shell-like text.
    normalized = re.sub(r"[\[\]\"',]", " ", source)
    return re.sub(r"[ \t]+", " ", normalized)


def _is_deployment_file(relative_path: str) -> bool:
    path = Path(relative_path)
    lower_name = path.name.lower()
    lower_parts = {part.lower() for part in path.parts[:-1]}
    if lower_name == "dockerfile" or lower_name.startswith("dockerfile."):
        return True
    if lower_name in ROOT_NAMES or lower_name.startswith("docker-compose."):
        return True
    if lower_name.endswith(".service"):
        return True
    if (
        len(path.parts) >= 3
        and path.parts[0].lower() == ".github"
        and path.parts[1].lower() == "workflows"
        and path.suffix.lower() in {".yaml", ".yml"}
    ):
        return True
    return bool(lower_parts & DEPLOYMENT_DIRECTORIES) and path.suffix.lower() in {
        ".conf",
        ".ini",
        ".json",
        ".toml",
        ".yaml",
        ".yml",
    }
