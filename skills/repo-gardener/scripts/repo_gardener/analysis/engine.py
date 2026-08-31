from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..config import Config, load_config
from ..discovery import (
    classify_path,
    discover_python_files,
    is_configured_entrypoint,
    module_names,
)
from ..git_support import (
    change_coupling,
    changed_paths,
    import_migrations,
    is_git_repository,
    python_sources_at_commit,
    resolve_commit,
    resolve_git_ref,
)
from ..graph import ModuleGraph
from ..models import FileRecord, Finding, Report
from ..packaging_metadata import discover_packaging_metadata
from ..parsing import parse_file, parse_source, populate_style
from .dependencies import dependency_leftover_findings
from .stale import stale_findings
from .structure import StructureAnalysis, analyze_structure
from .style import style_findings
from .symbols import symbol_gc_findings


class Analyzer:
    def __init__(self, root: Path, config_path: Path | None = None):
        self.root = root.resolve()
        self.config: Config = load_config(self.root, config_path)
        packaging = discover_packaging_metadata(self.root, self.config)
        entrypoint_modules = set(packaging.entrypoint_modules)
        self.records = self._load_records(entrypoint_modules)
        for record in self.records:
            record.packaged_public_module = bool(
                {record.module, *record.module_aliases} & packaging.public_modules
            )
            record.possible_package_module = packaging.is_namespace_module(
                self.root, record.relative_path
            )
            record.packaging_uncertainty = packaging.uncertainty_sources
        self.graph = ModuleGraph(self.records, entrypoint_modules)

    def report(
        self,
        command: str,
        base: str | None = None,
        experimental: bool = False,
        style_baseline: str | None = None,
    ) -> Report:
        modes = {
            "scan": ("stale",),
            "stale": ("stale",),
            "structure": ("structure",),
            "style": ("style",),
            "diff": ("stale",),
        }[command]
        if experimental and command in {"scan", "diff"}:
            modes = (*modes, "structure", "style")
        if "style" in modes:
            for record in self.records:
                populate_style(record)
        repository_has_git = is_git_repository(self.root)
        if base and not repository_has_git:
            raise ValueError("--base requires a Git repository")
        if base and resolve_git_ref(self.root, base) is None:
            raise ValueError(f"Git base cannot be resolved: {base}")
        git_available = bool(base) and repository_has_git
        migrations = (
            import_migrations(self.root, base, self.records)
            if git_available and base
            else []
        )
        changed = changed_paths(self.root, base) if git_available and base else set()
        baseline_commit = None
        baseline_records: list[FileRecord] = []
        if "style" in modes and style_baseline:
            baseline_commit = resolve_commit(self.root, style_baseline)
            if baseline_commit is None:
                raise ValueError(
                    f"style baseline is not a commit or resolvable date: {style_baseline}"
                )
            baseline_records = self._load_historical_records(baseline_commit)
        structure_analysis = None
        if "structure" in modes:
            source_paths = {
                record.relative_path
                for record in self.records
                if record.category == "source"
            }
            affinity = (
                change_coupling(self.root, source_paths) if repository_has_git else {}
            )
            structure_analysis = analyze_structure(
                self.records,
                self.graph,
                self.config,
                affinity,
            )
        findings = self._run_modes(
            modes,
            migrations,
            changed,
            baseline_records,
            structure_analysis,
        )
        if command == "diff":
            findings = _scope_to_diff(findings, changed)
        metrics = {
            "python_files": len(self.records),
            "source_files": sum(record.category == "source" for record in self.records),
            "entrypoints": sorted(self.graph.roots),
            "framework_entrypoints": {
                record.relative_path: list(record.framework_entrypoints)
                for record in self.records
                if record.framework_entrypoints
            },
            "reachable_modules": len(self.graph.reachable),
            "parse_errors": sum(
                record.parse_error is not None for record in self.records
            ),
            "parse_error_files": sorted(
                record.relative_path
                for record in self.records
                if record.parse_error is not None
            ),
            "experimental_analysis": experimental,
        }
        if base:
            metrics["changed_files"] = len(changed)
        if baseline_commit:
            metrics["style_baseline_commit"] = baseline_commit
        if "style" in modes:
            metrics["style_baseline_mode"] = (
                "pre-ai-git" if baseline_commit else "repository-peers"
            )
        if structure_analysis is not None:
            metrics["structure_entropy"] = structure_analysis.metrics
        return Report(
            command=command,
            root=self.root,
            findings=findings,
            metrics=metrics,
            base=base,
        )

    def _load_records(self, entrypoint_modules: set[str]) -> list[FileRecord]:
        specifications = []
        used_modules: set[str] = set()
        for path in discover_python_files(self.root, self.config):
            relative = path.relative_to(self.root).as_posix()
            aliases = module_names(self.root, path)
            module = next(
                (name for name in aliases if name not in used_modules), aliases[0]
            )
            used_modules.add(module)
            specifications.append(
                (
                    path,
                    relative,
                    module,
                    classify_path(self.root, path),
                    tuple(name for name in aliases if name != module),
                )
            )
        if len(specifications) >= 32:
            with ThreadPoolExecutor(max_workers=min(8, len(specifications))) as pool:
                records = list(pool.map(_parse_specification, specifications))
        else:
            records = [_parse_specification(item) for item in specifications]
        for record in records:
            if is_configured_entrypoint(
                record.relative_path,
                record.module,
                record.module_aliases,
                self.config,
                entrypoint_modules,
            ):
                record.has_main_guard = True
        return records

    def _load_historical_records(self, commit: str) -> list[FileRecord]:
        records: list[FileRecord] = []
        used_modules: set[str] = set()
        for relative, source in python_sources_at_commit(self.root, commit).items():
            if self.config.is_excluded(relative):
                continue
            path = self.root / relative
            aliases = module_names(self.root, path)
            module = next(
                (name for name in aliases if name not in used_modules), aliases[0]
            )
            used_modules.add(module)
            records.append(
                parse_source(
                    source,
                    path,
                    relative,
                    module,
                    classify_path(self.root, path),
                    tuple(name for name in aliases if name != module),
                    collect_style=True,
                )
            )
        return records

    def _run_modes(
        self,
        modes: tuple[str, ...],
        migrations,
        changed: set[str],
        baseline_records: list[FileRecord],
        structure_analysis: StructureAnalysis | None,
    ) -> list[Finding]:
        findings: list[Finding] = []
        if "stale" in modes:
            findings.extend(
                stale_findings(
                    self.records,
                    self.graph,
                    self.config,
                    self.root,
                    migrations,
                    changed,
                )
            )
            findings.extend(symbol_gc_findings(self.records, self.graph, changed))
            findings.extend(
                dependency_leftover_findings(
                    self.root,
                    self.records,
                    changed,
                    migrations,
                )
            )
        if "structure" in modes:
            findings.extend(
                structure_analysis.findings if structure_analysis is not None else []
            )
        if "style" in modes:
            findings.extend(style_findings(self.records, baseline_records))
        return findings


def _scope_to_diff(findings: list[Finding], changed: set[str]) -> list[Finding]:
    scoped: list[Finding] = []
    for finding in findings:
        path = finding.path.rstrip("./")
        direct = finding.path in changed or (
            finding.replacement is not None and finding.replacement in changed
        )
        under_directory = finding.rule == "flat-directory" and any(
            changed_path.startswith(path + "/") if path else True
            for changed_path in changed
        )
        migration = any(
            item.get("type") == "call_site_migration" for item in finding.evidence
        )
        related = any(
            changed_path in item.get("value", [])
            for item in finding.evidence
            if item.get("type") == "related_changed_paths"
            and isinstance(item.get("value"), list)
            for changed_path in changed
        )
        if direct or under_directory or migration or related:
            scoped.append(finding)
    return scoped


def _parse_specification(specification) -> FileRecord:
    path, relative, module, category, aliases = specification
    return parse_file(path, relative, module, category, aliases)
