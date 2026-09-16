"""Summarize a repo-gardener SARIF run as Markdown for CI job summaries."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

MAX_LISTED_RESULTS = 10


def summarize(document: dict[str, Any], limit: int = MAX_LISTED_RESULTS) -> str:
    run = (document.get("runs") or [{}])[0]
    results = run.get("results") or []
    properties = run.get("properties") or {}
    driver = (run.get("tool") or {}).get("driver") or {}
    lines = [
        f"## AI Repo Gardener {driver.get('version', '')}".rstrip(),
        "",
        (
            f"`{properties.get('command', 'scan')}` reported **{len(results)}** "
            f"finding(s) across {properties.get('pythonFiles', 0)} Python file(s)."
        ),
        "",
    ]
    accepted = properties.get("acceptedFindings")
    if isinstance(accepted, dict):
        lines.extend(
            [
                (
                    f"Accepted ledger `{accepted.get('source', '')}` suppressed "
                    f"{accepted.get('suppressed', 0)} finding(s); "
                    f"{len(accepted.get('unmatched') or [])} entry/entries no "
                    "longer reproduce and can be pruned."
                ),
                "",
            ]
        )
    if int(properties.get("parseErrors", 0)):
        lines.extend(
            [
                (
                    f"**{properties['parseErrors']} file(s) could not be "
                    "parsed.** Automatic deletion is disabled until that is "
                    "resolved."
                ),
                "",
            ]
        )
    if not results:
        lines.append("No findings at the selected confidence level.")
        return "\n".join(lines) + "\n"
    lines.extend(["| Level | Rule | Location | Detail |", "| --- | --- | --- | --- |"])
    for result in results[:limit]:
        level = str(result.get("level", "note"))
        location = _location(result)
        message = str((result.get("message") or {}).get("text", "")).replace("|", "\\|")
        lines.append(
            f"| {level} | `{result.get('ruleId')}` | `{location}` | {message} |"
        )
    if len(results) > limit:
        lines.append(f"| | | | and {len(results) - limit} more finding(s). |")
    lines.extend(["", "Findings are evidence for review, not permission to delete."])
    return "\n".join(lines) + "\n"


def _location(result: dict[str, Any]) -> str:
    physical = ((result.get("locations") or [{}])[0]).get("physicalLocation") or {}
    uri = (physical.get("artifactLocation") or {}).get("uri", "")
    start = (physical.get("region") or {}).get("startLine")
    return f"{uri}:{start}" if start else str(uri)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sarif", type=Path, help="SARIF 2.1.0 file to summarize")
    parser.add_argument(
        "--limit", type=int, default=MAX_LISTED_RESULTS, help="Listed findings"
    )
    arguments = parser.parse_args(argv)
    try:
        document = json.loads(arguments.sarif.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: unable to read SARIF file {arguments.sarif}: {exc}")
        return 2
    markdown = summarize(document, max(1, arguments.limit))
    print(markdown, end="")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
