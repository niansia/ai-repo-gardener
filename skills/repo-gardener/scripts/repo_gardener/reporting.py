from __future__ import annotations

import json
from hashlib import sha256
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
            if str(evidence["type"]).endswith("_sha256"):
                continue
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


def fix_plan_data(
    findings: list[Finding], root: str, apply: bool, base: str | None
) -> dict[str, Any]:
    operations = [
        {
            "finding_id": finding.id,
            "operation": "delete",
            "path": finding.path,
            "replacement": finding.replacement,
            "candidate_sha256": _evidence(finding, "candidate_sha256"),
            "replacement_sha256": _evidence(finding, "replacement_sha256"),
            "confidence": finding.confidence,
            "risk": finding.risk,
        }
        for finding in findings
    ]
    identity = json.dumps(
        {"base": base, "operations": operations}, sort_keys=True, separators=(",", ":")
    )
    return {
        "schema_version": 1,
        "command": "fix",
        "mode": "apply" if apply else "dry-run",
        "root": root,
        "base": base,
        "plan_id": sha256(identity.encode("utf-8")).hexdigest()[:16],
        "validation_required": True,
        "operations": operations,
    }


def render_fix_json(
    findings: list[Finding],
    root: str,
    apply: bool,
    base: str | None,
    manifest: dict[str, object] | None = None,
) -> str:
    data = fix_plan_data(findings, root, apply, base)
    if manifest is not None:
        data["result"] = manifest
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)


def _evidence(finding: Finding, evidence_type: str) -> Any:
    return next(
        (
            item.get("value")
            for item in finding.evidence
            if item.get("type") == evidence_type
        ),
        None,
    )


def _tier(confidence: float) -> str:
    return "high" if confidence >= 0.85 else "medium" if confidence >= 0.65 else "low"


def _compact(value: Any, evidence: dict[str, Any]) -> str:
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if "baseline_median" in evidence:
        return f"{value} (baseline {evidence['baseline_median']}, robust z {evidence['robust_z']})"
    return str(value)
