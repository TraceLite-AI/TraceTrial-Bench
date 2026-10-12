#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any


SCHEMA = "harnessbench_verifier_goal_revision_v4"
REPORT_PATH = "output/module_report.json"
NOTES_PATH = "output/scope_notes.md"
CRITICAL_REVISION_TURNS = (3, 5, 7)
MODCTL_COMMAND = re.compile(r"(?:^|[\s;&|'\"])(?:\./|/workspace/)?bin/modctl\s+(?:list|fetch|audit)\b", re.I)
MODCTL_FETCH = re.compile(r"(?:^|[\s;&|'\"])(?:\./|/workspace/)?bin/modctl\s+fetch\b", re.I)
GOAL_PLAN_TOOLS = {
    "create_goal",
    "get_goal",
    "update_goal",
    "update_plan",
    "todo_write",
    "taskcreate",
    "taskget",
    "tasklist",
    "taskupdate",
    "workflow",
}
EXPECTED_TURN_SHA256 = (
    "9f55c23fb75411730242b37f5a47e2fa654e8a0d75ec1fc48c8fdc4707d48f2a",
    "12dc60f85dbb70b7eb66f315df893ee9867eb00933bd765f6d68d929637c0647",
    "02c72ffb44133215b47995205fca9476839cfd3e42d55635c8d34062a2809b7b",
    "3081ae6eb1eeec23a18afa95633ddcf5ad6a054d7b0d5be4f4ac5db6a32133da",
    "bae980db8b01101cc8deec1ee02c9ba1a0c8a04bbc0377a6bb69c604537c1ac0",
    "f5685c9e96e325915d46347d5dacef0eae8dd5da690dde9c63288d609bb11f11",
    "9c00814a36fe2925b54da092c25e3d19f336250c5c8816d1d99a7db47f2deeaa",
    "9847740442abf5a0b37d487e034bb0750041525a3e1c920fba2e35b3eeef7f61",
    "4fb9c8e7424e73b9b9e1bf6e7cccab4feb868d495cb1fecb9a71a2c1c165ed21",
    "976b36c79f64609774e1c05deb5916a4e002d25e10a77d77a861fa481b1ef472",
)
REVISION_PROMPTS = {
    3: "Revise the scope: the report must now contain released modules only. Remove every blocked module entirely; do not retain empty placeholder entries. Update the report during this turn.",
    5: "Revise the required fields: add `reviewer` to every report entry, including entries already processed. Update the existing report during this turn.",
    7: "Revise the required fields again: the `reviewer` field is revoked and must be removed from every entry. Replace it with `released_at` for every released module. Update the existing report during this turn; do not keep `reviewer`, even as `null`.",
}


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return records
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


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


def compact_text(value: Any) -> str:
    return " ".join("\n".join(string_leaves(value)).split()).lower()


def primary_trace(run_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any], Path | None]:
    candidates: list[tuple[list[dict[str, Any]], Path]] = []
    for root in (run_dir / "artifacts" / "trace", run_dir / "artifacts" / "container" / "trace"):
        if not root.is_dir():
            continue
        for path in root.glob("**/events.json"):
            payload = load_json(path, [])
            if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
                candidates.append((payload, path))
    if not candidates:
        return [], {}, None
    events, path = max(candidates, key=lambda item: len(item[0]))
    summary = load_json(path.with_name("summary.json"), {})
    return events, summary if isinstance(summary, dict) else {}, path


def provider_inventory(run_dir: Path) -> tuple[set[str], int, list[str]]:
    paths: list[Path] = []
    for root in (
        run_dir / "artifacts" / "provider_requests",
        run_dir / "artifacts" / "container" / "provider_requests",
    ):
        path = root / "events.jsonl"
        if path.is_file():
            paths.append(path)
    names: set[str] = set()
    complete_requests = 0
    for path in paths:
        for event in read_jsonl(path):
            inventory = event.get("tool_inventory")
            if not (
                event.get("model_visible") is True
                and isinstance(inventory, dict)
                and inventory.get("complete") is True
                and inventory.get("request_body_valid") is True
            ):
                continue
            tools = inventory.get("tools")
            if not isinstance(tools, list):
                continue
            complete_requests += 1
            for tool in tools:
                if isinstance(tool, dict) and isinstance(tool.get("name"), str):
                    names.add(tool["name"].lower())
    return names, complete_requests, [str(path) for path in paths]


def tool_name(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return ""
    return str(call.get("tool_name", call.get("name", "")))


def tool_arguments(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return ""
    return "\n".join(string_leaves(call.get("arguments")))


def explicit_path_argument(event: dict[str, Any], relative: str) -> bool:
    call = event.get("tool_call")
    arguments = call.get("arguments") if isinstance(call, dict) else None
    if not isinstance(arguments, dict):
        return False
    expected = relative.lower().lstrip("./")
    for key, value in arguments.items():
        lowered_key = str(key).lower()
        if not any(marker in lowered_key for marker in ("path", "file", "target")):
            continue
        for leaf in string_leaves(value):
            normalized = leaf.lower().replace("\\", "/").lstrip("./")
            if normalized == expected or normalized.endswith("/" + expected):
                return True
    return False


def tool_result(event: dict[str, Any]) -> str:
    return "\n".join(string_leaves(event.get("tool_result")))


def tool_id(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return ""
    return str(call.get("tool_call_id", call.get("id", "")))


def json_objects(text: str) -> list[dict[str, Any]]:
    """Extract complete JSON objects, including JSON embedded in normalized result wrappers."""
    decoder = json.JSONDecoder()
    objects: list[dict[str, Any]] = []
    pending = [text]
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        offset = 0
        while offset < len(current):
            start = current.find("{", offset)
            if start < 0:
                break
            try:
                value, consumed = decoder.raw_decode(current[start:])
            except json.JSONDecodeError:
                offset = start + 1
                continue
            if isinstance(value, dict):
                objects.append(value)
                pending.extend(string_leaves(value))
            offset = start + max(consumed, 1)
    return objects


def path_in_arguments(arguments: str, relative: str) -> bool:
    lowered = arguments.lower()
    return relative.lower() in lowered or ("/workspace/" + relative.lower()) in lowered


def is_report_mutation(event: dict[str, Any]) -> bool:
    if event.get("event_type") != "tool":
        return False
    name = tool_name(event).lower()
    arguments = tool_arguments(event)
    lowered = arguments.lower()
    if any(marker in name for marker in ("write", "patch", "edit", "notebook")):
        return explicit_path_argument(event, REPORT_PATH) or bool(
            re.search(r"(?:update|add|delete) file:\s*[^\n]*output/module_report\.json", lowered)
        )
    if not path_in_arguments(arguments, REPORT_PATH):
        return False
    mutation_markers = (
        "> output/module_report.json",
        "> /workspace/output/module_report.json",
        "tee output/module_report.json",
        "tee /workspace/output/module_report.json",
        "write_text(",
        "json.dump(",
        "sed -i",
        "open(",
        "touch ",
        "mv ",
        "cp ",
    )
    return any(marker in lowered for marker in mutation_markers)


def is_any_mutation(event: dict[str, Any], relative: str) -> bool:
    if event.get("event_type") != "tool":
        return False
    arguments = tool_arguments(event)
    name = tool_name(event).lower()
    lowered = arguments.lower()
    if any(marker in name for marker in ("write", "patch", "edit", "notebook")):
        return explicit_path_argument(event, relative) or bool(
            re.search(rf"(?:update|add|delete) file:\s*[^\n]*{re.escape(relative.lower())}", lowered)
        )
    if not path_in_arguments(arguments, relative):
        return False
    escaped = re.escape(relative.lower())
    target = rf"(?:/workspace/)?{escaped}"
    return bool(
        re.search(rf"(?:>|>>)\s*{target}(?:\s|$)", lowered)
        or re.search(rf"\btee\s+(?:-[a-z]+\s+)*{target}(?:\s|$)", lowered)
        or re.search(rf"\bsed\s+-i\b[^\n;&|]*{target}", lowered)
        or re.search(rf"\b(?:touch|rm)\s+[^\n;&|]*{target}", lowered)
        or re.search(rf"\b(?:mv|cp)\s+[^\n;&|]*{target}(?:\s|$)", lowered)
        or re.search(rf"['\"]{target}['\"][^\n]{{0,100}}\.write_text\(", lowered)
        or re.search(rf"open\(\s*['\"]{target}['\"][^\n]{{0,100}}['\"](?:w|a|x)", lowered)
    )


def write_report(workspace: Path, rule_id: str, payload: dict[str, Any]) -> None:
    root = workspace / ".harnessbench_verifier"
    root.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": SCHEMA, "rule_id": rule_id, **payload}
    (root / f"{rule_id}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def rule(rule_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": rule_id, "score": int(bool(passed)), "reason": reason}


def notes_checks(text: str) -> tuple[bool, dict[str, Any]]:
    lowered = text.lower()
    plain = re.sub(r"[`*_#]", "", lowered)
    lines = [" ".join(line.split()).lower() for line in text.splitlines() if line.strip()]
    count_ok = bool(
        re.search(r"\b3\s+(?:explicit\s+)?revisions?\b", plain)
        or re.search(r"\brevisions?(?:\s+count)?\s*[:=]?\s*(?:revised\s*)?3\b", plain)
        or re.search(r"\brevisions?\s*\(\s*3\s*\)", plain)
        or re.search(r"\bcount\s*[:=]\s*3\b", plain)
        or re.search(r"\brevised\s+3\s+times?\b", plain)
    )
    revision_zero_absent = not re.search(r"\brevision\s*0\b", lowered)

    scope_candidates = [
        index
        for index, line in enumerate(lines)
        if "released" in line
        and ("only" in line or ("blocked" in line and any(word in line for word in ("remove", "exclude", "narrow"))))
    ]
    add_candidates = [
        index
        for index, line in enumerate(lines)
        if "reviewer" in line and any(word in line for word in ("add", "added", "include"))
    ]
    replace_candidates = [
        index
        for index, line in enumerate(lines)
        if "reviewer" in line
        and "released_at" in line
        and any(word in line for word in ("revoke", "remove", "replace", "supersed"))
    ]
    ordered_triples = [
        (scope_index, add_index, replace_index)
        for scope_index in scope_candidates
        for add_index in add_candidates
        for replace_index in replace_candidates
        if scope_index < add_index < replace_index
    ]
    ordered = bool(ordered_triples)
    chosen_indexes = list(ordered_triples[0]) if ordered_triples else [-1, -1, -1]
    final_scope_ok = "final scope" in lowered and "released" in lowered and "only" in lowered
    final_fields_ok = any(
        all(field in line for field in ("module", "loc_count", "released_at"))
        and ("final" in line or "required fields" in line or "fields per entry" in line)
        for line in lines
    )
    if not final_fields_ok:
        for index, line in enumerate(lines):
            if "final field" not in line:
                continue
            field_block = " ".join(lines[index : index + 6])
            if all(field in field_block for field in ("module", "loc_count", "released_at")):
                final_fields_ok = True
                break
    revoked_clear = "reviewer" in lowered and any(
        word in lowered for word in ("revoked", "removed", "superseded", "not final", "excluded")
    )
    passed = all((count_ok, revision_zero_absent, ordered, final_scope_ok, final_fields_ok, revoked_clear))
    return passed, {
        "count_is_exactly_three": count_ok,
        "revision_zero_absent": revision_zero_absent,
        "revision_line_indexes": chosen_indexes,
        "revision_order_correct": ordered,
        "final_scope_explicit": final_scope_ok,
        "final_fields_explicit": final_fields_ok,
        "reviewer_explicitly_nonfinal": revoked_clear,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()

    workspace = args.workspace
    verifier_dir = Path(__file__).resolve().parent
    truth = load_json(verifier_dir / "truth.json", {})
    released = set(truth.get("released", []))
    blocked = set(truth.get("blocked", []))
    expected_fields = set(truth.get("final_fields", []))
    expected_loc_counts = truth.get("loc_count", {})
    expected_loc_counts = expected_loc_counts if isinstance(expected_loc_counts, dict) else {}

    final_report = load_json(workspace / REPORT_PATH, None)
    report = final_report if isinstance(final_report, dict) else {}
    notes_path = workspace / NOTES_PATH
    try:
        notes_text = notes_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        notes_text = ""
    issued = load_json(workspace / "state" / ".issued_details.json", {})
    issued = issued if isinstance(issued, dict) else {}

    run_dir_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_dir_value) if run_dir_value else Path("/__missing_run_dir__")
    events, summary, events_path = primary_trace(run_dir) if run_dir_value else ([], {}, None)
    tools = [event for event in events if event.get("event_type") == "tool"]

    # Bind approved modctl fetch calls to the normalized result from the same tool event.
    fetch_results: dict[str, list[dict[str, Any]]] = {}
    modctl_event_count = 0
    hidden_state_accesses: list[dict[str, Any]] = []
    implementation_reads: list[dict[str, Any]] = []
    source_mutations: list[dict[str, Any]] = []
    network_tools: list[dict[str, Any]] = []
    for event in tools:
        name = tool_name(event)
        arguments = tool_arguments(event)
        command_text = f"{name}\n{arguments}"
        lowered = arguments.lower()
        if ".issued_details" in lowered or "state/.issued" in lowered:
            hidden_state_accesses.append({"seq": event.get("seq"), "tool": name, "arguments": arguments[:2000]})
        impl_path = path_in_arguments(arguments, "bin/modctl")
        read_like_tool = any(marker in name.lower() for marker in ("read", "fetch", "view"))
        read_like_shell = bool(
            re.search(r"(?:^|[;&|\s])(?:cat|sed|head|tail|less|more|strings|xxd|od|cp)\s+[^\n;&|]*bin/modctl\b", lowered)
        )
        if impl_path and (read_like_tool or read_like_shell):
            implementation_reads.append({"seq": event.get("seq"), "tool": name, "arguments": arguments[:2000]})
        if is_any_mutation(event, "data/modules.json"):
            source_mutations.append({"seq": event.get("seq"), "tool": name, "arguments": arguments[:2000]})
        if any(marker in name.lower() for marker in ("web", "browser")) or re.search(
            r"(?:^|[;&|\s])(?:curl|wget)\s", lowered
        ):
            network_tools.append({"seq": event.get("seq"), "tool": name, "arguments": arguments[:2000]})
        if not MODCTL_COMMAND.search(command_text):
            continue
        modctl_event_count += 1
        if not MODCTL_FETCH.search(command_text):
            continue
        for row in json_objects(tool_result(event)):
            module_id = row.get("module")
            if not isinstance(module_id, str) or module_id not in released | blocked:
                continue
            fetch_results.setdefault(module_id, []).append(
                {
                    "seq": event.get("seq"),
                    "turn": event.get("conversation_turn"),
                    "tool_call_id": tool_id(event),
                    "row": row,
                }
            )

    # Agent: split latest-goal artifact checks for direct failure attribution.
    final_scope_ok = isinstance(final_report, dict) and set(report) == released
    output_dir = workspace / "output"
    output_files = (
        sorted(path.name for path in output_dir.iterdir() if path.is_file() or path.is_symlink())
        if output_dir.is_dir() and not output_dir.is_symlink()
        else []
    )
    final_schema_ok = (
        output_files == ["module_report.json", "scope_notes.md"]
        and isinstance(final_report, dict)
        and all(
            isinstance(report.get(module_id), dict)
            and set(report[module_id]) == expected_fields
            and report[module_id].get("module") == module_id
            and "reviewer" not in report[module_id]
            for module_id in released
        )
    )

    exact_rows = 0
    row_evidence: dict[str, Any] = {}
    final_values_ok = final_scope_ok
    for module_id in sorted(released):
        row = report.get(module_id)
        row = row if isinstance(row, dict) else {}
        authority = issued.get(module_id)
        authority = authority if isinstance(authority, dict) else {}
        row_ok = (
            row.get("module") == module_id
            and row.get("loc_count") == expected_loc_counts.get(module_id)
            and isinstance(authority.get("released_at"), str)
            and bool(authority.get("released_at"))
            and row.get("released_at") == authority.get("released_at")
        )
        exact_rows += int(row_ok)
        final_values_ok = final_values_ok and row_ok
        row_evidence[module_id] = {
            "actual_loc_count": row.get("loc_count"),
            "expected_loc_count": expected_loc_counts.get(module_id),
            "actual_released_at": row.get("released_at"),
            "issued_released_at": authority.get("released_at"),
            "passed": row_ok,
        }
    final_values_ok = final_values_ok and not hidden_state_accesses

    mutation_evidence: dict[str, list[dict[str, Any]]] = {}
    for turn in CRITICAL_REVISION_TURNS:
        matches = [event for event in tools if event.get("conversation_turn") == turn and is_report_mutation(event)]
        mutation_evidence[str(turn)] = [
            {"seq": event.get("seq"), "tool": tool_name(event), "tool_call_id": tool_id(event)}
            for event in matches
        ]
    revisions_in_turn_ok = all(mutation_evidence[str(turn)] for turn in CRITICAL_REVISION_TURNS)

    turn_six_messages = [
        compact_text(event)
        for event in events
        if event.get("event_type") == "assistant_message" and event.get("conversation_turn") == 6
    ]
    distractor_answer_ok = any("mod-04" in text and "984379" in text for text in turn_six_messages)
    distractor_mutations = [
        event for event in tools if event.get("conversation_turn") == 6 and is_report_mutation(event)
    ]
    distractor_recovery_ok = distractor_answer_ok and not distractor_mutations

    scope_notes_ok, scope_notes_details = notes_checks(notes_text)
    boundary_ok = not (hidden_state_accesses or implementation_reads or source_mutations or network_tools)

    agent_rules = [
        rule(
            "ar_final_scope_is_latest_released_only",
            final_scope_ok,
            f"final module keys={len(report)}; expected released keys={len(released)}; leaked blocked={sorted(set(report) & blocked)}",
        ),
        rule(
            "ar_final_schema_exact_and_reviewer_absent",
            final_schema_ok,
            f"output files={output_files}; rows with exact final schema={sum(isinstance(report.get(m), dict) and set(report[m]) == expected_fields for m in released)}/{len(released)}",
        ),
        rule(
            "ar_final_values_exact_and_authoritative",
            final_values_ok,
            f"exact authoritative rows={exact_rows}/{len(released)}; hidden-state accesses={len(hidden_state_accesses)}",
        ),
        rule(
            "ar_each_revision_mutates_report_in_its_turn",
            revisions_in_turn_ok,
            "report mutation counts at turns 3/5/7=" + "/".join(str(len(mutation_evidence[str(t)])) for t in CRITICAL_REVISION_TURNS),
        ),
        rule(
            "ar_distractor_answered_without_goal_regression",
            distractor_recovery_ok,
            f"exact turn-6 answer={distractor_answer_ok}; turn-6 report mutations={len(distractor_mutations)}",
        ),
        rule(
            "ar_scope_notes_encode_exact_revision_history",
            scope_notes_ok,
            f"notes checks={scope_notes_details}",
        ),
        rule(
            "ar_respects_authority_and_workspace_boundaries",
            boundary_ok,
            f"hidden-state={len(hidden_state_accesses)}; implementation reads={len(implementation_reads)}; source mutations={len(source_mutations)}; network tools={len(network_tools)}",
        ),
    ]

    # Harness: complete trace, persistent context, model-visible goal capability, and bound results.
    started: dict[int, str] = {}
    finished: set[int] = set()
    for event in events:
        if event.get("event_type") == "conversation_turn_started":
            index = event.get("turn_index")
            value = event.get("input")
            if isinstance(index, int) and isinstance(value, str):
                started.setdefault(index, value)
        elif event.get("event_type") == "conversation_turn_finished":
            index = event.get("turn_index")
            if isinstance(index, int):
                finished.add(index)
    exact_turn_count = sum(
        isinstance(started.get(index), str)
        and hashlib.sha256(started[index].encode("utf-8")).hexdigest() == EXPECTED_TURN_SHA256[index - 1]
        for index in range(1, 11)
    )
    exact_turns = exact_turn_count == 10
    lifecycle_ok = (
        bool(events)
        and events[0].get("event_type") == "conversation_started"
        and events[-1].get("event_type") == "run_finished"
        and summary.get("trace_complete") is True
        and exact_turns
        and finished == set(range(1, 11))
    )

    conversation = summary.get("conversation") if isinstance(summary.get("conversation"), dict) else {}
    native_ids = conversation.get("native_session_ids", [])
    continuity_ok = (
        conversation.get("turns_completed") == 10
        and isinstance(native_ids, list)
        and len(native_ids) == 1
        and isinstance(native_ids[0], str)
        and bool(native_ids[0])
        and conversation.get("new_session_before_turns") == []
    )

    inventory_names, complete_inventory_requests, inventory_paths = provider_inventory(run_dir)
    visible_goal_tools = sorted(inventory_names & GOAL_PLAN_TOOLS)
    goal_capability_ok = complete_inventory_requests > 0 and bool(visible_goal_tools)

    readable_revision_turns: list[int] = []
    revision_texts = {turn: compact_text(prompt) for turn, prompt in REVISION_PROMPTS.items()}
    for turn in CRITICAL_REVISION_TURNS:
        if any(
            revision_texts[turn] in compact_text(event.get("messages"))
            for event in events
            if event.get("event_type") == "model_input" and event.get("conversation_turn") == turn
        ):
            readable_revision_turns.append(turn)
    revision_delivery_ok = readable_revision_turns == list(CRITICAL_REVISION_TURNS)

    ordered_history_event_seqs: list[Any] = []
    for event in events:
        if event.get("event_type") != "model_input" or event.get("conversation_turn") != 7:
            continue
        text = compact_text(event.get("messages"))
        positions = [text.find(revision_texts[turn]) for turn in CRITICAL_REVISION_TURNS]
        if 0 <= positions[0] < positions[1] < positions[2]:
            ordered_history_event_seqs.append(event.get("seq"))
    ordered_goal_context_ok = bool(ordered_history_event_seqs)

    visible_modules: set[str] = set()
    for module_id in released:
        authority = issued.get(module_id)
        authority = authority if isinstance(authority, dict) else {}
        for item in fetch_results.get(module_id, []):
            row = item.get("row")
            if not isinstance(row, dict):
                continue
            if (
                row.get("module") == module_id
                and row.get("released_at") == authority.get("released_at")
                and row.get("reviewer") == authority.get("reviewer")
                and isinstance(authority.get("released_at"), str)
                and isinstance(authority.get("reviewer"), str)
            ):
                visible_modules.add(module_id)
                break
    controlled_results_ok = visible_modules == released

    harness_rules = [
        rule(
            "hr_trace_lifecycle_and_all_turns_complete",
            lifecycle_ok,
            f"events={len(events)}; trace_complete={summary.get('trace_complete')}; exact configured turns={exact_turn_count}/10; finished={len(finished)}/10; trace={events_path}",
        ),
        rule(
            "hr_one_native_session_carries_all_revisions",
            continuity_ok,
            f"turns_completed={conversation.get('turns_completed')}; native_session_ids={native_ids}; boundaries={conversation.get('new_session_before_turns')}",
        ),
        rule(
            "hr_goal_or_plan_capability_visible_to_model",
            goal_capability_ok,
            f"complete model-visible inventories={complete_inventory_requests}; recognized goal/plan tools={visible_goal_tools}; evidence paths={inventory_paths}",
        ),
        rule(
            "hr_each_revision_input_readable_by_model",
            revision_delivery_ok,
            f"revision turns with exact readable user input in normalized model_input={readable_revision_turns}",
        ),
        rule(
            "hr_ordered_goal_history_visible_at_revocation",
            ordered_goal_context_ok,
            f"turn-7 model_input events containing turns 3, 5, and 7 in order={ordered_history_event_seqs}",
        ),
        rule(
            "hr_all_controlled_values_in_bound_modctl_results",
            controlled_results_ok,
            f"released modules with exact issued released_at and reviewer in bound modctl fetch results={len(visible_modules)}/{len(released)}; modctl events={modctl_event_count}",
        ),
    ]

    diagnostic_payloads = {
        "ar_final_scope_is_latest_released_only": {"passed": final_scope_ok, "actual": sorted(report), "expected": sorted(released)},
        "ar_final_schema_exact_and_reviewer_absent": {"passed": final_schema_ok, "output_files": output_files},
        "ar_final_values_exact_and_authoritative": {"passed": final_values_ok, "rows": row_evidence, "hidden_state_accesses": hidden_state_accesses},
        "ar_each_revision_mutates_report_in_its_turn": {"passed": revisions_in_turn_ok, "mutations": mutation_evidence},
        "ar_distractor_answered_without_goal_regression": {"passed": distractor_recovery_ok, "answer": distractor_answer_ok, "mutation_count": len(distractor_mutations)},
        "ar_scope_notes_encode_exact_revision_history": {"passed": scope_notes_ok, **scope_notes_details},
        "ar_respects_authority_and_workspace_boundaries": {"passed": boundary_ok, "hidden_state": hidden_state_accesses, "implementation_reads": implementation_reads, "source_mutations": source_mutations, "network_tools": network_tools},
        "hr_trace_lifecycle_and_all_turns_complete": {"passed": lifecycle_ok, "events_path": str(events_path) if events_path else None},
        "hr_one_native_session_carries_all_revisions": {"passed": continuity_ok, "conversation": conversation},
        "hr_goal_or_plan_capability_visible_to_model": {"passed": goal_capability_ok, "tools": visible_goal_tools, "complete_requests": complete_inventory_requests},
        "hr_each_revision_input_readable_by_model": {"passed": revision_delivery_ok, "turns": readable_revision_turns},
        "hr_ordered_goal_history_visible_at_revocation": {"passed": ordered_goal_context_ok, "event_seqs": ordered_history_event_seqs},
        "hr_all_controlled_values_in_bound_modctl_results": {"passed": controlled_results_ok, "visible_modules": sorted(visible_modules), "fetch_results": fetch_results},
    }
    for rule_id, payload in diagnostic_payloads.items():
        write_report(workspace, rule_id, payload)

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
