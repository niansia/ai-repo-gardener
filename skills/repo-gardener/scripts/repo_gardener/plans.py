from __future__ import annotations

import json
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from typing import Any

from .config import Config
from .fixes import FixError
from .git_support import resolve_git_ref
from .models import Finding

PLAN_SCHEMA_VERSION = 2
MAX_PLAN_BYTES = 2 * 1024 * 1024


def build_plan(
    root: Path,
    findings: list[Finding],
    base_ref: str,
    config: Config,
    apply: bool = False,
    blockers: list[str] | None = None,
) -> dict[str, Any]:
    base_sha = resolve_git_ref(root, base_ref)
    head_sha = resolve_git_ref(root, "HEAD")
    if base_sha is None or head_sha is None:
        raise FixError("a reviewed plan requires resolvable Git base and HEAD commits")
    operations = [_operation(root, finding) for finding in findings]
    plan: dict[str, Any] = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "command": "fix",
        "mode": "apply" if apply else "dry-run",
        "root": str(root.resolve()),
        "base": base_ref,
        "base_ref": base_ref,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "config_sha256": _config_hash(config),
        "validation_required": True,
        "automatic_deletion_blockers": sorted(blockers or []),
        "operations": operations,
    }
    plan["plan_id"] = _plan_id(plan)
    return plan


def load_reviewed_plan(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_PLAN_BYTES:
            raise FixError(f"reviewed plan is too large: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FixError(f"reviewed plan does not exist: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise FixError(f"unable to read reviewed plan {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise FixError("reviewed plan must be a JSON object")
    _validate_plan(data)
    return data


def require_matching_plan(reviewed: dict[str, Any], current: dict[str, Any]) -> None:
    reviewed_id = str(reviewed["plan_id"])
    current_id = str(current["plan_id"])
    if reviewed_id != current_id:
        raise FixError(
            "reviewed plan no longer matches the current repository; "
            f"reviewed plan {reviewed_id}, current plan {current_id}. "
            "Generate and review a new dry-run plan."
        )


def _operation(root: Path, finding: Finding) -> dict[str, Any]:
    return {
        "finding_id": finding.id,
        "operation": "delete",
        "path": finding.path,
        "replacement": finding.replacement,
        "candidate_sha256": _evidence(finding, "candidate_sha256"),
        "replacement_sha256": _evidence(finding, "replacement_sha256"),
        "evidence_files": [
            {
                "path": relative,
                "sha256": _path_hash(root / relative),
            }
            for relative in sorted(
                {
                    str(item["value"])
                    for item in finding.evidence
                    if item.get("type") == "call_site_migration" and item.get("value")
                }
            )
        ],
        "confidence": finding.confidence,
        "risk": finding.risk,
    }


def _validate_plan(plan: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "command",
        "mode",
        "root",
        "base",
        "base_ref",
        "base_sha",
        "head_sha",
        "config_sha256",
        "validation_required",
        "automatic_deletion_blockers",
        "operations",
        "plan_id",
    }
    missing = sorted(required - plan.keys())
    if missing:
        raise FixError("reviewed plan is missing fields: " + ", ".join(missing))
    if plan["schema_version"] != PLAN_SCHEMA_VERSION or plan["command"] != "fix":
        raise FixError("unsupported reviewed plan schema")
    if plan["mode"] != "dry-run":
        raise FixError("--plan requires JSON produced by a dry-run")
    if plan["base"] != plan["base_ref"]:
        raise FixError("reviewed plan has inconsistent base fields")
    if plan["validation_required"] is not True:
        raise FixError("reviewed plan must require validation")
    if not isinstance(plan["automatic_deletion_blockers"], list) or not all(
        isinstance(item, str) and item for item in plan["automatic_deletion_blockers"]
    ):
        raise FixError("reviewed plan contains invalid automatic deletion blockers")
    if not isinstance(plan["operations"], list):
        raise FixError("reviewed plan operations must be a list")
    for operation in plan["operations"]:
        if not isinstance(operation, dict) or operation.get("operation") != "delete":
            raise FixError("reviewed plan contains an unsupported operation")
        if not all(
            isinstance(operation.get(key), str) and operation.get(key)
            for key in (
                "finding_id",
                "path",
                "replacement",
                "candidate_sha256",
                "replacement_sha256",
            )
        ):
            raise FixError("reviewed plan contains an incomplete deletion operation")
        evidence_files = operation.get("evidence_files")
        if not isinstance(evidence_files, list):
            raise FixError("reviewed plan operation is missing evidence_files")
        for evidence in evidence_files:
            if not isinstance(evidence, dict) or not all(
                isinstance(evidence.get(key), str) and evidence.get(key)
                for key in ("path", "sha256")
            ):
                raise FixError("reviewed plan contains invalid evidence file data")
    expected = _plan_id(plan)
    if plan["plan_id"] != expected:
        raise FixError("reviewed plan content does not match its plan_id")


def _plan_id(plan: dict[str, Any]) -> str:
    identity = {
        key: plan[key]
        for key in (
            "schema_version",
            "root",
            "base_ref",
            "base_sha",
            "head_sha",
            "config_sha256",
            "automatic_deletion_blockers",
            "operations",
        )
    }
    payload = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


def _config_hash(config: Config) -> str:
    payload = json.dumps(asdict(config), sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def _path_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"


def _evidence(finding: Finding, evidence_type: str) -> Any:
    return next(
        (
            item.get("value")
            for item in finding.evidence
            if item.get("type") == evidence_type
        ),
        None,
    )
