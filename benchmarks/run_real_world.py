from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPOSITORIES_FILE = Path(__file__).with_name("real-world-repos.json")
MODES = ("scan-cold", "scan-warm", "diff", "structure", "style")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the pinned AI Repo Gardener real-world benchmark."
    )
    parser.add_argument("--repos", nargs="*", help="Subset of pinned repository names")
    parser.add_argument("--modes", nargs="*", choices=MODES, default=list(MODES))
    parser.add_argument("--output", type=Path, help="Write the JSON result here")
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument(
        "--workspace",
        type=Path,
        help="Use an empty persistent workspace instead of a temporary directory",
    )
    arguments = parser.parse_args()
    repositories = json.loads(REPOSITORIES_FILE.read_text(encoding="utf-8"))
    selected = arguments.repos or sorted(repositories)
    unknown = sorted(set(selected) - set(repositories))
    if unknown:
        parser.error("unknown repositories: " + ", ".join(unknown))
    if arguments.timeout <= 0:
        parser.error("--timeout must be greater than zero")

    if arguments.workspace:
        workspace = arguments.workspace.resolve()
        if workspace.exists() and any(workspace.iterdir()):
            parser.error("--workspace must be empty")
        workspace.mkdir(parents=True, exist_ok=True)
        return _run(workspace, repositories, selected, arguments)
    with tempfile.TemporaryDirectory(prefix="repo-gardener-benchmark-") as raw:
        return _run(Path(raw), repositories, selected, arguments)


def _run(
    workspace: Path,
    repositories: dict[str, dict[str, str]],
    selected: list[str],
    arguments: argparse.Namespace,
) -> int:
    results: dict[str, Any] = {
        "schema_version": 1,
        "measured_at": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "repo_gardener_version": _tool_version(),
        "modes": arguments.modes,
        "repositories": {},
    }
    failed = False
    for name in selected:
        specification = repositories[name]
        checkout = workspace / "repos" / name
        cache = workspace / "cache" / name
        _clone(checkout, specification)
        repository_result = {
            "url": specification["url"],
            "commit": specification["commit"],
            "runs": {},
        }
        for mode in arguments.modes:
            run = _measure(checkout, cache, mode, arguments.timeout)
            repository_result["runs"][mode] = run
            failed = failed or "error" in run
            print(
                f"{name:10} {mode:10} {run['wall_seconds']:8.2f}s "
                f"files={run.get('python_files', '-')} "
                f"hits={run.get('parse_cache_hits', '-')} "
                f"findings={run.get('findings', '-')}",
                flush=True,
            )
        results["repositories"][name] = repository_result
    rendered = json.dumps(results, indent=2, sort_keys=True) + "\n"
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)
    return 1 if failed else 0


def _clone(checkout: Path, specification: dict[str, str]) -> None:
    checkout.parent.mkdir(parents=True, exist_ok=True)
    checkout.mkdir()
    subprocess.run(["git", "-C", str(checkout), "init", "--quiet"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "remote",
            "add",
            "origin",
            specification["url"],
        ],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "fetch",
            "--depth=2",
            "origin",
            specification["commit"],
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(checkout), "checkout", "--detach", "FETCH_HEAD"],
        check=True,
    )


def _measure(
    checkout: Path, cache: Path, mode: str, timeout: float
) -> dict[str, object]:
    if mode == "scan-cold" and cache.exists():
        shutil.rmtree(cache)
    cache.mkdir(parents=True, exist_ok=True)
    command_name = "scan" if mode.startswith("scan-") else mode
    command = [
        sys.executable,
        "-m",
        "repo_gardener",
        command_name,
        str(checkout),
        "--format",
        "json",
        "--confidence",
        "all",
    ]
    if command_name == "diff":
        command.extend(("--base", "HEAD~1"))
    environment = os.environ.copy()
    source_root = str(ROOT / "skills" / "repo-gardener" / "scripts")
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (source_root, environment.get("PYTHONPATH", "")) if value
    )
    environment["REPO_GARDENER_CACHE_DIR"] = str(cache)
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "wall_seconds": round(time.perf_counter() - started, 3),
            "error": f"timed out after {timeout:g} seconds",
            "stdout_tail": str(exc.stdout or "")[-1000:],
            "stderr_tail": str(exc.stderr or "")[-1000:],
        }
    elapsed = round(time.perf_counter() - started, 3)
    if completed.returncode != 0:
        return {
            "wall_seconds": elapsed,
            "error": f"exit code {completed.returncode}",
            "stdout_tail": completed.stdout[-1000:],
            "stderr_tail": completed.stderr[-1000:],
        }
    payload = json.loads(completed.stdout)
    metrics = payload.get("metrics", {})
    summary = payload.get("summary", {})
    return {
        "wall_seconds": elapsed,
        "python_files": metrics.get("python_files", 0),
        "parse_cache_hits": metrics.get("parse_cache_hits", 0),
        "findings": len(payload.get("findings", [])),
        "safe_delete_candidates": sum(
            finding.get("recommendation") == "safe_delete_candidate"
            for finding in payload.get("findings", [])
        ),
        "by_rule": summary.get("by_rule", {}),
        "high": summary.get("by_confidence", {}).get("high", 0),
        "medium": summary.get("by_confidence", {}).get("medium", 0),
        "low": summary.get("by_confidence", {}).get("low", 0),
        "parse_errors": metrics.get("parse_errors", 0),
    }


def _tool_version() -> str:
    package_root = ROOT / "skills" / "repo-gardener" / "scripts"
    sys.path.insert(0, str(package_root))
    from repo_gardener import __version__

    return __version__


if __name__ == "__main__":
    raise SystemExit(main())
