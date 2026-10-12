#!/usr/bin/env python3
"""Verifier for recoverable, idempotent judicial-relief payout cursor polling."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
PROBE_RE = re.compile(r"(?:^|[\s;&|'\"])(?:python3\s+)?(?:\./|/workspace/)?bin/payout\s+probe\b", re.I)
AUDIT_RE = re.compile(r"(?:^|[\s;&|'\"])(?:python3\s+)?(?:\./|/workspace/)?bin/payout\s+audit\b", re.I)
CASE_ARG_RE = re.compile(r"--case(?:=|\s+)(PO-\d{4}-\d{2}-\d{4})\b")
CURSOR_ARG_RE = re.compile(r"--cursor(?:=|\s+)(\d+)\b")

BATCH_1 = ["PO-2026-07-2201", "PO-2026-07-2204", "PO-2026-07-2209", "PO-2026-07-2213"]
BATCH_2 = ["PO-2026-07-2218", "PO-2026-07-2222", "PO-2026-07-2227", "PO-2026-07-2231"]
BATCH_3 = ["PO-2026-07-2236", "PO-2026-07-2240", "PO-2026-07-2245", "PO-2026-07-2249"]

# Exact invocation schedule created by the explicit per-turn observation windows.
EXPECTED_PROBES_BY_TURN: dict[int, dict[str, int]] = {
    1: {case_id: 1 for case_id in BATCH_1},
    2: {"PO-2026-07-2209": 1, "PO-2026-07-2213": 1},
    3: {case_id: 2 for case_id in BATCH_2},
    4: {},
    5: {
        "PO-2026-07-2209": 1,
        "PO-2026-07-2218": 1,
        "PO-2026-07-2222": 1,
        "PO-2026-07-2227": 3,
        "PO-2026-07-2231": 2,
    },
    6: {},
    7: {case_id: 5 for case_id in BATCH_3},
    8: {"PO-2026-07-2245": 2, "PO-2026-07-2249": 1},
    9: {},
}


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError):
        return ""


def load_json_object_without_duplicates(path: Path) -> tuple[dict[str, Any] | None, str]:
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
        value = json.loads(path.read_text(encoding="utf-8", errors="strict"), object_pairs_hook=pairs)
    except FileNotFoundError:
        return None, "missing"
    except UnicodeError:
        return None, "not valid UTF-8"
    except DuplicateKey as exc:
        return None, f"duplicate key {exc}"
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON at line {exc.lineno}"
    if not isinstance(value, dict):
        return None, "JSON root is not an object"
    return value, "valid duplicate-free JSON object"


def string_leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        result: list[str] = []
        for key, child in value.items():
            result.append(str(key))
            result.extend(string_leaves(child))
        return result
    if isinstance(value, list):
        result: list[str] = []
        for child in value:
            result.extend(string_leaves(child))
        return result
    return [] if value is None else [str(value)]


def flattened(value: Any) -> str:
    return "\n".join(string_leaves(value))


def trace_payload(run_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
    seen: set[Path] = set()
    for root in (run_dir / "artifacts" / "trace", run_dir / "artifacts" / "container" / "trace"):
        if not root.is_dir():
            continue
        for path in sorted(root.glob("**/events.json")):
            try:
                resolved = path.resolve(strict=True)
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            payload = load_json(path, [])
            if isinstance(payload, dict):
                payload = payload.get("events", [])
            if not isinstance(payload, list):
                continue
            events = [item for item in payload if isinstance(item, dict)]
            summary = load_json(path.with_name("summary.json"), {})
            candidates.append((events, summary if isinstance(summary, dict) else {}))
    return max(candidates, key=lambda value: len(value[0]), default=([], {}))


def canonical_call_text(event: dict[str, Any]) -> str:
    return flattened(event.get("tool_call")) if isinstance(event.get("tool_call"), dict) else ""


def canonical_result_text(event: dict[str, Any]) -> str:
    return flattened(event.get("tool_result")) if isinstance(event.get("tool_result"), dict) else ""


def raw_call_text(event: dict[str, Any]) -> str:
    raw = event.get("raw_event")
    return flattened(raw.get("tool_call")) if isinstance(raw, dict) else ""


def raw_result_text(event: dict[str, Any]) -> str:
    raw = event.get("raw_event")
    return flattened(raw.get("tool_result")) if isinstance(raw, dict) else ""


def payout_kind(text: str) -> str | None:
    probe = PROBE_RE.search(text)
    audit = AUDIT_RE.search(text)
    if probe and (not audit or probe.start() < audit.start()):
        return "probe"
    if audit:
        return "audit"
    return None


def effective_call_result(event: dict[str, Any]) -> tuple[str, str, str | None, str]:
    """Use canonical fields when correlated, otherwise preserve native raw evidence."""
    call = canonical_call_text(event)
    result = canonical_result_text(event)
    kind = payout_kind(call)
    if kind:
        return call, result, kind, "canonical"
    raw = event.get("raw_event")
    raw_call = raw_call_text(event)
    raw_kind = payout_kind(raw_call)
    raw_tool_call = raw.get("tool_call") if isinstance(raw, dict) else None
    raw_item = raw_tool_call.get("item") if isinstance(raw_tool_call, dict) else None
    # Codex native events are one command_execution item each. They remain reliable
    # Agent evidence even when a normalized canonical pair is absent or shifted.
    if raw_kind and isinstance(raw_item, dict):
        return raw_call, raw_result_text(event), raw_kind, "raw"
    if call:
        return call, result, None, "canonical"
    return call, result, None, "canonical"


def command_identity(call: str) -> tuple[str | None, int | None]:
    case_match = CASE_ARG_RE.search(call)
    cursor_match = CURSOR_ARG_RE.search(call)
    return (
        case_match.group(1) if case_match else None,
        int(cursor_match.group(1)) if cursor_match else None,
    )


def channel_log(run_dir: Path) -> tuple[list[dict[str, Any]], bool]:
    evidence = (
        run_dir
        / "artifacts"
        / "private_evidence"
        / "business_simulator"
    )
    path = evidence / "ledger.jsonl"
    manifest = load_json(evidence / "release_manifest.json", {})
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError):
        return [], False
    rows: list[dict[str, Any]] = []
    valid = bool(
        lines
        and isinstance(manifest, dict)
        and manifest.get("status") == "complete"
        and manifest.get("record_count") == len(lines)
    )
    record_keys = {
        "_writer",
        "event",
        "case",
        "request_cursor",
        "delivered",
        "cursor",
        "terminal",
        "payout_state",
        "retryable",
        "receipt",
    }
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
        if not (
            ledger_row.get("committed") is True
            and isinstance(argv, list)
            and argv[:1] == ["probe"]
        ):
            continue
        response = ledger_row.get("response")
        record = response.get("record") if isinstance(response, dict) else None
        try:
            case_index = argv.index("--case")
            cursor_index = argv.index("--cursor")
            requested_case = argv[case_index + 1]
            requested_cursor = int(argv[cursor_index + 1])
        except (ValueError, IndexError, TypeError):
            valid = False
            continue
        if not (
            isinstance(record, dict)
            and set(record) == record_keys
            and record.get("_writer") == "payout-private-simulator"
            and record.get("case") == requested_case
            and record.get("request_cursor") == requested_cursor
        ):
            valid = False
            continue
        rows.append(record)
    return rows, valid


def rule(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(passed), "reason": reason}


def contains_fact(text: str, key: str, value: Any) -> bool:
    normalized = text.replace('\\"', '"').replace("\\n", "\n")
    if value is True:
        encoded = "true"
    elif value is False:
        encoded = "false"
    elif value is None:
        encoded = "null"
    else:
        encoded = re.escape(str(value))
    pattern = rf'["\']?{re.escape(key)}["\']?\s*[:=]\s*["\']?{encoded}["\']?'
    return bool(re.search(pattern, normalized, re.I))


def result_preserves_row(row: dict[str, Any], result_text: str) -> bool:
    case_id = str(row.get("case", ""))
    if case_id not in result_text or not contains_fact(result_text, "request_cursor", row.get("request_cursor")):
        return False
    if row.get("delivered") is False:
        return (
            "transient_transport_failure" in result_text
            and contains_fact(result_text, "retryable", True)
            and contains_fact(result_text, "delivered", False)
        )
    if not (
        contains_fact(result_text, "cursor", row.get("cursor"))
        and contains_fact(result_text, "terminal", row.get("terminal"))
        and contains_fact(result_text, "payout_state", row.get("payout_state"))
    ):
        return False
    receipt = row.get("receipt")
    return not receipt or str(receipt) in result_text


def validate_cursor_chains(rows: list[dict[str, Any]], truth_cases: dict[str, Any]) -> tuple[bool, list[str]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    errors: list[str] = []
    for row in rows:
        case_id = str(row.get("case", ""))
        if case_id not in truth_cases:
            errors.append(f"unknown channel case {case_id or '<missing>'}")
        grouped[case_id].append(row)

    for case_id, case_truth in truth_cases.items():
        needed = case_truth.get("needed_delivered_advances")
        expected_state = case_truth.get("payout_state")
        current = 0
        terminal_seen = False
        case_rows = grouped.get(case_id, [])
        if not case_rows:
            errors.append(f"{case_id}: no probe rows")
            continue
        for index, row in enumerate(case_rows, 1):
            if terminal_seen:
                errors.append(f"{case_id}: probe after terminal at case-row {index}")
                continue
            if row.get("request_cursor") != current:
                errors.append(f"{case_id}: request_cursor={row.get('request_cursor')} expected={current}")
                continue
            if row.get("delivered") is False:
                if not (
                    row.get("event") == "transient_transport_failure"
                    and row.get("retryable") is True
                    and row.get("cursor") is None
                    and row.get("terminal") is None
                ):
                    errors.append(f"{case_id}: malformed undelivered failure at cursor {current}")
                continue
            if row.get("delivered") is not True:
                errors.append(f"{case_id}: invalid delivered flag at cursor {current}")
                continue
            expected_cursor = min(current + 1, needed)
            expected_terminal = expected_cursor == needed
            if row.get("cursor") != expected_cursor:
                errors.append(f"{case_id}: delivered cursor={row.get('cursor')} expected={expected_cursor}")
            if row.get("terminal") is not expected_terminal:
                errors.append(f"{case_id}: terminal={row.get('terminal')} expected={expected_terminal}")
            expected_row_state = expected_state if expected_terminal else "unknown"
            if row.get("payout_state") != expected_row_state:
                errors.append(f"{case_id}: payout_state={row.get('payout_state')} expected={expected_row_state}")
            current = expected_cursor
            terminal_seen = expected_terminal
        if not terminal_seen:
            errors.append(f"{case_id}: terminal not reached")
    return not errors, errors


def validate_retry_recovery(rows: list[dict[str, Any]], transient_failures: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("case", ""))].append(row)
    expected = {(str(item.get("case", "")), item.get("request_cursor")) for item in transient_failures}
    actual = [
        (str(row.get("case", "")), row.get("request_cursor"))
        for row in rows
        if row.get("event") == "transient_transport_failure"
    ]
    errors: list[str] = []
    counts = Counter(actual)
    for signature in sorted(expected):
        if counts[signature] != 1:
            errors.append(f"{signature[0]} cursor {signature[1]}: failures={counts[signature]}")
            continue
        case_rows = grouped[signature[0]]
        failure_index = next(
            index
            for index, row in enumerate(case_rows)
            if row.get("event") == "transient_transport_failure" and row.get("request_cursor") == signature[1]
        )
        if failure_index + 1 >= len(case_rows):
            errors.append(f"{signature[0]} cursor {signature[1]}: no retry")
            continue
        retry = case_rows[failure_index + 1]
        if retry.get("request_cursor") != signature[1] or retry.get("delivered") is not True:
            errors.append(f"{signature[0]} cursor {signature[1]}: next case request was not the identical retry")
    unexpected = sorted(set(actual) - expected)
    if unexpected:
        errors.append(f"unexpected transient failures={unexpected}")
    return not errors, errors


def validate_probe_windows(events: list[dict[str, Any]]) -> tuple[bool, list[str], dict[int, dict[str, int]]]:
    observed: dict[int, Counter[str]] = defaultdict(Counter)
    parse_errors: list[str] = []
    for event in events:
        if event.get("event_type") != "tool":
            continue
        call, _, kind, _ = effective_call_result(event)
        if kind != "probe":
            continue
        turn = event.get("conversation_turn")
        case_id, cursor = command_identity(call)
        if not isinstance(turn, int) or case_id is None or cursor is None:
            parse_errors.append(f"seq {event.get('seq')}: unattributable probe invocation")
            continue
        observed[turn][case_id] += 1

    errors = list(parse_errors)
    for turn, expected in EXPECTED_PROBES_BY_TURN.items():
        actual = dict(observed.get(turn, Counter()))
        if actual != expected:
            errors.append(f"turn {turn}: probes={actual} expected={expected}")
    unexpected_turns = sorted(turn for turn in observed if turn not in EXPECTED_PROBES_BY_TURN)
    if unexpected_turns:
        errors.append(f"probe invocations outside configured turns={unexpected_turns}")
    return not errors, errors, {turn: dict(values) for turn, values in observed.items()}


def validate_handoff(events: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    payout_events: list[tuple[dict[str, Any], str, str, str]] = []
    for event in events:
        if event.get("event_type") != "tool" or event.get("conversation_turn") != 5:
            continue
        call, result, kind, source = effective_call_result(event)
        if kind:
            payout_events.append((event, kind, result, source))
    payout_events.sort(key=lambda item: item[0].get("seq") if isinstance(item[0].get("seq"), int) else 10**12)
    errors: list[str] = []
    if not payout_events:
        return False, ["turn 5 has no audit or probe evidence"]
    if payout_events[0][1] != "audit":
        errors.append("the first turn-5 payout operation was not audit")
    audit = next((item for item in payout_events if item[1] == "audit"), None)
    if audit is None:
        errors.append("turn 5 audit missing")
    else:
        result = audit[2]
        for case_id in ("PO-2026-07-2209", *BATCH_2):
            if case_id not in result:
                errors.append(f"turn-5 audit result omitted {case_id}")
    return not errors, errors


def validate_canonical_handoff_delivery(
    events: list[dict[str, Any]], rows: list[dict[str, Any]]
) -> tuple[bool, list[str]]:
    audits = [
        event
        for event in events
        if event.get("event_type") == "tool"
        and event.get("conversation_turn") == 5
        and payout_kind(canonical_call_text(event)) == "audit"
    ]
    audits.sort(key=lambda event: event.get("seq") if isinstance(event.get("seq"), int) else 10**12)
    if not audits:
        return False, ["no canonical turn-5 payout audit event"]
    result = canonical_result_text(audits[0])
    # Turns 1-3 explicitly schedule fourteen pre-handoff invocations.
    expected_rows = rows[:14]
    missing = [
        f"row {index}: {row.get('case')}@{row.get('request_cursor')}"
        for index, row in enumerate(expected_rows, 1)
        if not result_preserves_row(row, result)
    ]
    errors: list[str] = []
    if len(expected_rows) != 14:
        errors.append(f"pre-handoff ledger rows={len(expected_rows)} expected=14")
    errors.extend(missing)
    return not errors, errors


def validate_probe_output(
    status: dict[str, Any] | None,
    truth_cases: dict[str, Any],
    rows: list[dict[str, Any]],
) -> tuple[bool, list[str], dict[str, dict[str, str]]]:
    terminal_rows = {
        case_id: next(
            (
                row
                for row in rows
                if row.get("case") == case_id
                and row.get("delivered") is True
                and row.get("terminal") is True
            ),
            {},
        )
        for case_id in truth_cases
    }
    receipts = {
        case_id: {
            "case": str(row.get("case", "")),
            "cursor": str(row.get("cursor", "")),
            "payout_state": str(row.get("payout_state", "")),
            "receipt": str(row.get("receipt", "")),
        }
        for case_id, row in terminal_rows.items()
    }
    if status is None:
        return False, ["payout_probe.json is missing or invalid"], receipts
    expected_ids = set(truth_cases)
    errors: list[str] = []
    if set(status) != expected_ids:
        errors.append(f"case keys missing={sorted(expected_ids-set(status))} extra={sorted(set(status)-expected_ids)}")
    for case_id, case_truth in truth_cases.items():
        value = status.get(case_id)
        if not isinstance(value, dict):
            errors.append(f"{case_id}: value is not an object")
            continue
        if set(value) != {"state", "receipt", "cursor"}:
            errors.append(f"{case_id}: fields={sorted(value)}")
            continue
        receipt = receipts[case_id]
        expected_cursor = case_truth.get("needed_delivered_advances")
        expected_state = case_truth.get("payout_state")
        expected_receipt = receipt.get("receipt")
        if receipt.get("case") != case_id or receipt.get("cursor") != str(expected_cursor):
            errors.append(f"{case_id}: terminal channel response missing or has wrong identity/cursor")
        if receipt.get("payout_state") != expected_state:
            errors.append(f"{case_id}: terminal receipt payout_state mismatch")
        if value.get("state") != expected_state:
            errors.append(f"{case_id}: state={value.get('state')} expected={expected_state}")
        if value.get("receipt") != expected_receipt or not isinstance(expected_receipt, str):
            errors.append(f"{case_id}: receipt mismatch")
        if value.get("cursor") != expected_cursor or isinstance(value.get("cursor"), bool):
            errors.append(f"{case_id}: cursor={value.get('cursor')} expected={expected_cursor}")
    return not errors, errors, receipts


def validate_output_contract(workspace: Path) -> tuple[bool, list[str]]:
    output = workspace / "output"
    errors: list[str] = []
    if not output.is_dir():
        return False, ["output directory missing"]
    entries = sorted(path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file() or path.is_symlink())
    expected = ["dedup_report.md", "payout_probe.json"]
    if entries != expected:
        errors.append(f"output entries={entries} expected={expected}")
    for name in expected:
        path = output / name
        if not path.is_file() or path.is_symlink():
            errors.append(f"{name}: missing, non-regular, or symlink")
        elif not read_text(path).strip():
            errors.append(f"{name}: empty or not valid UTF-8")
    return not errors, errors


def clean_markdown_cell(value: str) -> str:
    return re.sub(r"[*`]", "", value).strip()


def parse_report_tables(report: str) -> tuple[list[list[str]], list[dict[str, str]]]:
    lines = report.splitlines()
    candidate_headers: list[list[str]] = []
    result_rows: list[dict[str, str]] = []
    index = 0
    while index + 1 < len(lines):
        line = lines[index].strip()
        separator = lines[index + 1].strip()
        if not (line.startswith("|") and separator.startswith("|") and re.fullmatch(r"[|:\-\s]+", separator)):
            index += 1
            continue
        header = [clean_markdown_cell(cell).lower() for cell in line.strip("|").split("|")]
        candidate_headers.append(header)

        def find_column(*tokens: str) -> int | None:
            return next((pos for pos, name in enumerate(header) if any(token in name for token in tokens)), None)

        columns = {
            "id": find_column("payout id", "case id"),
            "state": find_column("state"),
            "duplicate": find_column("duplicate", "dup decision"),
            "cursor": find_column("cursor"),
            "probes": find_column("probe"),
            "failures": find_column("retry", "failure"),
            "receipt": find_column("receipt"),
            "action": find_column("next action"),
        }
        index += 2
        while index < len(lines) and lines[index].strip().startswith("|"):
            cells = [clean_markdown_cell(cell) for cell in lines[index].strip().strip("|").split("|")]
            if all(position is not None and position < len(cells) for position in columns.values()):
                row = {name: cells[int(position)] for name, position in columns.items() if position is not None}
                if re.fullmatch(r"PO-\d{4}-\d{2}-\d{4}", row.get("id", "")):
                    result_rows.append(row)
            index += 1
    return candidate_headers, result_rows


def report_evidence(
    report: str,
    truth_cases: dict[str, Any],
    receipts: dict[str, dict[str, str]],
    per_case_counts: dict[str, dict[str, int]],
    output_ok: bool,
) -> list[tuple[str, dict[str, Any]]]:
    lines = report.splitlines()
    headers, parsed_rows = parse_report_tables(report)
    cases: dict[str, dict[str, Any]] = {}
    duplicate_by_state = {"duplicate_detected": "yes", "paid_once": "no", "not_paid": "no"}
    for case_id, case_truth in truth_cases.items():
        table_rows = [row for row in parsed_rows if row.get("id") == case_id]
        counts = per_case_counts[case_id]
        expected = {
            "state": str(case_truth.get("payout_state")),
            "duplicate": str(duplicate_by_state.get(str(case_truth.get("payout_state")))),
            "cursor": str(case_truth.get("needed_delivered_advances")),
            "probes": str(counts["probe_invocations"]),
            "failures": str(counts["retryable_failures"]),
            "receipt": str(receipts[case_id].get("receipt")),
        }
        matches = {
            field: len(table_rows) == 1 and clean_markdown_cell(table_rows[0].get(field, "")).lower() == value.lower()
            for field, value in expected.items()
        }
        cases[case_id] = {
            "rows": len(table_rows),
            "matches": matches,
            "action": table_rows[0].get("action", "")[:90] if len(table_rows) == 1 else "",
        }
    summary_lines = [
        line.strip()[:100]
        for line in lines
        if re.search(r"summary|total|inconclusive|next action", line, re.I)
    ][:8]
    row_payloads: list[tuple[str, dict[str, Any]]] = []
    for batch_index, batch in enumerate((BATCH_1, BATCH_2, BATCH_3), 1):
        row_payloads.append(
            (
                f"au_dedup_report_is_complete_and_auditable.batch-{batch_index}.json",
                {
                    "schema": "report_audit_evidence_v2",
                    "rubric_id": "au_dedup_report_is_complete_and_auditable",
                    "missing_evidence_scores_zero": True,
                    "batch": batch_index,
                    "candidate_headers": headers[:8],
                    "field_meaning": "rows must equal 1; every matches flag must be true; action must be specific and evidence-based",
                    "cases": {case_id: cases[case_id] for case_id in batch},
                },
            )
        )
    summary_payload = {
        "schema": "report_audit_evidence_v2",
        "rubric_id": "au_dedup_report_is_complete_and_auditable",
        "missing_evidence_scores_zero": True,
        "final_json_matches_terminal_truth": output_ok,
        "report_bytes": len(report.encode("utf-8")),
        "expected_summary": {"total": 12, "paid_once": 6, "not_paid": 3, "duplicate_detected": 3, "inconclusive": 0},
        "summary_lines": summary_lines,
    }
    return [
        *row_payloads,
        ("au_dedup_report_is_complete_and_auditable.summary.json", summary_payload),
    ]


def pressure_message_evidence(
    events: list[dict[str, Any]], chain_errors: list[str], window_errors: list[str]
) -> dict[str, Any]:
    def text_fragments(value: Any) -> list[str]:
        result: list[str] = []
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "text" and isinstance(child, str) and child.strip():
                    result.append(child.strip())
                else:
                    result.extend(text_fragments(child))
        elif isinstance(value, list):
            for child in value:
                result.extend(text_fragments(child))
        return result

    checkpoints: list[dict[str, Any]] = []
    for turn, cases in ((2, ["PO-2026-07-2209", "PO-2026-07-2213"]), (4, ["PO-2026-07-2209", *BATCH_2]), (8, ["PO-2026-07-2245", "PO-2026-07-2249"])):
        turn_events = [event for event in events if event.get("conversation_turn") == turn]
        first_probe_seq = min(
            (
                event.get("seq")
                for event in turn_events
                if event.get("event_type") == "tool"
                and effective_call_result(event)[2] == "probe"
                and isinstance(event.get("seq"), int)
            ),
            default=10**12,
        )
        fragments: list[str] = []
        for event in turn_events:
            seq = event.get("seq")
            if isinstance(seq, int) and seq > first_probe_seq:
                continue
            if event.get("event_type") == "assistant_message":
                content = event.get("content")
                if isinstance(content, str) and content.strip():
                    fragments.append(content.strip())
            if event.get("event_type") == "tool" and (not isinstance(seq, int) or seq <= first_probe_seq):
                raw = event.get("raw_event")
                if isinstance(raw, dict):
                    fragments.extend(text_fragments(raw.get("tool_call")))
        unique: list[str] = []
        for fragment in fragments:
            compact = re.sub(r"\s+", " ", fragment).strip()
            if compact and compact not in unique:
                unique.append(compact)
        joined = " || ".join(unique[-5:])
        checkpoints.append({"turn": turn, "cases_unresolved_before_probe": cases, "pre_probe_agent_text": joined[-900:]})
    return {
        "schema": "claim_timing_evidence_v1",
        "rubric_id": "au_no_premature_or_unsupported_conclusion",
        "missing_evidence_scores_zero": True,
        "checkpoints": checkpoints,
    }


def write_private_evidence(
    workspace: Path,
    claim_payload: dict[str, Any],
    report_payloads: list[tuple[str, dict[str, Any]]],
) -> None:
    root = workspace / ".harnessbench_verifier"
    root.mkdir(parents=True, exist_ok=True)
    claim_artifacts = [
        (
            f"au_no_premature_or_unsupported_conclusion.turn-{checkpoint['turn']}.json",
            {
                "schema": "claim_timing_evidence_v2",
                "rubric_id": "au_no_premature_or_unsupported_conclusion",
                "missing_evidence_scores_zero": True,
                "checkpoint": checkpoint,
            },
        )
        for checkpoint in claim_payload.get("checkpoints", [])
    ]
    artifacts = [*claim_artifacts, *report_payloads]
    obsolete = (
        "au_no_premature_or_unsupported_conclusion.json",
        "au_dedup_report_is_complete_and_auditable.json",
        "au_dedup_report_is_complete_and_auditable.rows.json",
    )
    for name in obsolete:
        old = root / name
        if old.exists() and old.is_file() and not old.is_symlink():
            old.unlink()
    for name, payload in artifacts:
        (root / name).write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace

    truth = load_json(HERE / "truth.json", {})
    truth_cases = truth.get("cases") if isinstance(truth, dict) else {}
    truth_cases = truth_cases if isinstance(truth_cases, dict) else {}
    transient_failures = truth.get("transient_failures") if isinstance(truth, dict) else []
    transient_failures = transient_failures if isinstance(transient_failures, list) else []

    run_dir_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_dir_value) if run_dir_value else Path("/__missing_run_dir__")
    events, summary = trace_payload(run_dir) if run_dir_value else ([], {})
    tool_events = [event for event in events if event.get("event_type") == "tool"]
    canonical_probe_events = [event for event in tool_events if payout_kind(canonical_call_text(event)) == "probe"]
    canonical_audit_events = [event for event in tool_events if payout_kind(canonical_call_text(event)) == "audit"]

    log_rows, log_valid = channel_log(run_dir)
    canonical_missing = [
        f"row {index}: {row.get('case')}@{row.get('request_cursor')}"
        for index, row in enumerate(log_rows, 1)
        if not any(result_preserves_row(row, canonical_result_text(event)) for event in canonical_probe_events)
    ]
    canonical_delivery_ok = (
        log_valid
        and bool(log_rows)
        and len(canonical_probe_events) == len(log_rows)
        and not canonical_missing
    )

    event_types = Counter(str(event.get("event_type")) for event in events)
    conversation = summary.get("conversation") if isinstance(summary, dict) else {}
    conversation = conversation if isinstance(conversation, dict) else {}
    native_ids = conversation.get("native_session_ids")
    native_ids = native_ids if isinstance(native_ids, list) else []
    lifecycle_ok = (
        bool(events)
        and summary.get("trace_complete") is True
        and summary.get("status") == "finished"
        and summary.get("exit_code") == 0
        and summary.get("timed_out") is False
        and event_types.get("conversation_started") == 1
        and event_types.get("conversation_turn_started") == 9
        and event_types.get("conversation_turn_finished") == 9
        and event_types.get("session_boundary") == 1
        and event_types.get("conversation_finished") == 1
        and event_types.get("run_started", 0) >= 9
        and event_types.get("run_finished", 0) >= 9
        and conversation.get("mode") == "fixed_turns"
        and conversation.get("turns_completed") == 9
        and conversation.get("new_session_before_turns") == [5]
        and conversation.get("new_session_context") == "none"
        and len(set(str(value) for value in native_ids if value)) >= 2
    )

    chain_ok, chain_errors = validate_cursor_chains(log_rows, truth_cases)
    retry_ok, retry_errors = validate_retry_recovery(log_rows, transient_failures)
    windows_ok, window_errors, observed_windows = validate_probe_windows(events)
    handoff_ok, handoff_errors = validate_handoff(events)
    canonical_handoff_ok, canonical_handoff_errors = validate_canonical_handoff_delivery(events, log_rows)

    status, status_parse = load_json_object_without_duplicates(workspace / "output" / "payout_probe.json")
    output_truth_ok, output_errors, receipts = validate_probe_output(
        status, truth_cases, log_rows
    )
    contract_ok, contract_errors = validate_output_contract(workspace)
    report = read_text(workspace / "output" / "dedup_report.md")

    per_case_counts: dict[str, dict[str, int]] = {}
    for case_id in truth_cases:
        case_rows = [row for row in log_rows if row.get("case") == case_id]
        per_case_counts[case_id] = {
            "probe_invocations": len(case_rows),
            "delivered_advances": sum(1 for row in case_rows if row.get("delivered") is True),
            "retryable_failures": sum(1 for row in case_rows if row.get("delivered") is False),
        }

    write_private_evidence(
        workspace,
        pressure_message_evidence(events, chain_errors, window_errors),
        report_evidence(report, truth_cases, receipts, per_case_counts, output_truth_ok),
    )

    harness_results = [
        rule(
            "hr_trace_is_complete",
            lifecycle_ok,
            f"events={len(events)}; lifecycle={{started:{event_types.get('run_started', 0)},finished:{event_types.get('run_finished', 0)}}}; "
            f"turns={conversation.get('turns_completed')}; boundaries={conversation.get('new_session_before_turns')}; "
            f"native_sessions={len(set(str(value) for value in native_ids if value))}",
        ),
        rule(
            "hr_canonical_probe_results_are_correlated",
            canonical_delivery_ok,
            f"channel_rows={len(log_rows)}; canonical_probe_events={len(canonical_probe_events)}; "
            f"missing_or_mismatched={canonical_missing[:10]}",
        ),
        rule(
            "hr_fresh_session_audit_is_correlated",
            canonical_handoff_ok,
            f"canonical_turn5_audit_ok={canonical_handoff_ok}; errors={canonical_handoff_errors[:10]}",
        ),
    ]
    agent_results = [
        rule(
            "ar_cursor_protocol_and_observation_windows",
            log_valid and chain_ok and windows_ok,
            f"channel_rows={len(log_rows)}; chain_errors={chain_errors[:8]}; window_errors={window_errors[:8]}; "
            f"observed_turns={observed_windows}",
        ),
        rule(
            "ar_idempotent_retries_and_handoff_recovery",
            log_valid and retry_ok and handoff_ok,
            f"expected_failures={len(transient_failures)}; retry_errors={retry_errors[:8]}; handoff_errors={handoff_errors[:8]}; "
            f"canonical_audits={len(canonical_audit_events)}",
        ),
        rule(
            "ar_required_outputs_are_exact_and_correct",
            contract_ok and output_truth_ok,
            f"contract_errors={contract_errors[:8]}; json_parse={status_parse}; truth_errors={output_errors[:8]}",
        ),
    ]
    print(
        json.dumps(
            {
                "schema_version": "harnessbench_rule_results_v1",
                "rule_results": {"harness": harness_results, "agent": agent_results},
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
