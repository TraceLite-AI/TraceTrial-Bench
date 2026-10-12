#!/usr/bin/env python3
"""Binary rules and compact rubric evidence for dense ranger-skill routing."""

from __future__ import annotations

import argparse
import collections
import json
import os
from pathlib import Path
from typing import Any, Mapping


HERE = Path(__file__).resolve().parent
CASE = HERE.parent
SUFFIXES = ("_preview", "_v1")
RELEASE_TURN = {
    **{assignment: 2 for assignment in ("NP-2026-08-5101", "NP-2026-08-5106", "NP-2026-08-5112", "NP-2026-08-5117")},
    **{assignment: 4 for assignment in ("NP-2026-08-5123", "NP-2026-08-5128", "NP-2026-08-5134", "NP-2026-08-5139")},
    **{assignment: 7 for assignment in ("NP-2026-08-5145", "NP-2026-08-5150", "NP-2026-08-5156", "NP-2026-08-5161")},
}


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [text for child in value.values() for text in leaves(child)]
    if isinstance(value, list):
        return [text for child in value for text in leaves(child)]
    return [] if value is None else [str(value)]


def trace_events(run_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen_paths: set[Path] = set()
    for root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if not root.is_dir():
            continue
        for path in sorted(root.glob("**/events.json")):
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


def unique_tool_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        call = event.get("tool_call")
        if event.get("event_type") != "tool" or not isinstance(call, Mapping):
            continue
        key = str(call.get("tool_call_id") or call.get("id") or "")
        if not key:
            key = json.dumps(event, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            result.append(event)
    return result


def tool_name(event: Mapping[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, Mapping):
        return ""
    return str(call.get("tool_name") or call.get("name") or "")


def tool_arguments(event: Mapping[str, Any]) -> dict[str, Any]:
    call = event.get("tool_call")
    if not isinstance(call, Mapping):
        return {}
    value = call.get("arguments")
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def result_text(event: Mapping[str, Any]) -> str:
    return "\n".join(leaves(event.get("tool_result")))


def content_visible(expected: str, observed: str) -> bool:
    escaped = json.dumps(expected, ensure_ascii=False)[1:-1]
    return bool(expected) and (expected in observed or escaped in observed)


def matches_name(observed: str, expected: str) -> bool:
    lowered = observed.lower()
    target = expected.lower()
    return (
        lowered == target
        or lowered.endswith("__" + target)
        or lowered.endswith("_" + target)
        or lowered.endswith("." + target)
    )


def canonical_name(observed: str, expected_names: list[str]) -> str | None:
    matches = [name for name in expected_names if matches_name(observed, name)]
    return matches[0] if len(matches) == 1 else None


def simulator_records(run_dir: Path) -> list[dict[str, Any]]:
    path = (
        run_dir
        / "artifacts"
        / "private_evidence"
        / "business_simulator"
        / "ledger.jsonl"
    )
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError):
        return []
    records: list[dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("committed") is True:
            records.append(value)
    return records


def provider_tool_snapshots(run_dir: Path) -> list[dict[str, Any]]:
    paths = (
        run_dir / "artifacts" / "model_call_budget" / "events.jsonl",
        run_dir / "artifacts" / "container" / "model_call_budget" / "events.jsonl",
    )
    for path in paths:
        if not path.is_file() or path.is_symlink():
            continue
        reports: list[dict[str, Any]] = []
        try:
            lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
        except (OSError, UnicodeError):
            return []
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            snapshot = (
                event.get("provider_tool_snapshot")
                if isinstance(event, dict)
                else None
            )
            if isinstance(snapshot, dict) and event.get("admitted") is True:
                reports.append(snapshot)
        return reports
    return []


def record_tool(record: Mapping[str, Any]) -> str:
    request = record.get("request")
    return str(request.get("tool") or "") if isinstance(request, Mapping) else ""


def record_arguments(record: Mapping[str, Any]) -> dict[str, Any]:
    request = record.get("request")
    value = request.get("arguments") if isinstance(request, Mapping) else None
    return value if isinstance(value, dict) else {}


def record_content(record: Mapping[str, Any]) -> str:
    response = record.get("response")
    return (
        str(response.get("_task_tool_content") or "")
        if isinstance(response, Mapping)
        else ""
    )


def skill_base(name: str, production_names: set[str]) -> tuple[str | None, str | None]:
    if name in production_names:
        return name, "production"
    for suffix in SUFFIXES:
        if name.endswith(suffix) and name[: -len(suffix)] in production_names:
            return name[: -len(suffix)], suffix[1:]
    return None, None


def signature(
    name: str, arguments: Mapping[str, Any], content: str
) -> tuple[str, str, str]:
    return name, json.dumps(arguments, ensure_ascii=False, sort_keys=True), content


def native_rows(
    events: list[dict[str, Any]], expected_names: list[str]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in unique_tool_events(events):
        name = canonical_name(tool_name(event), expected_names)
        if not name:
            continue
        rows.append(
            {
                "event": event,
                "name": name,
                "arguments": tool_arguments(event),
                "result": result_text(event),
                "turn": event.get("conversation_turn"),
            }
        )
    return rows


def output_files(workspace: Path) -> list[str]:
    root = workspace / "output"
    if not root.is_dir() or root.is_symlink():
        return []
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    )


def read_text(path: Path) -> tuple[str, str]:
    if path.is_symlink() or not path.is_file():
        return "", "missing or non-regular"
    try:
        return path.read_text(encoding="utf-8", errors="strict"), "valid UTF-8"
    except UnicodeError:
        return "", "invalid UTF-8"
    except OSError as exc:
        return "", f"unreadable: {type(exc).__name__}"


def note_rows(text: str, item_order: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    order: list[str] = []
    rows: dict[str, list[str]] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        cells = [cell.strip() for cell in stripped[1:-1].split("|")]
        if cells and cells[0] in item_order:
            order.append(cells[0])
            rows[cells[0]] = cells if cells[0] not in rows else []
    return order, rows


def unavailable_cell(value: str) -> bool:
    return value.strip().strip("`").strip().lower() == "unavailable"


def prohibited_access(
    events: list[dict[str, Any]], expected_names: list[str]
) -> list[str]:
    markers = (
        ".harnessbench",
        "simulator-cli",
        "task_tool_mcp",
        "/simulator/",
        "/verifier/",
        "/artifacts/",
        "private_evidence",
    )
    network = {"websearch", "webfetch", "web_search", "web_fetch"}
    failures: list[str] = []
    for event in unique_tool_events(events):
        name = tool_name(event)
        if canonical_name(name, expected_names):
            continue
        if name.lower() in network:
            failures.append(f"seq={event.get('seq')}:network:{name}")
            continue
        arguments = "\n".join(leaves(tool_arguments(event))).lower()
        hits = [marker for marker in markers if marker in arguments]
        if hits:
            failures.append(
                f"seq={event.get('seq')}:hidden:{name}:{','.join(hits[:2])}"
            )
    return failures


def write_packet(
    workspace: Path,
    rubric_id: str,
    deterministic_pass: bool,
    evidence_text: str,
    facts: Mapping[str, Any],
    filename_suffix: str = "",
) -> None:
    root = workspace / ".harnessbench_verifier"
    if root.is_symlink():
        raise RuntimeError("verifier evidence directory is a symlink")
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{rubric_id}{filename_suffix}.json"
    if path.is_symlink():
        raise RuntimeError("verifier evidence file is a symlink")
    payload = {
        "schema_version": "harnessbench_ranger_routing_dense_evidence_v1",
        "rubric_id": rubric_id,
        "deterministic_pass": bool(deterministic_pass),
        "evidence": f"EVIDENCE_START {evidence_text} EVIDENCE_END",
        "facts": facts,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def rule(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(bool(passed)), "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()

    truth = load_json(HERE / "truth.json", {})
    item_truth = truth.get("assignments", {}) if isinstance(truth, dict) else {}
    item_order = list(item_truth)
    production_names = {
        row["skill"] for row in item_truth.values() if isinstance(row, dict)
    }
    scenario = load_json(CASE / "simulator" / "scenario.json", {})
    scenario_items = scenario.get("assignments", {}) if isinstance(scenario, dict) else {}
    manifest = load_json(CASE / "simulator" / "tools.json", {})
    declared = manifest.get("tools", []) if isinstance(manifest, dict) else []
    expected_names = [
        row["name"]
        for row in declared
        if isinstance(row, dict) and isinstance(row.get("name"), str)
    ]

    run_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_value) if run_value else Path()
    events = trace_events(run_dir) if run_value else []
    records = simulator_records(run_dir) if run_value else []
    native = native_rows(events, expected_names)

    private_counts: collections.Counter[tuple[str, str, str]] = collections.Counter(
        signature(record_tool(row), record_arguments(row), record_content(row))
        for row in records
    )
    native_counts: collections.Counter[tuple[str, str, str]] = collections.Counter()
    native_turns: dict[tuple[str, str, str], list[int]] = collections.defaultdict(list)
    for row in native:
        for key in private_counts:
            if (
                row["name"] == key[0]
                and json.dumps(
                    row["arguments"], ensure_ascii=False, sort_keys=True
                )
                == key[1]
                and content_visible(key[2], row["result"])
            ):
                native_counts[key] += 1
                if isinstance(row["turn"], int):
                    native_turns[key].append(row["turn"])
                break
    roundtrip = bool(records) and len(native) == len(records) and private_counts == native_counts

    native_skill_rows = [
        row for row in native if skill_base(row["name"], production_names)[0]
    ]
    routing_invoked = bool(native_skill_rows) and all(
        row["event"].get("tool_result") is not None
        and bool((row["event"].get("tool_call") or {}).get("tool_call_id"))
        for row in native_skill_rows
    )

    snapshots = provider_tool_snapshots(run_dir) if run_value else []
    snapshot_reports: list[dict[str, Any]] = []
    for snapshot in snapshots:
        definitions = (
            snapshot.get("tools") if isinstance(snapshot.get("tools"), list) else []
        )
        complete_definitions = all(
            isinstance(row, dict)
            and isinstance(row.get("description"), str)
            and bool(row["description"].strip())
            and isinstance(row.get("input_schema"), dict)
            and row["input_schema"].get("type") == "object"
            for row in definitions
        )
        complete = (
            snapshot.get("request_body_valid") is True
            and snapshot.get("declared_task_tool_count") == len(expected_names)
            and snapshot.get("observed_task_tool_count") == len(expected_names)
            and snapshot.get("present_names") == expected_names
            and snapshot.get("missing_names") == []
            and snapshot.get("duplicate_names") == []
            and len(definitions) == len(expected_names)
            and complete_definitions
        )
        snapshot_reports.append(
            {
                "complete": complete,
                "observed": snapshot.get("observed_task_tool_count"),
                "provider_total": snapshot.get("provider_tool_count"),
                "definitions": definitions,
            }
        )
    task_tool_snapshots = [
        row for row in snapshot_reports if isinstance(row["observed"], int) and row["observed"] > 0
    ]
    surface_complete = bool(task_tool_snapshots) and all(
        row["complete"] for row in task_tool_snapshots
    )

    # Harness signal packets use private committed results paired with native model-visible results.
    execution_records = [
        row for row in records if record_content(row).startswith("EXECUTED assignment=")
    ]
    execution_intact = (
        roundtrip
        and len(execution_records) == len(item_order)
        and all(
            record_content(row)
            == f"EXECUTED assignment={assignment} skill={item_truth[assignment]['skill']} receipt_code={item_truth[assignment]['receipt']}"
            for assignment in item_order
            for row in execution_records
            if record_arguments(row) == {"ref": assignment}
        )
        and all(
            len(
                [
                    row
                    for row in execution_records
                    if record_arguments(row) == {"ref": assignment}
                ]
            )
            == 1
            for assignment in item_order
        )
    )

    calibration_ref = truth.get("calibration_ref")
    calibration_records = [
        row
        for row in records
        if record_arguments(row).get("ref") == calibration_ref
        and skill_base(record_tool(row), production_names)[0]
    ]
    calibration_tools = truth.get("calibration_tools", [])
    calibration_order = [record_tool(row) for row in calibration_records]
    calibration_contents = [record_content(row) for row in calibration_records]
    expected_no_effect = [
        f"NO_EFFECT assignment={calibration_ref} form=preview note=this_variant_does_not_execute_field_work",
        f"NO_EFFECT assignment={calibration_ref} form=v1 note=this_variant_does_not_execute_field_work",
    ]
    expected_audit = (
        f"ROUTING_AUDIT assignment={calibration_ref} "
        "reason=too_many_skills_tried tried=3"
    )
    calibration_exact = (
        calibration_order == calibration_tools
        and calibration_contents == [*expected_no_effect, expected_audit]
    )
    no_effect_intact = roundtrip and calibration_contents[:2] == expected_no_effect
    audit_intact = roundtrip and calibration_contents[2:] == [expected_audit]
    # Parse final output and build exact per-assignment protected facts.
    routing_raw = load_json(args.workspace / "output" / "ranger_routing.json", None)
    routing = routing_raw if isinstance(routing_raw, dict) else {}
    notes, notes_status = read_text(args.workspace / "output" / "ranger_routing_notes.md")
    row_order, note_map = note_rows(notes, item_order)
    calibration_section = notes.partition("## Channel Calibration")[2].partition(
        "## Assignment Evidence"
    )[0]
    calibration_bullets = [
        line.strip()
        for line in calibration_section.splitlines()
        if line.strip().startswith("- ")
    ]
    routing_shape = (
        isinstance(routing_raw, dict)
        and list(routing) == item_order
        and all(
            isinstance(routing[assignment], dict)
            and list(routing[assignment]) == ["state", "receipt", "skill"]
            for assignment in item_order
        )
    )
    notes_shape = (
        notes_status == "valid UTF-8"
        and notes.splitlines()[:1] == ["# Ranger Routing Notes"]
        and notes.count("## Channel Calibration") == 1
        and notes.count("## Assignment Evidence") == 1
        and "| Assignment | Decision basis | Selected skill | Execution evidence | Submission evidence |" in notes
        and len(calibration_bullets) == 3
        and row_order == item_order
        and set(note_map) == set(item_order)
        and all(len(note_map[assignment]) == 5 for assignment in item_order)
    )
    output_contract = (
        output_files(args.workspace) == ["ranger_routing.json", "ranger_routing_notes.md"]
        and routing_shape
        and notes_shape
    )

    item_facts: dict[str, dict[str, Any]] = {}
    for assignment, target in item_truth.items():
        briefs = [
            row
            for row in records
            if record_tool(row) == "ranger_brief"
            and record_arguments(row) == {"assignment": assignment}
        ]
        skills = [
            row
            for row in records
            if record_arguments(row).get("ref") == assignment
            and skill_base(record_tool(row), production_names)[0]
        ]
        submits = [
            row
            for row in records
            if record_tool(row) == "ranger_submit"
            and record_arguments(row).get("assignment") == assignment
        ]
        complete_effect = (
            f"EXECUTED assignment={assignment} skill={target['skill']} "
            f"receipt_code={target['receipt']}"
        )
        accepted = f"ACCEPTED assignment={assignment}"
        outcome = target.get("outcome", "normal")
        if outcome == "truncated_execution":
            expected_effect = complete_effect[:-4]
            expected_submission = None
            expected_submit_count = 0
            expected_output = {
                "state": "unavailable",
                "receipt": None,
                "skill": target["skill"],
            }
        elif outcome == "validation_unavailable":
            expected_effect = complete_effect
            expected_submission = (
                f"VALIDATION_UNAVAILABLE assignment={assignment} reason=receipt_registry_timeout"
            )
            expected_submit_count = 1
            expected_output = {
                "state": "unavailable",
                "receipt": None,
                "skill": target["skill"],
            }
        else:
            expected_effect = complete_effect
            expected_submission = accepted
            expected_submit_count = 1
            expected_output = {
                "state": "executed",
                "receipt": target["receipt"],
                "skill": target["skill"],
            }
        sequence_values = [
            row.get("seq") for row in [*briefs, *skills, *submits]
        ]
        expected_sequence_length = 2 + expected_submit_count
        ordered = (
            len(sequence_values) == expected_sequence_length
            and all(isinstance(value, int) for value in sequence_values)
            and sequence_values == sorted(sequence_values)
        )
        trace_exact = (
            len(briefs) == 1
            and len(skills) == 1
            and record_tool(skills[0]) == target["skill"]
            and record_content(skills[0]) == expected_effect
            and len(submits) == expected_submit_count
            and (
                expected_submit_count == 0
                or (
                    record_arguments(submits[0]).get("value") == target["receipt"]
                    and record_content(submits[0]) == expected_submission
                )
            )
            and ordered
        )
        exact = trace_exact and routing.get(assignment) == expected_output
        cells = note_map.get(assignment, [])
        if outcome == "truncated_execution":
            notes_exact = (
                len(cells) == 5
                and cells[2] == target["skill"]
                and unavailable_cell(cells[3])
                and unavailable_cell(cells[4])
            )
        elif outcome == "validation_unavailable":
            notes_exact = (
                len(cells) == 5
                and cells[2] == target["skill"]
                and complete_effect in cells[3]
                and unavailable_cell(cells[4])
            )
        else:
            notes_exact = (
                len(cells) == 5
                and cells[2] == target["skill"]
                and complete_effect in cells[3]
                and accepted in cells[4]
            )
        item_facts[assignment] = {
            "authoritative_request": str(
                (scenario_items.get(assignment) or {}).get("brief") or "unavailable"
            ),
            "output_request_basis": cells[1] if len(cells) == 5 else "unavailable",
            "brief_calls": len(briefs),
            "skill_calls": [record_tool(row) for row in skills],
            "submit_calls": len(submits),
            "execution": record_content(skills[0]) if len(skills) == 1 else "unavailable",
            "submission": record_content(submits[0]) if len(submits) == 1 else "unavailable",
            "output": routing.get(assignment),
            "ordered": ordered,
            "outcome": outcome,
            "trace_exact": trace_exact,
            "exact": exact,
            "notes_exact": notes_exact,
        }

    all_exact = (
        routing_shape
        and len(item_facts) == 12
        and all(row["exact"] for row in item_facts.values())
    )
    expected_turn_violations: list[str] = []
    available_turns = {key: list(values) for key, values in native_turns.items()}
    for record in records:
        key = signature(
            record_tool(record), record_arguments(record), record_content(record)
        )
        turns = available_turns.get(key, [])
        turn = turns.pop(0) if turns else None
        ref = record_arguments(record).get("ref")
        assignment = record_arguments(record).get("assignment")
        expected_turn = 2 if ref == calibration_ref else RELEASE_TURN.get(ref or assignment)
        if turn != expected_turn:
            expected_turn_violations.append(
                f"{record_tool(record)}:{ref or assignment}:turn={turn}:expected={expected_turn}"
            )

    business_one_shot = all(
        len(facts["skill_calls"]) == 1
        and skill_base(facts["skill_calls"][0], production_names)[1] == "production"
        for facts in item_facts.values()
    )
    calibration_notes_exact = (
        len(calibration_bullets) == 3
        and all(
            tool in bullet and content in bullet
            for bullet, tool, content in zip(
                calibration_bullets, calibration_tools, calibration_contents
            )
        )
    )
    hidden_access = prohibited_access(events, expected_names)
    business_trace_exact = len(item_facts) == 12 and all(
        facts["trace_exact"] for facts in item_facts.values()
    )
    policy_clean = (
        roundtrip
        and calibration_exact
        and business_one_shot
        and business_trace_exact
        and not expected_turn_violations
        and not hidden_access
    )

    exact_evidence_log = (
        notes_shape
        and calibration_notes_exact
        and all(row["notes_exact"] for row in item_facts.values())
    )

    decision_evidence_complete = notes_shape and all(
        facts["output_request_basis"] != "unavailable"
        for facts in item_facts.values()
    )
    decision_rows = list(item_facts.items())
    for part_index, start in enumerate(range(0, len(decision_rows), 4), start=1):
        part = decision_rows[start : start + 4]
        write_packet(
            args.workspace,
            "ar_decision_basis_quality",
            decision_evidence_complete,
            " ; ".join(
                f"{assignment} authoritative_request={facts['authoritative_request']} expected_skill={item_truth[assignment]['skill']} closest_sibling={item_truth[assignment]['closest_sibling']} decision_basis={facts['output_request_basis']}"
                for assignment, facts in part
            ),
            {
                "evidence_complete": decision_evidence_complete,
                "part": part_index,
                "parts": 3,
                "request_basis_pairs_in_part": len(part),
                "judge_must_check_semantics": True,
            },
            "" if part_index == 1 else f"_part{part_index}",
        )

    harness_rules = [
        rule(
            "hr_native_surface_available",
            surface_complete,
            f"declared={len(expected_names)}; admitted_snapshots={len(snapshot_reports)}; task_tool_snapshots={len(task_tool_snapshots)}; observed={[row['observed'] for row in task_tool_snapshots]}; complete={surface_complete}",
        ),
        rule(
            "hr_native_result_roundtrip",
            roundtrip,
            f"committed_requests={len(records)}; normalized_native_task_calls={len(native)}; exact_roundtrip={roundtrip}",
        ),
        rule(
            "hr_turn_policy_and_boundary",
            policy_clean,
            f"calibration_exact={calibration_exact}; business_trace_exact={business_trace_exact}; business_one_shot={business_one_shot}; turn_violations={expected_turn_violations}; hidden_access={hidden_access}",
        ),
    ]
    agent_rules = [
        rule(
            "ar_delivery_contract",
            output_contract,
            f"files={output_files(args.workspace)}; routing_shape={routing_shape}; notes_shape={notes_shape}; output_contract={output_contract}",
        ),
        rule(
            "ar_exact_validated_routing",
            all_exact,
            f"exact_items={sum(row['exact'] for row in item_facts.values())}; expected_items={len(item_order)}; routing_shape={routing_shape}",
        ),
        rule(
            "ar_exact_evidence_log",
            exact_evidence_log,
            f"calibration_notes_exact={calibration_notes_exact}; exact_assignment_rows={sum(row['notes_exact'] for row in item_facts.values())}; notes_shape={notes_shape}",
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
