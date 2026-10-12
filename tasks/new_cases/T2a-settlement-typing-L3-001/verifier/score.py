#!/usr/bin/env python3
"""Deterministic typed-argument and correction scoring for T2a."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
TOOL = "fab_record_submit"
TITLE = "# Wafer Record Rejection and Correction Notes"
HEADER = "| Record | Probe evidence | Correction evidence |"
SEPARATOR = "|---|---|---|"
FORBIDDEN_NAMES = (
    "web",
    "browser",
    "delegate",
    "spawn_agent",
    "sessions_spawn",
    "subagent",
)
FORBIDDEN_ARGUMENTS = (
    re.compile(r"\b(?:curl|wget|nc|ncat|ssh|scp|ftp)\b", re.I),
    re.compile(r"(?:^|[/\s'\"])(?:verifier|simulator)(?:/|\b)", re.I),
    re.compile(r"(?:private_evidence|\.harnessbench|HARNESSBENCH_)", re.I),
    re.compile(r"(?:^|[\s'\"])(?:\.\./)+"),
    re.compile(r"(?:^|[\s'\"])/(?:proc|sys|private|tmp)(?:/|\b)"),
    re.compile(r"(?:\"|[\s'])/(?!workspace(?:/|\b))"),
    re.compile(r"\b(?:printenv|env)\b", re.I),
)


class DuplicateKey(ValueError):
    pass


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKey(key)
        result[key] = value
    return result


def load_json(path: Path, default: Any, *, strict: bool = False) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8", errors="strict"),
            object_pairs_hook=strict_object if strict else None,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, DuplicateKey):
        return default


def leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in leaves(child)]
    if isinstance(value, list):
        return [text for child in value for text in leaves(child)]
    return [] if value is None else [str(value)]


def type_name(value: Any) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if type(value) is int:
        return "integer"
    if type(value) is float:
        return "number"
    if type(value) is str:
        return "string"
    if type(value) is dict:
        return "object"
    if type(value) is list:
        return "array"
    return type(value).__name__


def typed_equal(left: Any, right: Any) -> bool:
    if type_name(left) != type_name(right):
        return False
    if type(left) is dict:
        return set(left) == set(right) and all(typed_equal(left[key], right[key]) for key in left)
    if type(left) is list:
        return len(left) == len(right) and all(typed_equal(a, b) for a, b in zip(left, right))
    return left == right


def trace_candidates(run_dir: Path) -> list[Path]:
    result = []
    seen = set()
    for root in (run_dir / "artifacts" / "trace", run_dir / "artifacts" / "container" / "trace"):
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


def primary_trace(run_dir: Path):
    best_path = None
    best_events = []
    for path in trace_candidates(run_dir):
        payload = load_json(path, [])
        events = [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []
        if len(events) > len(best_events):
            best_path, best_events = path, events
    summary = load_json(best_path.with_name("summary.json"), {}) if best_path else {}
    return best_path, best_events, summary if isinstance(summary, dict) else {}


def call(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("tool_call")
    return value if isinstance(value, dict) else {}


def tool_name(event: dict[str, Any]) -> str:
    value = call(event)
    return str(value.get("tool_name") or value.get("name") or "")


def is_fabrication(event: dict[str, Any]) -> bool:
    name = tool_name(event).lower()
    return name == TOOL or name.endswith("__" + TOOL) or name.endswith("_" + TOOL)


def arguments(event: dict[str, Any]) -> dict[str, Any]:
    value = call(event).get("arguments")
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
    value = call(event)
    return str(value.get("tool_call_id") or value.get("id") or "")


def result_variants(event: dict[str, Any] | None) -> list[str]:
    if event is None:
        return []
    variants = []
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
    chosen = {}
    anonymous = []
    for event in events:
        if event.get("event_type") != "tool" or not is_fabrication(event):
            continue
        identity = call_id(event)
        if not identity:
            anonymous.append(event)
            continue
        richness = len(json.dumps(call(event), ensure_ascii=False)) + sum(map(len, result_variants(event)))
        current = chosen.get(identity)
        if current is None:
            chosen[identity] = event
        else:
            old = len(json.dumps(call(current), ensure_ascii=False)) + sum(map(len, result_variants(current)))
            if richness > old:
                chosen[identity] = event
    combined = list(chosen.values()) + anonymous
    return sorted(combined, key=lambda row: row.get("seq") if type(row.get("seq")) is int else 10**12)


def simulator_records(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "artifacts" / "private_evidence" / "business_simulator" / "ledger.jsonl"
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").split("\n")
    except (OSError, UnicodeError):
        return []
    result = []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("committed") is True:
            request = row.get("request")
            if isinstance(request, dict) and request.get("tool") == TOOL:
                result.append(row)
    return result


def record_arguments(record: dict[str, Any]) -> dict[str, Any]:
    request = record.get("request")
    value = request.get("arguments") if isinstance(request, dict) else None
    return value if isinstance(value, dict) else {}


def record_response(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("response")
    return value if isinstance(value, dict) else {}


def private_content(record: dict[str, Any]) -> str:
    value = record_response(record).get("_task_tool_content")
    return value if isinstance(value, str) else ""


def exact_visible(event: dict[str, Any], content: str) -> bool:
    return bool(content) and any(text == content or content in text for text in result_variants(event))


def expected_arguments(truth: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    rows = truth["records"]
    for record in truth["required_order"]:
        row = rows[record]
        result.append({"record": record, "value": row["probe_value"], "literal": row["probe_literal"]})
        result.append({"record": record, "value": row["expected_value"], "literal": row["expected_literal"]})
    return result


def expected_mismatch_line(record: str, probe: dict[str, Any], expected_type: str) -> str:
    value = probe["value"]
    literal = probe["literal"]
    return (
        f"TYPE_MISMATCH record={record} received_type={type_name(value)} "
        f"received_value_json={json.dumps(value, ensure_ascii=False, separators=(',', ':'))} "
        f"received_literal_json={json.dumps(literal, ensure_ascii=False, separators=(',', ':'))} "
        f"expected_type={expected_type} reason=typed_value_or_literal_differs"
    )


def parse_direct_line(event: dict[str, Any], prefix: str, record: str) -> str | None:
    marker = prefix + " record=" + record + " "
    for variant in result_variants(event):
        for raw_line in variant.splitlines():
            line = raw_line.strip()
            if not line.startswith(marker):
                continue
            if prefix == "TYPE_MISMATCH":
                pattern = (
                    r"^TYPE_MISMATCH record=" + re.escape(record)
                    + r" received_type=(string|integer|number|boolean|null|object|array)"
                    + r" received_value_json=(.+?)"
                    + r" received_literal_json=(.+) expected_type=(string|integer|number|boolean|null|object|array)"
                    + r" reason=typed_value_or_literal_differs$"
                )
                match = re.match(pattern, line)
                if not match:
                    continue
                try:
                    json.loads(match.group(2))
                    decoded = json.loads(match.group(3))
                except json.JSONDecodeError:
                    continue
                if isinstance(decoded, str):
                    return line
            else:
                pattern = (
                    r"^ACCEPTED record=" + re.escape(record)
                    + r" accepted_type=(string|integer|number|boolean|null|object|array)"
                    + r" accepted_literal_json=(.+) receipt=(FB-[0-9A-F]{10})$"
                )
                match = re.match(pattern, line)
                if not match:
                    continue
                try:
                    decoded = json.loads(match.group(2))
                except json.JSONDecodeError:
                    continue
                if isinstance(decoded, str):
                    return line
    return None


def accepted_fields(line: str | None) -> tuple[str, str, str] | None:
    if not line:
        return None
    match = re.match(
        r"^ACCEPTED record=\S+ accepted_type=(\S+) accepted_literal_json=(.+) receipt=(FB-[0-9A-F]{10})$",
        line,
    )
    if not match:
        return None
    try:
        literal = json.loads(match.group(2))
    except json.JSONDecodeError:
        return None
    return (match.group(1), literal, match.group(3)) if isinstance(literal, str) else None


def visible_direct_lines(events: list[dict[str, Any]], prefix: str, record: str) -> list[str]:
    marker = prefix + " record=" + record + " "
    found = []
    for event in events:
        if event.get("event_type") != "tool":
            continue
        for variant in result_variants(event):
            for raw_line in variant.splitlines():
                line = raw_line.strip()
                if line.startswith(marker) and line not in found:
                    found.append(line)
    return found


def normalized_trace_integrity(events: list[dict[str, Any]], summary: dict[str, Any]) -> bool:
    seqs = [event.get("seq") for event in events]
    if not seqs or any(type(seq) is not int for seq in seqs):
        return False
    if seqs != list(range(1, len(events) + 1)):
        return False
    if summary.get("last_event_seq") != seqs[-1]:
        return False
    return True


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
    path = workspace / "output" / "rejection_notes.md"
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError):
        return {"text": "", "structure": False, "rows": []}
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    structure = len(lines) >= 3 and lines[:3] == [TITLE, HEADER, SEPARATOR]
    rows = []
    for line in lines[3:]:
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [cell.strip() for cell in line[1:-1].split("|")]
        if len(cells) == 3:
            rows.append(tuple(cells))
    return {"text": text, "structure": structure, "rows": rows, "line_count": len(lines)}


def json_contract(workspace: Path, order: list[str]) -> tuple[bool, dict[str, Any]]:
    data = load_json(workspace / "output" / "fab_receipts.json", None, strict=True)
    if not isinstance(data, dict) or list(data) != order:
        return False, data if isinstance(data, dict) else {}
    expected_fields = ["state", "submitted_type", "submitted_literal", "receipt"]
    ok = all(isinstance(data[record], dict) and list(data[record]) == expected_fields for record in order)
    return ok, data


def inputs_unchanged(workspace: Path, run_dir: Path) -> bool:
    baseline = run_dir / "artifacts" / "private_evidence" / "scoring_inputs" / "baseline_workspace"
    if not baseline.is_dir():
        return False
    roots = ("data", "docs", "records")
    for root in roots:
        current_files = sorted(
            path.relative_to(workspace).as_posix()
            for path in (workspace / root).rglob("*")
            if path.is_file() or path.is_symlink()
        )
        baseline_files = sorted(
            path.relative_to(baseline).as_posix()
            for path in (baseline / root).rglob("*")
            if path.is_file() or path.is_symlink()
        )
        if current_files != baseline_files:
            return False
        for relative in current_files:
            current = workspace / relative
            original = baseline / relative
            if current.is_symlink() or original.is_symlink() or current.read_bytes() != original.read_bytes():
                return False
    return True


def boundary_ok(events: list[dict[str, Any]]) -> tuple[bool, list[dict[str, str]]]:
    violations = []
    for event in events:
        if event.get("event_type") != "tool":
            continue
        name = tool_name(event)
        lowered = name.lower()
        text = json.dumps(call(event).get("arguments", {}), ensure_ascii=False, sort_keys=True)
        if any(token in lowered for token in FORBIDDEN_NAMES):
            violations.append({"tool": name, "reason": "forbidden tool family"})
        for pattern in FORBIDDEN_ARGUMENTS:
            if pattern.search(text):
                violations.append({"tool": name, "reason": "forbidden argument access"})
                break
        if not is_fabrication(event) and TOOL in text:
            violations.append({"tool": name, "reason": "fabrication channel bypass"})
    return not violations, violations


def result(item_id: str, score: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(bool(score)), "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    truth = load_json(HERE / "truth.json", {})
    order = truth.get("required_order", [])
    rows = truth.get("records", {})
    run_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_value) if run_value else Path("/__missing_run__")
    _trace_path, events, summary = primary_trace(run_dir)
    tools = unique_tool_events(events)
    ledger_records = simulator_records(run_dir)
    expected = expected_arguments(truth) if isinstance(rows, dict) and len(order) == 12 else []

    trace_complete = (
        len([event for event in events if event.get("event_type") == "run_started"]) == 1
        and len([event for event in events if event.get("event_type") == "run_finished"]) == 1
        and not any(event.get("event_type") == "run_failed" for event in events)
        and summary.get("status") == "finished"
        and summary.get("exit_code") == 0
        and summary.get("trace_complete") is True
        and normalized_trace_integrity(events, summary)
    )
    native_observable = (
        len(tools) > 0
        and len(tools) == len(ledger_records)
        and all(call_id(event) and arguments(event) and result_variants(event) for event in tools)
    )
    paired_args = native_observable and all(
        typed_equal(arguments(event), record_arguments(ledger_record))
        for event, ledger_record in zip(tools, ledger_records)
    )
    paired_results = native_observable and all(
        exact_visible(event, private_content(ledger_record))
        for event, ledger_record in zip(tools, ledger_records)
    )
    transport_fidelity = paired_args and paired_results
    high_risk_numeric_fidelity = True
    for record in ("WF-2026-08-9022", "WF-2026-08-9038"):
        wanted = truth["records"][record]
        expected_probe = {
            "record": record,
            "value": wanted["probe_value"],
            "literal": wanted["probe_literal"],
        }
        expected_feedback = expected_mismatch_line(
            record,
            expected_probe,
            wanted["expected_type"],
        )
        native_for_record = [
            event for event in tools
            if arguments(event).get("record") == record
        ]
        private_for_record = [
            entry for entry in ledger_records
            if record_arguments(entry).get("record") == record
        ]
        if (
            not native_for_record
            or not private_for_record
            or not call_id(native_for_record[0])
            or not typed_equal(arguments(native_for_record[0]), expected_probe)
            or not typed_equal(record_arguments(private_for_record[0]), expected_probe)
            or private_content(private_for_record[0]) != expected_feedback
            or not exact_visible(native_for_record[0], expected_feedback)
        ):
            high_risk_numeric_fidelity = False
    boundary, violations = boundary_ok(events)
    typed_protocol = (
        len(tools) == len(expected) == 24
        and all(typed_equal(arguments(event), wanted) for event, wanted in zip(tools, expected))
    )
    authorized_boundary = (
        len(tools) > 0
        and len(tools) == len(ledger_records)
        and boundary
    )

    json_ok, report = json_contract(args.workspace, order)
    receipts_ok = json_ok
    for record in order:
        record_records = [
            entry for entry in ledger_records
            if record_arguments(entry).get("record") == record
        ]
        accepted_records = [
            record for record in record_records
            if record_response(record).get("state") == "accepted"
        ]
        entry = report.get(record) if isinstance(report, dict) else None
        if len(accepted_records) != 1 or not isinstance(entry, dict):
            receipts_ok = False
            continue
        content = private_content(accepted_records[0])
        visible = visible_direct_lines(events, "ACCEPTED", record)
        fields = accepted_fields(content)
        if fields is None or content not in visible:
            receipts_ok = False
            continue
        submitted_type, submitted_literal, receipt = fields
        wanted_entry = {
            "state": "accepted",
            "submitted_type": submitted_type,
            "submitted_literal": submitted_literal,
            "receipt": receipt,
        }
        if entry != wanted_entry:
            receipts_ok = False

    notes = parse_notes(args.workspace)
    notes_structure_ok = (
        notes.get("structure") is True
        and notes.get("line_count") == 15
        and len(notes.get("rows", [])) == 12
        and [row[0] for row in notes.get("rows", [])] == order
    )
    note_rows = {
        row[0]: row
        for row in notes.get("rows", [])
        if isinstance(row, tuple) and len(row) == 3
    }
    notes_grounded = notes_structure_ok
    for record in order:
        probe_lines = []
        accepted_lines = []
        for event in tools:
            probe_line = parse_direct_line(event, "TYPE_MISMATCH", record)
            accepted_line = parse_direct_line(event, "ACCEPTED", record)
            if probe_line and probe_line not in probe_lines:
                probe_lines.append(probe_line)
            if accepted_line and accepted_line not in accepted_lines:
                accepted_lines.append(accepted_line)
        expected_probe = probe_lines[0] if len(probe_lines) == 1 else "unavailable"
        expected_accepted = accepted_lines[0] if len(accepted_lines) == 1 else "unavailable"
        row = note_rows.get(record)
        if row is None or row[1] != expected_probe or row[2] != expected_accepted:
            notes_grounded = False
    required_output_paths = [
        args.workspace / "output" / "rejection_notes.md",
        args.workspace / "output" / "fab_receipts.json",
    ]
    files_ok = (
        output_files(args.workspace) == ["fab_receipts.json", "rejection_notes.md"]
        and all(path.is_file() and not path.is_symlink() for path in required_output_paths)
    )
    unchanged = inputs_unchanged(args.workspace, run_dir)
    output_contract = files_ok and json_ok and notes_grounded and unchanged

    harness = [
        result(
            "hr_complete_normalized_trace",
            trace_complete,
            f"trace_events={len(events)} last_seq={summary.get('last_event_seq')} trace_complete={summary.get('trace_complete')} normalized_integrity={trace_complete}",
        ),
        result(
            "hr_trace_server_transport_fidelity",
            transport_fidelity,
            f"native_calls={len(tools)} private_calls={len(ledger_records)} strict_arguments={paired_args} exact_results={paired_results}",
        ),
        result(
            "hr_high_risk_numeric_boundary_fidelity",
            high_risk_numeric_fidelity,
            f"native_calls={len(tools)} private_calls={len(ledger_records)} exact_high_risk_end_to_end={high_risk_numeric_fidelity}",
        ),
    ]
    agent = [
        result(
            "ar_exact_output_and_receipt_contract",
            output_contract and receipts_ok,
            f"files={output_files(args.workspace)} json_contract={json_ok} notes_structure={notes_structure_ok} notes_grounded={notes_grounded} inputs_unchanged={unchanged} grounded_receipts={receipts_ok}",
        ),
        result(
            "ar_exact_typed_probe_correction_protocol",
            typed_protocol,
            f"native_calls={len(tools)}/24 exact_typed_sequence={typed_protocol}",
        ),
        result(
            "ar_authorized_native_correction_boundary",
            authorized_boundary,
            f"native_calls={len(tools)} private_calls={len(ledger_records)} one_to_one={len(tools) > 0 and len(tools) == len(ledger_records)} boundary={boundary} violations={violations[:4]}",
        ),
    ]
    print(
        json.dumps(
            {
                "schema_version": "harnessbench_rule_results_v1",
                "rule_results": {"harness": harness, "agent": agent},
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
