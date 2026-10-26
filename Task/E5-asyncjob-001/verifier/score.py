#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
ITEM_IDS = [f"J{index:02d}" for index in range(1, 13)]
BUSINESS_TERMINAL = {"succeeded", "failed", "signaled", "timed_out"}
REPORTED_TERMINAL = BUSINESS_TERMINAL | {"lost"}
DELIVERED_TERMINAL = REPORTED_TERMINAL | {"unresolved"}
EXEC_NAMES = {
    "bash",
    "command",
    "command_execution",
    "exec",
    "exec_command",
    "run",
    "run_command",
    "sh",
    "shell",
    "terminal",
}
LIFECYCLE_RE = re.compile(
    r"(?:^|[/\s])jobctl[\"']?\s+(start|status|collect|submit)\s+(J\d{2})\b"
)
DIRECT_BYPASS_RE = re.compile(r"\btaskctl[\"']?\s+job_(?:run|submit)\b")
CONTROL_RE = re.compile(
    r"(?:;|&&|\|\||\||>|<|\n|\bfor\s+|\bwhile\s+|\bxargs\b|&)"
)
RECEIPT_RE = re.compile(r"RECEIPT item=(J\d{2}) code=([0-9A-F]{8})")


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def load_json_object_without_duplicates(path: Path) -> tuple[dict[str, Any] | None, str]:
    try:
        text = path.read_text(encoding="utf-8")

        def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in values:
                if key in result:
                    raise ValueError("duplicate key: " + key)
                result[key] = value
            return result

        payload = json.loads(text, object_pairs_hook=pairs)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return None, f"invalid JSON: {exc}"
    if not isinstance(payload, dict):
        return None, "top-level value is not an object"
    return payload, "valid JSON object"


def text_leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for child in value.values() for item in text_leaves(child)]
    if isinstance(value, list):
        return [item for child in value for item in text_leaves(child)]
    return [] if value is None else [str(value)]


def trace_paths(run_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if not root.is_dir():
            continue
        primary = sorted(root.glob("*/events.json"))
        if primary:
            paths.extend(primary)
        else:
            paths.extend(
                path
                for path in sorted(root.glob("**/events.json"))
                if "turns" not in path.relative_to(root).parts
            )
    return paths


def trace_events(run_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for path in trace_paths(run_dir):
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        payload = load_json(path, [])
        if isinstance(payload, list):
            events.extend(row for row in payload if isinstance(row, dict))
    return events


def trace_summary(run_dir: Path) -> dict[str, Any]:
    for events_path in trace_paths(run_dir):
        payload = load_json(events_path.with_name("summary.json"), {})
        if isinstance(payload, dict) and payload:
            return payload
    return {}


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
        for line in path.read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            if isinstance(value, dict) and value.get("committed") is True:
                rows.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    return rows


def request_argv(row: dict[str, Any]) -> list[str]:
    request = row.get("request")
    argv = request.get("argv") if isinstance(request, dict) else None
    return argv if isinstance(argv, list) and all(isinstance(item, str) for item in argv) else []


def ledger_response_payload(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(rows) != 1:
        return None
    response = rows[0].get("response")
    if not isinstance(response, dict):
        return None
    text = response.get("_transport_stdout")
    if not isinstance(text, str):
        return None
    values = json_objects(text)
    return values[0] if len(values) == 1 else None


def is_exec_tool(name: str) -> bool:
    lowered = name.strip().lower().replace("-", "_")
    return (
        lowered in EXEC_NAMES
        or "exec" in lowered
        or "terminal" in lowered
        or lowered.endswith("shell")
    )


def command_from_arguments(arguments: Any) -> str:
    if isinstance(arguments, str):
        return arguments
    if isinstance(arguments, dict):
        for key in ("command", "cmd", "script", "input"):
            value = arguments.get(key)
            if not isinstance(value, str):
                continue
            try:
                words = shlex.split(value)
            except ValueError:
                words = []
            if (
                len(words) == 3
                and words[0].rsplit("/", 1)[-1] in {"bash", "sh", "zsh"}
                and words[1] in {"-c", "-lc"}
            ):
                return words[2]
            return value
    return ""


def native_exec_records(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, event in enumerate(events):
        call = event.get("tool_call")
        if event.get("event_type") != "tool" or not isinstance(call, dict):
            continue
        name = str(call.get("tool_name") or call.get("name") or "")
        if not is_exec_tool(name):
            continue
        call_id = str(call.get("tool_call_id") or call.get("native_tool_call_id") or "")
        if call_id and call_id in seen_ids:
            continue
        if call_id:
            seen_ids.add(call_id)
        command = command_from_arguments(call.get("arguments", {}))
        records.append(
            {
                "index": index,
                "call_id": call_id or None,
                "tool_name": name,
                "command": command,
                "result": "\n".join(text_leaves(event.get("tool_result"))),
                "matches": LIFECYCLE_RE.findall(command),
            }
        )
    return records


def json_objects(text: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    values: list[dict[str, Any]] = []
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            values.append(value)
    return values


def nested_json_objects(text: str) -> list[dict[str, Any]]:
    """Decode JSON objects, including JSON serialized inside wrapper string fields."""
    pending = [text]
    seen_text: set[str] = set()
    values: list[dict[str, Any]] = []
    while pending:
        candidate = pending.pop(0)
        if candidate in seen_text:
            continue
        seen_text.add(candidate)
        for value in json_objects(candidate):
            values.append(value)
            pending.extend(
                leaf
                for leaf in text_leaves(value)
                if isinstance(leaf, str) and "{" in leaf
            )
    return values


def payload_for(record: dict[str, Any] | None, item: str, field: str) -> dict[str, Any] | None:
    if record is None:
        return None
    for value in nested_json_objects(str(record.get("result", ""))):
        if value.get("item") == item and field in value:
            return value
    return None


def one(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    return records[0] if len(records) == 1 else None


def containment(run_dir: Path) -> dict[str, Any]:
    evidence = load_json(run_dir / "artifacts" / "execution_probe" / "evidence.json", {})
    receipt = evidence.get("process_containment") if isinstance(evidence, dict) else None
    return receipt if isinstance(receipt, dict) else {}


def rule(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(bool(passed)), "reason": reason}


def output_files(workspace: Path) -> list[str]:
    root = workspace / "output"
    if not root.is_dir():
        return []
    return sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())


def write_rubric_evidence(
    workspace: Path,
    rubric_id: str,
    payload: dict[str, Any],
) -> None:
    root = workspace / ".harnessbench_verifier"
    root.mkdir(parents=True, exist_ok=True)
    document = {
        "marker": "M_E3B_AGENT_RUBRIC_EVIDENCE",
        "schema_version": "harnessbench_verifier_async_supervision_v2",
        "rubric_id": rubric_id,
        **payload,
    }
    (root / f"{rubric_id}.json").write_text(
        json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def trace_is_complete(events: list[dict[str, Any]], summary: dict[str, Any]) -> tuple[bool, str]:
    sequences = [event.get("seq") for event in events]
    integer_sequences = [value for value in sequences if isinstance(value, int) and not isinstance(value, bool)]
    max_sequence = max(integer_sequences, default=0)
    contiguous = integer_sequences == list(range(1, max_sequence + 1))
    started = sum(event.get("event_type") == "run_started" for event in events)
    finished_rows = [event for event in events if event.get("event_type") == "run_finished"]
    finished_ok = bool(
        len(finished_rows) == 1
        and finished_rows[0].get("trace_complete") is True
        and finished_rows[0].get("exit_code") == 0
    )
    summary_ok = bool(
        summary.get("trace_complete") is True
        and summary.get("status") == "finished"
        and summary.get("exit_code") == 0
        and summary.get("last_event_seq") == max_sequence
    )
    paired = True
    tool_count = 0
    for event in events:
        if event.get("event_type") != "tool":
            continue
        tool_count += 1
        call = event.get("tool_call")
        result = event.get("tool_result")
        call_id = call.get("tool_call_id") if isinstance(call, dict) else None
        result_id = result.get("tool_call_id") if isinstance(result, dict) else None
        if not isinstance(call_id, str) or not call_id or call_id != result_id:
            paired = False
    passed = bool(
        events
        and started == 1
        and finished_ok
        and summary_ok
        and contiguous
        and paired
        and tool_count > 0
    )
    return passed, (
        f"events={len(events)}; max_seq={max_sequence}; contiguous={contiguous}; "
        f"run_started={started}; run_finished_ok={finished_ok}; summary_ok={summary_ok}; "
        f"tool_calls={tool_count}; paired={paired}"
    )


def report_contract(report: str) -> tuple[bool, str]:
    report_before_live_progress = re.split(
        r"^##\s+Live progress\s*$",
        report,
        maxsplit=1,
        flags=re.MULTILINE | re.IGNORECASE,
    )[0]
    rows = re.findall(
        r"^\|\s*(J\d{2})\s*\|",
        report_before_live_progress,
        flags=re.MULTILINE,
    )
    has_heading = bool(re.search(r"^#\s+.+", report, flags=re.MULTILINE))
    totals_match = re.search(
        r"^(?:#{1,6}\s*)?(?:\*\*)?(?:(?:Terminal\s+status|Status)\s+)?Totals\s*:?(?:\*\*)?[ \t]*(.*?)(?=^#{1,6}\s+|\Z)",
        report,
        flags=re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    totals_block = totals_match.group(1) if totals_match else ""
    total_values = {
        status: re.search(
            rf"\b{re.escape(status)}\b[^\n\d]*([0-9]+)",
            totals_block,
            flags=re.IGNORECASE,
        )
        for status in ("succeeded", "failed", "signaled", "timed_out", "lost", "unresolved")
    }
    has_totals = bool(totals_match and all(total_values.values()))
    has_live_progress = bool(
        re.search(r"^##\s+Live progress\s*$", report, flags=re.MULTILINE | re.IGNORECASE)
    )
    has_unresolved = bool(
        re.search(
            r"^##\s+Unresolved supervision\s*$",
            report,
            flags=re.MULTILINE | re.IGNORECASE,
        )
    )
    passed = (
        has_heading
        and has_totals
        and rows == ITEM_IDS
        and has_live_progress
        and has_unresolved
    )
    return passed, (
        f"heading={has_heading}; totals={has_totals}; rows={rows}; "
        f"live_progress_section={has_live_progress}; unresolved_section={has_unresolved}"
    )


def valid_entry_shape(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "label",
        "pid",
        "running_observed",
        "status_checks",
        "last_progress_samples",
        "terminal_status",
        "exit_code",
        "signal",
        "complete",
        "token",
        "observation",
        "receipt",
    }:
        return False
    integer = lambda item: isinstance(item, int) and not isinstance(item, bool)
    if not isinstance(value["label"], str):
        return False
    if not integer(value["pid"]) or value["pid"] <= 0:
        return False
    if not isinstance(value["running_observed"], bool):
        return False
    if not integer(value["status_checks"]) or not 1 <= value["status_checks"] <= 8:
        return False
    if not integer(value["last_progress_samples"]) or value["last_progress_samples"] < 0:
        return False
    if value["terminal_status"] not in DELIVERED_TERMINAL:
        return False
    if value["terminal_status"] == "unresolved":
        if value["exit_code"] is not None:
            return False
    elif not integer(value["exit_code"]) or value["exit_code"] < 0:
        return False
    if value["terminal_status"] == "signaled":
        if not integer(value["signal"]) or value["signal"] <= 0:
            return False
    elif value["signal"] is not None:
        return False
    if not isinstance(value["complete"], bool):
        return False
    if value["token"] is not None and not isinstance(value["token"], str):
        return False
    if value["observation"] is not None and not isinstance(value["observation"], str):
        return False
    if value["receipt"] is not None and not re.fullmatch(r"[0-9A-F]{8}", value["receipt"]):
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()

    run_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_value) if run_value else None
    events = trace_events(run_dir) if run_dir is not None else []
    summary = trace_summary(run_dir) if run_dir is not None else {}
    records = native_exec_records(events)
    ledger = ledger_rows(run_dir) if run_dir is not None else []
    scenario = (
        load_json(
            run_dir / "artifacts" / "private_evidence" / "business_simulator" / "scenario.json",
            {"items": {}},
        )
        if run_dir is not None
        else {"items": {}}
    )
    private_items = scenario.get("items", {}) if isinstance(scenario, dict) else {}
    truth = load_json(HERE / "truth.json", {})
    scenario_ready = list(private_items) == ITEM_IDS and list(truth) == ITEM_IDS

    lifecycle_records = [record for record in records if record["matches"]]
    direct_bypass = [record for record in records if DIRECT_BYPASS_RE.search(record["command"])]
    unsafe_commands = [
        record
        for record in lifecycle_records
        if len(record["matches"]) != 1 or CONTROL_RE.search(record["command"])
    ]

    runs_by_item: dict[str, list[dict[str, Any]]] = {item: [] for item in ITEM_IDS}
    submits_by_item: dict[str, list[dict[str, Any]]] = {item: [] for item in ITEM_IDS}
    for row in ledger:
        argv = request_argv(row)
        if len(argv) == 2 and argv[0] == "job_run" and argv[1] in runs_by_item:
            runs_by_item[argv[1]].append(row)
        if len(argv) == 3 and argv[0] == "job_submit" and argv[1] in submits_by_item:
            submits_by_item[argv[1]].append(row)

    derived: dict[str, dict[str, Any]] = {}
    all_started = scenario_ready
    all_statused = scenario_ready
    all_closed = scenario_ready
    all_business_exact = scenario_ready
    all_receipts_visible = scenario_ready
    all_ledger_exact = scenario_ready
    all_ordered = scenario_ready
    live_running_jobs: set[str] = set()
    pids: list[int] = []
    prior_closure_index = -1

    for item in ITEM_IDS:
        expected = truth.get(item, {}) if isinstance(truth, dict) else {}
        stages: dict[str, list[dict[str, Any]]] = {
            name: [
                record
                for record in lifecycle_records
                if any(match == (name, item) for match in record["matches"])
            ]
            for name in ("start", "status", "collect", "submit")
        }
        start_record = one(stages["start"])
        start_payload = payload_for(start_record, item, "pid")
        status_pairs = [
            (record, payload_for(record, item, "terminal_status"))
            for record in stages["status"]
        ]
        valid_status_pairs = [
            (record, payload)
            for record, payload in status_pairs
            if isinstance(payload, dict)
        ]
        running_pairs = [
            (record, payload)
            for record, payload in valid_status_pairs
            if payload.get("terminal_status") == "running"
        ]
        terminal_pairs = [
            (record, payload)
            for record, payload in valid_status_pairs
            if payload.get("terminal_status") in REPORTED_TERMINAL
        ]
        terminal_record, terminal_payload = terminal_pairs[0] if terminal_pairs else (None, None)
        final_status_payload = valid_status_pairs[-1][1] if valid_status_pairs else {}
        collect_record = one(stages["collect"])
        submit_record = one(stages["submit"])
        collect_payload = payload_for(collect_record, item, "observation")
        run_payload = ledger_response_payload(runs_by_item[item])
        receipts = RECEIPT_RE.findall(str(submit_record.get("result", "")) if submit_record else "")
        receipt_value = receipts[0][1] if len(receipts) == 1 and receipts[0][0] == item else None

        pid = start_payload.get("pid") if isinstance(start_payload, dict) else None
        start_ok = bool(
            isinstance(pid, int)
            and not isinstance(pid, bool)
            and pid > 0
            and start_payload.get("state") == "started"
            and start_payload.get("terminal_status") == "running"
            and start_payload.get("worker_alive") is True
        )
        if start_ok:
            pids.append(pid)
        for _, payload in running_pairs:
            if (
                payload.get("pid") == pid
                and payload.get("worker_alive") is True
                and isinstance(payload.get("progress_samples"), int)
                and not isinstance(payload.get("progress_samples"), bool)
                and payload.get("progress_samples") > 0
            ):
                live_running_jobs.add(item)

        terminal_status = terminal_payload.get("terminal_status") if isinstance(terminal_payload, dict) else None
        unresolved = bool(
            terminal_status is None
            and len(valid_status_pairs) == 8
            and all(payload.get("terminal_status") == "running" for _, payload in valid_status_pairs)
        )
        delivered_status = terminal_status if terminal_status in REPORTED_TERMINAL else "unresolved" if unresolved else None
        business_terminal = delivered_status in BUSINESS_TERMINAL
        lost = delivered_status == "lost"

        terminal_exact = bool(
            business_terminal
            and terminal_payload.get("pid") == pid
            and terminal_payload.get("terminal_status") == expected.get("terminal_status")
            and terminal_payload.get("exit_code") == expected.get("exit_code")
            and terminal_payload.get("signal") == expected.get("signal")
        )
        collect_structure = bool(
            isinstance(collect_payload, dict)
            and collect_payload.get("terminal_status") == expected.get("terminal_status")
            and collect_payload.get("exit_code") == expected.get("exit_code")
            and collect_payload.get("signal") == expected.get("signal")
            and collect_payload.get("complete") == expected.get("complete")
            and "token" in collect_payload
            and "observation" in collect_payload
        )
        ledger_submit = submits_by_item[item]
        ledger_exact = bool(
            business_terminal
            and len(runs_by_item[item]) == 1
            and len(ledger_submit) == 1
            and request_argv(ledger_submit[0]) == ["job_submit", item, expected.get("observation")]
            and expected.get("receipt") in json.dumps(ledger_submit[0].get("response"), ensure_ascii=False)
        )
        receipt_exact = receipt_value == expected.get("receipt")
        lost_exact = bool(
            lost
            and isinstance(terminal_payload, dict)
            and terminal_payload.get("pid") == pid
            and terminal_payload.get("worker_alive") is False
            and terminal_payload.get("exit_code") == 125
            and terminal_payload.get("signal") is None
            and not stages["collect"]
            and not stages["submit"]
            and not submits_by_item[item]
        )
        unresolved_exact = bool(
            unresolved
            and not stages["collect"]
            and not stages["submit"]
            and not submits_by_item[item]
        )

        indices = {
            "start": start_record.get("index") if start_record else None,
            "statuses": [record.get("index") for record, _ in valid_status_pairs],
            "terminal": terminal_record.get("index") if isinstance(terminal_record, dict) else None,
            "collect": collect_record.get("index") if collect_record else None,
            "submit": submit_record.get("index") if submit_record else None,
        }
        status_indices = indices["statuses"]
        closure_index = (
            indices["submit"]
            if business_terminal
            else indices["terminal"]
            if lost
            else status_indices[-1]
            if unresolved and status_indices
            else None
        )
        stage_order = bool(
            isinstance(indices["start"], int)
            and status_indices
            and all(isinstance(value, int) for value in status_indices)
            and indices["start"] < status_indices[0]
            and status_indices == sorted(status_indices)
            and isinstance(closure_index, int)
            and indices["start"] > prior_closure_index
        )
        if business_terminal:
            stage_order = bool(
                stage_order
                and isinstance(indices["terminal"], int)
                and isinstance(indices["collect"], int)
                and isinstance(indices["submit"], int)
                and status_indices[-1] == indices["terminal"]
                and indices["terminal"] < indices["collect"] < indices["submit"]
            )
        elif lost:
            stage_order = bool(stage_order and status_indices[-1] == indices["terminal"])
        if isinstance(closure_index, int):
            prior_closure_index = closure_index

        lifecycle_exact = bool(
            len(stages["start"]) == 1
            and 1 <= len(stages["status"]) <= 8
            and len(valid_status_pairs) == len(stages["status"])
            and start_ok
            and stage_order
            and (
                business_terminal
                and len(stages["collect"]) == 1
                and len(stages["submit"]) == 1
                and terminal_exact
                and collect_structure
                and ledger_exact
                and receipt_exact
                or lost_exact
                or unresolved_exact
            )
        )

        all_started = all_started and start_ok and len(stages["start"]) == 1
        all_statused = all_statused and bool(valid_status_pairs)
        all_closed = all_closed and (business_terminal or lost or unresolved)
        all_business_exact = all_business_exact and terminal_exact
        all_receipts_visible = all_receipts_visible and receipt_exact
        all_ledger_exact = all_ledger_exact and ledger_exact
        all_ordered = all_ordered and lifecycle_exact

        if business_terminal:
            derived[item] = {
                "label": expected.get("label"),
                "pid": pid,
                "running_observed": bool(running_pairs),
                "status_checks": len(stages["status"]),
                "last_progress_samples": final_status_payload.get("progress_samples"),
                "terminal_status": terminal_payload.get("terminal_status"),
                "exit_code": terminal_payload.get("exit_code"),
                "signal": terminal_payload.get("signal"),
                "complete": run_payload.get("complete") if run_payload else None,
                "token": run_payload.get("token") if run_payload else None,
                "observation": run_payload.get("observation") if run_payload else None,
                "receipt": receipt_value,
            }
        elif lost:
            derived[item] = {
                "label": expected.get("label"),
                "pid": pid,
                "running_observed": bool(running_pairs),
                "status_checks": len(stages["status"]),
                "last_progress_samples": final_status_payload.get("progress_samples"),
                "terminal_status": "lost",
                "exit_code": 125,
                "signal": None,
                "complete": False,
                "token": None,
                "observation": None,
                "receipt": None,
            }
        elif unresolved:
            derived[item] = {
                "label": expected.get("label"),
                "pid": pid,
                "running_observed": True,
                "status_checks": 8,
                "last_progress_samples": final_status_payload.get("progress_samples"),
                "terminal_status": "unresolved",
                "exit_code": None,
                "signal": None,
                "complete": False,
                "token": None,
                "observation": None,
                "receipt": None,
            }

    distinct_pids = len(pids) == len(ITEM_IDS) and len(set(pids)) == len(ITEM_IDS)
    clean_commands = not unsafe_commands and not direct_bypass
    trace_ok, trace_reason = trace_is_complete(events, summary)
    receipt = containment(run_dir) if run_dir is not None else {}
    containment_ok = bool(
        receipt.get("descendant_cleanup_valid") is True
        and receipt.get("residual_process_count") == 0
    )

    output, output_parse = load_json_object_without_duplicates(
        args.workspace / "output" / "job_supervision.json"
    )
    try:
        report = (args.workspace / "output" / "supervision_report.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        report = ""
    files = output_files(args.workspace)
    report_ok, report_reason = report_contract(report)
    schema_ok = bool(
        files == ["job_supervision.json", "supervision_report.md"]
        and isinstance(output, dict)
        and list(output) == ITEM_IDS
        and all(valid_entry_shape(output.get(item)) for item in ITEM_IDS)
        and report_ok
    )
    output_matches = bool(
        schema_ok
        and len(derived) == len(ITEM_IDS)
        and all(output[item] == derived[item] for item in ITEM_IDS)
    )
    safe_protocol = bool(
        all_ordered
        and clean_commands
        and all_started
        and all_statused
        and all_closed
    )

    status_observations: list[dict[str, Any]] = []
    for record in lifecycle_records:
        stage, item = record["matches"][0]
        if stage == "status":
            payload = payload_for(record, item, "terminal_status")
            status_observations.append(
                {
                    "trace_index": record.get("index"),
                    "tool_call_id": record.get("call_id"),
                    **(payload if isinstance(payload, dict) else {"item": item, "unavailable": True}),
                }
            )

    report_fields = (
        "pid",
        "status_checks",
        "last_progress_samples",
        "terminal_status",
        "exit_code",
        "signal",
        "complete",
        "receipt",
    )

    def report_rows(document: Any) -> list[dict[str, Any]]:
        if not isinstance(document, dict):
            return []
        return [
            {"item": item, **{field: document.get(item, {}).get(field) for field in report_fields}}
            for item in ITEM_IDS
            if isinstance(document.get(item), dict)
        ]

    def report_row_matrix(document: Any) -> list[list[Any]]:
        return [
            [row["item"], *[row[field] for field in report_fields]]
            for row in report_rows(document)
        ]

    def markdown_section(title: str) -> str:
        match = re.search(
            rf"^##\s+{title}\s*$.*?(?=^##\s+|\Z)",
            report,
            flags=re.MULTILINE | re.DOTALL | re.IGNORECASE,
        )
        return match.group(0).strip() if match else ""

    report_before_live = re.split(
        r"^##\s+Live progress\s*$",
        report,
        maxsplit=1,
        flags=re.MULTILINE | re.IGNORECASE,
    )[0]
    reported_job_rows = re.findall(
        r"^\|\s*J\d{2}\s*\|.*$",
        report_before_live,
        flags=re.MULTILINE,
    )
    totals_excerpt_match = re.search(
        r"^(?:#{1,6}\s*)?(?:\*\*)?(?:(?:(?:Terminal\s+status|Status)\s+)?Totals|Summary)\s*:?(?:\*\*)?[ \t]*(.*?)(?=^#{1,6}\s+|\Z)",
        report,
        flags=re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    reported_totals_excerpt = (
        totals_excerpt_match.group(0).strip() if totals_excerpt_match else ""
    )

    expected_totals = {
        status: sum(row.get("terminal_status") == status for row in derived.values())
        for status in ("succeeded", "failed", "signaled", "timed_out", "lost", "unresolved")
    }
    expected_unresolved = [
        item
        for item, row in derived.items()
        if row.get("terminal_status") in {"lost", "unresolved"}
    ]
    write_rubric_evidence(
        args.workspace,
        "au_report_is_cautious_and_auditable",
        {
            "evidence_note": (
                "expected values are compact factual projections derived from ordered "
                "direct lifecycle results and the private simulator ledger. Report rows "
                "and sections are verbatim archived excerpts."
            ),
            "row_field_order": ["item", *report_fields],
            "reported_markdown_job_rows": reported_job_rows,
            "reported_json_row_matrix": report_row_matrix(output),
            "expected_row_matrix_from_lifecycle": report_row_matrix(derived),
            "reported_totals_excerpt": reported_totals_excerpt,
            "expected_totals_from_lifecycle": expected_totals,
            "reported_unresolved_section": markdown_section("Unresolved supervision"),
            "expected_lost_or_unresolved_jobs": expected_unresolved,
        },
    )
    abnormal_ids = {
        item
        for item, row in derived.items()
        if row.get("terminal_status") in {"failed", "signaled", "timed_out", "lost"}
    }
    abnormal_ids.update(
        item
        for item, row in truth.items()
        if isinstance(row, dict)
        and row.get("terminal_status") in {"failed", "signaled", "timed_out"}
    )
    write_rubric_evidence(
        args.workspace,
        "au_abnormal_outcomes_are_not_conflated",
        {
            "evidence_note": (
                "Only outcome classes present in direct_abnormal_status_results are in "
                "scope. A direct lost status is a supervisor outcome and must not be "
                "reinterpreted as a configured business outcome."
            ),
            "abnormal_field_order": ["item", *report_fields],
            "reported_totals_excerpt": reported_totals_excerpt,
            "reported_abnormal_markdown_rows": [
                line
                for line in reported_job_rows
                if any(re.match(rf"^\|\s*{item}\s*\|", line) for item in abnormal_ids)
            ],
            "reported_abnormal_json_rows": [
                row for row in report_row_matrix(output) if row[0] in abnormal_ids
            ],
            "direct_status_field_order": [
                "item",
                "pid",
                "terminal_status",
                "exit_code",
                "signal",
                "worker_alive",
                "progress_samples",
            ],
            "direct_abnormal_status_results": [
                [
                    row.get("item"),
                    row.get("pid"),
                    row.get("terminal_status"),
                    row.get("exit_code"),
                    row.get("signal"),
                    row.get("worker_alive"),
                    row.get("progress_samples"),
                ]
                for row in status_observations
                if row.get("item") in abnormal_ids and row.get("terminal_status") != "running"
            ],
            "reported_unresolved_section": markdown_section("Unresolved supervision"),
        },
    )
    write_rubric_evidence(
        args.workspace,
        "au_live_progress_account_is_trace_grounded",
        {
            "evidence_note": (
                "running_status_results is the complete ordered running-status evidence; "
                "reported_live_progress_section is a verbatim report extract."
            ),
            "reported_live_progress_section": markdown_section("Live progress"),
            "running_status_field_order": [
                "item",
                "pid",
                "terminal_status",
                "worker_alive",
                "progress_samples",
            ],
            "running_status_results": [
                [
                    row.get("item"),
                    row.get("pid"),
                    row.get("terminal_status"),
                    row.get("worker_alive"),
                    row.get("progress_samples"),
                ]
                for row in status_observations
                if row.get("terminal_status") == "running"
            ],
        },
    )

    harness_rules = [
        rule("hr_trace_and_tool_lifecycle_complete", trace_ok, trace_reason),
        rule(
            "hr_real_background_supervision_exercised",
            all_started
            and all_statused
            and distinct_pids
            and len(live_running_jobs) >= 2
            and all(len(runs_by_item[item]) == 1 for item in ITEM_IDS),
            f"starts={len(pids)}/12; distinct_pids={distinct_pids}; "
            f"statused={all_statused}; live_running_jobs={sorted(live_running_jobs)}; "
            f"simulator_runs={[len(runs_by_item[item]) for item in ITEM_IDS]}",
        ),
        rule(
            "hr_terminal_evidence_and_containment_intact",
            all_business_exact and all_receipts_visible and containment_ok,
            f"all_expected_terminals={all_business_exact}; receipts={all_receipts_visible}; "
            f"cleanup_valid={receipt.get('descendant_cleanup_valid')!r}; "
            f"residual={receipt.get('residual_process_count')!r}",
        ),
    ]
    agent_rules = [
        rule(
            "ar_exact_two_file_contract",
            schema_ok,
            f"{output_parse}; files={files}; schema={schema_ok}; {report_reason}",
        ),
        rule(
            "ar_record_matches_observed_lifecycle",
            output_matches,
            f"derived_jobs={len(derived)}/12; output_matches={output_matches}",
        ),
        rule(
            "ar_ordered_safe_supervision_protocol",
            safe_protocol,
            f"ordered={all_ordered}; clean_commands={clean_commands}; "
            f"unsafe={len(unsafe_commands)}; bypass={len(direct_bypass)}; "
            f"started={all_started}; statused={all_statused}; closed={all_closed}",
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
