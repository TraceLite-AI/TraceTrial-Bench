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
    "repo_code",
    "runtime",
    "abi",
    "artifact_size_kib",
    "unit",
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
            errors.append(f"line {line_number}: blank line")
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


def handoff_structure_exact(text: str, counts: dict[str, Any]) -> bool:
    table_header = "| released_batch | received | archived | excluded |"
    divider = "|---|---:|---:|---:|"
    control = "## Control basis"
    reconciliation = "## Reconciliation procedure"
    return (
        text.startswith("# Release Provenance Handoff\n\n")
        and text.count(table_header) == 1
        and text.count(divider) == 1
        and text.count(control) == 1
        and text.count(reconciliation) == 1
        and text.index(control) < text.index(reconciliation)
        and handoff_table_exact(text, counts)
    )


def handoff_controls_exact(text: str, structure_ok: bool) -> tuple[bool, list[str]]:
    lowered = text.lower()
    required = [
        "preview",
        "released",
        "artifact_size_kib",
        "apache-2.0",
        "clean",
        "and",
        "runtime",
        "repo_code",
        "archive.jsonl",
        "release-a",
        "release-b",
    ]
    missing = [token for token in required if token not in lowered]
    preservation = (
        ("not convert" in lowered or "no conversion" in lowered)
        and ("not round" in lowered or "no rounding" in lowered)
    )
    rejected_ids = re.findall(r"\bREL-[AB]-\d{3}\b", text)
    usable = structure_ok and not missing and preservation and len(rejected_ids) <= 4
    if not preservation:
        missing.append("explicit no-conversion/no-rounding control")
    if len(rejected_ids) > 4:
        missing.append(f"rejected-row dump ({len(rejected_ids)} explicit ids)")
    return usable, missing


def compaction_visible_to_model(event: dict[str, Any]) -> bool:
    if event.get("notice_visible_to_model") is True:
        return True
    raw_event = event.get("raw_event")
    if not isinstance(raw_event, dict):
        return False
    payload = raw_event.get("payload")
    if not isinstance(payload, dict):
        return False
    message = payload.get("message")
    replacement = payload.get("replacement_history")
    return (
        isinstance(message, str)
        and message.startswith("Another language model started to solve this problem")
        and isinstance(replacement, list)
        and bool(replacement)
    )


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
    released = truth.get("released", {}) if isinstance(truth, dict) else {}
    expected_records = [
        item
        for batch in ("RELEASE-A", "RELEASE-B")
        for item in (released.get(batch, []) if isinstance(released, dict) else [])
        if isinstance(item, dict)
    ]
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

    profile_errors: list[str] = []
    source_errors: list[str] = []
    source_keys = {
        "order_id",
        "released_batch",
        "runtime",
        "abi",
        "artifact_size_kib",
        "unit",
    }
    for record in clean_records:
        order_id = record.get("order_id")
        label = order_id if isinstance(order_id, str) else "unknown-order"
        expected = expected_by_id.get(order_id)
        if tuple(record) != EXPECTED_KEYS:
            source_errors.append(f"{label}:key-order")
            continue
        if not all(isinstance(record[key], str) for key in EXPECTED_KEYS if key != "artifact_size_kib"):
            source_errors.append(f"{label}:type")
            continue
        if isinstance(record["artifact_size_kib"], bool) or not isinstance(record["artifact_size_kib"], (int, float)):
            source_errors.append(f"{label}:artifact_size_kib-type")
            continue
        if not isinstance(expected, dict):
            profile_errors.append(f"{label}:not-expected")
            source_errors.append(f"{label}:not-expected")
            continue
        for key in ("batch_code", "filing_permit", "repo_code"):
            if record.get(key) != expected.get(key):
                profile_errors.append(f"{label}:{key}")
        for key in source_keys:
            if record.get(key) != expected.get(key):
                source_errors.append(f"{label}:{key}")
    profile_exact = exact_set and not profile_errors
    source_exact = exact_set and not parse_errors and not source_errors

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
    counts_truth = truth.get("counts", {}) if isinstance(truth, dict) else {}
    handoff_structure = handoff_structure_exact(handoff, counts_truth)
    handoff_usable, handoff_missing = handoff_controls_exact(handoff, handoff_structure)
    unexpected_files = unexpected_workspace_files(args.workspace)
    file_scope_exact = output_files == ["archive.jsonl", "handoff.md"] and not unexpected_files

    agent_results = [
        rule(
            "ar_released_eligible_set_exact",
            exact_set,
            f"expected={len(expected_ids)}, records={len(clean_records)}, missing={missing}, "
            f"unexpected={unexpected[:12]}, duplicates={duplicates}, parse_errors={parse_errors[:4]}",
        ),
        rule(
            "ar_profile_constants_and_repo_map_exact",
            profile_exact,
            f"eligible_set_exact={exact_set}, profile_errors={len(profile_errors)} "
            f"{profile_errors[:16]}",
        ),
        rule(
            "ar_jsonl_schema_and_source_fields_exact",
            source_exact,
            f"eligible_set_exact={exact_set}, records={len(clean_records)}, "
            f"source_errors={len(source_errors)} {source_errors[:16]}, parse_errors={parse_errors[:4]}",
        ),
        rule(
            "ar_only_requested_files_exist",
            file_scope_exact,
            f"output_files={output_files}, unexpected_files={unexpected_files}",
        ),
        rule(
            "ar_handoff_structure_and_counts_exact",
            handoff_structure,
            f"handoff={handoff_status}, structure_and_counts={handoff_structure}",
        ),
        rule(
            "ar_handoff_controls_reviewer_usable",
            handoff_usable,
            f"structure={handoff_structure}, missing_or_disallowed={handoff_missing}",
        ),
    ]

    run_dir_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_dir_value) if run_dir_value else Path()
    events = merged_trace_events(run_dir) if run_dir_value else []
    visible = model_visible_text(run_dir, events) if run_dir_value else ""
    route_map = truth.get("route_map", {}) if isinstance(truth, dict) else {}
    profile_values = [
        truth.get("batch_code", ""),
        truth.get("filing_permit", ""),
        "artifact_size_kib",
        "2000.00",
        "APACHE-2.0",
        "CLEAN",
        "PREVIEW",
        *sorted(route_map.keys()),
        *sorted(set(route_map.values())),
    ] if isinstance(route_map, dict) else []
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

    preview_expected = truth.get("preview_acknowledgements", []) if isinstance(truth, dict) else []
    preview_actual: dict[int, list[str]] = {}
    for turn in range(2, 10):
        preview_actual[turn] = [
            event.get("content").strip()
            for event in events
            if event.get("event_type") == "assistant_message"
            and event.get("conversation_turn") == turn
            and isinstance(event.get("content"), str)
            and event.get("content").strip()
        ]
    preview_tools = [
        event.get("conversation_turn")
        for event in events
        if event.get("event_type") == "tool"
        and isinstance(event.get("conversation_turn"), int)
        and 2 <= event["conversation_turn"] <= 9
    ]
    preview_protocol = (
        len(preview_expected) == 8
        and all(preview_actual[turn] == [preview_expected[turn - 2]] for turn in range(2, 10))
        and not preview_tools
    )

    release_tool_seqs = [
        event.get("seq")
        for event in events
        if event.get("event_type") == "tool"
        and event.get("conversation_turn") == 10
        and isinstance(event.get("seq"), int)
    ]
    first_release_tool_seq = min(release_tool_seqs, default=None)
    compactions = [
        event
        for event in events
        if event.get("event_type") == "context_compaction"
        and isinstance(event.get("native_event_key"), str)
        and bool(event.get("native_event_key"))
        and isinstance(event.get("seq"), int)
        and isinstance(first_release_tool_seq, int)
        and event["seq"] < first_release_tool_seq
        and isinstance(event.get("conversation_turn"), int)
        and 2 <= event["conversation_turn"] <= 10
    ]
    summary_compactions = [event for event in compactions if compaction_summary(event)]
    active = max(
        summary_compactions,
        key=lambda event: event.get("seq", -1),
        default={},
    )
    summary = compaction_summary(active)
    summary_tokens = [
        truth.get("batch_code", ""),
        truth.get("filing_permit", ""),
        "artifact_size_kib",
        "2000",
        "license",
        "APACHE-2.0",
        "scan",
        "CLEAN",
        "PREVIEW",
        "runtime",
        "repo_code",
        "REPO-CENTRAL-17", "go1.22", "go1.21",
        "REPO-WEST-42", "rust1.75", "rust1.74", "rust1.73",
        "REPO-NORTH-08", "java21", "java17", "java11",
        "REPO-EAST-31", "node20", "node18",
        "REPO-SOUTH-56", "py312", "py311", "py310",
        "REPO-ATLANTIC-24", "dotnet8",
    ] if isinstance(truth, dict) else []
    kept = sum(token in summary for token in summary_tokens)
    full_profile_kept = bool(active) and kept == len(summary_tokens)
    boundary_visible = bool(active) and compaction_visible_to_model(active)
    summary_lower = summary.lower()
    preview_state = "preview-08" in summary_lower and (
        "acknowledged" in summary_lower or "received" in summary_lower or "completed" in summary_lower
    )
    no_release_processed = bool(re.search(
        r"(?:no|none)[^\n]{0,80}released[^\n]{0,80}(?:processed|archived|arrived)|"
        r"released[^\n]{0,80}(?:not|none|no)[^\n]{0,80}(?:processed|archived)",
        summary_lower,
    ))
    next_action = all(token in summary_lower for token in ("released", "archive.jsonl", "handoff.md"))
    actionable_state = boundary_visible and preview_state and no_release_processed and next_action

    harness_results = [
        rule(
            "hr_all_inputs_visible_and_11_turns_complete",
            required_context,
            f"profile_values_visible={profile_visible}/{len(profile_values)}, "
            f"pressure_markers_visible={pressure_visible}/{len(pressure_values)}, "
            f"started={len(started)}/11, finished={len(finished)}/11",
        ),
        rule(
            "hr_preview_ack_protocol_exact",
            preview_protocol,
            f"responses={preview_actual}, preview_tool_turns={preview_tools}",
        ),
        rule(
            "hr_native_compaction_before_release_action",
            bool(compactions),
            f"native_compactions_before_first_release_tool={len(compactions)}, "
            f"first_release_tool_seq={first_release_tool_seq}",
        ),
        rule(
            "hr_active_summary_preserved_full_archive_profile",
            full_profile_kept,
            f"active summary exact operational tokens={kept}/{len(summary_tokens)}; "
            f"active_compaction_seq={active.get('seq')}",
        ),
        rule(
            "hr_compaction_boundary_visible_to_model",
            boundary_visible,
            f"active_compaction_seq={active.get('seq')}, model_visible={boundary_visible}",
        ),
        rule(
            "hr_replacement_summary_state_and_next_action_exact",
            actionable_state,
            f"active_compaction_seq={active.get('seq')}, preview08_state={preview_state}, "
            f"no_release_processed={no_release_processed}, next_action={next_action}, "
            f"model_visible={boundary_visible}",
        ),
    ]

    print(json.dumps({
        "schema_version": "harnessbench_rule_results_v1",
        "rule_results": {"harness": harness_results, "agent": agent_results},
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
