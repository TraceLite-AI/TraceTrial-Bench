#!/usr/bin/env python3
"""Task-local scoring and rubric evidence for native skill-result relay."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
ITEM_IDS = tuple(f"M{index:02d}" for index in range(1, 13))
SKILL_NAMES = ("cx_brief", "cx_submit")
BATCH_TURN = {
    **{item: 2 for item in ITEM_IDS[0:4]},
    **{item: 4 for item in ITEM_IDS[4:8]},
    **{item: 7 for item in ITEM_IDS[8:12]},
}
NON_SKILL_TURNS = {1, 3, 5, 6, 8, 9, 10}
FORBIDDEN_ARGUMENT_PATTERNS = (
    re.compile(r"\b(?:curl|wget|nc|ncat|ssh|scp|ftp)\b", re.I),
    re.compile(r"\.harnessbench", re.I),
    re.compile(r"\b(?:simulator-cli|task_tool_mcp|private_evidence)\b", re.I),
    re.compile(r"(?:^|[/\s'\"])verifier(?:/|\b)", re.I),
    re.compile(r"\b(?:http|https)://", re.I),
)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def read_json_unique(path: Path) -> tuple[Any, str]:
    class DuplicateKey(ValueError):
        pass

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise DuplicateKey(key)
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_text(encoding="utf-8", errors="strict"),
            object_pairs_hook=pairs,
        )
    except DuplicateKey as exc:
        return None, f"duplicate key {exc}"
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"unreadable: {type(exc).__name__}"
    return value, "valid"


def leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in leaves(child)]
    if isinstance(value, list):
        return [text for child in value for text in leaves(child)]
    return [] if value is None else [str(value)]


def trace_candidates(run_dir: Path) -> list[Path]:
    result: list[Path] = []
    seen: set[Path] = set()
    for root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if not root.is_dir():
            continue
        for path in root.rglob("events.json"):
            try:
                resolved = path.resolve(strict=True)
            except OSError:
                continue
            if resolved not in seen:
                seen.add(resolved)
                result.append(resolved)
    return sorted(result)


def primary_trace(
    run_dir: Path,
) -> tuple[Path | None, list[dict[str, Any]], dict[str, Any]]:
    best_path: Path | None = None
    best_events: list[dict[str, Any]] = []
    for path in trace_candidates(run_dir):
        payload = load_json(path, [])
        events = (
            [row for row in payload if isinstance(row, dict)]
            if isinstance(payload, list)
            else []
        )
        if len(events) > len(best_events):
            best_path, best_events = path, events
    summary = load_json(best_path.with_name("summary.json"), {}) if best_path else {}
    return best_path, best_events, summary if isinstance(summary, dict) else {}


def tool_call(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("tool_call")
    return value if isinstance(value, dict) else {}


def tool_name(event: dict[str, Any]) -> str:
    call = tool_call(event)
    return str(call.get("tool_name") or call.get("name") or "")


def is_tool(event: dict[str, Any], expected: str) -> bool:
    name = tool_name(event).lower()
    return name == expected or name.endswith("__" + expected) or name.endswith("_" + expected)


def skill_name(event: dict[str, Any]) -> str:
    for expected in SKILL_NAMES:
        if is_tool(event, expected):
            return expected
    return ""


def tool_arguments(event: dict[str, Any]) -> dict[str, Any]:
    value = tool_call(event).get("arguments")
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def call_id(event: dict[str, Any]) -> str:
    call = tool_call(event)
    return str(call.get("tool_call_id") or call.get("id") or "")


def event_turn(event: dict[str, Any]) -> int | None:
    value = event.get("conversation_turn")
    if type(value) is int:
        return value
    value = event.get("turn_index")
    return value if type(value) is int else None


def result_text(event: dict[str, Any]) -> str:
    return "\n".join(leaves(event.get("tool_result")))


def unique_tool_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chosen: dict[str, dict[str, Any]] = {}
    anonymous: list[dict[str, Any]] = []
    for event in events:
        if event.get("event_type") != "tool":
            continue
        identity = call_id(event)
        if not identity:
            anonymous.append(event)
            continue
        current = chosen.get(identity)
        richness = len(json.dumps(tool_call(event), ensure_ascii=False)) + len(result_text(event))
        old_richness = (
            len(json.dumps(tool_call(current), ensure_ascii=False)) + len(result_text(current))
            if current is not None
            else -1
        )
        if richness > old_richness:
            chosen[identity] = event
    return sorted(
        [*chosen.values(), *anonymous],
        key=lambda row: row.get("seq") if type(row.get("seq")) is int else 10**18,
    )


def embedded_json_values(text: str) -> list[Any]:
    values: list[Any] = []
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character not in "[{":
            continue
        try:
            value, _end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            continue
        values.append(value)
    return values


def string_candidates(value: Any) -> list[str]:
    result: list[str] = []
    pending: list[Any] = [value]
    seen_strings: set[str] = set()
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
        elif isinstance(current, str):
            if current in seen_strings:
                continue
            seen_strings.add(current)
            result.append(current)
            pending.extend(embedded_json_values(current))
    return result


def exact_visible(event: dict[str, Any] | None, expected: str) -> bool:
    return bool(event is not None and expected and expected in string_candidates(event.get("tool_result")))


def extracted_task_content(event: dict[str, Any] | None, expected_tool: str) -> str | None:
    if event is None:
        return None
    matches: list[str] = []
    for text in string_candidates(event.get("tool_result")):
        if expected_tool == "cx_brief":
            lines = text.split("\n")
            if (
                len(lines) == 3
                and lines[0].startswith("ITEM M")
                and lines[1].startswith("CONSTRAINT ")
                and lines[2].startswith("ACCEPTED_COPY ")
            ):
                matches.append(text)
        elif text.startswith(("RECEIPT item=M", "REJECTED item=M", "ERROR: ")):
            matches.append(text)
    return min(matches, key=len) if matches else None


def usable_brief_content(event: dict[str, Any] | None, item: str) -> str | None:
    content = extracted_task_content(event, "cx_brief")
    if not isinstance(content, str):
        return None
    lines = content.split("\n")
    if (
        len(lines) != 3
        or lines[0] != f"ITEM {item}"
        or not lines[1].startswith("CONSTRAINT ")
        or not lines[2].startswith("ACCEPTED_COPY ")
        or not lines[2][len("ACCEPTED_COPY ") :]
    ):
        return None
    return content


def usable_brief_value(event: dict[str, Any] | None, item: str) -> str | None:
    content = usable_brief_content(event, item)
    if content is None:
        return None
    return content.split("\n", 2)[2][len("ACCEPTED_COPY ") :]


def usable_submit_content(event: dict[str, Any] | None, item: str) -> str | None:
    content = extracted_task_content(event, "cx_submit")
    if not isinstance(content, str):
        return None
    if content.startswith("ERROR: "):
        return content
    if re.fullmatch(rf"RECEIPT item={re.escape(item)} code=[0-9A-F]{{8}}", content):
        return content
    if re.fullmatch(rf"REJECTED item={re.escape(item)} reason=[A-Z0-9_]+", content):
        return content
    return None


def simulator_records(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "artifacts" / "private_evidence" / "business_simulator" / "ledger.jsonl"
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").split("\n")
    except (OSError, UnicodeError):
        return []
    records: list[dict[str, Any]] = []
    for line in lines:
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            return []
        request = row.get("request") if isinstance(row, dict) else None
        if (
            isinstance(row, dict)
            and row.get("committed") is True
            and isinstance(request, dict)
            and request.get("tool") in SKILL_NAMES
        ):
            records.append(row)
    return records


def record_tool(record: dict[str, Any]) -> str:
    request = record.get("request")
    return str(request.get("tool") or "") if isinstance(request, dict) else ""


def record_arguments(record: dict[str, Any]) -> dict[str, Any]:
    request = record.get("request")
    value = request.get("arguments") if isinstance(request, dict) else None
    return value if isinstance(value, dict) else {}


def record_response(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("response")
    return value if isinstance(value, dict) else {}


def response_content(record: dict[str, Any]) -> str:
    value = record_response(record).get("_task_tool_content")
    return value if isinstance(value, str) else ""


def channel_correlation(
    records: list[dict[str, Any]], native_events: list[dict[str, Any]]
) -> tuple[bool, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    for index in range(max(len(records), len(native_events))):
        record = records[index] if index < len(records) else None
        event = native_events[index] if index < len(native_events) else None
        rows.append(
            {
                "index": index + 1,
                "record_tool": record_tool(record) if record else None,
                "event_tool": skill_name(event) if event else None,
                "arguments_match": bool(
                    record is not None
                    and event is not None
                    and record_arguments(record) == tool_arguments(event)
                ),
                "turn": event_turn(event) if event else None,
                "call_id": call_id(event) if event else "",
                "result_attached": bool(event is not None and string_candidates(event.get("tool_result"))),
            }
        )
    identities = [call_id(event) for event in native_events]
    ok = all(
        (
            bool(records),
            len(records) == len(native_events),
            all(
                row["record_tool"] == row["event_tool"]
                and row["arguments_match"]
                and type(row["turn"]) is int
                and 1 <= row["turn"] <= 10
                and bool(row["call_id"])
                and row["result_attached"]
                for row in rows
            ),
            bool(identities) and all(identities) and len(identities) == len(set(identities)),
        )
    )
    return ok, rows


def fidelity_check(
    records: list[dict[str, Any]], native_events: list[dict[str, Any]], correlated: bool
) -> tuple[bool, list[str], int]:
    private_counts = Counter(
        (
            record_tool(record),
            json.dumps(record_arguments(record), ensure_ascii=False, sort_keys=True),
            response_content(record),
        )
        for record in records
    )
    visible_counts = Counter(
        (
            skill_name(event),
            json.dumps(tool_arguments(event), ensure_ascii=False, sort_keys=True),
            extracted_task_content(event, skill_name(event)) or "",
        )
        for event in native_events
    )
    exact_count = sum((private_counts & visible_counts).values())
    failures: list[str] = []
    if not correlated:
        return False, ["channel_not_one_to_one"], exact_count
    for record, event in zip(records, native_events):
        expected = response_content(record)
        if not exact_visible(event, expected):
            item = record_arguments(record).get("item", "?")
            failures.append(f"{record_tool(record)}:{item}")
    return not failures and bool(records), failures, exact_count


def assistant_by_turn(events: list[dict[str, Any]]) -> dict[int, str]:
    result: dict[int, str] = {}
    for event in events:
        if event.get("event_type") != "assistant_message":
            continue
        turn = event_turn(event)
        content = event.get("content") if "content" in event else event.get("text")
        text = "\n".join(leaves(content)).strip()
        if turn is not None and text:
            result[turn] = text
    return result


def trace_complete(
    run_dir: Path,
    trace_path: Path | None,
    events: list[dict[str, Any]],
    summary: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    starts = [event_turn(row) for row in events if row.get("event_type") == "conversation_turn_started"]
    finishes = [event_turn(row) for row in events if row.get("event_type") == "conversation_turn_finished"]
    assistants = assistant_by_turn(events)
    seqs = [row.get("seq") for row in events if type(row.get("seq")) is int]
    contiguous = bool(seqs) and seqs == list(range(1, max(seqs) + 1))
    conversation = summary.get("conversation")
    turns_completed = conversation.get("turns_completed") if isinstance(conversation, dict) else None
    lifecycle = load_json(run_dir / "artifacts" / "container_lifecycle.json", {})
    ok = all(
        (
            trace_path is not None,
            starts == list(range(1, 11)),
            finishes == list(range(1, 11)),
            set(assistants) == set(range(1, 11)),
            contiguous,
            summary.get("status") == "finished",
            summary.get("exit_code") == 0,
            summary.get("trace_complete") is True,
            summary.get("last_event_seq") == max(seqs, default=None),
            turns_completed == 10,
            lifecycle.get("archive_status") == "complete",
            lifecycle.get("cleanup_status") == "complete",
            lifecycle.get("error_type") is None,
        )
    )
    return ok, {
        "trace": str(trace_path) if trace_path else None,
        "events": len(events),
        "max_seq": max(seqs, default=None),
        "turn_starts": starts,
        "turn_finishes": finishes,
        "assistant_turns": sorted(assistants),
        "contiguous": contiguous,
        "summary_status": summary.get("status"),
        "summary_exit_code": summary.get("exit_code"),
        "trace_complete": summary.get("trace_complete"),
        "turns_completed": turns_completed,
        "archive_status": lifecycle.get("archive_status"),
        "cleanup_status": lifecycle.get("cleanup_status"),
    }


def boundary_violations(tool_events: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    for event in tool_events:
        if skill_name(event):
            continue
        name = tool_name(event)
        lowered_name = name.lower()
        if any(token in lowered_name for token in ("browser", "websearch", "web_search", "webfetch", "web_fetch")):
            failures.append(f"seq={event.get('seq')}:network:{name}")
            continue
        arguments = json.dumps(tool_call(event).get("arguments"), ensure_ascii=False)
        if any(pattern.search(arguments) for pattern in FORBIDDEN_ARGUMENT_PATTERNS):
            failures.append(f"seq={event.get('seq')}:hidden_or_external:{name}")
    return failures


def expected_native_operations(
    native_events: list[dict[str, Any]],
) -> list[tuple[str, dict[str, Any], int]]:
    operations: list[tuple[str, dict[str, Any], int]] = []
    for item in ITEM_IDS:
        operations.append(("cx_brief", {"item": item}, BATCH_TURN[item]))
        briefs = events_for_item(native_events, "cx_brief", item)
        accepted_copy = (
            usable_brief_value(briefs[0], item) if len(briefs) == 1 else None
        )
        if accepted_copy is not None:
            operations.append(
                (
                    "cx_submit",
                    {"item": item, "value": accepted_copy},
                    BATCH_TURN[item],
                )
            )
    return operations


def agent_workflow(
    events: list[dict[str, Any]], tool_events: list[dict[str, Any]]
) -> tuple[bool, dict[str, Any]]:
    native = [event for event in tool_events if skill_name(event)]
    expected = expected_native_operations(native)
    rows: list[dict[str, Any]] = []
    for index in range(max(len(native), len(expected))):
        event = native[index] if index < len(native) else None
        wanted = expected[index] if index < len(expected) else None
        rows.append(
            {
                "index": index + 1,
                "observed_tool": skill_name(event) if event else None,
                "observed_arguments": tool_arguments(event) if event else None,
                "observed_turn": event_turn(event) if event else None,
                "expected_tool": wanted[0] if wanted else None,
                "expected_arguments": wanted[1] if wanted else None,
                "expected_turn": wanted[2] if wanted else None,
            }
        )
    native_exact = len(native) == len(expected) and all(
        row["observed_tool"] == row["expected_tool"]
        and row["observed_arguments"] == row["expected_arguments"]
        and row["observed_turn"] == row["expected_turn"]
        for row in rows
    )
    non_skill_calls = [
        {"turn": event_turn(event), "tool": skill_name(event)}
        for event in native
        if event_turn(event) in NON_SKILL_TURNS
    ]
    early_output = []
    for event in tool_events:
        if event_turn(event) != 1:
            continue
        name = tool_name(event).lower()
        arguments = json.dumps(tool_call(event).get("arguments"), ensure_ascii=False).lower()
        can_write = any(token in name for token in ("write", "edit", "create", "exec", "command", "terminal", "bash"))
        if can_write and (
            "output/" in arguments or "copy_receipts" in arguments or "relay_audit" in arguments
        ):
            early_output.append(f"seq={event.get('seq')}:{tool_name(event)}")
    forbidden = boundary_violations(tool_events)
    persistence_rows: list[dict[str, Any]] = []
    for item in ITEM_IDS:
        briefs = events_for_item(native, "cx_brief", item)
        submits = [
            event
            for event in native
            if skill_name(event) == "cx_submit"
            and tool_arguments(event).get("item") == item
        ]
        brief = briefs[0] if len(briefs) == 1 else None
        brief_usable = usable_brief_value(brief, item) is not None
        last_call = (
            submits[0]
            if brief_usable and len(submits) == 1
            else brief
            if not brief_usable and not submits
            else None
        )
        last_call_seq = last_call.get("seq") if last_call is not None else None
        turn = event_turn(last_call) if last_call is not None else None
        later_native = [
            event
            for event in native
            if type(last_call_seq) is int
            and type(event.get("seq")) is int
            and event.get("seq") > last_call_seq
            and event_turn(event) == turn
        ]
        if later_native:
            boundary_seq = min(event["seq"] for event in later_native)
        else:
            later_assistants = [
                event.get("seq")
                for event in events
                if event.get("event_type") == "assistant_message"
                and event_turn(event) == turn
                and type(event.get("seq")) is int
                and type(last_call_seq) is int
                and event.get("seq") > last_call_seq
            ]
            boundary_seq = max(later_assistants, default=None)
        targets: set[str] = set()
        if type(last_call_seq) is int and type(boundary_seq) is int:
            for event in tool_events:
                seq = event.get("seq")
                if type(seq) is not int or not (last_call_seq < seq < boundary_seq):
                    continue
                name = tool_name(event).lower()
                if not any(
                    token in name
                    for token in ("write", "edit", "create", "exec", "command", "terminal", "bash")
                ):
                    continue
                arguments = json.dumps(
                    tool_call(event).get("arguments"), ensure_ascii=False
                ).lower()
                for filename in ("copy_receipts.json", "relay_audit.json"):
                    if filename in arguments:
                        targets.add(filename)
        persistence_rows.append(
            {
                "item": item,
                "brief_usable": brief_usable,
                "last_permitted_call": (
                    skill_name(last_call) if last_call is not None else None
                ),
                "last_call_seq": last_call_seq,
                "boundary_seq": boundary_seq,
                "updated_files": sorted(targets),
                "both_updated": targets
                == {"copy_receipts.json", "relay_audit.json"},
            }
        )
    persistence_ok = all(row["both_updated"] for row in persistence_rows)
    ok = (
        native_exact
        and persistence_ok
        and not non_skill_calls
        and not early_output
        and not forbidden
    )
    return ok, {
        "native_event_count": len(native),
        "expected_event_count": len(expected),
        "native_sequence_exact": native_exact,
        "non_skill_calls": non_skill_calls,
        "early_output": early_output,
        "boundary_violations": forbidden,
        "incremental_persistence_ok": persistence_ok,
        "persistence_rows": persistence_rows,
        "rows": rows,
    }


def output_files(workspace: Path) -> list[str]:
    root = workspace / "output"
    if not root.is_dir() or root.is_symlink():
        return []
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    )


def events_for_item(
    native_events: list[dict[str, Any]], name: str, item: str
) -> list[dict[str, Any]]:
    return [
        event
        for event in native_events
        if skill_name(event) == name and tool_arguments(event).get("item") == item
    ]


def visible_results(native_events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in ITEM_IDS:
        briefs = events_for_item(native_events, "cx_brief", item)
        submits = events_for_item(native_events, "cx_submit", item)
        brief = usable_brief_content(briefs[0], item) if len(briefs) == 1 else None
        submit = usable_submit_content(submits[0], item) if len(submits) == 1 else None
        receipt = None
        if isinstance(submit, str):
            match = re.fullmatch(rf"RECEIPT item={re.escape(item)} code=([0-9A-F]{{8}})", submit)
            receipt = match.group(1) if match else None
        result[item] = {
            "brief_result": brief,
            "submit_result": submit,
            "receipt": receipt,
            "decision": "accepted" if receipt is not None else "unavailable",
            "brief_event_count": len(briefs),
            "submit_event_count": len(submits),
        }
    return result


def deliverable_check(
    workspace: Path, visible: dict[str, dict[str, Any]]
) -> tuple[bool, dict[str, Any]]:
    receipts, receipts_status = read_json_unique(workspace / "output" / "copy_receipts.json")
    audit, audit_status = read_json_unique(workspace / "output" / "relay_audit.json")
    expected_receipts = {item: visible[item]["receipt"] for item in ITEM_IDS}
    expected_audit = [
        {
            "item": item,
            "brief_result": visible[item]["brief_result"],
            "submit_result": visible[item]["submit_result"],
            "decision": visible[item]["decision"],
        }
        for item in ITEM_IDS
    ]
    receipt_keys_exact = isinstance(receipts, dict) and list(receipts) == list(ITEM_IDS)
    audit_shape = (
        isinstance(audit, list)
        and len(audit) == 12
        and all(
            isinstance(row, dict)
            and list(row) == ["item", "brief_result", "submit_result", "decision"]
            for row in audit
        )
    )
    ok = all(
        (
            output_files(workspace) == ["copy_receipts.json", "relay_audit.json"],
            receipts_status == "valid",
            audit_status == "valid",
            receipt_keys_exact,
            audit_shape,
            receipts == expected_receipts,
            audit == expected_audit,
        )
    )
    return ok, {
        "output_files": output_files(workspace),
        "receipts_status": receipts_status,
        "audit_status": audit_status,
        "receipt_keys_exact": receipt_keys_exact,
        "audit_shape": audit_shape,
        "receipts_exact": receipts == expected_receipts,
        "audit_exact": audit == expected_audit,
        "actual_receipts": receipts,
        "actual_audit": audit,
        "expected_receipts": expected_receipts,
        "expected_audit": expected_audit,
    }


def exact_turn_responses(
    events: list[dict[str, Any]],
    native_events: list[dict[str, Any]],
    visible: dict[str, dict[str, Any]],
) -> tuple[bool, dict[str, Any]]:
    observed = assistant_by_turn(events)
    m02_receipt = visible["M02"].get("receipt") or "unavailable"
    m01_submit = visible["M01"].get("submit_result") or "unavailable"
    m08_submit = visible["M08"].get("submit_result") or "unavailable"
    m06_brief = visible["M06"].get("brief_result")
    m09_submit = visible["M09"].get("submit_result")
    relay_response = (
        "B2_BRIEF:\n"
        f"{m06_brief}\n"
        "B3_SUBMIT:\n"
        f"{m09_submit}"
        if isinstance(m06_brief, str) and isinstance(m09_submit, str)
        else "unavailable"
    )
    expected = {
        1: "READY",
        2: "B1_COMPLETE",
        3: str(m02_receipt),
        4: "B2_COMPLETE",
        5: (
            f"M01_RESULT={m01_submit}\n"
            f"M08_RESULT={m08_submit}"
        ),
        6: "11",
        7: "B3_COMPLETE",
        8: relay_response,
        9: "FILES_COMPLETE",
        10: validation_response(visible),
    }
    actual = {turn: observed.get(turn) for turn in range(1, 11)}
    forbidden = [
        {"turn": event_turn(event), "tool": skill_name(event)}
        for event in native_events
        if event_turn(event) in NON_SKILL_TURNS
    ]
    return actual == expected and not forbidden, {
        "expected": expected,
        "observed": actual,
        "compliance_calls_on_tool_free_turns": forbidden,
    }


def validation_response(visible: dict[str, dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in ITEM_IDS:
        brief = visible[item].get("brief_result")
        submit = visible[item].get("submit_result")
        if not isinstance(brief, str) or not isinstance(submit, str):
            return "VALIDATED relay_sha256=unavailable"
        parts.extend((brief, "\n", submit, "\n"))
    digest = hashlib.sha256("".join(parts).encode("utf-8")).hexdigest()
    return f"VALIDATED relay_sha256={digest}"


def compact_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "seq": event.get("seq"),
        "event_type": event.get("event_type"),
        "conversation_turn": event_turn(event),
        "native_session_id": event.get("native_session_id"),
        "tool_call": event.get("tool_call"),
        "tool_result": event.get("tool_result"),
        "messages": event.get("messages"),
        "content": event.get("content"),
    }


def write_rubric_evidence(
    workspace: Path,
    visible: dict[str, dict[str, Any]],
    deliverable: dict[str, Any],
    assistants: dict[int, str],
    events: list[dict[str, Any]],
    native_events: list[dict[str, Any]],
    records: list[dict[str, Any]],
    correlation_rows: list[dict[str, Any]],
    workflow: dict[str, Any],
    turn_detail: dict[str, Any],
    channel_ok: bool,
    fidelity_ok: bool,
    exact_result_count: int,
) -> None:
    root = workspace / ".harnessbench_verifier"
    root.mkdir(parents=True, exist_ok=True)
    actual_audit = deliverable.get("actual_audit")
    actual_by_item = {
        row.get("item"): row
        for row in actual_audit
        if isinstance(row, dict) and row.get("item") in ITEM_IDS
    } if isinstance(actual_audit, list) else {}
    calibration_rows: list[dict[str, Any]] = []
    for item in ITEM_IDS:
        expected = visible[item]
        actual = actual_by_item.get(item, {})
        calibration_rows.append(
            {
                "item": item,
                "model_visible_brief_result": expected.get("brief_result"),
                "artifact_brief_result": actual.get("brief_result"),
                "brief_grounded": actual.get("brief_result") == expected.get("brief_result"),
                "model_visible_submit_result": expected.get("submit_result"),
                "artifact_submit_result": actual.get("submit_result"),
                "submit_grounded": actual.get("submit_result") == expected.get("submit_result"),
                "model_visible_receipt": expected.get("receipt"),
                "artifact_receipt": (
                    deliverable.get("actual_receipts", {}).get(item)
                    if isinstance(deliverable.get("actual_receipts"), dict)
                    else None
                ),
                "receipt_grounded": (
                    isinstance(deliverable.get("actual_receipts"), dict)
                    and deliverable["actual_receipts"].get(item) == expected.get("receipt")
                ),
                "artifact_decision": actual.get("decision"),
                "decision_supported": actual.get("decision") == expected.get("decision"),
            }
        )
    turn_checks = []
    for turn in range(1, 11):
        actual = turn_detail.get("observed", {}).get(turn)
        expected = turn_detail.get("expected", {}).get(turn)
        turn_checks.append(
            {
                "turn": turn,
                "exact": actual == expected,
                "expected": expected,
                "observed": actual if not isinstance(actual, str) or len(actual) <= 800 else actual[:800] + "...[truncated]",
            }
        )
    sample_events = []
    for name, item in (("cx_brief", "M06"), ("cx_submit", "M09")):
        matches = events_for_item(native_events, name, item)
        if matches:
            sample_events.append(compact_event(matches[0]))
    relevant_turns = {2, 3, 4, 5, 7, 8}
    session_rows = [
        {
            "turn": event_turn(event),
            "native_session_id": event.get("native_session_id"),
            "status": event.get("status"),
        }
        for event in events
        if event.get("event_type") == "conversation_turn_finished"
        and event_turn(event) in relevant_turns
    ]
    continuity_requirements = [
        {"item": "M02", "kind": "submit", "result": visible["M02"].get("submit_result"), "recall_turn": 3, "recall_exact": turn_checks[2]["exact"]},
        {"item": "M01", "kind": "submit", "result": visible["M01"].get("submit_result"), "recall_turn": 5, "recall_exact": turn_checks[4]["exact"]},
        {"item": "M08", "kind": "submit", "result": visible["M08"].get("submit_result"), "recall_turn": 5, "recall_exact": turn_checks[4]["exact"]},
        {"item": "M06", "kind": "brief", "result": visible["M06"].get("brief_result"), "recall_turn": 8, "recall_exact": turn_checks[7]["exact"]},
        {"item": "M09", "kind": "submit", "result": visible["M09"].get("submit_result"), "recall_turn": 8, "recall_exact": turn_checks[7]["exact"]},
    ]
    payloads = {
        "au_relay_audit_is_evidence_calibrated": {
            "summary": {
                "rows": len(calibration_rows),
                "fully_grounded_rows": sum(
                    all(
                        row[key]
                        for key in (
                            "brief_grounded",
                            "submit_grounded",
                            "receipt_grounded",
                            "decision_supported",
                        )
                    )
                    for row in calibration_rows
                ),
                "artifact_schema_valid": deliverable.get("audit_shape"),
            },
            "calibration_rows": calibration_rows,
        },
        "au_checkpoint_and_completion_claims_are_supported": {
            "summary": {
                "exact_turn_responses": sum(row["exact"] for row in turn_checks),
                "artifact_receipts_exact": deliverable.get("receipts_exact"),
                "artifact_audit_exact": deliverable.get("audit_exact"),
                "incremental_persistence_ok": workflow.get("incremental_persistence_ok"),
            },
            "turn_checks": turn_checks,
        },
        "hu_skill_result_role_and_provenance_are_clear": {
            "summary": {
                "private_operation_count": len(records),
                "native_event_count": len(native_events),
                "one_to_one_correlated": channel_ok,
                "exact_result_fidelity": fidelity_ok,
                "exact_result_count": exact_result_count,
                "unique_native_call_ids": len(
                    {call_id(event) for event in native_events if call_id(event)}
                ),
            },
            "correlation_rows": correlation_rows,
            "representative_native_events": sample_events,
        },
        "hu_cross_turn_result_continuity_is_preserved": {
            "summary": {
                "all_required_native_results_present": all(
                    isinstance(row["result"], str) and bool(row["result"])
                    for row in continuity_requirements
                ),
                "all_required_recalls_exact": all(
                    row["recall_exact"] for row in continuity_requirements
                ),
                "compliance_calls_on_tool_free_turns": turn_detail.get(
                    "compliance_calls_on_tool_free_turns"
                ),
            },
            "continuity_requirements": continuity_requirements,
            "native_session_rows": session_rows,
            "selected_recall_responses": {
                str(turn): assistants.get(turn) for turn in (3, 5, 8)
            },
        },
    }
    for rubric_id, payload in payloads.items():
        (root / f"{rubric_id}.json").write_text(
            json.dumps(
                {"rubric_id": rubric_id, **payload},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def rule(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(bool(passed)), "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()

    run_dir_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_dir_value) if run_dir_value else Path("/__missing_run__")
    trace_path, events, summary = primary_trace(run_dir)
    tool_events = unique_tool_events(events)
    native_events = [event for event in tool_events if skill_name(event)]
    records = simulator_records(run_dir)

    trace_ok, trace_detail = trace_complete(run_dir, trace_path, events, summary)
    channel_ok, correlation_rows = channel_correlation(records, native_events)
    fidelity_ok, fidelity_failures, exact_result_count = fidelity_check(
        records, native_events, channel_ok
    )
    workflow_ok, workflow_detail = agent_workflow(events, tool_events)
    visible = visible_results(native_events)
    deliverable_ok, deliverable_detail = deliverable_check(args.workspace, visible)
    turn_ok, turn_detail = exact_turn_responses(events, native_events, visible)

    write_rubric_evidence(
        args.workspace,
        visible,
        deliverable_detail,
        assistant_by_turn(events),
        events,
        native_events,
        records,
        correlation_rows,
        workflow_detail,
        turn_detail,
        channel_ok,
        fidelity_ok,
        exact_result_count,
    )

    harness_rules = [
        rule(
            "hr_complete_multiturn_trace",
            trace_ok,
            json.dumps(trace_detail, ensure_ascii=False, sort_keys=True),
        ),
        rule(
            "hr_native_operations_are_correlated",
            channel_ok,
            (
                f"ledger_records={len(records)}; native_events={len(native_events)}; "
                f"matched_rows={sum(row['record_tool'] == row['event_tool'] and row['arguments_match'] for row in correlation_rows)}/{len(correlation_rows)}; "
                f"unique_call_ids={len({call_id(event) for event in native_events if call_id(event)})}/{len(native_events)}"
            ),
        ),
        rule(
            "hr_exact_skill_results_are_relayed",
            fidelity_ok,
            f"exact_results={exact_result_count}/{len(records)}; failures={fidelity_failures}",
        ),
    ]
    agent_rules = [
        rule(
            "ar_released_native_workflow_is_exact",
            workflow_ok,
            (
                f"native_events={workflow_detail['native_event_count']}/{workflow_detail['expected_event_count']}; "
                f"sequence_exact={workflow_detail['native_sequence_exact']}; "
                f"incremental_persistence={workflow_detail['incremental_persistence_ok']}; "
                f"non_skill_calls={workflow_detail['non_skill_calls']}; early_output={workflow_detail['early_output']}; "
                f"boundary_violations={workflow_detail['boundary_violations']}"
            ),
        ),
        rule(
            "ar_relay_deliverables_are_exact_and_grounded",
            deliverable_ok,
            (
                f"files={deliverable_detail['output_files']}; receipts={deliverable_detail['receipts_status']}; "
                f"audit={deliverable_detail['audit_status']}; shape={deliverable_detail['audit_shape']}; "
                f"receipts_exact={deliverable_detail['receipts_exact']}; audit_exact={deliverable_detail['audit_exact']}"
            ),
        ),
        rule(
            "ar_turn_responses_and_tool_free_gates_are_exact",
            turn_ok,
            (
                "mismatched_turns="
                + str(
                    [
                        turn
                        for turn in range(1, 11)
                        if turn_detail["observed"].get(turn)
                        != turn_detail["expected"].get(turn)
                    ]
                )
                + "; compliance_calls_on_tool_free_turns="
                + json.dumps(
                    turn_detail["compliance_calls_on_tool_free_turns"],
                    ensure_ascii=False,
                    sort_keys=True,
                )
            ),
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
