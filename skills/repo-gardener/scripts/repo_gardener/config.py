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
    schema = raw.get("schema", 1)
    if type(schema) is not int or schema != 1:
        raise ValueError("schema must be the integer 1")
    entrypoints = _table(raw, "entrypoints")
    safety = _table(raw, "safety")
    analysis = _table(raw, "analysis")
    validation = _table(raw, "validation")
    flat_directory_threshold = _integer(
        analysis, "flat_directory_threshold", 15, "analysis"
    )
    min_similarity = _number(analysis, "min_similarity", 0.62, "analysis")
    if flat_directory_threshold < 4:
        raise ValueError("analysis.flat_directory_threshold must be at least 4")
    if not 0.3 <= min_similarity <= 0.95:
        raise ValueError("analysis.min_similarity must be between 0.3 and 0.95")
    return Config(
        entrypoint_modules=_string_tuple(entrypoints, "modules", (), "entrypoints"),
        entrypoint_paths=_string_tuple(entrypoints, "paths", (), "entrypoints"),
        protected=_string_tuple(safety, "protected", DEFAULT_PROTECTED, "safety"),
        exclude=_string_tuple(analysis, "exclude", (), "analysis"),
        flat_directory_threshold=flat_directory_threshold,
        min_similarity=min_similarity,
        allow_delete_src=_boolean(safety, "allow_delete_src", False, "safety"),
        allow_delete_package_modules=_boolean(
            safety, "allow_delete_package_modules", False, "safety"
        ),
        validation_commands=_string_tuple(validation, "commands", (), "validation"),
    )


def _table(raw: dict[str, object], name: str) -> dict[str, object]:
    value = raw.get(name, {})
    if type(value) is not dict:
        raise ValueError(f"{name} must be a TOML table")
    return value


def _string_tuple(
    table: dict[str, object],
    key: str,
    default: tuple[str, ...],
    section: str,
) -> tuple[str, ...]:
    if key not in table:
        return default
    value = table[key]
    if type(value) is not list:
        raise ValueError(f"{section}.{key} must be an array of strings")
    if not all(type(item) is str for item in value):
        raise ValueError(f"{section}.{key} must be an array of strings")
    return tuple(value)


def _boolean(table: dict[str, object], key: str, default: bool, section: str) -> bool:
    value = table.get(key, default)
    if type(value) is not bool:
        raise ValueError(f"{section}.{key} must be a boolean")
    return value


def _integer(table: dict[str, object], key: str, default: int, section: str) -> int:
    value = table.get(key, default)
    if type(value) is not int:
        raise ValueError(f"{section}.{key} must be an integer")
    return value


def _number(table: dict[str, object], key: str, default: float, section: str) -> float:
    value = table.get(key, default)
    if type(value) not in {int, float}:
        raise ValueError(f"{section}.{key} must be a number")
    return float(value)
