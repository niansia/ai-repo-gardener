from __future__ import annotations

import re
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path

from ..config import Config
from ..git_support import (
    ImportMigration,
    file_added_later,
    file_birth,
    is_git_repository,
)
from ..graph import ModuleGraph
from ..models import FileRecord, Finding
from ..similarity import Similarity, compare

ITERATION_SUFFIX = re.compile(
    r"(?:_(?:old|new|final|fixed|backup|copy\d*|v\d+)|\.(?:old|bak))$", re.IGNORECASE
)
COMMON_SYMBOLS = {
    "create",
    "get",
    "handle",
    "load",
    "main",
    "parse",
    "process",
    "run",
    "save",
    "set",
    "update",
}


def stale_findings(
    records: list[FileRecord],
    graph: ModuleGraph,
    config: Config,
    root: Path,
    migrations: list[ImportMigration],
    changed: set[str] | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    sources = [
        record
        for record in records
        if record.category == "source" and not record.parse_error
    ]
    reachable_sources = [
        record for record in sources if graph.is_reachable(record.module)
    ]
    family_index, symbol_index = _replacement_indexes(reachable_sources)
    for candidate in sources:
        if graph.is_reachable(candidate.module):
            continue
        replacement, similarity = _best_replacement(
            candidate,
            _replacement_pool(candidate, family_index, symbol_index),
            config,
        )
        if replacement is None or similarity is None:
            orphan = _orphan_finding(
                candidate,
                records,
                graph,
                config,
                changed or set(),
            )
            if orphan is not None:
                findings.append(orphan)
            continue
        findings.append(
            _build_finding(
                candidate,
                replacement,
                similarity,
                records,
                graph,
                config,
                root,
                migrations,
                candidate.relative_path in (changed or set()),
            )
        )
    return findings


def _orphan_finding(
    candidate: FileRecord,
    records: list[FileRecord],
    graph: ModuleGraph,
    config: Config,
    changed: set[str],
) -> Finding | None:
    if (
        candidate.relative_path not in changed
        or graph.inbound_count(candidate.module) != 0
        or candidate.path.name == "__init__.py"
    ):
        return None
    risk, risks = _risk(candidate, records, config)
    confidence = 0.68
    return Finding(
        rule="orphan-file",
        category="repo-gc",
        severity="info",
        confidence=confidence,
        risk=max(0.35, risk),
        path=candidate.relative_path,
        evidence=[
            {"type": "unreachable_from_entrypoints", "value": True},
            {"type": "inbound_imports", "value": 0},
            {"type": "changed_in_iteration", "value": True},
            {"type": "replacement_candidate", "value": False},
        ],
        risks=[*risks, "framework_or_dynamic_entrypoint_may_be_unknown"],
        recommendation="review_only",
    ).finalize()


def _build_finding(
    candidate: FileRecord,
    replacement: FileRecord,
    similarity: Similarity,
    records: list[FileRecord],
    graph: ModuleGraph,
    config: Config,
    root: Path,
    migrations: list[ImportMigration],
    candidate_changed: bool,
) -> Finding:
    inbound = graph.inbound_count(candidate.module)
    naming = _canonical_stem(candidate.path.stem) == _canonical_stem(
        replacement.path.stem
    )
    risk, risks = _risk(candidate, records, config)
    if candidate_changed:
        risk = max(risk, 0.75)
        risks.append("candidate_changed_in_iteration")
    uncovered_symbols = sorted(candidate.symbols - replacement.symbols)
    if uncovered_symbols:
        risk = max(risk, 0.55)
        risks.append("replacement_missing_symbols")
    missing_public_surface = sorted(
        set(candidate.public_surface) - set(replacement.public_surface)
    )
    changed_public_surface = sorted(
        name
        for name in set(candidate.public_surface) & set(replacement.public_surface)
        if candidate.public_surface[name] != replacement.public_surface[name]
    )
    if missing_public_surface:
        risk = max(risk, 0.65)
        risks.append("replacement_missing_public_surface")
    if changed_public_surface:
        risk = max(risk, 0.55)
        risks.append("replacement_changed_public_contract")
    call_site = _call_site_migration(candidate, replacement, migrations)
    candidate_birth = (
        file_birth(root, candidate.relative_path) if is_git_repository(root) else None
    )
    replacement_birth = (
        file_birth(root, replacement.relative_path) if is_git_repository(root) else None
    )
    newer = (
        file_added_later(root, candidate_birth, replacement_birth)
        if candidate_birth is not None and replacement_birth is not None
        else replacement.mtime > candidate.mtime
    )
    git_sequence = call_site is not None or (
        candidate_birth is not None and replacement_birth is not None and newer
    )
    confidence = _confidence(
        graph,
        replacement,
        similarity,
        newer,
        call_site,
        git_sequence,
        inbound,
        naming,
    )
    evidence = _evidence(
        graph,
        replacement,
        similarity,
        newer,
        call_site,
        git_sequence,
        inbound,
        naming,
        uncovered_symbols,
        missing_public_surface,
        changed_public_surface,
    )
    evidence.extend(
        [
            {"type": "candidate_sha256", "value": _file_hash(candidate.path)},
            {"type": "replacement_sha256", "value": _file_hash(replacement.path)},
        ]
    )
    recommendation = (
        "safe_delete_candidate" if confidence >= 0.85 and risk <= 0.20 else "review"
    )
    return Finding(
        rule="stale-file",
        category="repo-gc",
        severity="warning" if confidence >= 0.85 else "info",
        confidence=confidence,
        risk=risk,
        path=candidate.relative_path,
        replacement=replacement.relative_path,
        evidence=evidence,
        risks=risks,
        recommendation=recommendation,
    ).finalize()


def _best_replacement(
    candidate: FileRecord,
    possible_replacements: list[FileRecord],
    config: Config,
) -> tuple[FileRecord | None, Similarity | None]:
    best_record: FileRecord | None = None
    best_similarity: Similarity | None = None
    for other in possible_replacements:
        same_directory = other.path.parent == candidate.path.parent
        shared_vocabulary = bool(other.vocabulary & candidate.vocabulary)
        informative_symbols = (other.symbols & candidate.symbols) - COMMON_SYMBOLS
        symbol_union = other.symbols | candidate.symbols
        symbol_overlap = (
            len(informative_symbols) / len(symbol_union) if symbol_union else 0.0
        )
        same_family = _canonical_stem(other.path.stem) == _canonical_stem(
            candidate.path.stem
        )
        similar_size = _similar_size(candidate, other)
        substantive_overlap = (
            same_directory and len(informative_symbols) >= 2 and symbol_overlap >= 0.4
        )
        if not similar_size or not (
            same_family or substantive_overlap or (shared_vocabulary and same_family)
        ):
            continue
        similarity = compare(candidate, other)
        threshold = config.min_similarity - (0.08 if same_family else 0.0)
        if similarity.overall < threshold or similarity.ast < 0.55:
            continue
        if best_similarity is None or similarity.overall > best_similarity.overall:
            best_record, best_similarity = other, similarity
    return best_record, best_similarity


def _replacement_indexes(
    reachable_sources: list[FileRecord],
) -> tuple[dict[str, list[FileRecord]], dict[tuple[Path, str], list[FileRecord]]]:
    family_index: dict[str, list[FileRecord]] = defaultdict(list)
    symbol_index: dict[tuple[Path, str], list[FileRecord]] = defaultdict(list)
    for record in reachable_sources:
        family_index[_canonical_stem(record.path.stem)].append(record)
        for symbol in record.symbols - COMMON_SYMBOLS:
            symbol_index[(record.path.parent, symbol)].append(record)
    return dict(family_index), dict(symbol_index)


def _replacement_pool(
    candidate: FileRecord,
    family_index: dict[str, list[FileRecord]],
    symbol_index: dict[tuple[Path, str], list[FileRecord]],
) -> list[FileRecord]:
    selected = {
        record.module: record
        for record in family_index.get(_canonical_stem(candidate.path.stem), [])
    }
    shared_counts: Counter[str] = Counter()
    by_module: dict[str, FileRecord] = {}
    for symbol in candidate.symbols - COMMON_SYMBOLS:
        for record in symbol_index.get((candidate.path.parent, symbol), []):
            shared_counts[record.module] += 1
            by_module[record.module] = record
    for module, count in shared_counts.items():
        if count >= 2:
            selected[module] = by_module[module]
    return [selected[module] for module in sorted(selected)]


def _similar_size(left: FileRecord, right: FileRecord) -> bool:
    left_size = len(left.source)
    right_size = len(right.source)
    if not left_size or not right_size:
        return False
    ratio = left_size / right_size
    return 0.35 <= ratio <= 2.85


def _risk(
    record: FileRecord, records: list[FileRecord], config: Config
) -> tuple[float, list[str]]:
    risk = 0.0
    risks: list[str] = []
    parse_error_paths = sorted(
        item.relative_path for item in records if item.parse_error is not None
    )
    if parse_error_paths:
        risk = 1.0
        risks.append(
            "repository_parse_errors:"
            + str(len(parse_error_paths))
            + ":"
            + ",".join(parse_error_paths[:3])
        )
    if config.is_protected(record.relative_path):
        risk = 1.0
        risks.append("protected_path")
    if record.category in {"generated", "migration", "vendor"}:
        risk = 1.0
        risks.append(record.category)
    if record.packaged_public_module:
        risk = 1.0
        risks.append("packaged_public_module")
    elif record.declares_public_api:
        risk = max(risk, 0.45)
        risks.append("public_api_or_package_init")
    elif (
        record.possible_package_module or _is_package_module(record, records)
    ) and not config.allow_delete_package_modules:
        risk = max(risk, 0.45)
        risks.append("possible_external_package_module")
    if record.packaging_uncertainty:
        risk = 1.0
        risks.append(
            "packaging_entrypoint_uncertainty:"
            + ",".join(record.packaging_uncertainty[:3])
        )
    if record.deployment_uncertainty:
        risk = 1.0
        risks.append(
            "deployment_runtime_uncertainty:"
            + ",".join(record.deployment_uncertainty[:3])
        )
    elif record.relative_path.startswith("src/") and not config.allow_delete_src:
        risk = max(risk, 0.25)
        risks.append("possible_external_package_module")
    if record.public_assignments and not record.symbol_details:
        risk = max(risk, 0.55)
        risks.append("data_or_config_module_requires_literal_review")
    opaque_users = sorted(
        user.relative_path for user in records if user.opaque_dynamic_discovery
    )
    if opaque_users:
        risk = 1.0
        risks.append("opaque_dynamic_module_discovery:" + ",".join(opaque_users[:3]))
    dynamic_users = [
        user.relative_path
        for user in records
        if any(
            any(
                value == name or value.startswith(name + ".")
                for name in (record.module, *record.module_aliases)
            )
            for value in user.dynamic_refs
        )
    ]
    if dynamic_users:
        risk = max(risk, 0.75)
        risks.append("dynamic_reference:" + ",".join(sorted(dynamic_users)[:3]))
    string_users = [
        user.relative_path
        for user in records
        if any(
            any(
                value == name or value.startswith(name + ".")
                for name in (record.module, *record.module_aliases)
            )
            for value in user.runtime_string_refs
        )
    ]
    if string_users:
        risk = max(risk, 0.75)
        risks.append("module_shaped_string:" + ",".join(sorted(string_users)[:3]))
    return risk, risks


def _is_package_module(record: FileRecord, records: list[FileRecord]) -> bool:
    package_directories = {
        Path(item.relative_path).parent
        for item in records
        if item.path.name == "__init__.py"
    }
    parent = Path(record.relative_path).parent
    return any(
        directory == parent or directory in parent.parents
        for directory in package_directories
    )


def _file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _call_site_migration(
    candidate: FileRecord,
    replacement: FileRecord,
    migrations: list[ImportMigration],
) -> ImportMigration | None:
    return next(
        (
            migration
            for migration in migrations
            if any(
                name in migration.removed
                for name in (candidate.module, *candidate.module_aliases)
            )
            and (
                any(
                    name in migration.added
                    for name in (replacement.module, *replacement.module_aliases)
                )
                or any(
                    value.startswith(name + ".")
                    for value in migration.added
                    for name in (replacement.module, *replacement.module_aliases)
                )
            )
        ),
        None,
    )


def _confidence(
    graph: ModuleGraph,
    replacement: FileRecord,
    similarity: Similarity,
    newer: bool,
    call_site: ImportMigration | None,
    git_sequence: bool,
    inbound: int,
    naming: bool,
) -> float:
    points = 25.0
    points += (
        20.0
        if graph.is_reachable(replacement.module)
        and similarity.overall >= 0.72
        and (newer or call_site is not None)
        else 0.0
    )
    points += 15.0 * similarity.ast
    points += 15.0 if git_sequence else 0.0
    points += 10.0 * similarity.symbols
    points += 10.0 if inbound == 0 else 0.0
    points += 5.0 if naming else 0.0
    confidence = max(0.0, min(1.0, points / 100.0))
    return confidence if git_sequence else min(confidence, 0.84)


def _evidence(
    graph: ModuleGraph,
    replacement: FileRecord,
    similarity: Similarity,
    newer: bool,
    call_site: ImportMigration | None,
    git_sequence: bool,
    inbound: int,
    naming: bool,
    uncovered_symbols: list[str],
    missing_public_surface: list[str],
    changed_public_surface: list[str],
) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = [
        {"type": "unreachable_from_entrypoints", "value": True},
        {"type": "inbound_imports", "value": inbound},
        {"type": "ast_similarity", "value": round(similarity.ast, 3)},
        {"type": "token_similarity", "value": round(similarity.tokens, 3)},
        {"type": "symbol_overlap", "value": round(similarity.symbols, 3)},
        {
            "type": "replacement_reachable",
            "value": graph.is_reachable(replacement.module),
        },
        {"type": "replacement_newer", "value": newer},
    ]
    if naming:
        evidence.append({"type": "iteration_naming", "value": True})
    if uncovered_symbols:
        evidence.append(
            {
                "type": "symbols_missing_from_replacement",
                "value": uncovered_symbols,
            }
        )
    if missing_public_surface:
        evidence.append(
            {
                "type": "public_surface_missing_from_replacement",
                "value": missing_public_surface,
            }
        )
    if changed_public_surface:
        evidence.append(
            {
                "type": "public_contract_changed_in_replacement",
                "value": changed_public_surface,
            }
        )
    if call_site:
        evidence.append(
            {"type": "call_site_migration", "value": call_site.changed_path}
        )
    if git_sequence and not call_site:
        evidence.append({"type": "git_chronology", "value": "replacement_added_later"})
    return evidence


def _canonical_stem(stem: str) -> str:
    return ITERATION_SUFFIX.sub("", stem.lower())
