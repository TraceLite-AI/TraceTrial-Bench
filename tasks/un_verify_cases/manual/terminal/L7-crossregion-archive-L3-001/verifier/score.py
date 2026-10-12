#!/usr/bin/env python3
"""Strict binary verifier for the C2b cross-region compaction task."""

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
    "channel_code",
    "region",
    "class",
    "transfer_size_gib",
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
    """Select one merged root trace and never double-count per-turn copies."""
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


def structured_model_context_text(run_dir: Path, events: list[dict[str, Any]]) -> str:
    """Return model-context evidence without accepting stdout/stderr leakage."""
    chunks = [
        event_text(event)
        for event in events
        if event.get("event_type") == "model_input"
    ]
    seen: set[Path] = set()
    for trace_root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if not trace_root.is_dir():
            continue
        for path in trace_root.glob("run-*/turns/*/native_sessions/*.jsonl"):
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(trace_root.resolve(strict=True))
                size = path.stat().st_size
            except (OSError, ValueError):
                continue
            if resolved in seen or path.is_symlink() or size > 64 * 1024 * 1024:
                continue
            seen.add(resolved)
            try:
                chunks.append(path.read_text(encoding="utf-8", errors="strict"))
            except (OSError, UnicodeError):
                continue
    return "\n".join(chunks)


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


def tool_arguments(event: dict[str, Any]) -> Any:
    call = event.get("tool_call")
    return call.get("arguments") if isinstance(call, dict) else None


def action_writes_target(event: dict[str, Any], target: str) -> bool:
    """Recognize native write tools, shell writes, and Codex file-change events."""
    raw = event.get("raw_event")
    if isinstance(raw, dict):
        item = raw.get("item")
        if (
            isinstance(item, dict)
            and item.get("type") == "file_change"
            and item.get("status") == "completed"
        ):
            for change in item.get("changes", []):
                if isinstance(change, dict) and target in str(change.get("path", "")):
                    return True
    if event.get("event_type") != "tool":
        return False
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return False
    name = str(call.get("tool_name") or call.get("name") or "").lower()
    arguments = call.get("arguments")
    text = event_text(arguments)
    if target not in text:
        return False
    if any(token in name for token in ("write", "edit", "patch", "create")):
        return True
    if isinstance(arguments, dict) and "content" in arguments:
        return True
    if any(token in name for token in ("exec", "bash", "shell", "command")):
        command = str(arguments.get("command") if isinstance(arguments, dict) else arguments)
        write_tokens = (">", "tee ", "touch ", "open(", "write_text", "mkdir")
        return any(token in command for token in write_tokens)
    return False


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
        if isinstance(item, dict)
    }
    return bool(expected) and rows == expected


def handoff_structure_exact(text: str, counts: dict[str, Any]) -> bool:
    table_header = "| released_batch | received | archived | excluded |"
    divider = "|---|---:|---:|---:|"
    control = "## Control basis"
    reconciliation = "## Reconciliation procedure"
    return (
        text.startswith("# Cross-Region Archive Handoff\n\n")
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
        "transfer_size_gib",
        "2000",
        "rsync",
        "verified",
        "and",
        "region",
        "channel_code",
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
    if not preservation:
        missing.append("explicit no-conversion/no-rounding control")
    if len(rejected_ids) > 4:
        missing.append(f"rejected-row dump ({len(rejected_ids)} explicit ids)")
    return structure_ok and not missing, missing


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


def assess(workspace: Path, run_dir: Path | None) -> dict[str, Any]:
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
        if isinstance(item.get("order_id"), str)
    }
    expected_ids = set(expected_by_id)
    records, parse_errors = load_archive(workspace / "output" / "archive.jsonl")
    clean_records = [
        {key: value for key, value in record.items() if key != "__line__"}
        for record in records
    ]
    string_ids = [
        record.get("order_id")
        for record in clean_records
        if isinstance(record.get("order_id"), str)
    ]
    duplicates = sorted(key for key, count in Counter(string_ids).items() if count > 1)
    actual_ids = set(string_ids)
    missing = sorted(expected_ids - actual_ids)
    unexpected = sorted(actual_ids - expected_ids)
    exact_set = (
        len(expected_ids) == truth.get("expected_archived") == 12
        and not parse_errors
        and len(string_ids) == len(clean_records) == len(expected_ids)
        and not duplicates
        and not missing
        and not unexpected
    )

    profile_errors: list[str] = []
    source_errors: list[str] = []
    for record in clean_records:
        order_id = record.get("order_id")
        label = order_id if isinstance(order_id, str) else "unknown-order"
        expected = expected_by_id.get(order_id)
        if tuple(record) != EXPECTED_KEYS:
            source_errors.append(f"{label}:key-order")
            continue
        if not all(
            isinstance(record[key], str)
            for key in EXPECTED_KEYS
            if key != "transfer_size_gib"
        ):
            source_errors.append(f"{label}:type")
            continue
        size = record["transfer_size_gib"]
        if isinstance(size, bool) or not isinstance(size, (int, float)):
            source_errors.append(f"{label}:transfer_size_gib-type")
            continue
        if not isinstance(expected, dict):
            profile_errors.append(f"{label}:not-expected")
            source_errors.append(f"{label}:not-expected")
            continue
        for key in ("batch_code", "filing_permit", "channel_code"):
            if record.get(key) != expected.get(key):
                profile_errors.append(f"{label}:{key}")
        for key in (
            "order_id",
            "released_batch",
            "region",
            "class",
            "transfer_size_gib",
            "unit",
        ):
            if record.get(key) != expected.get(key):
                source_errors.append(f"{label}:{key}")
    profile_exact = exact_set and not profile_errors
    source_exact = exact_set and not source_errors

    output_dir = workspace / "output"
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
        handoff = (output_dir / "handoff.md").read_text(
            encoding="utf-8", errors="strict"
        )
        handoff_status = "valid UTF-8"
    except FileNotFoundError:
        handoff = ""
        handoff_status = "missing"
    except UnicodeError:
        handoff = ""
        handoff_status = "not valid UTF-8"
    counts_truth = truth.get("counts", {}) if isinstance(truth, dict) else {}
    handoff_structure = handoff_structure_exact(handoff, counts_truth)
    handoff_usable, handoff_missing = handoff_controls_exact(
        handoff, handoff_structure
    )
    unexpected_files = unexpected_workspace_files(workspace)
    file_scope_exact = (
        output_files == ["archive.jsonl", "handoff.md"] and not unexpected_files
    )

    events = merged_trace_events(run_dir) if run_dir is not None else []
    visible = (
        structured_model_context_text(run_dir, events)
        if run_dir is not None and events
        else ""
    )
    started_counts = Counter(
        event.get("turn_index")
        for event in events
        if event.get("event_type") == "conversation_turn_started"
        and isinstance(event.get("turn_index"), int)
    )
    finished_counts = Counter(
        event.get("turn_index")
        for event in events
        if event.get("event_type") == "conversation_turn_finished"
        and event.get("status") == "finished"
        and isinstance(event.get("turn_index"), int)
    )
    canonical_complete = (
        bool(events)
        and set(started_counts) == EXPECTED_TURNS
        and set(finished_counts) == EXPECTED_TURNS
        and all(started_counts[turn] == 1 for turn in EXPECTED_TURNS)
        and all(finished_counts[turn] == 1 for turn in EXPECTED_TURNS)
    )

    route_map = truth.get("route_map", {}) if isinstance(truth, dict) else {}
    profile_values = [
        truth.get("batch_code", ""),
        truth.get("filing_permit", ""),
        "transfer_size_gib",
        "2000.00",
        "RSYNC",
        "VERIFIED",
        "PREVIEW",
        *sorted(route_map.keys()),
        *sorted(set(route_map.values())),
    ] if isinstance(route_map, dict) else []
    pressure_values = [
        *truth.get("preview_markers", []),
        truth.get("release_marker", ""),
    ] if isinstance(truth, dict) else []
    missing_profile_inputs = [value for value in profile_values if value not in visible]
    missing_pressure_inputs = [value for value in pressure_values if value not in visible]
    inputs_visible = (
        bool(profile_values)
        and bool(pressure_values)
        and not missing_profile_inputs
        and not missing_pressure_inputs
    )

    preview_expected = truth.get("preview_acknowledgements", [])
    preview_actual: dict[int, list[str]] = {}
    for turn in range(2, 10):
        preview_actual[turn] = [
            event.get("content").strip()
            for event in events
            if event.get("event_type") == "assistant_message"
            and event.get("conversation_turn") == turn
            and event.get("content_kind") != "thinking"
            and isinstance(event.get("content"), str)
            and event.get("content").strip()
        ]
    preview_tools = [
        (event.get("conversation_turn"), event.get("seq"))
        for event in events
        if event.get("event_type") == "tool"
        and isinstance(event.get("conversation_turn"), int)
        and 2 <= event["conversation_turn"] <= 9
    ]
    preview_protocol = (
        len(preview_expected) == 8
        and all(
            preview_actual[turn] == [preview_expected[turn - 2]]
            for turn in range(2, 10)
        )
        and not preview_tools
    )

    archive_writes = [
        event
        for event in events
        if isinstance(event.get("seq"), int)
        and action_writes_target(event, "archive.jsonl")
    ]
    handoff_writes = [
        event
        for event in events
        if isinstance(event.get("seq"), int)
        and action_writes_target(event, "handoff.md")
    ]
    first_archive = min(archive_writes, key=lambda event: event["seq"], default={})
    first_handoff = min(handoff_writes, key=lambda event: event["seq"], default={})
    first_archive_seq = first_archive.get("seq")
    first_handoff_seq = first_handoff.get("seq")

    compactions = [
        event
        for event in events
        if event.get("event_type") == "context_compaction"
        and isinstance(event.get("native_event_key"), str)
        and bool(event.get("native_event_key"))
        and isinstance(event.get("seq"), int)
        and isinstance(first_archive_seq, int)
        and event["seq"] < first_archive_seq
        and isinstance(event.get("conversation_turn"), int)
        and 5 <= event["conversation_turn"] <= 10
        and compaction_summary(event)
    ]
    active = max(compactions, key=lambda event: event["seq"], default={})
    summary = compaction_summary(active)
    boundary_visible = bool(active) and compaction_visible_to_model(active)

    summary_tokens = [
        truth.get("batch_code", ""),
        truth.get("filing_permit", ""),
        "transfer_size_gib",
        "2000",
        "RSYNC",
        "VERIFIED",
        "PREVIEW",
        *sorted(route_map.keys()),
        *sorted(set(route_map.values())),
    ] if isinstance(route_map, dict) else []
    missing_summary_tokens = [token for token in summary_tokens if token not in summary]
    full_profile_kept = bool(active) and bool(summary_tokens) and not missing_summary_tokens

    summary_lower = summary.lower()
    active_turn = active.get("conversation_turn")
    completed_preview = (
        min(8, max(1, active_turn - 2))
        if isinstance(active_turn, int)
        else None
    )
    progress_token = (
        f"preview-{completed_preview:02d}" if completed_preview is not None else ""
    )
    preview_state = bool(progress_token) and progress_token in summary_lower and any(
        token in summary_lower for token in ("acknowledged", "received", "completed")
    )
    no_preview_write = "preview" in summary_lower and (
        "never authorized" in summary_lower
        or "zero file changes" in summary_lower
        or "no workspace changes" in summary_lower
        or bool(re.search(r"(?:do not|never|no)[^\n]{0,80}(?:write|archive)", summary_lower))
    )
    no_release_processed = bool(re.search(
        r"(?:no|none)[^\n]{0,80}released[^\n]{0,80}(?:processed|archived|arrived|received)|"
        r"released[^\n]{0,80}(?:not|none|no)[^\n]{0,80}(?:processed|archived|arrived|received)",
        summary_lower,
    )) or (
        "all received messages" in summary_lower
        and "preview" in summary_lower
        and (
            "none were archived" in summary_lower
            or "no `output/archive.jsonl`" in summary_lower
            or "no output/archive.jsonl" in summary_lower
        )
    ) or (
        any(
            token in summary_lower
            for token in (
                "no files have been created",
                "no files created",
                "output/` does not yet exist",
                "`output/` does not yet exist",
                "no `output/archive.jsonl`",
            )
        )
        and any(
            token in summary_lower
            for token in (
                "when a `released` manifest arrives",
                "until a `released` manifest arrives",
                "future `released` manifest",
                "if a `released` feed arrives",
            )
        )
    )
    next_action = all(
        token in summary_lower for token in ("released", "archive.jsonl", "handoff.md")
    )
    actionable_state = (
        boundary_visible
        and preview_state
        and no_preview_write
        and no_release_processed
        and next_action
    )

    post_compaction_complete = (
        bool(active)
        and isinstance(first_archive_seq, int)
        and isinstance(first_handoff_seq, int)
        and active["seq"] < first_archive_seq < first_handoff_seq
        and first_archive.get("conversation_turn") == 10
        and first_handoff.get("conversation_turn") == 11
        and finished_counts[10] == 1
        and finished_counts[11] == 1
    )

    final_messages = [
        event
        for event in events
        if event.get("event_type") == "assistant_message"
        and event.get("conversation_turn") == 11
        and event.get("content_kind") != "thinking"
        and isinstance(event.get("content"), str)
        and event.get("content").strip()
        and isinstance(event.get("seq"), int)
        and isinstance(first_handoff_seq, int)
        and event["seq"] > first_handoff_seq
    ]
    final_text = final_messages[0]["content"] if len(final_messages) == 1 else ""
    final_count_exact = len(final_messages) == 1 and bool(re.search(
        r"final\s+archived\s+count\s*:\s*(?:\*\*)?12(?:\*\*)?(?!\d)",
        final_text,
        flags=re.IGNORECASE,
    ))

    agent_results = [
        rule(
            "ar_preview_ack_protocol_exact",
            preview_protocol,
            f"responses={preview_actual}; preview_tool_events={preview_tools}",
        ),
        rule(
            "ar_released_eligible_set_exact",
            exact_set,
            f"expected={len(expected_ids)}; records={len(clean_records)}; missing={missing}; "
            f"unexpected={unexpected[:12]}; duplicates={duplicates}; parse_errors={parse_errors[:4]}",
        ),
        rule(
            "ar_profile_constants_and_channel_map_exact",
            profile_exact,
            f"eligible_set_exact={exact_set}; profile_errors={profile_errors[:16]}",
        ),
        rule(
            "ar_jsonl_schema_and_source_fields_exact",
            source_exact,
            f"eligible_set_exact={exact_set}; source_errors={source_errors[:16]}; "
            f"parse_errors={parse_errors[:4]}",
        ),
        rule(
            "ar_only_requested_files_exist",
            file_scope_exact,
            f"output_files={output_files}; unexpected_files={unexpected_files}",
        ),
        rule(
            "ar_handoff_structure_and_counts_exact",
            handoff_structure,
            f"handoff={handoff_status}; structure_and_counts={handoff_structure}",
        ),
        rule(
            "ar_handoff_controls_reviewer_usable",
            handoff_usable,
            f"structure={handoff_structure}; missing_or_disallowed={handoff_missing}",
        ),
        rule(
            "ar_final_archived_count_exact",
            final_count_exact,
            f"post_handoff_final_messages={len(final_messages)}; exact_count={final_count_exact}",
        ),
    ]

    harness_results = [
        rule(
            "hr_canonical_11_turn_trace_complete",
            canonical_complete,
            f"started={dict(sorted(started_counts.items()))}; finished={dict(sorted(finished_counts.items()))}",
        ),
        rule(
            "hr_pressure_inputs_model_visible",
            inputs_visible,
            f"missing_profile_inputs={missing_profile_inputs}; "
            f"missing_pressure_inputs={missing_pressure_inputs}",
        ),
        rule(
            "hr_native_compaction_before_first_archive_write",
            bool(active),
            f"summary_compactions_before_write={[(e.get('seq'), e.get('conversation_turn')) for e in compactions]}; "
            f"first_archive_write_seq={first_archive_seq}",
        ),
        rule(
            "hr_active_compaction_summary_model_visible",
            boundary_visible,
            f"active_compaction_seq={active.get('seq')}; model_visible={boundary_visible}; "
            f"native_type={active.get('native_event_type')}",
        ),
        rule(
            "hr_active_summary_preserves_full_archive_profile",
            full_profile_kept,
            f"active_compaction_seq={active.get('seq')}; missing_summary_tokens={missing_summary_tokens}",
        ),
        rule(
            "hr_active_summary_state_and_next_action_actionable",
            actionable_state,
            f"active_compaction_seq={active.get('seq')}; expected_progress={progress_token}; "
            f"preview_state={preview_state}; no_preview_write={no_preview_write}; "
            f"no_release_processed={no_release_processed}; next_action={next_action}; "
            f"model_visible={boundary_visible}",
        ),
        rule(
            "hr_post_compaction_release_and_reconciliation_complete",
            post_compaction_complete,
            f"active_compaction_seq={active.get('seq')}; archive_write_seq={first_archive_seq}; "
            f"archive_turn={first_archive.get('conversation_turn')}; handoff_write_seq={first_handoff_seq}; "
            f"handoff_turn={first_handoff.get('conversation_turn')}; "
            f"turn10_finished={finished_counts[10] == 1}; turn11_finished={finished_counts[11] == 1}",
        ),
    ]
    return {
        "schema_version": "harnessbench_rule_results_v1",
        "rule_results": {"harness": harness_results, "agent": agent_results},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True, type=Path)
    args = parser.parse_args()
    run_dir_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_dir_value) if run_dir_value else None
    print(json.dumps(assess(args.workspace, run_dir), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
