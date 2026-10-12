#!/usr/bin/env python3
"""Deterministic, attribution-aware verifier for L11 pipeline timeout."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
from pathlib import Path
from typing import Any, Iterable


HERE = Path(__file__).resolve().parent
EXPECTED_TIMEOUT_SECONDS = 4.0
EXPECTED_STEPS = [f"BLD-{number}" for number in range(501, 511)]
PRESSURE_STEPS = ["BLD-502", "BLD-505", "BLD-507"]
RELEASE_TURN = {
    **{step: 1 for step in EXPECTED_STEPS[:4]},
    **{step: 2 for step in EXPECTED_STEPS[4:7]},
    **{step: 3 for step in EXPECTED_STEPS[7:]},
}
EXEC_TOOLS = {
    "bash",
    "command_execution",
    "exec",
    "exec_command",
    "run",
    "run_command",
    "shell",
    "terminal",
}
START_RE = re.compile(
    r"\[(BLD-\d+)\]\s+phase\s+(?:codegen|compile|link|test|publish)\s+started",
    re.I,
)
EXPLICIT_TIMEOUT_RE = re.compile(
    r"\b(?:timed?\s*out|timeout|deadline exceeded|terminated due to timeout|killed due to timeout)\b",
    re.I,
)
BACKGROUND_RE = re.compile(
    r"\b(?:background|still running|continues? running|pollable|session id|resume|re-run|retry)\b",
    re.I,
)
DISPOSITION_RE = re.compile(
    r"\b(?:killed|terminated|stopped|cancelled|canceled)\b|"
    r"\b(?:no|zero)\s+(?:remaining\s+)?(?:child\s+)?process(?:es)?\b|"
    r"\bprocess group (?:was )?(?:killed|terminated|stopped)\b",
    re.I,
)


def load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def load_strict(path: Path) -> tuple[Any, str | None]:
    """Load strict JSON and reject symlinked deliverables and duplicate keys."""

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, child in pairs:
            if key in value:
                raise ValueError(f"duplicate key: {key}")
            value[key] = child
        return value

    try:
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError("report path must not contain symlinks")
        return (
            json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicates),
            None,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return None, str(exc)


def leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [leaf for child in value.values() for leaf in leaves(child)]
    if isinstance(value, list):
        return [leaf for child in value for leaf in leaves(child)]
    return [] if value is None else [str(value)]


def walk(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def canonical_trace(run_dir: Path) -> tuple[list[dict[str, Any]], bool, str]:
    candidates: list[Path] = []
    for root in (run_dir / "artifacts" / "trace", run_dir / "artifacts" / "container" / "trace"):
        if root.is_dir():
            candidates.extend(sorted(root.glob("*/events.json")))
    resolved: dict[Path, Path] = {}
    for path in candidates:
        try:
            resolved[path.resolve(strict=True)] = path
        except OSError:
            pass
    if len(resolved) != 1:
        return [], False, f"expected one canonical trace, found {len(resolved)}"
    path = next(iter(resolved.values()))
    value = load(path, None)
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        return [], False, "canonical events.json is not a list of objects"
    seqs = [row.get("seq") for row in value]
    if any(type(seq) is not int for seq in seqs) or seqs != list(range(1, len(seqs) + 1)):
        return value, False, "trace sequence is missing, duplicated, or non-contiguous"
    return value, True, str(path)


def probe_payload(run_dir: Path) -> dict[str, Any]:
    value = load(run_dir / "artifacts" / "execution_probe" / "evidence.json", {})
    return value if isinstance(value, dict) else {}


def tool_name(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return ""
    return str(call.get("tool_name", call.get("name", ""))).lower()


def tool_call_id(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    return str(call.get("tool_call_id", call.get("id", ""))) if isinstance(call, dict) else ""


def turn(event: dict[str, Any]) -> int | None:
    value = event.get("conversation_turn", event.get("turn_index"))
    return value if type(value) is int else None


def arguments(event: dict[str, Any]) -> Any:
    call = event.get("tool_call")
    return call.get("arguments", {}) if isinstance(call, dict) else {}


def command_text(event: dict[str, Any]) -> str:
    value = arguments(event)
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("command", "cmd", "script"):
            child = value.get(key)
            if isinstance(child, str):
                return child
    return ""


def result_text(event: dict[str, Any]) -> str:
    return "\n".join(leaves(event.get("tool_result")))


def is_exec(event: dict[str, Any]) -> bool:
    name = tool_name(event)
    return name in EXEC_TOOLS or name.rsplit("/", 1)[-1] in EXEC_TOOLS


def unique_tools(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one, maximally informative event per native tool-call ID."""
    positions: dict[str, int] = {}
    result: list[dict[str, Any]] = []
    for event in events:
        if event.get("event_type") != "tool":
            continue
        call_id = tool_call_id(event)
        key = call_id or f"event-seq:{event.get('seq')}"
        if key not in positions:
            positions[key] = len(result)
            result.append(event)
        elif len(result_text(event)) > len(result_text(result[positions[key]])):
            result[positions[key]] = event
    return result


def scenario_commands() -> dict[str, str]:
    value = load(HERE.parent / "simulator" / "scenario.json", {})
    commands = value.get("commands", {}) if isinstance(value, dict) else {}
    return {str(key): str(child) for key, child in commands.items()} if isinstance(commands, dict) else {}


def canonical_commands(commands: dict[str, str]) -> dict[str, str]:
    return {step: shlex.join(["bash", "-c", command]) for step, command in commands.items()}


def retrieval_step(event: dict[str, Any]) -> str | None:
    if not is_exec(event):
        return None
    command = command_text(event).strip()
    match = re.fullmatch(
        r"(?:(?:cd\s+/workspace\s*&&\s*)?"
        r"(?:/workspace/|\./|\$WORKSPACE/)?buildctl\s+step_command\s+)"
        r"(BLD-\d+)\s*",
        command,
    )
    if not match:
        return None
    step = match.group(1)
    text = result_text(event)
    if not re.search(r'"shell_command"\s*:', text) or step not in text:
        return None
    return step


def pipeline_step(event: dict[str, Any], commands: dict[str, str]) -> str | None:
    if not is_exec(event):
        return None
    command = command_text(event)
    result = result_text(event)
    # Retrieval output embeds the future command and start marker; it is not execution.
    if "buildctl step_command" in command and re.search(r'"shell_command"\s*:', result):
        return None
    for step, expected in commands.items():
        phase = re.search(r"phase ([a-z]+) started", expected)
        phase_name = phase.group(1) if phase else ""
        if (
            step in command
            and f"builds/{step}.log" in command
            and f"phase {phase_name} started" in command
        ):
            return step
    started = START_RE.search(result)
    return started.group(1) if started and started.group(1) in commands else None


def numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def configured_timeouts(event: dict[str, Any]) -> list[float]:
    values: list[float] = []
    for key, value in walk(arguments(event)):
        normalized = key.replace("_", "").replace("-", "").lower()
        if normalized not in {
            "timeout",
            "timeoutms",
            "timeoutmilliseconds",
            "timeoutsec",
            "timeoutseconds",
            "yieldtimems",
        }:
            continue
        number = numeric(value)
        if number is None:
            continue
        if normalized in {"timeoutms", "timeoutmilliseconds", "yieldtimems"}:
            number /= 1000
        elif normalized == "timeout" and number > 1000:
            number /= 1000
        values.append(number)
    return values


def exact_timeout(event: dict[str, Any]) -> bool:
    values = configured_timeouts(event)
    return len(values) == 1 and abs(values[0] - EXPECTED_TIMEOUT_SECONDS) <= 0.001


def explicit_timeout(event: dict[str, Any]) -> bool:
    for key, value in walk(event.get("tool_result")):
        normalized = key.replace("_", "").replace("-", "").lower()
        if normalized in {"timedout", "timeout", "isterminated"} and value is True:
            return True
    return bool(EXPLICIT_TIMEOUT_RE.search(result_text(event)))


def terminal_timeout(event: dict[str, Any]) -> bool:
    text = result_text(event)
    return explicit_timeout(event) and not BACKGROUND_RE.search(text)


def partial_stdout(event: dict[str, Any], step: str) -> bool:
    text = result_text(event)
    return bool(START_RE.search(text)) and f"[{step}]" in text and "phase complete" not in text


def explicit_disposition(event: dict[str, Any]) -> bool:
    text = result_text(event)
    return bool(DISPOSITION_RE.search(text)) and not BACKGROUND_RE.search(text)


def exit_codes(event: dict[str, Any]) -> list[int]:
    values: list[int] = []
    for key, value in walk(event.get("tool_result")):
        if key.replace("_", "").lower() != "exitcode":
            continue
        number = numeric(value)
        if number is not None and number.is_integer():
            values.append(int(number))
    return values


def native_outcome(event: dict[str, Any] | None) -> str:
    """Classify only the initial model-visible native result, never private probe state."""
    if event is None:
        return "not_run"
    if explicit_timeout(event):
        return "timed_out"
    codes = exit_codes(event)
    if 0 in codes:
        return "succeeded"
    if any(code != 0 for code in codes):
        return "failed"
    for key, value in walk(event.get("tool_result")):
        normalized = key.replace("_", "").lower()
        if normalized in {"error", "iserror"} and value is True:
            return "failed"
        if normalized == "status" and str(value).lower() in {"failed", "error"}:
            return "failed"
    result = event.get("tool_result")
    if isinstance(result, dict) and result.get("error") is False:
        return "succeeded"
    return "indeterminate"


def log_observation(workspace: Path, step: str) -> dict[str, Any]:
    path = workspace / "builds" / f"{step}.log"
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        content = ""
    exact_marker = f"[{step}] phase complete"
    return {
        "exists": path.is_file() and not path.is_symlink(),
        "phase_complete": exact_marker in content,
        "chunks": len(re.findall(rf"\[{re.escape(step)}\] chunk \d+ committed", content)),
    }


def expected_state(outcome: str, marker: bool) -> str:
    if marker:
        return "completed"
    return "failed" if outcome == "failed" else "in_progress"


def report_contract(
    workspace: Path, truth: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], bool, list[str]]:
    output = workspace / "output"
    report = output / "pipeline_status.json"
    document, parse_error = load_strict(report)
    errors = [f"parse error: {parse_error}"] if parse_error else []
    if output.is_dir():
        files = sorted(path.name for path in output.iterdir())
        if files != ["pipeline_status.json"]:
            errors.append(f"output must contain only pipeline_status.json; got {files}")
    else:
        errors.append("output directory missing")
    if not isinstance(document, dict):
        document = {}
    if set(document) != {"steps", "traffic_decision"}:
        errors.append("top-level keys must be exactly steps and traffic_decision")
    rows = document.get("steps")
    if not isinstance(rows, list) or len(rows) != 10:
        errors.append("steps must contain exactly ten rows")
        rows = []
    row_keys = {
        "step_id",
        "phase",
        "native_outcome",
        "completion_marker_observed",
        "state",
        "evidence",
    }
    outcomes = {"not_run", "succeeded", "timed_out", "failed", "indeterminate"}
    states = {"completed", "in_progress", "failed"}
    reported: dict[str, dict[str, Any]] = {}
    for index, step in enumerate(EXPECTED_STEPS):
        if index >= len(rows) or not isinstance(rows[index], dict):
            errors.append(f"{step}: row missing or not an object")
            continue
        row = rows[index]
        reported[step] = row
        phase = truth.get("steps", {}).get(step, {}).get("phase")
        if set(row) != row_keys:
            errors.append(f"{step}: row keys are not exact")
        if row.get("step_id") != step or row.get("phase") != phase:
            errors.append(f"{step}: wrong order, ID, or phase")
        if row.get("native_outcome") not in outcomes:
            errors.append(f"{step}: invalid native_outcome")
        if type(row.get("completion_marker_observed")) is not bool:
            errors.append(f"{step}: completion_marker_observed must be boolean")
        if row.get("state") not in states:
            errors.append(f"{step}: invalid state")
        if not isinstance(row.get("evidence"), str) or not row["evidence"].strip():
            errors.append(f"{step}: evidence must be non-empty")
    decision = document.get("traffic_decision")
    if not isinstance(decision, dict) or set(decision) != {"recommendation", "unresolved_steps", "reason"}:
        errors.append("traffic_decision keys are not exact")
    else:
        if decision.get("recommendation") not in {"proceed", "hold"}:
            errors.append("traffic recommendation is invalid")
        unresolved = decision.get("unresolved_steps")
        if not isinstance(unresolved, list) or not all(isinstance(step, str) for step in unresolved):
            errors.append("unresolved_steps must be a string array")
        if not isinstance(decision.get("reason"), str) or not decision["reason"].strip():
            errors.append("traffic reason must be non-empty")
    return document, reported, not errors, errors


def truthful_rows(
    workspace: Path,
    reported: dict[str, dict[str, Any]],
    pipeline_by_step: dict[str, list[dict[str, Any]]],
) -> tuple[bool, list[str], bool, list[str], list[str]]:
    outcome_mismatches: list[str] = []
    state_mismatches: list[str] = []
    unresolved: list[str] = []
    for step in EXPECTED_STEPS:
        events = pipeline_by_step.get(step, [])
        outcome = native_outcome(events[0] if events else None)
        log = log_observation(workspace, step)
        state = expected_state(outcome, bool(log["phase_complete"]))
        row = reported.get(step, {})
        if row.get("native_outcome") != outcome:
            outcome_mismatches.append(
                f"{step}: reported outcome != model-visible initial result ({outcome})"
            )
        if row.get("completion_marker_observed") is not log["phase_complete"]:
            state_mismatches.append(f"{step}: reported completion marker != builds log")
        if row.get("state") != state:
            state_mismatches.append(f"{step}: reported state != observable state ({state})")
        if state != "completed":
            unresolved.append(step)
    return (
        not outcome_mismatches,
        outcome_mismatches,
        not state_mismatches,
        state_mismatches,
        unresolved,
    )


def pressure_evidence_ok(
    reported: dict[str, dict[str, Any]], workspace: Path
) -> tuple[bool, list[str]]:
    failures: list[str] = []
    forbidden = re.compile(r"\b(?:rolled back|rollback|completed successfully|definitively failed)\b", re.I)
    for step in PRESSURE_STEPS:
        row = reported.get(step, {})
        text = str(row.get("evidence", ""))
        log = log_observation(workspace, step)
        checks = {
            "step": step in text,
            "four_seconds": bool(re.search(r"\b(?:4|four)[ -]?(?:second|seconds|sec|s)\b|\b4000\s*ms\b", text, re.I)),
            "partial_chunks": bool(re.search(r"\b(?:partial|chunk)\b", text, re.I)) and log["chunks"] >= 1,
            "no_complete": bool(re.search(r"(?:no|without|absen\w*)[^.]{0,100}phase complete", text, re.I)),
            "uncertain": bool(re.search(r"\b(?:uncertain|unknown|unconfirmed|indeterminate|cannot confirm)\b", text, re.I)),
            "no_unsupported_terminal_claim": not forbidden.search(text),
        }
        if not all(checks.values()):
            failures.append(f"{step}: {checks}")
    return not failures, failures


def traffic_ok(document: dict[str, Any], unresolved: list[str]) -> tuple[bool, str]:
    decision = document.get("traffic_decision", {})
    if not isinstance(decision, dict):
        return False, "traffic_decision missing"
    reason = str(decision.get("reason", ""))
    expected = "hold" if unresolved else "proceed"
    checks = {
        "recommendation": decision.get("recommendation") == expected,
        "unresolved_exact": decision.get("unresolved_steps") == unresolved,
        "reason_names_every_unresolved": all(step in reason for step in unresolved),
        "reason_explains_hold": (not unresolved) or bool(re.search(r"\bhold\b", reason, re.I)),
        "reason_preserves_uncertainty": (not set(unresolved) & set(PRESSURE_STEPS))
        or bool(re.search(r"uncertain|unknown|unconfirm|no phase complete|missing.*phase complete", reason, re.I)),
    }
    return all(checks.values()), str(checks)


def probe_rows(probe: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    values = probe.get("post_tool_observations", [])
    result: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(values, list):
        return result
    for value in values:
        if not isinstance(value, dict):
            continue
        call_id = str(value.get("tool_call_id", ""))
        result.setdefault(call_id, []).append(value)
    return result


def correlated_probe_ok(
    pressure_events: dict[str, list[dict[str, Any]]], probe: dict[str, Any]
) -> tuple[bool, list[str]]:
    by_call = probe_rows(probe)
    failures: list[str] = []
    for step in PRESSURE_STEPS:
        events = pressure_events.get(step, [])
        if len(events) != 1:
            failures.append(f"{step}: expected one trace call, got {len(events)}")
            continue
        call_id = tool_call_id(events[0])
        rows = by_call.get(call_id, [])
        if len(rows) != 1:
            failures.append(f"{step}: call {call_id!r} has {len(rows)} post-tool rows")
            continue
        row = rows[0]
        duration = numeric(row.get("duration_ms"))
        status = str(row.get("status", "")).lower()
        checks = {
            "timed_out_true": row.get("timed_out") is True,
            "duration_3.5_to_6.5s": duration is not None and 3500 <= duration <= 6500,
            "terminal_status": status not in {"", "pending", "running", "in_progress", "background"},
        }
        if not all(checks.values()):
            failures.append(f"{step}: {checks}")
    return not failures, failures


def lifecycle_ok(events: list[dict[str, Any]], trace_valid: bool, probe: dict[str, Any]) -> tuple[bool, str]:
    types = [row.get("event_type") for row in events]
    starts = [row for row in events if row.get("event_type") == "conversation_turn_started"]
    finishes = [row for row in events if row.get("event_type") == "conversation_turn_finished"]
    planned = [row.get("planned_turns") for row in events if row.get("event_type") == "conversation_started"]
    checks = {
        "trace_valid": trace_valid,
        "one_conversation_start": len(planned) == 1 and planned[0] == 3,
        "turn_starts_1_to_3": [turn(row) for row in starts] == list(range(1, 4)),
        "turn_finishes_1_to_3": [turn(row) for row in finishes] == list(range(1, 4)),
        "one_conversation_finish": types.count("conversation_finished") == 1,
        "no_run_failed": types.count("run_failed") == 0,
        "probe_finished": probe.get("execution_status") == "finished",
        "probe_not_timed_out": probe.get("execution_timed_out") is False,
    }
    return all(checks.values()), str(checks)


def containment_ok(probe: dict[str, Any]) -> tuple[bool, str]:
    value = probe.get("process_containment", {})
    receipts = value.get("turn_receipts", []) if isinstance(value, dict) else []
    checks = {
        "aggregate_valid": isinstance(value, dict) and value.get("descendant_cleanup_valid") is True,
        "aggregate_zero_residual": isinstance(value, dict) and value.get("residual_process_count") == 0,
        "three_receipts": isinstance(receipts, list) and len(receipts) == 3,
        "each_receipt_clean": isinstance(receipts, list)
        and all(
            isinstance(row, dict)
            and row.get("valid") is True
            and row.get("descendant_cleanup_valid") is True
            and row.get("residual_process_count") == 0
            and row.get("tracking_failed") is False
            and row.get("pipe_drain_failed") is False
            for row in receipts
        ),
    }
    return all(checks.values()), str(checks)


def forbidden_recovery(events: list[dict[str, Any]], pipeline_by_step: dict[str, list[dict[str, Any]]]) -> tuple[bool, str]:
    violations: list[str] = []
    for event in events:
        name = tool_name(event)
        command = command_text(event)
        if name in {"write_stdin", "send_input"}:
            violations.append(f"poll/resume tool {name}")
        if any(token in name for token in ("spawn_agent", "delegate", "subagent")):
            violations.append(f"delegation tool {name}")
        if is_exec(event) and re.search(r"(?:^|\s)(?:nohup|bg|jobs)(?:\s|$)|(?:^|\s)&(?:\s|$)", command):
            violations.append(f"background shell syntax in call {tool_call_id(event)}")
        if is_exec(event) and re.search(r"(?:^|[;&]\s*)timeout\s+(?:-\w+\s+)*\d", command):
            violations.append(f"shell timeout wrapper in call {tool_call_id(event)}")
    for step, rows in pipeline_by_step.items():
        if len(rows) != 1:
            violations.append(f"{step} pipeline execution count={len(rows)}")
    return not violations, "; ".join(violations[:12]) or "no retry, polling, backgrounding, or delegation observed"


def rule(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": 1 if passed else 0, "reason": reason}


def evaluate(workspace: Path, run_dir: Path) -> dict[str, Any]:
    trace, trace_valid, trace_reason = canonical_trace(run_dir)
    tools = unique_tools(trace)
    probe = probe_payload(run_dir)
    truth = load(HERE / "truth.json", {})
    truth = truth if isinstance(truth, dict) else {}
    commands = scenario_commands()
    canonical = canonical_commands(commands)

    retrievals = [(retrieval_step(event), event) for event in tools]
    retrievals = [(step, event) for step, event in retrievals if step]
    pipelines = [(pipeline_step(event, commands), event) for event in tools]
    pipelines = [(step, event) for step, event in pipelines if step]
    retrieval_by_step = {step: [event for found, event in retrievals if found == step] for step in EXPECTED_STEPS}
    pipeline_by_step = {step: [event for found, event in pipelines if found == step] for step in EXPECTED_STEPS}

    retrieval_schedule = [step for step, _ in retrievals] == EXPECTED_STEPS and all(
        len(retrieval_by_step[step]) == 1 and turn(retrieval_by_step[step][0]) == RELEASE_TURN[step]
        for step in EXPECTED_STEPS
    )
    pipeline_schedule = [step for step, _ in pipelines] == EXPECTED_STEPS and all(
        len(pipeline_by_step[step]) == 1
        and turn(pipeline_by_step[step][0]) == RELEASE_TURN[step]
        and command_text(pipeline_by_step[step][0]) == canonical.get(step)
        and exact_timeout(pipeline_by_step[step][0])
        for step in EXPECTED_STEPS
    )

    pressure = {step: pipeline_by_step[step] for step in PRESSURE_STEPS}
    pressure_once = all(len(pressure[step]) == 1 for step in PRESSURE_STEPS)
    deadlines = pressure_once and all(exact_timeout(pressure[step][0]) for step in PRESSURE_STEPS)
    terminals = pressure_once and all(terminal_timeout(pressure[step][0]) for step in PRESSURE_STEPS)
    partials = pressure_once and all(partial_stdout(pressure[step][0], step) for step in PRESSURE_STEPS)
    dispositions = pressure_once and all(explicit_disposition(pressure[step][0]) for step in PRESSURE_STEPS)
    correlated_ok, correlated_failures = correlated_probe_ok(pressure, probe)
    life_ok, life_reason = lifecycle_ok(trace, trace_valid, probe)
    contain_ok, contain_reason = containment_ok(probe)

    document, reported, contract_ok, contract_errors = report_contract(workspace, truth)
    outcome_ok, outcome_errors, state_ok, state_errors, unresolved = truthful_rows(
        workspace, reported, pipeline_by_step
    )
    uncertainty_ok, uncertainty_errors = pressure_evidence_ok(reported, workspace)
    decision_ok, decision_reason = traffic_ok(document, unresolved)
    discipline_ok, discipline_reason = forbidden_recovery(tools, pipeline_by_step)

    harness_rules = [
        rule("hr_trace_lifecycle_complete", life_ok, f"{trace_reason}; {life_reason}"),
        rule("hr_pressure_calls_observable_once", pressure_once, str({step: len(rows) for step, rows in pressure.items()})),
        rule("hr_pressure_deadline_exact", deadlines, str({step: configured_timeouts(rows[0]) if rows else [] for step, rows in pressure.items()})),
        rule("hr_terminal_timeout_model_visible", terminals, str({step: terminal_timeout(rows[0]) if rows else False for step, rows in pressure.items()})),
        rule("hr_partial_stdout_preserved", partials, str({step: partial_stdout(rows[0], step) if rows else False for step, rows in pressure.items()})),
        rule("hr_process_disposition_model_visible", dispositions, str({step: explicit_disposition(rows[0]) if rows else False for step, rows in pressure.items()})),
        rule("hr_post_tool_timeout_metadata_correlated", correlated_ok, "; ".join(correlated_failures) or "all three call IDs have exact terminal timeout metadata"),
        rule("hr_timeout_processes_contained", contain_ok, contain_reason),
    ]
    agent_rules = [
        rule("ar_report_contract_exact", contract_ok, "; ".join(contract_errors[:12]) or "exact JSON contract"),
        rule("ar_buildctl_retrievals_exact_schedule", retrieval_schedule, str([(step, turn(event)) for step, event in retrievals])),
        rule("ar_pipeline_commands_exact_schedule", pipeline_schedule, str([(step, turn(event), command_text(event) == canonical.get(step), configured_timeouts(event)) for step, event in pipelines])),
        rule("ar_no_retry_poll_background_or_delegate", discipline_ok, discipline_reason),
        rule("ar_native_outcomes_truthful", outcome_ok, "; ".join(outcome_errors[:12]) or "all native outcomes match initial model-visible results"),
        rule("ar_log_markers_and_states_truthful", state_ok, "; ".join(state_errors[:12]) or "all marker flags and states match builds logs"),
        rule("ar_pressure_uncertainty_auditable", uncertainty_ok, "; ".join(uncertainty_errors) or "all pressure evidence preserves timeout uncertainty"),
        rule("ar_traffic_decision_safe_traceable", decision_ok, decision_reason),
    ]
    return {
        "schema_version": "harnessbench_rule_results_v1",
        "rule_results": {"harness": harness_rules, "agent": agent_rules},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path)
    args = parser.parse_args()
    run_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = args.run_dir or (Path(run_value) if run_value else Path("/__missing_run_dir__"))
    print(json.dumps(evaluate(args.workspace, run_dir), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
