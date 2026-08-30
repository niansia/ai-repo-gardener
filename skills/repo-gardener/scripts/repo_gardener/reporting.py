from __future__ import annotations

import json
from typing import Any

from .models import Finding, Report


def filtered_report(report: Report, confidence: str) -> dict[str, Any]:
    data = report.to_dict()
    minimum = {"high": 0.85, "medium": 0.65, "all": 0.0}[confidence]
    data["findings"] = [
        item for item in data["findings"] if item["confidence"] >= minimum
    ]
    data["summary"]["shown"] = len(data["findings"])
    return data


def render_json(report: Report, confidence: str) -> str:
    return json.dumps(
        filtered_report(report, confidence),
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    )


def render_pretty(report: Report, confidence: str) -> str:
    data = filtered_report(report, confidence)
    lines = [
        "Repo Gardener",
        "=" * 72,
        f"Command: {report.command}  Python files: {data['metrics']['python_files']}  Findings shown: {data['summary']['shown']}",
    ]
    if report.base:
        lines.append(
            f"Iteration base: {report.base}  Changed files: {data['metrics'].get('changed_files', 0)}"
        )
    lines.append("")
    if not data["findings"]:
        lines.append("No findings at the selected confidence level.")
        return "\n".join(lines)
    for item in data["findings"]:
        tier = _tier(float(item["confidence"]))
        replacement = f" -> {item['replacement']}" if item.get("replacement") else ""
        lines.append(f"[{tier.upper()}] {item['rule']}  {item['path']}{replacement}")
        lines.append(
            f"  confidence {item['confidence']:.0%}  risk {item['risk']:.0%}  action {item['recommendation']}"
        )
        for evidence in item["evidence"]:
            lines.append(
                f"  - {evidence['type']}: {_compact(evidence.get('value'), evidence)}"
            )
        for risk in item["risks"]:
            lines.append(f"  ! {risk}")
        lines.append("")
    return "\n".join(lines).rstrip()


def render_fix_plan(findings: list[Finding], root: str, apply: bool) -> str:
    lines = ["Repo Gardener safe deletion plan", "=" * 72, f"Root: {root}"]
    lines.append("Rollback data: .repo-gardener/ (keep this path in .gitignore)")
    if not findings:
        lines.append("No high-confidence, low-risk deletion candidates.")
        return "\n".join(lines)
    lines.append(f"Mode: {'APPLY' if apply else 'DRY RUN'}")
    lines.append("")
    for finding in findings:
        lines.append(
            f"DELETE {finding.path}  (replacement: {finding.replacement}, confidence: {finding.confidence:.0%})"
        )
    if not apply:
        lines.extend(
            [
                "",
                "No files changed. Re-run with --apply and validation to execute this plan.",
            ]
        )
    return "\n".join(lines)


def _tier(confidence: float) -> str:
    return "high" if confidence >= 0.85 else "medium" if confidence >= 0.65 else "low"


def _compact(value: Any, evidence: dict[str, Any]) -> str:
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if "baseline_median" in evidence:
        return f"{value} (baseline {evidence['baseline_median']}, robust z {evidence['robust_z']})"
    return str(value)
