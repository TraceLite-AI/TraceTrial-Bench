#!/usr/bin/env python3
"""Deterministic binary rules for the L7 compaction-fidelity task."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any


EXPECTED_KEYS = (
    "order_id",
    "released_batch",
    "batch_code",
    "filing_permit",
    "route_code",
    "dest",
    "hs_code",
    "declared_value",
    "currency",
)
EXPECTED_TURNS = set(range(1, 12))
HERE = Path(__file__).resolve().parent
HARNESS_FILES = {
    ".gitkeep",
    "AGENTS.md",
    "BOOTSTRAP.md",
    "HEARTBEAT.md",
    "IDENTITY.md",
    "SOUL.md",
    "TOOLS.md",
    "USER.md",
    "openclaw-workspace-state.json",
}


class DuplicateKey(ValueError):
    pass


def unique_object(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise DuplicateKey(key)
        result[key] = value
    return result


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def load_archive(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except FileNotFoundError:
        return records, ["archive is missing"]
    except UnicodeError:
        return records, ["archive is not valid UTF-8"]
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line, object_pairs_hook=unique_object)
        except DuplicateKey as exc:
            errors.append(f"line {line_number}: duplicate key {exc}")
            continue
        except json.JSONDecodeError as exc:
            errors.append(f"line {line_number}: invalid JSON at column {exc.colno}")
            continue
        if not isinstance(value, dict):
            errors.append(f"line {line_number}: not a JSON object")
            continue
        value["__line__"] = line_number
        records.append(value)
    if not records and not errors:
        errors.append("archive contains no records")
    return records, errors


def merged_trace_events(run_dir: Path) -> list[dict[str, Any]]:
    """Select one merged run trace; never double-count per-turn trace copies."""
    candidates: list[list[dict[str, Any]]] = []
    for trace_root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if not trace_root.is_dir():
            continue
        for path in trace_root.glob("run-*/events.json"):
            payload = load_json(path, [])
            if isinstance(payload, list):
                candidates.append([item for item in payload if isinstance(item, dict)])
    return max(candidates, key=len, default=[])


def event_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def compaction_summary(event: dict[str, Any]) -> str:
    summary = event.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary
    raw_event = event.get("raw_event")
    if isinstance(raw_event, dict):
        payload = raw_event.get("payload")
        if isinstance(payload, dict):
            message = payload.get("message")
            if isinstance(message, str) and message.strip():
                return message
    return ""


def model_visible_text(run_dir: Path, events: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for event in events:
        if event.get("event_type") == "model_input":
            chunks.append(event_text(event))
        elif event.get("event_type") == "tool" and "tool_result" in event:
            chunks.append(event_text(event["tool_result"]))
        elif event.get("event_type") in {"raw_stdout", "raw_stderr"}:
            chunks.append(str(event.get("text", "")))
    seen: set[Path] = set()
    for trace_root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if not trace_root.is_dir():
            continue
        for path in trace_root.glob("run-*/turns/**/*session*.jsonl"):
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(trace_root.resolve(strict=True))
            except (OSError, ValueError):
                continue
            if resolved in seen or path.is_symlink() or path.stat().st_size > 64 * 1024 * 1024:
                continue
            seen.add(resolved)
            try:
                chunks.append(path.read_text(encoding="utf-8", errors="strict"))
            except (OSError, UnicodeError):
                continue
    return "\n".join(chunks)


def handoff_table_exact(text: str, counts: dict[str, Any]) -> bool:
    rows: dict[str, tuple[int, int, int]] = {}
    pattern = re.compile(
        r"^\|\s*(RELEASE-A|RELEASE-B|TOTAL)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*$"
    )
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            rows[match.group(1)] = tuple(int(match.group(i)) for i in range(2, 5))
    expected = {
        name: (item["received"], item["archived"], item["excluded"])
        for name, item in counts.items()
    }
    return rows == expected


def unexpected_workspace_files(workspace: Path) -> list[str]:
    unexpected: list[str] = []
    for path in workspace.rglob("*") if workspace.is_dir() else []:
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(workspace).as_posix()
        if relative.startswith(".git/") or relative in HARNESS_FILES:
            continue
        if relative in {"output/archive.jsonl", "output/handoff.md"}:
            continue
        unexpected.append(relative)
    return sorted(unexpected)


def rule(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(passed), "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True, type=Path)
    args = parser.parse_args()

    truth = load_json(HERE / "truth.json", {})
    expected_records = truth.get("expected_records", []) if isinstance(truth, dict) else []
    expected_by_id = {
        item["order_id"]: item
        for item in expected_records
        if isinstance(item, dict) and isinstance(item.get("order_id"), str)
    }
    expected_ids = set(expected_by_id)
    records, parse_errors = load_archive(args.workspace / "output" / "archive.jsonl")
    clean_records = [
        {key: value for key, value in record.items() if key != "__line__"}
        for record in records
    ]
    ids = [record.get("order_id") for record in clean_records]
    string_ids = [value for value in ids if isinstance(value, str)]
    counts = Counter(string_ids)
    duplicates = sorted(key for key, value in counts.items() if value > 1)
    actual_ids = set(string_ids)
    missing = sorted(expected_ids - actual_ids)
    unexpected = sorted(actual_ids - expected_ids)
    exact_set = (
        bool(expected_ids)
        and not parse_errors
        and len(string_ids) == len(clean_records)
        and not duplicates
        and not missing
        and not unexpected
        and len(clean_records) == len(expected_ids)
    )

    field_errors: list[str] = []
    for record in clean_records:
        order_id = record.get("order_id")
        label = order_id if isinstance(order_id, str) else "unknown-order"
        expected = expected_by_id.get(order_id)
        if tuple(record) != EXPECTED_KEYS:
            field_errors.append(f"{label}:key-order")
            continue
        if not all(isinstance(record[key], str) for key in EXPECTED_KEYS if key != "declared_value"):
            field_errors.append(f"{label}:type")
            continue
        if isinstance(record["declared_value"], bool) or not isinstance(record["declared_value"], (int, float)):
            field_errors.append(f"{label}:declared_value-type")
            continue
        if not isinstance(expected, dict):
            field_errors.append(f"{label}:not-expected")
            continue
        for key in EXPECTED_KEYS:
            if record.get(key) != expected.get(key):
                field_errors.append(f"{label}:{key}")
    fields_exact_for_produced = bool(clean_records) and not parse_errors and not field_errors

    output_dir = args.workspace / "output"
    output_files = (
        sorted(
            path.relative_to(output_dir).as_posix()
            for path in output_dir.rglob("*")
            if path.is_file() and not path.is_symlink()
        )
        if output_dir.is_dir()
        else []
    )
    try:
        handoff = (output_dir / "handoff.md").read_text(encoding="utf-8", errors="strict")
        handoff_status = "valid UTF-8"
    except FileNotFoundError:
        handoff = ""
        handoff_status = "missing"
    except UnicodeError:
        handoff = ""
        handoff_status = "not valid UTF-8"
    header_ok = handoff.startswith("# Polar Research Transfer Archive Handoff\n")
    sections_ok = "## Control basis" in handoff and "## Reconciliation procedure" in handoff
    table_ok = handoff_table_exact(handoff, truth.get("counts", {})) if isinstance(truth, dict) else False
    unexpected_files = unexpected_workspace_files(args.workspace)
    output_contract = (
        output_files == ["archive.jsonl", "handoff.md"]
        and header_ok
        and sections_ok
        and table_ok
        and not unexpected_files
    )

    agent_results = [
        rule(
            "ar_released_eligible_set_exact",
            exact_set,
            f"expected={len(expected_ids)}, records={len(clean_records)}, missing={missing}, "
            f"unexpected={unexpected[:12]}, duplicates={duplicates}, parse_errors={parse_errors[:4]}",
        ),
        rule(
            "ar_profile_enrichment_and_source_fields_exact",
            fields_exact_for_produced,
            f"records={len(clean_records)}, field_errors={len(field_errors)} {field_errors[:16]}, "
            f"parse_errors={parse_errors[:4]}",
        ),
        rule(
            "ar_two_file_contract_and_reconciliation_counts",
            output_contract,
            f"output_files={output_files}, handoff={handoff_status}, header={header_ok}, "
            f"sections={sections_ok}, table={table_ok}, unexpected_files={unexpected_files}",
        ),
    ]

    run_dir_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_dir_value) if run_dir_value else Path()
    events = merged_trace_events(run_dir) if run_dir_value else []
    visible = model_visible_text(run_dir, events) if run_dir_value else ""
    profile_values = [
        truth.get("batch_code", ""),
        truth.get("filing_permit", ""),
        "declared_value >= 2000.00",
        'incoterm == \\"DDP\\"',
        'screening == \\"CLEAR\\"',
        *sorted(set(truth.get("route_map", {}).values())),
    ] if isinstance(truth, dict) else []
    pressure_values = [
        *truth.get("preview_markers", []),
        truth.get("release_marker", ""),
    ] if isinstance(truth, dict) else []
    profile_visible = sum(value in visible for value in profile_values)
    pressure_visible = sum(value in visible for value in pressure_values)
    started = {
        event.get("turn_index")
        for event in events
        if event.get("event_type") == "conversation_turn_started"
        and isinstance(event.get("turn_index"), int)
    }
    finished = {
        event.get("turn_index")
        for event in events
        if event.get("event_type") == "conversation_turn_finished"
        and event.get("status") == "finished"
        and isinstance(event.get("turn_index"), int)
    }
    required_context = (
        bool(events)
        and profile_visible == len(profile_values)
        and pressure_visible == len(pressure_values)
        and started == EXPECTED_TURNS
        and finished == EXPECTED_TURNS
    )

    compactions = [
        event
        for event in events
        if event.get("event_type") == "context_compaction"
        and isinstance(event.get("native_event_key"), str)
        and bool(event.get("native_event_key"))
        and isinstance(event.get("conversation_turn"), int)
        and 2 <= event["conversation_turn"] <= 10
    ]
    active_turn = max(
        (event["conversation_turn"] for event in compactions),
        default=None,
    )
    active_candidates = [
        event for event in compactions if event.get("conversation_turn") == active_turn
    ]
    # Some adapters emit a summary-bearing native event followed by a marker-only
    # event for the same boundary. Prefer the summary-bearing member of the last
    # boundary instead of treating the marker as a later empty compaction.
    active = max(
        active_candidates,
        key=lambda event: (
            bool(compaction_summary(event)),
            event.get("seq") if isinstance(event.get("seq"), int) else -1,
        ),
        default={},
    )
    summary = compaction_summary(active)
    summary_tokens = [
        truth.get("batch_code", ""),
        truth.get("filing_permit", ""),
        "2000",
        "DDP",
        "CLEAR",
        "PREVIEW",
        "RTE-CENTRAL-17", "DE", "AT",
        "RTE-WEST-42", "FR", "BE", "NL",
        "RTE-NORTH-08", "DK", "SE", "FI",
        "RTE-EAST-31", "PL", "CZ",
        "RTE-SOUTH-56", "IT", "ES", "PT",
        "RTE-ATLANTIC-24", "IE",
    ] if isinstance(truth, dict) else []
    kept = sum(token in summary for token in summary_tokens)
    full_profile_kept = bool(compactions) and kept == len(summary_tokens)

    harness_results = [
        rule(
            "hr_all_inputs_visible_and_11_turns_complete",
            required_context,
            f"profile_values_visible={profile_visible}/{len(profile_values)}, "
            f"pressure_markers_visible={pressure_visible}/{len(pressure_values)}, "
            f"started={len(started)}/11, finished={len(finished)}/11",
        ),
        rule(
            "hr_native_compaction_before_release_action",
            bool(compactions),
            f"exact native compactions during preview pressure or release turn={len(compactions)}",
        ),
        rule(
            "hr_active_summary_preserved_full_archive_profile",
            full_profile_kept,
            f"active summary exact operational tokens={kept}/{len(summary_tokens)}; "
            f"active_compaction_seq={active.get('seq')}",
        ),
    ]

    print(json.dumps({
        "schema_version": "harnessbench_rule_results_v1",
        "rule_results": {"harness": harness_results, "agent": agent_results},
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
