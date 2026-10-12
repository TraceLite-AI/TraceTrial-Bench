from __future__ import annotations

import difflib
import filecmp
import hashlib
import json
import os
import secrets
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from harnessbench.business_simulator import install_agent_cli
from harnessbench.task_tool_mcp import install_task_tool_bridge
from harnessbench.types import TaskSpec


MAX_WORKSPACE_REDACTION_PASSES = 8
IGNORE_PYTHON_CACHES = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")


class WorkspaceCredentialError(ValueError):
    pass


@dataclass(frozen=True)
class RunLayout:
    run_dir: Path
    agent_dir: Path
    workspace_dir: Path
    private_dir: Path
    verifier_dir: Path
    artifacts_dir: Path
    baseline_dir: Path


def prepare_run_dir(
    task: TaskSpec,
    run_dir: Path,
    *,
    private_dir: Path | None = None,
) -> RunLayout:
    run_dir.mkdir(parents=True, exist_ok=False)
    agent_dir = run_dir / "agent"
    private_dir = private_dir or run_dir / "private"
    artifacts_dir = run_dir / "artifacts"
    agent_dir.mkdir()
    private_dir.mkdir(parents=True)
    artifacts_dir.mkdir()

    shutil.copy2(task.task_dir / "prompt.md", agent_dir / "prompt.md")
    shutil.copy2(task.task_dir / "task.yaml", private_dir / "task.yaml")
    runtime_dir = task.task_dir / "runtime"
    if runtime_dir.is_dir():
        shutil.copytree(runtime_dir, private_dir / "runtime")
    turns = task.task_dir / "turns.json"
    if turns.is_file():
        shutil.copy2(turns, private_dir / "turns.json")
    isolation = task.task_dir / "isolation.yaml"
    if isolation.exists():
        shutil.copy2(isolation, private_dir / "isolation.yaml")

    workspace_dir = agent_dir / "workspace"
    shutil.copytree(task.workspace_dir, workspace_dir)
    verifier_dir = task.task_dir / "verifier"
    private_verifier_dir = private_dir / "verifier"
    if verifier_dir.exists():
        shutil.copytree(
            verifier_dir,
            private_verifier_dir,
            ignore=IGNORE_PYTHON_CACHES,
        )
    if (
        task.business_simulator is not None
        or task.conversation.simulator_entrypoint is not None
    ):
        shutil.copytree(
            task.task_dir / "simulator",
            private_dir / "simulator",
            ignore=IGNORE_PYTHON_CACHES,
        )
    if task.business_simulator is not None:
        install_agent_cli(task.business_simulator, workspace_dir)
    if task.task_tools is not None:
        install_task_tool_bridge(workspace_dir)
    _link_workspace_for_verifier(workspace_dir, private_dir / "workspace")
    return RunLayout(
        run_dir=run_dir,
        agent_dir=agent_dir,
        workspace_dir=workspace_dir,
        private_dir=private_dir,
        verifier_dir=private_verifier_dir,
        artifacts_dir=artifacts_dir,
        baseline_dir=private_dir / ".baseline_workspace",
    )


def prepare_scoring_layout(
    task: TaskSpec,
    run_dir: Path,
    *,
    private_dir: Path,
    baseline_workspace: Path | None = None,
    simulator_evidence: Mapping[str, str] | None = None,
) -> RunLayout:
    candidate = run_dir.expanduser()
    if candidate.is_symlink():
        raise ValueError("existing run directory must not be a symlink")
    try:
        resolved_run_dir = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"existing run directory is missing: {candidate}") from exc
    if not resolved_run_dir.is_dir():
        raise ValueError(f"existing run directory is not a directory: {candidate}")

    agent_dir = resolved_run_dir / "agent"
    workspace_dir = agent_dir / "workspace"
    artifacts_dir = resolved_run_dir / "artifacts"
    for path, label in (
        (agent_dir, "agent directory"),
        (workspace_dir, "workspace"),
        (artifacts_dir, "artifacts directory"),
    ):
        if path.is_symlink() or not path.is_dir():
            raise ValueError(f"existing run {label} is missing or invalid")

    private_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(task.task_dir / "task.yaml", private_dir / "task.yaml")
    turns = task.task_dir / "turns.json"
    if turns.is_file():
        shutil.copy2(turns, private_dir / "turns.json")
    isolation = task.task_dir / "isolation.yaml"
    if isolation.is_file():
        shutil.copy2(isolation, private_dir / "isolation.yaml")
    verifier_dir = private_dir / "verifier"
    shutil.copytree(
        task.task_dir / "verifier",
        verifier_dir,
        ignore=IGNORE_PYTHON_CACHES,
    )
    scoring_workspace = private_dir / ".scoring_workspace"
    shutil.copytree(workspace_dir, scoring_workspace, symlinks=True)

    baseline_dir = private_dir / ".baseline_workspace"
    if baseline_workspace is not None:
        if baseline_workspace.is_symlink() or not baseline_workspace.is_dir():
            raise ValueError("persisted verifier baseline is missing or invalid")
        shutil.copytree(baseline_workspace, baseline_dir, symlinks=True)
    else:
        if task.native_instructions is not None:
            raise ValueError(
                "score-only requires an exact verifier baseline for native instructions"
            )
        shutil.copytree(task.workspace_dir, baseline_dir, symlinks=True)
        if task.business_simulator is not None:
            install_agent_cli(task.business_simulator, baseline_dir)
        if task.task_tools is not None:
            install_task_tool_bridge(baseline_dir)

    if task.business_simulator is not None:
        if simulator_evidence is None:
            raise ValueError("business simulator evidence is required for score-only")
        simulator_dir = private_dir / "simulator"
        shutil.copytree(
            task.task_dir / "simulator",
            simulator_dir,
            ignore=IGNORE_PYTHON_CACHES,
        )
        required = {
            "ledger": "ledger.jsonl",
            "final_state": "state.json",
            "scenario": "scenario.json",
        }
        optional = {
            "instance": "instance.json",
            "release_manifest": "manifest.json",
        }
        for key, destination_name in required.items():
            source_value = simulator_evidence.get(key)
            if not isinstance(source_value, str):
                raise ValueError(f"business simulator evidence is missing {key}")
            source = Path(source_value)
            if source.is_symlink() or not source.is_file():
                raise ValueError(f"business simulator evidence {key} is invalid")
            shutil.copy2(source, simulator_dir / destination_name)
        for key, destination_name in optional.items():
            source_value = simulator_evidence.get(key)
            if not isinstance(source_value, str):
                continue
            source = Path(source_value)
            if source.is_symlink() or not source.is_file():
                raise ValueError(f"business simulator evidence {key} is invalid")
            shutil.copy2(source, simulator_dir / destination_name)

    _link_workspace_for_verifier(scoring_workspace, private_dir / "workspace")
    return RunLayout(
        run_dir=resolved_run_dir,
        agent_dir=agent_dir,
        workspace_dir=scoring_workspace,
        private_dir=private_dir,
        verifier_dir=verifier_dir,
        artifacts_dir=artifacts_dir,
        baseline_dir=baseline_dir,
    )


def persist_scoring_inputs(task: TaskSpec, layout: RunLayout) -> Path:
    evidence_dir = layout.artifacts_dir / "private_evidence" / "scoring_inputs"
    evidence_dir.mkdir(parents=True, exist_ok=False)
    baseline_dir = evidence_dir / "baseline_workspace"
    shutil.copytree(layout.baseline_dir, baseline_dir, symlinks=True)
    files = _directory_manifest(baseline_dir)
    manifest_path = evidence_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "harnessbench_scoring_inputs_v1",
                "baseline_workspace": "baseline_workspace",
                "files": files,
                "task_private_files": _task_private_manifest(task),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest_path


def load_persisted_baseline(
    manifest_path: Path,
    run_dir: Path,
    task: TaskSpec,
) -> Path:
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("scoring inputs manifest is missing or symlinked")
    try:
        manifest_path.resolve(strict=True).relative_to(
            (run_dir / "artifacts").resolve(strict=True)
        )
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("scoring inputs manifest is invalid") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != "harnessbench_scoring_inputs_v1"
        or payload.get("baseline_workspace") != "baseline_workspace"
        or not isinstance(payload.get("files"), list)
        or not isinstance(payload.get("task_private_files"), list)
    ):
        raise ValueError("scoring inputs manifest is invalid")
    baseline_dir = manifest_path.parent / "baseline_workspace"
    if baseline_dir.is_symlink() or not baseline_dir.is_dir():
        raise ValueError("persisted verifier baseline is missing or invalid")
    expected = payload["files"]
    observed = _directory_manifest(baseline_dir)
    if expected != observed:
        raise ValueError("persisted verifier baseline does not match its manifest")
    if payload["task_private_files"] != _task_private_manifest(task):
        raise ValueError("scoring inputs manifest does not match task-private files")
    return baseline_dir


def _directory_manifest(root: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            if path.is_dir() and not path.is_symlink():
                continue
            raise ValueError("persisted verifier baseline contains an unsafe node")
        payload = path.read_bytes()
        entries.append(
            {
                "path": str(path.relative_to(root)),
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return entries


def _task_private_manifest(task: TaskSpec) -> list[dict[str, object]]:
    candidates = [task.task_dir / "task.yaml", task.task_dir / "prompt.md"]
    turns = task.task_dir / "turns.json"
    if turns.is_file():
        candidates.append(turns)
    isolation = task.task_dir / "isolation.yaml"
    if isolation.is_file():
        candidates.append(isolation)
    for directory_name in ("runtime", "verifier", "simulator"):
        directory = task.task_dir / directory_name
        if directory.is_symlink():
            raise ValueError("task-private scoring inputs contain a symbolic link")
        if not directory.exists():
            continue
        if not directory.is_dir():
            raise ValueError("task-private scoring inputs contain an unsafe node")
        candidates.extend(
            path
            for path in directory.rglob("*")
            if "__pycache__" not in path.parts
            and path.suffix not in {".pyc", ".pyo"}
        )

    entries: list[dict[str, object]] = []
    for path in sorted(set(candidates)):
        if path.is_symlink() or not path.is_file():
            if path.is_dir() and not path.is_symlink():
                continue
            raise ValueError("task-private scoring inputs contain an unsafe node")
        payload = path.read_bytes()
        entries.append(
            {
                "path": str(path.relative_to(task.task_dir)),
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return entries


def write_workspace_diff(before: Path, after: Path, output_path: Path) -> None:
    lines: list[str] = []
    _diff_dirs(before, after, before, after, lines)
    output_path.write_text(
        "".join(lines) if lines else "No workspace changes detected.\n"
    )


def snapshot_workspace(workspace: Path, snapshot_dir: Path) -> None:
    if snapshot_dir.exists():
        shutil.rmtree(snapshot_dir)
    shutil.copytree(workspace, snapshot_dir)


def validate_workspace_for_host_verifier(
    workspace: Path,
    *,
    secret_values: tuple[str, ...] = (),
    transient_socket_paths: tuple[Path, ...] = (),
) -> dict[str, int]:
    """Reject unsafe nodes and prove credential remediation has converged."""

    allowed_socket_paths = _normalize_transient_socket_paths(
        workspace,
        transient_socket_paths,
    )
    first_credential_error: WorkspaceCredentialError | None = None
    for _attempt in range(MAX_WORKSPACE_REDACTION_PASSES):
        try:
            result = _validate_workspace_once(
                workspace,
                secret_values=secret_values,
                allowed_socket_paths=allowed_socket_paths,
            )
        except WorkspaceCredentialError as exc:
            first_credential_error = first_credential_error or exc
            continue
        if first_credential_error is not None:
            raise first_credential_error
        return result
    raise ValueError("verifier workspace credential redaction did not converge")


def _validate_workspace_once(
    workspace: Path,
    *,
    secret_values: tuple[str, ...],
    allowed_socket_paths: frozenset[Path],
) -> dict[str, int]:

    try:
        root_info = workspace.lstat()
    except OSError as exc:
        raise ValueError("cannot inspect the complete verifier workspace") from exc
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise ValueError("verifier workspace must be a real directory")
    files = 0
    directories = 1
    contaminated_files = 0
    unsafe_error: str | None = None
    secret_bytes = tuple(
        value.encode("utf-8")
        for value in secret_values
        if isinstance(value, str) and value
    )
    directory_handles: list[tuple[int, int, bool]] = []
    inspected_paths: list[Path] = []
    scan_error: Exception | None = None
    contaminated_names = 0
    try:
        directory_handles.append(
            _open_workspace_directory(
                workspace,
                root_info,
                writable=bool(secret_bytes),
            )
        )
        for directory, directory_names, file_names in os.walk(
            workspace,
            topdown=True,
            onerror=_raise_workspace_walk_error,
            followlinks=False,
        ):
            directory_path = Path(directory)
            for name in list(directory_names):
                path = directory_path / name
                inspected_paths.append(path)
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode):
                    directory_names.remove(name)
                    unsafe_error = unsafe_error or (
                        "verifier workspace contains a symbolic link"
                    )
                    continue
                if not stat.S_ISDIR(info.st_mode):
                    directory_names.remove(name)
                    unsafe_error = unsafe_error or (
                        "verifier workspace contains a special directory node"
                    )
                    continue
                directory_handles.append(
                    _open_workspace_directory(
                        path,
                        info,
                        writable=bool(secret_bytes),
                    )
                )
                directories += 1
            for name in file_names:
                path = directory_path / name
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode):
                    inspected_paths.append(path)
                    unsafe_error = unsafe_error or (
                        "verifier workspace contains a symbolic link"
                    )
                    continue
                if (
                    stat.S_ISSOCK(info.st_mode)
                    and _absolute_lexical_path(path) in allowed_socket_paths
                ):
                    continue
                inspected_paths.append(path)
                if not stat.S_ISREG(info.st_mode):
                    unsafe_error = unsafe_error or (
                        "verifier workspace contains a special file"
                    )
                    continue
                files += 1
                if secret_bytes and _redact_regular_file_credentials(
                    path,
                    info,
                    secret_bytes,
                ):
                    contaminated_files += 1
        contaminated_names = _redact_secret_path_names(
            workspace,
            secret_values,
            inspected_paths,
        )
    except Exception as exc:
        scan_error = exc

    restore_error = _restore_workspace_directories(directory_handles)
    if scan_error is not None:
        if isinstance(scan_error, ValueError):
            raise scan_error
        raise ValueError("cannot inspect the complete verifier workspace") from scan_error
    if restore_error is not None:
        raise ValueError("cannot restore verifier workspace permissions") from restore_error
    if unsafe_error is not None:
        raise ValueError(unsafe_error)
    if contaminated_files or contaminated_names:
        raise WorkspaceCredentialError(
            "verifier workspace contained model credentials; "
            f"redacted {contaminated_files} file(s) and "
            f"{contaminated_names} path name(s)"
        )
    return {"files": files, "directories": directories}


def _normalize_transient_socket_paths(
    workspace: Path,
    paths: tuple[Path, ...],
) -> frozenset[Path]:
    workspace_root = _absolute_lexical_path(workspace)
    normalized: set[Path] = set()
    for path in paths:
        candidate = _absolute_lexical_path(path)
        try:
            relative = candidate.relative_to(workspace_root)
        except ValueError as exc:
            raise ValueError(
                "transient socket path must be inside the verifier workspace"
            ) from exc
        if not relative.parts:
            raise ValueError("transient socket path cannot be the verifier workspace")
        normalized.add(candidate)
    return frozenset(normalized)


def _absolute_lexical_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _raise_workspace_walk_error(error: OSError) -> None:
    raise ValueError("cannot inspect the complete verifier workspace") from error


def _open_workspace_directory(
    path: Path,
    expected_info: os.stat_result,
    *,
    writable: bool,
) -> tuple[int, int, bool]:
    reference, inspection_path = _inspection_path(path, expected_info)
    original_mode = stat.S_IMODE(expected_info.st_mode)
    required_mode = stat.S_IRUSR | stat.S_IXUSR
    if writable:
        required_mode |= stat.S_IWUSR
    inspection_mode = original_mode | required_mode
    permissions_changed = inspection_mode != original_mode
    descriptor: int | None = None
    permissions_applied = False
    try:
        if permissions_changed:
            _chmod_inspection_path(
                inspection_path,
                inspection_mode,
                stable_reference=reference is not None,
            )
            permissions_applied = True
        no_follow = (
            0
            if reference is not None
            else getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(
            inspection_path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | no_follow,
        )
        opened_info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(opened_info.st_mode)
            or opened_info.st_dev != expected_info.st_dev
            or opened_info.st_ino != expected_info.st_ino
        ):
            raise OSError("workspace directory changed during inspection")
        return descriptor, original_mode, permissions_changed
    except Exception:
        if descriptor is not None:
            if permissions_applied:
                os.fchmod(descriptor, original_mode)
            os.close(descriptor)
        elif permissions_applied:
            _chmod_inspection_path(
                inspection_path,
                original_mode,
                stable_reference=reference is not None,
            )
        raise
    finally:
        if reference is not None:
            os.close(reference)


def _redact_regular_file_credentials(
    path: Path,
    expected_info: os.stat_result,
    secret_values: tuple[bytes, ...],
) -> bool:
    reference, inspection_path = _inspection_path(path, expected_info)
    original_mode = stat.S_IMODE(expected_info.st_mode)
    inspection_mode = original_mode | stat.S_IRUSR | stat.S_IWUSR
    permissions_changed = inspection_mode != original_mode
    descriptor: int | None = None
    permissions_applied = False
    try:
        if permissions_changed:
            _chmod_inspection_path(
                inspection_path,
                inspection_mode,
                stable_reference=reference is not None,
            )
            permissions_applied = True
        no_follow = (
            0
            if reference is not None
            else getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(
            inspection_path,
            os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | no_follow,
        )
        opened_info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened_info.st_mode)
            or opened_info.st_dev != expected_info.st_dev
            or opened_info.st_ino != expected_info.st_ino
        ):
            raise OSError("workspace file changed during inspection")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 64 * 1024):
            chunks.append(chunk)
        payload = b"".join(chunks)
        redacted = payload
        for secret in secret_values:
            redacted = redacted.replace(secret, b"<redacted>")
        if redacted == payload:
            return False
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.ftruncate(descriptor, 0)
        view = memoryview(redacted)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("workspace credential redaction made no progress")
            view = view[written:]
        os.fsync(descriptor)
        return True
    finally:
        try:
            if descriptor is not None and permissions_applied:
                os.fchmod(descriptor, original_mode)
            elif descriptor is None and permissions_applied:
                _chmod_inspection_path(
                    inspection_path,
                    original_mode,
                    stable_reference=reference is not None,
                )
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if reference is not None:
                os.close(reference)


def _inspection_path(
    path: Path,
    expected_info: os.stat_result,
) -> tuple[int | None, Path]:
    if not _can_use_proc_descriptor_paths():
        return None, path
    reference = _open_stable_reference(path, expected_info)
    return reference, Path(f"/proc/self/fd/{reference}")


def _can_use_proc_descriptor_paths() -> bool:
    return hasattr(os, "O_PATH") and Path("/proc/self/fd").is_dir()


def _chmod_inspection_path(
    path: Path,
    mode: int,
    *,
    stable_reference: bool,
) -> None:
    if stable_reference or os.chmod not in os.supports_follow_symlinks:
        os.chmod(path, mode)
    else:
        os.chmod(path, mode, follow_symlinks=False)


def _open_stable_reference(path: Path, expected_info: os.stat_result) -> int:
    descriptor = os.open(
        path,
        os.O_PATH | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    opened_info = os.fstat(descriptor)
    if (
        opened_info.st_dev != expected_info.st_dev
        or opened_info.st_ino != expected_info.st_ino
        or stat.S_IFMT(opened_info.st_mode) != stat.S_IFMT(expected_info.st_mode)
    ):
        os.close(descriptor)
        raise OSError("workspace node changed during inspection")
    return descriptor


def _restore_workspace_directories(
    handles: list[tuple[int, int, bool]],
) -> OSError | None:
    first_error: OSError | None = None
    for descriptor, original_mode, permissions_changed in reversed(handles):
        try:
            if permissions_changed:
                os.fchmod(descriptor, original_mode)
        except OSError as exc:
            first_error = first_error or exc
        finally:
            try:
                os.close(descriptor)
            except OSError as exc:
                first_error = first_error or exc
    return first_error


def _redact_secret_path_names(
    workspace: Path,
    secret_values: tuple[str, ...],
    inspected_paths: list[Path],
) -> int:
    secrets = tuple(value for value in secret_values if value)
    if not secrets:
        return 0
    renamed = 0
    paths = sorted(inspected_paths, key=lambda path: len(path.parts), reverse=True)
    for path in paths:
        relative = path.relative_to(workspace).as_posix()
        parent_relative = path.parent.relative_to(workspace).as_posix()
        basename_match = any(secret in path.name for secret in secrets)
        cross_component_match = any(
            secret in relative and secret not in parent_relative for secret in secrets
        )
        if not basename_match and not cross_component_match:
            continue
        destination = _unused_credential_redaction_path(path)
        path.rename(destination)
        renamed += 1
    return renamed


def _unused_credential_redaction_path(path: Path) -> Path:
    for _attempt in range(32):
        destination = path.with_name(
            f"credential-redacted-{secrets.token_hex(16)}"
        )
        if not os.path.lexists(destination):
            return destination
    raise ValueError("cannot allocate a credential-redacted workspace path")


def _link_workspace_for_verifier(workspace: Path, link_path: Path) -> None:
    target = Path(
        os.path.relpath(workspace.resolve(), start=link_path.parent.resolve())
    )
    try:
        link_path.symlink_to(target, target_is_directory=True)
    except OSError:
        shutil.copytree(workspace, link_path)


def _diff_dirs(
    before_root: Path, after_root: Path, before: Path, after: Path, lines: list[str]
) -> None:
    comparison = filecmp.dircmp(before, after)
    for name in sorted(comparison.left_only):
        rel = (before / name).relative_to(before_root)
        lines.append(f"deleted: {rel}\n")
    for name in sorted(comparison.right_only):
        rel = (after / name).relative_to(after_root)
        lines.append(f"added: {rel}\n")
        path = after / name
        if path.is_file():
            _append_file_diff(None, path, rel, lines)
    for name in sorted(comparison.common_files):
        left = before / name
        right = after / name
        if not filecmp.cmp(left, right, shallow=False):
            rel = right.relative_to(after_root)
            _append_file_diff(left, right, rel, lines)
    for name in sorted(comparison.common_dirs):
        _diff_dirs(before_root, after_root, before / name, after / name, lines)


def _append_file_diff(
    before: Path | None, after: Path, rel: Path, lines: list[str]
) -> None:
    try:
        before_lines = before.read_text().splitlines(keepends=True) if before else []
        after_lines = after.read_text().splitlines(keepends=True)
    except UnicodeDecodeError:
        lines.append(f"binary changed: {rel}\n")
        return

    lines.extend(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=str(rel) if before else "/dev/null",
            tofile=str(rel),
        )
    )
