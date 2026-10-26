#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA = "harnessbench_verifier_goal_revision_v4"
REPORT_PATH = "output/mount_report.json"
CHECKPOINT_PATH = "state/.revision_checkpoints.json"
ISSUED_PATH = "state/.issued_details.json"
LOG_PATH = "state/mountctl_log.jsonl"
CHECKPOINT_TURNS = {
    "scope_verified": 3,
    "add_operator": 5,
    "interruption_preserved": 6,
    "replace_verified_at": 7,
}
NOTE_LINES = (
    "Final scope: verified mounts only",
    "Final fields: mount, capacity_kib, verified_at",
    "Revision count: 3",
    "Revision 1: scope changed from all mounts to verified mounts only",
    "Revision 2: operator added",
    "Revision 3: operator revoked and replaced with verified_at",
)
MOUNTCTL = re.compile(
    r"(?<![A-Za-z0-9_.-])(?:(?:/workspace|\$WORKSPACE)/|\./)?bin/mountctl\s+"
    r"(list|fetch|checkpoint|audit)\b"
    r"(?:\s+([^\s;&|\"']+))?"
    r"(?:\s+([^\s;&|\"']+))?",
    re.I,
)


class DuplicateKey(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKey(key)
        result[key] = value
    return result


def load_json(path: Path, default: Any, *, strict: bool = False) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object if strict else None,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, DuplicateKey):
        return default


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            row = json.loads(line, object_pairs_hook=_unique_object)
        except (json.JSONDecodeError, DuplicateKey):
            return []
        if not isinstance(row, dict):
            return []
        rows.append(row)
    return rows


def string_leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        result: list[str] = []
        for child in value.values():
            result.extend(string_leaves(child))
        return result
    if isinstance(value, list):
        result = []
        for child in value:
            result.extend(string_leaves(child))
        return result
    return []


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def json_objects(text: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder(object_pairs_hook=_unique_object)
    objects: list[dict[str, Any]] = []
    offset = 0
    while offset < len(text):
        start = text.find("{", offset)
        if start < 0:
            break
        try:
            value, consumed = decoder.raw_decode(text[start:])
        except (json.JSONDecodeError, DuplicateKey):
            offset = start + 1
            continue
        if isinstance(value, dict):
            objects.append(value)
        offset = start + max(consumed, 1)
    return objects


def primary_trace(
    run_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any], Path | None, int]:
    candidates: list[tuple[list[dict[str, Any]], dict[str, Any], Path]] = []
    for root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if not root.is_dir():
            continue
        for path in root.glob("**/events.json"):
            if "turns" in path.parts:
                continue
            payload = load_json(path, None)
            summary = load_json(path.with_name("summary.json"), None)
            if (
                isinstance(payload, list)
                and all(isinstance(item, dict) for item in payload)
                and isinstance(summary, dict)
            ):
                candidates.append((payload, summary, path))
    canonical_candidates = [
        item
        for item in candidates
        if item[0]
        and item[0][0].get("event_type") == "conversation_started"
        and any(event.get("event_type") == "conversation_turn_started" for event in item[0])
    ]
    if len(canonical_candidates) != 1:
        return [], {}, None, len(canonical_candidates)
    events, summary, path = canonical_candidates[0]
    return events, summary, path, 1


def tool_name(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return ""
    return str(call.get("tool_name", call.get("name", "")))


def tool_arguments(event: dict[str, Any]) -> Any:
    call = event.get("tool_call")
    return call.get("arguments") if isinstance(call, dict) else None


def command_text(event: dict[str, Any]) -> str:
    arguments = tool_arguments(event)
    if isinstance(arguments, dict):
        for key in ("command", "cmd", "script"):
            value = arguments.get(key)
            if isinstance(value, str):
                return value
        argv = arguments.get("argv")
        if isinstance(argv, list) and all(isinstance(item, str) for item in argv):
            return " ".join(argv)
    if isinstance(arguments, str):
        return arguments
    if isinstance(arguments, list) and all(isinstance(item, str) for item in arguments):
        return " ".join(arguments)
    return ""


def executable_shell_text(value: str) -> str:
    """Remove shell comments and heredoc bodies before command matching."""

    result: list[str] = []
    heredoc_delimiter: str | None = None
    for raw_line in value.splitlines():
        if heredoc_delimiter is not None:
            if raw_line.strip() == heredoc_delimiter:
                heredoc_delimiter = None
            continue
        line: list[str] = []
        single_quoted = False
        double_quoted = False
        escaped = False
        for index, character in enumerate(raw_line):
            if escaped:
                line.append(character)
                escaped = False
                continue
            if character == "\\" and not single_quoted:
                line.append(character)
                escaped = True
                continue
            if character == "'" and not double_quoted:
                single_quoted = not single_quoted
                line.append(character)
                continue
            if character == '"' and not single_quoted:
                double_quoted = not double_quoted
                line.append(character)
                continue
            if (
                character == "#"
                and not single_quoted
                and not double_quoted
                and (index == 0 or raw_line[index - 1].isspace())
            ):
                break
            line.append(character)
        executable = "".join(line)
        result.append(executable)
        heredoc = re.search(
            r"<<-?\s*(?:'([^']+)'|\"([^\"]+)\"|([A-Za-z_][A-Za-z0-9_]*))",
            executable,
        )
        if heredoc:
            heredoc_delimiter = next(group for group in heredoc.groups() if group is not None)
    return "\n".join(result)


def tool_result_text(event: dict[str, Any]) -> str:
    result = event.get("tool_result")
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, str):
            return content
    return "\n".join(string_leaves(result))


def tool_id(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return ""
    return str(call.get("tool_call_id", call.get("id", "")))


def mountctl_operations(event: dict[str, Any]) -> list[dict[str, Any]]:
    if event.get("event_type") != "tool":
        return []
    operations: list[dict[str, Any]] = []
    for match in MOUNTCTL.finditer(executable_shell_text(command_text(event))):
        op = match.group(1).lower()
        first = (match.group(2) or "").strip()
        second = (match.group(3) or "").strip()
        operations.append(
            {
                "op": op,
                "target": first.upper() if op == "fetch" else None,
                "label": first if op == "checkpoint" else None,
                "report": second if op == "checkpoint" else None,
                "turn": event.get("conversation_turn"),
                "seq": event.get("seq"),
                "tool_call_id": tool_id(event),
                "event": event,
            }
        )
    return operations


def model_input_text(event: dict[str, Any]) -> str:
    messages = event.get("messages")
    leaves = string_leaves(messages)
    if not leaves:
        leaves = string_leaves(event.get("raw_event"))
    return "\n\u241e\n".join(leaves)


def ordered_contains(text: str, values: list[str]) -> bool:
    cursor = 0
    for value in values:
        index = text.find(value, cursor)
        if index < 0:
            return False
        cursor = index + len(value)
    return True


def checkpoint_record(
    checkpoints: dict[str, Any], label: str
) -> tuple[dict[str, Any], bool]:
    value = checkpoints.get(label)
    record = value if isinstance(value, dict) else {}
    snapshot = record.get("snapshot")
    valid = (
        set(record) == {"report", "sha256", "recorded_at", "snapshot"}
        and record.get("report") == REPORT_PATH
        and isinstance(record.get("recorded_at"), str)
        and bool(record.get("recorded_at"))
        and isinstance(snapshot, dict)
        and record.get("sha256") == sha256_json(snapshot)
    )
    return record, valid


def exact_snapshot(
    snapshot: Any,
    *,
    verified: set[str],
    source: dict[str, Any],
    issued: dict[str, Any],
    controlled_field: str | None,
) -> bool:
    if not isinstance(snapshot, dict) or set(snapshot) != verified:
        return False
    expected_fields = {"mount", "capacity_kib"}
    if controlled_field is not None:
        expected_fields.add(controlled_field)
    for mount in verified:
        row = snapshot.get(mount)
        source_row = source.get(mount)
        issued_row = issued.get(mount)
        if not isinstance(row, dict) or set(row) != expected_fields:
            return False
        if not isinstance(source_row, dict) or row.get("mount") != mount:
            return False
        if row.get("capacity_kib") != source_row.get("capacity_kib"):
            return False
        if controlled_field is not None:
            if not isinstance(issued_row, dict):
                return False
            expected = issued_row.get(controlled_field)
            if not isinstance(expected, str) or not expected or row.get(controlled_field) != expected:
                return False
    return True


def boundary_violations(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for event in tools:
        command = command_text(event)
        lowered = command.lower()
        reasons: list[str] = []
        if any(marker in lowered for marker in (ISSUED_PATH.lower(), CHECKPOINT_PATH.lower())):
            reasons.append("hidden_state_access")
        if re.search(
            r"\b(cat|sed|head|tail|less|more|strings|xxd|od|cp|mv|rm|chmod|chown)\b"
            r"[^\n;&|]*bin/mountctl\b",
            lowered,
        ):
            reasons.append("mountctl_inspection_or_mutation")
        if (
            "data/mounts.json" in lowered
            and (
                re.search(r"(?:>|>>|tee\s+)[^\n;&|]*data/mounts\.json", lowered)
                or re.search(r"\b(sed\s+-i|cp|mv|rm)\b[^\n;&|]*data/mounts\.json", lowered)
            )
        ):
            reasons.append("source_data_mutation")
        if reasons:
            findings.append(
                {
                    "seq": event.get("seq"),
                    "turn": event.get("conversation_turn"),
                    "tool": tool_name(event),
                    "tool_call_id": tool_id(event),
                    "reasons": reasons,
                }
            )
    return findings


def write_evidence(workspace: Path, rule_id: str, payload: dict[str, Any]) -> None:
    root = workspace / ".harnessbench_verifier"
    root.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": SCHEMA, "rule_id": rule_id, **payload}
    (root / f"{rule_id}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def rule(rule_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": rule_id, "score": int(passed), "reason": reason}


def evaluate(workspace: Path, run_dir: Path | None) -> dict[str, Any]:
    verifier_dir = Path(__file__).resolve().parent
    truth = load_json(verifier_dir / "truth.json", {})
    source = load_json(workspace / "data" / "mounts.json", {})
    issued = load_json(workspace / ISSUED_PATH, {})
    checkpoints = load_json(workspace / CHECKPOINT_PATH, {})
    channel_log = load_jsonl(workspace / LOG_PATH)
    turns_payload = load_json(verifier_dir.parent / "turns.json", {})
    expected_turns = turns_payload.get("turns", []) if isinstance(turns_payload, dict) else []
    verified = set(truth.get("verified", []))
    degraded = set(truth.get("degraded", []))

    events, summary, events_path, canonical_trace_count = (
        primary_trace(run_dir) if run_dir is not None else ([], {}, None, 0)
    )
    tools = [event for event in events if event.get("event_type") == "tool"]
    operations = [op for event in tools for op in mountctl_operations(event)]
    boundary = boundary_violations(tools)

    # Harness: canonical lifecycle and native continuity.
    seqs = [event.get("seq") for event in events]
    sequence_ok = (
        bool(seqs)
        and all(type(value) is int for value in seqs)
        and seqs == list(range(seqs[0], seqs[0] + len(seqs)))
    )
    started: dict[int, list[str]] = {}
    finished: list[int] = []
    for event in events:
        if event.get("event_type") == "conversation_turn_started":
            index = event.get("turn_index")
            value = event.get("input")
            if type(index) is int and isinstance(value, str):
                started.setdefault(index, []).append(value)
        if event.get("event_type") == "conversation_turn_finished":
            index = event.get("turn_index")
            if type(index) is int:
                finished.append(index)
    exact_turns = (
        len(expected_turns) == 10
        and all(started.get(index) == [expected_turns[index - 1]] for index in range(1, 11))
    )
    lifecycle_ok = (
        canonical_trace_count == 1
        and bool(events)
        and events[0].get("event_type") == "conversation_started"
        and events[-1].get("event_type") == "run_finished"
        and sequence_ok
        and summary.get("trace_complete") is True
        and summary.get("status") == "finished"
        and exact_turns
        and finished == list(range(1, 11))
        and not any(event.get("event_type") == "run_failed" for event in events)
    )
    conversation = summary.get("conversation")
    conversation = conversation if isinstance(conversation, dict) else {}
    native_ids = conversation.get("native_session_ids")
    boundary_events = [
        event
        for event in events
        if event.get("event_type") in {"session_boundary", "conversation_session_boundary"}
    ]
    continuity_ok = (
        conversation.get("turns_completed") == 10
        and isinstance(native_ids, list)
        and len(native_ids) == 1
        and isinstance(native_ids[0], str)
        and bool(native_ids[0])
        and conversation.get("new_session_before_turns") == []
        and not boundary_events
    )

    # Harness: exact model-visible revision and interruption messages.
    model_inputs: dict[int, list[str]] = {}
    for event in events:
        if event.get("event_type") != "model_input":
            continue
        turn = event.get("conversation_turn")
        if type(turn) is int:
            model_inputs.setdefault(turn, []).append(model_input_text(event))

    message_metric_specs = (
        ("hr_turn3_scope_revision_model_input_complete", 3),
        ("hr_turn5_operator_revision_model_input_complete", 5),
        ("hr_turn6_interruption_model_input_complete", 6),
        ("hr_turn7_replacement_revision_model_input_complete", 7),
    )
    message_rules: list[dict[str, Any]] = []
    for rule_id, turn in message_metric_specs:
        expected = expected_turns[turn - 1] if len(expected_turns) == 10 else ""
        exposed = [text for text in model_inputs.get(turn, []) if expected and expected in text]
        passed = bool(exposed)
        reason = (
            f"turn_started_exact={started.get(turn) == [expected]}; "
            f"model_inputs={len(model_inputs.get(turn, []))}; exact_user_message={passed}"
        )
        message_rules.append(rule(rule_id, passed, reason))
        write_evidence(
            workspace,
            rule_id,
            {
                "passed": passed,
                "turn": turn,
                "turn_started_exact": started.get(turn) == [expected],
                "model_input_count": len(model_inputs.get(turn, [])),
                "exact_user_message_present": passed,
            },
        )

    history_messages = [
        expected_turns[index - 1] if len(expected_turns) == 10 else ""
        for index in (3, 5, 6, 7)
    ]
    history_ok = any(
        all(history_messages) and ordered_contains(text, history_messages)
        for text in model_inputs.get(7, [])
    )

    # Private channel actions and bound controlled-detail results.
    channel_fetch_counts = Counter(
        str(row.get("mount", ""))
        for row in channel_log
        if row.get("op") == "fetch"
    )
    fetch_actions_exact = (
        set(channel_fetch_counts) == verified
        and all(channel_fetch_counts[mount] == 1 for mount in verified)
        and not (set(channel_fetch_counts) & degraded)
    )
    fetch_event_map: dict[tuple[Any, str], dict[str, Any]] = {}
    for operation in operations:
        if operation["op"] == "fetch":
            event = operation["event"]
            fetch_event_map[(event.get("seq"), tool_id(event))] = event
    fetch_events = list(fetch_event_map.values())
    visible_rows = [
        row
        for event in fetch_events
        for row in json_objects(tool_result_text(event))
        if set(row) == {"mount", "status", "operator", "verified_at"}
    ]
    visible_fetches: dict[str, bool] = {}
    for mount in sorted(verified):
        issued_row = issued.get(mount) if isinstance(issued, dict) else None
        expected_row = {
            "mount": mount,
            "status": "verified",
            "operator": issued_row.get("operator") if isinstance(issued_row, dict) else None,
            "verified_at": issued_row.get("verified_at") if isinstance(issued_row, dict) else None,
        }
        visible_fetches[mount] = (
            all(isinstance(expected_row[key], str) for key in ("operator", "verified_at"))
            and visible_rows.count(expected_row) == 1
        )
    all_fetches_bound = (
        fetch_actions_exact
        and len(visible_rows) == len(verified)
        and all(visible_fetches.values())
    )
    fetch_harness_bypass = not fetch_actions_exact
    fetch_reason_prefix = "[AGENT BYPASS] " if fetch_harness_bypass else ""
    fetch_reason = (
        fetch_reason_prefix
        + f"channel_fetches={sum(channel_fetch_counts.values())}/8; "
        + f"trace_fetch_events={len(fetch_events)}; visible_payloads={len(visible_rows)}/8; "
        + f"bound_results={sum(visible_fetches.values())}/8"
    )

    # Private checkpoint snapshots and bound model-visible receipts.
    checkpoint_records: dict[str, dict[str, Any]] = {}
    checkpoint_validity: dict[str, bool] = {}
    for label in CHECKPOINT_TURNS:
        record, valid = checkpoint_record(
            checkpoints if isinstance(checkpoints, dict) else {}, label
        )
        checkpoint_records[label] = record
        checkpoint_validity[label] = valid
    checkpoints_private_exact = (
        isinstance(checkpoints, dict)
        and set(checkpoints) == set(CHECKPOINT_TURNS)
        and all(checkpoint_validity.values())
    )
    channel_checkpoint_labels = [
        str(row.get("label"))
        for row in channel_log
        if row.get("op") == "checkpoint"
    ]
    expected_labels = list(CHECKPOINT_TURNS)
    checkpoint_actions_exact = (
        checkpoints_private_exact and channel_checkpoint_labels == expected_labels
    )
    trace_checkpoints: dict[str, list[dict[str, Any]]] = {}
    for operation in operations:
        if operation["op"] == "checkpoint" and isinstance(operation["label"], str):
            trace_checkpoints.setdefault(operation["label"], []).append(operation)
    checkpoint_schedule_agent_error = False
    checkpoint_receipts: dict[str, bool] = {}
    for label, expected_turn in CHECKPOINT_TURNS.items():
        matches = trace_checkpoints.get(label, [])
        if len(matches) > 1 or (
            len(matches) == 1
            and (
                matches[0]["turn"] != expected_turn
                or matches[0]["report"] != REPORT_PATH
            )
        ):
            checkpoint_schedule_agent_error = True
        bound = False
        if (
            len(matches) == 1
            and matches[0]["turn"] == expected_turn
            and matches[0]["report"] == REPORT_PATH
        ):
            record = checkpoint_records[label]
            expected_receipt = {
                "writer": "mountctl-cli",
                "op": "checkpoint",
                "label": label,
                "report": REPORT_PATH,
                "sha256": record.get("sha256"),
                "accepted": True,
            }
            rows = json_objects(tool_result_text(matches[0]["event"]))
            bound = len([row for row in rows if row == expected_receipt]) == 1
        checkpoint_receipts[label] = bound
    checkpoint_harness_bypass = not checkpoint_actions_exact or checkpoint_schedule_agent_error
    all_checkpoints_bound = checkpoint_actions_exact and all(checkpoint_receipts.values())
    checkpoint_reason = (
        ("[AGENT BYPASS] " if checkpoint_harness_bypass else "")
        + f"private_records={sum(checkpoint_validity.values())}/4; "
        + f"channel_labels={channel_checkpoint_labels}; "
        + f"trace_calls={sum(len(value) for value in trace_checkpoints.values())}/4; "
        + f"bound_receipts={sum(checkpoint_receipts.values())}/4"
    )

    harness_rules = [
        rule(
            "hr_trace_ten_turn_lifecycle_complete",
            lifecycle_ok,
            f"canonical_traces={canonical_trace_count}; events={len(events)}; sequence_ok={sequence_ok}; trace_complete={summary.get('trace_complete')}; exact_turns={exact_turns}; finished_turns={len(finished)}/10; events_path={events_path}",
        ),
        rule(
            "hr_one_native_session_carries_all_revisions",
            continuity_ok,
            f"turns_completed={conversation.get('turns_completed')}; native_session_ids={native_ids}; configured_boundaries={conversation.get('new_session_before_turns')}; observed_boundaries={len(boundary_events)}",
        ),
        *message_rules,
        rule(
            "hr_turn7_ordered_goal_history_complete",
            history_ok,
            f"turn7_model_inputs={len(model_inputs.get(7, []))}; exact_turn3_5_6_7_history_ordered={history_ok}",
        ),
        rule(
            "hr_all_controlled_fetch_results_bound_exact",
            all_fetches_bound,
            fetch_reason,
        ),
        rule(
            "hr_all_checkpoint_results_bound_exact",
            all_checkpoints_bound,
            checkpoint_reason,
        ),
    ]

    # Agent: strict private snapshots for every load-bearing goal state.
    scope_snapshot = checkpoint_records["scope_verified"].get("snapshot")
    operator_snapshot = checkpoint_records["add_operator"].get("snapshot")
    interruption_snapshot = checkpoint_records["interruption_preserved"].get("snapshot")
    replacement_snapshot = checkpoint_records["replace_verified_at"].get("snapshot")
    scope_ok = checkpoint_validity["scope_verified"] and exact_snapshot(
        scope_snapshot,
        verified=verified,
        source=source if isinstance(source, dict) else {},
        issued=issued if isinstance(issued, dict) else {},
        controlled_field=None,
    )
    operator_ok = checkpoint_validity["add_operator"] and exact_snapshot(
        operator_snapshot,
        verified=verified,
        source=source if isinstance(source, dict) else {},
        issued=issued if isinstance(issued, dict) else {},
        controlled_field="operator",
    )
    interruption_ok = (
        checkpoint_validity["interruption_preserved"]
        and operator_ok
        and canonical(interruption_snapshot) == canonical(operator_snapshot)
    )
    replacement_ok = checkpoint_validity["replace_verified_at"] and exact_snapshot(
        replacement_snapshot,
        verified=verified,
        source=source if isinstance(source, dict) else {},
        issued=issued if isinstance(issued, dict) else {},
        controlled_field="verified_at",
    )

    # Agent channel schedule, exact turns, and prohibited state/source access.
    list_count = sum(row.get("op") == "list" for row in channel_log)
    audit_count = sum(row.get("op") == "audit" for row in channel_log)
    log_ops = [row.get("op") for row in channel_log]
    trace_lists = [operation for operation in operations if operation["op"] == "list"]
    trace_fetch_turns = [event.get("conversation_turn") for event in fetch_events]
    private_order_ok = (
        list_count == 1
        and audit_count == 0
        and fetch_actions_exact
        and checkpoint_actions_exact
        and len(channel_log) == 13
        and log_ops[0] == "list"
        and set(log_ops) == {"list", "fetch", "checkpoint"}
    )
    retrieval_trace_schedule_ok = (
        len(trace_lists) == 1
        and trace_lists[0]["turn"] == 2
        and bool(fetch_events)
        and all(turn == 4 for turn in trace_fetch_turns)
    )
    checkpoint_trace_schedule_ok = all(
        len(trace_checkpoints.get(label, [])) == 1
        and trace_checkpoints[label][0]["turn"] == turn
        and trace_checkpoints[label][0]["report"] == REPORT_PATH
        for label, turn in CHECKPOINT_TURNS.items()
    )
    approved_schedule_ok = (
        private_order_ok
        and retrieval_trace_schedule_ok
        and checkpoint_trace_schedule_ok
        and not boundary
        and all(operation["op"] != "audit" for operation in operations)
    )

    # Agent final report and exact authoritative values.
    output_dir = workspace / "output"
    output_entries = sorted(path.name for path in output_dir.iterdir()) if output_dir.is_dir() else []
    report_path = workspace / REPORT_PATH
    notes_path = workspace / "output" / "scope_notes.md"
    final_report = load_json(report_path, None, strict=True)
    report_object = final_report if isinstance(final_report, dict) else {}
    final_contract_ok = (
        output_dir.is_dir()
        and not output_dir.is_symlink()
        and output_entries == ["mount_report.json", "scope_notes.md"]
        and report_path.is_file()
        and not report_path.is_symlink()
        and notes_path.is_file()
        and not notes_path.is_symlink()
        and isinstance(final_report, dict)
        and set(report_object) == verified
        and all(
            isinstance(report_object.get(mount), dict)
            and set(report_object[mount]) == {"mount", "capacity_kib", "verified_at"}
            and report_object[mount].get("mount") == mount
            for mount in verified
        )
    )
    final_values_ok = final_contract_ok and not boundary
    for mount in verified:
        row = report_object.get(mount)
        source_row = source.get(mount) if isinstance(source, dict) else None
        issued_row = issued.get(mount) if isinstance(issued, dict) else None
        final_values_ok = final_values_ok and (
            isinstance(row, dict)
            and isinstance(source_row, dict)
            and isinstance(issued_row, dict)
            and row.get("capacity_kib") == source_row.get("capacity_kib")
            and isinstance(issued_row.get("verified_at"), str)
            and row.get("verified_at") == issued_row.get("verified_at")
        )

    try:
        notes = notes_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        notes = ""
    note_positions = [notes.find(line) for line in NOTE_LINES]
    revision_lines = [
        line.strip()
        for line in notes.splitlines()
        if re.fullmatch(r"Revision\s+\d+\s*:.*", line.strip(), re.I)
    ]
    notes_ok = (
        bool(notes)
        and all(notes.count(line) == 1 for line in NOTE_LINES)
        and note_positions == sorted(note_positions)
        and revision_lines == list(NOTE_LINES[3:])
        and not re.search(r"Revision\s+(?:0|[4-9]|\d{2,})\s*:", notes, re.I)
    )

    agent_rules = [
        rule(
            "ar_approved_channel_schedule_exact",
            approved_schedule_ok,
            f"list={list_count}; list_trace_turns={[item['turn'] for item in trace_lists]}; fetches={sum(channel_fetch_counts.values())}/8; fetch_trace_turns={trace_fetch_turns}; checkpoint_labels={channel_checkpoint_labels}; checkpoint_turns={[(label, [item['turn'] for item in trace_checkpoints.get(label, [])]) for label in CHECKPOINT_TURNS]}; audit={audit_count}; boundary_violations={len(boundary)}; log_ops={log_ops}",
        ),
        rule(
            "ar_scope_revision_checkpoint_exact",
            scope_ok,
            f"record_valid={checkpoint_validity['scope_verified']}; exact_verified_base_snapshot={scope_ok}",
        ),
        rule(
            "ar_operator_revision_checkpoint_exact",
            operator_ok,
            f"record_valid={checkpoint_validity['add_operator']}; exact_operator_snapshot={operator_ok}",
        ),
        rule(
            "ar_interruption_preserves_active_goal_checkpoint_exact",
            interruption_ok,
            f"record_valid={checkpoint_validity['interruption_preserved']}; canonical_equal_to_add_operator={interruption_ok}",
        ),
        rule(
            "ar_replacement_revision_checkpoint_exact",
            replacement_ok,
            f"record_valid={checkpoint_validity['replace_verified_at']}; exact_verified_at_snapshot_without_operator={replacement_ok}",
        ),
        rule(
            "ar_final_report_contract_matches_latest_goal",
            final_contract_ok,
            f"output_entries={output_entries}; report_object={isinstance(final_report, dict)}; report_mounts={len(report_object)}/8; exact_schema={final_contract_ok}",
        ),
        rule(
            "ar_final_values_exact_and_authoritative",
            final_values_ok,
            f"authoritative_rows={sum(isinstance(report_object.get(mount), dict) and isinstance(source.get(mount), dict) and isinstance(issued.get(mount), dict) and report_object[mount].get('capacity_kib') == source[mount].get('capacity_kib') and report_object[mount].get('verified_at') == issued[mount].get('verified_at') for mount in verified) if isinstance(source, dict) and isinstance(issued, dict) else 0}/8; hidden_or_boundary_violations={len(boundary)}",
        ),
        rule(
            "ar_scope_notes_exact_latest_goal_and_history",
            notes_ok,
            f"required_lines_once={sum(notes.count(line) == 1 for line in NOTE_LINES)}/6; ordered={note_positions == sorted(note_positions)}; revision_lines={revision_lines}",
        ),
    ]

    evidence_payloads = {
        "hr_trace_ten_turn_lifecycle_complete": {
            "passed": lifecycle_ok,
            "canonical_trace_count": canonical_trace_count,
            "events_path": str(events_path) if events_path else None,
            "sequence_ok": sequence_ok,
            "exact_turns": exact_turns,
            "finished_turns": finished,
        },
        "hr_one_native_session_carries_all_revisions": {
            "passed": continuity_ok,
            "conversation": conversation,
            "observed_boundary_count": len(boundary_events),
        },
        "hr_turn7_ordered_goal_history_complete": {
            "passed": history_ok,
            "turn7_model_input_count": len(model_inputs.get(7, [])),
        },
        "hr_all_controlled_fetch_results_bound_exact": {
            "passed": all_fetches_bound,
            "channel_fetch_counts": dict(channel_fetch_counts),
            "trace_fetch_event_count": len(fetch_events),
            "visible_payload_count": len(visible_rows),
            "bound_results": visible_fetches,
        },
        "hr_all_checkpoint_results_bound_exact": {
            "passed": all_checkpoints_bound,
            "private_validity": checkpoint_validity,
            "channel_labels": channel_checkpoint_labels,
            "trace_call_counts": {key: len(value) for key, value in trace_checkpoints.items()},
            "bound_receipts": checkpoint_receipts,
        },
        "ar_approved_channel_schedule_exact": {
            "passed": approved_schedule_ok,
            "channel_log": channel_log,
            "boundary_violations": boundary,
        },
        "ar_scope_revision_checkpoint_exact": {"passed": scope_ok},
        "ar_operator_revision_checkpoint_exact": {"passed": operator_ok},
        "ar_interruption_preserves_active_goal_checkpoint_exact": {"passed": interruption_ok},
        "ar_replacement_revision_checkpoint_exact": {"passed": replacement_ok},
        "ar_final_report_contract_matches_latest_goal": {
            "passed": final_contract_ok,
            "output_entries": output_entries,
            "report_keys": sorted(report_object),
        },
        "ar_final_values_exact_and_authoritative": {
            "passed": final_values_ok,
            "boundary_violations": boundary,
        },
        "ar_scope_notes_exact_latest_goal_and_history": {
            "passed": notes_ok,
            "required_line_counts": {line: notes.count(line) for line in NOTE_LINES},
            "revision_lines": revision_lines,
        },
    }
    for rule_id, payload in evidence_payloads.items():
        write_evidence(workspace, rule_id, payload)

    return {
        "schema_version": "harnessbench_rule_results_v1",
        "rule_results": {"harness": harness_rules, "agent": agent_rules},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    run_dir_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    payload = evaluate(args.workspace, Path(run_dir_value) if run_dir_value else None)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
