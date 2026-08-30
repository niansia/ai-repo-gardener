from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .analysis import Analyzer
from .fixes import FixError, apply_deletions, restore_last, safe_candidates
from .reporting import render_fix_plan, render_json, render_pretty


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo-gardener",
        description="Find AI iteration leftovers with deterministic evidence.",
    )
    parser.add_argument("--version", action="version", version="repo-gardener 0.1.0")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command, help_text in [
        ("scan", "Run the supported repo-GC analysis"),
        ("stale", "Find evidence-backed superseded Python files"),
        ("structure", "Experimentally inspect flat directories and clusters"),
        ("style", "Experimentally inspect Python house-style drift"),
        ("diff", "Audit repo-GC findings associated with a Git iteration"),
    ]:
        subparser = subparsers.add_parser(command, help=help_text)
        _analysis_arguments(subparser)
        if command == "diff":
            subparser.add_argument(
                "--base",
                required=True,
                help="Git ref that predates the agent iteration",
            )
        if command in {"scan", "diff"}:
            subparser.add_argument(
                "--experimental",
                action="store_true",
                help="Also run experimental structure and style analysis",
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
        help="Git ref that predates the agent iteration; use the same base as diff",
    )
    mode = fix.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply", action="store_true", help="Apply the reviewed safe deletion plan"
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
    return parser


def _analysis_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", nargs="?", default=".", help="Repository root")
    parser.add_argument("--config", type=Path, help="Config file path")
    parser.add_argument("--format", choices=("pretty", "json"), default="pretty")
    parser.add_argument(
        "--confidence", choices=("high", "medium", "all"), default="medium"
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.path).resolve()
    if not root.is_dir():
        return _error(f"repository path does not exist: {root}")
    if args.command == "fix" and args.restore:
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
    try:
        report = analyzer.report("stale", args.base)
    except ValueError as exc:
        return _error(str(exc))
    candidates = safe_candidates(report.findings)
    print(render_fix_plan(candidates, str(root), args.apply))
    if not args.apply or not candidates:
        return 0
    commands = [*analyzer.config.validation_commands, *args.validate]
    try:
        manifest = apply_deletions(root, candidates, commands)
    except (FixError, OSError) as exc:
        return _error(str(exc))
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
    return 0


def _error(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
