from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tracetrial_bench.types import EvaluationResult, HarnessRunSpec, ModelBinding, TaskSpec


def pending_evaluation(run_dir: Path) -> EvaluationResult:
    output_dir = run_dir / "artifacts" / "verifier_output"
    return EvaluationResult(
        status="pending",
        score=None,
        exit_code=None,
        stdout_path=output_dir / "stdout.log",
        stderr_path=output_dir / "stderr.log",
        score_path=None,
    )


def write_result(
    *,
    task: TaskSpec,
    run_spec: HarnessRunSpec,
    model_binding: ModelBinding,
    run_dir: Path,
    trace_summary: dict[str, Any],
    evaluation: EvaluationResult,
    workspace_diff: Path,
    raw_evaluation: EvaluationResult | None = None,
    runner_policy: dict[str, Any] | None = None,
    runner_policy_path: Path | None = None,
    evaluation_mode: str = "run-and-score",
) -> dict[str, Any]:
    raw_evaluation = raw_evaluation or evaluation
    runner_policy = runner_policy or {}
    trace_summary_path = Path(
        trace_summary.get("summary_path", run_dir / "trace" / "summary.json")
    )
    trace_events_path = Path(
        trace_summary.get("events_path", run_dir / "trace" / "events.json")
    )
    execution_status = trace_summary.get("status")
    skip = trace_summary.get("skip")
    overall_status = (
        "skipped"
        if isinstance(skip, dict) and skip.get("status") == "skipped"
        else _overall_status(execution_status, evaluation.status)
    )
    protocol_evidence = trace_summary.get("protocol_evidence_path")
    simulator_evidence = trace_summary.get("business_simulator_evidence")
    execution_probe_evidence = trace_summary.get("execution_probe_evidence")
    model_call_budget_evidence = trace_summary.get("model_call_budget_evidence")
    provider_request_evidence = trace_summary.get("provider_request_evidence")
    security_probe_evidence = trace_summary.get("security_probe_evidence")
    scoring_inputs_manifest = trace_summary.get("scoring_inputs_manifest")
    result = {
        "schema_version": "0.1",
        "status": overall_status,
        "evaluation_mode": evaluation_mode,
        "result_path": str(run_dir / "result.json"),
        "task_id": task.task_id,
        "task_dir": str(task.task_dir),
        "input_query": task.prompt,
        "skip": copy.deepcopy(skip) if isinstance(skip, dict) else None,
        "harness": {
            "name": run_spec.harness,
            "adapter_version": "0.1",
            "binary_version": None,
        },
        "provider": {
            "source": run_spec.provider_source,
            "id": run_spec.provider_id,
            "name": run_spec.provider_name,
        },
        "model": {
            "requested": model_binding.requested,
            "provider": model_binding.provider,
            "canonical": model_binding.canonical,
            "harness_arg": model_binding.harness_arg,
            "binding_status": model_binding.binding_status,
            "binding_mode": model_binding.binding_mode,
            "flag": model_binding.flag,
            "validation_status": model_binding.validation_status,
            "validation_message": model_binding.validation_message,
            "observed": model_binding.observed,
        },
        "metrics": {
            "step_count": copy.deepcopy(trace_summary.get("step_count")),
            "step_count_details": copy.deepcopy(
                trace_summary.get("step_count_details")
            ),
            "timing_metrics": copy.deepcopy(
                trace_summary.get("timing_metrics")
            ),
            "token_usage": copy.deepcopy(trace_summary.get("token_usage")),
            "total_usage": copy.deepcopy(trace_summary.get("token_usage")),
            "total_cost": copy.deepcopy(trace_summary.get("total_cost")),
            "cache_savings": copy.deepcopy(trace_summary.get("cache_savings")),
        },
        "execution": {
            "mode": run_spec.mode,
            "status": execution_status,
            "timeout_sec": run_spec.timeout,
            "docker_image": run_spec.docker_image,
            "exit_code": trace_summary.get("exit_code"),
            "trace_complete": trace_summary.get("trace_complete"),
            "error_type": trace_summary.get("error_type"),
            "error_message": trace_summary.get("error_message"),
            "container": trace_summary.get("container"),
            "gateway": trace_summary.get("gateway"),
        },
        "evaluation": _evaluation_section(
            evaluation,
            raw_evaluation,
            runner_policy,
        ),
        "artifacts": {
            **run_spec.provider_artifacts,
            "workspace": "agent/workspace",
            "workspace_diff": str(workspace_diff.relative_to(run_dir)),
            "trace_summary": _rel_or_abs(trace_summary_path, run_dir),
            "trace_events": _rel_or_abs(trace_events_path, run_dir),
            "protocol_evidence": (
                _rel_or_abs(Path(protocol_evidence), run_dir)
                if isinstance(protocol_evidence, str)
                else None
            ),
            "business_simulator_evidence": (
                {
                    key: _rel_or_abs(Path(value), run_dir)
                    for key, value in simulator_evidence.items()
                    if isinstance(key, str) and isinstance(value, str)
                }
                if isinstance(simulator_evidence, dict)
                else None
            ),
            "model_call_budget_evidence": (
                {
                    key: _rel_or_abs(Path(value), run_dir)
                    if key in {"state", "events"}
                    else value
                    for key, value in model_call_budget_evidence.items()
                }
                if isinstance(model_call_budget_evidence, dict)
                else None
            ),
            "provider_request_evidence": (
                {
                    key: _rel_or_abs(Path(value), run_dir)
                    if key == "events"
                    else value
                    for key, value in provider_request_evidence.items()
                }
                if isinstance(provider_request_evidence, dict)
                else None
            ),
            "execution_probe_evidence": (
                _rel_or_abs(Path(execution_probe_evidence), run_dir)
                if isinstance(execution_probe_evidence, str)
                else None
            ),
            "execution_probe_evidence_sha256": (
                _sha256_file(Path(execution_probe_evidence))
                if isinstance(execution_probe_evidence, str)
                and Path(execution_probe_evidence).is_file()
                else None
            ),
            "security_probe_evidence": (
                _rel_or_abs(Path(security_probe_evidence), run_dir)
                if isinstance(security_probe_evidence, str)
                else None
            ),
            "security_probe_evidence_sha256": (
                _sha256_file(Path(security_probe_evidence))
                if isinstance(security_probe_evidence, str)
                and Path(security_probe_evidence).is_file()
                else None
            ),
            "scoring_inputs_manifest": (
                _rel_or_abs(Path(scoring_inputs_manifest), run_dir)
                if isinstance(scoring_inputs_manifest, str)
                else None
            ),
            "verifier_stdout": _optional_artifact(
                evaluation.stdout_path, run_dir
            ),
            "verifier_stderr": _optional_artifact(
                evaluation.stderr_path, run_dir
            ),
            "verifier_score": (
                str(evaluation.score_path.relative_to(run_dir))
                if evaluation.score_path
                else None
            ),
            "score_markdown": _optional_artifact(
                run_dir / "artifacts" / "llm_judge" / "score.md", run_dir
            ),
            "runner_policy": (
                _rel_or_abs(runner_policy_path, run_dir)
                if runner_policy_path is not None
                else None
            ),
            "container_lifecycle": _optional_artifact(
                run_dir / "artifacts" / "container_lifecycle.json", run_dir
            ),
            "container_archive_manifest": _optional_artifact(
                run_dir / "artifacts" / "container" / "archive_manifest.json",
                run_dir,
            ),
            "verifier_gate": _optional_artifact(
                run_dir / "artifacts" / "verifier_gate.json", run_dir
            ),
            "scoring_error": None,
        },
    }
    (run_dir / "result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    )
    return result


def update_scored_result(
    *,
    run_dir: Path,
    existing_result: dict[str, Any],
    evaluation: EvaluationResult,
    raw_evaluation: EvaluationResult,
    runner_policy: dict[str, Any],
    runner_policy_path: Path,
) -> dict[str, Any]:
    result = copy.deepcopy(existing_result)
    execution = result.get("execution")
    execution_status = (
        execution.get("status") if isinstance(execution, dict) else None
    )
    result["status"] = _overall_status(execution_status, evaluation.status)
    result["evaluation_mode"] = "score-only"
    result["result_path"] = str(run_dir / "result.json")
    result["evaluation"] = _evaluation_section(
        evaluation,
        raw_evaluation,
        runner_policy,
    )
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, dict):
        artifacts = {}
        result["artifacts"] = artifacts
    artifacts.update(
        {
            "verifier_stdout": _optional_artifact(
                evaluation.stdout_path,
                run_dir,
            ),
            "verifier_stderr": _optional_artifact(
                evaluation.stderr_path,
                run_dir,
            ),
            "verifier_score": (
                _optional_artifact(evaluation.score_path, run_dir)
                if evaluation.score_path is not None
                else None
            ),
            "score_markdown": _optional_artifact(
                run_dir / "artifacts" / "llm_judge" / "score.md",
                run_dir,
            ),
            "runner_policy": _rel_or_abs(runner_policy_path, run_dir),
            "verifier_gate": _optional_artifact(
                run_dir / "artifacts" / "verifier_gate.json",
                run_dir,
            ),
            "trace_report": None,
            "scoring_error": None,
        }
    )
    _write_json_atomic(run_dir / "result.json", result)
    return result


def update_scoring_error_result(
    *,
    run_dir: Path,
    existing_result: dict[str, Any],
    error_type: str,
    error_message: str,
) -> dict[str, Any]:
    error_path = run_dir / "artifacts" / "scoring_error.json"
    _write_json_atomic(
        error_path,
        {
            "schema_version": "harnessbench_scoring_error_v1",
            "error_type": error_type,
            "error_message": error_message,
            "failed_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    error_evaluation = EvaluationResult(
        status="error",
        score=None,
        exit_code=None,
        stdout_path=run_dir / "artifacts" / "verifier_output" / "stdout.log",
        stderr_path=run_dir / "artifacts" / "verifier_output" / "stderr.log",
        score_path=None,
        error_message=error_message,
    )
    result = copy.deepcopy(existing_result)
    result["status"] = "error"
    result["evaluation_mode"] = "score-only"
    result["result_path"] = str(run_dir / "result.json")
    result["evaluation"] = _evaluation_section(
        error_evaluation,
        error_evaluation,
        {},
    )
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, dict):
        artifacts = {}
        result["artifacts"] = artifacts
    artifacts.update(
        {
            "verifier_stdout": None,
            "verifier_stderr": None,
            "verifier_score": None,
            "score_markdown": None,
            "runner_policy": None,
            "trace_report": None,
            "scoring_error": _rel_or_abs(error_path, run_dir),
        }
    )
    _write_json_atomic(run_dir / "result.json", result)
    return result


def _rel_or_abs(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _optional_artifact(path: Path, root: Path) -> str | None:
    return _rel_or_abs(path, root) if path.is_file() else None


def _sha256_file(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _evaluation_payload(value: EvaluationResult) -> dict[str, Any]:
    return {
        "status": value.status,
        "score": value.score,
        "exit_code": value.exit_code,
        "timed_out": value.timed_out,
        "error_message": value.error_message,
    }


def _evaluation_section(
    evaluation: EvaluationResult,
    raw_evaluation: EvaluationResult,
    runner_policy: dict[str, Any],
) -> dict[str, Any]:
    return {
        # Flat fields remain the effective result for compatibility with
        # existing aggregators. The explicit objects keep policy overrides
        # auditable.
        "status": evaluation.status,
        "score": evaluation.score,
        "raw_verifier_status": raw_evaluation.status,
        "raw_verifier_score": raw_evaluation.score,
        "raw_status": raw_evaluation.status,
        "raw_score": raw_evaluation.score,
        "effective_status": evaluation.status,
        "effective_score": evaluation.score,
        "exit_code": evaluation.exit_code,
        "timed_out": evaluation.timed_out,
        "error_message": evaluation.error_message,
        "raw": _evaluation_payload(raw_evaluation),
        "effective": _evaluation_payload(evaluation),
        "hard_gates": runner_policy.get("hard_gates", {}),
        "budget_observations": runner_policy.get("budget", {}),
        "policy_enforcement": runner_policy.get("enforcement", {}),
        "runner_policy": {
            "violations": runner_policy.get("violations", []),
            "budget_violations": runner_policy.get("budget_violations", []),
            "hard_gates": runner_policy.get("hard_gates", {}),
            "enforcement": runner_policy.get("enforcement", {}),
            "budget": runner_policy.get("budget", {}),
        },
    }


def _overall_status(execution_status: object, evaluation_status: str) -> str:
    if execution_status == "ineligible":
        return "ineligible"
    if execution_status == "failed" and evaluation_status in {"pass", "fail"}:
        # Only explicitly scoreable failed traces can reach a concrete verifier
        # verdict. The execution failure remains a benchmark failure even if
        # its individual binary rules happen to pass; it is no longer an
        # infrastructure error with a missing score.
        return "fail"
    if execution_status != "finished":
        return "error"
    if evaluation_status == "pending":
        return "run_complete"
    if evaluation_status in {"pass", "fail"}:
        return evaluation_status
    return "error"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
