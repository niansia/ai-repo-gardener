from __future__ import annotations

from collections import Counter, defaultdict, deque
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


def structure_findings(
    records: list[FileRecord], graph: ModuleGraph, config: Config
) -> list[Finding]:
    by_directory: dict[str, list[FileRecord]] = defaultdict(list)
    for record in records:
        if record.category == "source" and not record.parse_error:
            directory = str(Path(record.relative_path).parent).replace("\\", "/")
            by_directory[directory].append(record)
    findings: list[Finding] = []
    for directory, members in sorted(by_directory.items()):
        finding = _directory_finding(directory, members, graph, config)
        if finding is not None:
            findings.append(finding)
    return findings


def _directory_finding(
    directory: str,
    records: list[FileRecord],
    graph: ModuleGraph,
    config: Config,
) -> Finding | None:
    if len(records) < config.flat_directory_threshold:
        return None
    clusters = _clusters(records, graph)
    generic = sorted(
        record.relative_path
        for record in records
        if record.path.stem.lower() in GENERIC_MODULES
    )
    credible_clusters = [
        cluster
        for cluster in clusters
        if len(cluster["files"]) <= max(3, int(len(records) * 0.60))
    ]
    has_partition = len(credible_clusters) >= 2
    if not has_partition and not generic:
        return None
    excess = len(records) - config.flat_directory_threshold + 1
    confidence = (
        min(0.90, 0.60 + excess * 0.02 + len(credible_clusters) * 0.025)
        if has_partition
        else 0.60
    )
    return Finding(
        rule="flat-directory",
        category="structure",
        severity="warning" if has_partition else "info",
        confidence=confidence,
        risk=0.55,
        path=directory,
        evidence=[
            {"type": "direct_python_modules", "value": len(records)},
            {"type": "probable_clusters", "value": credible_clusters},
            {"type": "generic_modules", "value": generic},
            {
                "type": "cluster_proposal_available",
                "value": has_partition,
            },
        ],
        risks=[
            "module_moves_require_import_rewrites",
            "string_module_paths_may_break",
        ],
        recommendation="proposal_only" if has_partition else "review_directory_load",
    ).finalize()


def _clusters(records: list[FileRecord], graph: ModuleGraph) -> list[dict[str, object]]:
    modules = {record.module: record for record in records}
    adjacency = _cohesion_graph(modules, graph)
    groups = _connected_groups(adjacency)
    result: list[dict[str, object]] = []
    for group in groups:
        words = Counter(
            word
            for module in group
            for word in modules[module].vocabulary
            if word not in GENERIC_MODULES
        )
        label = words.most_common(1)[0][0] if words else "module-group"
        result.append(
            {
                "label": label,
                "files": sorted(modules[module].relative_path for module in group),
            }
        )
    return sorted(result, key=lambda item: (str(item["label"]), repr(item["files"])))


def _cohesion_graph(
    modules: dict[str, FileRecord], graph: ModuleGraph
) -> dict[str, set[str]]:
    adjacency: dict[str, set[str]] = {module: set() for module in modules}
    for module in modules:
        if module in graph.roots:
            continue
        for target in graph.edges.get(module, set()):
            if target in modules:
                adjacency[module].add(target)
                adjacency[target].add(module)
    return adjacency


def _connected_groups(adjacency: dict[str, set[str]]) -> list[set[str]]:
    unseen = set(adjacency)
    groups: list[set[str]] = []
    while unseen:
        start = min(unseen)
        component: set[str] = set()
        queue = deque([start])
        while queue:
            module = queue.popleft()
            if module in component:
                continue
            component.add(module)
            queue.extend(sorted(adjacency[module] - component))
        unseen -= component
        if len(component) >= 2:
            groups.append(component)
    return groups
