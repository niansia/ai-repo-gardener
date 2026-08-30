from __future__ import annotations

import tomllib
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

DEFAULT_PROTECTED = (
    "**/migrations/**",
    "**/plugins/**",
    "**/generated/**",
    "**/vendor/**",
)


@dataclass(frozen=True)
class Config:
    entrypoint_modules: tuple[str, ...] = ()
    entrypoint_paths: tuple[str, ...] = ()
    protected: tuple[str, ...] = DEFAULT_PROTECTED
    exclude: tuple[str, ...] = ()
    flat_directory_threshold: int = 15
    min_similarity: float = 0.62
    allow_delete_src: bool = False
    allow_delete_package_modules: bool = False
    validation_commands: tuple[str, ...] = ()

    def is_protected(self, relative_path: str) -> bool:
        return matches_any(relative_path, self.protected)

    def is_excluded(self, relative_path: str) -> bool:
        return matches_any(relative_path, self.exclude)


def matches_any(relative_path: str, patterns: tuple[str, ...] | list[str]) -> bool:
    normalized = relative_path.replace("\\", "/").lstrip("./")
    pure = PurePosixPath(normalized)
    for pattern in patterns:
        clean = pattern.replace("\\", "/").lstrip("./")
        if pure.match(clean) or fnmatch(normalized, clean):
            return True
        if clean.startswith("**/") and (
            pure.match(clean[3:]) or fnmatch(normalized, clean[3:])
        ):
            return True
    return False


def load_config(root: Path, explicit: Path | None = None) -> Config:
    candidates = (
        [explicit]
        if explicit
        else [root / "repo-gardener.toml", root / ".repo-gardener.toml"]
    )
    path = next(
        (candidate for candidate in candidates if candidate and candidate.is_file()),
        None,
    )
    if path is None:
        return Config()
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    entrypoints = raw.get("entrypoints", {})
    safety = raw.get("safety", {})
    analysis = raw.get("analysis", {})
    validation = raw.get("validation", {})
    protected = tuple(
        str(value) for value in safety.get("protected", DEFAULT_PROTECTED)
    )
    return Config(
        entrypoint_modules=tuple(
            str(value) for value in entrypoints.get("modules", ())
        ),
        entrypoint_paths=tuple(str(value) for value in entrypoints.get("paths", ())),
        protected=protected,
        exclude=tuple(str(value) for value in analysis.get("exclude", ())),
        flat_directory_threshold=max(
            4, int(analysis.get("flat_directory_threshold", 15))
        ),
        min_similarity=max(0.3, min(0.95, float(analysis.get("min_similarity", 0.62)))),
        allow_delete_src=bool(safety.get("allow_delete_src", False)),
        allow_delete_package_modules=bool(
            safety.get("allow_delete_package_modules", False)
        ),
        validation_commands=tuple(
            str(value) for value in validation.get("commands", ())
        ),
    )
