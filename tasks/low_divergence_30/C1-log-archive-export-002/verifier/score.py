#!/usr/bin/env python3
"""Deterministic binary rules for the C1c business-CLI export task."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any


TRUNCATION_NOTICE = re.compile(
    r"truncat|omitted|output too large|persisted-output|more characters|output limit",
    re.IGNORECASE,
)


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

    private_root = Path(__file__).resolve().parent.parent
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
    export_events = [
        event
        for event in tool_events
        if re.search(r"(?:^|[\s/])logctl\s+export\b", tool_call_text(event))
    ]
    pressure_captured = (
        len(successful_export_rows) == 1
        and isinstance(export_request, dict)
        and isinstance(export_response, dict)
        and isinstance(first_stdout, str)
        and len(first_stdout.encode("utf-8")) > 3_000_000
        and bool(export_events)
        and isinstance(state, dict)
        and state.get("complete_export_calls") == 1
    )

    delivery_index = len(tool_events)
    for index, event in enumerate(tool_events):
        call_text = tool_call_text(event).lower()
        if "output/retentions.json" in call_text and any(
            marker in call_text for marker in ("write", "cat >", "tee", "json.dump")
        ):
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
            "TOTAL_FLAGGED=12",
            "TOTAL_SEGMENTS=9012",
        )
    )
    # Some Harnesses preserve the raw command result in the normalized tool event
    # while recording the model-facing reduction notice in an adjacent assistant or
    # transport event. Search the complete pre-delivery trajectory so that the rule
    # measures what reached the model rather than one adapter's event layout.
    truncation_visible = bool(TRUNCATION_NOTICE.search(pre_delivery_model_text))
    all_issued_visible = bool(issued) and all(
        isinstance(code, str) and code in visible_before_delivery
        for code in issued.values()
    )
    authoritative_content_visible = (
        headers_visible and truncation_visible and all_issued_visible
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
        output_dir / "retentions.json"
    )
    notes_path = output_dir / "archive_notes.md"
    try:
        notes_path.read_text(encoding="utf-8", errors="strict")
        notes_status = "valid UTF-8"
    except FileNotFoundError:
        notes_status = "missing"
    except UnicodeError:
        notes_status = "not valid UTF-8"
    output_contract = (
        output_shape_safe
        and output_files == ["archive_notes.md", "retentions.json"]
        and mapping is not None
        and notes_status == "valid UTF-8"
    )
    exact_mapping = (
        mapping is not None
        and bool(issued)
        and set(mapping) == set(issued)
        and all(mapping.get(ticket_id) == code for ticket_id, code in issued.items())
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
                f"Successful complete exports in ledger={len(successful_export_rows)}; "
                f"complete stdout bytes={len(first_stdout.encode('utf-8')) if isinstance(first_stdout, str) else 0}; "
                f"trace export calls={len(export_events)}; successful complete exports="
                f"{state.get('complete_export_calls') if isinstance(state, dict) else None}."
            ),
        ),
        rule(
            "hr_authoritative_content_reaches_model",
            authoritative_content_visible,
            (
                f"Before artifact delivery: headers_visible={headers_visible}, "
                f"reduction_visible={truncation_visible}, "
                f"issued_codes_visible={sum(str(code) in visible_before_delivery for code in issued.values())}/{len(issued)}."
            ),
        ),
    ]
    agent_results = [
        rule(
            "ar_output_contract_exact",
            output_contract,
            (
                f"output entries={output_files}; retentions.json={mapping_status}; "
                f"archive_notes.md={notes_status}; directory_safe={output_shape_safe}."
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
