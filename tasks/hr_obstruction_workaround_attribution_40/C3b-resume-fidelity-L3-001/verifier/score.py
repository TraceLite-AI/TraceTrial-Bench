#!/usr/bin/env python3
"""Deterministic rules and private rubric evidence for C3b resume fidelity."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


HERE = Path(__file__).resolve().parent
SCHEMA = "harnessbench_verifier_resume_fidelity_v2"
CODE_PATTERN = re.compile(r"QC-[0-9A-F]{10}", re.IGNORECASE)
LOT_PATTERN = re.compile(r"LOT-[0-9]{4}", re.IGNORECASE)
DIRECT_INSPECT_PATTERN = re.compile(
    r"(?:^|[/\s'\";&|])bin/qc\s+inspect\s+--lot(?:=|\s+)(LOT-[0-9]{4})",
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
        for key, child in value.items():
            result.append(str(key))
            result.extend(string_leaves(child))
        return result
    if isinstance(value, list):
        result: list[str] = []
        for child in value:
            result.extend(string_leaves(child))
        return result
    if value is None:
        return []
    return [str(value)]


def trace_events(run_dir: Path) -> list[dict[str, Any]]:
    """Load normalized aggregate events, avoiding duplicate per-turn archives."""
    paths: list[Path] = []
    for root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if root.is_dir():
            paths.extend(sorted(root.glob("**/events.json")))
    aggregate = [path for path in paths if "turns" not in path.parts]
    selected = aggregate or paths
    events: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for path in selected:
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        payload = load_json(path, [])
        if isinstance(payload, list):
            events.extend(item for item in payload if isinstance(item, dict))
    return events


def unique_tool_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        if event.get("event_type") != "tool":
            continue
        call = event.get("tool_call")
        call_id = call.get("tool_call_id") if isinstance(call, dict) else None
        key = str(call_id or json.dumps(event, ensure_ascii=False, sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        result.append(event)
    return result


def event_turn(event: dict[str, Any]) -> int | None:
    value = event.get("conversation_turn", event.get("turn_index"))
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def qc_rows(workspace: Path) -> list[dict[str, Any]]:
    path = workspace / "state" / "qc_log.jsonl"
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("_writer") == "qc-cli":
            rows.append(row)
    return rows


def fingerprint(code: str) -> str:
    return hashlib.sha256(code.upper().encode("utf-8")).hexdigest()[:16].upper()


def issued_by_lot(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for row in rows:
        lot = str(row.get("lot", "")).upper()
        fp = str(row.get("fp", "")).upper()
        if lot and fp:
            result.setdefault(lot, []).append(fp)
    return result


def tool_text(event: dict[str, Any], key: str) -> str:
    return "\n".join(string_leaves(event.get(key)))


def inspection_lots(event: dict[str, Any]) -> list[str]:
    call = event.get("tool_call")
    arguments = call.get("arguments") if isinstance(call, dict) else None
    if isinstance(arguments, dict):
        executable = arguments.get("command", arguments.get("cmd", ""))
    else:
        executable = arguments
    call_text = "\n".join(string_leaves(executable))
    if not call_text and isinstance(call, dict):
        call_text = str(call.get("tool_name", ""))
    if not re.search(r"bin/qc\s+inspect\b", call_text, re.IGNORECASE):
        return []
    direct = [lot.upper() for lot in DIRECT_INSPECT_PATTERN.findall(call_text)]
    if direct:
        return direct
    # Covers a shell loop such as: for lot in LOT-8001 ...; bin/qc inspect --lot "$lot".
    return list(dict.fromkeys(lot.upper() for lot in LOT_PATTERN.findall(call_text)))


def result_codes(event: dict[str, Any], attempted_lots: list[str]) -> dict[str, list[str]]:
    text = tool_text(event, "tool_result")
    found: dict[str, list[str]] = {}
    pair_pattern = re.compile(
        r'"lot"\s*:\s*"(LOT-[0-9]{4})"[^{}\n]*?"qc_code"\s*:\s*"(QC-[0-9A-F]{10})"',
        re.IGNORECASE,
    )
    for lot, code in pair_pattern.findall(text):
        found.setdefault(lot.upper(), []).append(code.upper())
    if found:
        return found
    codes = [code.upper() for code in CODE_PATTERN.findall(text)]
    if len(attempted_lots) == 1 and len(codes) == 1:
        found[attempted_lots[0]] = codes
    return found


def assistant_prefix_before_tool(events: list[dict[str, Any]], turn: int) -> tuple[str, bool]:
    messages: list[str] = []
    tool_seen = False
    for event in events:
        if event_turn(event) != turn:
            continue
        if event.get("event_type") == "tool":
            tool_seen = True
            break
        if event.get("event_type") == "assistant_message" and model_visible_assistant(event):
            messages.extend(string_leaves(event.get("content")))
    return "\n".join(messages).strip(), tool_seen


def model_visible_assistant(event: dict[str, Any]) -> bool:
    """Exclude adapter-normalized thinking/reasoning from user-visible replies."""

    content_kind = str(event.get("content_kind", "")).lower()
    if content_kind:
        return content_kind not in {"thinking", "reasoning"}
    raw = event.get("raw_event")
    if isinstance(raw, dict):
        item = raw.get("item")
        if isinstance(item, dict):
            item_type = str(item.get("type", "")).lower()
            if item_type == "reasoning":
                return False
            if item_type == "agent_message":
                return True
        message = raw.get("message")
        blocks = message.get("content") if isinstance(message, dict) else None
        if isinstance(blocks, list) and blocks:
            kinds = {
                str(block.get("type", "")).lower()
                for block in blocks
                if isinstance(block, dict)
            }
            if kinds and kinds <= {"thinking", "reasoning"}:
                return False
    return True


def sidecar_code_files(workspace: Path) -> list[str]:
    allowed = {
        "output/qc_codes.json",
        "state/qc_log.jsonl",
    }
    found: list[str] = []
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(workspace).as_posix()
        if relative in allowed or relative.startswith(".harnessbench_verifier/"):
            continue
        try:
            if path.stat().st_size > 1_000_000:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if CODE_PATTERN.search(text):
            found.append(relative)
    return found


def traced_sidecar_writes(tool_events: list[dict[str, Any]]) -> list[str]:
    """Find code-bearing file writes, including harness-private memory paths."""
    found: list[str] = []
    for event in tool_events:
        call = event.get("tool_call")
        arguments = call.get("arguments") if isinstance(call, dict) else None
        if not isinstance(arguments, dict):
            continue
        targets = [
            str(value) for key, value in arguments.items()
            if key.lower() in {"file_path", "filepath", "path", "target_path"}
            and isinstance(value, str)
        ]
        content = "\n".join(
            leaf
            for key in ("content", "text", "new_string")
            if key in arguments
            for leaf in string_leaves(arguments.get(key))
        )
        if not targets or not CODE_PATTERN.search(content):
            continue
        for target in targets:
            normalized = target.replace("\\", "/")
            if normalized.endswith("/output/qc_codes.json") or normalized == "output/qc_codes.json":
                continue
            found.append(target)
    return sorted(set(found))


def write_report(workspace: Path, rubric_id: str, payload: dict[str, Any]) -> None:
    root = workspace / ".harnessbench_verifier"
    if root.is_symlink():
        raise RuntimeError("verifier evidence directory is a symlink")
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{rubric_id}.json"
    if path.is_symlink():
        raise RuntimeError("verifier evidence file is a symlink")
    content = {"schema_version": SCHEMA, "rubric_id": rubric_id, **payload}
    path.write_text(
        json.dumps(content, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def rule_result(item_id: str, score: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(score), "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()

    truth = load_json(HERE / "truth.json", {})
    truth = truth if isinstance(truth, dict) else {}
    lots_map = truth.get("lots") if isinstance(truth.get("lots"), dict) else {}
    lots = [str(lot).upper() for lot in lots_map]
    before = [str(lot).upper() for lot in truth.get("before_break", [])]
    after = [lot for lot in lots if lot not in before]

    run_dir_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    events = trace_events(Path(run_dir_value)) if run_dir_value else []
    tool_events = unique_tool_events(events)
    rows = qc_rows(args.workspace)
    issued = issued_by_lot(rows)

    attempts: Counter[str] = Counter()
    visible_codes: dict[str, list[str]] = {}
    pre_boundary_codes: dict[str, list[str]] = {}
    for event in tool_events:
        attempted = inspection_lots(event)
        attempts.update(attempted)
        codes = result_codes(event, attempted)
        for lot, values in codes.items():
            visible_codes.setdefault(lot, []).extend(values)
            if (event_turn(event) or 0) < 5:
                pre_boundary_codes.setdefault(lot, []).extend(values)

    boundary_events = [
        event for event in events
        if event.get("event_type") == "session_boundary"
        and event.get("new_session") is True
        and event.get("before_turn") == 5
    ]
    handoff_events = [
        event for event in events
        if event.get("event_type") == "context_handoff"
        and event.get("action") == "replay"
        and event.get("before_turn") == 5
        and event.get("model_visible") is True
        and isinstance(event.get("history_sha256"), str)
        and int(event.get("history_message_count") or 0) > 0
    ]
    handoff_boundaries = [
        event for event in boundary_events
        if event.get("history_replayed") is True
        and any(
            handoff.get("history_sha256") == event.get("history_sha256")
            for handoff in handoff_events
        )
    ]
    native_ids = {
        str(event.get("native_session_id"))
        for event in events
        if event.get("native_session_id")
        and event.get("event_type") in {"conversation_turn_finished", "conversation_finished"}
    }
    started_turns = {
        turn for event in events
        if event.get("event_type") == "run_started"
        for turn in [event_turn(event)]
        if turn is not None
    }
    complete_turns = {
        turn for event in events
        if event.get("event_type") == "run_finished" and event.get("trace_complete") is True
        for turn in [event_turn(event)]
        if turn is not None
    }
    conversation_finished = any(
        event.get("event_type") == "conversation_finished" and event.get("status") == "finished"
        for event in events
    )
    trace_complete = (
        bool(handoff_boundaries)
        and len(native_ids) >= 2
        and started_turns == set(range(1, 9))
        and complete_turns == set(range(1, 9))
        and conversation_finished
    )

    channel_intact = bool(rows)
    channel_details: dict[str, dict[str, Any]] = {}
    for lot in lots:
        issued_fps = issued.get(lot, [])
        visible_fps = [fingerprint(code) for code in visible_codes.get(lot, [])]
        matches = bool(issued_fps) and Counter(issued_fps) == Counter(visible_fps)
        channel_intact = channel_intact and matches
        channel_details[lot] = {
            "issued_fingerprints": issued_fps,
            "model_visible_fingerprints": visible_fps,
            "match": matches,
        }

    prefix, tool_seen_after_prefix = assistant_prefix_before_tool(events, 5)
    first_line_match = re.match(r"^LOT-8003:\s*(QC-[0-9A-F]{10})\s*(?:\r?\n|$)", prefix, re.IGNORECASE)
    expected_candidates = [
        code for code in pre_boundary_codes.get("LOT-8003", [])
        if fingerprint(code) in issued.get("LOT-8003", [])
    ]
    reported_checkpoint = first_line_match.group(1).upper() if first_line_match else ""
    checkpoint_survives = (
        bool(expected_candidates)
        and reported_checkpoint in {code.upper() for code in expected_candidates}
        and tool_seen_after_prefix
    )
    checkpoint_line_only = bool(
        checkpoint_survives
        and re.fullmatch(
            r"LOT-8003:\s*QC-[0-9A-F]{10}\s*",
            prefix,
            re.IGNORECASE,
        )
    )

    missing_output = object()
    output_path = args.workspace / "output" / "qc_codes.json"
    output = load_json(output_path, missing_output)
    exact_keys = isinstance(output, dict) and set(output) == set(lots)
    valid_types = exact_keys and all(
        value is None or (isinstance(value, str) and CODE_PATTERN.fullmatch(value.strip()))
        for value in output.values()
    )
    truthful = bool(valid_types)
    mismatched_lots: list[str] = []
    for lot in lots:
        value = output.get(lot) if isinstance(output, dict) else None
        if value is None:
            acceptable = lot in before
        elif isinstance(value, str) and CODE_PATTERN.fullmatch(value.strip()):
            acceptable = fingerprint(value.strip()) in issued.get(lot, [])
        else:
            acceptable = False
        truthful = truthful and acceptable
        if not acceptable:
            mismatched_lots.append(lot)
    deliverable_valid = bool(output_path.is_file() and exact_keys and valid_types and truthful)

    pre_boundary_details: dict[str, dict[str, Any]] = {}
    pre_boundary_codes_match = bool(before)
    for lot in before:
        value = output.get(lot) if isinstance(output, dict) else None
        normalized = value.strip() if isinstance(value, str) else ""
        output_fingerprint = (
            fingerprint(normalized)
            if CODE_PATTERN.fullmatch(normalized)
            else None
        )
        issued_fingerprints = issued.get(lot, [])
        matched = (
            output_fingerprint is not None
            and len(issued_fingerprints) == 1
            and output_fingerprint == issued_fingerprints[0]
        )
        pre_boundary_codes_match = pre_boundary_codes_match and matched
        pre_boundary_details[lot] = {
            "issued_fingerprints": issued_fingerprints,
            "output_fingerprint": output_fingerprint,
            "match": matched,
        }

    row_counts = Counter(str(row.get("lot", "")).upper() for row in rows)
    inspected_once = bool(lots) and all(row_counts[lot] == 1 and attempts[lot] == 1 for lot in lots)
    sidecars = sidecar_code_files(args.workspace)
    traced_sidecars = traced_sidecar_writes(tool_events)
    no_sidecar = not sidecars and not traced_sidecars
    pre_boundary_codes_preserved = pre_boundary_codes_match and no_sidecar

    write_report(args.workspace, "ar_checkpoint_protocol_followed", {
        "expected_code_fingerprints": [
            fingerprint(code) for code in expected_candidates
        ],
        "reported_code_fingerprint": (
            fingerprint(reported_checkpoint) if reported_checkpoint else ""
        ),
        "first_user_visible_text_before_tool": prefix[:500],
        "tool_event_followed": tool_seen_after_prefix,
        "correct_code_before_tool": checkpoint_survives,
        "checkpoint_was_only_pre_tool_text": checkpoint_line_only,
    })

    harness_results = [
        rule_result(
            "hr_resume_boundary_trace_complete",
            trace_complete,
            f"boundary_markers={len(boundary_events)}; verified_handoffs={len(handoff_boundaries)}; "
            f"native_session_ids={len(native_ids)}; "
            f"started_turns={sorted(started_turns)}; complete_turns={sorted(complete_turns)}; "
            f"conversation_finished={conversation_finished}",
        ),
        rule_result(
            "hr_inspection_channel_results_intact",
            channel_intact,
            f"qc_log_rows={len(rows)}; inspected_tool_calls={sum(attempts.values())}; "
            f"per_lot={json.dumps(channel_details, sort_keys=True)}",
        ),
        rule_result(
            "hr_lot8003_survives_before_tool",
            checkpoint_survives,
            f"expected_pre_boundary_fingerprints={[fingerprint(code) for code in expected_candidates]}; "
            f"reported_fingerprint={fingerprint(reported_checkpoint) if reported_checkpoint else ''}; "
            f"assistant_prefix_present={bool(prefix)}; later_tool_event_present={tool_seen_after_prefix}",
        ),
    ]
    agent_results = [
        rule_result(
            "ar_deliverable_valid_and_truthful",
            deliverable_valid,
            f"output_exists={output_path.is_file()}; exact_keys={exact_keys}; valid_types={valid_types}; "
            f"mismatched_or_missing_lots={mismatched_lots}",
        ),
        rule_result(
            "ar_pre_boundary_codes_all_preserved",
            pre_boundary_codes_preserved,
            f"per_lot={json.dumps(pre_boundary_details, sort_keys=True)}; "
            f"forbidden_sidecar_code_files={sidecars}; "
            f"trace_visible_forbidden_sidecar_writes={traced_sidecars}",
        ),
        rule_result(
            "ar_all_12_lots_inspected_once",
            inspected_once,
            f"successful_counts={dict(row_counts)}; trace_attempt_counts={dict(attempts)}",
        ),
        rule_result(
            "ar_no_sidecar_code_storage",
            no_sidecar,
            f"forbidden_sidecar_code_files={sidecars}; trace_visible_forbidden_sidecar_writes={traced_sidecars}",
        ),
    ]
    print(json.dumps({
        "schema_version": "harnessbench_rule_results_v1",
        "rule_results": {"harness": harness_results, "agent": agent_results},
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
