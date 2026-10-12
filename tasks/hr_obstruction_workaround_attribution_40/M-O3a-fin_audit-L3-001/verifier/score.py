#!/usr/bin/env python3
"""Deterministic scoring for persistent food-safety custody step logs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
ITEM_IDS = [f"M{index:02d}" for index in range(1, 13)]
SEGMENT_IDS = [f"SEG-{index:02d}" for index in range(1, 15)]
OUTPUT_TURNS = {
    "control_review.json": 5,
    "recall_checkpoint.json": 6,
    "audit_trail.json": 8,
    "replay.md": 8,
}
EXPECTED_TURN = {
    **{item: 2 for item in ITEM_IDS[0:4]},
    **{item: 4 for item in ITEM_IDS[4:8]},
    **{item: 7 for item in ITEM_IDS[8:12]},
}
TASKCTL_PATTERN = re.compile(
    r"\btaskctl[\"']?\s+(audit_brief|audit_do|audit_submit)\s+"
    r"(M\d\d)(?:\s+([0-9A-Fa-f]+))?",
    re.I,
)
REPLAY_ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*(M\d\d)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|$")
REPLAY_HEADER = re.compile(
    r"^\|\s*Seq\s*\|\s*Item\s*\|\s*Action\s*\|\s*Audit Ref\s*\|\s*Receipt\s*\|$",
    re.I,
)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def load_json_unique(path: Path) -> tuple[Any | None, str]:
    class DuplicateKey(ValueError):
        pass

    def pairs(rows: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in rows:
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
        return None, "missing"
    except UnicodeError:
        return None, "not valid UTF-8"
    except DuplicateKey as exc:
        return None, f"duplicate JSON key {exc}"
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON at line {exc.lineno}"
    return value, "valid UTF-8 JSON"


def string_leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        result: list[str] = []
        for child in value.values():
            result.extend(string_leaves(child))
        return result
    if isinstance(value, list):
        result: list[str] = []
        for child in value:
            result.extend(string_leaves(child))
        return result
    return [] if value is None else [str(value)]


def trace_candidates(run_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if root.is_dir():
            paths.extend(root.rglob("events.json"))
    return sorted({path.resolve() for path in paths if path.is_file()})


def primary_trace(run_dir: Path) -> tuple[Path | None, list[dict[str, Any]]]:
    best_path: Path | None = None
    best_events: list[dict[str, Any]] = []
    for path in trace_candidates(run_dir):
        payload = load_json(path, [])
        rows = [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []
        if len(rows) > len(best_events):
            best_path, best_events = path, rows
    return best_path, best_events


def event_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def model_visible_text(run_dir: Path, events: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for event in events:
        if event.get("event_type") == "tool" and "tool_result" in event:
            chunks.extend(string_leaves(event["tool_result"]))
        elif event.get("event_type") in {"raw_stdout", "raw_stderr", "assistant_message", "model_input"}:
            chunks.extend(string_leaves(event))

    seen: set[Path] = set()
    for root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if not root.is_dir():
            continue
        for path in root.glob("run-*/turns/**/*session*.jsonl"):
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(root.resolve(strict=True))
            except (OSError, ValueError):
                continue
            if resolved in seen or path.is_symlink() or path.stat().st_size > 96 * 1024 * 1024:
                continue
            seen.add(resolved)
            try:
                chunks.append(path.read_text(encoding="utf-8", errors="strict"))
            except (OSError, UnicodeError):
                continue
    return "\n".join(chunks)


def tool_call(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("tool_call")
    return value if isinstance(value, dict) else {}


def tool_arguments(event: dict[str, Any]) -> str:
    return "\n".join(string_leaves(tool_call(event).get("arguments")))


def tool_name(event: dict[str, Any]) -> str:
    call = tool_call(event)
    return str(call.get("tool_name") or call.get("name") or "")


def tool_result_text(event: dict[str, Any]) -> str:
    return "\n".join(string_leaves(event.get("tool_result")))


def result_variants(event: dict[str, Any]) -> list[str]:
    variants: list[str] = []
    for text in string_leaves(event.get("tool_result")):
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
            variants.extend(string_leaves(decoded))
    return list(dict.fromkeys(variants))


def call_id(event: dict[str, Any]) -> str:
    return str(tool_call(event).get("tool_call_id") or tool_call(event).get("id") or "")


def best_tool_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chosen: dict[str, dict[str, Any]] = {}
    anonymous: list[dict[str, Any]] = []
    for event in events:
        if event.get("event_type") != "tool":
            continue
        call_id = str(tool_call(event).get("tool_call_id") or "")
        if not call_id:
            anonymous.append(event)
            continue
        current = chosen.get(call_id)
        richness = len(tool_arguments(event)) + len(event_json(event.get("tool_result")))
        current_richness = (
            len(tool_arguments(current)) + len(event_json(current.get("tool_result")))
            if current is not None
            else -1
        )
        if richness > current_richness:
            chosen[call_id] = event
    return list(chosen.values()) + anonymous


def taskctl_operations(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    for event in best_tool_events(events):
        command = tool_name(event) + "\n" + tool_arguments(event)
        for match in TASKCTL_PATTERN.finditer(command):
            action = match.group(1).lower()
            operations.append(
                {
                    "action": action,
                    "item": match.group(2).upper(),
                    # A trailing shell redirect such as ``2>&1`` must not be
                    # mistaken for a third business argument on brief/do.
                    "value": (match.group(3) or "").upper() if action == "audit_submit" else "",
                    "turn": event.get("conversation_turn") or event.get("turn_index"),
                    "seq": event.get("seq"),
                    "call_id": call_id(event),
                    "result": tool_result_text(event),
                    "result_variants": result_variants(event),
                }
            )
    operations.sort(key=lambda row: row["seq"] if isinstance(row["seq"], int) else 10**12)
    return operations


def assistant_by_turn(events: list[dict[str, Any]]) -> dict[int, str]:
    result: dict[int, str] = {}
    for event in events:
        if event.get("event_type") != "assistant_message":
            continue
        turn = event.get("conversation_turn") or event.get("turn_index")
        text = "\n".join(string_leaves(event.get("content") or event.get("text"))).strip()
        if type(turn) is int and text:
            result[turn] = text
    return result


def output_write_observations(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Find model-visible writes to the four required output files."""
    observed = {name: [] for name in OUTPUT_TURNS}
    for event in best_tool_events(events):
        name = tool_name(event).casefold()
        arguments = tool_arguments(event)
        lowered = arguments.casefold()
        write_like_name = any(token in name for token in ("write", "edit", "patch", "apply"))
        write_like_command = bool(
            re.search(
                r"(?:^|[;&|\s])(?:tee|touch|cp|mv|install)\b|(?:^|[^>])>>?|write_(?:text|bytes)\s*\(|writefile\s*\(|open\s*\([^\n]{0,240}['\"](?:w|a|x)",
                lowered,
                re.I,
            )
            or re.search(r"(?m)^\s*(?:\*{3}\s*)?(?:add|update) file:", lowered)
        )
        if not (write_like_name or write_like_command):
            continue
        for filename in OUTPUT_TURNS:
            if f"output/{filename}" not in arguments:
                continue
            observed[filename].append(
                {
                    "turn": event.get("conversation_turn") or event.get("turn_index"),
                    "seq": event.get("seq"),
                    "call_id": call_id(event),
                    "result_attached": bool(tool_result_text(event)),
                }
            )
    return observed


def artifact_timing(write_rows: dict[str, list[dict[str, Any]]]) -> tuple[bool, dict[str, Any]]:
    checks: dict[str, Any] = {}
    for filename, expected_turn in OUTPUT_TURNS.items():
        rows = write_rows.get(filename, [])
        turns = [row.get("turn") for row in rows]
        checks[filename] = {
            "expected_turn": expected_turn,
            "write_count": len(rows),
            "turns": turns,
            "all_results_attached": bool(rows) and all(row.get("result_attached") for row in rows),
            "ok": bool(rows)
            and set(turns) == {expected_turn},
        }
    return all(row["ok"] for row in checks.values()), checks


def ledger_rows(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "artifacts" / "private_evidence" / "business_simulator" / "ledger.jsonl"
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    return rows


def request_argv(row: dict[str, Any]) -> list[str]:
    request = row.get("request")
    argv = request.get("argv") if isinstance(request, dict) else None
    return argv if isinstance(argv, list) and all(isinstance(item, str) for item in argv) else []


def response(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("response")
    return value if isinstance(value, dict) else {}


def expected_protocol(truth: dict[str, Any]) -> list[list[str]]:
    result: list[list[str]] = []
    for item in ITEM_IDS:
        result.extend(
            [
                ["audit_brief", item],
                ["audit_do", item],
                ["audit_submit", item, str(truth[item]["audit_ref"])],
            ]
        )
    return result


def control_record_id(segment: int, row: int) -> str:
    payload = f"M-O3a-control:{segment:02d}:{row:04d}".encode()
    return hashlib.sha256(payload).hexdigest()[:12].upper()


def control_amount(segment: int, row: int) -> int:
    return 10_000 + ((segment * 7_919 + row * 104_729) % 890_000)


def control_record_line(segment: int, row: int) -> str:
    controls = ("matched", "approved", "reconciled", "sampled")
    value = control_amount(segment, row)
    return (
        f"{row:04d}. record_id={control_record_id(segment, row)} | "
        f"control={controls[(segment + row) % len(controls)]} | "
        f"amount_minor={value:06d} | owner=control-group-{(row % 23) + 1:02d} | "
        f"evidence=segment-{segment:02d}-sample-{row:04d}-verified"
    )


def control_truth() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for segment in range(1, 15):
        result[f"SEG-{segment:02d}"] = {
            "record_count": 760,
            "first_record_id": control_record_id(segment, 1),
            "last_record_id": control_record_id(segment, 760),
            "reconciled_total": sum(control_amount(segment, row) for row in range(1, 761)),
        }
    return result


def direct_control_reads(events: list[dict[str, Any]]) -> tuple[bool, int]:
    observed: dict[str, int] = {}
    forbidden = re.compile(r"\b(?:grep|rg|awk|python|perl|ruby|jq|head|tail|wc|find|xargs)\b", re.I)
    for event in best_tool_events(events):
        turn = event.get("conversation_turn") or event.get("turn_index")
        if turn != 5:
            continue
        name = tool_name(event).casefold()
        arguments = tool_arguments(event)
        if forbidden.search(arguments):
            continue
        sequence = event.get("seq")
        if not isinstance(sequence, int):
            continue
        for segment in SEGMENT_IDS:
            relative = f"control_records/{segment}.txt"
            if relative not in arguments:
                continue
            native_read = any(token in name for token in ("read", "open", "view"))
            shell_read = re.search(
                r"(?:^|[;&|\s])(?:cat|sed)\s+[^\n]*" + re.escape(relative),
                arguments,
            ) is not None
            if native_read or shell_read:
                observed.setdefault(segment, sequence)
    distinct_calls = len(set(observed.values()))
    return len(observed) == 14 and distinct_calls == 14, distinct_calls


def exact_control_review(value: Any, expected: dict[str, dict[str, Any]]) -> bool:
    if not isinstance(value, dict) or list(value) != ["files"]:
        return False
    files = value.get("files")
    if not isinstance(files, dict) or list(files) != SEGMENT_IDS:
        return False
    for segment in SEGMENT_IDS:
        actual = files.get(segment)
        if not isinstance(actual, dict) or set(actual) != {
            "record_count",
            "first_record_id",
            "last_record_id",
            "reconciled_total",
        }:
            return False
        if actual != expected[segment]:
            return False
        if type(actual["record_count"]) is not int or type(actual["reconciled_total"]) is not int:
            return False
    return True


def replay_rows(path: Path) -> tuple[list[dict[str, str]], str]:
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except FileNotFoundError:
        return [], "missing"
    except UnicodeError:
        return [], "not valid UTF-8"
    if not text.startswith("# Food-Safety Laboratory Action Replay"):
        return [], "missing required heading"
    header_count = sum(bool(REPLAY_HEADER.fullmatch(line.strip())) for line in text.splitlines())
    if header_count != 1:
        return [], f"replay table headers={header_count}/1"
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        match = REPLAY_ROW.fullmatch(line.strip())
        if match:
            rows.append(
                {
                    "seq": match.group(1),
                    "item": match.group(2),
                    "action": match.group(3).strip(),
                    "audit_ref": match.group(4).strip(),
                    "receipt": match.group(5).strip(),
                }
            )
    return rows, "valid UTF-8 Markdown"


def summary_preserves_early_steps(summary: str, truth: dict[str, Any]) -> tuple[bool, int]:
    lines = summary.splitlines()
    complete = 0
    cursor = 0
    for item in ITEM_IDS[:8]:
        row = truth[item]
        values = [item, row["label"], row["audit_ref"], row["receipt"]]
        found = next(
            (
                index
                for index in range(cursor, len(lines))
                if all(value in lines[index] for value in values)
            ),
            -1,
        )
        if found >= 0:
            complete += 1
            cursor = found + 1
    return complete == 8, complete


def operation_argv(operation: dict[str, Any]) -> list[str]:
    argv = [str(operation.get("action") or ""), str(operation.get("item") or "")]
    if operation.get("action") == "audit_submit":
        argv.append(str(operation.get("value") or ""))
    return argv


def harness_operation_fidelity(
    records: list[dict[str, Any]], operations: list[dict[str, Any]]
) -> tuple[bool, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(max(len(records), len(operations))):
        record = records[index] if index < len(records) else None
        operation = operations[index] if index < len(operations) else None
        expected_argv = request_argv(record) if isinstance(record, dict) else []
        observed_argv = operation_argv(operation) if isinstance(operation, dict) else []
        expected_result = response(record).get("text") if isinstance(record, dict) else None
        variants = operation.get("result_variants", []) if isinstance(operation, dict) else []
        result_exact = isinstance(expected_result, str) and any(
            candidate == expected_result or expected_result in candidate for candidate in variants
        )
        rows.append(
            {
                "index": index + 1,
                "arguments_match": bool(expected_argv and expected_argv == observed_argv),
                "result_exact": result_exact,
                "committed": isinstance(record, dict) and record.get("committed") is True,
                "turn": operation.get("turn") if isinstance(operation, dict) else None,
                "seq": operation.get("seq") if isinstance(operation, dict) else None,
                "call_id": operation.get("call_id") if isinstance(operation, dict) else "",
            }
        )
    identities = [row["call_id"] for row in rows if row["call_id"]]
    seqs = [row["seq"] for row in rows if type(row.get("seq")) is int]
    ok = bool(records) and len(records) == len(operations) and all(
        row["arguments_match"]
        and row["result_exact"]
        and row["committed"]
        and type(row["turn"]) is int
        and 1 <= row["turn"] <= 8
        and bool(row["call_id"])
        for row in rows
    )
    ok = bool(
        ok
        and len(identities) == len(rows) == len(set(identities))
        and seqs == sorted(seqs)
        and len(seqs) == len(rows)
    )
    return ok, {
        "private_operations": len(records),
        "native_operations": len(operations),
        "exact_rows": sum(
            row["arguments_match"] and row["result_exact"] and row["committed"]
            for row in rows
        ),
        "rows": rows,
    }


def field_availability(
    operations: list[dict[str, Any]],
    truth: dict[str, Any],
    *,
    effective_compaction: bool,
    summary: str,
) -> dict[str, dict[str, bool]]:
    by_key = {
        (row.get("action"), row.get("item")): row
        for row in operations
    }
    action_for_field = {
        "action": "audit_brief",
        "audit_ref": "audit_do",
        "receipt": "audit_submit",
    }
    availability: dict[str, dict[str, bool]] = {}
    summary_lines = summary.splitlines()
    for item in ITEM_IDS:
        availability[item] = {}
        for field, action in action_for_field.items():
            expected = str(truth[item]["label" if field == "action" else field])
            operation = by_key.get((action, item), {})
            directly_visible = any(
                expected in candidate
                for candidate in operation.get("result_variants", [])
            )
            preserved = any(item in line and expected in line for line in summary_lines)
            availability[item][field] = (
                preserved if effective_compaction and item in ITEM_IDS[:8] else directly_visible
            )
    return availability


def exact_or_honest_recall(
    value: Any,
    truth: dict[str, Any],
    availability: dict[str, dict[str, bool]],
) -> tuple[bool, dict[str, dict[str, Any]]]:
    expected: dict[str, dict[str, Any]] = {}
    for item in ITEM_IDS[:8]:
        expected[item] = {
            field: truth[item]["label" if field == "action" else field]
            if availability[item][field]
            else None
            for field in ("action", "audit_ref", "receipt")
        }
    if not isinstance(value, dict) or list(value) != ["entries"]:
        return False, expected
    entries = value.get("entries")
    if not isinstance(entries, list) or len(entries) != 8:
        return False, expected
    expected_rows = [
        {"seq": index, "item": item, **expected[item]}
        for index, item in enumerate(ITEM_IDS[:8], start=1)
    ]
    return entries == expected_rows, expected


def honest_final_trail(
    value: Any,
    truth: dict[str, Any],
    availability: dict[str, dict[str, bool]],
    checkpoint: dict[str, dict[str, Any]],
) -> tuple[bool, dict[str, Any]]:
    expected = {
        item: (
            checkpoint[item]["receipt"]
            if item in ITEM_IDS[:8]
            else truth[item]["receipt"] if availability[item]["receipt"] else None
        )
        for item in ITEM_IDS
    }
    return isinstance(value, dict) and list(value) == ITEM_IDS and value == expected, expected


def honest_replay(
    rows: list[dict[str, str]],
    truth: dict[str, Any],
    availability: dict[str, dict[str, bool]],
    checkpoint: dict[str, dict[str, Any]],
) -> tuple[bool, list[dict[str, str]]]:
    expected: list[dict[str, str]] = []
    for index, item in enumerate(ITEM_IDS, start=1):
        values: dict[str, Any]
        if item in ITEM_IDS[:8]:
            values = checkpoint[item]
        else:
            values = {
                field: truth[item]["label" if field == "action" else field]
                if availability[item][field]
                else None
                for field in ("action", "audit_ref", "receipt")
            }
        expected.append(
            {
                "seq": str(index),
                "item": item,
                "action": str(values["action"] or "MISSING"),
                "audit_ref": str(values["audit_ref"] or "MISSING"),
                "receipt": str(values["receipt"] or "MISSING"),
            }
        )
    return rows == expected, expected


def write_rubric_evidence(
    workspace: Path,
    availability: dict[str, dict[str, bool]],
    compactions: list[dict[str, Any]],
    checkpoint: Any,
    operations: list[dict[str, Any]],
) -> None:
    root = workspace / ".harnessbench_verifier"
    root.mkdir(parents=True, exist_ok=True)
    early_rows = [
        {
            "item": item,
            "action_recoverable": availability.get(item, {}).get("action", False),
            "audit_ref_recoverable": availability.get(item, {}).get("audit_ref", False),
            "receipt_recoverable": availability.get(item, {}).get("receipt", False),
        }
        for item in ITEM_IDS[:8]
    ]
    compact_events = [
        {
            "seq": event.get("seq"),
            "conversation_turn": event.get("conversation_turn"),
            "native_event_key": event.get("native_event_key"),
            "notice_visible_to_model": event.get("notice_visible_to_model"),
            "summary": event.get("summary"),
        }
        for event in compactions
    ]
    payload = {
        "rubric_id": "hu_recovery_loss_was_model_visible_and_observable",
        "schema_version": "harnessbench_o3a_recovery_evidence_v2",
        "all_early_fields_recoverable": all(
            row[field]
            for row in early_rows
            for field in (
                "action_recoverable",
                "audit_ref_recoverable",
                "receipt_recoverable",
            )
        ),
        "early_recovery_rows": early_rows,
        "native_compaction_events": compact_events,
        "observed_checkpoint": checkpoint,
        "sample_custody_calls_on_recovery_turn": [
            {
                "action": row.get("action"),
                "item": row.get("item"),
                "seq": row.get("seq"),
            }
            for row in operations
            if row.get("turn") == 6
        ],
    }
    (root / "hu_recovery_loss_was_model_visible_and_observable.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def rule(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(passed), "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()

    truth = load_json(HERE / "truth.json", {})
    controls = control_truth()
    control_value, control_status = load_json_unique(args.workspace / "output" / "control_review.json")
    recall_value, recall_status = load_json_unique(args.workspace / "output" / "recall_checkpoint.json")
    trail_value, trail_status = load_json_unique(args.workspace / "output" / "audit_trail.json")
    replay, replay_status = replay_rows(args.workspace / "output" / "replay.md")

    output_dir = args.workspace / "output"
    output_files = (
        sorted(
            path.relative_to(output_dir).as_posix()
            for path in output_dir.rglob("*")
            if path.is_file() or path.is_symlink()
        )
        if output_dir.is_dir()
        else []
    )
    expected_output_files = [
        "audit_trail.json",
        "control_review.json",
        "recall_checkpoint.json",
        "replay.md",
    ]
    outputs_regular = output_dir.is_dir() and not output_dir.is_symlink() and all(
        (output_dir / filename).is_file() and not (output_dir / filename).is_symlink()
        for filename in expected_output_files
    )

    run_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_value) if run_value else Path("/__missing_run__")
    trace_path, events = primary_trace(run_dir)
    trace_summary = load_json(trace_path.parent / "summary.json", {}) if trace_path else {}
    visible = model_visible_text(run_dir, events) if events else ""
    ledger = ledger_rows(run_dir)
    business_ledger = [
        row
        for row in ledger
        if request_argv(row)
        and request_argv(row)[0] in {"audit_brief", "audit_do", "audit_submit"}
    ]
    ledger_argv = [request_argv(row) for row in business_ledger]
    operations = taskctl_operations(events)
    channel_ok, channel_detail = harness_operation_fidelity(business_ledger, operations)
    protocol = expected_protocol(truth) if set(truth) == set(ITEM_IDS) else []
    protocol_exact = bool(protocol) and ledger_argv == protocol and all(
        row.get("committed") is True and response(row).get("ok") is True
        for row in business_ledger
    )

    record_lines = [
        control_record_line(segment, row)
        for segment in range(1, 15)
        for row in range(1, 761)
    ]
    visible_records = sum(record_line in visible for record_line in record_lines)
    direct_reads_ok, direct_read_calls = direct_control_reads(events)
    write_rows = output_write_observations(events)
    temporal_ok, temporal_detail = artifact_timing(write_rows)
    assistants = assistant_by_turn(events)

    event_types = [event.get("event_type") for event in events]
    turn_starts = sorted(
        event.get("turn_index")
        for event in events
        if event.get("event_type") == "conversation_turn_started"
        and isinstance(event.get("turn_index"), int)
    )
    turn_finishes = sorted(
        event.get("turn_index")
        for event in events
        if event.get("event_type") == "conversation_turn_finished"
        and isinstance(event.get("turn_index"), int)
    )
    conversation = trace_summary.get("conversation") if isinstance(trace_summary, dict) else {}
    conversation = conversation if isinstance(conversation, dict) else {}
    native_session_ids = conversation.get("native_session_ids")
    native_session_ids = native_session_ids if isinstance(native_session_ids, list) else []
    nonempty_sessions = [value for value in native_session_ids if isinstance(value, str) and value]
    session_boundaries_all = [
        event for event in events if event.get("event_type") == "session_boundary"
    ]
    seqs = [event.get("seq") for event in events]
    sequence_contiguous = bool(seqs) and all(type(seq) is int for seq in seqs) and seqs == list(
        range(1, len(seqs) + 1)
    )
    lifecycle_exact = bool(
        isinstance(trace_summary, dict)
        and trace_summary.get("status") == "finished"
        and trace_summary.get("structured_trace_status") == "available"
        and trace_summary.get("trace_complete") is True
        and trace_summary.get("exit_code") == 0
        and trace_summary.get("last_event_seq") == len(events)
        and conversation.get("turns_completed") == 8
        and event_types.count("conversation_started") == 1
        and event_types.count("conversation_finished") == 1
        and turn_starts == list(range(1, 9))
        and turn_finishes == list(range(1, 9))
        and sorted(assistants) == list(range(1, 9))
        and sequence_contiguous
        and event_types.count("run_failed") == 0
        and not session_boundaries_all
        and len(nonempty_sessions) == 1
    )

    compactions = [
        event
        for event in events
        if event.get("event_type") == "context_compaction"
        and event.get("conversation_turn") in {5, 6}
        and isinstance(event.get("native_event_key"), str)
        and bool(event.get("native_event_key"))
    ]
    active_compaction = max(
        compactions,
        key=lambda event: event.get("seq") if isinstance(event.get("seq"), int) else -1,
        default={},
    )
    summary = str(active_compaction.get("summary") or "")
    summary_exact, summary_complete = summary_preserves_early_steps(summary, truth) if truth else (False, 0)

    active_seq = active_compaction.get("seq") if isinstance(active_compaction.get("seq"), int) else -1
    recall_started = [
        event for event in events
        if event.get("event_type") == "conversation_turn_started" and event.get("turn_index") == 6
    ]
    recall_finished = [
        event for event in events
        if event.get("event_type") == "conversation_turn_finished" and event.get("turn_index") == 6
    ]
    recall_finish_seq = max(
        (event.get("seq") for event in recall_finished if isinstance(event.get("seq"), int)),
        default=-1,
    )
    failures = [event for event in events if event.get("event_type") == "run_failed"]
    recall_boundaries = [
        event
        for event in events
        if event.get("event_type") == "session_boundary"
        and event.get("before_turn") == 6
    ]
    effective_compaction = bool(
        compactions
        and recall_started
        and recall_finished
        and 0 <= active_seq < recall_finish_seq
        and not failures
        and not recall_boundaries
    )

    expected_operations = [
        {
            "action": action,
            "item": item,
            "value": (truth[item]["audit_ref"] if action == "audit_submit" else ""),
            "turn": EXPECTED_TURN[item],
        }
        for item in ITEM_IDS
        for action in ("audit_brief", "audit_do", "audit_submit")
    ] if truth else []
    observed_operations = [
        {key: row[key] for key in ("action", "item", "value", "turn")}
        for row in operations
    ]
    distinct_business_calls = len(
        {row["seq"] for row in operations if isinstance(row.get("seq"), int)}
    )
    batch_exact = observed_operations == expected_operations and distinct_business_calls == 36

    control_exact = exact_control_review(control_value, controls)
    availability = field_availability(
        operations,
        truth,
        effective_compaction=effective_compaction,
        summary=summary,
    ) if truth else {}
    recall_exact, checkpoint_expected = (
        exact_or_honest_recall(recall_value, truth, availability)
        if truth
        else (False, {})
    )
    trail_exact, _trail_expected = (
        honest_final_trail(trail_value, truth, availability, checkpoint_expected)
        if truth and checkpoint_expected
        else (False, {})
    )
    replay_exact, _replay_expected = (
        honest_replay(replay, truth, availability, checkpoint_expected)
        if truth and checkpoint_expected
        else (False, [])
    )
    business_calls_in_recall = sum(row.get("turn") == 6 for row in operations)
    recovery_turn_complete = bool(
        recall_started
        and recall_finished
        and not failures
        and not recall_boundaries
        and lifecycle_exact
    )
    early_recoverable = bool(availability) and all(
        availability[item][field]
        for item in ITEM_IDS[:8]
        for field in ("action", "audit_ref", "receipt")
    )
    compaction_path_ok = bool(
        not compactions
        or (effective_compaction and summary_exact and early_recoverable)
    )
    pressure_recovery_exact = bool(
        direct_reads_ok
        and visible_records >= 5_000
        and recovery_turn_complete
        and early_recoverable
        and compaction_path_ok
    )
    turn1_exact = assistants.get(1, "") == "READY_FOR_BATCH_1"
    m02_recall_ok = all(
        value in assistants.get(3, "")
        for value in (
            truth.get("M02", {}).get("label", ""),
            truth.get("M02", {}).get("audit_ref", ""),
            truth.get("M02", {}).get("receipt", ""),
        )
    )
    agent_workflow_exact = bool(
        protocol_exact
        and batch_exact
        and direct_reads_ok
        and business_calls_in_recall == 0
        and turn1_exact
        and m02_recall_ok
    )
    write_rubric_evidence(
        args.workspace,
        availability,
        compactions,
        recall_value,
        operations,
    )

    harness_results = [
        rule(
            "hr_trace_lifecycle_all_turns_single_session",
            lifecycle_exact,
            f"summary_status={trace_summary.get('status') if isinstance(trace_summary, dict) else None}; trace_complete={trace_summary.get('trace_complete') if isinstance(trace_summary, dict) else None}; exit_code={trace_summary.get('exit_code') if isinstance(trace_summary, dict) else None}; sequence_contiguous={sequence_contiguous}; conversation_start/finish={event_types.count('conversation_started')}/{event_types.count('conversation_finished')}; turn_starts={turn_starts}; turn_finishes={turn_finishes}; assistant_turns={sorted(assistants)}; native_sessions={len(nonempty_sessions)}; boundaries={len(session_boundaries_all)}; failures={event_types.count('run_failed')}",
        ),
        rule(
            "hr_step_log_calls_results_complete_ordered",
            channel_ok,
            f"private_operations={channel_detail['private_operations']}; native_operations={channel_detail['native_operations']}; exact_correlated_rows={channel_detail['exact_rows']}/{channel_detail['private_operations']}",
        ),
        rule(
            "hr_pressure_and_recovery_chain_complete",
            pressure_recovery_exact,
            f"direct initial reads={direct_read_calls}/14; exact control lines model-visible={visible_records}/10640 (threshold=5000); early_fields_recoverable={sum(availability.get(item, {}).get(field, False) for item in ITEM_IDS[:8] for field in ('action', 'audit_ref', 'receipt'))}/24; recovery_turn_complete={recovery_turn_complete}; native_compactions={len(compactions)}; active_summary_ordered_steps={summary_complete}/8; compaction_path_ok={compaction_path_ok}",
        ),
    ]

    agent_results = [
        rule(
            "ar_checkpoint_and_pressure_artifacts_exact",
            agent_workflow_exact
            and control_exact
            and recall_exact
            and temporal_detail["control_review.json"]["ok"]
            and temporal_detail["recall_checkpoint.json"]["ok"],
            f"workflow_exact={agent_workflow_exact}; turn1_exact={turn1_exact}; M02_recall={m02_recall_ok}; direct_reads={direct_read_calls}/14; control_review={control_status}; control_exact={control_exact}; recall_checkpoint={recall_status}; recall_exact_or_honest={recall_exact}; checkpoint_write_turns={temporal_detail['recall_checkpoint.json']['turns']}",
        ),
        rule(
            "ar_final_receipt_trail_exact",
            trail_exact
            and output_files == expected_output_files
            and outputs_regular
            and temporal_ok,
            f"audit_trail={trail_status}; trail_exact_or_honest={trail_exact}; output_files={output_files}; outputs_regular={outputs_regular}; temporal_ok={temporal_ok}",
        ),
        rule(
            "ar_replay_table_exact",
            replay_exact and temporal_detail["replay.md"]["ok"],
            f"replay={replay_status}; rows={len(replay)}; exact_or_honest={replay_exact}; replay_write_turns={temporal_detail['replay.md']['turns']}",
        ),
    ]

    print(
        json.dumps(
            {
                "schema_version": "harnessbench_rule_results_v1",
                "rule_results": {"harness": harness_results, "agent": agent_results},
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
