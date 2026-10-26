"""Harness-neutral lifecycle capability matching and additive evidence.

The capability contract answers a narrow question: can this runner/adaptor
combination expose enough normalized evidence to execute a task validly?  It
does not pre-award any rubric.  A harness can support timeout observation and
still score zero when its native exec ignores the requested timeout.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tracetrial_bench.types import TaskSpec


SCHEMA_VERSION = "harnessbench_execution_capabilities_v1"
EVIDENCE_SCHEMA_VERSION = "harnessbench_execution_probe_evidence_v1"

# These are adapter contracts, not claims that the harness under test behaves
# correctly.  All five formal adapters preserve native tool calls/results. The
# outer ProcessRunner supplies process-tree containment, while the terminal and
# background contracts mean their observable native results remain in trace.
_FORMAL_CAPABILITIES = frozenset(
    {
        "native_exec_v1",
        "post_tool_v1",
        "timeout_observation_v1",
        "process_containment_v1",
        "background_process_v1",
        "terminal_io_v1",
        "business_cli_v1",
        "task_tools_v1",
        # Every formal adapter normalizes its harness-native context compaction
        # boundary (and replacement summary when the native runtime provides
        # one) as a typed context_compaction event. This is observation only:
        # it does not assert that compaction will occur or preserve context.
        "native_context_compaction_observation_v1",
        # Task-scoped completion observation.  Adapters that need a native
        # terminal-event fallback or a narrowly recognized completed-tool-turn
        # soft exit may enable it without changing ordinary task behavior.
        "native_terminal_completion_v1",
        # Opt-in archival contract: the offline report may embed the immutable
        # task-tool transport ledger alongside the normalized native trace.
        # This is useful when a harness's own compaction legitimately removes
        # old tool messages from its native session archive.
        "task_tool_transport_report_v1",
        # Some OpenAI-compatible model endpoints reject Claude Code's final
        # response-format diagnostic after a tool turn even though Claude has
        # already reported terminal_reason=completed. Tasks that explicitly
        # require this contract may soften only that narrowly recognized exit.
        "claude_completed_tool_turn_soft_exit_v1",
        "native_child_delegation_v1",
        # Observation means the adapter can preserve typed child lifecycle
        # evidence.  It deliberately does not claim that the native child API
        # is asynchronous or that the parent has a pushed-completion boundary.
        "native_child_lifecycle_observation_v1",
    }
)
HARNESS_CAPABILITIES: dict[str, frozenset[str]] = {
    "codex": _FORMAL_CAPABILITIES
    | {
        "native_async_child_dispatch_v1",
        "native_child_return_boundary_v1",
        "native_pushed_child_completion_v1",
    },
    "openclaw": _FORMAL_CAPABILITIES
    | {
        "native_async_child_dispatch_v1",
        "native_child_return_boundary_v1",
        "native_pushed_child_completion_v1",
    },
    "hermes": _FORMAL_CAPABILITIES,
    "claudecode": _FORMAL_CAPABILITIES
    | {
        "native_async_child_dispatch_v1",
        "native_child_return_boundary_v1",
        "native_pushed_child_completion_v1",
    },
    "deepseek": _FORMAL_CAPABILITIES
    | {
        "native_async_child_dispatch_v1",
        "native_child_return_boundary_v1",
        "native_pushed_child_completion_v1",
    },
}


def capability_report(task: TaskSpec, harness: str) -> dict[str, Any]:
    required = (
        task.execution_probe.requires if task.execution_probe is not None else ()
    )
    available = HARNESS_CAPABILITIES.get(harness, frozenset())
    missing = tuple(item for item in required if item not in available)
    if "business_cli_v1" in required and task.business_simulator is None:
        missing = (*missing, "business_cli_v1:task_not_configured")
    if "task_tools_v1" in required and task.task_tools is None:
        missing = (*missing, "task_tools_v1:task_not_configured")
    return {
        "schema_version": SCHEMA_VERSION,
        "harness": harness,
        "probe_version": (
            task.execution_probe.version if task.execution_probe is not None else None
        ),
        "required": list(required),
        "available": sorted(available),
        "missing": list(dict.fromkeys(missing)),
        "compatible": not missing,
        "default_path_unchanged": task.execution_probe is None,
    }


def require_capabilities(task: TaskSpec, harness: str) -> dict[str, Any]:
    report = capability_report(task, harness)
    if not report["compatible"]:
        joined = ", ".join(report["missing"])
        raise ValueError(
            f"harness {harness!r} lacks execution capabilities required by "
            f"{task.task_id}: {joined}"
        )
    return report


def adapter_extra_args(task: TaskSpec, harness: str) -> dict[str, str]:
    """Return private adapter switches for task-scoped execution probes."""

    probe = task.execution_probe
    if probe is None:
        return {}
    lifecycle_probe = "native_child_lifecycle_observation_v1" in probe.requires
    legacy_probe = "native_child_delegation_v1" in probe.requires
    async_child_probe = all(
        capability in probe.requires
        for capability in (
            "native_async_child_dispatch_v1",
            "native_child_return_boundary_v1",
            "native_pushed_child_completion_v1",
        )
    )
    claude_soft_exit = "claude_completed_tool_turn_soft_exit_v1" in probe.requires
    terminal_completion = "native_terminal_completion_v1" in probe.requires
    if harness in {"codex", "openclaw"} and (lifecycle_probe or legacy_probe):
        return {"harnessbench-native-child-delegation": "true"}
    if harness == "claudecode" and (
        lifecycle_probe or claude_soft_exit or terminal_completion
    ):
        result: dict[str, str] = {}
        if lifecycle_probe:
            result["harnessbench-native-child-delegation"] = "true"
        if async_child_probe:
            result["harnessbench-native-child-return-boundary"] = "true"
        if lifecycle_probe or claude_soft_exit or terminal_completion:
            result["harnessbench-approval-claude-diagnostic-soft-exit"] = "true"
        return result
    if harness == "deepseek" and (lifecycle_probe or terminal_completion):
        if async_child_probe:
            return {"harnessbench-deepseek-continuable-subagents": "true"}
        return {"harnessbench-deepseek-terminal-event-return": "true"}
    return {}


def persist_execution_probe_evidence(
    task: TaskSpec,
    *,
    harness: str,
    layout: Any,
    trace_summary: dict[str, Any],
    capability: dict[str, Any],
) -> str | None:
    """Write additive post-tool/process evidence without rewriting native trace."""

    if task.execution_probe is None:
        return None
    source_path = Path(str(trace_summary.get("events_path", "")))
    source_events = _load_event_list(source_path)
    post_tool = [
        _post_tool_observation(event)
        for event in source_events
        if event.get("event_type") == "tool"
        and isinstance(event.get("tool_call"), dict)
        and event.get("tool_result") is not None
    ]
    failure_gate_decisions = [
        _post_tool_failure_gate_decision(observation)
        for observation in post_tool
    ]
    containment = _process_containment(trace_summary)
    payload = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "task_id": task.task_id,
        "harness": harness,
        "capability_report": capability,
        "source_trace": str(source_path),
        "post_tool_observations": post_tool,
        "post_tool_failure_gate_decisions": failure_gate_decisions,
        "process_containment": containment,
        "execution_status": trace_summary.get("status"),
        "execution_timed_out": bool(trace_summary.get("timed_out", False)),
        "execution_error_type": trace_summary.get("error_type"),
    }
    directory = layout.artifacts_dir / "execution_probe"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "evidence.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return str(path)


def _load_event_list(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _process_containment(trace_summary: dict[str, Any]) -> dict[str, Any] | None:
    direct = trace_summary.get("process_containment")
    if isinstance(direct, dict):
        return direct
    conversation = trace_summary.get("conversation")
    summary_path = Path(str(trace_summary.get("summary_path", "")))
    turn_paths = conversation.get("turn_summaries") if isinstance(conversation, dict) else None
    if not isinstance(turn_paths, list) or not summary_path.is_file():
        return None
    receipts: list[dict[str, Any]] = []
    for value in turn_paths:
        if not isinstance(value, str):
            continue
        payload_path = summary_path.parent / value
        try:
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        receipt = payload.get("process_containment") if isinstance(payload, dict) else None
        if isinstance(receipt, dict):
            receipts.append(receipt)
    if not receipts:
        return None
    return {
        "schema_version": "harness-process-containment-aggregate-v1",
        "turn_count": len(receipts),
        "descendant_cleanup_valid": all(
            receipt.get("descendant_cleanup_valid") is True for receipt in receipts
        ),
        "residual_process_count": sum(
            int(receipt.get("residual_process_count", 0)) for receipt in receipts
        ),
        "tracked_descendant_count": sum(
            int(receipt.get("tracked_descendant_count", 0)) for receipt in receipts
        ),
        "turn_receipts": receipts,
    }


def _post_tool_observation(event: dict[str, Any]) -> dict[str, Any]:
    call = event.get("tool_call")
    result = event.get("tool_result")
    raw = event.get("raw_event")
    payload = {"tool_result": result, "raw_event": raw}
    raw_result = raw.get("tool_result") if isinstance(raw, dict) else None
    return {
        "source_event_seq": event.get("seq"),
        "tool_call_id": call.get("tool_call_id"),
        "tool_name": call.get("tool_name") or call.get("name"),
        "arguments": call.get("arguments", {}),
        "tool_result": result,
        # Prefer the terminal result over a raw started-event status.  Codex,
        # for example, places ``in_progress`` in the call and ``failed`` in
        # the correlated result.
        "status": (
            _first_named_value(result, {"status", "reason", "error_type"})
            or _first_named_value(raw_result, {"status", "reason", "error_type"})
            or _first_named_value(raw, {"status", "reason", "error_type"})
        ),
        "exit_code": _first_named_value(payload, {"exit_code", "exitcode"}),
        "timed_out": _truthy_named_value(payload, {"timed_out", "timeout"}),
        "error": _truthy_named_value(
            payload,
            {
                "error",
                "is_error",
                "iserror",
                "failed",
                "harnessbench_task_tool_error",
            },
        ),
        "duration_ms": _first_named_value(payload, {"duration_ms", "elapsed_ms"}),
        "stdout_channel_present": _has_named_key(payload, {"stdout"}),
        "stderr_channel_present": _has_named_key(payload, {"stderr"}),
    }


def _post_tool_failure_gate_decision(observation: dict[str, Any]) -> dict[str, Any]:
    """Persist the generic consumer decision for a success-shaped artifact.

    The E4a leaf is specifically about consumers that key acceptance on an
    explicit post-tool error signal.  Recording the decision separately from
    the observation makes that causal edge auditable instead of inferring
    rejection later from the absence of an acceptance file.
    """

    error = observation.get("error")
    rejected = error is True or isinstance(error, str) and bool(error.strip())
    return {
        "source_event_seq": observation.get("source_event_seq"),
        "tool_call_id": observation.get("tool_call_id"),
        "consumer": "explicit_error_failure_gate_v1",
        "decision": "reject" if rejected else "accept",
        "artifact_accepted": not rejected,
        "reason_code": (
            "EXPLICIT_POST_TOOL_ERROR"
            if rejected
            else "MISSING_EXPLICIT_POST_TOOL_ERROR"
        ),
    }


def _walk(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key).lower().replace("-", "_"), child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _first_named_value(value: Any, names: set[str]) -> Any:
    for key, child in _walk(value):
        if key in names and not isinstance(child, (dict, list)):
            return child
    return None


def _truthy_named_value(value: Any, names: set[str]) -> bool:
    for key, child in _walk(value):
        if key in names and (child is True or isinstance(child, str) and child):
            return True
    return False


def _has_named_key(value: Any, names: set[str]) -> bool:
    return any(key in names for key, _ in _walk(value))
