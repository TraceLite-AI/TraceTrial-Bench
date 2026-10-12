from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness_tracer.runner import RunConfig as TraceRunConfig
from harness_tracer.runner import TraceRunner

from harnessbench.business_simulator import (
    BusinessSimulatorRuntime,
    BusinessSimulatorStartupError,
    persist_business_simulator_evidence,
    write_simulator_infrastructure_trace,
)
from harnessbench.ccswitch import resolve_provider
from harnessbench.container_environment import resolve_transport_environment
from harnessbench.container_runtime import (
    ContainerLimits,
    ContainerRunRequest,
    ContainerRuntimeError,
    DockerHarnessRuntime,
    load_salvaged_provider_request_evidence,
)
from harnessbench.conversation_runner import (
    ConversationRunConfig,
    ConversationSimulator,
    ConversationTraceRunner,
)
from harnessbench.execution_capabilities import (
    adapter_extra_args as execution_adapter_extra_args,
    persist_execution_probe_evidence,
    require_capabilities,
)
from harnessbench.security_capabilities import (
    ManagedPathGuard,
    adapter_extra_args as security_adapter_extra_args,
    persist_security_probe_evidence,
    require_capabilities as require_security_capabilities,
)
from harnessbench.harness_isolation import (
    _parse_yaml_subset,
    persist_harness_isolation_manifest,
    prepare_harness_isolation,
)
from harnessbench.llm_judge import (
    JudgeRequester,
    JudgeRuntime,
)
from harnessbench.models import bind_model, normalize_harness_name
from harnessbench.native_instructions import install_native_instructions
from harnessbench.model_registry import (
    SUPPORTED_CONTAINER_HARNESSES,
    ContainerBuildConfig,
    ModelRegistry,
    ModelRuntimeSpec,
    default_model_registry_path,
)
from harnessbench.protocol_runner import ProtocolRunConfig, ProtocolRunner
from harnessbench.rate_limit_policy import apply_rate_limit_skip
from harnessbench.result import (
    pending_evaluation,
    update_scored_result,
    update_scoring_error_result,
    write_result,
)
from harnessbench.runner_policy import (
    apply_evaluation_policy,
    load_simulator_manifest,
    persist_runner_policy,
    resolve_harness_invocation,
)
from harnessbench.scoring import (
    commit_scoring_artifacts,
    create_scoring_stage,
    discard_scoring_stage,
    rebase_scoring_outcome,
    score_layout,
)
from harnessbench.step_metrics import attach_step_count
from harnessbench.task_resolver import read_fixed_turns, resolve_task
from harnessbench.timing_metrics import attach_timing_metrics
from harnessbench.token_cost import attach_token_cost
from harnessbench.task_tool_mcp import (
    BRIDGE_RELATIVE_PATH,
    task_tool_environment,
)
from harnessbench.trace_report import generate_trace_report, write_report_error
from harnessbench.types import EvaluationResult, HarnessRunSpec, TaskSpec
from harnessbench.workspace import (
    load_persisted_baseline,
    persist_scoring_inputs,
    prepare_scoring_layout,
    prepare_run_dir,
    snapshot_workspace,
    validate_workspace_for_host_verifier,
    write_workspace_diff,
)


@dataclass(frozen=True)
class BenchmarkRunConfig:
    task: Path
    harness: str
    model: str
    output_dir: Path
    mode: str = "local"
    docker_auth: str = "model-registry"
    reasoning_intensity: str | None = None
    harness_path: str | None = None
    timeout: int | None = None
    docker_image: str | None = None
    model_registry: Path | None = None
    extra_args: dict[str, str] = field(default_factory=dict)
    validate_model: bool = False
    provider_source: str | None = None
    provider_id: str | None = None
    provider_name: str | None = None
    ccswitch_home: Path | None = None
    harness_isolation: str = "none"
    instance_seed: str | None = None
    judge_harness: str | None = None
    judge_model: str | None = None
    judge_harness_path: str | None = None
    evaluation_mode: str = "run-and-score"
    skip_on_rate_limit: bool = False


def _container_limits_for_task(task: TaskSpec) -> ContainerLimits:
    """Resolve opt-in task limits while preserving global Docker defaults."""

    resources = task.container_resources
    if resources is None or resources.memory_bytes is None:
        return ContainerLimits()
    return ContainerLimits(memory_bytes=resources.memory_bytes)


@dataclass(frozen=True)
class BenchmarkScoreConfig:
    run_dir: Path
    model_registry: Path | None = None
    judge_model: str | None = None


@dataclass(frozen=True)
class ExistingRunContext:
    run_dir: Path
    task: TaskSpec
    result: dict[str, Any]
    resolved_config: dict[str, Any]
    trace_summary: dict[str, Any]
    container_evidence: dict[str, Any]
    simulator_evidence: dict[str, str] | None
    baseline_workspace: Path | None


_ASYNC_CHILD_FAILURE_PROBE_CAPABILITIES = frozenset(
    {
        "native_async_child_dispatch_v1",
        "native_child_return_boundary_v1",
        "native_pushed_child_completion_v1",
    }
)


def _execution_is_scoreable(
    task: TaskSpec,
    *,
    execution_status: object,
    trace_summary: dict[str, Any],
) -> bool:
    """Allow explicit child-lifecycle probes to score auditable failed traces.

    A missing parent reactivation or a pending child at process exit is the
    behavior these probes measure. Treating every such run as verifier
    infrastructure failure would erase the mechanism under test. The exception
    stays opt-in and requires an archived structured trace; ordinary failed
    executions remain unscoreable.
    """

    trace_status = trace_summary.get("status")
    if execution_status == "finished":
        return trace_status == "finished"
    probe = task.execution_probe
    return bool(
        execution_status == "failed"
        and trace_status == "failed"
        and trace_summary.get("structured_trace_status") == "available"
        and probe is not None
        and _ASYNC_CHILD_FAILURE_PROBE_CAPABILITIES.issubset(probe.requires)
    )


def load_existing_run_context(run_dir: Path) -> ExistingRunContext:
    candidate = run_dir.expanduser()
    if candidate.is_symlink():
        raise ValueError("existing run directory must not be a symlink")
    try:
        resolved_run_dir = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"existing run directory is missing: {candidate}") from exc
    if not resolved_run_dir.is_dir():
        raise ValueError(f"existing run directory is not a directory: {candidate}")

    result = _read_run_json(resolved_run_dir / "result.json", "result")
    resolved_config = _read_run_json(
        resolved_run_dir / "config.resolved.json", "resolved config"
    )
    execution = result.get("execution")
    if not isinstance(execution, dict) or execution.get("mode") != "docker":
        raise ValueError("score-only requires a Docker run result")
    if resolved_config.get("mode") != "docker":
        raise ValueError("resolved config is not for Docker mode")
    if execution.get("status") not in {"finished", "failed"}:
        raise ValueError("existing Docker execution is not scoreable")

    task_value = resolved_config.get("task")
    if not isinstance(task_value, str) or not task_value:
        raise ValueError("resolved config task path is missing")
    task = resolve_task(Path(task_value))
    result_task_dir = result.get("task_dir")
    if (
        result.get("task_id") != task.task_id
        or not isinstance(result_task_dir, str)
        or Path(result_task_dir).expanduser().resolve() != task.task_dir
    ):
        raise ValueError("existing run task metadata does not match the task source")

    harness = result.get("harness")
    model = result.get("model")
    resolved_harness_value = resolved_config.get("harness")
    result_harness_value = harness.get("name") if isinstance(harness, dict) else None
    if not isinstance(resolved_harness_value, str) or not isinstance(
        result_harness_value, str
    ):
        raise ValueError("existing run harness configuration is invalid")
    resolved_harness = normalize_harness_name(resolved_harness_value)
    result_harness = normalize_harness_name(result_harness_value)
    if (
        not isinstance(harness, dict)
        or result_harness != resolved_harness
        or not isinstance(model, dict)
        or model.get("requested") != resolved_config.get("model")
    ):
        raise ValueError("existing run model metadata does not match resolved config")
    resolved_config = dict(resolved_config)
    resolved_config["harness"] = resolved_harness

    container = execution.get("container")
    if not isinstance(container, dict) or (
        container.get("archive_status"), container.get("cleanup_status")
    ) != ("complete", "complete"):
        raise ValueError("container archive and cleanup evidence must be complete")
    lifecycle_path = _resolve_run_artifact(
        resolved_run_dir,
        container.get("lifecycle"),
        label="container lifecycle evidence",
    )
    lifecycle = _read_run_json(lifecycle_path, "container lifecycle evidence")
    if (
        lifecycle.get("archive_status"),
        lifecycle.get("cleanup_status"),
    ) != ("complete", "complete") or lifecycle.get("container_id") != container.get(
        "container_id"
    ):
        raise ValueError("container lifecycle evidence does not match the result")

    artifacts = result.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("existing run artifact metadata is missing")
    trace_summary_path = _resolve_run_artifact(
        resolved_run_dir,
        artifacts.get("trace_summary"),
        label="trace summary",
    )
    trace_summary = _read_run_json(trace_summary_path, "trace summary")
    if not _execution_is_scoreable(
        task,
        execution_status=execution.get("status"),
        trace_summary=trace_summary,
    ):
        raise ValueError("existing run trace summary is not scoreable")
    trace_events_path = _resolve_run_artifact(
        resolved_run_dir,
        artifacts.get("trace_events"),
        label="trace events",
    )
    archive_manifest_path = _resolve_run_artifact(
        resolved_run_dir,
        artifacts.get("container_archive_manifest"),
        label="container archive manifest",
    )
    archive_files = _validate_container_archive_manifest(archive_manifest_path)
    archive_root = archive_manifest_path.parent.resolve(strict=True)
    for path, label in (
        (trace_summary_path, "trace summary"),
        (trace_events_path, "trace events"),
    ):
        try:
            relative = str(path.relative_to(archive_root))
        except ValueError as exc:
            raise ValueError(f"{label} is outside the container archive") from exc
        if relative not in archive_files:
            raise ValueError(f"{label} is not covered by the archive manifest")
    _validate_trace_paths(
        trace_summary,
        run_dir=resolved_run_dir,
        archive_root=archive_root,
        trace_summary_path=trace_summary_path,
        trace_events_path=trace_events_path,
    )
    _validate_container_identity(
        result=result,
        resolved_config=resolved_config,
        lifecycle=lifecycle,
        container=container,
    )
    verifier_gate_value = artifacts.get("verifier_gate")
    if verifier_gate_value is not None:
        verifier_gate_path = _resolve_run_artifact(
            resolved_run_dir,
            verifier_gate_value,
            label="verifier gate",
        )
        _validate_verifier_gate(
            verifier_gate_path,
            lifecycle_path=lifecycle_path,
            archive_manifest_path=archive_manifest_path,
            container=container,
        )
    elif execution.get("status") != "failed":
        # Failed asynchronous lifecycle probes created by older runners did not
        # open a verifier gate. Their lifecycle and archived trace have already
        # passed the same identity, digest, archive, and cleanup checks above;
        # finished runs must still carry the explicit gate.
        raise ValueError("verifier gate path is missing")

    simulator_evidence = _resolve_simulator_evidence(
        resolved_run_dir,
        artifacts.get("business_simulator_evidence"),
    )
    _validate_business_simulator_evidence(
        task=task,
        result=result,
        run_dir=resolved_run_dir,
        evidence=simulator_evidence,
    )
    execution_probe_evidence = _resolve_execution_probe_evidence(
        task=task,
        harness=result_harness,
        run_dir=resolved_run_dir,
        artifacts=artifacts,
    )
    if execution_probe_evidence is not None:
        trace_summary["execution_probe_evidence"] = str(
            execution_probe_evidence
        )
    security_probe_evidence = _resolve_security_probe_evidence(
        task=task,
        harness=result_harness,
        run_dir=resolved_run_dir,
        artifacts=artifacts,
    )
    if security_probe_evidence is not None:
        trace_summary["security_probe_evidence"] = str(security_probe_evidence)
    scoring_inputs_value = artifacts.get("scoring_inputs_manifest")
    baseline_workspace: Path | None = None
    if scoring_inputs_value is not None:
        scoring_inputs_path = _resolve_run_artifact(
            resolved_run_dir,
            scoring_inputs_value,
            label="scoring inputs manifest",
        )
        baseline_workspace = load_persisted_baseline(
            scoring_inputs_path,
            resolved_run_dir,
            task,
        )
    return ExistingRunContext(
        run_dir=resolved_run_dir,
        task=task,
        result=result,
        resolved_config=resolved_config,
        trace_summary=trace_summary,
        container_evidence=dict(container),
        simulator_evidence=simulator_evidence,
        baseline_workspace=baseline_workspace,
    )


def _read_run_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is missing or symlinked: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is invalid: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not a JSON object: {path}")
    return value


def _resolve_run_artifact(run_dir: Path, value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} path is missing")
    raw_path = Path(value)
    path = raw_path if raw_path.is_absolute() else run_dir / raw_path
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is missing or symlinked")
    try:
        path = path.resolve(strict=True)
        path.relative_to((run_dir / "artifacts").resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ValueError(
            f"{label} must stay below the run artifacts directory"
        ) from exc
    return path


def _resolve_simulator_evidence(
    run_dir: Path,
    value: object,
) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("business simulator evidence metadata is invalid")
    resolved: dict[str, str] = {}
    for key, raw_path in value.items():
        if not isinstance(key, str):
            raise ValueError("business simulator evidence key is invalid")
        resolved[key] = str(
            _resolve_run_artifact(
                run_dir,
                raw_path,
                label=f"business simulator evidence {key}",
            )
        )
    return resolved


def _resolve_execution_probe_evidence(
    *,
    task: TaskSpec,
    harness: str,
    run_dir: Path,
    artifacts: dict[str, Any],
) -> Path | None:
    value = artifacts.get("execution_probe_evidence")
    digest = artifacts.get("execution_probe_evidence_sha256")
    if task.execution_probe is None:
        if value is not None or digest is not None:
            raise ValueError("unexpected execution probe evidence")
        return None
    path = _resolve_run_artifact(
        run_dir,
        value,
        label="execution probe evidence",
    )
    if not isinstance(digest, str) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError("execution probe evidence digest mismatch")
    payload = _read_run_json(path, "execution probe evidence")
    report = payload.get("capability_report")
    if (
        payload.get("schema_version")
        != "harnessbench_execution_probe_evidence_v1"
        or payload.get("task_id") != task.task_id
        or payload.get("harness") != harness
        or not isinstance(report, dict)
        or report.get("compatible") is not True
        or report.get("required") != list(task.execution_probe.requires)
    ):
        raise ValueError("execution probe evidence does not match the run")
    return path


def _resolve_security_probe_evidence(
    *,
    task: TaskSpec,
    harness: str,
    run_dir: Path,
    artifacts: dict[str, Any],
) -> Path | None:
    value = artifacts.get("security_probe_evidence")
    digest = artifacts.get("security_probe_evidence_sha256")
    if task.security_probe is None:
        if value is not None or digest is not None:
            raise ValueError("unexpected security probe evidence")
        return None
    path = _resolve_run_artifact(run_dir, value, label="security probe evidence")
    if not isinstance(digest, str) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError("security probe evidence digest mismatch")
    payload = _read_run_json(path, "security probe evidence")
    report = payload.get("capability_report")
    profile_digest = hashlib.sha256(task.security_probe.profile_path.read_bytes()).hexdigest()
    if (
        payload.get("schema_version") != "harnessbench_security_probe_evidence_v1"
        or payload.get("task_id") != task.task_id
        or payload.get("harness") != harness
        or payload.get("profile_sha256") != profile_digest
        or not isinstance(report, dict)
        or report.get("compatible") is not True
        or report.get("required") != list(task.security_probe.requires)
    ):
        raise ValueError("security probe evidence does not match the run")
    return path


def _validate_business_simulator_evidence(
    *,
    task: TaskSpec,
    result: dict[str, Any],
    run_dir: Path,
    evidence: dict[str, str] | None,
) -> None:
    if task.business_simulator is None:
        if evidence is not None:
            raise ValueError("unexpected business simulator evidence")
        return
    if evidence is None:
        raise ValueError("business simulator evidence is required for score-only")
    required = {
        "manifest",
        "ledger",
        "final_state",
        "instance",
        "release_manifest",
        "scenario",
    }
    if not required.issubset(evidence):
        raise ValueError("business simulator evidence is incomplete")
    manifest_path = Path(evidence["manifest"])
    manifest = _read_run_json(manifest_path, "business simulator evidence manifest")
    harness = result.get("harness")
    model = result.get("model")
    if (
        manifest.get("schema_version") != "business_simulator_evidence_v1"
        or manifest.get("task_id") != task.task_id
        or manifest.get("run_id") != run_dir.name
        or not isinstance(harness, dict)
        or manifest.get("harness") != harness.get("name")
        or not isinstance(model, dict)
        or manifest.get("model") != model.get("harness_arg")
    ):
        raise ValueError("business simulator evidence identity is invalid")

    fields = (
        ("ledger", "ledger", "sha256"),
        ("final_state", "final_state", "file_sha256"),
        ("instance", "instance", "file_sha256"),
        ("release_manifest", "release_manifest", "file_sha256"),
        ("scenario", "scenario", "file_sha256"),
    )
    for evidence_key, manifest_key, digest_key in fields:
        path = Path(evidence[evidence_key])
        metadata = manifest.get(manifest_key)
        if (
            not isinstance(metadata, dict)
            or metadata.get("path") != path.name
            or path.parent != manifest_path.parent
            or metadata.get(digest_key) != hashlib.sha256(path.read_bytes()).hexdigest()
        ):
            raise ValueError(
                f"business simulator evidence {evidence_key} does not match its manifest"
            )

    try:
        scenario = json.loads(
            Path(evidence["scenario"]).read_text(encoding="utf-8")
        )
        scenario_digest = hashlib.sha256(
            json.dumps(scenario, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("business simulator scenario is invalid") from exc
    instance_metadata = manifest.get("instance")
    scenario_metadata = manifest.get("scenario")
    if (
        not isinstance(instance_metadata, dict)
        or not isinstance(scenario_metadata, dict)
        or scenario_metadata.get("canonical_sha256") != scenario_digest
        or instance_metadata.get("private_scenario_sha256") != scenario_digest
        or instance_metadata.get("scenario_sha256") != scenario_digest
    ):
        raise ValueError("business simulator evidence scenario does not match task")


def _validate_container_archive_manifest(manifest_path: Path) -> set[str]:
    manifest = _read_run_json(manifest_path, "container archive manifest")
    files = manifest.get("files")
    if (
        manifest.get("schema_version") != "harnessbench_container_archive_v1"
        or not isinstance(files, list)
    ):
        raise ValueError("container archive manifest has an invalid schema")
    archive_root = manifest_path.parent.resolve(strict=True)
    expected: dict[str, tuple[int, str]] = {}
    for entry in files:
        if not isinstance(entry, dict):
            raise ValueError("container archive manifest contains an invalid entry")
        raw_path = entry.get("path")
        size = entry.get("size")
        digest = entry.get("sha256")
        if (
            not isinstance(raw_path, str)
            or not raw_path
            or Path(raw_path).is_absolute()
            or ".." in Path(raw_path).parts
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or raw_path in expected
        ):
            raise ValueError("container archive manifest contains an invalid entry")
        expected[raw_path] = (size, digest)

    observed: set[str] = set()
    for path in sorted(manifest_path.parent.rglob("*")):
        if path == manifest_path:
            continue
        if path.is_symlink():
            raise ValueError("container archive manifest covers a symbolic link")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("container archive contains an unsafe node")
        try:
            resolved = path.resolve(strict=True)
            relative = str(resolved.relative_to(archive_root))
        except (OSError, ValueError) as exc:
            raise ValueError("container archive file escapes its root") from exc
        observed.add(relative)
        metadata = expected.get(relative)
        payload = path.read_bytes()
        if metadata is None or metadata != (
            len(payload),
            hashlib.sha256(payload).hexdigest(),
        ):
            raise ValueError("container archive manifest does not match archived files")
    if observed != set(expected):
        raise ValueError("container archive manifest file set is incomplete")
    return observed


def _validate_trace_paths(
    trace_summary: dict[str, Any],
    *,
    run_dir: Path,
    archive_root: Path,
    trace_summary_path: Path,
    trace_events_path: Path,
) -> None:
    for key, expected in (
        ("summary_path", trace_summary_path),
        ("events_path", trace_events_path),
    ):
        value = trace_summary.get(key)
        if not isinstance(value, str):
            raise ValueError(f"trace {key} is missing")
        try:
            raw_path = Path(value)
            if raw_path.is_absolute():
                observed = raw_path.resolve(strict=True)
            else:
                if not raw_path.parts or ".." in raw_path.parts:
                    raise ValueError
                observed = _resolve_relative_trace_path(
                    raw_path,
                    expected=expected,
                    run_dir=run_dir,
                    archive_root=archive_root,
                )
        except ValueError as exc:
            raise ValueError(f"trace {key} is invalid") from exc
        except OSError as exc:
            raise ValueError(f"trace {key} is invalid") from exc
        if observed != expected:
            raise ValueError(f"trace {key} does not match result metadata")


def _resolve_relative_trace_path(
    raw_path: Path,
    *,
    expected: Path,
    run_dir: Path,
    archive_root: Path,
) -> Path:
    for root in (archive_root, run_dir):
        try:
            return (root / raw_path).resolve(strict=True)
        except OSError:
            continue

    expected_run_relative = expected.relative_to(run_dir)
    legacy_suffix = (run_dir.name, *expected_run_relative.parts)
    if (
        len(raw_path.parts) >= len(legacy_suffix)
        and raw_path.parts[-len(legacy_suffix) :] == legacy_suffix
    ):
        return expected.resolve(strict=True)
    raise ValueError("relative trace path is not rooted in the run archive")


def _validate_container_identity(
    *,
    result: dict[str, Any],
    resolved_config: dict[str, Any],
    lifecycle: dict[str, Any],
    container: dict[str, Any],
) -> None:
    execution = result.get("execution")
    model = result.get("model")
    if not isinstance(execution, dict) or not isinstance(model, dict):
        raise ValueError("existing run execution identity is invalid")
    lifecycle_model = lifecycle.get("model")
    lifecycle_image = lifecycle.get("image")
    expected_image = {
        "reference": container.get("image_reference"),
        "image_id": container.get("image_id"),
        "digest": container.get("image_digest"),
        "built": container.get("image_built"),
    }
    if (
        lifecycle.get("schema_version")
        != "harnessbench_container_lifecycle_v1"
        or not isinstance(lifecycle.get("run_token"), str)
        or not lifecycle.get("run_token")
        or not isinstance(lifecycle.get("container_name"), str)
        or not lifecycle.get("container_name")
        or lifecycle.get("task_id") != result.get("task_id")
        or normalize_harness_name(str(lifecycle.get("harness")))
        != resolved_config.get("harness")
        or not isinstance(lifecycle_model, dict)
        or lifecycle_model.get("alias") != resolved_config.get("model")
        or lifecycle_model.get("provider") != model.get("provider")
        or lifecycle_model.get("model") != model.get("canonical")
        or lifecycle_model.get("harness_model") != model.get("harness_arg")
        or normalize_harness_name(str(lifecycle_model.get("harness")))
        != resolved_config.get("harness")
        or lifecycle_image != expected_image
        or lifecycle.get("gateway") != container.get("gateway")
        or execution.get("gateway") != container.get("gateway")
        or execution.get("docker_image") != container.get("image_id")
        or re.fullmatch(r"[0-9a-f]{64}", str(container.get("container_id")))
        is None
        or re.fullmatch(r"sha256:[0-9a-f]{64}", str(container.get("image_id")))
        is None
    ):
        raise ValueError("container lifecycle identity does not match the result")


def _validate_verifier_gate(
    gate_path: Path,
    *,
    lifecycle_path: Path,
    archive_manifest_path: Path,
    container: dict[str, Any],
) -> None:
    gate = _read_run_json(gate_path, "verifier gate")
    lifecycle_digest = hashlib.sha256(lifecycle_path.read_bytes()).hexdigest()
    archive_manifest_digest = hashlib.sha256(
        archive_manifest_path.read_bytes()
    ).hexdigest()
    run_dir = lifecycle_path.parent.parent
    if (
        gate.get("schema_version") != "harnessbench_verifier_gate_v1"
        or gate.get("verifier_execution") != "host-only"
        or gate.get("container_id") != container.get("container_id")
        or gate.get("image_id") != container.get("image_id")
        or gate.get("archive_status") != "complete"
        or gate.get("cleanup_status") != "complete"
        or gate.get("container_lifecycle") != container.get("lifecycle")
        or gate.get("container_lifecycle_sha256") != lifecycle_digest
        or gate.get("container_archive_manifest")
        != _relative_artifact(archive_manifest_path, run_dir)
        or gate.get("container_archive_manifest_sha256")
        != archive_manifest_digest
    ):
        raise ValueError("verifier gate does not match container lifecycle evidence")


class BenchmarkRunner:
    def __init__(
        self,
        *,
        trace_runner: TraceRunner | None = None,
        protocol_runner: ProtocolRunner | None = None,
        conversation_runner: ConversationTraceRunner | None = None,
        container_runtime: DockerHarnessRuntime | None = None,
        judge_requester: JudgeRequester | None = None,
    ) -> None:
        self.trace_runner = trace_runner or TraceRunner()
        self.protocol_runner = protocol_runner or ProtocolRunner()
        self.conversation_runner = conversation_runner or ConversationTraceRunner(
            trace_runner=self.trace_runner
        )
        self.container_runtime = container_runtime or DockerHarnessRuntime()
        self.judge_requester = judge_requester

    def score(self, config: BenchmarkScoreConfig) -> dict[str, Any]:
        context = load_existing_run_context(config.run_dir)
        resolved_config = context.resolved_config
        harness = resolved_config.get("harness")
        model_alias = resolved_config.get("model")
        if not isinstance(harness, str) or not isinstance(model_alias, str):
            raise ValueError("existing run harness or model configuration is invalid")

        saved_registry = resolved_config.get("model_registry")
        docker_auth = str(resolved_config.get("docker_auth") or "model-registry")
        registry: ModelRegistry | None = None
        registry_path: Path | None = None
        if config.model_registry is not None:
            registry_path = config.model_registry
        elif isinstance(saved_registry, str) and saved_registry:
            registry_path = Path(saved_registry)
        elif docker_auth != "subscription":
            registry_path = default_model_registry_path()

        if docker_auth == "subscription":
            model_runtime = ModelRuntimeSpec(
                alias=model_alias,
                provider="subscription",
                model=model_alias,
                base_url="",
                api_mode="native_subscription",
                harness=harness,
                harness_model=model_alias,
                harness_provider=None,
                api_key="",
            )
            if registry_path is not None:
                registry = ModelRegistry.load(registry_path)
        else:
            assert registry_path is not None
            registry = ModelRegistry.load(registry_path)
            model_runtime = registry.resolve(model_alias, harness)

        timeout_value = resolved_config.get("effective_timeout")
        if timeout_value is not None and (
            not isinstance(timeout_value, int)
            or isinstance(timeout_value, bool)
            or timeout_value <= 0
        ):
            raise ValueError("existing run timeout configuration is invalid")
        invocation_report = resolved_config.get("invocation_policy")
        if not isinstance(invocation_report, dict):
            raise ValueError("existing run invocation policy is invalid")

        judge_alias: str | None = None
        judge_runtime: JudgeRuntime | None = None
        judge_model_runtime: ModelRuntimeSpec | None = None
        if context.task.scoring is not None:
            saved_judge_model = resolved_config.get("judge_model")
            judge_alias = config.judge_model or (
                saved_judge_model
                if isinstance(saved_judge_model, str) and saved_judge_model
                else model_alias
            )
            if registry is None:
                raise ValueError(
                    "score-only for a subscription-auth layered task requires "
                    "--judge-model and --model-registry"
                )
            judge_model_runtime = registry.resolve(judge_alias, harness)
            judge_runtime = JudgeRuntime(
                provider=judge_model_runtime.provider,
                model=judge_model_runtime.model,
                base_url=judge_model_runtime.base_url,
                api_key=judge_model_runtime.api_key,
                api_mode=judge_model_runtime.api_mode,
                timeout=float(min(timeout_value or 180, 300)),
            )

        secret_values = tuple(
            dict.fromkeys(
                value
                for value in (
                    model_runtime.api_key,
                    (
                        judge_model_runtime.api_key
                        if judge_model_runtime is not None
                        else None
                    ),
                )
                if value
            )
        )
        with tempfile.TemporaryDirectory(
            prefix="harnessbench-score-private-"
        ) as private_root:
            layout = prepare_scoring_layout(
                context.task,
                context.run_dir,
                private_dir=Path(private_root) / "private",
                baseline_workspace=context.baseline_workspace,
                simulator_evidence=context.simulator_evidence,
            )
            validate_workspace_for_host_verifier(
                layout.workspace_dir,
                secret_values=secret_values,
            )
            stage = create_scoring_stage(context.run_dir)
            scoring_layout = replace(layout, artifacts_dir=stage)
            try:
                scoring = score_layout(
                    task=context.task,
                    layout=scoring_layout,
                    trace_summary=context.trace_summary,
                    invocation_report=invocation_report,
                    simulator_evidence=context.simulator_evidence,
                    harness=harness,
                    model=str(
                        context.result.get("model", {}).get(
                            "canonical",
                            model_runtime.model,
                        )
                    ),
                    timeout=timeout_value,
                    judge_runtime=judge_runtime,
                    judge_requester=self.judge_requester,
                )
                scoring = rebase_scoring_outcome(
                    scoring,
                    stage=stage,
                    artifacts_dir=context.run_dir / "artifacts",
                )
                with commit_scoring_artifacts(context.run_dir, stage):
                    result = update_scored_result(
                        run_dir=context.run_dir,
                        existing_result=context.result,
                        evaluation=scoring.effective,
                        raw_evaluation=scoring.raw,
                        runner_policy=scoring.policy,
                        runner_policy_path=scoring.policy_path,
                    )
                    _update_resolved_config_for_score(
                        context.run_dir,
                        resolved_config,
                        model_registry=(registry.source if registry else None),
                        judge_model=judge_alias,
                    )
                    _generate_trace_report_safely(
                        context.run_dir,
                        result,
                        api_key=model_runtime.api_key,
                    )
            except Exception as exc:
                discard_scoring_stage(stage)
                message = str(exc) or type(exc).__name__
                for secret in secret_values:
                    message = _redact_runtime_error(message, secret)
                result = update_scoring_error_result(
                    run_dir=context.run_dir,
                    existing_result=context.result,
                    error_type=type(exc).__name__,
                    error_message=message,
                )
                _update_resolved_config_for_score(
                    context.run_dir,
                    resolved_config,
                    model_registry=(registry.source if registry else None),
                    judge_model=judge_alias,
                )
                return result

        return result

    def run(self, config: BenchmarkRunConfig) -> dict:
        if config.mode not in {"local", "docker"}:
            raise ValueError("supported modes: local, docker")
        if config.docker_auth not in {"model-registry", "subscription"}:
            raise ValueError("supported Docker auth modes: model-registry, subscription")
        if config.mode != "docker" and config.docker_auth != "model-registry":
            raise ValueError("--docker-auth requires Docker mode")
        if config.skip_on_rate_limit and config.mode != "docker":
            raise ValueError("--skip-on-rate-limit requires Docker mode")
        if config.skip_on_rate_limit and config.docker_auth != "model-registry":
            raise ValueError(
                "--skip-on-rate-limit requires Docker model-registry auth"
            )
        if config.evaluation_mode not in {"run-only", "run-and-score"}:
            raise ValueError(
                "supported runner evaluation modes: run-only, run-and-score"
            )
        if config.evaluation_mode == "run-only" and config.mode != "docker":
            raise ValueError("run-only requires Docker mode")
        if config.harness_isolation not in {"none", "macos"}:
            raise ValueError("supported harness isolation modes: none, macos")
        # Host-side simulator/socket helpers briefly change the process CWD.
        # Resolve caller-provided paths before any concurrent run can do that,
        # otherwise relative output roots can point into another run's workspace.
        output_root = config.output_dir.expanduser().resolve(strict=False)

        task = resolve_task(config.task)
        if task.conversation_protocol is not None and task.conversation.mode != "single_turn":
            raise ValueError(
                "conversation_protocol cannot be combined with ordinary multi-turn execution"
            )
        harness_name = normalize_harness_name(config.harness)
        if harness_name == "deepseek" and config.mode != "docker":
            raise ValueError("DeepSeek Harness is supported only in Docker mode")
        capability = require_capabilities(task, harness_name)
        security_capability = require_security_capabilities(task, harness_name)
        registry: ModelRegistry | None = None
        model_runtime: ModelRuntimeSpec | None = None
        judge_model_runtime: ModelRuntimeSpec | None = None
        container_build = ContainerBuildConfig()
        subscription_auth: dict[str, Any] | None = None
        if config.mode == "docker":
            _validate_docker_config(config, harness_name)
            if task.conversation_protocol is not None:
                raise ValueError(
                    "Docker mode does not yet support conversation_protocol tasks"
                )
            if config.docker_auth == "subscription":
                subscription_auth = _collect_subscription_auth(harness_name)
                if task.model_call_budget is not None:
                    raise ValueError(
                        "Docker subscription auth does not support model_call_budget tasks"
                    )
                if config.model_registry is not None:
                    registry = ModelRegistry.load(
                        config.model_registry.expanduser().resolve(strict=False)
                    )
                    container_build = registry.container_build
                model_runtime = ModelRuntimeSpec(
                    alias=config.model,
                    provider="subscription",
                    model=config.model,
                    base_url="",
                    api_mode="native_subscription",
                    harness=harness_name,
                    harness_model=config.model,
                    harness_provider=None,
                    api_key="",
                )
                binding = replace(
                    bind_model(
                        harness=harness_name,
                        model=config.model,
                        validate=False,
                    ),
                    provider="subscription",
                    canonical=config.model,
                    harness_arg=config.model,
                    binding_mode="subscription",
                    validation_status="subscription_state_resolved",
                )
                if (
                    config.evaluation_mode == "run-and-score"
                    and task.scoring is not None
                ):
                    if registry is None or not config.judge_model:
                        raise ValueError(
                            "run-and-score for layered scoring in Docker "
                            "subscription auth requires --judge-model and "
                            "--model-registry, or use --evaluation-mode run-only"
                        )
                    judge_model_runtime = registry.resolve(
                        config.judge_model,
                        harness_name,
                    )
            else:
                registry_path = config.model_registry or default_model_registry_path()
                registry = ModelRegistry.load(
                    registry_path.expanduser().resolve(strict=False)
                )
                container_build = registry.container_build
                model_runtime = registry.resolve(config.model, harness_name)
                binding = replace(
                    bind_model(
                        harness=harness_name,
                        model=model_runtime.harness_model,
                        validate=False,
                    ),
                    requested=config.model,
                    provider=model_runtime.provider,
                    canonical=model_runtime.model,
                    harness_arg=model_runtime.harness_model,
                    binding_mode="model_registry",
                    validation_status="registry_resolved",
                )
                judge_model_runtime = model_runtime
                if (
                    config.evaluation_mode == "run-and-score"
                    and task.scoring is not None
                    and config.judge_model
                ):
                    judge_model_runtime = registry.resolve(
                        config.judge_model,
                        harness_name,
                    )
        else:
            binding = bind_model(
                harness=harness_name,
                model=config.model,
                harness_path=config.harness_path,
                validate=config.validate_model,
            )
        if binding.binding_status == "failed":
            detail = (
                f": {binding.validation_message}" if binding.validation_message else ""
            )
            raise ValueError(
                f"model validation failed for {config.model!r} on {harness_name}{detail}"
            )
        invocation = resolve_harness_invocation(
            harness=harness_name,
            requested_timeout=config.timeout,
            task_max_wall_time_sec=task.max_wall_time_sec,
            task_max_turns=task.max_turns,
            requested_extra_args=config.extra_args,
            requested_reasoning_intensity=config.reasoning_intensity,
            strict=(config.mode == "docker" or config.harness_isolation == "macos"),
        )
        effective_extra_args = {
            **invocation.extra_args,
            **execution_adapter_extra_args(task, harness_name),
            **security_adapter_extra_args(task),
        }
        run_dir = output_root / _run_dir_name(
            task.task_id, harness_name, binding.canonical
        )
        private_root = tempfile.TemporaryDirectory(prefix="harnessbench-private-")
        private_dir = Path(private_root.name) / "private"
        try:
            layout = prepare_run_dir(task, run_dir, private_dir=private_dir)
            if task.task_tools is not None:
                effective_extra_args.update(
                    {
                        "harnessbench-task-tools": "true",
                        "harnessbench-task-tool-bridge": str(
                            layout.workspace_dir / BRIDGE_RELATIVE_PATH
                        ),
                    }
                )
            resolved_docker_image = config.docker_image
            if config.mode == "docker":
                task_runtime_image = _task_runtime_image(task, harness_name)
                if resolved_docker_image is None:
                    resolved_docker_image = task_runtime_image
                if task_runtime_image is not None and config.docker_image is None:
                    _build_task_runtime_image(
                        task,
                        task_runtime_image,
                        layout.artifacts_dir / "task_image_build.log",
                        harness=harness_name,
                        build_config=container_build,
                    )
            native_instruction_evidence = install_native_instructions(
                task,
                harness=harness_name,
                agent_dir=layout.agent_dir,
                artifacts_dir=layout.artifacts_dir,
                instance_seed=config.instance_seed,
            )
            snapshot_workspace(layout.workspace_dir, layout.baseline_dir)

            provider_context = None
            provider_env: dict[str, str] = {}
            provider_artifacts: dict[str, str] = {}
            if config.mode == "local" and config.provider_source:
                if config.provider_source != "ccswitch":
                    raise ValueError(
                        f"unsupported provider source: {config.provider_source}"
                    )
                provider_context = resolve_provider(
                    harness=harness_name,
                    model=config.model,
                    run_dir=run_dir,
                    provider_id=config.provider_id,
                    provider_name=config.provider_name,
                    ccswitch_home=config.ccswitch_home,
                )
                provider_env = provider_context.env
                provider_artifacts = provider_context.artifacts

            isolation = prepare_harness_isolation(
                mode=(config.harness_isolation if config.mode == "local" else "none"),
                harness=harness_name,
                agent_dir=layout.agent_dir,
                private_dir=layout.private_dir,
                benchmark_repo=Path(__file__).resolve().parents[1],
                output_root=output_root,
                harness_path=config.harness_path,
                provider_environment=provider_env,
            )
            provider_env = _runtime_env(
                harness_name,
                isolation.environment,
                layout.agent_dir,
            )
            _configure_openclaw_workspace(
                harness=harness_name, layout=layout, environment=provider_env
            )
            if task.business_simulator is not None:
                provider_env["WORKSPACE"] = str(layout.workspace_dir)
            if task.task_tools is not None:
                if config.mode == "local":
                    provider_env.update(
                        task_tool_environment(
                            task.task_tools.tools,
                            socket_path=layout.workspace_dir
                            / ".harnessbench-business-simulator.sock",
                        )
                    )
                if config.mode == "local":
                    if config.harness_isolation != "none":
                        raise ValueError(
                            "task_tools local mode does not support harness isolation"
                        )
                    _configure_local_task_tools(
                        harness=harness_name,
                        layout=layout,
                        environment=provider_env,
                        extra_args=effective_extra_args,
                    )
            if isolation.manifest is not None:
                provider_artifacts["harness_isolation_manifest"] = str(
                    Path("artifacts") / "harness_isolation" / "manifest.json"
                )
            run_spec = HarnessRunSpec(
                harness=harness_name,
                model=config.model,
                mode=config.mode,
                harness_path=config.harness_path,
                timeout=invocation.timeout,
                docker_image=(
                    resolved_docker_image if config.mode == "docker" else None
                ),
                extra_args=effective_extra_args,
                provider_source=(
                    (
                        "subscription"
                        if config.mode == "docker"
                        and config.docker_auth == "subscription"
                        else "model_registry"
                    )
                    if model_runtime is not None
                    else (
                        provider_context.source
                        if provider_context
                        else config.provider_source
                    )
                ),
                provider_id=(
                    (
                        model_runtime.provider
                        if model_runtime is not None
                        and not (
                            config.mode == "docker"
                            and config.docker_auth == "subscription"
                        )
                        else None
                    )
                    if model_runtime is not None
                    else (
                        provider_context.provider_id
                        if provider_context
                        else config.provider_id
                    )
                ),
                provider_name=(
                    (
                        model_runtime.provider
                        if model_runtime is not None
                        and not (
                            config.mode == "docker"
                            and config.docker_auth == "subscription"
                        )
                        else None
                    )
                    if model_runtime is not None
                    else (
                        provider_context.provider_name
                        if provider_context
                        else config.provider_name
                    )
                ),
                provider_artifacts=provider_artifacts,
                harness_isolation=config.harness_isolation,
            )

            simulator = BusinessSimulatorRuntime(
                task,
                layout,
                instance_seed=config.instance_seed,
                configuration_context={
                    "harness": harness_name,
                    "model": binding.canonical,
                    "mode": config.mode,
                    "harness_isolation": config.harness_isolation,
                    "timeout_seconds": run_spec.timeout,
                },
                enable_container_transport=config.mode == "docker",
            )
            verifier_allowed = config.mode == "local"
            verifier_skip_reason = (
                "container archive/cleanup evidence was not valid"
            )
            rate_limit_skip: dict[str, object] | None = None
            container_evidence: dict[str, Any] | None = None
            transport_environment = (
                resolve_transport_environment() if config.mode == "docker" else {}
            )
            try:
                managed_path_guard = ManagedPathGuard(task, layout.workspace_dir)
                with simulator, managed_path_guard:
                    if config.mode == "docker":
                        assert model_runtime is not None
                        try:
                            execution = self.container_runtime.run(
                                ContainerRunRequest(
                                    task_id=task.task_id,
                                    harness=harness_name,
                                    model=model_runtime,
                                    auth_mode=config.docker_auth,
                                    subscription_auth=subscription_auth,
                                    prompt=task.prompt,
                                    workspace=layout.workspace_dir,
                                    artifacts_dir=layout.artifacts_dir,
                                    timeout=run_spec.timeout,
                                    conversation_mode=task.conversation.mode,
                                    turns=(
                                        tuple(read_fixed_turns(task.conversation))
                                        if task.conversation.mode == "fixed_turns"
                                        else ()
                                    ),
                                    conversation_max_turns=task.conversation.max_turns,
                                    new_session_before_turns=(
                                        task.conversation.new_session_before_turns
                                    ),
                                    new_session_context=(
                                        task.conversation.new_session_context
                                    ),
                                    conversation_simulator_source=(
                                        task.conversation.simulator_entrypoint.read_text(
                                            encoding="utf-8"
                                        )
                                        if task.conversation.simulator_entrypoint is not None
                                        else None
                                    ),
                                    business_simulator_endpoint=(
                                        simulator.business_endpoint
                                        if task.business_simulator is not None
                                        else None
                                    ),
                                    task_tools=(
                                        {
                                            "schema_version": "harnessbench_task_tools_v1",
                                            "tools": list(task.task_tools.tools),
                                        }
                                        if task.task_tools is not None
                                        else None
                                    ),
                                    task_tool_endpoint=(
                                        simulator.task_tool_endpoint
                                        if task.task_tools is not None
                                        else None
                                    ),
                                    model_call_budget=(
                                        {
                                            "version": task.model_call_budget.version,
                                            "limit": task.model_call_budget.limit,
                                            "finalization_reserve": (
                                                task.model_call_budget.finalization_reserve
                                            ),
                                        }
                                        if task.model_call_budget is not None
                                        else None
                                    ),
                                    container_build=container_build,
                                    extra_args=effective_extra_args,
                                    image_override=resolved_docker_image,
                                    limits=_container_limits_for_task(task),
                                    transport_environment=transport_environment,
                                    transient_workspace_sockets=(
                                        (simulator.socket_path,)
                                        if simulator.enabled
                                        else ()
                                    ),
                                )
                            )
                            container_evidence = _verified_container_evidence(
                                execution, run_dir
                            )
                            trace_summary = execution.trace_summary
                            trace_summary["container"] = container_evidence
                            if execution.gateway is not None:
                                trace_summary["gateway"] = execution.gateway
                            run_spec = replace(
                                run_spec, docker_image=execution.image.image_id
                            )
                            execution_status = trace_summary.get("status")
                            verifier_allowed = _execution_is_scoreable(
                                task,
                                execution_status=execution_status,
                                trace_summary=trace_summary,
                            )
                            if not verifier_allowed:
                                verifier_skip_reason = (
                                    "execution did not finish successfully "
                                    f"(status={execution_status or 'missing'})"
                                )
                        except Exception as exc:
                            message = _redact_runtime_error(
                                str(exc), model_runtime.api_key
                            )
                            container_evidence = _load_failed_container_evidence(
                                layout.artifacts_dir, run_dir
                            )
                            trace_summary = _write_container_infrastructure_trace(
                                layout=layout,
                                harness=harness_name,
                                model=binding.harness_arg,
                                error_type="container_runtime",
                                error_message=message,
                            )
                            trace_summary["container"] = container_evidence
                            salvaged_provider_evidence = (
                                load_salvaged_provider_request_evidence(
                                    layout.artifacts_dir
                                )
                            )
                            if salvaged_provider_evidence is not None:
                                trace_summary["provider_request_evidence"] = (
                                    salvaged_provider_evidence
                                )
                            verifier_allowed = False
                    elif task.conversation_protocol is not None:
                        trace_summary = self.protocol_runner.run(
                            ProtocolRunConfig(
                                task=task,
                                harness=harness_name,
                                model=binding.harness_arg,
                                layout=layout,
                                harness_path=config.harness_path,
                                timeout=run_spec.timeout,
                                env=provider_env,
                            )
                        )
                    elif task.conversation.mode != "single_turn":
                        messages = (
                            tuple(read_fixed_turns(task.conversation))
                            if task.conversation.mode == "fixed_turns"
                            else (task.prompt,)
                        )
                        conversation_simulator = (
                            ConversationSimulator(task.conversation.simulator_entrypoint)
                            if task.conversation.simulator_entrypoint is not None
                            else None
                        )
                        trace_summary = self.conversation_runner.run(
                            ConversationRunConfig(
                                task_id=task.task_id,
                                harness=_trace_harness_name(harness_name),
                                model=binding.harness_arg,
                                initial_messages=messages,
                                output_dir=layout.artifacts_dir / "trace",
                                harness_path=config.harness_path,
                                timeout=run_spec.timeout,
                                max_turns=task.conversation.max_turns,
                                new_session_before_turns=(
                                    task.conversation.new_session_before_turns
                                ),
                                new_session_context=(
                                    task.conversation.new_session_context
                                ),
                                extra_args=effective_extra_args,
                                cwd=_harness_cwd(harness_name, layout),
                                env=provider_env,
                                command_prefix=isolation.command_prefix,
                                replace_environment=isolation.replace_environment,
                                isolation=isolation.trace_info,
                                redact_paths=isolation.redact_paths,
                                simulator=conversation_simulator,
                            )
                        )
                    else:
                        trace_summary = self.trace_runner.run(
                            TraceRunConfig(
                                harness=_trace_harness_name(harness_name),
                                model=binding.harness_arg,
                                input_query=task.prompt,
                                output_dir=layout.artifacts_dir / "trace",
                                harness_path=config.harness_path,
                                timeout=run_spec.timeout,
                                extra_args=effective_extra_args,
                                cwd=_harness_cwd(harness_name, layout),
                                env=provider_env,
                                command_prefix=isolation.command_prefix,
                                replace_environment=isolation.replace_environment,
                                isolation=isolation.trace_info,
                                redact_paths=isolation.redact_paths,
                            )
                        )
            except BusinessSimulatorStartupError as exc:
                trace_summary = write_simulator_infrastructure_trace(
                    layout=layout,
                    harness=harness_name,
                    model=binding.harness_arg,
                    prompt=task.prompt,
                    error=exc,
                )
                if config.mode == "docker":
                    verifier_allowed = False

            rate_limit_skip = apply_rate_limit_skip(
                trace_summary,
                enabled=config.skip_on_rate_limit,
            )
            if rate_limit_skip is not None:
                verifier_allowed = False
                verifier_skip_reason = "provider returned HTTP 429 rate limit"

            diff_path = layout.artifacts_dir / "workspace.diff"
            if config.mode == "docker" and verifier_allowed:
                try:
                    validate_workspace_for_host_verifier(
                        layout.workspace_dir,
                        secret_values=(
                            (model_runtime.api_key,)
                            if model_runtime is not None
                            else ()
                        ),
                    )
                except ValueError as exc:
                    trace_summary = _write_container_infrastructure_trace(
                        layout=layout,
                        harness=harness_name,
                        model=binding.harness_arg,
                        error_type="unsafe_workspace",
                        error_message=str(exc),
                    )
                    trace_summary["container"] = container_evidence
                    verifier_allowed = False
                    verifier_skip_reason = str(exc)
            write_workspace_diff(layout.baseline_dir, layout.workspace_dir, diff_path)
            if config.mode == "docker" and verifier_allowed:
                assert container_evidence is not None
                _persist_verifier_gate(layout, container_evidence)
            simulator_evidence = persist_business_simulator_evidence(
                task,
                layout,
                harness=harness_name,
                model=binding.harness_arg,
            )
            if simulator_evidence is not None:
                trace_summary["business_simulator_evidence"] = simulator_evidence
            execution_probe_evidence = persist_execution_probe_evidence(
                task,
                harness=harness_name,
                layout=layout,
                trace_summary=trace_summary,
                capability=capability,
            )
            if execution_probe_evidence is not None:
                trace_summary["execution_probe_evidence"] = (
                    execution_probe_evidence
                )
            security_probe_evidence = persist_security_probe_evidence(
                task,
                harness=harness_name,
                layout=layout,
                trace_summary=trace_summary,
                capability=security_capability,
                backstop_active=managed_path_guard.active,
            )
            if security_probe_evidence is not None:
                trace_summary["security_probe_evidence"] = security_probe_evidence
            if config.mode == "docker" and verifier_allowed:
                scoring_inputs_manifest = persist_scoring_inputs(task, layout)
                trace_summary["scoring_inputs_manifest"] = str(
                    scoring_inputs_manifest
                )
            if native_instruction_evidence is not None:
                trace_summary["native_instruction_evidence"] = (
                    native_instruction_evidence
                )
            isolation_manifest = persist_harness_isolation_manifest(
                isolation,
                artifacts_dir=layout.artifacts_dir,
            )
            if isolation_manifest is not None:
                trace_summary["harness_isolation_manifest"] = isolation_manifest

            attach_step_count(trace_summary)
            attach_timing_metrics(trace_summary)
            attach_token_cost(trace_summary, binding.canonical)

            runner_policy: dict[str, Any] = {}
            runner_policy_path: Path | None = None
            if rate_limit_skip is not None:
                evaluation = _write_rate_limit_skipped_verifier(
                    layout,
                    classification=str(
                        rate_limit_skip.get("classification") or "unspecified"
                    ),
                )
                raw_evaluation = evaluation
            elif config.evaluation_mode == "run-only":
                evaluation = pending_evaluation(run_dir)
                raw_evaluation = evaluation
            elif verifier_allowed:
                judge_runtime: JudgeRuntime | None = None
                if task.scoring is not None:
                    if config.mode == "docker":
                        if (
                            not isinstance(container_evidence, dict)
                            or container_evidence.get("cleanup_status") != "complete"
                        ):
                            raise ContainerRuntimeError(
                                "LLM judge requires completed container cleanup"
                            )
                        assert judge_model_runtime is not None
                        judge_runtime = JudgeRuntime(
                            provider=judge_model_runtime.provider,
                            model=judge_model_runtime.model,
                            base_url=judge_model_runtime.base_url,
                            api_key=judge_model_runtime.api_key,
                            api_mode=judge_model_runtime.api_mode,
                            timeout=float(min(run_spec.timeout or 180, 300)),
                        )
                    else:
                        judge_runtime = _local_judge_runtime(
                            config=config,
                            model=binding.canonical,
                            provider_env=provider_env,
                            provider=(
                                provider_context.provider_id
                                if provider_context is not None
                                else "openai-compatible"
                            ),
                            timeout=run_spec.timeout,
                        )
                scoring = score_layout(
                    task=task,
                    layout=layout,
                    trace_summary=trace_summary,
                    invocation_report=invocation.report,
                    simulator_evidence=simulator_evidence,
                    harness=harness_name,
                    model=binding.canonical,
                    timeout=run_spec.timeout,
                    judge_runtime=judge_runtime,
                    judge_requester=self.judge_requester,
                )
                evaluation = scoring.effective
                raw_evaluation = scoring.raw
                runner_policy = scoring.policy
                runner_policy_path = scoring.policy_path
            else:
                raw_evaluation = _write_skipped_verifier(
                    layout,
                    reason=verifier_skip_reason,
                )
                policy_result = apply_evaluation_policy(
                    task=task,
                    trace_summary=trace_summary,
                    raw_evaluation=raw_evaluation,
                    simulator_manifest=load_simulator_manifest(simulator_evidence),
                    invocation_report=invocation.report,
                )
                evaluation = policy_result.effective
                raw_evaluation = policy_result.raw
                runner_policy = policy_result.report
                runner_policy_path = Path(
                    persist_runner_policy(
                        policy_result.report,
                        layout.artifacts_dir,
                    )
                )

            result = write_result(
                task=task,
                run_spec=run_spec,
                model_binding=binding,
                run_dir=run_dir,
                trace_summary=trace_summary,
                evaluation=evaluation,
                raw_evaluation=raw_evaluation,
                runner_policy=runner_policy,
                runner_policy_path=runner_policy_path,
                workspace_diff=diff_path,
                evaluation_mode=config.evaluation_mode,
            )
            _write_resolved_config(
                run_dir,
                config,
                result,
                effective_timeout=invocation.timeout,
                effective_extra_args=effective_extra_args,
                invocation_report=invocation.report,
                execution_capabilities=capability,
                security_capabilities=security_capability,
            )
            _generate_trace_report_safely(
                run_dir,
                result,
                api_key=(model_runtime.api_key if model_runtime is not None else None),
            )
            return result
        finally:
            private_root.cleanup()


def _run_dir_name(task_id: str, harness: str, model: str) -> str:
    safe_model = model.replace("/", "_").replace(":", "_")
    return f"{task_id}__{harness}__{safe_model}__{uuid.uuid4().hex[:8]}"


def _generate_trace_report_safely(
    run_dir: Path,
    result: dict,
    *,
    api_key: str | None,
) -> None:
    try:
        generate_trace_report(run_dir, result=result)
    except Exception as exc:
        safe_message = _redact_runtime_error(str(exc), api_key or "")
        try:
            write_report_error(
                run_dir,
                RuntimeError(f"{type(exc).__name__}: {safe_message}"),
            )
        except OSError:
            pass


_SUBSCRIPTION_AUTH_FILES: dict[str, tuple[str, ...]] = {
    "codex": ("auth.json", "config.toml", "credentials.json"),
    "claudecode": (
        ".credentials.json",
        "credentials.json",
        "settings.json",
        "settings.local.json",
    ),
}
_SUBSCRIPTION_AUTH_ENV: dict[str, tuple[str, ...]] = {
    "codex": (),
    "claudecode": ("CLAUDE_CODE_OAUTH_TOKEN",),
}
_SUBSCRIPTION_AUTH_MAX_FILE_BYTES = 2 * 1024 * 1024


def _collect_subscription_auth(harness: str) -> dict[str, Any]:
    if harness not in _SUBSCRIPTION_AUTH_FILES:
        raise ValueError(
            "Docker subscription auth supports only codex and claudecode"
        )
    env_name = "CODEX_HOME" if harness == "codex" else "CLAUDE_CONFIG_DIR"
    default_dir = Path.home() / (".codex" if harness == "codex" else ".claude")
    source_root = Path(os.environ.get(env_name) or default_dir).expanduser()
    if source_root.is_symlink() or not source_root.is_dir():
        raise ValueError(
            f"{env_name} subscription state directory is missing or symlinked: "
            f"{source_root}"
        )

    files: dict[str, str] = {}
    for name in _SUBSCRIPTION_AUTH_FILES[harness]:
        path = source_root / name
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"subscription auth file is not a regular file: {path}")
        payload = path.read_bytes()
        if len(payload) > _SUBSCRIPTION_AUTH_MAX_FILE_BYTES:
            raise ValueError(f"subscription auth file is too large: {path.name}")
        files[name] = base64.b64encode(payload).decode("ascii")

    environment: dict[str, str] = {}
    for name in _SUBSCRIPTION_AUTH_ENV[harness]:
        value = os.environ.get(name)
        if value:
            environment[name] = value

    if not files and not environment:
        expected = ", ".join(_SUBSCRIPTION_AUTH_FILES[harness])
        raise ValueError(
            f"no usable {harness} subscription auth state found in {source_root}; "
            f"expected one of: {expected}"
        )
    return {
        "schema_version": "harnessbench_subscription_auth_v1",
        "harness": harness,
        "source_env": env_name,
        "file_count": len(files),
        "files": files,
        "env": environment,
    }


def _validate_docker_config(config: BenchmarkRunConfig, harness: str) -> None:
    if harness not in SUPPORTED_CONTAINER_HARNESSES:
        supported = ", ".join(sorted(SUPPORTED_CONTAINER_HARNESSES))
        raise ValueError(f"Docker mode supports {supported}")
    if config.docker_auth == "subscription" and harness not in {"codex", "claudecode"}:
        raise ValueError(
            "Docker subscription auth supports only codex and claudecode"
        )
    if config.harness_path is not None:
        raise ValueError("--harness-path is not supported in Docker mode")
    if config.harness_isolation != "none":
        raise ValueError("--harness-isolation is not used in Docker mode")
    if config.validate_model:
        raise ValueError("--validate-model is replaced by the Docker model registry")
    if any(
        value is not None
        for value in (
            config.provider_source,
            config.provider_id,
            config.provider_name,
            config.ccswitch_home,
        )
    ):
        raise ValueError(
            "provider-source options are not supported in Docker mode; "
            + (
                "use --model-registry"
                if config.docker_auth == "model-registry"
                else "use --docker-auth subscription with native CLI login state"
            )
        )


def _task_runtime_image(task: TaskSpec, harness: str) -> str | None:
    execution = task.metadata.get("execution")
    if not isinstance(execution, dict):
        return None
    image = execution.get("image")
    protocol = execution.get("protocol")
    mode = execution.get("mode")
    if mode != "docker" or not isinstance(image, str) or not image.strip():
        return None
    if protocol == "openclaw_native_v1" and harness != "openclaw":
        raise ValueError(
            f"task {task.task_id} requires an OpenClaw-native Docker adapter; "
            f"harness {harness!r} is ineligible"
        )
    return image.strip()


def _build_task_runtime_image(
    task: TaskSpec,
    image: str,
    build_log: Path,
    *,
    harness: str = "openclaw",
    build_config: ContainerBuildConfig | None = None,
) -> None:
    dockerfile = task.task_dir / "runtime" / "Dockerfile"
    if not dockerfile.is_file():
        raise ValueError(
            f"task runtime image is declared but Dockerfile is missing: {dockerfile}"
        )
    repository_root = Path(__file__).resolve().parents[1]
    dockerfile_text = dockerfile.read_text(encoding="utf-8")
    if not _task_dockerfile_uses_base_image_arg(dockerfile_text):
        raise ValueError(
            "task runtime Dockerfile must declare ARG BASE_IMAGE and use it "
            f"as a FROM image: {dockerfile}"
        )
    context = (
        repository_root
        if "COPY new_task_" in dockerfile_text
        else task.task_dir.parent
    )
    current_base_image = _ensure_current_harness_base_image(
        harness,
        build_log=build_log.parent / "harness_base_image" / "build.log",
        build_config=build_config,
    )
    command = [
        "docker",
        "image",
        "build",
        "--pull=false",
        "--build-arg",
        f"BASE_IMAGE={current_base_image.reference}",
    ]
    command.extend(
        [
            "--tag",
            image,
            "--file",
            str(dockerfile),
            str(context),
        ]
    )
    build_log.parent.mkdir(parents=True, exist_ok=True)
    base_message = (
        f"Using current hash-pinned {harness} base image: "
        f"{current_base_image.reference}\n"
        f"Current base image ID: {current_base_image.image_id}\n"
    )
    build_log.write_text(base_message, encoding="utf-8")
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=1800,
        )
    except subprocess.TimeoutExpired as exc:
        _append_task_image_build_log(
            build_log,
            exc.stdout,
            exc.stderr,
            f"Task Docker image build timed out after {exc.timeout} seconds",
        )
        raise ContainerRuntimeError(
            f"task Docker image build timed out for {image}; see {build_log}"
        ) from exc
    except OSError as exc:
        _append_task_image_build_log(
            build_log,
            f"Task Docker image build could not start: {type(exc).__name__}: {exc}",
        )
        raise ContainerRuntimeError(
            f"task Docker image build could not start for {image}; see {build_log}"
        ) from exc
    _append_task_image_build_log(build_log, completed.stdout, completed.stderr)
    if completed.returncode != 0:
        raise ContainerRuntimeError(
            f"task Docker image build failed for {image}; see {build_log}"
        )


def _append_task_image_build_log(
    build_log: Path,
    *parts: str | bytes | None,
) -> None:
    with build_log.open("a", encoding="utf-8") as stream:
        for part in parts:
            if isinstance(part, bytes):
                text = part.decode("utf-8", errors="replace")
            else:
                text = part or ""
            stream.write(text)
            if text and not text.endswith("\n"):
                stream.write("\n")


def _ensure_current_harness_base_image(
    harness: str,
    *,
    build_log: Path,
    build_config: ContainerBuildConfig | None,
):
    """Resolve the exact base image built from the current worker sources."""

    from harnessbench.container_images import HarnessImageManager

    return HarnessImageManager().ensure(
        harness,
        override=None,
        build_log=build_log,
        build_config=build_config,
    )


def _task_dockerfile_uses_base_image_arg(dockerfile_text: str) -> bool:
    declares_base = re.search(
        r"(?im)^[ \t]*ARG[ \t]+BASE_IMAGE(?:[ \t]*=[^\r\n]*)?[ \t]*$",
        dockerfile_text,
    )
    inherits_base = re.search(
        r"(?im)^[ \t]*FROM(?:[ \t]+--\S+)*[ \t]+"
        r"\$(?:\{BASE_IMAGE\}|BASE_IMAGE)"
        r"(?:[ \t]+AS[ \t]+\S+)?[ \t]*$",
        dockerfile_text,
    )
    return declares_base is not None and inherits_base is not None


def _verified_container_evidence(execution: Any, run_dir: Path) -> dict[str, Any]:
    lifecycle_path = Path(execution.lifecycle_path)
    if lifecycle_path.is_symlink() or not lifecycle_path.is_file():
        raise ContainerRuntimeError("container lifecycle evidence is missing or symlinked")
    try:
        lifecycle_path.resolve().relative_to((run_dir / "artifacts").resolve())
        lifecycle = json.loads(lifecycle_path.read_text(encoding="utf-8"))
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContainerRuntimeError("container lifecycle evidence is invalid") from exc
    if not isinstance(lifecycle, dict):
        raise ContainerRuntimeError("container lifecycle evidence is not an object")
    statuses = (
        execution.archive_status,
        execution.cleanup_status,
        lifecycle.get("archive_status"),
        lifecycle.get("cleanup_status"),
    )
    if statuses != ("complete", "complete", "complete", "complete"):
        raise ContainerRuntimeError(
            "container archive and cleanup must both be proven complete"
        )
    if lifecycle.get("container_id") != execution.container_id:
        raise ContainerRuntimeError("container lifecycle ID does not match execution")
    gateway = getattr(execution, "gateway", None)
    if lifecycle.get("gateway") != gateway:
        raise ContainerRuntimeError("container gateway evidence does not match execution")
    return {
        "container_id": execution.container_id,
        "image_reference": execution.image.reference,
        "image_id": execution.image.image_id,
        "image_digest": execution.image.digest,
        "image_built": execution.image.built,
        "archive_status": "complete",
        "cleanup_status": "complete",
        "gateway": gateway,
        "lifecycle": _relative_artifact(lifecycle_path, run_dir),
    }


def _load_failed_container_evidence(
    artifacts_dir: Path, run_dir: Path
) -> dict[str, Any] | None:
    lifecycle_path = artifacts_dir / "container_lifecycle.json"
    if lifecycle_path.is_symlink() or not lifecycle_path.is_file():
        return None
    try:
        payload = json.loads(lifecycle_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {
            "archive_status": "unknown",
            "cleanup_status": "unknown",
            "lifecycle": _relative_artifact(lifecycle_path, run_dir),
        }
    if not isinstance(payload, dict):
        return None
    return {
        "container_id": payload.get("container_id"),
        "image_reference": (payload.get("image") or {}).get("reference")
        if isinstance(payload.get("image"), dict)
        else None,
        "image_id": (payload.get("image") or {}).get("image_id")
        if isinstance(payload.get("image"), dict)
        else None,
        "archive_status": payload.get("archive_status", "unknown"),
        "cleanup_status": payload.get("cleanup_status", "unknown"),
        "provider_request_salvage": payload.get("provider_request_salvage"),
        "lifecycle": _relative_artifact(lifecycle_path, run_dir),
    }


def _write_container_infrastructure_trace(
    *,
    layout: Any,
    harness: str,
    model: str,
    error_type: str,
    error_message: str,
) -> dict[str, Any]:
    trace_dir = (
        layout.artifacts_dir
        / "trace"
        / f"run-container-error-{uuid.uuid4().hex[:8]}"
    ).resolve()
    trace_dir.mkdir(parents=True, exist_ok=False)
    events_path = trace_dir / "events.json"
    summary_path = trace_dir / "summary.json"
    events_path.write_text(
        json.dumps(
            [
                {
                    "seq": 1,
                    "event_type": "run_failed",
                    "error_type": error_type,
                    "error_message": error_message,
                    "infrastructure_error": True,
                }
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    summary: dict[str, Any] = {
        "status": "infrastructure_error",
        "trace_complete": False,
        "structured_trace_status": "unavailable",
        "exit_code": None,
        "error_type": error_type,
        "error_message": error_message,
        "harness": harness,
        "model": model,
        "events_path": str(events_path),
        "summary_path": str(summary_path),
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def _write_skipped_verifier(
    layout: Any,
    *,
    reason: str = "container archive/cleanup evidence was not valid",
) -> EvaluationResult:
    output_dir = layout.artifacts_dir / "verifier_output"
    output_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = output_dir / "stdout.log"
    stderr_path = output_dir / "stderr.log"
    stdout_path.write_text("", encoding="utf-8")
    stderr_path.write_text(
        f"verifier skipped because {reason}\n",
        encoding="utf-8",
    )
    return EvaluationResult(
        status="error",
        score=None,
        exit_code=None,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        score_path=None,
        error_message="verifier skipped",
    )


def _write_rate_limit_skipped_verifier(
    layout: Any, *, classification: str
) -> EvaluationResult:
    output_dir = layout.artifacts_dir / "verifier_output"
    output_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = output_dir / "stdout.log"
    stderr_path = output_dir / "stderr.log"
    stdout_path.write_text("", encoding="utf-8")
    stderr_path.write_text(
        "verifier and judge skipped because the evaluated model provider "
        f"returned a rate limit (classification={classification})\n",
        encoding="utf-8",
    )
    return EvaluationResult(
        status="skipped",
        score=None,
        exit_code=None,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        score_path=None,
        error_message="provider rate limit",
    )


def _redact_runtime_error(message: str, secret: str) -> str:
    return message.replace(secret, "<redacted>") if secret else message


def _relative_artifact(path: Path, run_dir: Path) -> str:
    try:
        return str(path.resolve().relative_to(run_dir.resolve()))
    except ValueError:
        return str(path)


def _persist_verifier_gate(
    layout: Any, container_evidence: dict[str, Any]
) -> Path:
    lifecycle_value = container_evidence.get("lifecycle")
    if not isinstance(lifecycle_value, str):
        raise ContainerRuntimeError("container lifecycle path is missing")
    lifecycle_path = layout.run_dir / lifecycle_value
    if lifecycle_path.is_symlink() or not lifecycle_path.is_file():
        raise ContainerRuntimeError("container lifecycle path is invalid")
    archive_manifest_path = layout.artifacts_dir / "container" / "archive_manifest.json"
    if archive_manifest_path.is_symlink() or not archive_manifest_path.is_file():
        raise ContainerRuntimeError("container archive manifest is invalid")
    payload = {
        "schema_version": "harnessbench_verifier_gate_v1",
        "verifier_execution": "host-only",
        "container_id": container_evidence.get("container_id"),
        "image_id": container_evidence.get("image_id"),
        "archive_status": container_evidence.get("archive_status"),
        "cleanup_status": container_evidence.get("cleanup_status"),
        "container_lifecycle": lifecycle_value,
        "container_lifecycle_sha256": hashlib.sha256(
            lifecycle_path.read_bytes()
        ).hexdigest(),
        "container_archive_manifest": _relative_artifact(
            archive_manifest_path,
            layout.run_dir,
        ),
        "container_archive_manifest_sha256": hashlib.sha256(
            archive_manifest_path.read_bytes()
        ).hexdigest(),
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    if payload["archive_status"] != "complete" or payload["cleanup_status"] != (
        "complete"
    ):
        raise ContainerRuntimeError("container lifecycle does not open verifier gate")
    path = layout.artifacts_dir / "verifier_gate.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _trace_harness_name(harness: str) -> str:
    if harness == "claudecode":
        return "claudecode"
    return harness


def _harness_cwd(harness: str, layout: Any) -> Any:
    """harness 落脚的工作目录 —— 四家统一用 workspace_dir。

    曾经给 OpenClaw 留了 agent_dir(理由是"那是它放 IDENTITY.md 的约定"), 那是错的:
    IDENTITY.md 在哪跟**交付物该落在哪**是两件事。判据只看 workspace/output/,
    cwd 给 agent_dir 就会让模型把 output/ 写到 agent/output/, 判据一个都读不到,
    AR 层大面积归零。

    实测(M-E3a-deploy, OpenClaw + deepseek, 同题对照):
      cwd=agent_dir(local)     AR 1/5   总分 32.2
      cwd=/workspace(docker)   AR 5/5   总分 88.9
    27 道同题对照平均差 +21.96 分, 23 道 docker 更高, 差的几乎全在 AR 层。
    docker 模式里 OpenClaw 的 cwd 和 OPENCLAW_WORKSPACE_DIR 都是同一个 /workspace,
    根本没有 agent/workspace 两层之分 —— local 模式应该对齐。
    """
    return layout.workspace_dir


def _configure_openclaw_workspace(*, harness, layout, environment) -> None:
    """给每次 OpenClaw 运行写一份私有 config, 把 workspace 指到本次运行目录。

    实测: runner 已经把 OPENCLAW_WORKSPACE_DIR 和 cwd 都设成本次的 agent 目录, 但全局
    config 里的 ``agents.defaults.workspace = ~/.openclaw/workspace`` **压过环境变量** ——
    模型于是在全局工作区里翻找, 题目材料根本不在那儿。18 道 OpenClaw 跑批里 13 道就是
    这么废掉的(几十次工具调用全在瞎找), 分数看起来像"harness 差", 实为装置没接对。
    """
    if harness != "openclaw":
        return
    source_value = (
        environment.get("OPENCLAW_CONFIG_PATH")
        or environment.get("OPENCLAW_CONFIG")
        or os.environ.get("OPENCLAW_CONFIG_PATH")
        or os.environ.get("OPENCLAW_CONFIG")
    )
    source = (
        Path(source_value).expanduser()
        if source_value
        else Path.home() / ".openclaw" / "openclaw.json"
    )
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(payload, dict):
        return
    payload.setdefault("agents", {}).setdefault("defaults", {})["workspace"] = str(
        layout.workspace_dir.resolve()
    )
    path = layout.private_dir / "workspace.openclaw.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    environment["OPENCLAW_CONFIG_PATH"] = str(path)
    environment["OPENCLAW_CONFIG"] = str(path)


def _configure_local_task_tools(
    *,
    harness: str,
    layout: Any,
    environment: dict[str, str],
    extra_args: dict[str, str],
) -> None:
    """Create per-run native-tool configuration without editing user state."""

    bridge = layout.workspace_dir / BRIDGE_RELATIVE_PATH
    server = {
        "command": sys.executable,
        "args": [str(bridge)],
        "env": {
            name: environment[name]
            for name in ("HARNESSBENCH_TASK_TOOLS", "HARNESSBENCH_TASK_TOOL_SOCKET")
        },
    }
    if harness == "codex":
        return
    if harness == "claudecode":
        path = layout.private_dir / "task-tools.claude.mcp.json"
        path.write_text(
            json.dumps(
                {"mcpServers": {"harnessbench": {"type": "stdio", **server}}},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)
        extra_args["harnessbench-task-tool-config"] = str(path)
        return
    if harness == "openclaw":
        source_value = (
            environment.get("OPENCLAW_CONFIG_PATH")
            or environment.get("OPENCLAW_CONFIG")
            or os.environ.get("OPENCLAW_CONFIG_PATH")
            or os.environ.get("OPENCLAW_CONFIG")
        )
        source = (
            Path(source_value).expanduser()
            if source_value
            else Path.home() / ".openclaw" / "openclaw.json"
        )
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("cannot load OpenClaw config for task tools") from exc
        if not isinstance(payload, dict):
            raise ValueError("OpenClaw config for task tools must be an object")
        payload["mcp"] = {
            "servers": {
                "harnessbench": {
                    "enabled": True,
                    **server,
                    "cwd": str(layout.workspace_dir),
                    "connectionTimeoutMs": 10000,
                    "requestTimeoutMs": 300000,
                }
            }
        }
        path = layout.private_dir / "task-tools.openclaw.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)
        environment["OPENCLAW_CONFIG_PATH"] = str(path)
        environment["OPENCLAW_CONFIG"] = str(path)
        return
    if harness == "hermes":
        source_home = Path(
            environment.get("HERMES_HOME")
            or os.environ.get("HERMES_HOME")
            or Path.home() / ".hermes"
        ).expanduser()
        source_config = Path(
            environment.get("HERMES_CONFIG")
            or os.environ.get("HERMES_CONFIG")
            or source_home / "config.yaml"
        ).expanduser()
        try:
            payload = _parse_yaml_subset(source_config.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise ValueError("cannot load Hermes config for task tools") from exc
        payload["mcp_servers"] = {
            "harnessbench": {
                **server,
                "timeout": 300,
                "connect_timeout": 10,
            }
        }
        platform_toolsets = payload.get("platform_toolsets")
        if not isinstance(platform_toolsets, dict):
            platform_toolsets = {}
            payload["platform_toolsets"] = platform_toolsets
        platform_toolsets["cli"] = ["terminal", "file", "delegation", "harnessbench"]
        destination = layout.private_dir / "task-tools-hermes"
        destination.mkdir(mode=0o700)
        config_path = destination / "config.yaml"
        config_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        config_path.chmod(0o600)
        source_env = source_home / ".env"
        if source_env.is_file():
            shutil.copy2(source_env, destination / ".env")
            (destination / ".env").chmod(0o600)
        environment["HERMES_HOME"] = str(destination)
        environment["HERMES_CONFIG"] = str(config_path)
        extra_args["toolsets"] = "terminal,file,delegation,harnessbench"
        return
    raise ValueError(f"task_tools does not support local harness {harness!r}")


def _runtime_env(
    harness: str, provider_env: dict[str, str], agent_dir: Path
) -> dict[str, str]:
    env = dict(provider_env)
    if harness == "openclaw":
        env["OPENCLAW_WORKSPACE_DIR"] = str(agent_dir.resolve())
    return env


def _local_judge_runtime(
    *,
    config: BenchmarkRunConfig,
    model: str,
    provider_env: dict[str, str],
    provider: str,
    timeout: int | None,
) -> JudgeRuntime:
    base_url = _first_nonempty(
        os.environ.get("HARNESSBENCH_JUDGE_BASE_URL"),
        os.environ.get("JUDGE_BASE_URL"),
        provider_env.get("OPENAI_BASE_URL"),
        os.environ.get("OPENAI_BASE_URL"),
    )
    api_key = _first_nonempty(
        os.environ.get("HARNESSBENCH_JUDGE_API_KEY"),
        os.environ.get("JUDGE_API_KEY"),
        provider_env.get("OPENAI_API_KEY"),
        os.environ.get("OPENAI_API_KEY"),
    )
    judge_model = _first_nonempty(
        config.judge_model,
        os.environ.get("HARNESSBENCH_JUDGE_MODEL"),
        os.environ.get("JUDGE_MODEL"),
        model,
    )
    return JudgeRuntime(
        provider=provider,
        model=judge_model,
        base_url=base_url,
        api_key=api_key,
        timeout=float(min(timeout or 180, 300)),
    )


def _first_nonempty(*values: str | None) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _write_resolved_config(
    run_dir: Path,
    config: BenchmarkRunConfig,
    result: dict,
    *,
    effective_timeout: int | None = None,
    effective_extra_args: dict[str, str] | None = None,
    invocation_report: dict | None = None,
    execution_capabilities: dict[str, Any] | None = None,
    security_capabilities: dict[str, Any] | None = None,
) -> None:
    strict = config.mode == "docker" or config.harness_isolation == "macos"
    displayed_requested_args = (
        {
            key: "<discarded>"
            for key in (invocation_report or {}).get("requested_keys", [])
        }
        if strict
        else _redact_sensitive_args(config.extra_args)
    )
    payload = {
        "task": str(config.task.expanduser().resolve(strict=False)),
        "harness": normalize_harness_name(config.harness),
        "model": config.model,
        "mode": config.mode,
        "docker_auth": config.docker_auth if config.mode == "docker" else None,
        "reasoning_intensity": config.reasoning_intensity,
        "harness_path": config.harness_path,
        "timeout": config.timeout,
        "effective_timeout": effective_timeout,
        "docker_image": config.docker_image,
        "resolved_docker_image": result.get("execution", {}).get("docker_image"),
        "model_registry": (
            str(
                (config.model_registry or default_model_registry_path())
                .expanduser()
                .resolve(strict=False)
            )
            if config.mode == "docker"
            and (
                config.docker_auth == "model-registry"
                or config.model_registry is not None
            )
            else None
        ),
        "extra_args": displayed_requested_args,
        "effective_extra_args": _redact_sensitive_args(effective_extra_args or {}),
        "invocation_policy": invocation_report,
        "execution_capabilities": execution_capabilities,
        "security_capabilities": security_capabilities,
        "validate_model": config.validate_model,
        "provider_source": config.provider_source,
        "provider_id": config.provider_id,
        "provider_name": config.provider_name,
        "ccswitch_home": str(config.ccswitch_home) if config.ccswitch_home else None,
        "harness_isolation": config.harness_isolation,
        "instance_seed_sha256": (
            hashlib.sha256(config.instance_seed.encode("utf-8")).hexdigest()
            if config.instance_seed is not None
            else None
        ),
        "judge_harness": config.judge_harness,
        "judge_model": config.judge_model,
        "judge_harness_path": config.judge_harness_path,
        "evaluation_mode": config.evaluation_mode,
        "skip_on_rate_limit": config.skip_on_rate_limit,
        "result_path": str(run_dir / "result.json"),
    }
    (run_dir / "config.resolved.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    )


def _update_resolved_config_for_score(
    run_dir: Path,
    existing: dict[str, Any],
    *,
    model_registry: Path | None,
    judge_model: str | None,
) -> None:
    payload = dict(existing)
    payload["evaluation_mode"] = "score-only"
    if model_registry is not None:
        payload["model_registry"] = str(model_registry)
    if judge_model is not None:
        payload["judge_model"] = judge_model
    path = run_dir / "config.resolved.json"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _redact_sensitive_args(extra_args: dict[str, str]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for key, value in extra_args.items():
        normalized = key.lower().replace("-", "_")
        sensitive = (
            normalized == "key"
            or normalized.endswith("_key")
            or any(
                fragment in normalized
                for fragment in (
                    "api_key",
                    "apikey",
                    "authorization",
                    "credential",
                    "password",
                    "secret",
                    "token",
                )
            )
        )
        redacted[key] = "<redacted>" if sensitive else value
    return redacted
