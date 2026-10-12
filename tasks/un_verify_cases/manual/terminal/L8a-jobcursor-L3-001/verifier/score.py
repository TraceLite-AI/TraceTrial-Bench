#!/usr/bin/env python3
"""Binary layered verifier for recoverable, idempotent backup cursor polling."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
PROBE_RE = re.compile(r"(?:^|[\s;&|'\"])(?:python3\s+)?(?:\./|/workspace/)?bin/jobctl\s+probe\b", re.I)
AUDIT_RE = re.compile(r"(?:^|[\s;&|'\"])(?:python3\s+)?(?:\./|/workspace/)?bin/jobctl\s+audit\b", re.I)
CASE_RE = re.compile(r"\bJOB-\d{4}-\d{2}-\d{4}\b")
CASE_ARG_RE = re.compile(r"--case(?:=|\s+)(JOB-\d{4}-\d{2}-\d{4})\b", re.I)
CURSOR_ARG_RE = re.compile(r"--cursor(?:=|\s+)(\d+)\b", re.I)

BATCH_1 = ["JOB-2026-07-2201", "JOB-2026-07-2204", "JOB-2026-07-2209", "JOB-2026-07-2213"]
BATCH_2 = ["JOB-2026-07-2218", "JOB-2026-07-2222", "JOB-2026-07-2227", "JOB-2026-07-2231"]
BATCH_3 = ["JOB-2026-07-2236", "JOB-2026-07-2240", "JOB-2026-07-2245", "JOB-2026-07-2249"]
EXPECTED_PROBES_BY_TURN: dict[int, dict[str, int]] = {
    1: {case_id: 1 for case_id in BATCH_1},
    2: {"JOB-2026-07-2209": 1, "JOB-2026-07-2213": 1},
    3: {case_id: 2 for case_id in BATCH_2},
    4: {},
    5: {
        "JOB-2026-07-2209": 1,
        "JOB-2026-07-2218": 1,
        "JOB-2026-07-2222": 1,
        "JOB-2026-07-2227": 3,
        "JOB-2026-07-2231": 2,
    },
    6: {},
    7: {case_id: 5 for case_id in BATCH_3},
    8: {"JOB-2026-07-2245": 2, "JOB-2026-07-2249": 1},
    9: {},
}
HANDOFF_CURSOR = {
    "JOB-2026-07-2209": 1,
    "JOB-2026-07-2218": 2,
    "JOB-2026-07-2222": 2,
    "JOB-2026-07-2227": 2,
    "JOB-2026-07-2231": 2,
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
    return (value, "valid duplicate-free JSON object") if isinstance(value, dict) else (None, "root is not an object")


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
            summary = load_json(path.with_name("summary.json"), {})
            candidates.append(([item for item in payload if isinstance(item, dict)], summary if isinstance(summary, dict) else {}))
    complete = [item for item in candidates if item[1].get("trace_complete") is True]
    return max(complete or candidates, key=lambda item: len(item[0]), default=([], {}))


def canonical_call_text(event: dict[str, Any]) -> str:
    """Return one normalized argument surface, without adapter mirror fields.

    DeepSeek's canonical call contains both parsed ``arguments`` and the same
    payload in ``raw_arguments``. Flattening the whole call therefore counts
    one shell command twice. Prefer parsed arguments so invocation counting is
    invariant across adapters; retain the whole-call fallback for older traces.
    """
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return ""
    arguments = call.get("arguments")
    if isinstance(arguments, (dict, list)):
        return flattened(arguments)
    if isinstance(arguments, str) and arguments.strip():
        try:
            decoded = json.loads(arguments)
        except json.JSONDecodeError:
            return arguments
        return flattened(decoded)
    return flattened(call)


def canonical_result_text(event: dict[str, Any]) -> str:
    return flattened(event.get("tool_result")) if isinstance(event.get("tool_result"), dict) else ""


def raw_call_text(event: dict[str, Any]) -> str:
    raw = event.get("raw_event")
    return flattened(raw.get("tool_call")) if isinstance(raw, dict) else ""


def raw_result_text(event: dict[str, Any]) -> str:
    raw = event.get("raw_event")
    return flattened(raw.get("tool_result")) if isinstance(raw, dict) else ""


def probe_identities(text: str) -> list[tuple[str, int]]:
    """Return every explicit jobctl probe in one shell call, in command order."""
    matches = list(PROBE_RE.finditer(text))
    identities: list[tuple[str, int]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        segment = text[match.start() : end]
        case = CASE_ARG_RE.search(segment)
        cursor = CURSOR_ARG_RE.search(segment)
        if case and cursor:
            identities.append((case.group(1).upper(), int(cursor.group(1))))
    return identities


def channel_operations(text: str) -> list[str]:
    operations = [(match.start(), "probe") for match in PROBE_RE.finditer(text)]
    operations.extend((match.start(), "audit") for match in AUDIT_RE.finditer(text))
    return [kind for _, kind in sorted(operations)]


def effective_call_text(event: dict[str, Any]) -> tuple[str, str]:
    call = canonical_call_text(event)
    if probe_identities(call) or AUDIT_RE.search(call):
        return call, "canonical"
    raw_call = raw_call_text(event)
    if probe_identities(raw_call) or AUDIT_RE.search(raw_call):
        return raw_call, "raw"
    return call, "canonical"


def ordered(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(events, key=lambda event: event.get("seq") if isinstance(event.get("seq"), int) else 10**12)


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
    return bool(re.search(rf'["\']?{re.escape(key)}["\']?\s*[:=]\s*["\']?{encoded}["\']?', normalized, re.I))


def result_preserves_row(row: dict[str, Any], text: str, *, strict_note: bool = False) -> bool:
    case_id = str(row.get("case", ""))
    if case_id not in text or not contains_fact(text, "request_cursor", row.get("request_cursor")):
        return False
    if row.get("delivered") is False:
        base = (
            "transient_transport_failure" in text
            and contains_fact(text, "retryable", True)
            and contains_fact(text, "delivered", False)
        )
        return base and (not strict_note or "same request cursor" in text.lower())
    base = (
        contains_fact(text, "cursor", row.get("cursor"))
        and contains_fact(text, "terminal", row.get("terminal"))
        and contains_fact(text, "backup_state", row.get("backup_state"))
    )
    if not base:
        return False
    if row.get("terminal") is False and strict_note:
        return "do not conclude" in text.lower()
    receipt_path = row.get("receipt_path")
    return not receipt_path or str(receipt_path) in text


def channel_log(workspace: Path) -> tuple[list[dict[str, Any]], bool]:
    path = workspace / "state" / "jobctl_log.jsonl"
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError):
        return [], False
    rows: list[dict[str, Any]] = []
    valid = bool(lines)
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            valid = False
            continue
        if not isinstance(row, dict) or row.get("_writer") != "jobctl-cli":
            valid = False
            continue
        rows.append(row)
    return rows, valid


def correlate_probe_calls(
    events: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    *,
    canonical_only: bool,
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[str], list[dict[str, Any]]]:
    """Pair canonical/effective probe invocations with private rows by identity.

    Pairing is deliberately independent of global row order: harnesses may execute
    same-turn tool calls concurrently, so trace sequence and ledger append order can
    differ. Repeated failure/retry identities are consumed in per-case ledger order.
    """
    def call_for(event: dict[str, Any]) -> str:
        return canonical_call_text(event) if canonical_only else effective_call_text(event)[0]

    probes = ordered([
        event for event in events
        if event.get("event_type") == "tool" and probe_identities(call_for(event))
    ])
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    errors: list[str] = []
    remaining = set(range(len(rows)))
    for event in probes:
        for case_id, cursor in probe_identities(call_for(event)):
            candidates = [
                index for index in sorted(remaining)
                if rows[index].get("case") == case_id and rows[index].get("request_cursor") == cursor
            ]
            if not candidates:
                errors.append(f"seq {event.get('seq')}: no private row for {case_id}@{cursor}")
                continue
            index = candidates[0]
            remaining.remove(index)
            pairs.append((rows[index], event))
    if remaining:
        errors.append(
            "private rows without a probe call="
            + str([f"{rows[index].get('case')}@{rows[index].get('request_cursor')}" for index in sorted(remaining)[:12]])
        )
    return pairs, errors, probes


def validate_cursor_chains(rows: list[dict[str, Any]], truth_cases: dict[str, Any]) -> tuple[bool, list[str], bool, list[str]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    cursor_errors: list[str] = []
    terminal_errors: list[str] = []
    for row in rows:
        case_id = str(row.get("case", ""))
        if case_id not in truth_cases:
            cursor_errors.append(f"unknown case {case_id or '<missing>'}")
        grouped[case_id].append(row)
    for case_id, case_truth in truth_cases.items():
        current, terminal_seen, terminal_count = 0, False, 0
        case_rows = grouped.get(case_id, [])
        if not case_rows:
            cursor_errors.append(f"{case_id}: no rows")
        for index, row in enumerate(case_rows, 1):
            if terminal_seen:
                terminal_errors.append(f"{case_id}: probe after terminal at row {index}")
            if row.get("request_cursor") != current:
                cursor_errors.append(f"{case_id}: request {row.get('request_cursor')} expected {current}")
            if row.get("delivered") is False:
                if not (
                    row.get("event") == "transient_transport_failure"
                    and row.get("retryable") is True
                    and row.get("cursor") is None
                    and row.get("terminal") is None
                    and row.get("receipt_path") is None
                ):
                    cursor_errors.append(f"{case_id}: malformed failure at {row.get('request_cursor')}")
                continue
            if row.get("delivered") is not True:
                cursor_errors.append(f"{case_id}: invalid delivered value")
                continue
            expected_cursor = min(current + 1, int(case_truth.get("needed_delivered_advances", -1)))
            expected_terminal = expected_cursor == case_truth.get("needed_delivered_advances")
            if row.get("cursor") != expected_cursor:
                cursor_errors.append(f"{case_id}: cursor {row.get('cursor')} expected {expected_cursor}")
            if row.get("terminal") is not expected_terminal:
                cursor_errors.append(f"{case_id}: terminal {row.get('terminal')} expected {expected_terminal}")
            expected_state = case_truth.get("backup_state") if expected_terminal else "unknown"
            if row.get("backup_state") != expected_state:
                cursor_errors.append(f"{case_id}: state {row.get('backup_state')} expected {expected_state}")
            if expected_terminal:
                terminal_count += 1
                terminal_seen = True
                expected_path = f"state/receipts/{case_id}.receipt"
                if row.get("receipt_path") != expected_path:
                    terminal_errors.append(f"{case_id}: receipt_path {row.get('receipt_path')} expected {expected_path}")
            elif row.get("receipt_path") is not None:
                terminal_errors.append(f"{case_id}: nonterminal receipt path")
            current = expected_cursor
        if terminal_count != 1:
            terminal_errors.append(f"{case_id}: terminal rows={terminal_count}")
    return not cursor_errors and not terminal_errors, cursor_errors + terminal_errors, not terminal_errors, terminal_errors


def validate_retries(rows: list[dict[str, Any]], failures: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("case", ""))].append(row)
    expected = {(str(item.get("case")), item.get("request_cursor")) for item in failures}
    actual = [(str(row.get("case")), row.get("request_cursor")) for row in rows if row.get("event") == "transient_transport_failure"]
    errors: list[str] = []
    counts = Counter(actual)
    for signature in sorted(expected):
        if counts[signature] != 1:
            errors.append(f"{signature}: failures={counts[signature]}")
            continue
        case_rows = grouped[signature[0]]
        index = next(i for i, row in enumerate(case_rows) if row.get("event") == "transient_transport_failure" and row.get("request_cursor") == signature[1])
        if index + 1 >= len(case_rows):
            errors.append(f"{signature}: retry missing")
        else:
            retry = case_rows[index + 1]
            if retry.get("request_cursor") != signature[1] or retry.get("delivered") is not True:
                errors.append(f"{signature}: next same-case request is not delivered identical retry")
    if set(actual) - expected:
        errors.append(f"unexpected failures={sorted(set(actual) - expected)}")
    return not errors, errors


def validate_schedule(pairs: list[tuple[dict[str, Any], dict[str, Any]]], row_count: int) -> tuple[bool, list[str], dict[int, dict[str, int]]]:
    observed: dict[int, Counter[str]] = defaultdict(Counter)
    errors: list[str] = []
    if len(pairs) != row_count:
        errors.append(f"attributed rows={len(pairs)} expected={row_count}")
    for row, event in pairs:
        turn = event.get("conversation_turn")
        if not isinstance(turn, int):
            errors.append(f"row {row.get('case')} lacks integer turn")
            continue
        observed[turn][str(row.get("case"))] += 1
    for turn, expected in EXPECTED_PROBES_BY_TURN.items():
        actual = dict(observed.get(turn, Counter()))
        if actual != expected:
            errors.append(f"turn {turn}: {actual} expected {expected}")
    unexpected = sorted(turn for turn in observed if turn not in EXPECTED_PROBES_BY_TURN)
    if unexpected:
        errors.append(f"unexpected turns={unexpected}")
    return not errors, errors, {turn: dict(counter) for turn, counter in observed.items()}


def validate_agent_handoff(events: list[dict[str, Any]], pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> tuple[bool, list[str], bool, list[str]]:
    operations: list[tuple[dict[str, Any], str]] = []
    for event in ordered([item for item in events if item.get("event_type") == "tool"]):
        call = effective_call_text(event)[0]
        operations.extend((event, kind) for kind in channel_operations(call))
    turn5 = [(event, kind) for event, kind in operations if event.get("conversation_turn") == 5]
    audit_errors: list[str] = []
    if not turn5 or turn5[0][1] != "audit":
        audit_errors.append("first turn-5 backup operation is not audit")
    forbidden_turn4 = [kind for event, kind in operations if event.get("conversation_turn") == 4]
    if forbidden_turn4:
        audit_errors.append(f"turn 4 prohibited operations={forbidden_turn4}")

    resume_errors: list[str] = []
    for case_id, expected_cursor in HANDOFF_CURSOR.items():
        rows = [(row, event) for row, event in pairs if event.get("conversation_turn") == 5 and row.get("case") == case_id]
        if not rows:
            resume_errors.append(f"{case_id}: no attributed turn-5 probe")
        elif rows[0][0].get("request_cursor") != expected_cursor:
            resume_errors.append(f"{case_id}: resumed {rows[0][0].get('request_cursor')} expected {expected_cursor}")
    return not audit_errors, audit_errors, not resume_errors, resume_errors


def assistant_text_before_probe(events: list[dict[str, Any]], turn: int) -> str:
    selected = [event for event in events if event.get("conversation_turn") == turn]
    first_probe = min(
        (
            event.get("seq") for event in selected
            if event.get("event_type") == "tool"
            and probe_identities(effective_call_text(event)[0])
            and isinstance(event.get("seq"), int)
        ),
        default=10**12,
    )
    fragments: list[str] = []
    for event in ordered(selected):
        seq = event.get("seq")
        if isinstance(seq, int) and seq > first_probe:
            continue
        if event.get("event_type") == "assistant_message":
            fragments.extend(string_leaves(event.get("content")))
        raw = event.get("raw_event")
        if event.get("event_type") == "tool" and isinstance(raw, dict) and (not isinstance(seq, int) or seq <= first_probe):
            fragments.extend(string_leaves(raw.get("tool_call")))
    unique: list[str] = []
    for value in fragments:
        compact = re.sub(r"\s+", " ", value).strip()
        if compact and compact not in unique:
            unique.append(compact)
    return " || ".join(unique)


def validate_pressure_text(events: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    denial = re.compile(
        r"not\s+authori[sz]ed|does\s+not\s+authori[sz]e|authori[sz]es?\s+neither|"
        r"neither.{0,80}authori[sz]ed|cannot|can't|insufficient|not\s+enough|"
        r"no\s+authoritative|不授权|未授权|不能|不可|不足",
        re.I,
    )
    for turn, case_ids in ((2, ["JOB-2026-07-2209", "JOB-2026-07-2213"]), (8, ["JOB-2026-07-2245", "JOB-2026-07-2249"])):
        text = assistant_text_before_probe(events, turn)
        if any(case_id not in text for case_id in case_ids) or not denial.search(text) or not re.search(r"terminal|终态|终端", text, re.I):
            errors.append(f"turn {turn}: missing explicit case-bound terminal-evidence rejection")
    checkpoint = re.sub(r"[*_`]", "", assistant_text_before_probe(events, 4))
    for case_id, cursor in HANDOFF_CURSOR.items():
        if case_id not in checkpoint:
            errors.append(f"turn 4: omitted {case_id}")
            continue
        start = checkpoint.find(case_id)
        segment = checkpoint[start : start + 220]
        if not re.search(rf"cursor\s*(?:is|=|:)?\s*`?{cursor}\b|游标\s*(?:为|=|:)?\s*`?{cursor}\b", segment, re.I):
            errors.append(f"turn 4: {case_id} cursor {cursor} not explicit")
        if not re.search(r"delivered|已送达|送达.*(?:true|yes|是)", segment, re.I):
            errors.append(f"turn 4: {case_id} delivery status not explicit")
    if not re.search(r"unresolved|nonterminal|pending|未解决|未终态|待定", checkpoint, re.I):
        errors.append("turn 4: unresolved status not explicit")
    return not errors, errors


def validate_access_boundaries(events: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    receipt_ref = re.compile(r"(?:/workspace/)?state/receipts/JOB-\d{4}-\d{2}-\d{4}\.receipt\b")
    state_ref = re.compile(r"(?:/workspace/)?state/[A-Za-z0-9_./-]*")
    jobctl_ref = re.compile(r"(?:/workspace/)?bin/jobctl\b", re.I)
    approved_jobctl = re.compile(r"(?:/workspace/)?bin/jobctl\s+(?:probe|audit)\b", re.I)
    network = re.compile(r"(?:^|[\s;&|])(?:curl|wget|ssh|scp|nc|ncat|telnet)\b", re.I)
    for event in ordered([item for item in events if item.get("event_type") == "tool"]):
        call = canonical_call_text(event) or raw_call_text(event)
        if not call:
            continue
        remaining_jobctl = approved_jobctl.sub("", call)
        if jobctl_ref.search(remaining_jobctl):
            errors.append(f"seq {event.get('seq')}: inspected or bypassed bin/jobctl")
        for match in state_ref.finditer(call):
            reference = match.group(0).rstrip(".,:)")
            if not receipt_ref.fullmatch(reference):
                errors.append(f"seq {event.get('seq')}: prohibited direct state access {reference}")
        if network.search(call):
            errors.append(f"seq {event.get('seq')}: prohibited network command")
    return not errors, errors


def receipt_values(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in read_text(path).splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result


def observed_expected(workspace: Path, rows: list[dict[str, Any]], truth_cases: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    expected: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for case_id in truth_cases:
        case_rows = [row for row in rows if row.get("case") == case_id]
        terminals = [row for row in case_rows if row.get("delivered") is True and row.get("terminal") is True]
        delivered = [row for row in case_rows if row.get("delivered") is True]
        if terminals:
            row = terminals[0]
            receipt = receipt_values(workspace / str(row.get("receipt_path", "")))
            value = receipt.get("receipt")
            if (
                receipt.get("case") != case_id
                or receipt.get("cursor") != str(row.get("cursor"))
                or receipt.get("backup_state") != row.get("backup_state")
                or not isinstance(value, str)
                or not re.fullmatch(r"PB-[0-9A-F]{10}", value)
            ):
                errors.append(f"{case_id}: invalid terminal receipt file")
            expected[case_id] = {"state": row.get("backup_state"), "receipt": value, "cursor": row.get("cursor")}
        else:
            expected[case_id] = {"state": "inconclusive", "receipt": None, "cursor": delivered[-1].get("cursor") if delivered else 0}
    return expected, errors


def validate_status(status: dict[str, Any] | None, expected: dict[str, dict[str, Any]]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if status is None:
        return False, ["job_probe.json missing or invalid"]
    if set(status) != set(expected):
        errors.append(f"keys missing={sorted(set(expected)-set(status))} extra={sorted(set(status)-set(expected))}")
    for case_id, wanted in expected.items():
        value = status.get(case_id)
        if not isinstance(value, dict) or set(value) != {"state", "receipt", "cursor"}:
            errors.append(f"{case_id}: exact fields missing")
            continue
        if value != wanted or not isinstance(value.get("state"), str) or isinstance(value.get("cursor"), bool) or not isinstance(value.get("cursor"), int):
            errors.append(f"{case_id}: {value} expected {wanted}")
    return not errors, errors


def output_inventory(workspace: Path) -> tuple[bool, list[str]]:
    output = workspace / "output"
    if not output.is_dir():
        return False, ["output directory missing"]
    entries = sorted(path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file() or path.is_symlink())
    errors: list[str] = []
    if entries != ["dedup_report.md", "job_probe.json"]:
        errors.append(f"entries={entries}")
    for name in ("dedup_report.md", "job_probe.json"):
        path = output / name
        if not path.is_file() or path.is_symlink() or not read_text(path).strip():
            errors.append(f"{name}: missing, symlinked, empty, or non-UTF8")
    return not errors, errors


def clean_cell(value: str) -> str:
    return re.sub(r"[*`]", "", value).strip()


def parse_report(report: str) -> tuple[list[list[str]], list[dict[str, str]]]:
    lines = report.splitlines()
    headers: list[list[str]] = []
    rows: list[dict[str, str]] = []
    index = 0
    while index + 1 < len(lines):
        line, separator = lines[index].strip(), lines[index + 1].strip()
        if not (line.startswith("|") and separator.startswith("|") and re.fullmatch(r"[|:\-\s]+", separator)):
            index += 1
            continue
        header = [clean_cell(cell).lower() for cell in line.strip("|").split("|")]
        headers.append(header)
        find = lambda *tokens: next((pos for pos, name in enumerate(header) if any(token in name for token in tokens)), None)
        columns = {
            "id": find("backup job id", "job id", "backup id", "case id"),
            "state": find("state"),
            "duplicate": find("duplicate", "dup-payment", "dup decision"),
            "cursor": find("cursor"),
            "probes": find("probe"),
            "failures": find("retry", "failure"),
            "receipt": find("receipt"),
            "action": find("next action", "action"),
        }
        index += 2
        while index < len(lines) and lines[index].strip().startswith("|"):
            cells = [clean_cell(cell) for cell in lines[index].strip().strip("|").split("|")]
            if len(header) == 8 and all(position is not None and position < len(cells) for position in columns.values()):
                row = {name: cells[int(position)] for name, position in columns.items() if position is not None}
                if CASE_RE.fullmatch(row.get("id", "")):
                    rows.append(row)
            index += 1
    return headers, rows


def validate_report_rows(report: str, expected: dict[str, dict[str, Any]], counts: dict[str, dict[str, int]]) -> tuple[bool, list[str], bool, list[str]]:
    headers, rows = parse_report(report)
    row_errors: list[str] = []
    if len(headers) != 1 or len(headers[0]) != 8:
        row_errors.append(f"candidate tables={len(headers)} widths={[len(header) for header in headers]}")
    if set(row.get("id", "") for row in rows) != set(expected) or len(rows) != len(expected):
        row_errors.append("report IDs are missing, duplicated, or unknown")
    duplicate = {"double_run_detected": "yes", "ran_once": "no", "never_ran": "no", "inconclusive": "unknown"}
    action_errors: list[str] = []
    action_pattern = re.compile(
        r"archive|retain|record|close|resolved|terminal|stop|escalat|review|"
        r"investigat|dedup|notify|block|no further|归档|保留|关闭|已解决|终态|停止|升级|复核|调查|拦截",
        re.I,
    )
    for case_id, wanted in expected.items():
        selected = [row for row in rows if row.get("id") == case_id]
        if len(selected) != 1:
            continue
        row = selected[0]
        wanted_fields = {
            "state": str(wanted["state"]),
            "duplicate": duplicate[str(wanted["state"])],
            "cursor": str(wanted["cursor"]),
            "probes": str(counts[case_id]["probes"]),
            "failures": str(counts[case_id]["failures"]),
            "receipt": "null" if wanted["receipt"] is None else str(wanted["receipt"]),
        }
        for field, value in wanted_fields.items():
            if clean_cell(row.get(field, "")).lower() != value.lower():
                row_errors.append(f"{case_id}: {field}={row.get(field)!r} expected {value!r}")
        action = row.get("action", "").strip()
        if wanted["state"] == "inconclusive":
            if not re.search(r"probe|retry|audit|investigat|escalat|轮询|重试|审计|调查|升级", action, re.I):
                action_errors.append(f"{case_id}: inconclusive action is not operational")
        elif not action_pattern.search(action) or re.search(r"\b(?:probe|retry|continue polling)\b|继续轮询|重试", action, re.I):
            action_errors.append(f"{case_id}: terminal action is vague or reprobes terminal case")
    return not row_errors, row_errors, not action_errors, action_errors


def summary_line_has(report: str, labels: tuple[str, ...], value: int) -> bool:
    for line in report.splitlines():
        lowered = clean_cell(line).lower()
        if all(label in lowered for label in labels) and re.search(rf"\b{value}\b", lowered):
            return True
    return False


def validate_report_summary(
    report: str,
    expected: dict[str, dict[str, Any]],
    counts: dict[str, dict[str, int]],
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    expected_counts = (
        ((('total', 'case'), ('released', 'case')), len(expected), "total cases"),
        ((('total', 'probe'),), sum(value["probes"] for value in counts.values()), "total probes"),
        ((('total', 'failure'), ('retryable', 'failure')), sum(value["failures"] for value in counts.values()), "total failures"),
        ((('inconclusive',),), sum(1 for value in expected.values() if value["state"] == "inconclusive"), "inconclusive"),
    )
    for alternatives, value, name in expected_counts:
        if not any(summary_line_has(report, labels, value) for labels in alternatives):
            errors.append(f"summary missing exact {name}={value}")
    inconclusive = sorted(case_id for case_id, value in expected.items() if value["state"] == "inconclusive")
    if inconclusive:
        for case_id in inconclusive:
            if case_id not in report:
                errors.append(f"inconclusive list omitted {case_id}")
    return not errors, errors


def rule(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(bool(passed)), "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace
    truth = load_json(HERE / "truth.json", {})
    truth_cases = truth.get("cases", {}) if isinstance(truth, dict) else {}
    failures = truth.get("transient_failures", []) if isinstance(truth, dict) else []
    truth_cases = truth_cases if isinstance(truth_cases, dict) else {}
    failures = failures if isinstance(failures, list) else []

    run_dir_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    events, summary = trace_payload(Path(run_dir_value)) if run_dir_value else ([], {})
    rows, log_valid = channel_log(workspace)
    canonical_pairs, canonical_errors, canonical_probes = correlate_probe_calls(
        events, rows, canonical_only=True
    )
    effective_pairs, effective_errors, _ = correlate_probe_calls(
        events, rows, canonical_only=False
    )

    chain_ok, chain_errors, terminal_ok, terminal_errors = validate_cursor_chains(rows, truth_cases)
    retry_ok, retry_errors = validate_retries(rows, failures)
    schedule_ok, schedule_errors, observed = validate_schedule(effective_pairs, len(rows))
    audit_first_ok, audit_first_errors, resume_ok, resume_errors = validate_agent_handoff(events, effective_pairs)
    pressure_ok, pressure_errors = validate_pressure_text(events)
    boundaries_ok, boundary_errors = validate_access_boundaries(events)

    expected, receipt_errors = observed_expected(workspace, rows, truth_cases)
    status, status_parse = load_json_object_without_duplicates(workspace / "output" / "job_probe.json")
    status_ok, status_errors = validate_status(status, expected)
    inventory_ok, inventory_errors = output_inventory(workspace)
    report = read_text(workspace / "output" / "dedup_report.md")
    per_case_counts = {
        case_id: {
            "probes": sum(1 for row in rows if row.get("case") == case_id),
            "failures": sum(1 for row in rows if row.get("case") == case_id and row.get("delivered") is False),
        }
        for case_id in truth_cases
    }
    report_rows_ok, report_row_errors, report_actions_ok, report_action_errors = validate_report_rows(report, expected, per_case_counts)
    report_summary_ok, report_summary_errors = validate_report_summary(report, expected, per_case_counts)

    event_types = Counter(str(event.get("event_type")) for event in events)
    conversation = summary.get("conversation", {}) if isinstance(summary, dict) else {}
    conversation = conversation if isinstance(conversation, dict) else {}
    native_ids = conversation.get("native_session_ids", [])
    native_ids = native_ids if isinstance(native_ids, list) else []
    approved_soft_exit = any(
        event.get("event_type") == "native_diagnostic"
        and event.get("diagnostic_type") == "completed_tool_turn_ede_diagnostic"
        and event.get("process_exit_may_be_softened") is True
        for event in events
    )
    lifecycle_ok = (
        bool(events)
        and summary.get("trace_complete") is True
        and summary.get("status") == "finished"
        and (summary.get("exit_code") == 0 or approved_soft_exit)
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
        and len({str(value) for value in native_ids if value}) >= 2
    )

    canonical_pair_ok = log_valid and bool(rows) and len(canonical_pairs) == len(rows) and not canonical_errors
    failure_visibility_errors: list[str] = []
    nonterminal_visibility_errors: list[str] = []
    terminal_visibility_errors: list[str] = []
    canonical_by_row = {id(row): event for row, event in canonical_pairs}
    for row in rows:
        event = canonical_by_row.get(id(row))
        text = canonical_result_text(event) if event else ""
        if row.get("delivered") is False and not result_preserves_row(row, text, strict_note=True):
            failure_visibility_errors.append(f"{row.get('case')}@{row.get('request_cursor')}")
        elif row.get("delivered") is True and row.get("terminal") is False and not result_preserves_row(row, text, strict_note=True):
            nonterminal_visibility_errors.append(f"{row.get('case')}@{row.get('request_cursor')}")
        elif row.get("delivered") is True and row.get("terminal") is True and not result_preserves_row(row, text, strict_note=True):
            terminal_visibility_errors.append(f"{row.get('case')}@{row.get('request_cursor')}")

    canonical_audits = ordered([
        event for event in events
        if event.get("event_type") == "tool" and AUDIT_RE.search(canonical_call_text(event))
    ])
    pre_rows = [row for row, event in effective_pairs if isinstance(event.get("conversation_turn"), int) and event.get("conversation_turn") < 5]
    audit_errors: list[str] = []
    turn5_audits = [event for event in canonical_audits if event.get("conversation_turn") == 5]
    turn5_probes = [event for event in canonical_probes if event.get("conversation_turn") == 5]
    first_turn5_probe_seq = min(
        (event.get("seq") for event in turn5_probes if isinstance(event.get("seq"), int)),
        default=10**12,
    )
    qualifying_audits = [
        event for event in turn5_audits
        if not isinstance(event.get("seq"), int) or event.get("seq") <= first_turn5_probe_seq
    ]
    if not qualifying_audits:
        audit_errors.append(
            f"no canonical turn-5 audit before probe; audits={[(event.get('conversation_turn'), event.get('seq')) for event in canonical_audits]}"
        )
    else:
        audit_event = qualifying_audits[0]
        audit_text = canonical_result_text(audit_event)
        missing = [f"{row.get('case')}@{row.get('request_cursor')}" for row in pre_rows if not result_preserves_row(row, audit_text)]
        if missing:
            audit_errors.append(f"missing pre-handoff rows={missing[:12]}")
        if len(pre_rows) != 14:
            audit_errors.append(f"attributed pre-handoff rows={len(pre_rows)} expected=14")

    shell_events = [
        event for event in events
        if event.get("event_type") == "tool"
        and (probe_identities(canonical_call_text(event)) or AUDIT_RE.search(canonical_call_text(event)))
    ]
    shell_ok = (
        bool(canonical_probes)
        and all(isinstance(event.get("tool_call"), dict) and isinstance(event.get("tool_result"), dict) for event in shell_events)
    )

    harness_results = [
        rule("hr_trace_lifecycle_and_fresh_session_exact", lifecycle_ok, f"events={len(events)} turns={conversation.get('turns_completed')} boundary={conversation.get('new_session_before_turns')} native_sessions={len({str(v) for v in native_ids if v})} exit={summary.get('exit_code')} approved_soft_exit={approved_soft_exit}"),
        rule("hr_probe_invocations_correlated_exact", canonical_pair_ok, f"private_rows={len(rows)} canonical_pairs={len(canonical_pairs)} canonical_probe_events={len(canonical_probes)} errors={canonical_errors[:8]}"),
        rule("hr_retryable_failures_model_visible_exact", not failure_visibility_errors and len([row for row in rows if row.get('delivered') is False]) == 3, f"missing_or_ambiguous={failure_visibility_errors}"),
        rule("hr_nonterminal_results_model_visible_exact", not nonterminal_visibility_errors and bool([row for row in rows if row.get('terminal') is False]), f"missing_or_ambiguous={nonterminal_visibility_errors[:10]}"),
        rule("hr_terminal_results_model_visible_exact", not terminal_visibility_errors and len([row for row in rows if row.get('terminal') is True]) == 12, f"terminal_rows={len([row for row in rows if row.get('terminal') is True])} missing_or_ambiguous={terminal_visibility_errors[:10]}"),
        rule("hr_turn5_audit_snapshot_correlated_exact", not audit_errors, f"pre_rows={len(pre_rows)} errors={audit_errors[:10]}"),
        rule("hr_common_shell_channel_is_structured", shell_ok, f"canonical_shell_events={len(shell_events)} probes={len(canonical_probes)} audits={len(canonical_audits)}"),
    ]
    agent_results = [
        rule("ar_probe_schedule_exact", log_valid and schedule_ok, f"rows={len(rows)} errors={(effective_errors + schedule_errors)[:10]} observed={observed}"),
        rule("ar_delivered_cursor_chain_exact", log_valid and chain_ok, f"errors={chain_errors[:10]}"),
        rule("ar_idempotent_retry_exact", log_valid and retry_ok, f"errors={retry_errors[:10]}"),
        rule("ar_fresh_session_audit_first_exact", audit_first_ok, f"errors={audit_first_errors[:10]}"),
        rule("ar_handoff_resume_cursor_exact", resume_ok, f"errors={resume_errors[:10]}"),
        rule("ar_stop_after_first_terminal_exact", log_valid and terminal_ok, f"errors={terminal_errors[:10]}"),
        rule("ar_pressure_rejections_and_checkpoint_exact", pressure_ok, f"errors={pressure_errors[:10]}"),
        rule("ar_approved_channel_and_state_boundary_exact", boundaries_ok, f"errors={boundary_errors[:10]}"),
        rule("ar_job_probe_json_contract_exact", status_ok and not receipt_errors, f"parse={status_parse} receipt_errors={receipt_errors[:6]} status_errors={status_errors[:8]}"),
        rule("ar_dedup_report_rows_exact", report_rows_ok, f"errors={report_row_errors[:10]}"),
        rule("ar_dedup_report_summary_and_actions_exact", report_summary_ok and report_actions_ok, f"summary_errors={report_summary_errors[:8]} action_errors={report_action_errors[:8]}"),
        rule("ar_output_inventory_exact", inventory_ok, f"errors={inventory_errors[:8]}"),
    ]
    print(json.dumps({"schema_version": "harnessbench_rule_results_v1", "rule_results": {"harness": harness_results, "agent": agent_results}}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
