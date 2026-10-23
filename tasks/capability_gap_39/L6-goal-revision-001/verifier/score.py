#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any


SCHEMA = "harnessbench_verifier_goal_revision_v3"
RECON_COMMAND = re.compile(r"(?:^|[\s/])(?:bin/)?recon\s+(?:list|fetch|audit)\b", re.I)
RECON_FETCH = re.compile(r"(?:^|[\s/])(?:bin/)?recon\s+fetch\s+(BL-\d{2})\b", re.I)
REPORT_PATH = "output/recon_report.json"
CRITICAL_REVISION_TURNS = (3, 5, 7)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def private_channel(run_dir: Path) -> tuple[dict[str, dict[str, str]], list[dict[str, Any]], bool]:
    evidence = run_dir / "artifacts" / "private_evidence" / "business_simulator"
    ledger_path = evidence / "ledger.jsonl"
    manifest = load_json(evidence / "release_manifest.json", {})
    try:
        payload = ledger_path.read_bytes()
        lines = payload.decode("utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError):
        return {}, [], False
    valid = bool(
        isinstance(manifest, dict)
        and manifest.get("status") == "complete"
        and manifest.get("record_count") == len(lines)
        and manifest.get("ledger_sha256") == hashlib.sha256(payload).hexdigest()
    )
    record_keys = {
        "_writer",
        "event",
        "op",
        "line",
        "status",
        "settled_at",
        "approver",
    }
    issued: dict[str, dict[str, str]] = {}
    records: list[dict[str, Any]] = []
    for line in lines:
        try:
            ledger_row = json.loads(line)
        except json.JSONDecodeError:
            valid = False
            continue
        if not isinstance(ledger_row, dict):
            valid = False
            continue
        request = ledger_row.get("request")
        argv = request.get("argv") if isinstance(request, dict) else None
        response = ledger_row.get("response")
        record = response.get("record") if isinstance(response, dict) else None
        if record is None:
            continue
        if not (
            ledger_row.get("committed") is True
            and isinstance(argv, list)
            and all(isinstance(value, str) for value in argv)
            and isinstance(record, dict)
            and set(record) == record_keys
            and record.get("_writer") == "recon-private-simulator"
            and record.get("event") == "recon_command"
        ):
            valid = False
            continue
        op = record.get("op")
        command_matches = (
            (op == "list" and argv == ["list"])
            or (op == "audit" and argv == ["audit"])
            or (
                op == "fetch"
                and len(argv) == 2
                and argv[0] == "fetch"
                and record.get("line") == argv[1]
            )
        )
        if not command_matches:
            valid = False
            continue
        records.append(record)
        if op != "fetch" or record.get("status") != "settled":
            continue
        line_id = record.get("line")
        settled_at = record.get("settled_at")
        approver = record.get("approver")
        if not all(isinstance(value, str) and value for value in (line_id, settled_at, approver)):
            valid = False
            continue
        detail = {"settled_at": settled_at, "approver": approver}
        if line_id in issued and issued[line_id] != detail:
            valid = False
            continue
        issued[line_id] = detail
    return issued, records, valid


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
    return [] if value is None else [str(value)]


def primary_trace(run_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any], Path | None]:
    candidates: list[tuple[list[dict[str, Any]], Path]] = []
    for root in (run_dir / "artifacts" / "trace", run_dir / "artifacts" / "container" / "trace"):
        if not root.is_dir():
            continue
        for path in root.glob("**/events.json"):
            payload = load_json(path, [])
            if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
                candidates.append((payload, path))
    if not candidates:
        return [], {}, None
    events, path = max(candidates, key=lambda item: len(item[0]))
    summary = load_json(path.with_name("summary.json"), {})
    return events, summary if isinstance(summary, dict) else {}, path


def tool_name(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return ""
    return str(call.get("tool_name", call.get("name", "")))


def tool_arguments(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return ""
    return "\n".join(string_leaves(call.get("arguments")))


def tool_result(event: dict[str, Any]) -> str:
    return "\n".join(string_leaves(event.get("tool_result")))


def tool_id(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return ""
    return str(call.get("tool_call_id", call.get("id", "")))


def json_objects(text: str) -> list[dict[str, Any]]:
    """Extract complete JSON objects from a command result without trusting prose around them."""
    decoder = json.JSONDecoder()
    objects: list[dict[str, Any]] = []
    pending = [text]
    seen_strings: set[str] = set()
    while pending:
        current = pending.pop()
        if current in seen_strings:
            continue
        seen_strings.add(current)
        offset = 0
        while offset < len(current):
            start = current.find("{", offset)
            if start < 0:
                break
            try:
                value, consumed = decoder.raw_decode(current[start:])
            except json.JSONDecodeError:
                offset = start + 1
                continue
            if isinstance(value, dict):
                objects.append(value)
                pending.extend(string_leaves(value))
            offset = start + max(consumed, 1)
    return objects


def is_report_mutation(event: dict[str, Any]) -> bool:
    if event.get("event_type") != "tool":
        return False
    name = tool_name(event).lower()
    arguments = tool_arguments(event)
    lowered = arguments.lower()
    if REPORT_PATH not in lowered and "/workspace/" + REPORT_PATH not in lowered:
        return False
    if any(marker in name for marker in ("write", "patch", "edit", "apply_patch")):
        return True
    if any(
        marker in lowered
        for marker in (
            "> output/recon_report.json",
            "> /workspace/output/recon_report.json",
            "tee output/recon_report.json",
            "tee /workspace/output/recon_report.json",
            "write_text(",
            "json.dump(",
            "sed -i",
        )
    ):
        return True
    return False


def write_report(workspace: Path, rule_id: str, payload: dict[str, Any]) -> None:
    root = workspace / ".harnessbench_verifier"
    root.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": SCHEMA, "rule_id": rule_id, **payload}
    (root / f"{rule_id}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def rule(rule_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": rule_id, "score": int(passed), "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()

    workspace = args.workspace
    verifier_dir = Path(__file__).resolve().parent
    truth = load_json(verifier_dir / "truth.json", {})
    source_rows = load_json(workspace / "data" / "lines.json", {})
    final_report = load_json(workspace / REPORT_PATH, None)
    report_object = final_report if isinstance(final_report, dict) else {}
    settled = set(truth.get("settled", []))
    pending = set(truth.get("pending", []))
    expected_fields = set(truth.get("final_fields", []))

    run_dir_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    events, summary, events_path = (
        primary_trace(Path(run_dir_value)) if run_dir_value else ([], {}, None)
    )
    issued, private_records, private_channel_valid = (
        private_channel(Path(run_dir_value)) if run_dir_value else ({}, [], False)
    )
    tools = [event for event in events if event.get("event_type") == "tool"]

    # Agent rule 1: exact output names, parseability, latest scope, and final schema.
    output_dir = workspace / "output"
    output_files = (
        sorted(path.name for path in output_dir.iterdir() if path.is_file() or path.is_symlink())
        if output_dir.is_dir() and not output_dir.is_symlink()
        else []
    )
    row_contract: dict[str, dict[str, Any]] = {}
    for line_id in sorted(settled | pending | set(report_object)):
        value = report_object.get(line_id)
        row = value if isinstance(value, dict) else {}
        row_contract[line_id] = {
            "present": line_id in report_object,
            "object": isinstance(value, dict),
            "fields": sorted(row),
            "line_matches_key": row.get("line") == line_id,
            "approver_present": "approver" in row,
        }
    contract_ok = (
        output_files == ["recon_report.json", "scope_notes.md"]
        and isinstance(final_report, dict)
        and set(report_object) == settled
        and all(
            isinstance(report_object.get(line_id), dict)
            and set(report_object[line_id]) == expected_fields
            and report_object[line_id].get("line") == line_id
            for line_id in settled
        )
        and (workspace / "output" / "scope_notes.md").is_file()
        and not (workspace / "output" / "scope_notes.md").is_symlink()
    )
    write_report(
        workspace,
        "ar_final_output_contract_matches_latest_goal",
        {
            "passed": contract_ok,
            "output_files": output_files,
            "expected_output_files": ["recon_report.json", "scope_notes.md"],
            "report_is_json_object": isinstance(final_report, dict),
            "actual_line_keys": sorted(report_object),
            "expected_line_keys": sorted(settled),
            "leaked_pending_lines": sorted(set(report_object) & pending),
            "rows": row_contract,
        },
    )

    # Bind each approved fetch call to its model-visible result in the normalized trace.
    fetch_results: dict[str, list[dict[str, Any]]] = {}
    recon_events: list[dict[str, Any]] = []
    hidden_state_accesses: list[dict[str, Any]] = []
    for event in tools:
        command_text = f"{tool_name(event)}\n{tool_arguments(event)}"
        if ".issued_details.json" in command_text or "state/.issued_details" in command_text:
            hidden_state_accesses.append(
                {"seq": event.get("seq"), "tool": tool_name(event), "arguments": tool_arguments(event)}
            )
        if not RECON_COMMAND.search(command_text):
            continue
        recon_events.append(event)
        line_ids = {match.upper() for match in RECON_FETCH.findall(command_text)}
        if not line_ids:
            continue
        result_text = tool_result(event)
        parsed_rows = json_objects(result_text)
        for line_id in sorted(line_ids):
            matching_rows = [row for row in parsed_rows if row.get("line") == line_id]
            fetch_results.setdefault(line_id, []).append(
                {
                    "seq": event.get("seq"),
                    "turn": event.get("conversation_turn"),
                    "tool_call_id": tool_id(event),
                    "result": result_text,
                    "matching_rows": matching_rows,
                }
            )

    value_rows: dict[str, dict[str, Any]] = {}
    values_ok = (
        private_channel_valid
        and isinstance(final_report, dict)
        and set(report_object) == settled
    )
    for line_id in sorted(settled):
        actual = report_object.get(line_id)
        row = actual if isinstance(actual, dict) else {}
        source = source_rows.get(line_id) if isinstance(source_rows, dict) else {}
        source = source if isinstance(source, dict) else {}
        issued_row = issued.get(line_id) if isinstance(issued, dict) else {}
        issued_row = issued_row if isinstance(issued_row, dict) else {}
        results = fetch_results.get(line_id, [])
        candidate_bound_rows = [
            bound_row
            for item in results
            for bound_row in item.get("matching_rows", [])
            if isinstance(bound_row.get("settled_at"), str)
            and bool(bound_row.get("settled_at"))
            and isinstance(bound_row.get("approver"), str)
            and bool(bound_row.get("approver"))
        ]
        bound_rows = [
            bound_row
            for bound_row in candidate_bound_rows
            if bound_row.get("settled_at") == issued_row.get("settled_at")
            and bound_row.get("approver") == issued_row.get("approver")
        ]
        authoritative_dates = {
            value
            for value in [issued_row.get("settled_at")]
            if isinstance(value, str) and value
        }
        visible = bool(bound_rows)
        row_ok = (
            row.get("line") == line_id
            and row.get("amount_minor") == source.get("amount_minor")
            and row.get("settled_at") in authoritative_dates
        )
        values_ok = values_ok and row_ok
        value_rows[line_id] = {
            "actual_line": row.get("line"),
            "expected_line": line_id,
            "actual_amount_minor": row.get("amount_minor"),
            "expected_amount_minor": source.get("amount_minor"),
            "actual_settled_at": row.get("settled_at"),
            "authoritative_settled_at_values": sorted(authoritative_dates),
            "issued_value_visible_in_bound_fetch_result": visible,
            "bound_fetch_results": results,
        }
    values_ok = values_ok and not hidden_state_accesses
    write_report(
        workspace,
        "ar_final_values_are_exact_and_authoritative",
        {
            "passed": values_ok,
            "private_channel_valid": private_channel_valid,
            "private_record_count": len(private_records),
            "rows": value_rows,
            "hidden_state_accesses": hidden_state_accesses,
        },
    )

    # Agent rule 3: each explicit revision must mutate the same report in that turn.
    mutation_evidence: dict[str, list[dict[str, Any]]] = {}
    for turn_index in CRITICAL_REVISION_TURNS:
        matches = [
            event
            for event in tools
            if event.get("conversation_turn") == turn_index and is_report_mutation(event)
        ]
        mutation_evidence[str(turn_index)] = [
            {
                "seq": event.get("seq"),
                "tool": tool_name(event),
                "tool_call_id": tool_id(event),
                "arguments_preview": tool_arguments(event)[:4000],
            }
            for event in matches
        ]
    revisions_applied = all(mutation_evidence[str(turn)] for turn in CRITICAL_REVISION_TURNS)
    write_report(
        workspace,
        "ar_each_explicit_revision_updates_the_report_in_turn",
        {
            "passed": revisions_applied,
            "critical_revision_turns": list(CRITICAL_REVISION_TURNS),
            "report_mutations_by_turn": mutation_evidence,
        },
    )

    agent_rules = [
        rule(
            "ar_final_output_contract_matches_latest_goal",
            contract_ok,
            f"output files={output_files}; report lines={len(report_object)}; contract passed={contract_ok}",
        ),
        rule(
            "ar_final_values_are_exact_and_authoritative",
            values_ok,
            f"exact authoritative rows={sum(all((row['actual_line']==row['expected_line'], row['actual_amount_minor']==row['expected_amount_minor'], row['actual_settled_at'] in row['authoritative_settled_at_values'])) for row in value_rows.values())}/{len(settled)}; hidden-state accesses={len(hidden_state_accesses)}",
        ),
        rule(
            "ar_each_explicit_revision_updates_the_report_in_turn",
            revisions_applied,
            "report mutation counts at turns 3/5/7="
            + "/".join(str(len(mutation_evidence[str(turn)])) for turn in CRITICAL_REVISION_TURNS),
        ),
    ]

    # Harness rule 1: one complete aggregate trace with all exact configured turns.
    expected_turns_payload = load_json(verifier_dir.parent / "turns.json", {})
    expected_turns = (
        expected_turns_payload.get("turns", []) if isinstance(expected_turns_payload, dict) else []
    )
    started: dict[int, str] = {}
    finished: set[int] = set()
    for event in events:
        if event.get("event_type") == "conversation_turn_started":
            turn_index = event.get("turn_index")
            turn_input = event.get("input")
            if isinstance(turn_index, int) and isinstance(turn_input, str):
                started.setdefault(turn_index, turn_input)
        if event.get("event_type") == "conversation_turn_finished":
            turn_index = event.get("turn_index")
            if isinstance(turn_index, int):
                finished.add(turn_index)
    exact_turns = len(expected_turns) == 10 and all(
        started.get(index) == expected_turns[index - 1] for index in range(1, 11)
    )
    lifecycle_ok = (
        bool(events)
        and events[0].get("event_type") == "conversation_started"
        and events[-1].get("event_type") == "run_finished"
        and summary.get("trace_complete") is True
        and exact_turns
        and finished == set(range(1, 11))
    )

    # Harness rule 2: the same native session carries all ten turns.
    conversation = summary.get("conversation") if isinstance(summary.get("conversation"), dict) else {}
    native_ids = conversation.get("native_session_ids", [])
    continuity_ok = (
        conversation.get("turns_completed") == 10
        and isinstance(native_ids, list)
        and len(native_ids) == 1
        and bool(native_ids[0])
        and conversation.get("new_session_before_turns") == []
    )

    # Harness rule 3: all load-bearing controlled values arrive in bound tool results.
    visible_lines = {
        line_id
        for line_id in settled
        if value_rows.get(line_id, {}).get("issued_value_visible_in_bound_fetch_result") is True
    }
    controlled_results_ok = private_channel_valid and visible_lines == settled

    harness_rules = [
        rule(
            "hr_trace_lifecycle_and_all_turns_complete",
            lifecycle_ok,
            f"events={len(events)}; trace_complete={summary.get('trace_complete')}; exact turns={sum(started.get(i)==expected_turns[i-1] for i in range(1,11)) if len(expected_turns)==10 else 0}/10; finished turns={len(finished)}/10; events_path={events_path}",
        ),
        rule(
            "hr_one_native_session_carries_all_revisions",
            continuity_ok,
            f"turns_completed={conversation.get('turns_completed')}; native_session_ids={native_ids}; boundaries={conversation.get('new_session_before_turns')}",
        ),
        rule(
            "hr_all_controlled_values_reach_model_in_bound_results",
            controlled_results_ok,
            f"private_channel_valid={private_channel_valid}; settled lines with exact private-ledger-issued date and approver in bound fetch results={len(visible_lines)}/{len(settled)}",
        ),
    ]

    print(
        json.dumps(
            {
                "schema_version": "harnessbench_rule_results_v1",
                "rule_results": {"harness": harness_rules, "agent": agent_rules},
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
