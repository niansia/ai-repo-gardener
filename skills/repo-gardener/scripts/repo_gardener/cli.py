from __future__ import annotations

import argparse
import sys
from math import isfinite
from pathlib import Path

from . import __version__
from .analysis import Analyzer
from .fixes import FixError, apply_deletions, restore_last, safe_candidates
from .plans import build_plan, load_reviewed_plan, require_matching_plan
from .reporting import render_fix_json, render_fix_plan, render_json, render_pretty


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return seconds


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo-gardener",
        description=(
            "Find AI iteration leftovers, architecture pressure, and house-style "
            "drift with deterministic evidence."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"repo-gardener {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command, help_text in [
        ("scan", "Run the supported repo-GC analysis"),
        ("stale", "Find file, symbol, duplicate, and dependency leftovers"),
        ("structure", "Inspect structure entropy and review-only move plans"),
        ("style", "Inspect baseline-relative Python house-style drift"),
        ("diff", "Audit repo-GC findings associated with a Git iteration"),
    ]:
        subparser = subparsers.add_parser(command, help=help_text)
        _analysis_arguments(subparser)
        if command == "diff":
            subparser.add_argument(
                "--base",
                default="HEAD",
                help="Git ref that predates the agent iteration",
            )
        if command in {"scan", "diff"}:
            subparser.add_argument(
                "--experimental",
                action="store_true",
                help="Also run opt-in, review-only structure and style analysis",
            )
        if command in {"scan", "style", "diff"}:
            subparser.add_argument(
                "--baseline",
                help="Pre-AI style baseline commit or date (for example 2026-01-15)",
            )

    fix = subparsers.add_parser(
        "fix", help="Preview, apply, or restore safe stale-file deletions"
    )
    fix.add_argument("path", nargs="?", default=".", help="Repository root")
    fix.add_argument("--config", type=Path, help="Config file path")
    fix.add_argument(
        "--base",
        help="Git ref that predates the agent iteration (defaults to HEAD)",
    )
    mode = fix.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        help="EXPERIMENTAL: apply the reviewed safe deletion plan",
    )
    mode.add_argument(
        "--dry-run", action="store_true", help="Preview only (the default)"
    )
    mode.add_argument(
        "--restore", action="store_true", help="Restore the last applied operation"
    )
    fix.add_argument(
        "--safe",
        action="store_true",
        help="Compatibility flag; fixes are always restricted to safe candidates",
    )
    fix.add_argument(
        "--validate",
        action="append",
        default=[],
        metavar="COMMAND",
        help="Validation command; repeatable",
    )
    fix.add_argument(
        "--validation-timeout",
        type=_positive_seconds,
        default=300.0,
        metavar="SECONDS",
        help="Timeout for each validation command (default: 300)",
    )
    fix.add_argument("--format", choices=("pretty", "json"), default="pretty")
    fix.add_argument(
        "--plan",
        type=Path,
        help="Reviewed dry-run JSON plan; required with --apply",
    )
    fix.add_argument(
        "--trust-repo-config",
        action="store_true",
        help="Allow validation commands read from the target repository config",
    )
    return parser


def _analysis_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", nargs="?", default=".", help="Repository root")
    parser.add_argument("--config", type=Path, help="Config file path")
    parser.add_argument("--format", choices=("pretty", "json"), default="pretty")
    parser.add_argument(
        "--confidence", choices=("high", "medium", "all"), default="medium"
    )
    parser.add_argument(
        "--fail-on",
        choices=("high", "medium", "any"),
        help="Exit 1 when a finding reaches this confidence tier",
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.path).resolve()
    if not root.is_dir():
        return _error(f"repository path does not exist: {root}")
    if args.command == "fix" and args.restore:
        if args.plan:
            return _error("--plan cannot be combined with --restore")
        return _run_restore(root)
    try:
        analyzer = Analyzer(root, args.config)
    except (OSError, ValueError) as exc:
        return _error(f"unable to analyze repository: {exc}")
    if args.command == "fix":
        return _run_fix(analyzer, root, args)
    return _run_analysis(analyzer, args)


def _run_restore(root: Path) -> int:
    try:
        manifest = restore_last(root)
    except (FixError, OSError, ValueError) as exc:
        return _error(str(exc))
    print(
        f"Restored operation {manifest['operation_id']} "
        f"({len(manifest.get('files', []))} files)."
    )
    return 0


def _run_fix(analyzer: Analyzer, root: Path, args: argparse.Namespace) -> int:
    if args.plan and not args.apply:
        return _error("--plan is only valid with --apply")
    if args.apply and not args.plan:
        return _error("--apply requires a reviewed JSON plan via --plan")
    try:
        reviewed = load_reviewed_plan(args.plan) if args.plan else None
    except FixError as exc:
        return _error(str(exc))
    base = args.base or (str(reviewed["base_ref"]) if reviewed else "HEAD")
    try:
        report = analyzer.report("stale", base)
        candidates = safe_candidates(report.findings)
        blockers = _automatic_deletion_blockers(analyzer)
        plan = build_plan(
            root,
            candidates,
            base,
            analyzer.config,
            args.apply,
            blockers,
        )
        if reviewed:
            require_matching_plan(reviewed, plan)
    except (FixError, ValueError) as exc:
        return _error(str(exc))
    if args.format == "pretty":
        print(
            render_fix_plan(
                candidates,
                str(root),
                args.apply,
                str(plan["plan_id"]),
                blockers,
            )
        )
        if analyzer.config.validation_commands and not args.trust_repo_config:
            print(
                "\nRepository-configured validation commands are ignored unless "
                "--trust-repo-config is supplied."
            )
    if not args.apply or not candidates:
        if args.format == "json":
            print(render_fix_json(plan))
        return 0
    commands = list(args.validate)
    if args.trust_repo_config:
        commands = [*analyzer.config.validation_commands, *commands]
    elif analyzer.config.validation_commands and not commands:
        return _error(
            "repository-configured validation commands are untrusted; pass explicit "
            "--validate commands or knowingly opt in with --trust-repo-config"
        )
    try:
        manifest = apply_deletions(
            root,
            candidates,
            commands,
            reviewed,
            args.validation_timeout,
        )
    except (FixError, OSError) as exc:
        return _error(str(exc))
    if args.format == "json":
        print(render_fix_json(plan, manifest))
    else:
        print(f"\nApplied operation {manifest['operation_id']}; validation passed.")
    return 0


def _run_analysis(analyzer: Analyzer, args: argparse.Namespace) -> int:
    if (
        getattr(args, "baseline", None)
        and args.command in {"scan", "diff"}
        and not getattr(args, "experimental", False)
    ):
        return _error("--baseline requires --experimental on scan or diff")
    try:
        report = analyzer.report(
            args.command,
            getattr(args, "base", None),
            getattr(args, "experimental", False),
            getattr(args, "baseline", None),
        )
    except ValueError as exc:
        return _error(str(exc))
    output = (
        render_json(report, args.confidence)
        if args.format == "json"
        else render_pretty(report, args.confidence)
    )
    print(output)
    minimum = {"high": 0.85, "medium": 0.65, "any": 0.0}.get(args.fail_on)
    return (
        1
        if minimum is not None
        and any(finding.confidence >= minimum for finding in report.findings)
        else 0
    )


def _error(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def _automatic_deletion_blockers(analyzer: Analyzer) -> list[str]:
    blockers: list[str] = []
    parse_errors = sorted(
        record.relative_path for record in analyzer.records if record.parse_error
    )
    if parse_errors:
        blockers.append(
            f"{len(parse_errors)} Python file(s) could not be parsed: "
            + ", ".join(parse_errors[:3])
        )
    opaque = sorted(
        record.relative_path
        for record in analyzer.records
        if record.opaque_dynamic_discovery
    )
    if opaque:
        blockers.append("opaque runtime module discovery: " + ", ".join(opaque[:3]))
    packaging = sorted(
        {
            source
            for record in analyzer.records
            for source in record.packaging_uncertainty
        }
    )
    if packaging:
        blockers.append("unresolved packaging metadata: " + ", ".join(packaging[:3]))
    return blockers


if __name__ == "__main__":
    raise SystemExit(main())
