#!/usr/bin/env python3
"""Recover pending result metadata after run-and-score crashed post-execution."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tracetrial_bench.cache_metrics import attach_cache_savings
from tracetrial_bench.container_runtime import _validate_exported_provider_requests
from tracetrial_bench.execution_capabilities import (
    adapter_extra_args as execution_adapter_extra_args,
)
from tracetrial_bench.execution_capabilities import capability_report
from tracetrial_bench.model_registry import ModelRegistry
from tracetrial_bench.models import bind_model, normalize_harness_name
from tracetrial_bench.provider_request_recorder import (
    validate_provider_request_summary,
)
from tracetrial_bench.provider_usage import attach_provider_token_usage
from tracetrial_bench.result import _write_json_atomic, pending_evaluation, write_result
from tracetrial_bench.runner import (
    BenchmarkRunConfig,
    _validate_container_archive_manifest,
    _validate_trace_paths,
    _validate_verifier_gate,
    _write_resolved_config,
)
from tracetrial_bench.runner_policy import resolve_harness_invocation
from tracetrial_bench.security_capabilities import (
    adapter_extra_args as security_adapter_extra_args,
)
from tracetrial_bench.security_capabilities import capability_report as security_report
from tracetrial_bench.step_metrics import attach_step_count
from tracetrial_bench.task_resolver import resolve_task
from tracetrial_bench.timing_metrics import attach_timing_metrics
from tracetrial_bench.token_cost import attach_token_cost
from tracetrial_bench.types import HarnessRunSpec
from tracetrial_bench.workspace import _directory_manifest, _task_private_manifest


RECOVERY_SCHEMA = "harnessbench_interrupted_scoring_recovery_v1"
RECOVERED_SCORING_MANIFEST = "manifest.recovered.json"


def _load_object(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is missing or symlinked: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is invalid: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(path: Path, root: Path) -> str:
    return str(path.resolve(strict=True).relative_to(root.resolve(strict=True)))


def _aggregate_trace(run_dir: Path) -> tuple[dict[str, Any], Path, Path]:
    archive_root = run_dir / "artifacts" / "container"
    trace_root = archive_root / "trace"
    candidates = [
        path
        for path in trace_root.glob("**/summary.json")
        if "turns" not in path.relative_to(trace_root).parts
    ]
    if len(candidates) != 1:
        raise ValueError(
            f"expected one aggregate trace summary in {run_dir}, got {len(candidates)}"
        )
    summary_path = candidates[0].resolve(strict=True)
    trace = _load_object(summary_path, "trace summary")
    if trace.get("status") != "finished":
        raise ValueError(f"execution did not finish successfully: {run_dir}")
    raw_events = trace.get("events_path")
    if not isinstance(raw_events, str) or not raw_events:
        raise ValueError(f"trace events path is missing: {run_dir}")
    events_path = Path(raw_events)
    if not events_path.is_absolute():
        events_path = summary_path.parent / events_path
    events_path = events_path.resolve(strict=True)
    return trace, summary_path, events_path


def _container_evidence(lifecycle: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    image = lifecycle.get("image")
    if not isinstance(image, dict):
        raise ValueError(f"container image metadata is invalid: {run_dir}")
    if (
        lifecycle.get("archive_status") != "complete"
        or lifecycle.get("cleanup_status") != "complete"
    ):
        raise ValueError(f"container archive or cleanup is incomplete: {run_dir}")
    return {
        "container_id": lifecycle.get("container_id"),
        "image_reference": image.get("reference"),
        "image_id": image.get("image_id"),
        "image_digest": image.get("digest"),
        "image_built": image.get("built"),
        "archive_status": "complete",
        "cleanup_status": "complete",
        "gateway": lifecycle.get("gateway"),
        "lifecycle": "artifacts/container_lifecycle.json",
    }


def _task_private_changes(
    task: Any,
    manifest_path: Path,
) -> list[dict[str, Any]]:
    manifest = _load_object(manifest_path, "scoring inputs manifest")
    if (
        manifest.get("schema_version") != "harnessbench_scoring_inputs_v1"
        or manifest.get("baseline_workspace") != "baseline_workspace"
        or not isinstance(manifest.get("files"), list)
        or not isinstance(manifest.get("task_private_files"), list)
    ):
        raise ValueError("scoring inputs manifest has an invalid schema")
    baseline = manifest_path.parent / "baseline_workspace"
    if _directory_manifest(baseline) != manifest["files"]:
        raise ValueError("persisted baseline does not match its manifest")

    before = {
        row["path"]: row
        for row in manifest["task_private_files"]
        if isinstance(row, dict) and isinstance(row.get("path"), str)
    }
    after_rows = _task_private_manifest(task)
    after = {row["path"]: row for row in after_rows}
    if len(before) != len(manifest["task_private_files"]):
        raise ValueError("original task-private manifest contains invalid rows")
    return [
        {
            "path": path,
            "before": before.get(path),
            "after": after.get(path),
        }
        for path in sorted(set(before) | set(after))
        if before.get(path) != after.get(path)
    ]


def _recovered_scoring_manifest(
    *,
    task: Any,
    original_manifest_path: Path,
    changes: list[dict[str, Any]],
    run_dir: Path,
) -> tuple[Path, dict[str, Any], dict[str, str]]:
    """Build a score-only manifest without rewriting the execution-time evidence."""

    if not changes:
        return original_manifest_path, {}, {}
    changed_paths = {str(change.get("path")) for change in changes}
    if any(
        not path.startswith("verifier/") or path in {"verifier/", "verifier"}
        for path in changed_paths
    ):
        raise ValueError(
            "interrupted-scoring recovery may only declare verifier file changes"
        )

    original = _load_object(original_manifest_path, "scoring inputs manifest")
    recovered = dict(original)
    recovered["task_private_files"] = _task_private_manifest(task)
    recovered["recovery"] = {
        "schema_version": RECOVERY_SCHEMA,
        "purpose": "score_only_after_declared_verifier_repair",
        "original_scoring_inputs_manifest": _relative(
            original_manifest_path,
            run_dir,
        ),
        "original_scoring_inputs_manifest_sha256": _sha256(
            original_manifest_path
        ),
        "task_private_changes": changes,
    }
    recovered_path = original_manifest_path.with_name(RECOVERED_SCORING_MANIFEST)
    metadata = {
        "effective_scoring_inputs_manifest": str(
            recovered_path.relative_to(run_dir)
        ),
        "scoring_task_private_source": (
            "current_task_with_declared_verifier_repair"
        ),
    }
    return recovered_path, recovered, metadata


def _finalize_existing_recovery(run_dir: Path) -> dict[str, Any]:
    """Make already-recovered metadata scoreable after a declared verifier fix."""

    run_dir = run_dir.resolve(strict=True)
    result_path = run_dir / "result.json"
    config_path = run_dir / "config.resolved.json"
    result = _load_object(result_path, "recovered result")
    resolved_config = _load_object(config_path, "recovered resolved config")
    recovery = result.get("recovery")
    if (
        not isinstance(recovery, dict)
        or recovery.get("schema_version") != RECOVERY_SCHEMA
        or resolved_config.get("recovery") != recovery
    ):
        raise ValueError(f"recovery metadata is missing or inconsistent: {run_dir}")

    task_value = resolved_config.get("task")
    manifest_value = recovery.get("scoring_inputs_manifest")
    if not isinstance(task_value, str) or not isinstance(manifest_value, str):
        raise ValueError(f"recovery source metadata is incomplete: {run_dir}")
    task = resolve_task(Path(task_value))
    original_manifest_path = (run_dir / manifest_value).resolve(strict=True)
    original_manifest_path.relative_to((run_dir / "artifacts").resolve(strict=True))
    if recovery.get("scoring_inputs_manifest_sha256") != _sha256(
        original_manifest_path
    ):
        raise ValueError(f"recovery source manifest digest mismatch: {run_dir}")

    changes = _task_private_changes(task, original_manifest_path)
    declared_changes = recovery.get("task_private_changes")
    if not isinstance(declared_changes, list):
        raise ValueError(f"declared task-private changes are invalid: {run_dir}")
    declared_by_path = {
        str(change.get("path")): change
        for change in declared_changes
        if isinstance(change, dict) and isinstance(change.get("path"), str)
    }
    current_by_path = {str(change["path"]): change for change in changes}
    if (
        len(declared_by_path) != len(declared_changes)
        or set(declared_by_path) != set(current_by_path)
        or any(
            declared_by_path[path].get("before")
            != current_by_path[path].get("before")
            for path in current_by_path
        )
    ):
        raise ValueError(f"declared task-private change scope no longer matches: {run_dir}")
    effective_path, effective_payload, recovery_updates = _recovered_scoring_manifest(
        task=task,
        original_manifest_path=original_manifest_path,
        changes=changes,
        run_dir=run_dir,
    )
    if not changes:
        return {
            "run_dir": str(run_dir),
            "status": "already_scoreable",
            "task_private_changes": [],
        }

    if effective_path.exists():
        existing_effective = _load_object(
            effective_path,
            "recovered scoring inputs manifest",
        )
        if existing_effective != effective_payload:
            _write_json_atomic(effective_path, effective_payload)
    else:
        _write_json_atomic(effective_path, effective_payload)

    recovery = {
        **recovery,
        "task_private_changes": changes,
        **recovery_updates,
    }
    recovery["effective_scoring_inputs_manifest_sha256"] = _sha256(effective_path)
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError(f"recovered result artifacts are invalid: {run_dir}")
    current_manifest = artifacts.get("scoring_inputs_manifest")
    allowed_manifest_values = {
        manifest_value,
        recovery_updates["effective_scoring_inputs_manifest"],
    }
    if current_manifest not in allowed_manifest_values:
        raise ValueError(f"recovered result points at an unexpected manifest: {run_dir}")

    result = dict(result)
    result["artifacts"] = dict(artifacts)
    result["artifacts"]["scoring_inputs_manifest"] = recovery_updates[
        "effective_scoring_inputs_manifest"
    ]
    result["recovery"] = recovery
    resolved_config = dict(resolved_config)
    resolved_config["recovery"] = recovery
    _write_json_atomic(result_path, result)
    _write_json_atomic(config_path, resolved_config)
    return {
        "run_dir": str(run_dir),
        "status": "scoreable_with_declared_verifier_repair",
        "task_private_changes": sorted(
            str(change["path"]) for change in changes
        ),
        "effective_scoring_inputs_manifest": str(effective_path),
    }


def _prepare_existing_verifier_repair(run_dir: Path) -> dict[str, Any]:
    """Declare and install a verifier-only manifest for a completed old run."""

    run_dir = run_dir.resolve(strict=True)
    result_path = run_dir / "result.json"
    config_path = run_dir / "config.resolved.json"
    result = _load_object(result_path, "existing result")
    resolved_config = _load_object(config_path, "existing resolved config")
    execution = result.get("execution")
    if (
        not isinstance(execution, dict)
        or execution.get("status") != "finished"
        or execution.get("trace_complete") is not True
    ):
        raise ValueError(
            f"verifier repair requires a complete successful execution: {run_dir}"
        )
    if result.get("recovery") is not None or resolved_config.get("recovery") is not None:
        raise ValueError(f"recovery metadata already exists: {run_dir}")

    task_value = resolved_config.get("task")
    artifacts = result.get("artifacts")
    if not isinstance(task_value, str) or not isinstance(artifacts, dict):
        raise ValueError(f"existing run metadata is incomplete: {run_dir}")
    manifest_value = artifacts.get("scoring_inputs_manifest")
    if not isinstance(manifest_value, str):
        raise ValueError(f"scoring inputs manifest is missing: {run_dir}")
    original_manifest_path = (run_dir / manifest_value).resolve(strict=True)
    original_manifest_path.relative_to((run_dir / "artifacts").resolve(strict=True))

    task = resolve_task(Path(task_value))
    changes = _task_private_changes(task, original_manifest_path)
    effective_path, effective_payload, recovery_updates = _recovered_scoring_manifest(
        task=task,
        original_manifest_path=original_manifest_path,
        changes=changes,
        run_dir=run_dir,
    )
    if not changes:
        return {
            "run_dir": str(run_dir),
            "status": "already_scoreable",
            "task_private_changes": [],
        }
    if effective_path.exists():
        existing = _load_object(effective_path, "recovered scoring inputs manifest")
        if existing != effective_payload:
            raise ValueError(
                f"refusing to overwrite a different recovered manifest: {effective_path}"
            )
    else:
        _write_json_atomic(effective_path, effective_payload)

    recovery = {
        "schema_version": RECOVERY_SCHEMA,
        "reason": "score_only_after_declared_verifier_repair",
        "execution_reused": True,
        "model_reexecuted": False,
        "scoring_inputs_manifest": manifest_value,
        "scoring_inputs_manifest_sha256": _sha256(original_manifest_path),
        "task_private_changes": changes,
        **recovery_updates,
        "effective_scoring_inputs_manifest_sha256": _sha256(effective_path),
    }
    updated_result = dict(result)
    updated_result["artifacts"] = dict(artifacts)
    updated_result["artifacts"]["scoring_inputs_manifest"] = recovery_updates[
        "effective_scoring_inputs_manifest"
    ]
    updated_result["recovery"] = recovery
    updated_config = dict(resolved_config)
    updated_config["recovery"] = recovery
    _write_json_atomic(result_path, updated_result)
    _write_json_atomic(config_path, updated_config)
    return {
        "run_dir": str(run_dir),
        "status": "scoreable_with_declared_verifier_repair",
        "task_private_changes": sorted(str(change["path"]) for change in changes),
        "effective_scoring_inputs_manifest": str(effective_path),
    }


def _batch_record(
    *,
    batch_manifest: dict[str, Any],
    results_path: Path,
    task_dir: Path,
    harness: str,
    model: str,
) -> dict[str, Any]:
    work_items = batch_manifest.get("work_items")
    if not isinstance(work_items, list):
        raise ValueError("batch manifest work_items are invalid")
    expected = {
        "task": str(task_dir.resolve(strict=True)),
        "harness": harness,
        "model": model,
    }
    if sum(item == expected for item in work_items) != 1:
        raise ValueError(f"run identity is not unique in batch manifest: {expected}")

    records: list[dict[str, Any]] = []
    for line in results_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if (
            isinstance(value, dict)
            and value.get("task") == expected["task"]
            and normalize_harness_name(str(value.get("harness"))) == harness
            and value.get("model") == model
        ):
            records.append(value)
    if len(records) != 1 or records[0].get("status") != "error":
        raise ValueError(f"expected one failed batch record for {expected}")
    record = records[0]
    return {
        key: record.get(key)
        for key in ("index", "status", "error_type", "error_message")
    }


def _task_dir_from_batch(
    *,
    batch_manifest: dict[str, Any],
    repository: Path,
    task_id: str,
    harness: str,
    model: str,
) -> Path:
    """Resolve the original task path instead of assuming tasks/new_cases."""

    work_items = batch_manifest.get("work_items")
    if not isinstance(work_items, list):
        raise ValueError("batch manifest work_items are invalid")
    matches: list[Path] = []
    for item in work_items:
        if not isinstance(item, dict):
            continue
        raw_task = item.get("task")
        if not isinstance(raw_task, str):
            continue
        candidate = Path(raw_task)
        if not candidate.is_absolute():
            candidate = repository / candidate
        if (
            candidate.name == task_id
            and normalize_harness_name(str(item.get("harness"))) == harness
            and item.get("model") == model
        ):
            matches.append(candidate.resolve(strict=True))
    if len(matches) != 1:
        raise ValueError(
            "run identity does not resolve to exactly one task in batch manifest: "
            f"task_id={task_id!r}, harness={harness!r}, model={model!r}"
        )
    task_dir = matches[0]
    task_dir.relative_to((repository / "tasks").resolve(strict=True))
    return task_dir


def _validate_model_identity(runtime: Any, lifecycle: dict[str, Any]) -> None:
    observed = lifecycle.get("model")
    if not isinstance(observed, dict):
        raise ValueError("container lifecycle model metadata is invalid")
    expected = runtime.public_summary()
    identity_keys = (
        "alias",
        "provider",
        "model",
        "base_url",
        "api_mode",
        "harness",
        "harness_model",
        "harness_provider",
    )
    if any(observed.get(key) != expected.get(key) for key in identity_keys):
        raise ValueError("current registry model identity differs from the run")


def recover_run(
    *,
    run_dir: Path,
    repository: Path,
    model_registry: Path,
    model_alias: str,
    reasoning_intensity: str | None,
    judge_model: str,
    batch_manifest_path: Path,
    allowed_task_private_changes: set[str],
    write: bool,
) -> dict[str, Any]:
    run_dir = run_dir.resolve(strict=True)
    result_path = run_dir / "result.json"
    config_path = run_dir / "config.resolved.json"
    if write and (result_path.exists() or config_path.exists()):
        raise ValueError(f"refusing to overwrite recovered metadata: {run_dir}")

    lifecycle_path = run_dir / "artifacts" / "container_lifecycle.json"
    lifecycle = _load_object(lifecycle_path, "container lifecycle")
    task_id = lifecycle.get("task_id")
    harness = normalize_harness_name(str(lifecycle.get("harness")))
    if not isinstance(task_id, str) or not task_id:
        raise ValueError(f"container task identity is invalid: {run_dir}")
    batch_manifest = _load_object(batch_manifest_path, "batch manifest")
    task_dir = _task_dir_from_batch(
        batch_manifest=batch_manifest,
        repository=repository,
        task_id=task_id,
        harness=harness,
        model=model_alias,
    )
    task = resolve_task(task_dir)
    if not run_dir.name.startswith(f"{task_id}__{harness}__"):
        raise ValueError(f"run directory name does not match lifecycle: {run_dir}")

    registry = ModelRegistry.load(model_registry)
    runtime = registry.resolve(model_alias, harness)
    _validate_model_identity(runtime, lifecycle)
    binding = replace(
        bind_model(harness=harness, model=runtime.harness_model, validate=False),
        requested=model_alias,
        provider=runtime.provider,
        canonical=runtime.model,
        harness_arg=runtime.harness_model,
        binding_mode="model_registry",
        validation_status="registry_resolved",
    )

    results_path = batch_manifest_path.parent / "results.jsonl"
    batch_record = _batch_record(
        batch_manifest=batch_manifest,
        results_path=results_path,
        task_dir=task_dir,
        harness=harness,
        model=model_alias,
    )

    archive_manifest_path = run_dir / "artifacts" / "container" / "archive_manifest.json"
    archive_files = _validate_container_archive_manifest(archive_manifest_path)
    trace, summary_path, events_path = _aggregate_trace(run_dir)
    archive_root = archive_manifest_path.parent.resolve(strict=True)
    for path in (summary_path, events_path):
        if _relative(path, archive_root) not in archive_files:
            raise ValueError(f"trace artifact is not covered by archive manifest: {path}")
    _validate_trace_paths(
        trace,
        run_dir=run_dir,
        archive_root=archive_root,
        trace_summary_path=summary_path,
        trace_events_path=events_path,
    )

    container = _container_evidence(lifecycle, run_dir)
    _validate_verifier_gate(
        run_dir / "artifacts" / "verifier_gate.json",
        lifecycle_path=lifecycle_path,
        archive_manifest_path=archive_manifest_path,
        container=container,
    )

    provider_summary = lifecycle.get("provider_request_evidence")
    if not isinstance(provider_summary, dict):
        raise ValueError("provider request evidence metadata is missing")
    provider_summary = validate_provider_request_summary(provider_summary)
    provider_evidence = _validate_exported_provider_requests(
        archive_root,
        provider_summary,
    )
    trace["container"] = container
    trace["gateway"] = lifecycle.get("gateway")
    trace["provider_request_evidence"] = provider_evidence
    attach_provider_token_usage(trace)
    attach_cache_savings(trace, runtime.pricing)

    scoring_manifest = (
        run_dir / "artifacts" / "private_evidence" / "scoring_inputs" / "manifest.json"
    )
    changes = _task_private_changes(task, scoring_manifest)
    changed_paths = {str(change["path"]) for change in changes}
    unexpected_changes = changed_paths - allowed_task_private_changes
    if unexpected_changes:
        raise ValueError(
            "task-private changes include paths outside the explicitly allowed set: "
            f"unexpected={sorted(unexpected_changes)}, "
            f"allowed={sorted(allowed_task_private_changes)}"
        )
    effective_scoring_manifest, effective_manifest_payload, recovery_updates = (
        _recovered_scoring_manifest(
            task=task,
            original_manifest_path=scoring_manifest,
            changes=changes,
            run_dir=run_dir,
        )
    )
    trace["scoring_inputs_manifest"] = str(effective_scoring_manifest)
    attach_step_count(trace)
    attach_timing_metrics(trace)
    attach_token_cost(trace, binding.canonical)

    invocation = resolve_harness_invocation(
        harness=harness,
        requested_timeout=None,
        task_max_wall_time_sec=task.max_wall_time_sec,
        task_max_turns=task.max_turns,
        requested_extra_args={},
        requested_reasoning_intensity=reasoning_intensity,
        strict=True,
    )
    effective_extra_args = {
        **invocation.extra_args,
        **execution_adapter_extra_args(task, harness),
        **security_adapter_extra_args(task),
    }
    run_spec = HarnessRunSpec(
        harness=harness,
        model=model_alias,
        mode="docker",
        timeout=invocation.timeout,
        docker_image=str(container["image_id"]),
        extra_args=effective_extra_args,
        provider_source="model_registry",
        provider_id=runtime.provider,
        provider_name=runtime.provider,
    )
    config = BenchmarkRunConfig(
        task=task_dir,
        harness=harness,
        model=model_alias,
        output_dir=run_dir.parent,
        mode="docker",
        docker_auth="model-registry",
        reasoning_intensity=reasoning_intensity,
        model_registry=model_registry,
        judge_model=judge_model,
        evaluation_mode="run-and-score",
        skip_on_rate_limit=True,
    )
    recovery = {
        "schema_version": RECOVERY_SCHEMA,
        "reason": "scoring_exception_before_result_commit",
        "recovered_at": datetime.now(timezone.utc).isoformat(),
        "execution_reused": True,
        "model_reexecuted": False,
        "original_evaluation_mode": "run-and-score",
        "batch_manifest": _relative(batch_manifest_path, repository),
        "batch_manifest_sha256": _sha256(batch_manifest_path),
        "batch_record": batch_record,
        "scoring_inputs_manifest": _relative(scoring_manifest, run_dir),
        "scoring_inputs_manifest_sha256": _sha256(scoring_manifest),
        "task_private_changes": changes,
        "interrupted_scoring_artifacts": {
            "verifier_stdout": "artifacts/verifier_output/stdout.log",
            "verifier_stderr": "artifacts/verifier_output/stderr.log",
            "verifier_score": "artifacts/verifier_output/score.json",
        },
        **recovery_updates,
    }

    if not write:
        return {
            "run_dir": str(run_dir),
            "task_id": task_id,
            "harness": harness,
            "trace_complete": trace.get("trace_complete"),
            "task_private_changes": sorted(changed_paths),
            "step_count": trace.get("step_count"),
            "status": "validated",
        }

    created: list[Path] = []
    try:
        if effective_manifest_payload:
            if effective_scoring_manifest.exists():
                raise ValueError(
                    "refusing to overwrite recovered scoring inputs manifest: "
                    f"{effective_scoring_manifest}"
                )
            _write_json_atomic(effective_scoring_manifest, effective_manifest_payload)
            created.append(effective_scoring_manifest)
            recovery["effective_scoring_inputs_manifest_sha256"] = _sha256(
                effective_scoring_manifest
            )
        result = write_result(
            task=task,
            run_spec=run_spec,
            model_binding=binding,
            run_dir=run_dir,
            trace_summary=trace,
            evaluation=pending_evaluation(run_dir),
            workspace_diff=run_dir / "artifacts" / "workspace.diff",
            evaluation_mode="run-and-score",
        )
        created.append(result_path)
        result["recovery"] = recovery
        result["artifacts"]["verifier_stdout"] = None
        result["artifacts"]["verifier_stderr"] = None
        result["artifacts"]["verifier_score"] = None
        _write_json_atomic(result_path, result)

        _write_resolved_config(
            run_dir,
            config,
            result,
            effective_timeout=invocation.timeout,
            effective_extra_args=effective_extra_args,
            invocation_report=invocation.report,
            execution_capabilities=capability_report(task, harness),
            security_capabilities=security_report(task, harness),
        )
        created.append(config_path)
        resolved_config = _load_object(config_path, "recovered resolved config")
        resolved_config["recovery"] = recovery
        _write_json_atomic(config_path, resolved_config)
    except BaseException:
        for path in reversed(created):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise

    return {
        "run_dir": str(run_dir),
        "task_id": task_id,
        "harness": harness,
        "trace_complete": trace.get("trace_complete"),
        "task_private_changes": sorted(changed_paths),
        "step_count": trace.get("step_count"),
        "status": "written",
        "result_path": str(result_path),
        "config_path": str(config_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", action="append", type=Path, required=True)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--model-registry", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--reasoning-intensity")
    parser.add_argument("--judge-model")
    parser.add_argument("--batch-manifest", type=Path)
    parser.add_argument("--allow-task-private-change", action="append", default=[])
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--finalize-existing", action="store_true")
    parser.add_argument("--prepare-verifier-repair", action="store_true")
    args = parser.parse_args()

    modes = sum((args.write, args.finalize_existing, args.prepare_verifier_repair))
    if modes > 1:
        parser.error(
            "--write, --finalize-existing, and --prepare-verifier-repair are "
            "mutually exclusive"
        )

    if args.prepare_verifier_repair:
        results = [_prepare_existing_verifier_repair(run_dir) for run_dir in args.run_dir]
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0

    if args.finalize_existing:
        results = [_finalize_existing_recovery(run_dir) for run_dir in args.run_dir]
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0

    required = {
        "--model-registry": args.model_registry,
        "--model": args.model,
        "--judge-model": args.judge_model,
        "--batch-manifest": args.batch_manifest,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        parser.error(f"the following arguments are required: {', '.join(missing)}")

    repository = args.repository.resolve(strict=True)
    model_registry = args.model_registry.resolve(strict=True)
    batch_manifest = args.batch_manifest.resolve(strict=True)
    allowed = set(args.allow_task_private_change)
    results = [
        recover_run(
            run_dir=run_dir,
            repository=repository,
            model_registry=model_registry,
            model_alias=args.model,
            reasoning_intensity=args.reasoning_intensity,
            judge_model=args.judge_model,
            batch_manifest_path=batch_manifest,
            allowed_task_private_changes=allowed,
            write=args.write,
        )
        for run_dir in args.run_dir
    ]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
