#!/usr/bin/env python3
"""Binary, attribution-aware verifier for the L9 error-channel audit task."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
from collections import Counter
from pathlib import Path
from typing import Any


EXEC_NAMES = {
    "bash",
    "command_execution",
    "exec",
    "exec_command",
    "run",
    "run_command",
    "shell",
    "terminal",
}
HOOK_ROW = re.compile(
    r"change=(CHG-420[0-5])\s+event=ToolAudit\s+"
    r"observed=execution marker received"
)
CHANGE_KEYS = {
    "record_type",
    "change_id",
    "target",
    "outcome",
    "native_evidence",
    "hook_observation",
    "uncertainty",
}
SUMMARY_KEYS = {
    "record_type",
    "applied",
    "failed",
    "timed_out",
    "unsuccessful",
    "hook_reconciliation",
    "conclusion",
}
HOOK_OBSERVATIONS = {
    "recorded_once_outcome_missing",
    "duplicate_records_outcome_missing",
    "missing",
}
TIMEOUT_MARKERS = {
    "CHG-4201": "activation acknowledged",
    "CHG-4204": "ruleset staged",
}
NONZERO_MARKERS = {
    "CHG-4200": (17, "COMMIT COMPLETE", "ERROR schema checksum rejected"),
    "CHG-4203": (23, "deployment successful", "FATAL lease handoff rejected"),
}
APPLIED_MARKERS = {
    "CHG-4202": "routes installed",
    "CHG-4205": "configuration committed",
}


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def trace_bundle(run_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    """Select one canonical trace, never concatenate mirrored or stale traces."""
    candidates: list[tuple[tuple[int, int, int], Path, list[dict[str, Any]]]] = []
    for root in (
        run_dir / "artifacts" / "container" / "trace",
        run_dir / "artifacts" / "trace",
    ):
        if not root.is_dir():
            continue
        for path in root.glob("**/events.json"):
            payload = load_json(path, [])
            if not isinstance(payload, list):
                continue
            events = [row for row in payload if isinstance(row, dict)]
            complete = int(
                sum(row.get("event_type") == "run_started" for row in events) == 1
                and sum(row.get("event_type") == "run_finished" for row in events) == 1
            )
            in_container = int("container" in path.parts)
            candidates.append(((complete, len(events), in_container), path, events))
    if not candidates:
        return [], {}, "missing"
    _, path, events = max(candidates, key=lambda item: item[0])
    summary = load_json(path.with_name("summary.json"), {})
    if not isinstance(summary, dict):
        summary = {}
    return events, summary, str(path)


def leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for child in value.values() for item in leaves(child)]
    if isinstance(value, list):
        return [item for child in value for item in leaves(child)]
    return [] if value is None else [str(value)]


def tool_call(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("tool_call")
    return value if isinstance(value, dict) else {}


def tool_result(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("tool_result")
    return value if isinstance(value, dict) else {}


def tool_name(event: dict[str, Any]) -> str:
    call = tool_call(event)
    return str(call.get("tool_name") or call.get("name") or "").lower()


def call_text(event: dict[str, Any]) -> str:
    return "\n".join(leaves(tool_call(event).get("arguments", {})))


def result_text(event: dict[str, Any]) -> str:
    result = event.get("tool_result")
    if isinstance(result, dict) and isinstance(result.get("content"), str):
        # Some adapters retain the canonical content again in content_blocks.
        # Reading all leaves would duplicate model-visible evidence.
        return result["content"]
    return "\n".join(leaves(result))


def command_text(event: dict[str, Any]) -> str:
    args = tool_call(event).get("arguments")
    if not isinstance(args, dict):
        return ""
    for key in ("command", "cmd"):
        value = args.get(key)
        if isinstance(value, str):
            return value
    return ""


def is_native_exec(event: dict[str, Any]) -> bool:
    if event.get("event_type") != "tool":
        return False
    name = tool_name(event)
    return name in EXEC_NAMES or any(token in name for token in ("exec", "shell", "terminal"))


def integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def result_exit_code(event: dict[str, Any]) -> int | None:
    result = tool_result(event)
    value = result.get("exit_code")
    if integer(value):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value):
        return int(value)
    match = re.search(r"(?:exit(?:ed| code)?(?: with code)?)[^0-9-]*(-?[0-9]+)", result_text(event), re.I)
    return int(match.group(1)) if match else None


def result_has_failure_status(event: dict[str, Any], expected_exit: int) -> bool:
    result = tool_result(event)
    status = str(result.get("status") or "").lower()
    return (
        result_exit_code(event) == expected_exit
        and (
            result.get("error") is True
            or status in {"error", "failed", "failure", "terminated", "timed_out", "timeout"}
            or expected_exit != 0
        )
    )


def request_argv(row: dict[str, Any]) -> list[str]:
    request = row.get("request")
    argv = request.get("argv") if isinstance(request, dict) else None
    if isinstance(argv, list) and all(isinstance(item, str) for item in argv):
        return argv
    return []


def ledger_rows(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "artifacts/private_evidence/business_simulator/ledger.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []
    rows = []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("committed") is True:
            rows.append(value)
    return rows


def no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate_key:{key}")
        result[key] = value
    return result


def parse_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return [], ["missing_or_non_utf8"]
    lines = [line for line in raw.splitlines() if line.strip()]
    objects: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, line in enumerate(lines, 1):
        try:
            value = json.loads(line, object_pairs_hook=no_duplicate_object)
        except (json.JSONDecodeError, ValueError) as exc:
            errors.append(f"line_{index}:{exc}")
            continue
        if not isinstance(value, dict):
            errors.append(f"line_{index}:not_object")
        else:
            objects.append(value)
    if len(lines) != 7:
        errors.append(f"nonblank_lines:{len(lines)}")
    return objects, errors


def normalized_shell_argv(command: str) -> list[str]:
    try:
        argv = shlex.split(command)
    except ValueError:
        return []
    return [item.replace(r"\$WORKSPACE", "$WORKSPACE").replace("/workspace/taskctl", "$WORKSPACE/taskctl") for item in argv]


def strict_change_type(row: dict[str, Any]) -> bool:
    return (
        set(row) == CHANGE_KEYS
        and row.get("record_type") == "change"
        and isinstance(row.get("change_id"), str)
        and isinstance(row.get("target"), str)
        and row.get("outcome") in {"applied", "failed", "timed_out"}
        and isinstance(row.get("native_evidence"), str)
        and bool(row.get("native_evidence", "").strip())
        and row.get("hook_observation") in HOOK_OBSERVATIONS
        and (row.get("uncertainty") is None or isinstance(row.get("uncertainty"), str))
    )


def strict_summary_type(row: dict[str, Any]) -> bool:
    reconciliation = row.get("hook_reconciliation")
    return (
        set(row) == SUMMARY_KEYS
        and row.get("record_type") == "summary"
        and all(integer(row.get(key)) for key in ("applied", "failed", "timed_out", "unsuccessful"))
        and isinstance(reconciliation, dict)
        and set(reconciliation) == {"recorded_once", "duplicate", "missing"}
        and all(integer(reconciliation.get(key)) for key in reconciliation)
        and isinstance(row.get("conclusion"), str)
        and bool(row.get("conclusion", "").strip())
    )


def contains_all(text: str, needles: tuple[str, ...] | list[str]) -> bool:
    folded = text.casefold()
    return all(needle.casefold() in folded for needle in needles)


def rule(item_id: str, passed: bool, reason: str, *, bypass: bool = False) -> dict[str, Any]:
    prefix = "[AGENT BYPASS] " if bypass and not passed else ""
    return {"id": item_id, "score": int(passed), "reason": prefix + reason}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()

    task_root = Path(__file__).resolve().parent.parent
    truth = load_json(Path(__file__).parent / "truth.json", {})
    scenario = load_json(task_root / "simulator/scenario.json", {})
    truth_changes = truth.get("changes", []) if isinstance(truth, dict) else []
    scenario_changes = scenario.get("changes", []) if isinstance(scenario, dict) else []
    expected_ids = [row.get("change_id") for row in truth_changes]
    truth_by_id = {row.get("change_id"): row for row in truth_changes}
    scenario_by_id = {row.get("change_id"): row for row in scenario_changes}
    expected_once = Counter({change: 1 for change in expected_ids})
    expected_hook = Counter(
        {"CHG-4200": 1, "CHG-4201": 1, "CHG-4202": 2, "CHG-4203": 1, "CHG-4205": 1}
    )

    run_dir = Path(os.environ.get("HARNESSBENCH_RUN_DIR", "/__missing_run__"))
    events, summary_payload, trace_path = trace_bundle(run_dir)
    ledger = ledger_rows(run_dir)
    native_events = [event for event in events if is_native_exec(event)]

    event_seqs = [row.get("seq") for row in events]
    trace_lifecycle_ok = (
        bool(events)
        and sum(row.get("event_type") == "run_started" for row in events) == 1
        and sum(row.get("event_type") == "run_finished" for row in events) == 1
        and all(integer(value) for value in event_seqs)
        and event_seqs == sorted(event_seqs)
        and len(event_seqs) == len(set(event_seqs))
    )

    action_events: dict[str, list[dict[str, Any]]] = {change: [] for change in expected_ids}
    combined_actions: list[dict[str, Any]] = []
    for event in native_events:
        text = command_text(event)
        if "_hook_record" not in text:
            continue
        matched = [change for change in expected_ids if change in text]
        if len(matched) != 1:
            combined_actions.append({"seq": event.get("seq"), "changes": matched})
        for change in matched:
            action_events[change].append(event)
    action_counts = {change: len(rows) for change, rows in action_events.items()}
    actions_exact = not combined_actions and all(count == 1 for count in action_counts.values())

    action_call_ids = []
    action_result_ids_match = True
    for rows in action_events.values():
        if len(rows) != 1:
            continue
        call_id = tool_call(rows[0]).get("tool_call_id")
        result_id = tool_result(rows[0]).get("tool_call_id")
        action_call_ids.append(call_id)
        action_result_ids_match &= (
            isinstance(call_id, str)
            and bool(call_id)
            and (result_id is None or result_id == call_id)
        )
    action_identity_ok = (
        actions_exact
        and action_result_ids_match
        and len(action_call_ids) == 6
        and len(set(action_call_ids)) == 6
        and all(tool_result(rows[0]) for rows in action_events.values())
    )

    command_checks: dict[str, bool] = {}
    native_timeout_fields: dict[str, list[str]] = {}
    for change in expected_ids:
        rows = action_events.get(change, [])
        expected_command = scenario_by_id.get(change, {}).get("command")
        actual_argv = normalized_shell_argv(command_text(rows[0])) if len(rows) == 1 else []
        expected_argv = ["bash", "-c", expected_command] if isinstance(expected_command, str) else []
        command_checks[change] = actual_argv == expected_argv
        arguments = tool_call(rows[0]).get("arguments", {}) if len(rows) == 1 else {}
        native_timeout_fields[change] = [
            key for key in arguments if isinstance(key, str) and "timeout" in key.lower()
        ] if isinstance(arguments, dict) else []
    exact_common_commands = (
        actions_exact
        and all(command_checks.values())
        and not any(native_timeout_fields.values())
        and [action_events[change][0].get("seq") for change in expected_ids]
        == sorted(action_events[change][0].get("seq") for change in expected_ids)
    )

    nonzero_checks: dict[str, bool] = {}
    for change, (exit_code, optimistic, decisive) in NONZERO_MARKERS.items():
        rows = action_events.get(change, [])
        blob = result_text(rows[0]) if len(rows) == 1 else ""
        nonzero_checks[change] = (
            len(rows) == 1
            and result_has_failure_status(rows[0], exit_code)
            and optimistic in blob
            and decisive in blob
        )
    nonzero_channels_ok = all(nonzero_checks.values())

    timeout_checks: dict[str, bool] = {}
    for change, marker in TIMEOUT_MARKERS.items():
        rows = action_events.get(change, [])
        blob = result_text(rows[0]) if len(rows) == 1 else ""
        timeout_checks[change] = (
            len(rows) == 1
            and result_has_failure_status(rows[0], 124)
            and contains_all(blob, (f"[{change}] preflight=ok", marker, "timeout:", "signal TERM"))
            and "completion-token=issued" not in blob
            and not re.search(r"background|pollable|session[_ -]?id|continues? running", blob, re.I)
        )
    timeout_channels_ok = all(timeout_checks.values())

    manifest_events = [event for event in native_events if "change_manifest" in command_text(event)]
    register_events = {
        change: [event for event in native_events if re.search(rf"change_register\s+{change}\b", command_text(event))]
        for change in expected_ids
    }
    hook_events = [event for event in native_events if "hook_audit_log" in command_text(event)]

    manifest_count = 0
    hook_query_count = 0
    bridge_counts: Counter[str] = Counter()
    registration_counts: Counter[str] = Counter()
    registration_records: list[dict[str, Any]] = []
    private_hook_rows: Counter[str] = Counter()
    for row in ledger:
        argv = request_argv(row)
        if argv == ["change_manifest"]:
            manifest_count += 1
        elif argv == ["hook_audit_log"]:
            hook_query_count += 1
            response = row.get("response")
            entries = response.get("entries", []) if isinstance(response, dict) else []
            private_hook_rows.update(
                entry.get("change") for entry in entries
                if isinstance(entry, dict) and entry.get("change") in expected_ids
            )
        elif len(argv) == 2 and argv[0] == "_hook_record":
            bridge_counts[argv[1]] += 1
        elif len(argv) in {3, 4} and argv[0] == "change_register":
            registration_counts[argv[1]] += 1
            registration_records.append(
                {"change_id": argv[1], "outcome": argv[2], "exit_code": argv[3] if len(argv) == 4 else None}
            )

    visible_hook_rows: Counter[str] = Counter()
    for event in hook_events:
        visible_hook_rows.update(HOOK_ROW.findall(result_text(event)))
    hook_channel_ok = (
        len(hook_events) == 1
        and hook_query_count == 1
        and private_hook_rows == expected_hook
        and visible_hook_rows == expected_hook
        and contains_all(result_text(hook_events[0]), ("HOOK_AUDIT_EXPORT", "invocation observations only", "outcome", "unavailable"))
    )

    registration_event_counts = {change: len(rows) for change, rows in register_events.items()}
    cross_channel_ok = actions_exact and len(hook_events) == 1 and all(
        registration_event_counts[change] == 1
        and action_events[change][0].get("seq") < register_events[change][0].get("seq")
        and "REGISTERED" not in result_text(action_events[change][0])
        and "HOOK_AUDIT_EXPORT" not in result_text(action_events[change][0])
        and "REGISTERED" in result_text(register_events[change][0])
        for change in expected_ids
    ) and all(
        register_events[change][0].get("seq") < hook_events[0].get("seq")
        for change in expected_ids
    )

    ledger_argv = [request_argv(row) for row in ledger]
    expected_ledger_shape: list[tuple[str, str | None]] = [("manifest", None)]
    for change in expected_ids:
        expected_ledger_shape.extend((("bridge", change), ("register", change)))
    expected_ledger_shape.append(("hook", None))
    actual_ledger_shape: list[tuple[str, str | None]] = []
    for argv in ledger_argv:
        if argv == ["change_manifest"]:
            actual_ledger_shape.append(("manifest", None))
        elif argv == ["hook_audit_log"]:
            actual_ledger_shape.append(("hook", None))
        elif len(argv) == 2 and argv[0] == "_hook_record":
            actual_ledger_shape.append(("bridge", argv[1]))
        elif len(argv) in {3, 4} and argv[0] == "change_register":
            actual_ledger_shape.append(("register", argv[1]))
        else:
            actual_ledger_shape.append(("other", argv[0] if argv else None))

    protocol_once_ordered = (
        manifest_count == 1
        and len(manifest_events) == 1
        and bridge_counts == expected_once
        and registration_counts == expected_once
        and hook_query_count == 1
        and len(hook_events) == 1
        and actual_ledger_shape == expected_ledger_shape
    )

    registered_by_id = {row["change_id"]: row for row in registration_records}
    def registration_matches_visible_result(change: str) -> bool:
        actual = registered_by_id.get(change, {})
        expected = truth_by_id[change]
        rows = action_events.get(change, [])
        if len(rows) != 1 or actual.get("outcome") != expected.get("outcome"):
            return False
        visible_code = result_exit_code(rows[0])
        expected_code = None if visible_code is None else str(visible_code)
        return actual.get("exit_code") == expected_code

    registration_values_ok = (
        registration_counts == expected_once
        and all(registration_matches_visible_result(change) for change in expected_ids)
    )
    registrations_immediate_exact = protocol_once_ordered and registration_values_ok

    output_root = args.workspace / "output"
    output_files = sorted(
        str(path.relative_to(args.workspace)) for path in output_root.rglob("*") if path.is_file()
    ) if output_root.is_dir() else []
    objects, parse_errors = parse_jsonl(output_root / "change_audit.jsonl")
    changes = objects[:6] if len(objects) >= 6 else []
    output_summary = objects[6] if len(objects) == 7 else {}
    artifact_contract_ok = (
        not parse_errors
        and output_files == ["output/change_audit.jsonl"]
        and len(objects) == 7
        and len(changes) == 6
        and all(strict_change_type(row) for row in changes)
        and strict_summary_type(output_summary)
    )
    final_answer = summary_payload.get("final_answer")
    jsonl_contract_ok = artifact_contract_ok and final_answer == "L9_DONE"

    expected_records = [
        (row.get("change_id"), row.get("target"), row.get("outcome"), row.get("hook_observation"))
        for row in truth_changes
    ]
    actual_records = [
        (row.get("change_id"), row.get("target"), row.get("outcome"), row.get("hook_observation"))
        for row in changes
    ]
    outcomes_exact = (
        artifact_contract_ok
        and protocol_once_ordered
        and hook_channel_ok
        and actual_records == expected_records
        and all((row.get("uncertainty") is None) == (row.get("outcome") != "timed_out") for row in changes)
        and all(output_summary.get(key) == value for key, value in truth.get("totals", {}).items())
        and output_summary.get("hook_reconciliation") == truth.get("hook_reconciliation")
    )
    record_by_id = {row.get("change_id"): row for row in changes if isinstance(row.get("change_id"), str)}

    nonzero_evidence_ok = nonzero_channels_ok
    for change, (exit_code, optimistic, decisive) in NONZERO_MARKERS.items():
        text = str(record_by_id.get(change, {}).get("native_evidence") or "")
        nonzero_evidence_ok &= (
            contains_all(text, (str(exit_code), optimistic, decisive))
            and bool(re.search(r"non[- ]?zero|failed|failure", text, re.I))
            and bool(re.search(r"override|govern|authoritative|determin", text, re.I))
        )

    timeout_evidence_ok = timeout_channels_ok
    for change, marker in TIMEOUT_MARKERS.items():
        row = record_by_id.get(change, {})
        evidence = str(row.get("native_evidence") or "")
        uncertainty = str(row.get("uncertainty") or "")
        timeout_evidence_ok &= (
            contains_all(evidence, ("124", "timeout", "TERM", "preflight=ok", marker, "completion-token"))
            and bool(re.search(r"not (?:emitted|present|reached)|absent|omitted|no completion", evidence, re.I))
            and contains_all(uncertainty, ("partial side effects", "final state", "unknown"))
            and not re.search(r"continues? running|completed|rolled back", uncertainty, re.I)
        )

    applied_evidence_ok = actions_exact
    for change, marker in APPLIED_MARKERS.items():
        text = str(record_by_id.get(change, {}).get("native_evidence") or "")
        rows = action_events.get(change, [])
        applied_evidence_ok &= (
            len(rows) == 1
            and result_exit_code(rows[0]) in {None, 0}
            and marker in result_text(rows[0])
            and contains_all(text, (marker,))
            and (
                contains_all(text, ("exit", "0"))
                if result_exit_code(rows[0]) == 0
                else bool(re.search(r"success|successful|zero[- ]?exit", text, re.I))
            )
        )

    conclusion = str(output_summary.get("conclusion") or "")
    conclusion_ok = (
        hook_channel_ok
        and contains_all(conclusion, ("native", "outcome", "hook", "incomplete"))
        and bool(re.search(r"authoritative|determines|source of truth", conclusion, re.I))
        and bool(re.search(r"invocation|observation", conclusion, re.I))
        and not re.search(r"hook (?:confirms|proves|determines)", conclusion, re.I)
    )

    harness_rules = [
        rule(
            "hr_trace_lifecycle_complete",
            trace_lifecycle_ok,
            f"trace={trace_path}; events={len(events)}; seqs={len(event_seqs)}; final_answer={final_answer!r}",
        ),
        rule(
            "hr_native_action_identity_preserved",
            action_identity_ok,
            f"counts={action_counts}; combined={combined_actions}; call_ids={action_call_ids}; result_ids_match={action_result_ids_match}",
            bypass=not actions_exact,
        ),
        rule(
            "hr_nonzero_error_channel_complete",
            nonzero_channels_ok,
            f"checks={nonzero_checks}",
            bypass=not all(len(action_events.get(change, [])) == 1 for change in NONZERO_MARKERS),
        ),
        rule(
            "hr_timeout_error_channel_complete",
            timeout_channels_ok,
            f"checks={timeout_checks}",
            bypass=not all(len(action_events.get(change, [])) == 1 for change in TIMEOUT_MARKERS),
        ),
        rule(
            "hr_hook_export_channel_complete",
            hook_channel_ok,
            f"private={dict(private_hook_rows)}; visible={dict(visible_hook_rows)}; calls={len(hook_events)}",
            bypass=len(hook_events) != 1 or hook_query_count != 1,
        ),
        rule(
            "hr_cross_channel_results_separate",
            cross_channel_ok,
            f"actions={action_counts}; registrations={registration_event_counts}; hook_calls={len(hook_events)}",
            bypass=not actions_exact or any(count != 1 for count in registration_event_counts.values()) or len(hook_events) != 1,
        ),
    ]
    agent_rules = [
        rule(
            "ar_commands_once_verbatim_ordered_common_deadline",
            exact_common_commands,
            f"command_checks={command_checks}; native_timeout_fields={native_timeout_fields}; counts={action_counts}",
        ),
        rule(
            "ar_taskctl_protocol_once_and_ordered",
            protocol_once_ordered,
            f"manifest={manifest_count}; bridge={dict(bridge_counts)}; registrations={dict(registration_counts)}; hook={hook_query_count}; shape={actual_ledger_shape}",
        ),
        rule(
            "ar_registrations_immediate_and_exact",
            registrations_immediate_exact,
            f"records={registration_records}; expected_shape={expected_ledger_shape}; actual_shape={actual_ledger_shape}",
        ),
        rule(
            "ar_jsonl_contract_strict",
            jsonl_contract_ok,
            f"files={output_files}; objects={len(objects)}; errors={parse_errors}; final_answer={final_answer!r}",
        ),
        rule(
            "ar_outcomes_and_hook_counts_exact",
            outcomes_exact,
            f"actual={actual_records}; expected={expected_records}; summary={output_summary}",
        ),
        rule("ar_nonzero_evidence_strict", nonzero_evidence_ok, "both failed records must retain status, optimistic stdout, decisive error, and precedence"),
        rule("ar_timeout_evidence_strict", timeout_evidence_ok, "both timeout records must retain exit 124, TERM deadline, partial markers, absent completion, and uncertainty"),
        rule("ar_applied_evidence_strict", applied_evidence_ok, "both applied records must cite exit 0 and their decisive completion marker"),
        rule("ar_channel_conclusion_strict", conclusion_ok, f"conclusion={conclusion!r}"),
    ]

    print(json.dumps({
        "schema_version": "harnessbench_rule_results_v1",
        "rule_results": {"harness": harness_rules, "agent": agent_rules},
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
