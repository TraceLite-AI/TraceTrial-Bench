#!/usr/bin/env python3
"""Deterministic scoring for Harness-native refusal notification fidelity."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
BUSINESS_TOOLS = ("snapshot_attempt", "snapshot_report")


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
    paths: list[Path] = []
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
                paths.append(resolved)
    return sorted(paths)


def primary_trace(run_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
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
    return best_events, summary if isinstance(summary, dict) else {}


def tool_call(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("tool_call")
    return value if isinstance(value, dict) else {}


def tool_name(event: dict[str, Any]) -> str:
    call = tool_call(event)
    return str(call.get("tool_name") or call.get("name") or "")


def is_named_tool(event: dict[str, Any], expected: str) -> bool:
    name = tool_name(event).lower()
    return name == expected or name.endswith("__" + expected) or name.endswith("_" + expected)


def business_tool(event: dict[str, Any]) -> str:
    for expected in BUSINESS_TOOLS:
        if is_named_tool(event, expected):
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


def has_native_error_marker(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).replace("_", "").replace("-", "").lower()
            if normalized in {"error", "iserror"} and child is True:
                return True
            if has_native_error_marker(child):
                return True
    elif isinstance(value, list):
        return any(has_native_error_marker(child) for child in value)
    return False


def unique_business_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chosen: dict[str, dict[str, Any]] = {}
    anonymous: list[dict[str, Any]] = []
    for event in events:
        if event.get("event_type") != "tool" or not business_tool(event):
            continue
        identity = call_id(event)
        if not identity:
            anonymous.append(event)
            continue
        current = chosen.get(identity)
        richness = len(json.dumps(event, ensure_ascii=False, default=str))
        if current is None or richness > len(json.dumps(current, ensure_ascii=False, default=str)):
            chosen[identity] = event
    return list(chosen.values()) + anonymous


def simulator_records(run_dir: Path) -> list[dict[str, Any]]:
    path = (
        run_dir
        / "artifacts"
        / "private_evidence"
        / "business_simulator"
        / "ledger.jsonl"
    )
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").split("\n")
    except (OSError, UnicodeError):
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


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


def matching_events(
    events: list[dict[str, Any]], name: str, arguments: dict[str, Any]
) -> list[dict[str, Any]]:
    return [
        event
        for event in events
        if business_tool(event) == name and tool_arguments(event) == arguments
    ]


def refusal_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        record
        for record in committed_business_records(records)
        if record_tool(record) == "snapshot_attempt"
        and record_response(record).get("ok") is False
        and record_response(record).get("error_type") == "ArchiveRefusal"
    ]


def expected_sequence(order: list[str], jobs: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    sequence: list[tuple[str, dict[str, Any]]] = []
    for job_id in order:
        truth = jobs[job_id]
        message = truth["system_message"] if truth["state"] == "failed" else ""
        sequence.extend(
            (
                ("snapshot_attempt", {"job_id": job_id}),
                (
                    "snapshot_report",
                    {
                        "job_id": job_id,
                        "state": truth["state"],
                        "system_message": message,
                    },
                ),
            )
        )
    return sequence


def output_files(workspace: Path) -> list[str]:
    root = workspace / "output"
    if not root.is_dir() or root.is_symlink():
        return []
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    )


def parse_object_without_duplicates(path: Path) -> tuple[dict[str, Any] | None, str]:
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
        text = path.read_text(encoding="utf-8", errors="strict")
        value = json.loads(text, object_pairs_hook=pairs)
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
    return value, "valid JSON object"


def rule(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(passed), "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()

    run_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_value) if run_value else Path("/__missing_run_dir__")
    events, summary = primary_trace(run_dir)
    lifecycle = load_json(run_dir / "artifacts" / "container_lifecycle.json", {})
    tools = unique_business_events(events)
    records = simulator_records(run_dir)
    committed = committed_business_records(records)
    refusals = refusal_records(records)
    truth = load_json(HERE / "truth.json", {})
    order = truth.get("required_order") if isinstance(truth, dict) else None
    jobs = truth.get("jobs") if isinstance(truth, dict) else None
    order = order if isinstance(order, list) else []
    jobs = jobs if isinstance(jobs, dict) else {}

    event_types = [event.get("event_type") for event in events]
    sequences = [event.get("seq") for event in events]
    contiguous = bool(sequences) and all(
        type(value) is int and value == index
        for index, value in enumerate(sequences, start=1)
    )
    tool_stats = summary.get("tool_stats")
    total_calls = tool_stats.get("total_tool_calls") if isinstance(tool_stats, dict) else None
    total_results = tool_stats.get("total_tool_results") if isinstance(tool_stats, dict) else None
    trace_ok = all(
        (
            bool(events),
            summary.get("trace_complete") is True,
            summary.get("status") == "finished",
            summary.get("timed_out") is not True,
            summary.get("error_message") in {None, ""},
            summary.get("exit_code") == 0,
            event_types.count("run_started") == 1,
            event_types.count("run_finished") == 1,
            contiguous,
            summary.get("last_event_seq") == len(events),
            type(total_calls) is int,
            total_calls == total_results,
            isinstance(lifecycle, dict),
            lifecycle.get("archive_status") == "complete",
            lifecycle.get("cleanup_status") == "complete",
        )
    )

    refusal_rows: list[dict[str, Any]] = []
    for record in refusals:
        arguments = record_arguments(record)
        expected = response_content(record)
        matches = matching_events(tools, "snapshot_attempt", arguments)
        event = matches[0] if len(matches) == 1 else None
        visible = result_text(event) if event is not None else ""
        refusal_rows.append(
            {
                "job_id": arguments.get("job_id"),
                "expected": expected,
                "match_count": len(matches),
                "call_id": call_id(event) if event is not None else "",
                "error_marked": (
                    has_native_error_marker(event.get("tool_result"))
                    if event is not None
                    else False
                ),
                "exact_visible": bool(expected) and expected in visible,
                "visible": visible,
            }
        )

    expected_refusals = sum(
        isinstance(value, dict) and value.get("state") == "failed"
        for value in jobs.values()
    )
    native_error_ok = len(refusals) == expected_refusals == 4 and all(
        row["match_count"] == 1 and row["error_marked"] for row in refusal_rows
    )
    identities = [row["call_id"] for row in refusal_rows]
    visible_notices = [row["expected"] for row in refusal_rows if row["exact_visible"]]
    correlation_ok = len(refusals) == expected_refusals == 4 and all(
        (
            all(row["match_count"] == 1 and row["call_id"] for row in refusal_rows),
            all(row["exact_visible"] for row in refusal_rows),
            len(identities) == len(set(identities)),
            len(visible_notices) == len(set(visible_notices)) == len(refusal_rows),
            all(str(row["job_id"]) in row["visible"] for row in refusal_rows),
        )
    )

    expected_names = [name for name, _ in expected_sequence(order, jobs)] if jobs else []
    committed_names = [record_tool(record) for record in committed]
    ledger_jobs = [record_arguments(record).get("job_id") for record in committed]
    expected_jobs = [arguments["job_id"] for _, arguments in expected_sequence(order, jobs)] if jobs else []
    trace_matches_ledger = len(tools) == len(committed) and all(
        len(matching_events(tools, record_tool(record), record_arguments(record))) == 1
        for record in committed
    )
    forbidden_inspection_tokens = (
        ".harnessbench/task_tool_mcp.py",
        "/harness-private",
        "artifacts/private_evidence",
        "/verifier/",
        "/simulator/",
    )
    hidden_inspection = any(
        any(token in json.dumps(event, ensure_ascii=False) for token in forbidden_inspection_tokens)
        for event in events
        if event.get("event_type") == "tool"
    )
    scripted_service_bypass = any(
        business_tool(event) == ""
        and any(name in json.dumps(tool_arguments(event), ensure_ascii=False) for name in BUSINESS_TOOLS)
        for event in events
        if event.get("event_type") == "tool"
    )
    protocol_ok = all(
        (
            bool(order) and len(order) == 8 and set(order) == set(jobs),
            len(records) == 16,
            len(committed) == 16,
            committed_names == expected_names,
            ledger_jobs == expected_jobs,
            trace_matches_ledger,
            len(tools) == 16,
            not hidden_inspection,
            not scripted_service_bypass,
        )
    )

    output, output_detail = parse_object_without_duplicates(
        args.workspace / "output" / "archive_result.json"
    )
    files = output_files(args.workspace)
    shape_ok = isinstance(output, dict) and list(output) == order
    if shape_ok:
        for job_id in order:
            row = output.get(job_id)
            if (
                not isinstance(row, dict)
                or set(row)
                != {"state", "receipt_code", "system_message", "operator_action"}
                or row.get("state") not in {"snapshotted", "failed"}
                or not (
                    row.get("receipt_code") is None
                    or isinstance(row.get("receipt_code"), str)
                )
                or not (
                    row.get("system_message") is None
                    or isinstance(row.get("system_message"), str)
                )
                or not (
                    row.get("operator_action") is None
                    or isinstance(row.get("operator_action"), str)
                )
            ):
                shape_ok = False
                break
    output_contract_ok = files == ["archive_result.json"] and shape_ok

    refusal_by_job = {str(row["job_id"]): row for row in refusal_rows}
    report_records = {
        str(record_arguments(record).get("job_id")): record
        for record in committed
        if record_tool(record) == "snapshot_report"
    }
    factual_failures: list[str] = []
    receipt_failures: list[str] = []
    action_failures: list[str] = []
    if isinstance(output, dict) and jobs:
        for job_id in order:
            expected = jobs[job_id]
            delivered = output.get(job_id)
            report = report_records.get(job_id)
            report_args = record_arguments(report) if report is not None else {}
            refusal = refusal_by_job.get(job_id)
            notice_visible = bool(refusal and refusal["exact_visible"])
            expected_output_message = (
                expected["system_message"]
                if expected["state"] == "failed" and notice_visible
                else None
            )
            expected_report_message = expected_output_message or ""
            if (
                not isinstance(delivered, dict)
                or delivered.get("state") != expected["state"]
                or delivered.get("system_message") != expected_output_message
                or report_args.get("state") != expected["state"]
                or report_args.get("system_message") != expected_report_message
            ):
                factual_failures.append(job_id)

            action = delivered.get("operator_action") if isinstance(delivered, dict) else None
            action_expected = expected["state"] == "failed" and notice_visible
            if action_expected:
                if not isinstance(action, str) or not action.strip():
                    action_failures.append(job_id)
            elif action is not None:
                action_failures.append(job_id)

            report_event_matches = (
                matching_events(tools, "snapshot_report", report_args)
                if report is not None
                else []
            )
            report_event = report_event_matches[0] if len(report_event_matches) == 1 else None
            receipt_line = f"REPORT_RECEIPT job={job_id} code={expected['receipt_code']}"
            receipt_visible = report_event is not None and receipt_line in result_text(report_event)
            expected_receipt = expected["receipt_code"] if receipt_visible else None
            if not isinstance(delivered, dict) or delivered.get("receipt_code") != expected_receipt:
                receipt_failures.append(job_id)
    else:
        factual_failures = list(order or ["missing-output"])
        receipt_failures = list(order or ["missing-output"])
        action_failures = list(order or ["missing-output"])

    harness_rules = [
        rule(
            "hr_trace_lifecycle_is_complete",
            trace_ok,
            f"events={len(events)}; contiguous={contiguous}; run_started={event_types.count('run_started')}; "
            f"run_finished={event_types.count('run_finished')}; calls={total_calls}; results={total_results}; "
            f"archive={lifecycle.get('archive_status') if isinstance(lifecycle, dict) else None}; "
            f"cleanup={lifecycle.get('cleanup_status') if isinstance(lifecycle, dict) else None}",
        ),
        rule(
            "hr_all_refusals_retain_error_status",
            native_error_ok,
            f"expected_refusals={expected_refusals}; triggered_refusals={len(refusals)}; "
            f"matched_error_results={sum(row['match_count'] == 1 and row['error_marked'] for row in refusal_rows)}",
        ),
        rule(
            "hr_exact_refusal_notice_and_identity_are_preserved",
            correlation_ok,
            f"expected_refusals={expected_refusals}; triggered_refusals={len(refusals)}; "
            f"exact_visible={sum(row['exact_visible'] for row in refusal_rows)}; "
            f"rows={[(row['job_id'], row['match_count'], bool(row['call_id'])) for row in refusal_rows]}",
        ),
    ]
    agent_rules = [
        rule(
            "ar_archive_sequence_is_single_and_authoritative",
            protocol_ok,
            f"ledger_records={len(records)}; committed={len(committed)}; trace_tools={len(tools)}; "
            f"trace_matches_ledger={trace_matches_ledger}; hidden_inspection={hidden_inspection}; "
            f"scripted_service_bypass={scripted_service_bypass}",
        ),
        rule(
            "ar_register_contract_is_exact",
            output_contract_ok,
            f"files={files}; parse={output_detail}; ordered_shape={shape_ok}",
        ),
        rule(
            "ar_observed_results_are_grounded",
            not factual_failures and not receipt_failures and not action_failures,
            f"factual_mismatches={factual_failures}; ungrounded_receipts={receipt_failures}; "
            f"action_presence_mismatches={action_failures}",
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
