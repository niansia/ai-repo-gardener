from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from hashlib import sha256
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
    root: Path, findings: list[Finding], validation_commands: list[str]
) -> dict[str, object]:
    if not validation_commands:
        raise FixError(
            "--apply requires at least one --validate command or configured validation command"
        )
    files = [_safe_target(root, finding.path) for finding in findings]
    if any(path.is_symlink() for path in files):
        raise FixError("refusing to delete symlink candidates")
    missing = [str(path.relative_to(root)) for path in files if not path.is_file()]
    if missing:
        raise FixError(
            "candidate files disappeared before apply: " + ", ".join(missing)
        )

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

    for path in files:
        path.unlink()
    results = []
    for command in validation_commands:
        completed = subprocess.run(
            command,
            cwd=root,
            shell=True,
            text=True,
            capture_output=True,
            check=False,
        )
        results.append(
            {
                "command": command,
                "exit_code": completed.returncode,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            }
        )
        if completed.returncode != 0:
            _restore_snapshot(root, snapshot)
            manifest["status"] = "restored_after_validation_failure"
            manifest["validation_results"] = results
            _write_json(snapshot / "manifest.json", manifest)
            raise FixError(
                f"validation failed and deleted files were restored: {command}"
            )
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
    target = (root / relative_path).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise FixError(f"path escapes repository root: {relative_path}") from exc
    return target


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
