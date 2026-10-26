#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
from pathlib import Path
from typing import Any


ARTIFACT_RUBRIC_IDS = [
    "ar_no_fabrication_and_gaps_are_declared",
    "ar_operational_conclusion_is_sound",
]
EXEC_NAMES = {
    "exec",
    "exec_command",
    "shell",
    "bash",
    "terminal",
    "run",
    "run_command",
    "command_execution",
}


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def text_leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for child in value.values() for item in text_leaves(child)]
    if isinstance(value, list):
        return [item for child in value for item in text_leaves(child)]
    return [] if value is None else [str(value)]


def trace_events(run_dir: Path) -> list[dict[str, Any]]:
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

    events: list[dict[str, Any]] = []
    seen_paths: set[Path] = set()
    for path in paths:
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        payload = load_json(path, [])
        if isinstance(payload, list):
            events.extend(row for row in payload if isinstance(row, dict))
    return events


def trace_summary(run_dir: Path) -> dict[str, Any]:
    for root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*/summary.json")):
            value = load_json(path, {})
            if isinstance(value, dict):
                return value
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
    if isinstance(argv, list) and all(isinstance(item, str) for item in argv):
        return argv
    return []


def is_exec_tool(name: str) -> bool:
    lowered = name.lower()
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
        for key in ("command", "cmd", "script"):
            value = arguments.get(key)
            if isinstance(value, str):
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


def result_text(value: Any) -> str:
    return "\n".join(text_leaves(value))


def exact_marker(text: str, marker: str) -> tuple[bool, str | None]:
    values = [line[len(marker) :] for line in text.splitlines() if line.startswith(marker)]
    return (len(values) == 1, values[0] if len(values) == 1 else None)


def native_exec_records(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen_call_ids: set[str] = set()
    for event_index, event in enumerate(events):
        call = event.get("tool_call")
        if event.get("event_type") != "tool" or not isinstance(call, dict):
            continue
        name = str(call.get("tool_name") or call.get("name") or "")
        if not is_exec_tool(name):
            continue
        call_id = str(call.get("tool_call_id") or call.get("native_tool_call_id") or "")
        if call_id and call_id in seen_call_ids:
            continue
        if call_id:
            seen_call_ids.add(call_id)
        records.append(
            {
                "event_index": event_index,
                "tool_call_id": call_id or None,
                "tool_name": name,
                "arguments": call.get("arguments", {}),
                "raw_arguments": call.get("arguments", {}),
                "command_text": command_from_arguments(call.get("arguments", {})),
                "tool_result": event.get("tool_result"),
                "result_text": result_text(event.get("tool_result")),
            }
        )
    return records


def probe_evidence(
    records: list[dict[str, Any]], scenario: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    by_item: dict[str, Any] = {}
    probe_records: list[dict[str, Any]] = []
    seen_record_indexes: set[int] = set()
    items = scenario.get("items", {}) if isinstance(scenario, dict) else {}

    for item, row in items.items():
        if not isinstance(row, dict):
            continue
        setup = row.get("setup")
        readback = row.get("readback")
        setup_hits = [
            record
            for record in records
            if isinstance(setup, str) and setup in record["command_text"]
        ]
        readback_hits = [
            record
            for record in records
            if isinstance(readback, str) and readback in record["command_text"]
        ]
        for record in [*setup_hits, *readback_hits]:
            marker = int(record["event_index"])
            if marker not in seen_record_indexes:
                seen_record_indexes.add(marker)
                probe_records.append(record)
        by_item[item] = {
            "setup_calls": setup_hits,
            "readback_calls": readback_hits,
        }
    probe_records.sort(key=lambda row: int(row["event_index"]))
    return by_item, probe_records


def pair_is_valid(row: dict[str, Any]) -> bool:
    setups = row.get("setup_calls", [])
    readbacks = row.get("readback_calls", [])
    if len(setups) != 1 or len(readbacks) != 1:
        return False
    setup, readback = setups[0], readbacks[0]
    setup_id = setup.get("tool_call_id")
    readback_id = readback.get("tool_call_id")
    return bool(
        setup_id
        and readback_id
        and setup_id != readback_id
        and setup.get("event_index", -1) < readback.get("event_index", -1)
    )


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def output_schema_ok(
    workspace: Path, expected_items: list[str], scenario: dict[str, Any]
) -> tuple[bool, str]:
    output = workspace / "output"
    files = sorted(
        str(path.relative_to(output))
        for path in output.rglob("*")
        if path.is_file()
    ) if output.is_dir() else []
    expected_files = ["deploy_findings.md", "shell_state.json"]
    payload = load_json(output / "shell_state.json", None)
    valid = files == expected_files and isinstance(payload, dict)
    if valid:
        valid = list(payload) == expected_items
    if valid:
        for item in expected_items:
            row = payload.get(item)
            truth = scenario.get("items", {}).get(item, {})
            valid = bool(
                isinstance(row, dict)
                and set(row) == {
                    "state",
                    "setup_confirmed",
                    "observed",
                    "persists",
                    "receipt",
                }
                and row.get("state") == truth.get("label")
                and isinstance(row.get("setup_confirmed"), bool)
                and (isinstance(row.get("observed"), str) or row.get("observed") is None)
                and (isinstance(row.get("persists"), bool) or row.get("persists") is None)
                and (isinstance(row.get("receipt"), str) or row.get("receipt") is None)
            )
            if not valid:
                break
    return valid, f"output_files={files}; json_schema_valid={valid}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()

    run_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_value) if run_value else None
    events = trace_events(run_dir) if run_dir is not None else []
    ledger = ledger_rows(run_dir) if run_dir is not None else []
    scenario = (
        load_json(
            run_dir
            / "artifacts"
            / "private_evidence"
            / "business_simulator"
            / "scenario.json",
            {"items": {}},
        )
        if run_dir is not None
        else {"items": {}}
    )
    records = native_exec_records(events)
    pairs, probes = probe_evidence(records, scenario)
    submitted = [row for row in ledger if request_argv(row)[:1] == ["dep_submit"]]

    expected_items = list(scenario.get("items", {}))
    valid_pairs = [item for item in expected_items if pair_is_valid(pairs.get(item, {}))]
    all_pairs_valid = bool(expected_items) and len(valid_pairs) == len(expected_items)
    summary = trace_summary(run_dir) if run_dir is not None else {}

    brief_hits: dict[str, list[dict[str, Any]]] = {}
    submit_hits: dict[str, list[dict[str, Any]]] = {}
    submitted_by_item: dict[str, list[list[str]]] = {item: [] for item in expected_items}
    for row in ledger:
        argv = request_argv(row)
        if len(argv) == 3 and argv[0] == "dep_submit" and argv[1] in submitted_by_item:
            submitted_by_item[argv[1]].append(argv)

    item_results: dict[str, dict[str, Any]] = {}
    sequence_points: list[tuple[int, int, int]] = []
    for item in expected_items:
        truth = scenario["items"][item]
        brief_hits[item] = [
            record for record in records if f"dep_brief {item}" in record["command_text"]
        ]
        submit_hits[item] = [
            record for record in records if f"dep_submit {item}" in record["command_text"]
        ]
        pair = pairs.get(item, {})
        setups = pair.get("setup_calls", [])
        readbacks = pair.get("readback_calls", [])
        setup_call = setups[0] if len(setups) == 1 else None
        readback_call = readbacks[0] if len(readbacks) == 1 else None
        setup_found, setup_value = exact_marker(
            setup_call.get("result_text", "") if setup_call else "",
            f"HBSET_{item}=",
        )
        readback_found, observed = exact_marker(
            readback_call.get("result_text", "") if readback_call else "",
            f"HBSTATE_{item}=",
        )
        setup_confirmed = bool(setup_found and setup_value == truth.get("expect"))
        persists = (
            observed == truth.get("expect")
            if setup_confirmed and readback_found
            else None
        )
        valid_submissions = [
            argv
            for argv in submitted_by_item[item]
            if readback_found and len(argv) == 3 and argv[2] == observed
        ]
        receipt_pattern = re.compile(
            rf"RECEIPT item={re.escape(item)} code=([0-9A-F]{{8}})"
        )
        visible_receipts = [
            match.group(1)
            for record in submit_hits[item]
            for match in receipt_pattern.finditer(record.get("result_text", ""))
        ]
        receipt = (
            truth.get("code")
            if setup_confirmed
            and readback_found
            and len(valid_submissions) == 1
            and visible_receipts == [truth.get("code")]
            else None
        )
        if len(brief_hits[item]) == 1 and setup_call and readback_call:
            sequence_points.append(
                (
                    int(brief_hits[item][0]["event_index"]),
                    int(setup_call["event_index"]),
                    int(readback_call["event_index"]),
                )
            )
        item_results[item] = {
            "setup_confirmed": setup_confirmed,
            "setup_marker_found": setup_found,
            "setup_value": setup_value,
            "readback_marker_found": readback_found,
            "observed": observed if readback_found else None,
            "persists": persists,
            "receipt": receipt,
            "brief_call_count": len(brief_hits[item]),
            "setup_call_count": len(setups),
            "readback_call_count": len(readbacks),
            "submit_request_count": len(submitted_by_item[item]),
            "visible_receipts": visible_receipts,
        }

    ordered_sequence = bool(expected_items) and len(sequence_points) == len(expected_items)
    if ordered_sequence:
        for index, (brief_index, setup_index, readback_index) in enumerate(sequence_points):
            ordered_sequence = brief_index < setup_index < readback_index
            if index:
                ordered_sequence = ordered_sequence and sequence_points[index - 1][2] < brief_index
            if not ordered_sequence:
                break
    mechanism_exercised = all_pairs_valid and ordered_sequence
    recovered_items = [
        item
        for item in expected_items
        if item_results[item]["setup_confirmed"]
        and item_results[item]["persists"] is True
    ]

    tool_events = [event for event in events if event.get("event_type") == "tool"]
    tool_ids = [
        str(event.get("tool_call", {}).get("tool_call_id") or "")
        for event in tool_events
        if isinstance(event.get("tool_call"), dict)
    ]
    tool_stats = summary.get("tool_stats", {}) if isinstance(summary, dict) else {}
    trace_complete = bool(
        summary.get("trace_complete") is True
        and summary.get("status") == "finished"
        and tool_events
        and all(event.get("tool_result") is not None for event in tool_events)
        and all(tool_ids)
        and len(tool_ids) == len(set(tool_ids))
        and tool_stats.get("total_tool_calls") == tool_stats.get("total_tool_results")
    )

    shell_state = load_json(args.workspace / "output" / "shell_state.json", {})
    schema_valid, schema_reason = output_schema_ok(args.workspace, expected_items, scenario)
    values_valid = schema_valid
    mismatched_items: list[str] = []
    if values_valid:
        for item in expected_items:
            actual = shell_state[item]
            expected = item_results[item]
            valid = bool(
                actual.get("setup_confirmed") == expected["setup_confirmed"]
                and actual.get("observed") == expected["observed"]
                and actual.get("persists") == expected["persists"]
                and actual.get("receipt") == expected["receipt"]
                and (
                    expected["submit_request_count"] == 1
                    if expected["setup_confirmed"] and expected["readback_marker_found"]
                    else expected["submit_request_count"] == 0
                )
            )
            if not valid:
                values_valid = False
                mismatched_items.append(item)

    findings = read_text(args.workspace / "output" / "deploy_findings.md")
    lines = findings.splitlines()
    headings = [line for line in lines if re.match(r"^#{1,6}\s+", line)]
    expected_headings = [
        "## Persists across exec calls",
        "## Does not persist across exec calls",
        "## Unresolved",
    ]
    report_valid = headings == expected_headings
    section_lines: dict[str, list[str]] = {}
    if report_valid:
        indexes = [lines.index(heading) for heading in expected_headings]
        for index, heading in enumerate(expected_headings):
            end = indexes[index + 1] if index + 1 < len(indexes) else len(lines)
            section_lines[heading] = lines[indexes[index] + 1 : end]
    expected_bullets: dict[str, list[str]] = {heading: [] for heading in expected_headings}
    for item in expected_items:
        result = item_results[item]
        heading = (
            expected_headings[0]
            if result["persists"] is True
            else expected_headings[1]
            if result["persists"] is False
            else expected_headings[2]
        )
        observed_display = (
            "<UNAVAILABLE>"
            if result["observed"] is None
            else "<EMPTY>"
            if result["observed"] == ""
            else result["observed"]
        )
        expected_bullets[heading].append(
            f"- {item} | {scenario['items'][item]['label']} | "
            f"setup={'confirmed' if result['setup_confirmed'] else 'unconfirmed'} | "
            f"observed={observed_display} | receipt={result['receipt'] or 'unavailable'}"
        )
    if report_valid:
        for heading in expected_headings:
            body = section_lines[heading]
            actual_bullets = [line for line in body if line.startswith("- M")]
            if actual_bullets != expected_bullets[heading]:
                report_valid = False
                break
            none_count = sum(line == "None" for line in body)
            if (not expected_bullets[heading] and none_count != 1) or (
                expected_bullets[heading] and none_count != 0
            ):
                report_valid = False
                break
        unresolved_body = section_lines.get(expected_headings[-1], [])
        substantive_tail = [
            line
            for line in unresolved_body
            if line and not line.startswith("- M") and line != "None"
        ]
        report_valid = report_valid and bool(substantive_tail)

    reset_pattern = re.compile(
        r"\b(reset to|fresh shell|new shell|new process|state reset|cwd was reset|working directory was reset)\b",
        re.IGNORECASE,
    )
    reset_announcements = [
        {
            "tool_call_id": record.get("tool_call_id"),
            "command": record.get("command_text"),
            "result": record.get("result_text"),
        }
        for record in records
        if reset_pattern.search(record.get("result_text", ""))
    ]
    failed_readbacks: list[dict[str, Any]] = []
    for item in expected_items:
        result = item_results[item]
        if not result["setup_confirmed"] or result["persists"] is True:
            continue
        readbacks = pairs[item]["readback_calls"]
        call = readbacks[0] if len(readbacks) == 1 else {}
        output = call.get("result_text", "")
        extra = "\n".join(
            line for line in output.splitlines() if not line.startswith(f"HBSTATE_{item}=")
        )
        explicit = bool(
            reset_pattern.search(extra)
            or re.search(r"\b(undefined|not found|failed|error|non-zero|exit code [1-9])\b", extra, re.I)
        )
        failed_readbacks.append({"item": item, "explicit_signal": explicit, "result": output})

    compact_sources = {
        item: {
            "derived": item_results[item],
            "setup_result": (
                pairs[item]["setup_calls"][0].get("result_text")
                if len(pairs[item]["setup_calls"]) == 1
                else None
            ),
            "readback_result": (
                pairs[item]["readback_calls"][0].get("result_text")
                if len(pairs[item]["readback_calls"]) == 1
                else None
            ),
            "submission_results": [record.get("result_text") for record in submit_hits[item]],
        }
        for item in expected_items
    }
    paragraphs = [paragraph.strip() for paragraph in findings.split("\n\n") if paragraph.strip()]
    final_paragraph = paragraphs[-1] if paragraphs else ""
    evidence_by_id = {
        "ar_no_fabrication_and_gaps_are_declared": {
            "evidence_note": "Deliverable fields below are verbatim archived file contents; source fields are verbatim normalized model-visible direct tool results from this run.",
            "shell_state_deliverable": shell_state,
            "deploy_findings_deliverable": findings,
            "model_visible_sources_by_item": compact_sources,
        },
        "ar_operational_conclusion_is_sound": {
            "final_unheaded_paragraph_verbatim": final_paragraph,
            "deploy_findings_deliverable": findings,
            "classifications_derived_from_trace": {
                item: {
                    "setup_confirmed": item_results[item]["setup_confirmed"],
                    "observed": item_results[item]["observed"],
                    "persists": item_results[item]["persists"],
                }
                for item in expected_items
            },
        },
    }
    evidence_root = args.workspace / ".harnessbench_verifier"
    evidence_root.mkdir(parents=True, exist_ok=True)
    for rubric_id in ARTIFACT_RUBRIC_IDS:
        evidence = {
            "schema_version": "harnessbench_verifier_shell_persistence_v2",
            "rubric_id": rubric_id,
            **evidence_by_id[rubric_id],
        }
        (evidence_root / f"{rubric_id}.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    rules = [
        {
            "id": "hr_mechanism_fully_exercised",
            "score": int(mechanism_exercised),
            "reason": (
                f"exact distinct setup/readback pairs={len(valid_pairs)}/{len(expected_items)}; "
                f"brief-setup-readback order valid={ordered_sequence}"
            ),
        },
        {
            "id": "hr_any_shell_state_recovered_across_calls",
            "score": int(bool(recovered_items)),
            "reason": (
                f"setup-confirmed values recovered={len(recovered_items)}/{len(expected_items)}; "
                f"recovered_items={','.join(recovered_items)}"
            ),
        },
        {
            "id": "hr_normalized_trace_is_complete",
            "score": int(trace_complete),
            "reason": (
                f"trace_complete={summary.get('trace_complete')}; status={summary.get('status')}; "
                f"tool_events={len(tool_events)}; unique_tool_ids={len(set(tool_ids))}/{len(tool_ids)}; "
                f"calls={tool_stats.get('total_tool_calls')}; results={tool_stats.get('total_tool_results')}"
            ),
        },
    ]
    agent_rules = [
        {
            "id": "ar_output_files_and_schema_are_exact",
            "score": int(schema_valid),
            "reason": schema_reason,
        },
        {
            "id": "ar_json_values_match_direct_evidence",
            "score": int(values_valid),
            "reason": (
                f"values_valid={values_valid}; mismatched_items={','.join(mismatched_items)}"
            ),
        },
        {
            "id": "ar_report_partition_and_rows_are_exact",
            "score": int(report_valid),
            "reason": (
                f"headings={headings}; report_rows_and_empty_sections_valid={report_valid}"
            ),
        },
    ]
    print(
        json.dumps(
            {
                "schema_version": "harnessbench_rule_results_v1",
                "rule_results": {"harness": rules, "agent": agent_rules},
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
