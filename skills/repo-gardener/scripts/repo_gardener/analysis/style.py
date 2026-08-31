from __future__ import annotations

import statistics

from ..models import FileRecord, Finding

STRONG_SIGNALS = {
    "docstring_lines_per_function",
    "narration_comments_per_100_loc",
    "broad_exceptions_per_function",
    "nested_dicts_per_function",
    "mean_cyclomatic_complexity",
}
MIN_BASELINE_FILES = 5
HIGH_CONFIDENCE_BASELINE_FILES = 20
FEATURE_MIN_SUPPORT = {
    "builtin_generic_ratio": 2,
    "pep604_union_ratio": 2,
    "pathlib_ratio": 3,
    "comprehension_ratio": 3,
    "structured_model_ratio": 2,
    "print_share_of_output_calls": 3,
    "private_helper_ratio": 3,
    "snake_case_function_ratio": 3,
    "single_use_tiny_helper_ratio": 3,
    "wrapper_function_ratio": 3,
}


def style_findings(
    records: list[FileRecord], baseline_records: list[FileRecord] | None = None
) -> list[Finding]:
    sources = [
        record
        for record in records
        if record.category == "source"
        and not record.parse_error
        and record.style.loc >= 2
    ]
    baseline_sources = [
        record
        for record in (baseline_records or [])
        if record.category == "source"
        and not record.parse_error
        and record.style.loc >= 2
    ]
    if not baseline_sources and len(sources) < MIN_BASELINE_FILES + 1:
        return []
    all_records = [*sources, *baseline_sources]
    features = {id(record): record.style.features() for record in all_records}
    supports = {id(record): record.style.feature_supports() for record in all_records}
    findings: list[Finding] = []
    for record in sources:
        if not _eligible(record):
            continue
        peers = _peers(record, sources, baseline_sources)
        if len(peers) < MIN_BASELINE_FILES:
            continue
        unusual = _unusual_features(record, peers, features, supports)
        strong = any(item["type"] in STRONG_SIGNALS for item in unusual)
        if len(unusual) >= 2 or strong:
            findings.append(
                _finding(
                    record,
                    peers,
                    unusual,
                    "pre-ai-git" if baseline_sources else "repository-peers",
                )
            )
    return findings


def _eligible(record: FileRecord) -> bool:
    return (
        record.style.loc >= 5
        and record.style.functions > 0
        and record.path.name not in {"__init__.py", "__main__.py"}
    )


def _peers(
    record: FileRecord,
    sources: list[FileRecord],
    baseline_sources: list[FileRecord],
) -> list[FileRecord]:
    pool = baseline_sources or [peer for peer in sources if peer is not record]
    peers = [peer for peer in pool if peer.path.parent == record.path.parent]
    return peers if len(peers) >= MIN_BASELINE_FILES else pool


def _unusual_features(
    record: FileRecord,
    peers: list[FileRecord],
    features: dict[int, dict[str, float]],
    supports: dict[int, dict[str, int]],
) -> list[dict[str, object]]:
    unusual: list[dict[str, object]] = []
    for name, value in features[id(record)].items():
        if name == "print_calls_per_function" and (
            record.has_main_guard or record.path.stem.lower() == "cli"
        ):
            continue
        support = supports[id(record)][name]
        minimum_support = FEATURE_MIN_SUPPORT.get(name, 1)
        if support < minimum_support:
            continue
        supported_peers = [
            peer for peer in peers if supports[id(peer)][name] >= minimum_support
        ]
        if len(supported_peers) < MIN_BASELINE_FILES:
            continue
        baseline = [features[id(peer)][name] for peer in supported_peers]
        median = statistics.median(baseline)
        mad = statistics.median(abs(item - median) for item in baseline)
        scale = max(1.4826 * mad, _minimum_scale(name, median))
        z_score = abs(value - median) / scale
        prevalence = sum(item > 0 for item in baseline) / len(baseline)
        rare_positive = (
            value > _rare_threshold(name) and median == 0 and prevalence <= 0.2
        )
        meaningful_delta = abs(value - median) >= _minimum_effect(name)
        if (z_score >= 3.5 and meaningful_delta) or rare_positive:
            unusual.append(
                {
                    "type": name,
                    "value": round(value, 3),
                    "baseline_median": round(median, 3),
                    "robust_z": round(z_score, 2),
                    "support": support,
                    "baseline_supported_files": len(supported_peers),
                }
            )
    return unusual


def _finding(
    record: FileRecord,
    peers: list[FileRecord],
    unusual: list[dict[str, object]],
    baseline_mode: str,
) -> Finding:
    strength = sum(min(8.0, float(item["robust_z"])) for item in unusual)
    confidence = min(0.95, 0.50 + len(unusual) * 0.08 + strength * 0.012)
    if len(peers) < 8:
        confidence = min(confidence, 0.64)
    elif len(peers) < HIGH_CONFIDENCE_BASELINE_FILES:
        confidence = min(confidence, 0.84)
    return Finding(
        rule="style-drift",
        category="style",
        severity="warning" if confidence >= 0.85 else "info",
        confidence=confidence,
        risk=0.35,
        path=record.relative_path,
        evidence=[
            *unusual,
            {"type": "baseline_files", "value": len(peers)},
            {"type": "baseline_mode", "value": baseline_mode},
            {"type": "ai_authorship_proof", "value": False},
        ],
        risks=["repository_style_can_vary_by_domain"],
        recommendation="agent_review",
    ).finalize()


def _minimum_scale(name: str, median: float) -> float:
    if name.endswith("_ratio") or name == "print_share_of_output_calls":
        return max(0.10, abs(median) * 0.15)
    if "per_100_loc" in name:
        return max(0.35, abs(median) * 0.15)
    if "per_function" in name or name == "annotation_density":
        return max(0.25, abs(median) * 0.15)
    if name == "function_name_words_mean":
        return max(0.5, abs(median) * 0.15)
    return max(1.0, abs(median) * 0.15)


def _rare_threshold(name: str) -> float:
    return {
        "docstring_lines_per_function": 5.0,
        "narration_comments_per_100_loc": 2.0,
        "broad_exceptions_per_function": 0.25,
        "print_calls_per_function": 0.5,
        "nested_dicts_per_function": 0.25,
        "temporary_names_per_100_loc": 3.0,
        "annotation_density": 2.0,
        "median_function_loc": 35.0,
        "builtin_generic_ratio": 0.75,
        "legacy_typing_per_100_loc": 2.0,
        "pep604_union_ratio": 0.75,
        "legacy_union_per_100_loc": 1.0,
        "pathlib_ratio": 0.75,
        "os_path_calls_per_100_loc": 1.0,
        "comprehension_ratio": 0.75,
        "manual_loops_per_function": 1.0,
        "structured_model_ratio": 0.75,
        "bare_dict_models_per_function": 0.5,
        "print_share_of_output_calls": 0.75,
        "branch_points_per_function": 5.0,
        "mean_cyclomatic_complexity": 6.0,
        "high_complexity_function_ratio": 0.4,
        "private_helper_ratio": 0.75,
        "snake_case_function_ratio": 0.75,
        "function_name_words_mean": 4.0,
        "defensive_guards_per_function": 0.5,
        "single_use_tiny_helper_ratio": 0.75,
        "wrapper_function_ratio": 0.75,
        "log_then_reraise_per_function": 0.25,
        "redundant_temp_returns_per_function": 0.5,
        "mapping_get_calls_per_function": 2.0,
        "narration_logging_per_100_loc": 1.0,
    }[name]


def _minimum_effect(name: str) -> float:
    return {
        "docstring_lines_per_function": 3.0,
        "narration_comments_per_100_loc": 1.0,
        "broad_exceptions_per_function": 0.25,
        "print_calls_per_function": 0.5,
        "nested_dicts_per_function": 0.25,
        "temporary_names_per_100_loc": 2.0,
        "annotation_density": 1.0,
        "median_function_loc": 30.0,
        "builtin_generic_ratio": 0.35,
        "legacy_typing_per_100_loc": 1.0,
        "pep604_union_ratio": 0.35,
        "legacy_union_per_100_loc": 0.5,
        "pathlib_ratio": 0.35,
        "os_path_calls_per_100_loc": 0.5,
        "comprehension_ratio": 0.35,
        "manual_loops_per_function": 0.5,
        "structured_model_ratio": 0.35,
        "bare_dict_models_per_function": 0.5,
        "print_share_of_output_calls": 0.35,
        "branch_points_per_function": 2.0,
        "mean_cyclomatic_complexity": 2.0,
        "high_complexity_function_ratio": 0.3,
        "private_helper_ratio": 0.4,
        "snake_case_function_ratio": 0.35,
        "function_name_words_mean": 1.5,
        "defensive_guards_per_function": 0.5,
        "single_use_tiny_helper_ratio": 0.4,
        "wrapper_function_ratio": 0.4,
        "log_then_reraise_per_function": 0.25,
        "redundant_temp_returns_per_function": 0.5,
        "mapping_get_calls_per_function": 1.0,
        "narration_logging_per_100_loc": 0.75,
    }[name]
