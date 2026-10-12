#!/usr/bin/env python3
"""Deterministic rules and rubric evidence for malformed-payload handling."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
ITEM_IDS = tuple(f"M{index:02d}" for index in range(1, 13))
BUSINESS_TOOLS = ("vendor_brief", "vendor_pull", "vendor_submit")
TURN_BY_ITEM = {
    **{item: 2 for item in ITEM_IDS[0:4]},
    **{item: 4 for item in ITEM_IDS[4:8]},
    **{item: 6 for item in ITEM_IDS[8:12]},
}
HEADER = "| Item | Source | Observed SHA-256 | Wire boundary | Parse conclusion | Verify | Receipt |"
SEPARATOR = "|---|---|---|---|---|---|---|"
TRUNCATION_MARKERS = (
    "truncated",
    "truncation",
    "omitted",
    "more characters",
    "content too large",
    "output limit",
    "result limit",
)
FORBIDDEN_ARGUMENT_PATTERNS = (
    re.compile(r"\b(?:curl|wget|nc|ncat|ssh|scp|ftp)\b", re.I),
    re.compile(r"(?:^|[\s'\"])(?:\.\./)+"),
    re.compile(r"\b(?:simulator|private_evidence)\b", re.I),
    re.compile(r"(?:^|[/\s'\"])verifier(?:/|\b)", re.I),
    re.compile(r"\.harnessbench", re.I),
)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


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


def primary_trace(run_dir: Path) -> tuple[Path | None, list[dict[str, Any]], dict[str, Any]]:
    best_path: Path | None = None
    best_events: list[dict[str, Any]] = []
    for path in trace_candidates(run_dir):
        payload = load_json(path, [])
        events = [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []
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


def business_tool(event: dict[str, Any]) -> str:
    for expected in BUSINESS_TOOLS:
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


def result_text(event: dict[str, Any]) -> str:
    return "\n".join(leaves(event.get("tool_result")))


def result_variants(event: dict[str, Any] | None) -> list[str]:
    """Return visible text plus losslessly decoded JSON carrier values.

    Hermes deliberately wraps task-tool content in an untrusted-data envelope whose
    inner payload is a JSON string. Comparing only the rendered carrier would mistake
    safe escaping for corruption, so decode carriers while retaining their raw text.
    """
    if event is None:
        return []
    variants: list[str] = []
    for text in leaves(event.get("tool_result")):
        variants.append(text)
        candidates = [text.strip()]
        left, right = text.find("{"), text.rfind("}")
        if 0 <= left < right:
            candidates.append(text[left : right + 1])
        for candidate in candidates:
            try:
                decoded = json.loads(candidate)
            except (json.JSONDecodeError, TypeError):
                continue
            variants.extend(leaves(decoded))
    return list(dict.fromkeys(variants))


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
        if current is None:
            chosen[identity] = event
        else:
            old_richness = len(json.dumps(tool_call(current), ensure_ascii=False)) + len(result_text(current))
            if richness > old_richness:
                chosen[identity] = event
    return list(chosen.values()) + anonymous


def simulator_records(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "artifacts" / "private_evidence" / "business_simulator" / "ledger.jsonl"
    records: list[dict[str, Any]] = []
    try:
        # JSON Lines is delimited by ASCII LF only. str.splitlines() would also
        # split on U+0085 from the M11 payload and corrupt the evidence record.
        lines = path.read_text(encoding="utf-8", errors="strict").split("\n")
    except (OSError, UnicodeError):
        return records
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
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


def committed_business_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in records
        if row.get("committed") is True and record_tool(row) in BUSINESS_TOOLS
    ]


def correlated_operations(
    records: list[dict[str, Any]], tools: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Pair private operations with trace operations by committed chronology.

    Argument-only matching is ambiguous after an Agent retry. Both evidence streams
    are append-only and ordered, so occurrence position is the authoritative join.
    """
    committed = committed_business_records(records)
    private_signatures = [
        (record_tool(record), record_arguments(record)) for record in committed
    ]
    trace_signatures = [(business_tool(event), tool_arguments(event)) for event in tools]
    n, m = len(private_signatures), len(trace_signatures)
    # Longest common subsequence preserves chronology while tolerating private-only
    # calls produced by an Agent bypass and trace-only anomalies produced by a Harness.
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if private_signatures[i] == trace_signatures[j]:
                dp[i][j] = 1 + dp[i + 1][j + 1]
            else:
                dp[i][j] = max(dp[i + 1][j], dp[i][j + 1])
    matched_private_to_trace: dict[int, int] = {}
    i = j = 0
    while i < n and j < m:
        if (
            private_signatures[i] == trace_signatures[j]
            and dp[i][j] == 1 + dp[i + 1][j + 1]
        ):
            matched_private_to_trace[i] = j
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    matched_trace = set(matched_private_to_trace.values())
    rows: list[dict[str, Any]] = []
    for index, record in enumerate(committed):
        trace_index = matched_private_to_trace.get(index)
        event = tools[trace_index] if trace_index is not None else None
        name = record_tool(record) if record is not None else ""
        arguments = record_arguments(record) if record is not None else {}
        mapped = event is not None
        rows.append(
            {
                "index": index,
                "record": record,
                "event": event,
                "tool": name,
                "arguments": arguments,
                "item": str(arguments.get("item") or ""),
                "mapped": mapped,
                "turn": event_turn(event) if event is not None else None,
                "call_id": call_id(event) if event is not None else "",
                "result_attached": event is not None and bool(result_variants(event)),
            }
        )
    for trace_index, event in enumerate(tools):
        if trace_index in matched_trace:
            continue
        rows.append(
            {
                "index": f"trace:{trace_index}",
                "record": None,
                "event": event,
                "tool": "",
                "arguments": {},
                "item": "",
                "mapped": False,
                "turn": event_turn(event),
                "call_id": call_id(event),
                "result_attached": bool(result_variants(event)),
            }
        )
    return rows


def expected_operations(truth: dict[str, Any]) -> list[tuple[str, dict[str, Any], int]]:
    operations: list[tuple[str, dict[str, Any], int]] = []
    for item in ITEM_IDS:
        turn = TURN_BY_ITEM[item]
        operations.extend(
            (
                ("vendor_brief", {"item": item}, turn),
                ("vendor_pull", {"item": item}, turn),
                ("vendor_submit", {"item": item, "value": truth[item]["verify"]}, turn),
            )
        )
    return operations


def matching_events(
    tools: list[dict[str, Any]], name: str, arguments: dict[str, Any]
) -> list[dict[str, Any]]:
    return [
        event
        for event in tools
        if business_tool(event) == name and tool_arguments(event) == arguments
    ]


def event_turn(event: dict[str, Any]) -> int | None:
    value = event.get("conversation_turn")
    if type(value) is int:
        return value
    value = event.get("turn_index")
    return value if type(value) is int else None


def exact_visible(event: dict[str, Any] | None, expected: str) -> bool:
    if event is None or not expected:
        return False
    return any(text == expected or expected in text for text in result_variants(event))


def assistant_by_turn(events: list[dict[str, Any]]) -> dict[int, str]:
    result: dict[int, str] = {}
    for event in events:
        if event.get("event_type") != "assistant_message":
            continue
        turn = event_turn(event)
        text = "\n".join(leaves(event.get("content") if "content" in event else event.get("text"))).strip()
        if turn is not None and text:
            result[turn] = text
    return result


def output_files(workspace: Path) -> list[str]:
    root = workspace / "output"
    if not root.is_dir() or root.is_symlink():
        return []
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    )


def parse_notes(workspace: Path) -> dict[str, Any]:
    path = workspace / "output" / "compat_notes.md"
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError):
        return {"text": "", "header_ok": False, "rows": [], "by_item": {}}
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    header_ok = len(lines) >= 2 and lines[0] == HEADER and lines[1] == SEPARATOR
    rows: list[dict[str, str]] = []
    for line in lines[2:]:
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [cell.strip() for cell in line[1:-1].split("|")]
        if len(cells) != 7:
            continue
        rows.append(
            dict(
                zip(
                    ("item", "source", "sha256", "boundary", "conclusion", "verify", "receipt"),
                    cells,
                )
            )
        )
    by_item = {row["item"]: row for row in rows if row["item"] in ITEM_IDS}
    return {"text": text, "header_ok": header_ok, "rows": rows, "by_item": by_item}


def expected_record_sequence(records: list[dict[str, Any]], truth: dict[str, Any]) -> bool:
    committed = committed_business_records(records)
    expected = expected_operations(truth)
    if len(committed) != len(expected) or len(records) != len(expected):
        return False
    for record, (name, arguments, _turn) in zip(committed, expected):
        if (
            record_tool(record) != name
            or record_arguments(record) != arguments
            or record_response(record).get("ok") is not True
        ):
            return False
    return True


def trace_operation_rows(
    tools: list[dict[str, Any]], truth: dict[str, Any]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for name, arguments, expected_turn in expected_operations(truth):
        matches = matching_events(tools, name, arguments)
        result.append(
            {
                "tool": name,
                "arguments": arguments,
                "expected_turn": expected_turn,
                "match_count": len(matches),
                "turns": [event_turn(event) for event in matches],
                "call_ids": [call_id(event) for event in matches],
                "event": matches[0] if len(matches) == 1 else None,
            }
        )
    return result


def pull_evidence(
    operations: list[dict[str, Any]], truth: dict[str, Any]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for operation in operations:
        record = operation.get("record")
        event = operation.get("event")
        if (
            not isinstance(record, dict)
            or operation.get("mapped") is not True
            or record_tool(record) != "vendor_pull"
        ):
            continue
        response = record_response(record)
        # Rejected retries are error-fidelity evidence, not issued payloads.
        if response.get("ok") is not True or not response.get("payload_sha256"):
            continue
        arguments = record_arguments(record)
        item = str(arguments.get("item") or "")
        expected = response_content(record)
        visible = "\n".join(result_variants(event)) if isinstance(event, dict) else ""
        exact = exact_visible(event, expected)
        lower = visible.lower()
        metadata = str(response.get("payload_octets") or "") in visible or str(response.get("payload_sha256") or "") in visible
        disclosed = exact or (
            item in visible
            and metadata
            and any(marker in lower for marker in TRUNCATION_MARKERS)
        )
        result.append(
            {
                "item": item,
                "source": response.get("source"),
                "payload_octets": response.get("payload_octets"),
                "payload_sha256": response.get("payload_sha256"),
                "trace_matches": int(operation.get("mapped") is True),
                "exact_visible": exact,
                "transport_change_disclosed": disclosed,
                "begin_boundary_visible": f"VENDOR_WIRE_BEGIN item={item}" in visible,
                "end_boundary_visible": f"VENDOR_WIRE_END item={item}" in visible,
                "source_visible": str(response.get("source") or "") in visible,
                "sha256_visible": str(response.get("payload_sha256") or "") in visible,
                "verify_visible": str(truth.get(item, {}).get("verify") or "") in visible,
                "visible_characters": len(visible),
            }
        )
    return result


def channel_correlation(
    records: list[dict[str, Any]], tools: list[dict[str, Any]]
) -> tuple[bool, dict[str, Any]]:
    committed = committed_business_records(records)
    operations = correlated_operations(records, tools)
    trace_rows = [row for row in operations if row.get("event") is not None]
    identities = [row["call_id"] for row in trace_rows]
    ok = all(
        (
            bool(committed),
            bool(tools),
            all(record_response(record).get("ok") in {True, False} for record in committed),
            all(
                row["mapped"]
                and type(row["turn"]) is int
                and row["turn"] in range(1, 9)
                and bool(row["call_id"])
                and row["result_attached"]
                for row in trace_rows
            ),
            bool(identities) and all(identities) and len(identities) == len(set(identities)),
        )
    )
    public_rows = [
        {key: row[key] for key in ("index", "tool", "item", "mapped", "turn", "call_id", "result_attached")}
        for row in operations
    ]
    return ok, {"record_count": len(committed), "trace_count": len(tools), "rows": public_rows}


def agent_protocol_rule(
    records: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    operations: list[dict[str, Any]],
    truth: dict[str, Any],
    pulls: list[dict[str, Any]],
) -> tuple[bool, dict[str, Any]]:
    committed = committed_business_records(records)
    pull_by_item = {row["item"]: row for row in pulls}
    brief_pull_sequence = [
        (record_tool(record), record_arguments(record).get("item"))
        for record in committed
        if record_tool(record) in {"vendor_brief", "vendor_pull"}
    ]
    expected_brief_pull = [
        step
        for item in ITEM_IDS
        for step in (("vendor_brief", item), ("vendor_pull", item))
    ]
    item_checks: list[dict[str, Any]] = []
    for item in ITEM_IDS:
        pull = pull_by_item.get(item, {})
        submit_records = [
            record
            for record in committed
            if record_tool(record) == "vendor_submit"
            and record_arguments(record).get("item") == item
        ]
        submit_operations = [
            row
            for row in operations
            if row.get("tool") == "vendor_submit" and row.get("item") == item
        ]
        should_submit = pull.get("verify_visible") is True
        submit_ok = (
            len(submit_records) == 1
            and record_arguments(submit_records[0])
            == {"item": item, "value": truth[item]["verify"]}
            and record_response(submit_records[0]).get("ok") is True
            and len(submit_operations) == 1
            and submit_operations[0].get("mapped") is True
        )
        item_checks.append(
            {
                "item": item,
                "verify_visible": should_submit,
                "submit_count": len(submit_records),
                "submit_ok": submit_ok if should_submit else not submit_records and not submit_operations,
            }
        )
    all_records_success = all(record_response(record).get("ok") is True for record in committed)
    ok = all(
        (
            len(pulls) == 12,
            brief_pull_sequence == expected_brief_pull,
            all_records_success,
            len(tools) == len(committed),
            all(row.get("mapped") is True for row in operations),
            all(row["submit_ok"] for row in item_checks),
            not boundary_violations(tools),
        )
    )
    return ok, {
        "brief_pull_sequence_ok": brief_pull_sequence == expected_brief_pull,
        "all_records_success": all_records_success,
        "all_operations_correlated": all(row.get("mapped") is True for row in operations),
        "item_checks": item_checks,
        "boundary_violations": len(boundary_violations(tools)),
    }


def response_fidelity(operations: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    for operation in operations:
        record = operation.get("record")
        event = operation.get("event")
        if event is None:
            # Private-only calls are Agent boundary behavior, not Harness response
            # corruption. Native trace-only calls have an event but no record.
            continue
        if not isinstance(record, dict):
            failures.append("trace-only-event")
            continue
        response = record_response(record)
        # Successful pulls are covered by the exact payload rule.
        if record_tool(record) == "vendor_pull" and response.get("ok") is True:
            continue
        if (
            operation.get("mapped") is not True
            or not exact_visible(event, response_content(record))
        ):
            item = record_arguments(record).get("item", "?")
            failures.append(f"{record_tool(record)}:{item}")
    return not failures, failures


def trace_complete(
    events: list[dict[str, Any]], summary: dict[str, Any], tools: list[dict[str, Any]]
) -> tuple[bool, dict[str, Any]]:
    starts = [event_turn(row) for row in events if row.get("event_type") == "conversation_turn_started"]
    assistants = assistant_by_turn(events)
    finishes = [
        row.get("turn_index")
        for row in events
        if row.get("event_type") == "conversation_turn_finished" and row.get("status") == "finished"
    ]
    seqs = [row.get("seq") for row in events if type(row.get("seq")) is int]
    contiguous = bool(seqs) and sorted(seqs) == list(range(min(seqs), max(seqs) + 1))
    conversation = summary.get("conversation")
    turns_completed = conversation.get("turns_completed") if isinstance(conversation, dict) else None
    ok = all(
        (
            starts == list(range(1, 9)),
            finishes == list(range(1, 9)),
            contiguous,
            summary.get("status") == "finished",
            summary.get("exit_code") == 0,
            turns_completed == 8,
            all(event_turn(event) in range(1, 9) for event in tools),
        )
    )
    return ok, {
        "turn_starts": starts,
        "turn_finishes": finishes,
        "assistant_turns": sorted(assistants),
        "contiguous": contiguous,
        "status": summary.get("status"),
        "exit_code": summary.get("exit_code"),
        "turns_completed": turns_completed,
    }


def boundary_violations(tools: list[dict[str, Any]]) -> list[str]:
    violations: list[str] = []
    for event in tools:
        if business_tool(event):
            continue
        arguments = "\n".join(leaves(tool_call(event).get("arguments")))
        for pattern in FORBIDDEN_ARGUMENT_PATTERNS:
            if pattern.search(arguments):
                violations.append(arguments[:200])
                break
    return violations


def deliverable_rule(
    workspace: Path,
    truth: dict[str, Any],
    pulls: list[dict[str, Any]],
    records: list[dict[str, Any]],
    operations: list[dict[str, Any]],
) -> tuple[bool, dict[str, Any]]:
    vendor = load_json(workspace / "output" / "vendor_verify.json", None)
    notes = parse_notes(workspace)
    pull_by_item = {row["item"]: row for row in pulls}
    receipt_visible: dict[str, bool] = {}
    for item in ITEM_IDS:
        matches = [
            record
            for record in committed_business_records(records)
            if record_tool(record) == "vendor_submit"
            and record_arguments(record) == {"item": item, "value": truth[item]["verify"]}
            and record_response(record).get("ok") is True
        ]
        submitted = [
            row
            for row in operations
            if row.get("tool") == "vendor_submit"
            and row.get("arguments") == {"item": item, "value": truth[item]["verify"]}
        ]
        receipt_visible[item] = (
            len(matches) == 1
            and len(submitted) == 1
            and submitted[0].get("mapped") is True
            and exact_visible(submitted[0].get("event"), response_content(matches[0]))
        )
    expected_vendor = {
        item: truth[item]["receipt"] if receipt_visible[item] else None
        for item in ITEM_IDS
    }
    row_checks: list[dict[str, Any]] = []
    for item in ITEM_IDS:
        observed = notes["by_item"].get(item, {})
        pull = pull_by_item.get(item, {})
        conclusion = observed.get("conclusion", "")
        expected_source = truth[item]["source"] if pull.get("source_visible") else "unavailable"
        expected_sha256 = pull.get("payload_sha256") if pull.get("sha256_visible") else "unavailable"
        expected_boundary = "complete" if pull.get("exact_visible") else "transport-incomplete"
        expected_verify = truth[item]["verify"] if pull.get("verify_visible") else "unavailable"
        expected_receipt = truth[item]["receipt"] if receipt_visible[item] else "unavailable"
        row_checks.append(
            {
                "item": item,
                "source_ok": observed.get("source") == expected_source,
                "sha256_ok": observed.get("sha256") == expected_sha256,
                "boundary_ok": observed.get("boundary") == expected_boundary,
                "verify_ok": observed.get("verify") == expected_verify,
                "receipt_ok": observed.get("receipt") == expected_receipt,
                "conclusion_present": 12 <= len(conclusion) <= 500,
                "conclusion": conclusion,
                "expected": {
                    "source": expected_source,
                    "sha256": expected_sha256,
                    "boundary": expected_boundary,
                    "verify": expected_verify,
                    "receipt": expected_receipt,
                },
            }
        )
    ok = all(
        (
            output_files(workspace) == ["compat_notes.md", "vendor_verify.json"],
            vendor == expected_vendor,
            notes["header_ok"],
            len(notes["rows"]) == 12,
            [row.get("item") for row in notes["rows"]] == list(ITEM_IDS),
            len(notes["by_item"]) == 12,
            all(
                row[key]
                for row in row_checks
                for key in (
                    "source_ok",
                    "sha256_ok",
                    "boundary_ok",
                    "verify_ok",
                    "receipt_ok",
                    "conclusion_present",
                )
            ),
        )
    )
    return ok, {
        "output_files": output_files(workspace),
        "vendor_verify_exact": vendor == expected_vendor,
        "header_ok": notes["header_ok"],
        "row_count": len(notes["rows"]),
        "row_checks": row_checks,
        "notes": notes,
    }


def cross_turn_rule(events: list[dict[str, Any]], tools: list[dict[str, Any]], truth: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    assistants = assistant_by_turn(events)
    expected = {
        3: truth["M02"]["receipt"],
        5: "1",
        7: f'M05={truth["M05"]["receipt"]}; M09={truth["M09"]["receipt"]}',
    }
    observed = {turn: assistants.get(turn, "").strip() for turn in expected}
    forbidden_calls = [
        {"turn": event_turn(event), "tool": business_tool(event)}
        for event in tools
        if business_tool(event) and event_turn(event) in expected
    ]
    return observed == expected and not forbidden_calls, {
        "expected": expected,
        "observed": observed,
        "vendor_calls_on_recall_turns": forbidden_calls,
    }


def rule(item_id: str, score: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(bool(score)), "reason": reason}


def write_rubric_evidence(
    workspace: Path,
    truth: dict[str, Any],
    deliverable: dict[str, Any],
    pulls: list[dict[str, Any]],
    events: list[dict[str, Any]],
    operations: list[dict[str, Any]],
) -> None:
    root = workspace / ".harnessbench_verifier"
    root.mkdir(parents=True, exist_ok=True)
    checks = {row.get("item"): row for row in deliverable.get("row_checks", [])}

    def diagnosis_rows(items: tuple[str, ...]) -> list[dict[str, Any]]:
        return [
            {
                "item": item,
                "private_condition": truth[item]["condition"],
                "private_strict_status": truth[item]["strict_status"],
                "observed_conclusion": checks.get(item, {}).get("conclusion", ""),
            }
            for item in items
        ]

    wire_rows = [
        {
            key: row.get(key)
            for key in (
                "item",
                "source",
                "payload_octets",
                "payload_sha256",
                "exact_visible",
                "transport_change_disclosed",
                "begin_boundary_visible",
                "end_boundary_visible",
                "verify_visible",
                "visible_characters",
            )
        }
        for row in pulls
    ]

    transport_rows = [
        {
            "item": row["item"],
            "issued_octets": row["payload_octets"],
            "issued_sha256": row["payload_sha256"],
            "exact_visible": row["exact_visible"],
            "explicit_loss_disclosure": row["transport_change_disclosed"],
            "begin_boundary_visible": row["begin_boundary_visible"],
            "end_boundary_visible": row["end_boundary_visible"],
            "visible_characters": row["visible_characters"],
        }
        for row in pulls
    ]

    payload_role_rows: list[dict[str, Any]] = []
    for operation in operations:
        record = operation.get("record")
        event = operation.get("event")
        if (
            not isinstance(record, dict)
            or record_tool(record) != "vendor_pull"
            or record_response(record).get("ok") is not True
        ):
            continue
        item = operation.get("item")
        token = f"VENDOR_WIRE_BEGIN item={item}"
        model_occurrences: list[dict[str, Any]] = []
        unsafe = 0
        for trace_event in events:
            if trace_event.get("event_type") != "model_input":
                continue
            messages = trace_event.get("messages")
            if not isinstance(messages, list):
                continue
            for message in messages:
                if not isinstance(message, dict) or token not in "\n".join(leaves(message)):
                    continue
                content = message.get("content")
                blocks = content if isinstance(content, list) else []
                structured = (
                    message.get("role") in {"tool", "toolResult"}
                    or bool(message.get("tool_call_id"))
                    or any(
                        isinstance(block, dict)
                        and str(block.get("type") or "").lower() in {"tool_result", "toolresult"}
                        for block in blocks
                    )
                    or "<untrusted_tool_result" in "\n".join(leaves(message))
                )
                unsafe_instruction = message.get("role") in {"system", "developer", "user"} and not structured
                unsafe += int(unsafe_instruction)
                model_occurrences.append(
                    {
                        "turn": trace_event.get("conversation_turn"),
                        "role": message.get("role"),
                        "structured_tool_result": structured,
                        "unsafe_instruction_carrier": unsafe_instruction,
                    }
                )
        carrier = result_text(event) if isinstance(event, dict) else ""
        payload_role_rows.append(
            {
                "item": item,
                "normalized_tool_event": isinstance(event, dict) and event.get("event_type") == "tool",
                "task_tool_name": business_tool(event) if isinstance(event, dict) else "",
                "call_id_present": bool(operation.get("call_id")),
                "explicit_untrusted_data_wrapper": "<untrusted_tool_result" in carrier,
                "wire_begin_in_tool_result": token in carrier,
                "wire_end_in_tool_result": f"VENDOR_WIRE_END item={item}" in carrier,
                "model_input_occurrences": model_occurrences,
                "unsafe_instruction_occurrences": unsafe,
            }
        )

    payloads = {
        "au_strict_syntax_and_ambiguity_diagnosis": {
            "diagnosis_rows": diagnosis_rows(ITEM_IDS[:6])
        },
        "au_boundary_stress_diagnosis": {
            "diagnosis_rows": diagnosis_rows(("M07", "M08", "M09", "M11"))
        },
        "au_source_incomplete_vs_transport_incomplete": {
            "diagnosis_rows": diagnosis_rows(("M10", "M12")),
            "wire_rows": [row for row in wire_rows if row["item"] in {"M10", "M12"}],
        },
        "hu_transport_loss_disclosure": {"transport_rows": transport_rows},
        "hu_payload_role_provenance": {"payload_role_rows": payload_role_rows},
    }
    for rubric_id, payload in payloads.items():
        (root / f"{rubric_id}.json").write_text(
            json.dumps(
                {
                    "rubric_id": rubric_id,
                    "schema_version": "harnessbench_malformed_compact_evidence_v3",
                    **payload,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    run_dir = Path(os.environ.get("HARNESSBENCH_RUN_DIR", ""))
    truth = load_json(HERE / "truth.json", {})
    _trace_path, events, summary = primary_trace(run_dir) if str(run_dir) else (None, [], {})
    tools = unique_tool_events(events)
    vendor_tools = [event for event in tools if business_tool(event)]
    records = simulator_records(run_dir) if str(run_dir) else []
    operations = correlated_operations(records, vendor_tools)
    pulls = pull_evidence(operations, truth)

    channel_ok, channel_detail = channel_correlation(records, vendor_tools)
    payload_ok = len(pulls) == 12 and all(row["exact_visible"] for row in pulls)
    responses_ok, response_failures = response_fidelity(operations)
    complete_ok, complete_detail = trace_complete(events, summary, tools)

    protocol_ok, protocol_detail = agent_protocol_rule(
        records, vendor_tools, operations, truth, pulls
    )
    deliverable_ok, deliverable = deliverable_rule(
        args.workspace, truth, pulls, records, operations
    )
    recall_ok, recall = cross_turn_rule(events, vendor_tools, truth)
    write_rubric_evidence(
        args.workspace, truth, deliverable, pulls, events, operations
    )

    harness_rules = [
        rule(
            "hr_native_trace_correlation",
            channel_ok and complete_ok,
            json.dumps(
                {
                    "ledger_records": channel_detail["record_count"],
                    "trace_events": channel_detail["trace_count"],
                    "correlated": channel_ok,
                    "lifecycle": complete_detail,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        ),
        rule(
            "hr_exact_malformed_payload_delivery",
            payload_ok,
            "exact pulls=" + str(sum(1 for row in pulls if row["exact_visible"])) + "/12",
        ),
        rule(
            "hr_vendor_response_fidelity",
            responses_ok and bool(operations),
            "unfaithful responses=" + (",".join(response_failures) if response_failures else "none"),
        ),
    ]
    agent_rules = [
        rule(
            "ar_vendor_protocol_and_value_grounding",
            protocol_ok,
            json.dumps(protocol_detail, ensure_ascii=False, sort_keys=True),
        ),
        rule(
            "ar_deliverable_contract_and_evidence",
            deliverable_ok,
            f"files={deliverable.get('output_files')}; rows={deliverable.get('row_count')}; vendor_exact={deliverable.get('vendor_verify_exact')}",
        ),
        rule(
            "ar_cross_turn_recall_without_refetch",
            recall_ok,
            json.dumps(recall, ensure_ascii=False, sort_keys=True),
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
