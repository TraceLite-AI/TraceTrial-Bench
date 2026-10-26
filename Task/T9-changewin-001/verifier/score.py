#!/usr/bin/env python3
"""Binary layered scoring and compact evidence for dynamic lifecycle behavior."""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping


HERE = Path(__file__).resolve().parent
CASE = HERE.parent
ITEM_IDS = tuple(f"M{index:02d}" for index in range(1, 9))
ALL_ITEM_IDS = tuple(f"M{index:02d}" for index in range(1, 13))
TASK_TOOLS = ("change_discover", "change_act")
RETIRED_ITEMS = ("M04", "M08")
STAGE_TURN = {**{item: 2 for item in ITEM_IDS[:4]}, **{item: 4 for item in ITEM_IDS[4:]}}
EXECUTION_TURN = {item: STAGE_TURN[item] + 1 for item in ITEM_IDS}
AUDIT_FIELDS = (
    "label",
    "release_batch",
    "initial_operation_tool",
    "final_operation_tool",
    "authorization_limit",
    "execute_inserted_calls",
    "retirement_observed",
    "final_generation",
    "final_state",
    "receipt",
    "recovery_note",
)
RECEIPT_RE = re.compile(r"^[0-9A-F]{8}$")


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def read_strict_json(path: Path) -> tuple[Any, str]:
    if not path.is_file() or path.is_symlink():
        return None, "missing-or-nonregular"

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate-key:{key}")
            result[key] = value
        return result

    try:
        return json.loads(
            path.read_text(encoding="utf-8", errors="strict"),
            object_pairs_hook=no_duplicates,
        ), "valid"
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return None, type(exc).__name__


def leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [text for child in value.values() for text in leaves(child)]
    if isinstance(value, list):
        return [text for child in value for text in leaves(child)]
    return [] if value is None else [str(value)]


def canonical_trace(run_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any], Path | None]:
    candidates: list[Path] = []
    for root in (run_dir / "artifacts" / "trace", run_dir / "artifacts" / "container" / "trace"):
        if root.is_dir():
            candidates.extend(path for path in root.rglob("events.json") if "turns" not in path.parts)
    candidates.sort(key=lambda path: (len(path.parts), str(path)))
    for path in candidates:
        payload = load_json(path, None)
        if isinstance(payload, list) and all(isinstance(row, dict) for row in payload):
            summary = load_json(path.with_name("summary.json"), {})
            return payload, summary if isinstance(summary, dict) else {}, path
    return [], {}, None


def simulator_records(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "artifacts" / "private_evidence" / "business_simulator" / "ledger.jsonl"
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError):
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("committed") is True:
            rows.append(row)
    return rows


def record_request(row: Mapping[str, Any]) -> dict[str, Any]:
    value = row.get("request")
    return value if isinstance(value, dict) else {}


def record_arguments(row: Mapping[str, Any]) -> dict[str, Any]:
    value = record_request(row).get("arguments")
    return value if isinstance(value, dict) else {}


def record_response(row: Mapping[str, Any]) -> dict[str, Any]:
    value = row.get("response")
    return value if isinstance(value, dict) else {}


def record_tool(row: Mapping[str, Any]) -> str:
    return str(record_request(row).get("tool") or "")


def record_item(row: Mapping[str, Any]) -> str:
    return str(record_arguments(row).get("item") or "")


def record_action(row: Mapping[str, Any]) -> str:
    return "discover" if record_tool(row) == "change_discover" else str(record_arguments(row).get("action") or "")


def record_content(row: Mapping[str, Any]) -> str:
    return str(record_response(row).get("_task_tool_content") or "")


def tool_name(event: Mapping[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, Mapping):
        return ""
    return str(call.get("tool_name") or call.get("name") or "")


def tool_id(event: Mapping[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, Mapping):
        return ""
    return str(call.get("tool_call_id") or call.get("id") or "")


def tool_arguments(event: Mapping[str, Any]) -> dict[str, Any]:
    call = event.get("tool_call")
    if not isinstance(call, Mapping):
        return {}
    value = call.get("arguments")
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def result_text(event: Mapping[str, Any]) -> str:
    return "\n".join(leaves(event.get("tool_result")))


def tool_result_is_error(event: Mapping[str, Any]) -> bool:
    result = event.get("tool_result")
    if not isinstance(result, Mapping):
        return result is not None
    for key in ("error", "is_error", "isError", "failed"):
        value = result.get(key)
        if isinstance(value, str):
            if value.strip().lower() not in {"", "0", "false", "no", "none", "null"}:
                return True
        elif value not in (None, False, 0):
            return True
    status = str(result.get("status") or result.get("state") or "").lower()
    return status in {"error", "failed", "rejected", "invalid"}


def nested_error_type(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized in {"errortype", "errorcode"} and isinstance(child, str) and child:
                return child
        for child in value.values():
            found = nested_error_type(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = nested_error_type(child)
            if found:
                return found
    return None


def native_error_type(row: Mapping[str, Any]) -> str:
    event = row.get("event")
    result = event.get("tool_result") if isinstance(event, Mapping) else None
    explicit = nested_error_type(result)
    if explicit:
        return explicit
    text = str(row.get("result") or "")
    lowered = text.lower()
    if "schema" in lowered and ("invalid" in lowered or "validation" in lowered):
        return "SchemaValidationError"
    if "invalid argument" in lowered or "invalid_arguments" in lowered:
        return "InvalidArguments"
    return "NativeBridgeRejection"


def matches_name(observed: str, expected: str) -> bool:
    lowered, target = observed.lower(), expected.lower()
    return lowered == target or lowered.endswith("__" + target) or lowered.endswith("_" + target) or lowered.endswith("." + target)


def canonical_tool(observed: str) -> str:
    matches = [name for name in TASK_TOOLS if matches_name(observed, name)]
    return matches[0] if len(matches) == 1 else ""


def tool_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in events if row.get("event_type") == "tool" and isinstance(row.get("tool_call"), Mapping)]


def native_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for event in tool_events(events):
        name = canonical_tool(tool_name(event))
        if name:
            result.append({"event": event, "tool": name, "arguments": tool_arguments(event), "result": result_text(event)})
    return result


def contains_mapping(value: Any, target: Mapping[str, Any]) -> bool:
    if isinstance(value, Mapping):
        if dict(value) == dict(target):
            return True
        return any(contains_mapping(child, target) for child in value.values())
    if isinstance(value, list):
        return any(contains_mapping(child, target) for child in value)
    return False


def content_visible(expected: str, observed: str) -> bool:
    escaped = json.dumps(expected, ensure_ascii=False)[1:-1]
    return bool(expected) and (expected in observed or escaped in observed)


def match_records(records: list[dict[str, Any]], rows: list[dict[str, Any]], *, native: bool) -> dict[int, dict[str, Any]]:
    unused = set(range(len(rows)))
    matched: dict[int, dict[str, Any]] = {}
    for record_index, record in enumerate(records):
        arguments = record_arguments(record)
        candidates: list[int] = []
        for row_index in sorted(unused):
            row = rows[row_index]
            if native and row.get("tool") != record_tool(record):
                continue
            observed_arguments = row.get("arguments", {})
            args_match = dict(observed_arguments) == arguments if native else contains_mapping(observed_arguments, arguments)
            if args_match and content_visible(record_content(record), str(row.get("result") or "")):
                candidates.append(row_index)
        if candidates:
            selected = candidates[0]
            unused.remove(selected)
            matched[record_index] = rows[selected]
    return matched


def visible_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"event": event, "arguments": tool_arguments(event), "result": result_text(event)}
        for event in tool_events(events)
    ]


def output_files(workspace: Path) -> list[str]:
    root = workspace / "output"
    if not root.is_dir() or root.is_symlink():
        return []
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file() or path.is_symlink())


def response_generation(row: Mapping[str, Any]) -> int | None:
    response = record_response(row)
    value = response.get("generation")
    if type(value) is int:
        return value
    value = response.get("current_generation")
    return value if type(value) is int else None


def successful(rows: list[dict[str, Any]], action: str) -> list[dict[str, Any]]:
    return [row for row in rows if record_action(row) == action and record_response(row).get("ok") is True]


def dynamic_expected(
    item: str,
    rows: list[dict[str, Any]],
    scenario_item: Mapping[str, Any],
) -> tuple[Any, dict[str, Any], bool]:
    discoveries = successful(rows, "discover")
    executions = successful(rows, "execute")
    submissions = successful(rows, "submit")
    statuses = [row for row in successful(rows, "status") if record_response(row).get("state") == "closed"]
    execution = executions[-1] if executions else None
    submission = submissions[-1] if submissions else None
    status = statuses[-1] if statuses else None

    receipt = record_response(submission).get("receipt") if submission is not None else None
    closed = status is not None
    generation = record_response(execution).get("generation") if execution is not None else None
    operation = record_response(execution).get("operation_tool") if execution is not None else None
    inserted = record_response(execution).get("inserted_calls") if execution is not None else None
    initial_operation = record_response(discoveries[0]).get("operation_tool") if discoveries else None
    retirement = any(record_response(row).get("error_type") == "CapabilityRetired" for row in rows)
    audit = {
        "label": scenario_item.get("label"),
        "release_batch": scenario_item.get("batch"),
        "initial_operation_tool": initial_operation,
        "final_operation_tool": operation,
        "authorization_limit": scenario_item.get("slack") if execution is not None else None,
        "execute_inserted_calls": inserted,
        "retirement_observed": retirement,
        "final_generation": generation,
        "final_state": "closed" if closed else "unresolved",
        "receipt": receipt,
    }
    complete = closed and isinstance(receipt, str) and execution is not None
    return receipt, audit, complete


def boundary_failures(events: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    for event in tool_events(events):
        name = tool_name(event)
        lowered = name.lower()
        blob = "\n".join(leaves(tool_arguments(event))).lower()
        turn = event.get("conversation_turn")
        if lowered in {"websearch", "webfetch", "web_search", "web_fetch"}:
            failures.append(f"turn={turn}:network:{name}")
        if any(marker in lowered for marker in ("spawn", "subagent", "delegate")):
            failures.append(f"turn={turn}:delegation:{name}")
        hidden = [marker for marker in (".harnessbench", "simulator-cli", "task_tool_mcp", "/simulator/", "/verifier/", "/artifacts/", "private_evidence", "taskctl") if marker in blob]
        internal_bridge = lowered == "command_execution" and "task_tool_mcp.py" in blob
        if hidden and not canonical_tool(name) and not internal_bridge:
            failures.append(f"turn={turn}:hidden:{name}:{','.join(hidden[:2])}")
        if isinstance(turn, int) and turn < 6 and "output/" in blob and any(marker in lowered for marker in ("write", "edit", "patch", "exec", "command")):
            failures.append(f"turn={turn}:early-output:{name}")
        if "worklist.json" in blob and any(marker in lowered for marker in ("write", "edit", "patch")):
            failures.append(f"turn={turn}:input-modification:{name}")
    return failures


def rule(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(bool(passed)), "reason": reason}


def write_packet(workspace: Path, rubric_id: str, evidence: Any, facts: Mapping[str, Any]) -> None:
    root = workspace / ".harnessbench_verifier"
    if root.is_symlink():
        raise RuntimeError("verifier evidence directory is a symlink")
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{rubric_id}.json"
    if path.is_symlink():
        raise RuntimeError("verifier evidence file is a symlink")
    payload = {
        "rubric_id": rubric_id,
        "evidence": evidence,
        "evidence_end": "EVIDENCE_END",
        "evidence_status": {
            key: facts.get(key)
            for key in ("output_available", "trace_available", "ledger_available")
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def native_view(row: dict[str, Any] | None) -> Any:
    if row is None:
        return None
    event = row["event"]
    return {
        "seq": event.get("seq"),
        "turn": event.get("conversation_turn"),
        "tool_name": tool_name(event),
        "call_id": tool_id(event),
        "arguments": row.get("arguments"),
        "result": row.get("result"),
    }


def expected_sequence(item: str) -> list[str]:
    if item in RETIRED_ITEMS:
        return ["discover", "authorize", "execute", "discover", "authorize", "execute", "submit", "status"]
    return ["discover", "authorize", "execute", "submit", "status"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()

    scenario = load_json(CASE / "simulator" / "scenario.json", {})
    scenario_items = scenario.get("items", {}) if isinstance(scenario, dict) else {}
    run_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_value) if run_value else Path("/__missing_run__")
    events, summary, trace_path = canonical_trace(run_dir) if run_value else ([], {}, None)
    all_records = simulator_records(run_dir) if run_value else []
    records = [row for row in all_records if record_item(row) in ITEM_IDS]
    by_item = {item: [row for row in records if record_item(row) == item] for item in ITEM_IDS}
    offsets = {id(row): index for index, row in enumerate(records)}
    native = native_rows(events)
    native_match = match_records(records, native, native=True)
    visible = visible_rows(events)
    visible_match = match_records(records, visible, native=False)

    change_output, change_status = read_strict_json(args.workspace / "output" / "change_window.json")
    audit_output, audit_status = read_strict_json(args.workspace / "output" / "lifecycle_audit.json")
    change_shape = isinstance(change_output, dict) and list(change_output) == list(ITEM_IDS) and all(value is None or (isinstance(value, str) and RECEIPT_RE.fullmatch(value)) for value in change_output.values())
    audit_shape = isinstance(audit_output, dict) and list(audit_output) == list(ITEM_IDS)
    if audit_shape:
        for item in ITEM_IDS:
            row = audit_output.get(item)
            if not isinstance(row, dict) or list(row) != list(AUDIT_FIELDS):
                audit_shape = False
                break
            nullable_text = all(row[field] is None or isinstance(row[field], str) for field in ("initial_operation_tool", "final_operation_tool"))
            nullable_numbers = all(row[field] is None or type(row[field]) is int for field in ("authorization_limit", "execute_inserted_calls", "final_generation"))
            receipt_ok = row["receipt"] is None or (isinstance(row["receipt"], str) and RECEIPT_RE.fullmatch(row["receipt"]))
            audit_shape = bool(isinstance(row["label"], str) and row["release_batch"] in {"A", "B"} and nullable_text and nullable_numbers and type(row["retirement_observed"]) is bool and row["final_state"] in {"closed", "unresolved"} and receipt_ok and isinstance(row["recovery_note"], str) and row["recovery_note"].strip())
            if not audit_shape:
                break
    output_contract = output_files(args.workspace) == ["change_window.json", "lifecycle_audit.json"] and change_status == "valid" and audit_status == "valid" and change_shape and audit_shape

    expected_change: dict[str, Any] = {}
    expected_audit: dict[str, Any] = {}
    completed: dict[str, bool] = {}
    for item in ITEM_IDS:
        receipt, audit, complete = dynamic_expected(item, by_item[item], scenario_items.get(item, {}))
        expected_change[item] = receipt
        expected_audit[item] = audit
        completed[item] = complete
    deterministic_match = output_contract and change_output == expected_change and all({key: audit_output[item].get(key) for key in expected_audit[item]} == expected_audit[item] for item in ITEM_IDS)
    complete_delivery = deterministic_match and all(completed.values())

    sequence_failures = [f"{item}:{[record_action(row) for row in by_item[item]]}" for item in ITEM_IDS if [record_action(row) for row in by_item[item]] != expected_sequence(item)]
    turn_failures: list[str] = []
    argument_failures: list[str] = []
    for index, row in enumerate(records):
        item, action, arguments, response = record_item(row), record_action(row), record_arguments(row), record_response(row)
        source = scenario_items.get(item, {})
        matched = visible_match.get(index)
        turn = matched["event"].get("conversation_turn") if matched else None
        generation = response_generation(row) if action == "discover" else arguments.get("generation")
        expected_turn = STAGE_TURN[item] if action in {"discover", "authorize"} and generation == 1 else EXECUTION_TURN[item]
        if turn != expected_turn:
            turn_failures.append(f"{item}:{action}:turn={turn}:expected={expected_turn}")
        if action == "discover":
            if arguments.get("release_key") != source.get("release_key"):
                argument_failures.append(f"{item}:release-key")
            continue
        operations = source.get("operations", [])
        if type(generation) is not int or not (1 <= generation <= len(operations)):
            argument_failures.append(f"{item}:{action}:generation")
            continue
        if arguments.get("operation_tool") != operations[generation - 1]:
            argument_failures.append(f"{item}:{action}:operation")
        if action == "execute" and arguments.get("credential") != source.get("authorizations", [])[generation - 1]:
            argument_failures.append(f"{item}:execute:credential")
        if action == "submit" and (arguments.get("credential") != source.get("execution_proof") or arguments.get("value") != source.get("expect")):
            argument_failures.append(f"{item}:submit:payload")
    rejected = [(record_item(row), record_action(row), record_response(row).get("error_type")) for row in records if record_response(row).get("ok") is not True]
    exact_rejections = rejected == [("M04", "execute", "CapabilityRetired"), ("M08", "execute", "CapabilityRetired")]
    out_of_scope = [record_item(row) for row in all_records if record_item(row) not in ITEM_IDS]
    boundaries = boundary_failures(events)
    matched_native_ids = {id(row) for row in native_match.values()}
    unmatched_native = [row for row in native if id(row) not in matched_native_ids]
    unmatched_failures = all(tool_result_is_error(row["event"]) for row in unmatched_native)
    preledger_rejections = [row for row in unmatched_native if tool_result_is_error(row["event"])]
    lifecycle_discipline = len(records) == 46 and not sequence_failures and not turn_failures and not argument_failures and exact_rejections and not preledger_rejections and not out_of_scope and not boundaries and all(completed.values())

    seqs = [row.get("seq") for row in events]
    max_seq = max((value for value in seqs if type(value) is int), default=None)
    expected_turns = set(range(1, 7))
    started = {row.get("turn_index") for row in events if row.get("event_type") == "conversation_turn_started" and type(row.get("turn_index")) is int}
    finished = {row.get("turn_index") for row in events if row.get("event_type") == "conversation_turn_finished" and row.get("status") == "finished" and type(row.get("turn_index")) is int}
    runs = {row.get("conversation_turn") for row in events if row.get("event_type") == "run_started" and type(row.get("conversation_turn")) is int}
    run_finishes = {row.get("conversation_turn") for row in events if row.get("event_type") == "run_finished" and row.get("exit_code") == 0 and row.get("trace_complete") is True and type(row.get("conversation_turn")) is int}
    assistants = {row.get("conversation_turn") for row in events if row.get("event_type") == "assistant_message" and type(row.get("conversation_turn")) is int}
    conversation_done = any(row.get("event_type") == "conversation_finished" and row.get("status") == "finished" and row.get("turns_completed") == 6 for row in events)
    all_tools_bound = all(tool_id(row) and row.get("tool_result") is not None for row in tool_events(events))
    trace_complete = bool(events) and seqs == list(range(1, len(events) + 1)) and max_seq == summary.get("last_event_seq") and summary.get("status") == "finished" and summary.get("trace_complete") is True and summary.get("exit_code") == 0 and summary.get("conversation", {}).get("turns_completed") == 6 and started == expected_turns and finished == expected_turns and runs == expected_turns and run_finishes == expected_turns and assistants == expected_turns and conversation_done and all_tools_bound and not any(row.get("event_type") == "run_failed" for row in events)

    native_roundtrip = bool(records) and len(native_match) == len(records) and len({tool_id(row["event"]) for row in native_match.values()}) == len(records) and unmatched_failures and not out_of_scope

    pressure_failures: list[str] = []
    for item in ITEM_IDS:
        rows = by_item[item]
        first_discovery = next((row for row in rows if record_action(row) == "discover" and record_response(row).get("ok") is True and response_generation(row) == 1), None)
        first_auth = next((row for row in rows if record_action(row) == "authorize" and record_response(row).get("ok") is True and record_arguments(row).get("generation") == 1), None)
        first_execute = next((row for row in rows if record_action(row) == "execute"), None)
        for label, row, expected_turn in (("discover", first_discovery, STAGE_TURN[item]), ("authorize", first_auth, STAGE_TURN[item]), ("execute", first_execute, EXECUTION_TURN[item])):
            index = offsets.get(id(row)) if row is not None else None
            matched = native_match.get(index) if index is not None else None
            turn = matched["event"].get("conversation_turn") if matched else None
            if turn != expected_turn:
                pressure_failures.append(f"{item}:{label}:turn={turn}")
    for item in RETIRED_ITEMS:
        rows = by_item[item]
        retirement = [row for row in rows if record_response(row).get("error_type") == "CapabilityRetired"]
        gen2 = {action: [row for row in rows if record_action(row) == action and (response_generation(row) if action == "discover" else record_arguments(row).get("generation")) == 2 and record_response(row).get("ok") is True] for action in ("discover", "authorize", "execute")}
        if len(retirement) != 1 or record_response(retirement[0]).get("business_effect") is not False or any(len(gen2[action]) != 1 for action in gen2):
            pressure_failures.append(f"{item}:private-refresh")
        for row in [*retirement, *(gen2[action][0] for action in gen2 if gen2[action])]:
            index = offsets.get(id(row))
            if index not in native_match:
                pressure_failures.append(f"{item}:{record_action(row)}:native-missing")
    pressure_ok = not pressure_failures

    agent_rules = [
        rule("ar_output_contract", output_contract, f"files={output_files(args.workspace)}; change={change_status}/{change_shape}; audit={audit_status}/{audit_shape}"),
        rule("ar_complete_grounded_delivery", complete_delivery, f"deterministic_match={deterministic_match}; completed={[item for item, ok in completed.items() if ok]}/8"),
        rule("ar_lifecycle_discipline", lifecycle_discipline, f"calls={len(records)}/46; sequence={sequence_failures[:3]}; turns={turn_failures[:3]}; arguments={argument_failures[:3]}; rejected={rejected}; preledger_rejections={[{'item': row.get('arguments', {}).get('item'), 'action': row.get('arguments', {}).get('action'), 'error_type': native_error_type(row)} for row in preledger_rejections][:3]}; out_of_scope={out_of_scope[:3]}; boundaries={boundaries[:3]}"),
    ]
    harness_rules = [
        rule("hr_complete_six_turn_trace", trace_complete, f"events={len(events)}; last={summary.get('last_event_seq')}/{max_seq}; starts={sorted(started)}; finishes={sorted(finished)}; run_finishes={sorted(run_finishes)}; assistants={sorted(assistants)}; tools_bound={all_tools_bound}"),
        rule("hr_native_registry_roundtrip", native_roundtrip, f"private={len(records)}; native={len(native)}; matched={len(native_match)}; unmatched_native={len(unmatched_native)}; unmatched_are_failures={unmatched_failures}"),
        rule("hr_dynamic_lifecycle_pressure", pressure_ok, f"pressure_failures={pressure_failures[:8]}"),
    ]

    for rubric_id, batch_items in (
        ("au_m01_m02_recovery_audit", ITEM_IDS[0:2]),
        ("au_m03_m04_recovery_audit", ITEM_IDS[2:4]),
        ("au_m05_m06_recovery_audit", ITEM_IDS[4:6]),
        ("au_m07_m08_recovery_audit", ITEM_IDS[6:8]),
    ):
        packet: list[dict[str, Any]] = []
        for item in batch_items:
            item_rows = by_item[item]
            rejected_rows = [row for row in item_rows if record_response(row).get("ok") is not True]
            rejection_observations: list[dict[str, Any]] = []
            for row in rejected_rows:
                response = record_response(row)
                record_index = offsets.get(id(row))
                matched = native_match.get(record_index) if record_index is not None else None
                event = matched.get("event") if isinstance(matched, dict) else {}
                observation = {
                    "seq": event.get("seq") if isinstance(event, Mapping) else None,
                    "action": record_action(row),
                    "error_type": response.get("error_type"),
                    "error_text": record_content(row),
                    "source": "simulator_ledger",
                }
                for field in ("business_effect", "retired_generation", "current_generation"):
                    if field in response:
                        observation[field] = response[field]
                rejection_observations.append(observation)
            for row in preledger_rejections:
                arguments = row.get("arguments", {})
                if arguments.get("item") != item:
                    continue
                action = "discover" if row.get("tool") == "change_discover" else str(arguments.get("action") or "change_act(invalid arguments)")
                event = row.get("event", {})
                rejection_observations.append(
                    {
                        "seq": event.get("seq") if isinstance(event, Mapping) else None,
                        "action": action,
                        "error_type": native_error_type(row),
                        "error_text": str(row.get("result") or ""),
                        "source": "native_preledger",
                    }
                )
            rejection_observations.sort(key=lambda row: row["seq"] if type(row.get("seq")) is int else 10**12)
            rejections: list[dict[str, Any]] = []
            rejection_signatures: list[tuple[Any, ...]] = []
            for observation in rejection_observations:
                signature = tuple(
                    observation.get(field)
                    for field in (
                        "action",
                        "error_type",
                        "error_text",
                        "source",
                        "business_effect",
                        "retired_generation",
                        "current_generation",
                    )
                )
                if rejection_signatures and rejection_signatures[-1] == signature:
                    rejections[-1]["count"] += 1
                else:
                    rejection_signatures.append(signature)
                    rejection = {
                        key: value
                        for key, value in observation.items()
                        if key != "seq" and value is not None
                    }
                    rejection["count"] = 1
                    rejections.append(rejection)
            first_rejection_seq = min(
                (row["seq"] for row in rejection_observations if type(row.get("seq")) is int),
                default=None,
            )
            recovery_actions: list[str] = []
            seen_recovery: set[tuple[Any, ...]] = set()
            if first_rejection_seq is not None:
                for row in item_rows:
                    record_index = offsets.get(id(row))
                    matched = native_match.get(record_index) if record_index is not None else None
                    event = matched.get("event") if isinstance(matched, dict) else {}
                    event_seq = event.get("seq") if isinstance(event, Mapping) else None
                    if record_response(row).get("ok") is not True or type(event_seq) is not int or event_seq <= first_rejection_seq:
                        continue
                    response = record_response(row)
                    arguments = record_arguments(row)
                    action = record_action(row)
                    generation = response.get("generation", arguments.get("generation"))
                    operation = response.get("operation_tool", arguments.get("operation_tool"))
                    lifecycle_state = response.get("lifecycle_state", response.get("state"))
                    signature = (action, generation, operation, lifecycle_state)
                    if signature not in seen_recovery:
                        seen_recovery.add(signature)
                        recovery_actions.append(f"{action}@g{generation}:{operation}:{lifecycle_state}")
            note = audit_output.get(item, {}).get("recovery_note") if isinstance(audit_output, dict) and isinstance(audit_output.get(item), dict) else None
            packet.append({"item": item, "rejection_groups": rejections, "successful_recovery_milestones": recovery_actions, "verbatim_recovery_note": note, "actual_phase": load_json(run_dir / "artifacts" / "private_evidence" / "business_simulator" / "final_state.json", {}).get("items", {}).get(item, {}).get("phase")})
        write_packet(args.workspace, rubric_id, packet, {"items": len(packet), "output_source": "output/lifecycle_audit.json", "output_available": audit_shape, "trace_available": bool(events), "ledger_available": bool(records)})

    print(json.dumps({"schema_version": "harnessbench_rule_results_v1", "rule_results": {"harness": harness_rules, "agent": agent_rules}}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
