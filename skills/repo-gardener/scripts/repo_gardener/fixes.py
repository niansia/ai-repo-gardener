from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from hashlib import sha256
from math import isfinite
from pathlib import Path
from uuid import uuid4

from .git_support import git_executable
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
    root = root.resolve()
    if not validation_commands:
        raise FixError(
            "--apply requires at least one --validate command or configured validation command"
        )
    if not isfinite(validation_timeout) or validation_timeout <= 0:
        raise FixError("validation timeout must be greater than zero")
    _preflight_state_paths(root)
    files = [_safe_target(root, finding.path) for finding in findings]
    if any(path.is_symlink() for path in files):
        raise FixError("refusing to delete symlink candidates")
    missing = [str(path.relative_to(root)) for path in files if not path.is_file()]
    if missing:
        raise FixError(
            "candidate files disappeared before apply: " + ", ".join(missing)
        )
    _verify_plan(root, findings, reviewed_plan)
    _validate_repository_symlinks(root)
    try:
        results = _validate_in_isolated_copy(
            root,
            findings,
            validation_commands,
            validation_timeout,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except (FixError, OSError) as exc:
        raise FixError(
            "validation failed in an isolated copy; original repository unchanged: "
            f"{exc}"
        ) from exc
    _verify_plan(root, findings, reviewed_plan)

    operation_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    state_root, snapshot = _create_snapshot(root, operation_id)
    suffix = 1
    while snapshot.exists():
        snapshot = _safe_state_path(root, Path("rollback") / f"{operation_id}-{suffix}")
        suffix += 1
    snapshot.mkdir()
    manifest: dict[str, object] = {
        "schema_version": 1,
        "operation_id": snapshot.name,
        "created_at": datetime.now(UTC).isoformat(),
        "status": "pending",
        "files": [],
        "validation": validation_commands,
        "validation_mode": "isolated_copy",
        "validation_timeout_seconds": validation_timeout,
        "validation_results": results,
    }
    entries: list[dict[str, str]] = []
    for path in files:
        relative = path.relative_to(root)
        destination = _safe_state_path(
            root, Path("rollback") / snapshot.name / "files" / relative
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination = _safe_state_path(
            root, Path("rollback") / snapshot.name / "files" / relative
        )
        shutil.copy2(path, destination)
        entries.append({"path": relative.as_posix(), "sha256": _hash(path)})
    manifest["files"] = entries
    _write_state_json(root, snapshot / "manifest.json", manifest)
    _write_state_json(
        root, state_root / "last-operation.json", {"operation_id": snapshot.name}
    )

    try:
        _verify_plan(root, findings, reviewed_plan)
        for path in files:
            path.unlink()
    except BaseException as exc:
        _restore_snapshot(root, snapshot)
        manifest["status"] = "restored_after_apply_failure_or_interruption"
        manifest["failure"] = f"{type(exc).__name__}: {exc}"
        _write_state_json(root, snapshot / "manifest.json", manifest)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise FixError(
            f"apply failed or was interrupted and deleted files were restored: {exc}"
        ) from exc
    manifest["status"] = "applied"
    _write_state_json(root, snapshot / "manifest.json", manifest)
    return manifest


def _validate_in_isolated_copy(
    root: Path,
    findings: list[Finding],
    commands: list[str],
    timeout: float,
) -> list[dict[str, object]]:
    with tempfile.TemporaryDirectory(prefix="repo-gardener-validation-") as raw:
        workspace = Path(raw) / "repository"
        worktree_created = _prepare_validation_workspace(root, workspace)
        try:
            for finding in findings:
                candidate = _safe_target(workspace, finding.path)
                if not candidate.is_file():
                    raise FixError(
                        f"candidate missing from isolated copy: {finding.path}"
                    )
                candidate.unlink()
            return _run_validation_commands(workspace, commands, timeout)
        finally:
            if worktree_created:
                _remove_validation_worktree(root, workspace)


def _prepare_validation_workspace(root: Path, workspace: Path) -> bool:
    """Create an isolated copy while keeping Git commands functional.

    A linked worktree's ``.git`` is a pointer file that cannot simply be copied
    to another directory.  When HEAD exists, Git creates a disposable worktree
    and the current (including untracked) files are overlaid onto it.
    """
    executable = git_executable()
    worktree_created = False
    if executable and (root / ".git").exists():
        head = subprocess.run(
            [executable, "-C", str(root), "rev-parse", "--verify", "HEAD"],
            check=False,
            capture_output=True,
        )
        if head.returncode == 0:
            try:
                subprocess.run(
                    [
                        executable,
                        "-C",
                        str(root),
                        "worktree",
                        "add",
                        "--detach",
                        "--no-checkout",
                        str(workspace),
                        "HEAD",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except (OSError, subprocess.CalledProcessError) as exc:
                detail = getattr(exc, "stderr", "") or str(exc)
                raise FixError(
                    f"could not create isolated Git worktree: {detail}"
                ) from exc
            worktree_created = True
            _initialize_validation_index(root, workspace, executable)

    def ignore_metadata(directory: str, names: list[str]) -> set[str]:
        if Path(directory).resolve() != root:
            return set()
        ignored = {STATE_DIRECTORY} & set(names)
        if worktree_created:
            ignored.update({".git"} & set(names))
        return ignored

    try:
        shutil.copytree(
            root,
            workspace,
            symlinks=True,
            dirs_exist_ok=worktree_created,
            ignore=ignore_metadata,
        )
    except BaseException:
        if worktree_created:
            _remove_validation_worktree(root, workspace)
        raise
    return worktree_created


def _remove_validation_worktree(root: Path, workspace: Path) -> None:
    executable = git_executable()
    if executable is None:
        return
    subprocess.run(
        [executable, "-C", str(root), "worktree", "remove", "--force", str(workspace)],
        check=False,
        capture_output=True,
    )


def _initialize_validation_index(root: Path, workspace: Path, executable: str) -> None:
    """Reproduce the original staged/unstaged distinction in the worktree."""

    try:
        subprocess.run(
            [executable, "-C", str(workspace), "reset", "--mixed", "--quiet", "HEAD"],
            check=True,
            capture_output=True,
        )
        staged = subprocess.run(
            [
                executable,
                "-C",
                str(root),
                "diff",
                "--cached",
                "--binary",
                "--full-index",
                "HEAD",
                "--",
            ],
            check=True,
            capture_output=True,
        ).stdout
        if staged:
            subprocess.run(
                [
                    executable,
                    "-C",
                    str(workspace),
                    "apply",
                    "--cached",
                    "--binary",
                    "--whitespace=nowarn",
                    "-",
                ],
                input=staged,
                check=True,
                capture_output=True,
            )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", b"")
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        raise FixError(
            f"could not reproduce staged Git state: {detail or exc}"
        ) from exc


def _run_validation_commands(
    workspace: Path, commands: list[str], timeout: float
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                cwd=workspace,
                shell=True,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            results.append(
                {
                    "command": command,
                    "timed_out": True,
                    "timeout_seconds": timeout,
                    "stdout": str(exc.stdout or "")[-4000:],
                    "stderr": str(exc.stderr or "")[-4000:],
                }
            )
            raise FixError(
                f"validation timed out after {timeout:g} seconds: {command}"
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
            stdout = completed.stdout[-2000:].strip()
            stderr = completed.stderr[-2000:].strip()
            detail = stderr or stdout or "no output"
            raise FixError(
                f"validation failed with exit code {completed.returncode}: {command}\n"
                f"last output:\n{detail}"
            )
    return results


def restore_last(root: Path) -> dict[str, object]:
    root = root.resolve()
    pointer = _safe_state_path(root, "last-operation.json")
    if not pointer.is_file():
        raise FixError("no AI Repo Gardener operation is available to restore")
    try:
        pointer_data = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FixError("rollback state pointer is unreadable") from exc
    if not isinstance(pointer_data, dict):
        raise FixError("rollback state pointer is invalid")
    operation = pointer_data.get("operation_id")
    if (
        not isinstance(operation, str)
        or not operation
        or Path(operation).name != operation
        or operation in {".", ".."}
    ):
        raise FixError("rollback state operation id is invalid")
    snapshot = _safe_state_path(root, Path("rollback") / operation)
    manifest = _restore_snapshot(root, snapshot)
    manifest["status"] = "restored_by_user"
    _write_state_json(root, snapshot / "manifest.json", manifest)
    return manifest


def _restore_snapshot(root: Path, snapshot: Path) -> dict[str, object]:
    expected_snapshot = _safe_state_path(root, Path("rollback") / snapshot.name)
    if expected_snapshot != snapshot:
        raise FixError("rollback snapshot path is invalid")
    manifest_path = _safe_state_path(
        root, Path("rollback") / snapshot.name / "manifest.json"
    )
    if not manifest_path.is_file():
        raise FixError(f"rollback manifest not found: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FixError("rollback manifest is unreadable") from exc
    if not isinstance(manifest, dict):
        raise FixError("rollback manifest is invalid")
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise FixError("rollback manifest files must be a list")

    pending: list[tuple[str, Path, Path, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise FixError("rollback manifest contains an invalid file entry")
        relative = entry.get("path")
        expected_hash = entry.get("sha256")
        if not isinstance(relative, str) or not isinstance(expected_hash, str):
            raise FixError("rollback manifest contains incomplete file data")
        destination = _safe_target(root, relative)
        source = _safe_state_path(
            root, Path("rollback") / snapshot.name / "files" / relative
        )
        if not source.is_file():
            raise FixError(f"rollback content missing for {relative}")
        if _hash(source) != expected_hash:
            raise FixError(f"rollback snapshot content hash mismatch for {relative}")
        if destination.exists() and _hash(destination) != expected_hash:
            raise FixError(
                f"refusing to overwrite a changed file during restore: {relative}"
            )
        pending.append((relative, source, destination, expected_hash))

    # No worktree file is touched until every snapshot entry has passed all
    # integrity and overwrite checks.
    for relative, source, destination, expected_hash in pending:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination = _safe_target(root, relative)
        temporary = destination.parent / f".{destination.name}.{uuid4().hex}.restore"
        try:
            shutil.copy2(source, temporary)
            if _hash(temporary) != expected_hash:
                raise FixError(f"restored content hash mismatch for {relative}")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    return manifest


def _validate_repository_symlinks(root: Path) -> None:
    """Reject links that could escape the disposable validation workspace."""
    root_resolved = root.resolve()
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        if current_path == root:
            directories[:] = [
                name for name in directories if name not in {".git", STATE_DIRECTORY}
            ]
        for name in (*directories, *files):
            path = current_path / name
            if not path.is_symlink():
                continue
            raw_target = Path(os.readlink(path))
            if raw_target.is_absolute():
                raise FixError(
                    f"validation symlink may write outside isolated workspace: "
                    f"{path.relative_to(root).as_posix()}"
                )
            try:
                path.resolve().relative_to(root_resolved)
            except ValueError as exc:
                raise FixError(
                    f"validation symlink escapes repository: "
                    f"{path.relative_to(root).as_posix()}"
                ) from exc


def _safe_target(root: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute():
        raise FixError(f"absolute path is not allowed: {relative_path}")
    if ".." in relative.parts:
        raise FixError(f"path escapes repository root: {relative_path}")
    root_resolved = root.resolve()
    target = root_resolved / relative
    resolved = target.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise FixError(f"path escapes repository root: {relative_path}") from exc
    current = root_resolved
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise FixError(f"refusing path through a symlink: {relative_path}")
    return target


def _state_root(root: Path) -> Path:
    root_resolved = root.resolve()
    state_root = root_resolved / STATE_DIRECTORY
    if state_root.is_symlink():
        raise FixError("refusing symlink rollback state directory: .repo-gardener")
    try:
        state_root.resolve().relative_to(root_resolved)
    except ValueError as exc:
        raise FixError("rollback state directory escapes repository root") from exc
    if state_root.exists() and not state_root.is_dir():
        raise FixError("rollback state path is not a directory: .repo-gardener")
    return state_root


def _safe_state_path(root: Path, relative_path: str | Path) -> Path:
    state_root = _state_root(root)
    target = _safe_target(state_root, str(relative_path))
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise FixError(
            f"rollback state path escapes repository root: {relative_path}"
        ) from exc
    return target


def _preflight_state_paths(root: Path) -> tuple[Path, Path]:
    state_root = _state_root(root)
    rollback_root = _safe_state_path(root, "rollback")
    if rollback_root.exists() and not rollback_root.is_dir():
        raise FixError("rollback state path is not a directory: rollback")
    return state_root, rollback_root


def _create_snapshot(root: Path, operation_id: str) -> tuple[Path, Path]:
    state_root, rollback_root = _preflight_state_paths(root)
    state_root.mkdir(exist_ok=True)
    state_root, rollback_root = _preflight_state_paths(root)
    rollback_root.mkdir(exist_ok=True)
    snapshot = _safe_state_path(root, Path("rollback") / operation_id)
    return state_root, snapshot


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


def _write_state_json(root: Path, path: Path, value: object) -> None:
    state_root = _state_root(root)
    try:
        relative = path.relative_to(state_root)
    except ValueError as exc:
        raise FixError(f"state file is outside .repo-gardener: {path}") from exc
    safe_path = _safe_state_path(root, relative)
    safe_path.parent.mkdir(parents=True, exist_ok=True)
    safe_path = _safe_state_path(root, relative)
    temporary = safe_path.parent / f".{safe_path.name}.{uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(
                json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
            )
        if safe_path.is_symlink():
            raise FixError(f"refusing to replace symlink state file: {relative}")
        os.replace(temporary, safe_path)
    finally:
        temporary.unlink(missing_ok=True)
