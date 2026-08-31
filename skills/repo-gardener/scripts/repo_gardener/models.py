from __future__ import annotations

import ast
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ImportRef:
    module: str
    names: tuple[str, ...] = ()
    conditional: bool = False
    type_checking: bool = False


@dataclass
class StyleMetrics:
    loc: int = 0
    functions: int = 0
    docstring_lines: int = 0
    narration_comments: int = 0
    comments: int = 0
    broad_exceptions: int = 0
    print_calls: int = 0
    nested_dicts: int = 0
    temporary_names: int = 0
    annotations: int = 0
    function_median_loc: float = 0.0
    builtin_generic_annotations: int = 0
    legacy_generic_annotations: int = 0
    pep604_unions: int = 0
    legacy_optional_unions: int = 0
    pathlib_uses: int = 0
    os_path_uses: int = 0
    comprehensions: int = 0
    for_loops: int = 0
    structured_models: int = 0
    bare_dict_annotations: int = 0
    logging_calls: int = 0
    branch_points: int = 0
    cyclomatic_complexity: int = 0
    high_complexity_functions: int = 0
    top_level_functions: int = 0
    private_helpers: int = 0
    snake_case_functions: int = 0
    function_name_words: int = 0
    defensive_guards: int = 0
    single_use_tiny_helpers: int = 0
    wrapper_functions: int = 0
    log_then_reraise_handlers: int = 0
    redundant_temp_returns: int = 0
    mapping_get_calls: int = 0
    narration_logging_calls: int = 0

    def features(self) -> dict[str, float]:
        funcs = max(self.functions, 1)
        loc = max(self.loc, 1)
        generics = self.builtin_generic_annotations + self.legacy_generic_annotations
        unions = self.pep604_unions + self.legacy_optional_unions
        paths = self.pathlib_uses + self.os_path_uses
        iterations = self.comprehensions + self.for_loops
        models = self.structured_models + self.bare_dict_annotations
        output_calls = self.logging_calls + self.print_calls
        return {
            "docstring_lines_per_function": self.docstring_lines / funcs,
            "narration_comments_per_100_loc": self.narration_comments * 100 / loc,
            "broad_exceptions_per_function": self.broad_exceptions / funcs,
            "print_calls_per_function": self.print_calls / funcs,
            "nested_dicts_per_function": self.nested_dicts / funcs,
            "temporary_names_per_100_loc": self.temporary_names * 100 / loc,
            "annotation_density": self.annotations / funcs,
            "median_function_loc": self.function_median_loc,
            "builtin_generic_ratio": self.builtin_generic_annotations
            / max(generics, 1),
            "legacy_typing_per_100_loc": self.legacy_generic_annotations * 100 / loc,
            "pep604_union_ratio": self.pep604_unions / max(unions, 1),
            "legacy_union_per_100_loc": self.legacy_optional_unions * 100 / loc,
            "pathlib_ratio": self.pathlib_uses / max(paths, 1),
            "os_path_calls_per_100_loc": self.os_path_uses * 100 / loc,
            "comprehension_ratio": self.comprehensions / max(iterations, 1),
            "manual_loops_per_function": self.for_loops / funcs,
            "structured_model_ratio": self.structured_models / max(models, 1),
            "bare_dict_models_per_function": self.bare_dict_annotations / funcs,
            "print_share_of_output_calls": self.print_calls / max(output_calls, 1),
            "branch_points_per_function": self.branch_points / funcs,
            "mean_cyclomatic_complexity": self.cyclomatic_complexity / funcs,
            "high_complexity_function_ratio": self.high_complexity_functions / funcs,
            "private_helper_ratio": self.private_helpers
            / max(self.top_level_functions, 1),
            "snake_case_function_ratio": self.snake_case_functions / funcs,
            "function_name_words_mean": self.function_name_words / funcs,
            "defensive_guards_per_function": self.defensive_guards / funcs,
            "single_use_tiny_helper_ratio": self.single_use_tiny_helpers
            / max(self.top_level_functions, 1),
            "wrapper_function_ratio": self.wrapper_functions / funcs,
            "log_then_reraise_per_function": self.log_then_reraise_handlers / funcs,
            "redundant_temp_returns_per_function": self.redundant_temp_returns / funcs,
            "mapping_get_calls_per_function": self.mapping_get_calls / funcs,
            "narration_logging_per_100_loc": self.narration_logging_calls * 100 / loc,
        }

    def feature_supports(self) -> dict[str, int]:
        """Return the observation count behind each derived style feature."""
        generics = self.builtin_generic_annotations + self.legacy_generic_annotations
        unions = self.pep604_unions + self.legacy_optional_unions
        paths = self.pathlib_uses + self.os_path_uses
        iterations = self.comprehensions + self.for_loops
        models = self.structured_models + self.bare_dict_annotations
        output_calls = self.logging_calls + self.print_calls
        return {
            "docstring_lines_per_function": self.functions,
            "narration_comments_per_100_loc": self.loc,
            "broad_exceptions_per_function": self.functions,
            "print_calls_per_function": self.functions,
            "nested_dicts_per_function": self.functions,
            "temporary_names_per_100_loc": self.loc,
            "annotation_density": self.functions,
            "median_function_loc": self.functions,
            "builtin_generic_ratio": generics,
            "legacy_typing_per_100_loc": self.loc,
            "pep604_union_ratio": unions,
            "legacy_union_per_100_loc": self.loc,
            "pathlib_ratio": paths,
            "os_path_calls_per_100_loc": self.loc,
            "comprehension_ratio": iterations,
            "manual_loops_per_function": self.functions,
            "structured_model_ratio": models,
            "bare_dict_models_per_function": self.functions,
            "print_share_of_output_calls": output_calls,
            "branch_points_per_function": self.functions,
            "mean_cyclomatic_complexity": self.functions,
            "high_complexity_function_ratio": self.functions,
            "private_helper_ratio": self.top_level_functions,
            "snake_case_function_ratio": self.functions,
            "function_name_words_mean": self.functions,
            "defensive_guards_per_function": self.functions,
            "single_use_tiny_helper_ratio": self.top_level_functions,
            "wrapper_function_ratio": self.functions,
            "log_then_reraise_per_function": self.functions,
            "redundant_temp_returns_per_function": self.functions,
            "mapping_get_calls_per_function": self.functions,
            "narration_logging_per_100_loc": self.loc,
        }


@dataclass(frozen=True)
class SymbolRecord:
    name: str
    kind: str
    lineno: int
    end_lineno: int
    private: bool
    decorated: bool
    normalized_body_hash: str
    body_nodes: int
    parameter_count: int


@dataclass
class FileRecord:
    path: Path
    relative_path: str
    module: str
    category: str
    source: str
    module_aliases: tuple[str, ...] = ()
    imports: list[ImportRef] = field(default_factory=list)
    symbols: set[str] = field(default_factory=set)
    symbol_details: tuple[SymbolRecord, ...] = ()
    exported_symbols: set[str] = field(default_factory=set)
    public_surface: dict[str, str] = field(default_factory=dict)
    public_assignments: set[str] = field(default_factory=set)
    dynamic_refs: set[str] = field(default_factory=set)
    opaque_dynamic_discovery: bool = False
    vocabulary: set[str] = field(default_factory=set)
    structural_tokens: tuple[str, ...] = ()
    normalized_tokens: tuple[str, ...] = ()
    style: StyleMetrics = field(default_factory=StyleMetrics)
    has_main_guard: bool = False
    framework_entrypoints: tuple[str, ...] = ()
    declares_public_api: bool = False
    possible_package_module: bool = False
    packaged_public_module: bool = False
    packaging_uncertainty: tuple[str, ...] = ()
    runtime_string_refs: set[str] = field(default_factory=set)
    parse_error: str | None = None
    mtime: float = 0.0
    tree: ast.Module | None = field(default=None, repr=False)
    parse_cache_hit: bool = False


@dataclass
class Finding:
    rule: str
    category: str
    severity: str
    confidence: float
    risk: float
    path: str
    replacement: str | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    recommendation: str = "review"
    id: str = ""

    def finalize(self) -> Finding:
        payload = "|".join(
            [
                self.rule,
                self.path.replace("\\", "/"),
                self.replacement or "",
                repr(
                    sorted(
                        (item.get("type"), item.get("value")) for item in self.evidence
                    )
                ),
            ]
        )
        self.id = f"{self.rule}:{sha256(payload.encode('utf-8')).hexdigest()[:12]}"
        self.confidence = round(max(0.0, min(1.0, self.confidence)), 3)
        self.risk = round(max(0.0, min(1.0, self.risk)), 3)
        self.evidence.sort(
            key=lambda item: (str(item.get("type", "")), repr(item.get("value")))
        )
        self.risks = sorted(set(self.risks))
        return self

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.replacement is None:
            data.pop("replacement")
        return data


@dataclass
class Report:
    command: str
    root: Path
    findings: list[Finding]
    metrics: dict[str, Any]
    base: str | None = None
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        ordered = sorted(
            self.findings,
            key=lambda finding: (-finding.confidence, finding.rule, finding.path),
        )
        by_rule: dict[str, int] = {}
        by_confidence = {"high": 0, "medium": 0, "low": 0}
        for finding in ordered:
            by_rule[finding.rule] = by_rule.get(finding.rule, 0) + 1
            tier = (
                "high"
                if finding.confidence >= 0.85
                else "medium"
                if finding.confidence >= 0.65
                else "low"
            )
            by_confidence[tier] += 1
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "command": self.command,
            "root": str(self.root),
            "summary": {
                "findings": len(ordered),
                "by_confidence": by_confidence,
                "by_rule": dict(sorted(by_rule.items())),
            },
            "metrics": self.metrics,
            "findings": [finding.to_dict() for finding in ordered],
        }
        if self.base is not None:
            result["base"] = self.base
        return result
