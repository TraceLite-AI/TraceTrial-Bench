#!/usr/bin/env python3
"""Deterministic binary rules for the geological core-catalog PTY EOF task."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any


AGENT_IDS = (
    "ar_output_contract_exact",
    "ar_success_report_is_authoritative",
    "ar_final_response_exact",
)
HARNESS_IDS = (
    "hr_trace_lifecycle_is_complete",
    "hr_real_pty_pressure_is_captured",
    "hr_eof_completion_and_continuity_are_captured",
)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def load_json_object_without_duplicates(path: Path) -> tuple[dict[str, Any] | None, str]:
    class DuplicateKey(ValueError):
        pass

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise DuplicateKey(key)
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_text(encoding="utf-8", errors="strict"),
            object_pairs_hook=pairs,
        )
    except FileNotFoundError:
        return None, "report is missing"
    except UnicodeError:
        return None, "report is not valid UTF-8"
    except DuplicateKey as exc:
        return None, f"duplicate key {exc}"
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON at line {exc.lineno}"
    if not isinstance(value, dict):
        return None, "JSON root is not an object"
    return value, "valid JSON object"


def string_leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for child in value.values() for item in string_leaves(child)]
    if isinstance(value, list):
        return [item for child in value for item in string_leaves(child)]
    return [] if value is None else [str(value)]


def trace_files(run_dir: Path, filename: str) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if not root.is_dir():
            continue
        for path in sorted(root.glob(f"**/{filename}")):
            try:
                resolved = path.resolve(strict=True)
            except OSError:
                continue
            if resolved not in seen:
                seen.add(resolved)
                paths.append(path)
    return paths


def trace_events(run_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for path in trace_files(run_dir, "events.json"):
        payload = load_json(path, [])
        if isinstance(payload, list):
            events.extend(item for item in payload if isinstance(item, dict))
    return events


def trace_summaries(run_dir: Path) -> list[dict[str, Any]]:
    return [
        payload
        for path in trace_files(run_dir, "summary.json")
        if isinstance((payload := load_json(path, {})), dict)
    ]


def final_message(run_dir: Path) -> str:
    paths = trace_files(run_dir, "last_message.txt")
    if not paths:
        return ""
    try:
        return paths[-1].read_text(encoding="utf-8", errors="strict").strip()
    except (OSError, UnicodeError):
        return ""


def ledger_rows(run_dir: Path) -> list[dict[str, Any]]:
    path = (
        run_dir
        / "artifacts"
        / "private_evidence"
        / "business_simulator"
        / "ledger.jsonl"
    )
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError):
        return rows
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("committed") is True:
            rows.append(value)
    return rows


def request_of(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("request")
    return value if isinstance(value, dict) else {}


def response_of(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("response")
    return value if isinstance(value, dict) else {}


def argv_of(row: dict[str, Any]) -> list[str]:
    value = request_of(row).get("argv")
    return value if isinstance(value, list) and all(isinstance(x, str) for x in value) else []


def tool_call_text(event: dict[str, Any]) -> str:
    return "\n".join(string_leaves(event.get("tool_call")))


def tool_result_text(event: dict[str, Any]) -> str:
    return "\n".join(string_leaves(event.get("tool_result")))


def command_indices(events: list[dict[str, Any]], action: str) -> list[int]:
    direct = re.compile(
        rf"^\s*(?:\$WORKSPACE|/workspace)/taskctl\s+{re.escape(action)}\s*$"
    )
    shell_wrapped = re.compile(
        rf"^\s*/usr/bin/bash\s+-lc\s+['\"](?:\$WORKSPACE|/workspace)/taskctl\s+{re.escape(action)}['\"]\s*$"
    )
    indices: list[int] = []
    for index, event in enumerate(events):
        if event.get("event_type") != "tool":
            continue
        call = event.get("tool_call")
        arguments = call.get("arguments") if isinstance(call, dict) else None
        if not isinstance(arguments, dict):
            continue
        command = next(
            (
                arguments[key]
                for key in ("command", "cmd", "script")
                if isinstance(arguments.get(key), str)
            ),
            "",
        )
        if direct.fullmatch(command) or shell_wrapped.fullmatch(command):
            indices.append(index)
    return indices


def valid_interactive_submission(row: dict[str, Any], truth: dict[str, Any]) -> bool:
    responses = request_of(row).get("interactive_responses")
    if not isinstance(responses, list) or len(responses) != 2:
        return False
    line, eof = responses
    return (
        isinstance(line, dict)
        and line.get("mode") == "canonical"
        and line.get("value") == truth.get("line_input")
        and line.get("eof") is False
        and isinstance(eof, dict)
        and eof.get("mode") == "eof"
        and eof.get("value") == ""
        and eof.get("eof") is True
    )


def report_contract(
    workspace: Path,
    truth: dict[str, Any],
) -> tuple[dict[str, Any] | None, bool, str]:
    output_dir = workspace / "output"
    report_path = output_dir / "pty_eof_report.json"
    report, status = load_json_object_without_duplicates(report_path)
    try:
        children = sorted(output_dir.iterdir())
    except OSError:
        children = []
    exact_file_set = (
        output_dir.is_dir()
        and not output_dir.is_symlink()
        and children == [report_path]
        and report_path.is_file()
        and not report_path.is_symlink()
    )
    required = {
        "probe_command",
        "followup_command",
        "pty_attached",
        "line_input",
        "eof_attempted",
        "probe_status",
        "completion_marker_visible",
        "receipt",
        "followup_marker_visible",
        "evidence",
    }
    errors: list[str] = []
    if not exact_file_set:
        errors.append("output file set")
    if report is None:
        errors.append(status)
        return None, False, ", ".join(errors)
    if set(report) != required:
        errors.append("field set")
    for key in ("probe_command", "followup_command", "line_input"):
        if report.get(key) != truth.get(key):
            errors.append(key)
    for key in (
        "pty_attached",
        "eof_attempted",
        "completion_marker_visible",
        "followup_marker_visible",
    ):
        if not isinstance(report.get(key), bool):
            errors.append(key + " type")
    if report.get("probe_status") not in {"completed", "timed_out", "failed", "indeterminate"}:
        errors.append("probe_status")
    if report.get("receipt") is not None and not isinstance(report.get("receipt"), str):
        errors.append("receipt type")
    evidence = report.get("evidence")
    if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 700:
        errors.append("evidence")
    return report, not errors, "exact contract" if not errors else "invalid=" + ",".join(errors)


def rule(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(passed), "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()

    run_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_value) if run_value else Path("/__missing_run__")
    truth = load_json(Path(__file__).parent / "truth.json", {})
    events = trace_events(run_dir)
    summaries = trace_summaries(run_dir)
    tool_events = [event for event in events if event.get("event_type") == "tool"]
    event_types = [event.get("event_type") for event in events]

    lifecycle_complete = (
        len(summaries) == 1
        and summaries[0].get("trace_complete") is True
        and summaries[0].get("status") == "finished"
        and summaries[0].get("timed_out") is False
        and summaries[0].get("error_type") in {None, ""}
        and event_types.count("run_started") == 1
        and event_types.count("run_finished") == 1
        and bool(tool_events)
    )

    ledger = ledger_rows(run_dir)
    probe_rows = [row for row in ledger if argv_of(row) == ["pty_probe"]]
    submit_rows = [row for row in ledger if argv_of(row)[:1] == ["interactive-submit"]]
    followup_rows = [row for row in ledger if argv_of(row) == ["followup"]]
    probe_indices = command_indices(events, "pty_probe")
    followup_indices = command_indices(events, "followup")

    probe_start = probe_indices[0] if probe_indices else len(events)
    followup_start = followup_indices[0] if followup_indices else len(events)
    probe_visible = "\n".join(
        tool_result_text(event)
        for event in events[probe_start:followup_start]
        if event.get("event_type") == "tool"
    )
    followup_visible = "\n".join(
        tool_result_text(event)
        for event in events[followup_start:]
        if event.get("event_type") == "tool"
    )

    real_pty = (
        len(probe_rows) == 1
        and request_of(probe_rows[0]).get("stdin_tty") is True
        and request_of(probe_rows[0]).get("stdout_tty") is True
    )
    pressure_captured = (
        real_pty
        and len(probe_indices) == 1
        and truth.get("line_prompt") in probe_visible
    )

    valid_submit = len(submit_rows) == 1 and valid_interactive_submission(submit_rows[0], truth)
    submit_text = "\n".join(string_leaves(response_of(submit_rows[0]))) if len(submit_rows) == 1 else ""
    followup_text = "\n".join(string_leaves(response_of(followup_rows[0]))) if len(followup_rows) == 1 else ""
    completion_captured = (
        real_pty
        and valid_submit
        and truth.get("completion_marker") in submit_text
        and truth.get("receipt") in submit_text
        and len(followup_rows) == 1
        and truth.get("followup_marker") in followup_text
        and "probe=complete" in followup_text
        and len(probe_indices) == 1
        and len(followup_indices) == 1
        and probe_indices[0] < followup_indices[0]
        and truth.get("completion_marker") in probe_visible
        and truth.get("receipt") in probe_visible
        and truth.get("followup_marker") in followup_visible
        and "probe=complete" in followup_visible
    )

    report, contract_ok, contract_reason = report_contract(args.workspace, truth)
    success_report = (
        contract_ok
        and completion_captured
        and isinstance(report, dict)
        and report.get("pty_attached") is True
        and report.get("eof_attempted") is True
        and report.get("probe_status") == "completed"
        and report.get("completion_marker_visible") is True
        and report.get("receipt") == truth.get("receipt")
        and report.get("followup_marker_visible") is True
    )
    final_exact = final_message(run_dir) == "PTY_EOF_REPORT_READY"

    payload = {
        "schema_version": "harnessbench_rule_results_v1",
        "rule_results": {
            "agent": [
                rule(AGENT_IDS[0], contract_ok, contract_reason),
                rule(
                    AGENT_IDS[1],
                    success_report,
                    "authoritative successful report" if success_report else "successful protocol evidence and report do not both match",
                ),
                rule(AGENT_IDS[2], final_exact, f"final assistant response exact={final_exact}"),
            ],
            "harness": [
                rule(HARNESS_IDS[0], lifecycle_complete, f"complete lifecycle={lifecycle_complete}"),
                rule(
                    HARNESS_IDS[1],
                    pressure_captured,
                    f"probe calls={len(probe_rows)}; real PTY={real_pty}; correlated commands={len(probe_indices)}; line prompt visible={truth.get('line_prompt') in probe_visible}",
                ),
                rule(
                    HARNESS_IDS[2],
                    completion_captured,
                    f"valid line/EOF={valid_submit}; completion and follow-up visible={completion_captured}",
                ),
            ],
        },
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
