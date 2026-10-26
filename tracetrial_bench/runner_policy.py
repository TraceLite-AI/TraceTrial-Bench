from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from tracetrial_bench.types import EvaluationResult, TaskSpec


RUNNER_POLICY_SCHEMA = "harnessbench_runner_policy_v4"
_SAFE_KEY = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_SAFE_PROVIDER = re.compile(r"^[A-Za-z0-9_.:/-]{1,160}$")
_OPENCLAW_THINKING_LEVELS = {
    "off",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "adaptive",
    "max",
}
_CODEX_REASONING_LEVELS = {"minimal", "low", "medium", "high"}
_CLAUDECODE_EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}
_DEEPSEEK_REASONING_LEVELS = {"low", "medium", "high", "max"}
_DEEPSEEK_NATIVE_REASONING = {
    "low": "off",
    "medium": "high",
    "high": "high",
    "max": "max",
}


@dataclass(frozen=True)
class HarnessInvocationPolicy:
    """Runner-owned harness arguments and their non-secret audit record."""

    timeout: int | None
    extra_args: dict[str, str]
    report: dict[str, Any]


@dataclass(frozen=True)
class EvaluationPolicyResult:
    """Raw verifier result plus the score that benchmark aggregation may use."""

    raw: EvaluationResult
    effective: EvaluationResult
    report: dict[str, Any]


def resolve_harness_invocation(
    *,
    harness: str,
    requested_timeout: int | None,
    task_max_wall_time_sec: int | None,
    task_max_turns: int | None,
    requested_extra_args: Mapping[str, str],
    strict: bool,
    requested_reasoning_intensity: str | None = None,
) -> HarnessInvocationPolicy:
    """Resolve task budget caps and strict-mode harness argument allowlists.

    Strict mode intentionally records only argument *keys* for rejected values.
    A rejected value may itself be a secret or an attempted prompt injection and
    therefore must not appear in any benchmark artifact.
    """

    effective_timeout = _bounded_positive_int(
        requested=requested_timeout,
        limit=task_max_wall_time_sec,
        label="timeout",
    )
    requested = dict(requested_extra_args)
    effective = dict(requested)
    reasoning_report = _resolve_reasoning_intensity(
        harness=harness,
        value=requested_reasoning_intensity,
        requested_extra_args=requested,
    )
    dropped: list[dict[str, str]] = []
    adjustments: list[dict[str, Any]] = []

    if strict:
        effective = {}
        allowed = _strict_allowed_args(harness)
        for original_key, value in requested.items():
            key = _canonical_arg_key(original_key)
            display_key = _display_key(original_key)
            if harness == "openclaw" and key == "session-key":
                dropped.append({"key": display_key, "reason": "runner_owned_session"})
                continue
            if _is_sensitive_key(key) and not (
                harness == "deepseek"
                and key == "harnessbench-deepseek-max-output-tokens"
            ):
                dropped.append({"key": display_key, "reason": "sensitive_argument"})
                continue
            if key not in allowed:
                dropped.append({"key": display_key, "reason": "not_allowlisted"})
                continue
            if harness == "openclaw" and key == "timeout":
                # The subprocess and OpenClaw must share the exact same wall
                # budget.  A free-form --extra-arg may not create a second one.
                dropped.append({"key": display_key, "reason": "runner_owned_budget"})
                continue
            if harness == "hermes" and key == "max-turns":
                # Added below after validation. Hermes uses this option for
                # Agent/tool-loop iterations; budget.max_turns instead limits
                # runner-owned user-input turns, so the two must not be
                # coupled.
                continue
            if harness == "openclaw" and key == "thinking":
                normalized_value = str(value).strip().lower()
                if normalized_value not in _OPENCLAW_THINKING_LEVELS:
                    dropped.append({"key": display_key, "reason": "invalid_value"})
                    continue
                effective[key] = normalized_value
                continue
            if harness == "openclaw" and key == "verbose":
                normalized_value = str(value).strip().lower()
                if normalized_value not in {"on", "off"}:
                    dropped.append({"key": display_key, "reason": "invalid_value"})
                    continue
                effective[key] = normalized_value
                continue
            if (
                harness == "claudecode"
                and key == "harnessbench-approval-claude-diagnostic-soft-exit"
            ):
                normalized_value = str(value).strip().lower()
                if normalized_value != "true":
                    dropped.append({"key": display_key, "reason": "invalid_value"})
                    continue
                effective[key] = normalized_value
                continue
            if (
                harness == "deepseek"
                and key == "harnessbench-deepseek-max-output-bytes"
            ):
                try:
                    output_limit = int(str(value).strip())
                except ValueError:
                    output_limit = 0
                if not 0 < output_limit <= 512_000_000:
                    dropped.append({"key": display_key, "reason": "invalid_value"})
                    continue
                effective[key] = str(output_limit)
                continue
            if (
                harness == "deepseek"
                and key == "harnessbench-deepseek-max-output-tokens"
            ):
                try:
                    output_token_limit = int(str(value).strip())
                except ValueError:
                    output_token_limit = 0
                if not 0 < output_token_limit <= 1_000_000:
                    dropped.append({"key": display_key, "reason": "invalid_value"})
                    continue
                effective[key] = str(output_token_limit)
                continue
            if harness == "hermes" and key == "provider":
                normalized_value = str(value).strip()
                if not _SAFE_PROVIDER.fullmatch(
                    normalized_value
                ) or normalized_value.startswith("-"):
                    dropped.append({"key": display_key, "reason": "invalid_value"})
                    continue
                effective[key] = normalized_value
                continue
            effective[key] = str(value)

    if reasoning_report["native_key"] is not None:
        effective[str(reasoning_report["native_key"])] = str(
            reasoning_report["native_value"]
        )

    if harness == "hermes":
        requested_turn_limit = any(
            _canonical_arg_key(key) == "max-turns" for key in requested
        )
        requested_turns = _requested_positive_int(requested, "max-turns")
        _remove_arg_aliases(effective, "max-turns")
        if requested_turn_limit:
            if requested_turns is None:
                if strict:
                    dropped.append(
                        {"key": "max-turns", "reason": "invalid_value"}
                    )
                else:
                    raise ValueError("max-turns must be a positive integer")
            else:
                effective["max-turns"] = str(requested_turns)

    if harness == "openclaw":
        _remove_arg_aliases(effective, "timeout")
        if effective_timeout is not None:
            effective["timeout"] = str(effective_timeout)
        adjustments.append(
            {
                "key": "timeout",
                "reason": "runner_wall_timeout",
                "requested": requested_timeout,
                "limit": task_max_wall_time_sec,
                "effective": effective_timeout,
            }
        )

    report = {
        "mode": "strict" if strict else "compatibility",
        "harness": harness,
        "session_policy": (
            "runner_generated"
            if strict and harness == "openclaw"
            else "adapter_default"
        ),
        "requested_keys": sorted(_display_key(key) for key in requested),
        "effective_keys": sorted(effective),
        "dropped": sorted(dropped, key=lambda item: (item["key"], item["reason"])),
        "adjustments": adjustments,
        "wall_time": {
            "requested_sec": requested_timeout,
            "task_limit_sec": task_max_wall_time_sec,
            "effective_sec": effective_timeout,
            "clamped": (
                requested_timeout is not None
                and effective_timeout is not None
                and requested_timeout != effective_timeout
            ),
        },
        "turn_budget": {
            "task_limit": task_max_turns,
            "task_limit_scope": "runner_user_input_turns",
            "effective_harness_limit": (
                int(effective["max-turns"])
                if harness == "hermes" and "max-turns" in effective
                else None
            ),
            "effective_harness_limit_scope": (
                "hermes_agent_tool_loop_iterations"
                if harness == "hermes" and "max-turns" in effective
                else None
            ),
            "coupled_to_task_limit": False,
        },
        "reasoning_intensity": reasoning_report,
    }
    return HarnessInvocationPolicy(
        timeout=effective_timeout,
        extra_args=effective,
        report=report,
    )


def apply_evaluation_policy(
    *,
    task: TaskSpec,
    trace_summary: Mapping[str, Any],
    raw_evaluation: EvaluationResult,
    simulator_manifest: Mapping[str, Any] | None,
    invocation_report: Mapping[str, Any] | None = None,
) -> EvaluationPolicyResult:
    """Apply runner-owned integrity and budget gates after the verifier exits."""

    has_simulator = task.business_simulator is not None
    integrity_value = (
        simulator_manifest.get("integrity_valid")
        if isinstance(simulator_manifest, Mapping)
        else None
    )
    simulator_budget = (
        simulator_manifest.get("budget")
        if isinstance(simulator_manifest, Mapping)
        and isinstance(simulator_manifest.get("budget"), Mapping)
        else {}
    )
    simulator_reported_exceeded = simulator_budget.get("exceeded") is True
    simulator_observed = simulator_budget.get("observed_simulator_requests")
    simulator_limit = simulator_budget.get("max_simulator_requests")
    simulator_budget_applicable = has_simulator and task.max_tool_calls is not None
    simulator_count_observed = isinstance(simulator_observed, int) and not isinstance(
        simulator_observed, bool
    )
    simulator_limit_matches = bool(
        simulator_budget_applicable and simulator_limit == task.max_tool_calls
    )
    simulator_recomputed_exceeded = bool(
        simulator_budget_applicable
        and simulator_count_observed
        and simulator_observed > task.max_tool_calls  # type: ignore[operator]
    )
    # Do not trust the manifest's convenience boolean by itself.  Its reported
    # limit must match the immutable task specification, and the runner also
    # recomputes the comparison from the observed count.
    simulator_exceeded = bool(
        simulator_budget_applicable
        and (
            simulator_reported_exceeded
            or simulator_recomputed_exceeded
            or not simulator_limit_matches
        )
    )

    trace_complete = trace_summary.get("trace_complete") is True
    structured_trace_available = (
        trace_summary.get("structured_trace_status") == "available"
    )
    tool_stats = trace_summary.get("tool_stats")
    tool_stats = tool_stats if isinstance(tool_stats, Mapping) else {}
    trace_tool_calls = tool_stats.get("total_tool_calls")
    trace_count_observed = isinstance(trace_tool_calls, int) and not isinstance(
        trace_tool_calls, bool
    )
    trace_budget_assessed = bool(
        task.max_tool_calls is not None
        and trace_complete
        and structured_trace_available
        and trace_count_observed
    )
    trace_exceeded = bool(
        trace_budget_assessed and trace_tool_calls > task.max_tool_calls  # type: ignore[operator]
    )

    hard_gates = {
        "simulator_evidence_integrity": {
            "applicable": has_simulator,
            "passed": integrity_value is True if has_simulator else None,
            "observed": integrity_value,
        },
        "simulator_request_budget": {
            "applicable": simulator_budget_applicable,
            "passed": (not simulator_exceeded if simulator_budget_applicable else None),
            "limit": task.max_tool_calls,
            "manifest_limit": simulator_limit,
            "manifest_limit_matches_task": (
                simulator_limit_matches if simulator_budget_applicable else None
            ),
            "observed": simulator_observed,
            "exceeded": simulator_exceeded,
            "reported_exceeded": simulator_reported_exceeded,
            "recomputed_exceeded": simulator_recomputed_exceeded,
        },
        "complete_trace_tool_budget": {
            "applicable": task.max_tool_calls is not None,
            "assessed": trace_budget_assessed,
            "passed": not trace_exceeded if trace_budget_assessed else None,
            "limit": task.max_tool_calls,
            "observed": trace_tool_calls if trace_count_observed else None,
            "trace_complete": trace_complete,
            "structured_trace_available": structured_trace_available,
            "exceeded": trace_exceeded,
        },
    }
    # The simulator enforces its own request ceiling before dispatching an
    # over-budget operation, so requests after the ceiling cannot improve the
    # private business state.  Record that exhaustion for efficiency analysis,
    # but do not erase completion/recovery points that were earned before the
    # ceiling.  Integrity failures and trace-observed tool calls that were not
    # stopped before taking effect remain hard gates.
    violations: list[str] = []
    budget_violations: list[str] = []
    if has_simulator and integrity_value is not True:
        violations.append("simulator_evidence_integrity")
    if simulator_exceeded:
        budget_violations.append("simulator_request_budget")
    if trace_exceeded:
        if has_simulator:
            # Simulator-backed tasks already reject over-ceiling business
            # operations before dispatch.  A complete trace is useful for
            # measuring the attempted excess, but applying a second score-zero
            # gate would duplicate the task's efficiency penalty.
            budget_violations.append("complete_trace_tool_budget")
        else:
            # A generic workspace task has no runner-owned business gateway
            # that can prove calls past the ceiling were effect-free.
            violations.append("complete_trace_tool_budget")

    execution_status = trace_summary.get("status")
    infrastructure_reason: str | None = None
    if execution_status == "infrastructure_error":
        infrastructure_reason = "harness_or_simulator_infrastructure_error"
    elif raw_evaluation.status == "error" or raw_evaluation.timed_out:
        infrastructure_reason = "verifier_infrastructure_error"
    elif execution_status == "ineligible":
        infrastructure_reason = "harness_ineligible"

    enforced = bool(violations and infrastructure_reason is None)
    effective = raw_evaluation
    if enforced:
        effective = replace(
            raw_evaluation,
            status="fail",
            score=0.0,
            error_message=("runner policy hard gate failed: " + ", ".join(violations)),
        )

    report = {
        "schema_version": RUNNER_POLICY_SCHEMA,
        "task_id": task.task_id,
        "execution_status": execution_status,
        "raw_evaluation": _evaluation_summary(raw_evaluation),
        "effective_evaluation": _evaluation_summary(effective),
        "hard_gates": hard_gates,
        "violations": violations,
        "budget_violations": budget_violations,
        "enforcement": {
            "applied": enforced,
            "skipped_for_infrastructure": infrastructure_reason is not None,
            "skip_reason": infrastructure_reason,
        },
        "budget": {
            "max_turns": task.max_turns,
            "max_tool_calls": task.max_tool_calls,
            "max_wall_time_sec": task.max_wall_time_sec,
            "observed_simulator_requests": simulator_observed,
            "observed_complete_trace_tool_calls": (
                trace_tool_calls if trace_budget_assessed else None
            ),
            "trace_complete": trace_complete,
            "structured_trace_available": structured_trace_available,
        },
        "invocation": dict(invocation_report or {}),
    }
    return EvaluationPolicyResult(
        raw=raw_evaluation, effective=effective, report=report
    )


def persist_runner_policy(report: Mapping[str, Any], artifacts_dir: Path) -> str:
    path = artifacts_dir / "runner_policy.json"
    path.write_text(
        json.dumps(dict(report), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return str(path)


def load_simulator_manifest(
    evidence: Mapping[str, str] | None,
) -> dict[str, Any] | None:
    if not evidence:
        return None
    path_value = evidence.get("manifest")
    if not isinstance(path_value, str):
        return None
    try:
        value = json.loads(Path(path_value).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _strict_allowed_args(harness: str) -> set[str]:
    if harness == "hermes":
        return {"provider", "max-turns"}
    if harness == "openclaw":
        return {"thinking", "timeout", "verbose"}
    if harness == "claudecode":
        return {
            "harnessbench-approval-claude-diagnostic-soft-exit",
            "harnessbench-max-output-bytes",
        }
    if harness == "deepseek":
        return {
            "harnessbench-deepseek-max-output-bytes",
            "harnessbench-deepseek-max-output-tokens",
        }
    return set()


def _resolve_reasoning_intensity(
    *,
    harness: str,
    value: str | None,
    requested_extra_args: Mapping[str, str],
) -> dict[str, str | None]:
    if value is None:
        return {
            "requested": None,
            "effective": None,
            "native_key": None,
            "native_value": None,
        }
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError("reasoning-intensity must not be empty")

    if harness == "codex":
        if normalized not in _CODEX_REASONING_LEVELS:
            raise ValueError(
                "codex reasoning-intensity must be one of: "
                + ", ".join(sorted(_CODEX_REASONING_LEVELS))
            )
        _reject_reasoning_conflict(requested_extra_args, "config", harness)
        return {
            "requested": value,
            "effective": normalized,
            "native_key": "config",
            "native_value": f'model_reasoning_effort="{normalized}"',
        }

    if harness == "claudecode":
        if normalized not in _CLAUDECODE_EFFORT_LEVELS:
            raise ValueError(
                "claudecode reasoning-intensity must be one of: "
                + ", ".join(sorted(_CLAUDECODE_EFFORT_LEVELS))
            )
        _reject_reasoning_conflict(requested_extra_args, "effort", harness)
        return {
            "requested": value,
            "effective": normalized,
            "native_key": "effort",
            "native_value": normalized,
        }

    if harness == "openclaw":
        if normalized not in _OPENCLAW_THINKING_LEVELS:
            raise ValueError(
                "openclaw reasoning-intensity must be one of: "
                + ", ".join(sorted(_OPENCLAW_THINKING_LEVELS))
            )
        _reject_reasoning_conflict(requested_extra_args, "thinking", harness)
        return {
            "requested": value,
            "effective": normalized,
            "native_key": "thinking",
            "native_value": normalized,
        }

    if harness == "deepseek":
        if normalized not in _DEEPSEEK_REASONING_LEVELS:
            raise ValueError(
                "deepseek reasoning-intensity must be one of: "
                + ", ".join(sorted(_DEEPSEEK_REASONING_LEVELS))
            )
        _reject_reasoning_conflict(
            requested_extra_args, "reasoning-effort", harness
        )
        return {
            "requested": value,
            "effective": normalized,
            "native_key": "reasoning-effort",
            "native_value": _DEEPSEEK_NATIVE_REASONING[normalized],
        }

    raise ValueError(
        "reasoning-intensity is only supported for codex, claudecode, "
        "openclaw, and deepseek"
    )


def _reject_reasoning_conflict(
    requested_extra_args: Mapping[str, str],
    native_key: str,
    harness: str,
) -> None:
    for key in requested_extra_args:
        if _canonical_arg_key(key) == native_key:
            raise ValueError(
                f"reasoning-intensity for {harness} conflicts with "
                f"--extra-arg {native_key}=..."
            )


def _canonical_arg_key(value: str) -> str:
    return value.strip().lower().lstrip("-").replace("_", "-")


def _display_key(value: str) -> str:
    stripped = value.strip()
    return stripped if _SAFE_KEY.fullmatch(stripped) else "<invalid-key>"


def _is_sensitive_key(value: str) -> bool:
    normalized = value.replace("-", "_")
    return (
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


def _remove_arg_aliases(arguments: dict[str, str], canonical_key: str) -> None:
    for key in tuple(arguments):
        if _canonical_arg_key(key) == canonical_key:
            arguments.pop(key, None)


def _requested_positive_int(
    arguments: Mapping[str, str], canonical_key: str
) -> int | None:
    for key, value in arguments.items():
        if _canonical_arg_key(key) != canonical_key:
            continue
        try:
            parsed = int(str(value).strip())
        except ValueError:
            return None
        return parsed if parsed > 0 else None
    return None


def _bounded_positive_int(
    *,
    requested: int | None,
    limit: int | None,
    label: str,
) -> int | None:
    if requested is not None and (isinstance(requested, bool) or requested <= 0):
        raise ValueError(f"{label} must be a positive integer")
    if limit is not None and (isinstance(limit, bool) or limit <= 0):
        raise ValueError(f"task {label} limit must be a positive integer")
    if requested is None:
        return limit
    if limit is None:
        return requested
    return min(requested, limit)


def _evaluation_summary(value: EvaluationResult) -> dict[str, Any]:
    return {
        "status": value.status,
        "score": value.score,
        "exit_code": value.exit_code,
        "timed_out": value.timed_out,
        "error_message": value.error_message,
    }
