from __future__ import annotations

import json
from typing import Any

from . import __version__
from .models import Report
from .reporting import filtered_report

SARIF_VERSION = "2.1.0"
SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
INFORMATION_URI = "https://github.com/niansia/ai-repo-gardener"
_GC_ANCHOR = f"{INFORMATION_URI}#what-v01-supports"
_EXPERIMENTAL_ANCHOR = f"{INFORMATION_URI}#review-only-architecture-and-style-analyzers"

RULE_METADATA: dict[str, dict[str, str]] = {
    "stale-file": {
        "description": (
            "A whole Python file has a reachable, structurally similar replacement "
            "plus independent evidence such as call-site migration or Git chronology."
        ),
        "level": "warning",
        "help_uri": _GC_ANCHOR,
    },
    "orphan-file": {
        "description": (
            "A file changed in this iteration is unreachable, has zero inbound "
            "imports, and has no plausible replacement. Review only."
        ),
        "level": "note",
        "help_uri": _GC_ANCHOR,
    },
    "orphan-helper": {
        "description": (
            "A top-level helper has no static reference in the repository. "
            "Reflective or external callers are not proven absent."
        ),
        "level": "note",
        "help_uri": _GC_ANCHOR,
    },
    "duplicate-implementation": {
        "description": (
            "Two substantial top-level implementations share an exact "
            "normalized-AST fingerprint. Proposes consolidation review only."
        ),
        "level": "note",
        "help_uri": _GC_ANCHOR,
    },
    "dependency-leftover": {
        "description": (
            "A declared runtime dependency has no matching static or runtime "
            "import. Distribution and import names may legitimately differ."
        ),
        "level": "note",
        "help_uri": _GC_ANCHOR,
    },
    "flat-directory": {
        "description": (
            "A directory carries measurable architecture pressure. Move plans are "
            "review-only proposals and are never applied."
        ),
        "level": "note",
        "help_uri": _EXPERIMENTAL_ANCHOR,
    },
    "style-drift": {
        "description": (
            "A file deviates from the repository's baseline Python house style. "
            "This is never proof of AI authorship."
        ),
        "level": "note",
        "help_uri": _EXPERIMENTAL_ANCHOR,
    },
}


def render_sarif(report: Report, confidence: str) -> str:
    data = filtered_report(report, confidence)
    findings = data["findings"]
    rule_ids = sorted({str(item["rule"]) for item in findings})
    rule_index = {rule: index for index, rule in enumerate(rule_ids)}
    document = {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "AI Repo Gardener",
                        "informationUri": INFORMATION_URI,
                        "version": __version__,
                        "semanticVersion": __version__,
                        "rules": [_rule(rule) for rule in rule_ids],
                    }
                },
                "automationDetails": {"id": f"repo-gardener/{data['command']}"},
                "columnKind": "utf16CodeUnits",
                "properties": _run_properties(data),
                "results": [_result(item, rule_index) for item in findings],
            }
        ],
    }
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False)


def _rule(rule_id: str) -> dict[str, Any]:
    metadata = RULE_METADATA.get(
        rule_id,
        {"description": rule_id, "level": "note", "help_uri": INFORMATION_URI},
    )
    return {
        "id": rule_id,
        "name": _camel_case(rule_id),
        "shortDescription": {"text": rule_id},
        "fullDescription": {"text": metadata["description"]},
        "help": {"text": metadata["description"]},
        "helpUri": metadata["help_uri"],
        "defaultConfiguration": {"level": metadata["level"]},
        "properties": {"tags": ["repo-gardener", _category_tag(rule_id)]},
    }


def _result(finding: dict[str, Any], rule_index: dict[str, int]) -> dict[str, Any]:
    rule_id = str(finding["rule"])
    location: dict[str, Any] = {"artifactLocation": {"uri": _uri(str(finding["path"]))}}
    region = _region(finding)
    if region:
        location["region"] = region
    return {
        "ruleId": rule_id,
        "ruleIndex": rule_index[rule_id],
        "level": _level(str(finding.get("severity", "info"))),
        "message": {"text": _message(finding)},
        "locations": [{"physicalLocation": location}],
        "partialFingerprints": {"repoGardenerFindingId/v1": str(finding["id"])},
        "properties": {
            "confidence": finding["confidence"],
            "risk": finding["risk"],
            "recommendation": finding["recommendation"],
            "category": finding["category"],
            "replacement": finding.get("replacement"),
            "risks": finding["risks"],
            "evidence": _evidence_types(finding),
        },
    }


def _run_properties(data: dict[str, Any]) -> dict[str, Any]:
    metrics = data["metrics"]
    properties: dict[str, Any] = {
        "command": data["command"],
        "summary": data["summary"],
        "pythonFiles": metrics.get("python_files", 0),
        "parseErrors": metrics.get("parse_errors", 0),
        "experimentalAnalysis": metrics.get("experimental_analysis", False),
    }
    if "base" in data:
        properties["base"] = data["base"]
    accepted = metrics.get("accepted_findings")
    if isinstance(accepted, dict):
        properties["acceptedFindings"] = accepted
    return properties


def _message(finding: dict[str, Any]) -> str:
    replacement = finding.get("replacement")
    target = f" -> {replacement}" if replacement else ""
    evidence = ", ".join(_evidence_types(finding)[:3])
    detail = f" Evidence: {evidence}." if evidence else ""
    return (
        f"{finding['rule']}: {finding['path']}{target} "
        f"(confidence {float(finding['confidence']):.0%}, "
        f"risk {float(finding['risk']):.0%}, "
        f"recommendation {finding['recommendation']}).{detail}"
    )


def _evidence_types(finding: dict[str, Any]) -> list[str]:
    return [
        str(item.get("type"))
        for item in finding["evidence"]
        if not str(item.get("type", "")).endswith("_sha256")
    ]


def _region(finding: dict[str, Any]) -> dict[str, int]:
    for item in finding["evidence"]:
        kind = str(item.get("type"))
        value = item.get("value")
        if kind == "definition_line" and isinstance(value, int) and value > 0:
            return {"startLine": value}
        if (
            kind == "definition_lines"
            and isinstance(value, list)
            and len(value) == 2
            and all(isinstance(number, int) and number > 0 for number in value)
        ):
            return {"startLine": value[0], "endLine": max(value[0], value[1])}
    return {}


def _level(severity: str) -> str:
    return {"error": "error", "warning": "warning"}.get(severity, "note")


def _uri(path: str) -> str:
    normalized = path.replace("\\", "/").strip()
    return normalized or "."


def _category_tag(rule_id: str) -> str:
    metadata = RULE_METADATA.get(rule_id)
    if metadata is None:
        return "repo-gc"
    return "experimental" if metadata["help_uri"] == _EXPERIMENTAL_ANCHOR else "repo-gc"


def _camel_case(rule_id: str) -> str:
    head, *rest = rule_id.split("-")
    return head + "".join(part.capitalize() for part in rest)
