from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from hashlib import sha256
from math import isfinite
from pathlib import Path

from .models import Finding

STATE_DIRECTORY = ".repo-gardener"


class FixError(RuntimeError):
    pass


def safe_candidates(findings: list[Finding]) -> list[Finding]:
    return sorted(
        [
            finding
            for finding in findings
            if finding.rule == "stale-file"
            and finding.confidence >= 0.85
            and finding.risk <= 0.20
            and finding.recommendation == "safe_delete_candidate"
            and finding.replacement
        ],
        key=lambda finding: finding.path,
    )


def apply_deletions(
    root: Path,
    findings: list[Finding],
    validation_commands: list[str],
    reviewed_plan: dict[str, object] | None = None,
    validation_timeout: float = 300.0,
) -> dict[str, object]:
    if not validation_commands:
        raise FixError(
            "--apply requires at least one --validate command or configured validation command"
        )
    if not isfinite(validation_timeout) or validation_timeout <= 0:
        raise FixError("validation timeout must be greater than zero")
    files = [_safe_target(root, finding.path) for finding in findings]
    if any(path.is_symlink() for path in files):
        raise FixError("refusing to delete symlink candidates")
    missing = [str(path.relative_to(root)) for path in files if not path.is_file()]
    if missing:
        raise FixError(
            "candidate files disappeared before apply: " + ", ".join(missing)
        )
    _verify_plan(root, findings, reviewed_plan)

    operation_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    state_root = root / STATE_DIRECTORY
    snapshot = state_root / "rollback" / operation_id
    suffix = 1
    while snapshot.exists():
        snapshot = state_root / "rollback" / f"{operation_id}-{suffix}"
        suffix += 1
    snapshot.mkdir(parents=True)
    manifest: dict[str, object] = {
        "schema_version": 1,
        "operation_id": snapshot.name,
        "created_at": datetime.now(UTC).isoformat(),
        "status": "pending",
        "files": [],
        "validation": validation_commands,
        "validation_timeout_seconds": validation_timeout,
    }
    entries: list[dict[str, str]] = []
    for path in files:
        relative = path.relative_to(root)
        destination = snapshot / "files" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        entries.append({"path": relative.as_posix(), "sha256": _hash(path)})
    manifest["files"] = entries
    _write_json(snapshot / "manifest.json", manifest)
    _write_json(state_root / "last-operation.json", {"operation_id": snapshot.name})

    results: list[dict[str, object]] = []
    try:
        _verify_plan(root, findings, reviewed_plan)
        for path in files:
            path.unlink()
        for command in validation_commands:
            try:
                completed = subprocess.run(
                    command,
                    cwd=root,
                    shell=True,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=validation_timeout,
                )
            except subprocess.TimeoutExpired as exc:
                results.append(
                    {
                        "command": command,
                        "timed_out": True,
                        "timeout_seconds": validation_timeout,
                        "stdout": str(exc.stdout or "")[-4000:],
                        "stderr": str(exc.stderr or "")[-4000:],
                    }
                )
                raise FixError(
                    f"validation timed out after {validation_timeout:g} seconds: "
                    f"{command}"
                ) from exc
            results.append(
                {
                    "command": command,
                    "exit_code": completed.returncode,
                    "stdout": completed.stdout[-4000:],
                    "stderr": completed.stderr[-4000:],
                }
            )
            if completed.returncode != 0:
                raise FixError(f"validation failed: {command}")
    except BaseException as exc:
        _restore_snapshot(root, snapshot)
        manifest["status"] = "restored_after_validation_failure_or_interruption"
        manifest["validation_results"] = results
        manifest["failure"] = f"{type(exc).__name__}: {exc}"
        _write_json(snapshot / "manifest.json", manifest)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise FixError(
            f"validation failed or was interrupted and deleted files were restored: {exc}"
        ) from exc
    manifest["status"] = "applied"
    manifest["validation_results"] = results
    _write_json(snapshot / "manifest.json", manifest)
    return manifest


def restore_last(root: Path) -> dict[str, object]:
    pointer = root / STATE_DIRECTORY / "last-operation.json"
    if not pointer.is_file():
        raise FixError("no Repo Gardener operation is available to restore")
    operation = json.loads(pointer.read_text(encoding="utf-8"))["operation_id"]
    snapshot = root / STATE_DIRECTORY / "rollback" / operation
    manifest = _restore_snapshot(root, snapshot)
    manifest["status"] = "restored_by_user"
    _write_json(snapshot / "manifest.json", manifest)
    return manifest


def _restore_snapshot(root: Path, snapshot: Path) -> dict[str, object]:
    manifest_path = snapshot / "manifest.json"
    if not manifest_path.is_file():
        raise FixError(f"rollback manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest.get("files", []):
        destination = _safe_target(root, entry["path"])
        source = snapshot / "files" / entry["path"]
        if not source.is_file():
            raise FixError(f"rollback content missing for {entry['path']}")
        if destination.is_symlink():
            raise FixError(f"refusing to restore through a symlink: {entry['path']}")
        if destination.exists() and _hash(destination) != entry["sha256"]:
            raise FixError(
                f"refusing to overwrite a changed file during restore: {entry['path']}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if _hash(destination) != entry["sha256"]:
            raise FixError(f"restored content hash mismatch for {entry['path']}")
    return manifest


def _safe_target(root: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute():
        raise FixError(f"absolute path is not allowed: {relative_path}")
    root_resolved = root.resolve()
    target = root_resolved / relative
    resolved = target.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise FixError(f"path escapes repository root: {relative_path}") from exc
    return target


def _verify_plan(
    root: Path,
    findings: list[Finding],
    reviewed_plan: dict[str, object] | None = None,
) -> None:
    for finding in findings:
        candidate = _safe_target(root, finding.path)
        expected_candidate = _evidence_value(finding, "candidate_sha256")
        if expected_candidate and (
            not candidate.is_file() or _hash(candidate) != expected_candidate
        ):
            raise FixError(
                f"stale plan: candidate changed since analysis: {finding.path}"
            )
        expected_replacement = _evidence_value(finding, "replacement_sha256")
        if expected_replacement and finding.replacement:
            replacement = _safe_target(root, finding.replacement)
            if replacement.is_symlink():
                raise FixError(f"refusing a symlink replacement: {finding.replacement}")
            if not replacement.is_file() or _hash(replacement) != expected_replacement:
                raise FixError(
                    "stale plan: replacement changed since analysis: "
                    f"{finding.replacement}"
                )
    if reviewed_plan is not None:
        _verify_evidence_files(root, reviewed_plan)


def _verify_evidence_files(root: Path, plan: dict[str, object]) -> None:
    operations = plan.get("operations", [])
    if not isinstance(operations, list):
        raise FixError("reviewed plan operations must be a list")
    for operation in operations:
        if not isinstance(operation, dict):
            raise FixError("reviewed plan contains an invalid operation")
        evidence_files = operation.get("evidence_files", [])
        if not isinstance(evidence_files, list):
            raise FixError("reviewed plan evidence_files must be a list")
        for evidence in evidence_files:
            if not isinstance(evidence, dict):
                raise FixError("reviewed plan contains invalid evidence file data")
            relative = evidence.get("path")
            expected = evidence.get("sha256")
            if not isinstance(relative, str) or not isinstance(expected, str):
                raise FixError("reviewed plan contains incomplete evidence file data")
            path = _safe_target(root, relative)
            if path.is_symlink():
                raise FixError(
                    f"stale plan: evidence file became a symlink: {relative}"
                )
            current = _hash(path) if path.is_file() else "missing"
            if current != expected:
                raise FixError(
                    f"stale plan: evidence file changed since review: {relative}"
                )


def _evidence_value(finding: Finding, evidence_type: str) -> str | None:
    return next(
        (
            str(item["value"])
            for item in finding.evidence
            if item.get("type") == evidence_type and item.get("value")
        ),
        None,
    )


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
