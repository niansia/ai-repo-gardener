from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

from ..config import Config
from ..graph import ModuleGraph
from ..models import FileRecord, Finding

GENERIC_MODULES = {
    "base",
    "common",
    "core",
    "helper",
    "helpers",
    "manager",
    "service",
    "services",
    "util",
    "utils",
}
VOCABULARY_STOP_WORDS = GENERIC_MODULES | {
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


@dataclass(frozen=True)
class StructureAnalysis:
    findings: list[Finding]
    metrics: dict[str, object]


def analyze_structure(
    records: list[FileRecord],
    graph: ModuleGraph,
    config: Config,
    change_affinity: dict[tuple[str, str], float] | None = None,
) -> StructureAnalysis:
    by_directory: dict[str, list[FileRecord]] = defaultdict(list)
    for record in records:
        if record.category == "source" and not record.parse_error:
            directory = str(Path(record.relative_path).parent).replace("\\", "/")
            by_directory[directory].append(record)

    findings: list[Finding] = []
    directory_metrics: list[dict[str, object]] = []
    for directory, members in sorted(by_directory.items()):
        metric, finding = _directory_analysis(
            directory,
            members,
            records,
            graph,
            config,
            change_affinity or {},
        )
        directory_metrics.append(metric)
        if finding is not None:
            findings.append(finding)
    weighted_modules = sum(int(item["modules"]) for item in directory_metrics)
    score = (
        sum(float(item["score"]) * int(item["modules"]) for item in directory_metrics)
        / weighted_modules
        if weighted_modules
        else 0.0
    )
    metrics = {
        "score": round(score, 1),
        "scale": "0=low structural pressure, 100=high structural pressure",
        "directories": directory_metrics,
        "history_coupling_available": bool(change_affinity),
    }
    return StructureAnalysis(findings=findings, metrics=metrics)


def structure_findings(
    records: list[FileRecord], graph: ModuleGraph, config: Config
) -> list[Finding]:
    return analyze_structure(records, graph, config).findings


def _directory_analysis(
    directory: str,
    members: list[FileRecord],
    all_records: list[FileRecord],
    graph: ModuleGraph,
    config: Config,
    change_affinity: dict[tuple[str, str], float],
) -> tuple[dict[str, object], Finding | None]:
    affinities = _pair_affinities(members, graph, change_affinity)
    clusters = _clusters(members, graph, affinities)
    entropy = _entropy_factors(members, graph, config, clusters)
    plans = _migration_plans(directory, clusters, all_records, graph)
    entropy_after = max(0.0, float(entropy["score"]) - _estimated_entropy_gain(plans))
    metric = {
        "path": directory,
        "modules": len(members),
        **entropy,
        "estimated_after_proposals": round(entropy_after, 1),
        "estimated_delta": round(entropy_after - float(entropy["score"]), 1),
    }
    if len(members) < config.flat_directory_threshold:
        return metric, None

    generic = sorted(
        record.relative_path
        for record in members
        if record.path.stem.lower() in GENERIC_MODULES
    )
    has_partition = len(clusters) >= 2 and bool(plans)
    excess = len(members) - config.flat_directory_threshold + 1
    confidence = (
        min(0.92, 0.65 + excess * 0.02 + len(clusters) * 0.025)
        if has_partition
        else 0.60
    )
    cluster_evidence = [
        {
            "label": cluster["label"],
            "files": cluster["files"],
            "affinity": cluster["affinity"],
            "signals": cluster["signals"],
        }
        for cluster in clusters
    ]
    finding = Finding(
        rule="flat-directory",
        category="structure",
        severity="warning" if has_partition else "info",
        confidence=confidence,
        risk=0.55,
        path=directory,
        evidence=[
            {"type": "direct_python_modules", "value": len(members)},
            {"type": "structure_entropy", "value": metric},
            {"type": "probable_clusters", "value": cluster_evidence},
            {"type": "migration_plan", "value": plans},
            {"type": "generic_modules", "value": generic},
            {"type": "cluster_proposal_available", "value": has_partition},
        ],
        risks=[
            "module_moves_require_import_rewrites",
            "string_module_paths_may_break",
            "entropy_delta_is_an_estimate_not_a_refactor_guarantee",
        ],
        recommendation="proposal_only" if has_partition else "review_directory_load",
    ).finalize()
    return metric, finding


def _pair_affinities(
    records: list[FileRecord],
    graph: ModuleGraph,
    change_affinity: dict[tuple[str, str], float],
) -> dict[tuple[str, str], dict[str, float]]:
    result: dict[tuple[str, str], dict[str, float]] = {}
    by_module = {record.module: record for record in records}
    for left_name, right_name in combinations(sorted(by_module), 2):
        left = by_module[left_name]
        right = by_module[right_name]
        if left_name in graph.roots or right_name in graph.roots:
            import_score = 0.0
        else:
            import_score = float(
                right_name in graph.edges.get(left_name, set())
                or left_name in graph.edges.get(right_name, set())
            )
        left_words = left.vocabulary - VOCABULARY_STOP_WORDS
        right_words = right.vocabulary - VOCABULARY_STOP_WORDS
        union = left_words | right_words
        vocabulary = len(left_words & right_words) / len(union) if union else 0.0
        paths = tuple(sorted((left.relative_path, right.relative_path)))
        cochange = change_affinity.get(paths, 0.0)
        combined = min(1.0, import_score * 0.55 + vocabulary * 0.25 + cochange * 0.35)
        result[(left_name, right_name)] = {
            "combined": combined,
            "import": import_score,
            "vocabulary": vocabulary,
            "cochange": cochange,
        }
    return result


def _clusters(
    records: list[FileRecord],
    graph: ModuleGraph,
    affinities: dict[tuple[str, str], dict[str, float]],
) -> list[dict[str, object]]:
    modules = {record.module: record for record in records}
    active = sorted(module for module in modules if module not in graph.roots)
    adjacency: dict[str, set[str]] = {module: set() for module in active}
    for (left, right), signals in affinities.items():
        if left in adjacency and right in adjacency and signals["combined"] >= 0.42:
            adjacency[left].add(right)
            adjacency[right].add(left)

    unseen = set(active)
    components: list[set[str]] = []
    while unseen:
        start = min(unseen)
        stack = [start]
        component: set[str] = set()
        while stack:
            module = stack.pop()
            if module in component:
                continue
            component.add(module)
            stack.extend(sorted(adjacency[module] - component, reverse=True))
        unseen -= component
        if len(component) >= 2:
            components.append(component)

    maximum = max(3, int(len(records) * 0.60))
    credible = [component for component in components if len(component) <= maximum]
    result: list[dict[str, object]] = []
    for component in credible:
        internal = [
            affinities[tuple(sorted((left, right)))]
            for left, right in combinations(sorted(component), 2)
        ]
        combined = sum(item["combined"] for item in internal) / len(internal)
        if combined < 0.30:
            continue
        words = Counter(
            word
            for module in component
            for word in modules[module].vocabulary - VOCABULARY_STOP_WORDS
        )
        label = min(words, key=lambda word: (-words[word], word)) if words else "domain"
        result.append(
            {
                "label": label,
                "modules": sorted(component),
                "files": sorted(modules[module].relative_path for module in component),
                "affinity": round(combined, 3),
                "signals": {
                    "import": round(
                        sum(item["import"] for item in internal) / len(internal), 3
                    ),
                    "change_coupling": round(
                        sum(item["cochange"] for item in internal) / len(internal), 3
                    ),
                    "vocabulary": round(
                        sum(item["vocabulary"] for item in internal) / len(internal),
                        3,
                    ),
                },
            }
        )
    return sorted(result, key=lambda item: (str(item["label"]), repr(item["files"])))


def _entropy_factors(
    records: list[FileRecord],
    graph: ModuleGraph,
    config: Config,
    clusters: list[dict[str, object]],
) -> dict[str, object]:
    count = len(records)
    modules = {record.module for record in records if record.module not in graph.roots}
    possible_edges = max(1, len(modules) * max(1, len(modules) - 1))
    internal_edges = sum(
        target in modules
        for module in modules
        for target in graph.edges.get(module, set())
    )
    flatness = min(1.0, count / max(config.flat_directory_threshold, 1))
    directory_load = min(1.0, count / 25.0)
    cohesion_pressure = (
        0.0
        if len(modules) <= 1
        else 1.0 - min(1.0, internal_edges / possible_edges * 4.0)
    )
    generic_pressure = sum(
        record.path.stem.lower() in GENERIC_MODULES for record in records
    ) / max(count, 1)
    domain_fragmentation = min(1.0, len(clusters) / 4.0)
    score = 100 * (
        flatness * 0.25
        + directory_load * 0.25
        + cohesion_pressure * 0.20
        + generic_pressure * 0.15
        + domain_fragmentation * 0.15
    )
    return {
        "score": round(score, 1),
        "factors": {
            "flatness": round(flatness, 3),
            "directory_load": round(directory_load, 3),
            "low_cohesion": round(cohesion_pressure, 3),
            "generic_module_pressure": round(generic_pressure, 3),
            "domain_fragmentation": round(domain_fragmentation, 3),
        },
    }


def _migration_plans(
    directory: str,
    clusters: list[dict[str, object]],
    all_records: list[FileRecord],
    graph: ModuleGraph,
) -> list[dict[str, object]]:
    by_module = {record.module: record for record in all_records}
    plans: list[dict[str, object]] = []
    used_targets: Counter[str] = Counter()
    for cluster in clusters:
        label = _safe_directory_name(str(cluster["label"]))
        used_targets[label] += 1
        if used_targets[label] > 1:
            label = f"{label}-{used_targets[label]}"
        target_directory = label if directory == "." else f"{directory}/{label}"
        modules = set(cluster["modules"])
        moves = []
        external_importers: set[str] = set()
        string_reference_files: set[str] = set()
        rewrite_count = 0
        package_surface = False
        for module in sorted(modules):
            record = by_module[module]
            importers = graph.inbound.get(module, set())
            rewrite_count += len(importers)
            external_importers.update(
                by_module[user].relative_path
                for user in importers - modules
                if user in by_module
            )
            names = {record.module, *record.module_aliases}
            string_reference_files.update(
                other.relative_path
                for other in all_records
                if any(
                    value == name or value.startswith(name + ".")
                    for value in other.runtime_string_refs | other.dynamic_refs
                    for name in names
                )
            )
            package_surface = package_surface or record.possible_package_module
            moves.append(
                {
                    "from": record.relative_path,
                    "to": f"{target_directory}/{record.path.name}",
                    "module": record.module,
                }
            )
        risk = (
            "high"
            if string_reference_files
            else "medium"
            if rewrite_count > 8 or package_surface
            else "low"
        )
        plans.append(
            {
                "label": cluster["label"],
                "target_directory": target_directory,
                "moves": moves,
                "imports_to_rewrite": rewrite_count,
                "external_importers": sorted(external_importers),
                "string_reference_files": sorted(string_reference_files),
                "risk": risk,
                "apply_supported": False,
            }
        )
    return plans


def _estimated_entropy_gain(plans: list[dict[str, object]]) -> float:
    moved = sum(len(plan["moves"]) for plan in plans)
    return min(30.0, len(plans) * 5.0 + moved * 1.5)


def _safe_directory_name(label: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return normalized or "domain"
