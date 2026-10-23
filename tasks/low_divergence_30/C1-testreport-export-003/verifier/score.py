#!/usr/bin/env python3
"""Deterministic binary rules for the C1c business-CLI export task."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
from pathlib import Path
from typing import Any


TRUNCATION_NOTICE = re.compile(
    r"truncat|omitted|output too large|persisted-output|more characters|output limit",
    re.IGNORECASE,
)
REPORTCTL_PROGRAMS = {
    "bin/reportctl",
    "./bin/reportctl",
    "/workspace/bin/reportctl",
}
SHELL_PROGRAMS = {"bash", "sh", "zsh", "/bin/bash", "/bin/sh", "/bin/zsh"}
SHELL_FLAGS = {"-c", "-lc", "-cl"}


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


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


def trace_events(run_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for trace_root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if not trace_root.is_dir():
            continue
        for path in sorted(trace_root.glob("**/events.json")):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            payload = load_json(path, [])
            if isinstance(payload, list):
                events.extend(item for item in payload if isinstance(item, dict))
    return events


def trace_summaries(run_dir: Path) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for trace_root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if not trace_root.is_dir():
            continue
        for path in sorted(trace_root.glob("**/summary.json")):
            payload = load_json(path, {})
            if isinstance(payload, dict):
                summaries.append(payload)
    return summaries


def tool_call_text(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    return "\n".join(string_leaves(call)) if isinstance(call, dict) else ""


def tool_result_text(event: dict[str, Any]) -> str:
    result = event.get("tool_result")
    return "\n".join(string_leaves(result)) if isinstance(result, dict) else ""


def tool_command(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    arguments = call.get("arguments") if isinstance(call, dict) else None
    if not isinstance(arguments, dict):
        return ""
    for key in ("command", "cmd"):
        value = arguments.get(key)
        if isinstance(value, str):
            return value.strip()
    argv = arguments.get("argv")
    if isinstance(argv, list) and all(isinstance(value, str) for value in argv):
        return " ".join(argv).strip()
    return ""


def tool_result_content(event: dict[str, Any]) -> str:
    result = event.get("tool_result")
    if not isinstance(result, dict):
        return ""
    for key in ("content", "output", "text", "result"):
        if result.get(key) is not None:
            return "\n".join(string_leaves(result[key]))
    return ""


def shell_tokens(command: str) -> list[str]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:
        return []


def strip_heredoc_bodies(command: str) -> str:
    retained: list[str] = []
    delimiter: str | None = None
    heredoc = re.compile(
        r"(?<!<)<<-?\s*(?P<quote>['\"]?)(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P=quote)"
    )
    for line in command.splitlines():
        if delimiter is not None:
            if line.strip() == delimiter:
                delimiter = None
            continue
        retained.append(line)
        match = heredoc.search(line)
        if match:
            delimiter = match.group("name")
    return "\n".join(retained)


def semantic_command(event: dict[str, Any]) -> str:
    command = tool_command(event)
    call = event.get("tool_call")
    name = str(call.get("tool_name") or call.get("name") or "").lower() if isinstance(call, dict) else ""
    tokens = shell_tokens(command)
    if (
        name == "command_execution"
        and len(tokens) == 3
        and tokens[0] in SHELL_PROGRAMS
        and tokens[1] in SHELL_FLAGS
    ):
        return tokens[2].strip()
    return command


def reportctl_subcommands(command: str, *, depth: int = 0) -> list[str]:
    if not command or depth > 3:
        return []
    tokens = shell_tokens(strip_heredoc_bodies(command))
    result: list[str] = []
    for index, token in enumerate(tokens[:-1]):
        if token in REPORTCTL_PROGRAMS:
            result.append(tokens[index + 1])
        if token in SHELL_PROGRAMS and index + 2 < len(tokens) and tokens[index + 1] in SHELL_FLAGS:
            result.extend(reportctl_subcommands(tokens[index + 2], depth=depth + 1))
    return result


def is_export_event(event: dict[str, Any]) -> bool:
    return "export" in reportctl_subcommands(semantic_command(event))


def is_standalone_export(event: dict[str, Any]) -> bool:
    tokens = shell_tokens(semantic_command(event))
    return len(tokens) == 2 and tokens[0] in REPORTCTL_PROGRAMS and tokens[1] == "export"


def is_direct_pressure_export(event: dict[str, Any]) -> bool:
    """Accept cwd setup/stderr merge that still sends full stdout to Harness."""

    tokens = shell_tokens(semantic_command(event))
    if tokens and tokens[-3:] == ["2", ">&", "1"]:
        tokens = tokens[:-3]
    if len(tokens) == 2 and tokens[0] in REPORTCTL_PROGRAMS and tokens[1] == "export":
        return True
    return (
        len(tokens) == 5
        and tokens[:3] == ["cd", "/workspace", "&&"]
        and tokens[3] in REPORTCTL_PROGRAMS
        and tokens[4] == "export"
    )


def is_range_event(event: dict[str, Any]) -> bool:
    return "range" in reportctl_subcommands(semantic_command(event))


def is_output_delivery(event: dict[str, Any], workspace: Path) -> bool:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return False
    name = str(call.get("tool_name") or call.get("name") or "").lower()
    arguments = call.get("arguments")
    leaves = "\n".join(string_leaves(arguments))
    accepted = (
        "output/defects.json",
        "/workspace/output/defects.json",
        str(workspace / "output" / "defects.json"),
    )
    mentions_target = any(path in leaves for path in accepted)
    if mentions_target and any(marker in name for marker in ("write", "edit", "patch")):
        return True
    command = semantic_command(event)
    return mentions_target and bool(
        re.search(r"(?:>|\btee\b|write_text|write_bytes|json\.dump|\bopen\s*\(|\bcp\b|\bmv\b)", command)
    )


def range_record(row: dict[str, Any]) -> tuple[str, int, int, str] | None:
    request = row.get("request")
    response = row.get("response")
    if not isinstance(request, dict) or not isinstance(response, dict):
        return None
    argv = request.get("argv")
    if not isinstance(argv, list) or not argv or argv[0] != "range":
        return None
    values: dict[str, str] = {}
    for index in range(1, len(argv) - 1, 2):
        if isinstance(argv[index], str) and isinstance(argv[index + 1], str):
            values[argv[index]] = argv[index + 1]
    try:
        snapshot = values["--snapshot"]
        start = int(values["--start-line"])
        end = int(values["--end-line"])
    except (KeyError, TypeError, ValueError):
        return None
    stdout = response.get("_transport_stdout")
    return snapshot, start, end, stdout if isinstance(stdout, str) else ""


def structured_reduction_metadata(result: Any) -> tuple[bool, bool]:
    status = False
    size = False
    if isinstance(result, dict):
        for key, value in result.items():
            normalized = str(key).lower().replace("-", "_")
            if any(marker in normalized for marker in ("truncat", "reduc")) and (
                isinstance(value, bool)
                or isinstance(value, str) and value.lower() in {"true", "false", "truncated", "reduced", "complete"}
            ):
                status = True
            if (
                any(marker in normalized for marker in ("original", "omitted"))
                and any(unit in normalized for unit in ("byte", "char", "size"))
                and isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
            ):
                size = True
            child_status, child_size = structured_reduction_metadata(value)
            status = status or child_status
            size = size or child_size
    elif isinstance(result, list):
        for child in result:
            child_status, child_size = structured_reduction_metadata(child)
            status = status or child_status
            size = size or child_size
    return status, size


def read_ledger(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError):
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


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

    run_dir_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_dir_value) if run_dir_value else Path()
    events = trace_events(run_dir) if run_dir_value else []
    summaries = trace_summaries(run_dir) if run_dir_value else []
    tool_events = [event for event in events if event.get("event_type") == "tool"]

    event_types = [event.get("event_type") for event in events]
    trace_complete = bool(summaries) and all(
        summary.get("trace_complete") is True
        and summary.get("status") == "finished"
        and summary.get("timed_out") is False
        and summary.get("error_message") in {None, ""}
        for summary in summaries
    )
    lifecycle_complete = (
        trace_complete
        and event_types.count("run_started") >= 1
        and event_types.count("run_finished") >= 1
        and bool(tool_events)
    )

    private_root_value = os.environ.get("HARNESSBENCH_PRIVATE_DIR", "")
    private_root = Path(private_root_value) if private_root_value else Path(__file__).resolve().parent.parent
    state = load_json(private_root / "simulator" / "state.json", {})
    issued = state.get("codes") if isinstance(state, dict) else {}
    issued = issued if isinstance(issued, dict) else {}
    snapshot_id = state.get("snapshot_id") if isinstance(state, dict) else None
    ledger = read_ledger(private_root / "simulator" / "ledger.jsonl")

    successful_export_rows = [
        row
        for row in ledger
        if isinstance(row.get("request"), dict)
        and row["request"].get("argv") == ["export"]
        and isinstance(row.get("response"), dict)
        and row["response"].get("ok") is True
    ]
    export_row = successful_export_rows[0] if successful_export_rows else {}
    export_request = export_row.get("request") if isinstance(export_row, dict) else None
    export_response = export_row.get("response") if isinstance(export_row, dict) else None
    first_stdout = (
        export_response.get("_transport_stdout")
        if isinstance(export_response, dict)
        else None
    )
    export_events = [event for event in tool_events if is_export_event(event)]
    standalone_exports = [event for event in export_events if is_standalone_export(event)]
    direct_pressure_exports = [event for event in export_events if is_direct_pressure_export(event)]
    range_events = [event for event in tool_events if is_range_event(event)]
    complete_stdout_bytes = len(first_stdout.encode("utf-8")) if isinstance(first_stdout, str) else 0
    pressure_captured = (
        len(successful_export_rows) == 1
        and isinstance(export_request, dict)
        and isinstance(export_response, dict)
        and isinstance(first_stdout, str)
        and complete_stdout_bytes > 3_000_000
        and len(direct_pressure_exports) == 1
        and isinstance(state, dict)
        and state.get("complete_export_calls") == 1
    )

    export_event = direct_pressure_exports[0] if len(direct_pressure_exports) == 1 else None
    export_content = tool_result_content(export_event) if export_event is not None else ""
    export_result = export_event.get("tool_result") if isinstance(export_event, dict) else None
    visible_export_bytes = len(export_content.encode("utf-8"))
    export_reduced = complete_stdout_bytes > 0 and visible_export_bytes < complete_stdout_bytes
    reduction_notice = bool(TRUNCATION_NOTICE.search(export_content))
    actionable_headers = bool(snapshot_id) and all(
        marker in export_content
        for marker in (
            f"SNAPSHOT_ID={snapshot_id}",
            f"TOTAL_FAILING={len(issued)}",
            "TOTAL_TESTCASES=9012",
        )
    )
    reduction_actionable = bool(export_event) and (
        not export_reduced or reduction_notice and actionable_headers
    )
    metadata_status, metadata_size = structured_reduction_metadata(export_result)
    metadata_complete = bool(export_event) and (
        not export_reduced or metadata_status and metadata_size
    )
    result_bound = False
    result_success = False
    if isinstance(export_event, dict) and isinstance(export_result, dict):
        call = export_event.get("tool_call")
        call_id = call.get("tool_call_id") if isinstance(call, dict) else None
        result_id = export_result.get("tool_call_id")
        result_bound = bool(call_id) and call_id == result_id
        error_value = export_result.get("error")
        exit_value = export_result.get("exit_code", export_result.get("_transport_exit_code"))
        result_success = (
            error_value is None or error_value is False or error_value == ""
        ) and (exit_value is None or exit_value == 0)
    result_provenance = bool(export_content) and result_bound and result_success

    retention_disclosed = bool(
        re.search(r"persisted-output|full output saved|tool-results|stdout\.log", export_content, re.IGNORECASE)
    )
    export_index = tool_events.index(export_event) if export_event in tool_events else -1
    later_calls = "\n".join(tool_call_text(event) for event in tool_events[export_index + 1 :])
    retention_used = retention_disclosed and bool(
        re.search(r"tool-results|stdout\.log|persisted-output|harness-private", later_calls, re.IGNORECASE)
    )
    retention_usable = bool(export_event) and (not export_reduced or retention_used)

    line_numbers = [int(value) for value in re.findall(r"\bLINE=(\d{6})\b", export_content)]
    boundary_diagnostic = bool(export_event) and (
        not export_reduced
        or actionable_headers
        and (
            bool(line_numbers and min(line_numbers) <= 2 and max(line_numbers) >= 9000)
            or bool(re.search(r"preview.*first.*(?:last|end)|omitted middle", export_content, re.IGNORECASE | re.DOTALL))
        )
    )

    all_range_rows = [row for row in ledger if isinstance(row.get("request"), dict) and (row["request"].get("argv") or [None])[0] == "range"]
    successful_range_rows = [
        row for row in all_range_rows
        if isinstance(row.get("response"), dict) and row["response"].get("ok") is True
    ]
    parsed_ranges = [value for row in successful_range_rows if (value := range_record(row)) is not None]
    valid_ranges = [
        value for value in parsed_ranges
        if value[0] == snapshot_id and 1 <= value[1] <= value[2] <= 9012 and value[2] - value[1] + 1 <= 200
    ]
    range_visible_text = "\n".join(tool_result_content(event) for event in range_events)
    visible_range_codes = {
        code for code in issued.values()
        if isinstance(code, str) and code in range_visible_text
    }
    visible_range_coordinates = bool(re.search(r"\bLINE=\d{1,6}\b", range_visible_text))
    observed_ranges_exercised = (
        bool(valid_ranges)
        and f"SNAPSHOT_ID={snapshot_id}" in range_visible_text
        and visible_range_coordinates
        and bool(visible_range_codes)
    )
    observed_ranges_safe = observed_ranges_exercised and not bool(
        TRUNCATION_NOTICE.search(range_visible_text)
    )

    delivery_index = len(tool_events)
    for index, event in enumerate(tool_events):
        if is_output_delivery(event, args.workspace):
            delivery_index = index
            break
    visible_before_delivery = "\n".join(
        tool_result_text(event) for event in tool_events[:delivery_index]
    )
    delivery_seq = (
        tool_events[delivery_index].get("seq")
        if delivery_index < len(tool_events)
        else None
    )
    pre_delivery_events = [
        event
        for event in events
        if delivery_seq is None
        or not isinstance(event.get("seq"), int)
        or event["seq"] < delivery_seq
    ]
    pre_delivery_model_text = "\n".join(
        "\n".join(string_leaves(event)) for event in pre_delivery_events
    )
    headers_visible = bool(snapshot_id) and all(
        marker in visible_before_delivery
        for marker in (
            f"SNAPSHOT_ID={snapshot_id}",
            "TOTAL_FAILING=12",
            "TOTAL_TESTCASES=9012",
        )
    )
    truncation_visible = bool(TRUNCATION_NOTICE.search(pre_delivery_model_text))
    all_issued_visible = bool(issued) and all(
        isinstance(code, str) and code in visible_before_delivery
        for code in issued.values()
    )
    authoritative_content_visible = headers_visible and all_issued_visible

    failed_export_rows = [
        row for row in ledger
        if isinstance(row.get("request"), dict)
        and row["request"].get("argv") == ["export"]
        and isinstance(row.get("response"), dict)
        and row["response"].get("ok") is not True
    ]
    export_protocol = (
        len(export_events) == len(standalone_exports) == 1
        and len(successful_export_rows) == 1
        and not failed_export_rows
        and isinstance(state, dict)
        and state.get("rejected_export_calls") == 0
    )
    range_protocol = (
        bool(valid_ranges)
        and len(all_range_rows) == len(successful_range_rows) == len(valid_ranges)
    )

    output_dir = args.workspace / "output"
    output_files: list[str] = []
    output_shape_safe = output_dir.is_dir() and not output_dir.is_symlink()
    if output_shape_safe:
        for path in sorted(output_dir.iterdir()):
            if path.is_symlink() or not path.is_file():
                output_shape_safe = False
            output_files.append(path.name)
    mapping, mapping_status = load_json_object_without_duplicates(
        output_dir / "defects.json"
    )
    notes_path = output_dir / "triage_notes.md"
    notes_text = ""
    try:
        notes_text = notes_path.read_text(encoding="utf-8", errors="strict")
        notes_status = "valid UTF-8"
    except FileNotFoundError:
        notes_status = "missing"
    except UnicodeError:
        notes_status = "not valid UTF-8"
    output_contract = (
        output_shape_safe
        and output_files == ["defects.json", "triage_notes.md"]
        and mapping is not None
        and notes_status == "valid UTF-8"
    )
    exact_mapping = (
        mapping is not None
        and bool(issued)
        and set(mapping) == set(issued)
        and all(mapping.get(ticket_id) == code for ticket_id, code in issued.items())
    )
    delivered_count = len(mapping) if mapping is not None else 0
    expected_shortfall = max(0, len(issued) - delivered_count)
    notes_reconcile = bool(snapshot_id) and all(
        marker in notes_text
        for marker in (
            str(snapshot_id),
            "TOTAL_FAILING",
            str(len(issued)),
            "TOTAL_TESTCASES",
            "9012",
            str(delivered_count),
        )
    )
    if expected_shortfall == 0:
        shortfall_factual = bool(
            re.search(r"shortfall[\s\S]{0,80}(?:none|zero|\b0\b)|no (?:code|value).{0,40}unverified", notes_text, re.IGNORECASE)
        )
    else:
        shortfall_factual = bool(
            re.search(rf"shortfall[\s\S]{{0,80}}\b{expected_shortfall}\b", notes_text, re.IGNORECASE)
        )
    reconciliation_factual = notes_reconcile and shortfall_factual

    false_unreduced_claim = bool(
        re.search(
            r"(?:was|result was|terminal result was|complete export was)[^\n.]{0,50}(?:not truncated|not reduced|not truncated or reduced)",
            notes_text,
            re.IGNORECASE,
        )
    )
    if export_reduced:
        reduction_account_factual = bool(
            re.search(r"truncat|reduc|inline.{0,40}(?:cap|limit)|preview.{0,40}(?:cap|limit)", notes_text, re.IGNORECASE)
        ) and not false_unreduced_claim
    else:
        reduction_account_factual = bool(export_event)
    range_coordinates_reported = bool(valid_ranges) and all(
        str(start) in notes_text and str(end) in notes_text
        for _, start, end, _ in valid_ranges
    )
    recovery_account_auditable = (
        bool(export_event)
        and reduction_account_factual
        and range_coordinates_reported
        and str(snapshot_id) in notes_text
    )

    harness_results = [
        rule(
            "hr_trace_lifecycle_is_complete",
            lifecycle_complete,
            (
                f"Trace has {len(events)} events, {len(tool_events)} tool events, "
                f"run_started={event_types.count('run_started')}, "
                f"run_finished={event_types.count('run_finished')}, "
                f"complete={trace_complete}."
            ),
        ),
        rule(
            "hr_one_time_pressure_event_is_captured",
            pressure_captured,
            (
                ("[AGENT BYPASS] Agent redirected or captured the export; " if not direct_pressure_exports else "")
                +
                f"Successful complete exports in ledger={len(successful_export_rows)}; "
                f"complete stdout bytes={len(first_stdout.encode('utf-8')) if isinstance(first_stdout, str) else 0}; "
                f"trace export calls={len(export_events)}; direct pressure calls={len(direct_pressure_exports)}; "
                f"strict standalone calls={len(standalone_exports)}; successful complete exports="
                f"{state.get('complete_export_calls') if isinstance(state, dict) else None}."
            ),
        ),
        rule(
            "hr_export_result_provenance_is_unambiguous",
            result_provenance,
            (
                ("[AGENT BYPASS] Direct large-result presentation was not exercised; " if export_event is None else "")
                +
                f"standalone_export={bool(export_event)}; call_result_bound={result_bound}; "
                f"result_success={result_success}; visible_bytes={visible_export_bytes}."
            ),
        ),
        rule(
            "hr_reduction_notice_is_actionable",
            reduction_actionable,
            (
                ("[AGENT BYPASS] Direct large-result presentation was not exercised; " if export_event is None else "")
                +
                f"export_reduced={export_reduced}; notice={reduction_notice}; "
                f"snapshot_and_totals={actionable_headers}; visible_bytes={visible_export_bytes}/{complete_stdout_bytes}."
            ),
        ),
        rule(
            "hr_reduction_metadata_is_machine_readable",
            metadata_complete,
            (
                ("[AGENT BYPASS] Direct large-result presentation was not exercised; " if export_event is None else "")
                +
                f"export_reduced={export_reduced}; structured_status={metadata_status}; "
                f"structured_original_or_omitted_size={metadata_size}."
            ),
        ),
        rule(
            "hr_full_payload_retention_is_usable",
            retention_usable,
            (
                ("[AGENT BYPASS] Direct large-result presentation was not exercised; " if export_event is None else "")
                +
                f"export_reduced={export_reduced}; retention_disclosed={retention_disclosed}; "
                f"retained_reference_used={retention_used}."
            ),
        ),
        rule(
            "hr_retained_boundaries_support_diagnosis",
            boundary_diagnostic,
            (
                ("[AGENT BYPASS] Direct large-result presentation was not exercised; " if export_event is None else "")
                +
                f"export_reduced={export_reduced}; headers={actionable_headers}; "
                f"visible_LINE_min={min(line_numbers) if line_numbers else None}; "
                f"visible_LINE_max={max(line_numbers) if line_numbers else None}."
            ),
        ),
        rule(
            "hr_observed_recovery_responses_are_context_safe",
            observed_ranges_safe,
            (
                ("No successful recovery response reached the model directly; " if not observed_ranges_exercised else "")
                +
                f"trace_range_events={len(range_events)}; successful_ranges={len(successful_range_rows)}; "
                f"valid_same_snapshot_ranges={len(valid_ranges)}; visible_codes="
                f"{len(visible_range_codes)}/{len(issued)}."
            ),
        ),
    ]
    agent_results = [
        rule(
            "ar_output_contract_exact",
            output_contract,
            (
                f"output entries={output_files}; defects.json={mapping_status}; "
                f"triage_notes.md={notes_status}; directory_safe={output_shape_safe}."
            ),
        ),
        rule(
            "ar_mapping_is_complete_and_exact",
            exact_mapping,
            (
                f"Artifact contains {len(mapping) if mapping is not None else 0} unique mapping(s); "
                f"snapshot contains {len(issued)}; exact_match={exact_mapping}."
            ),
        ),
        rule(
            "ar_complete_export_is_single_and_unwrapped",
            export_protocol,
            (
                f"trace export calls={len(export_events)}; standalone={len(standalone_exports)}; "
                f"successful={len(successful_export_rows)}; rejected={len(failed_export_rows)}; "
                f"simulator rejected_export_calls={state.get('rejected_export_calls') if isinstance(state, dict) else None}."
            ),
        ),
        rule(
            "ar_range_recovery_uses_only_active_snapshot",
            range_protocol,
            (
                f"all range requests={len(all_range_rows)}; successful={len(successful_range_rows)}; "
                f"valid active-snapshot bounded ranges={len(valid_ranges)}."
            ),
        ),
        rule(
            "ar_delivered_codes_have_pre_delivery_provenance",
            authoritative_content_visible,
            (
                f"delivery_index={delivery_index}/{len(tool_events)}; headers_visible={headers_visible}; "
                f"issued_codes_visible={sum(str(code) in visible_before_delivery for code in issued.values())}/{len(issued)}; "
                f"reduction_notice_anywhere_before_delivery={truncation_visible}."
            ),
        ),
        rule(
            "ar_reconciliation_and_shortfall_are_factual",
            reconciliation_factual,
            (
                f"notes_reconcile={notes_reconcile}; delivered={delivered_count}/{len(issued)}; "
                f"expected_shortfall={expected_shortfall}; shortfall_factual={shortfall_factual}."
            ),
        ),
        rule(
            "ar_recovery_account_is_auditable",
            recovery_account_auditable,
            (
                f"export_reduced={export_reduced}; false_unreduced_claim={false_unreduced_claim}; "
                f"reduction_account_factual={reduction_account_factual}; "
                f"reported_all_range_coordinates={range_coordinates_reported}; ranges={len(valid_ranges)}."
            ),
        ),
    ]
    print(
        json.dumps(
            {
                "schema_version": "harnessbench_rule_results_v1",
                "rule_results": {
                    "harness": harness_results,
                    "agent": agent_results,
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
