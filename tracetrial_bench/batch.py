from __future__ import annotations

import json
import os
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Callable, Iterable

from harness_tracer.provider_requests import (
    ProviderRequestEvidenceError,
    read_provider_request_events,
)


SMOKE_TASK = Path("test_task/C1a-academic-L3-001")


@dataclass(frozen=True)
class WorkItem:
    task: Path
    harness: str
    model: str


def resolve_task_inputs(
    *,
    tasks: Iterable[Path],
    smoke: bool,
    task_manifest: Path | None,
    repository_root: Path,
) -> tuple[Path, ...]:
    root = repository_root.resolve()
    if smoke:
        values = [root / SMOKE_TASK]
    elif task_manifest is not None:
        manifest = task_manifest.expanduser().resolve()
        if manifest.is_symlink() or not manifest.is_file():
            raise ValueError(f"task manifest is missing or symlinked: {manifest}")
        values = []
        for line_number, raw_line in enumerate(
            manifest.read_text(encoding="utf-8").splitlines(), start=1
        ):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            candidate = Path(line).expanduser()
            if not candidate.is_absolute():
                candidate = manifest.parent / candidate
            values.append(candidate)
        if not values:
            raise ValueError(f"task manifest contains no task paths: {manifest}")
    else:
        values = []
        for raw_task in tasks:
            candidate = raw_task.expanduser()
            if not candidate.is_absolute():
                candidate = root / candidate
            values.append(candidate)
    resolved = _unique_paths(path.resolve() for path in values)
    if not resolved:
        raise ValueError("at least one task is required")
    return resolved


def expand_work_items(
    *,
    tasks: Iterable[Path],
    harnesses: Iterable[str],
    models: Iterable[str],
) -> tuple[WorkItem, ...]:
    task_values = _unique_paths(tasks)
    harness_values = _unique_strings(harnesses)
    model_values = _unique_strings(models)
    if not task_values or not harness_values or not model_values:
        raise ValueError("tasks, harnesses, and models must all be non-empty")
    return tuple(
        WorkItem(task=task, harness=harness, model=model)
        for task in task_values
        for harness in harness_values
        for model in model_values
    )


def execute_batch(
    *,
    work_items: Iterable[WorkItem],
    concurrency: int,
    output_dir: Path,
    execute: Callable[[WorkItem], dict],
    resume: Path | None = None,
    evaluation_mode: str = "run-and-score",
    skip_on_rate_limit: bool = False,
) -> tuple[dict, Path]:
    if concurrency < 1:
        raise ValueError("concurrency must be a positive integer")
    items = tuple(work_items)
    if not items:
        raise ValueError("batch contains no work items")
    if evaluation_mode not in {"run-only", "run-and-score"}:
        raise ValueError("batch evaluation mode is invalid")
    output_dir = output_dir.expanduser().resolve(strict=False)
    manifest = {
        "schema_version": "harnessbench_batch_v1",
        "concurrency": concurrency,
        "evaluation_mode": evaluation_mode,
        "skip_on_rate_limit": skip_on_rate_limit,
        "work_items": [
            {"task": str(item.task), "harness": item.harness, "model": item.model}
            for item in items
        ],
    }
    records: list[dict | None] = [None] * len(items)
    if resume is None:
        batch_dir = output_dir / "batches" / f"batch-{uuid.uuid4().hex[:12]}"
        batch_dir.mkdir(parents=True, exist_ok=False)
        _write_json(batch_dir / "manifest.json", manifest)
    else:
        batch_dir = resume.expanduser().resolve()
        _validate_resume_manifest(batch_dir, manifest)
        _load_completed_records(batch_dir / "results.jsonl", items, records)
    results_path = batch_dir / "results.jsonl"
    append_lock = Lock()

    def record(index: int, item: WorkItem, result: dict) -> None:
        value = {
            "index": index,
            "task": str(item.task),
            "harness": item.harness,
            "model": item.model,
            "status": result.get("status", "error"),
            "result_path": result.get("result_path"),
            "error_type": result.get("error_type"),
            "error_message": result.get("error_message"),
            "skip_reason": (
                result.get("skip", {}).get("reason")
                if isinstance(result.get("skip"), dict)
                else None
            ),
        }
        records[index] = value
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n"
        with append_lock:
            with results_path.open("a", encoding="utf-8") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())

    if concurrency == 1:
        for index, item in enumerate(items):
            if records[index] is not None:
                continue
            record(index, item, _execute_safely(execute, item))
    else:
        with ThreadPoolExecutor(
            max_workers=concurrency, thread_name_prefix="harnessbench"
        ) as executor:
            futures: dict[Future[dict], tuple[int, WorkItem]] = {
                executor.submit(_execute_safely, execute, item): (index, item)
                for index, item in enumerate(items)
                if records[index] is None
            }
            for future in as_completed(futures):
                index, item = futures[future]
                record(index, item, future.result())

    complete_records = [value for value in records if value is not None]
    summary = {
        "schema_version": "harnessbench_batch_summary_v1",
        "evaluation_mode": evaluation_mode,
        "counts": summarize_statuses(
            value["status"] for value in complete_records
        ),
        "results": complete_records,
    }
    summary_path = batch_dir / "summary.json"
    _write_json(summary_path, summary)
    return summary, summary_path


def summarize_statuses(statuses: Iterable[str]) -> dict[str, int]:
    values = tuple(statuses)
    passed = values.count("pass")
    failed = values.count("fail")
    run_complete = values.count("run_complete")
    skipped = values.count("skipped")
    errors = len(values) - passed - failed - run_complete - skipped
    return {
        "pass": passed,
        "fail": failed,
        "error": errors,
        "run_complete": run_complete,
        "skipped": skipped,
        "non_pass": failed + errors,
        "total": len(values),
    }


def _execute_safely(execute: Callable[[WorkItem], dict], item: WorkItem) -> dict:
    try:
        return execute(item)
    except Exception as exc:
        return {
            "status": "error",
            "result_path": None,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }


def _unique_paths(values: Iterable[Path]) -> tuple[Path, ...]:
    seen: set[str] = set()
    unique: list[Path] = []
    for value in values:
        key = os.fspath(value)
        if key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return tuple(unique)


def _unique_strings(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    unique: list[str] = []
    for raw in values:
        value = raw.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return tuple(unique)


def _write_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _validate_resume_manifest(batch_dir: Path, expected: dict) -> None:
    manifest_path = batch_dir / "manifest.json"
    if batch_dir.is_symlink() or not batch_dir.is_dir() or not manifest_path.is_file():
        raise ValueError(f"resume batch is missing or invalid: {batch_dir}")
    try:
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"resume batch manifest is invalid: {manifest_path}") from exc
    if not isinstance(existing, dict) or existing.get("work_items") != expected["work_items"]:
        raise ValueError("resume batch work-item matrix does not match this invocation")
    existing_mode = existing.get("evaluation_mode", "run-and-score")
    if existing_mode != expected.get("evaluation_mode", "run-and-score"):
        raise ValueError("resume batch evaluation mode does not match this invocation")
    existing_skip_policy = existing.get("skip_on_rate_limit", False)
    if existing_skip_policy != expected.get("skip_on_rate_limit", False):
        raise ValueError("resume batch rate-limit policy does not match this invocation")


def _load_completed_records(
    results_path: Path,
    items: tuple[WorkItem, ...],
    records: list[dict | None],
) -> None:
    if not results_path.exists():
        return
    try:
        lines = results_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read resume results: {results_path}") from exc
    candidates: dict[int, dict] = {}
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict) or not isinstance(value.get("index"), int):
            continue
        index = value["index"]
        if 0 <= index < len(items):
            candidates[index] = value
    for index, value in candidates.items():
        item = items[index]
        if (
            value.get("task") != str(item.task)
            or value.get("harness") != item.harness
            or value.get("model") != item.model
            or not _result_is_complete(value.get("result_path"))
        ):
            continue
        records[index] = value


def _result_is_complete(raw_path: object) -> bool:
    if not isinstance(raw_path, str) or not raw_path:
        return False
    path = Path(raw_path)
    if path.is_symlink() or not path.is_file():
        return False
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(result, dict) or not isinstance(result.get("status"), str):
        return False
    execution = result.get("execution")
    if not isinstance(execution, dict) or execution.get("mode") != "docker":
        return True
    container = execution.get("container")
    if (
        not isinstance(container, dict)
        or container.get("cleanup_status") != "complete"
    ):
        return False
    if container.get("archive_status") == "complete":
        return True
    return _salvaged_rate_limit_skip_is_complete(result, path.parent)


def _salvaged_rate_limit_skip_is_complete(
    result: dict, run_dir: Path
) -> bool:
    skip = result.get("skip")
    artifacts = result.get("artifacts")
    evidence = (
        artifacts.get("provider_request_evidence")
        if isinstance(artifacts, dict)
        else None
    )
    if not (
        result.get("status") == "skipped"
        and isinstance(skip, dict)
        and skip.get("reason") == "provider_rate_limit"
        and isinstance(evidence, dict)
        and evidence.get("status") == "salvaged_after_runtime_error"
    ):
        return False
    raw_events = evidence.get("events")
    if not isinstance(raw_events, str) or not raw_events:
        return False
    events_path = Path(raw_events)
    if not events_path.is_absolute():
        events_path = run_dir / events_path
    if events_path.is_symlink():
        return False
    try:
        resolved = events_path.resolve(strict=True)
        resolved.relative_to(run_dir.resolve(strict=True))
        if not resolved.is_file():
            return False
        rows = read_provider_request_events(resolved)
    except (
        FileNotFoundError,
        OSError,
        ValueError,
        ProviderRequestEvidenceError,
    ):
        return False
    request_seqs = [
        int(row["seq"])
        for row in rows
        if row.get("upstream_status") == 429
    ]
    return bool(
        request_seqs
        and request_seqs == skip.get("request_seqs")
        and len(request_seqs) == skip.get("observed_request_count")
    )
