#!/usr/bin/env python3
"""Deterministic evidence reconciliation for the dense release window tool surface."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping


HERE = Path(__file__).resolve().parent
CASE = HERE.parent
SUFFIXES = ("_draft", "_dry_run", "_legacy")
HR_RUBRICS = (
    "hr_effective_receipt_reaches_model",
    "hr_no_effect_reaches_model",
    "hr_audit_block_reaches_model",
    "hr_variant_names_distinguishable",
)
AR_RUBRICS = (
    "ar_all_12_covered_and_effective",
    "ar_action_and_receipt_exact",
    "ar_no_blind_trying_and_honest_state",
)


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
    seen_files: set[Path] = set()
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
            if resolved in seen_files:
                continue
            seen_files.add(resolved)
            payload = load_json(path, [])
            if isinstance(payload, list):
                events.extend(item for item in payload if isinstance(item, dict))
    return events


def unique_tool_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        if event.get("event_type") != "tool":
            continue
        call = event.get("tool_call")
        if not isinstance(call, dict):
            continue
        key = str(call.get("tool_call_id") or call.get("id") or "")
        if not key:
            key = json.dumps(event, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
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


def nested(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def normalize_tool(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, str):
        return {"name": raw.strip(), "description": "", "schema": None} if raw.strip() else None
    if not isinstance(raw, Mapping):
        return None
    core: Mapping[str, Any] = raw
    for key in ("function", "toolSpec", "tool_spec"):
        if isinstance(raw.get(key), Mapping):
            core = raw[key]
            break
    name = core.get("name") or core.get("tool_name") or raw.get("name") or raw.get("tool_name")
    if not isinstance(name, str) or not name.strip():
        return None
    description = core.get("description") or raw.get("description") or ""
    schema = None
    for key in ("parameters", "input_schema", "inputSchema", "schema"):
        if key in core:
            schema = core.get(key)
            break
        if key in raw:
            schema = raw.get(key)
            break
    return {
        "name": name.strip(),
        "description": description if isinstance(description, str) else str(description),
        "schema": schema,
    }


def tool_snapshots(events: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    snapshots: list[list[dict[str, Any]]] = []
    for event in events:
        if event.get("event_type") not in {"model_input", "model_request"}:
            continue
        candidates = (
            event.get("tools"),
            nested(event, "request", "tools"),
            nested(event, "request_body", "tools"),
            nested(event, "raw_event", "tools"),
            nested(event, "raw_event", "request", "tools"),
            nested(event, "raw_event", "request_body", "tools"),
            nested(event, "raw_event", "body", "tools"),
        )
        for candidate in candidates:
            if isinstance(candidate, list):
                normalized = [tool for raw in candidate if (tool := normalize_tool(raw))]
                if normalized:
                    snapshots.append(normalized)
    return snapshots


def simulator_records(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "artifacts" / "private_evidence" / "business_simulator" / "ledger.jsonl"
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
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


def record_tool(record: Mapping[str, Any]) -> str:
    request = record.get("request")
    return str(request.get("tool") or "") if isinstance(request, Mapping) else ""


def record_arguments(record: Mapping[str, Any]) -> dict[str, Any]:
    request = record.get("request")
    value = request.get("arguments") if isinstance(request, Mapping) else None
    return value if isinstance(value, dict) else {}


def record_response(record: Mapping[str, Any]) -> dict[str, Any]:
    response = record.get("response")
    return response if isinstance(response, dict) else {}


def record_content(record: Mapping[str, Any]) -> str:
    return str(record_response(record).get("_task_tool_content") or "")


def committed(record: Mapping[str, Any]) -> bool:
    return record.get("committed") is True


def action_base(name: str, action_names: set[str]) -> tuple[str | None, str | None]:
    if name in action_names:
        return name, "effective"
    for suffix in SUFFIXES:
        if name.endswith(suffix) and name[: -len(suffix)] in action_names:
            return name[: -len(suffix)], suffix[1:]
    return None, None


def matching_trace_events(
    record: Mapping[str, Any], events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    name = record_tool(record)
    arguments = record_arguments(record)
    return [
        event
        for event in events
        if matches_name(tool_name(event), name) and tool_arguments(event) == arguments
    ]


def signal_rows(
    records: list[dict[str, Any]], events: list[dict[str, Any]], marker: str
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        content = record_content(record)
        if marker not in content:
            continue
        matches = matching_trace_events(record, events)
        exact = [event for event in matches if content and content in result_text(event)]
        rows.append(
            {
                "private_sequence": record.get("seq"),
                "tool": record_tool(record),
                "arguments": record_arguments(record),
                "private_result": content,
                "native_call_result_matches": len(matches),
                "exact_model_visible_matches": len(exact),
                "delivered_exactly_once": len(matches) == 1 and len(exact) == 1,
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


def prohibited_access(events: list[dict[str, Any]], expected_names: list[str]) -> list[str]:
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
    for event in events:
        name = tool_name(event)
        if canonical_name(name, expected_names):
            continue
        if name.lower() in network:
            failures.append(f"seq={event.get('seq')}: prohibited network tool {name}")
            continue
        arguments = "\n".join(leaves(tool_arguments(event))).lower()
        hits = [marker for marker in markers if marker in arguments]
        if hits:
            failures.append(f"seq={event.get('seq')}: hidden path access via {name}: {hits[:2]}")
    return failures


def write_packet(workspace: Path, rubric_id: str, payload: dict[str, Any]) -> None:
    root = workspace / ".harnessbench_verifier"
    if root.is_symlink():
        raise RuntimeError("verifier evidence directory is a symlink")
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{rubric_id}.json"
    if path.is_symlink():
        raise RuntimeError("verifier evidence file is a symlink")
    path.write_text(
        json.dumps(
            {
                "schema_version": "harnessbench_monthend_dense_tools_evidence_v1",
                "rubric_id": rubric_id,
                **payload,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def rule(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(bool(passed)), "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()

    truth = load_json(HERE / "truth.json", {})
    item_truth = truth.get("items", {}) if isinstance(truth, dict) else {}
    item_order = list(item_truth)
    action_names = {row.get("action") for row in item_truth.values() if isinstance(row, dict)}
    manifest = load_json(CASE / "simulator" / "tools.json", {})
    declared = manifest.get("tools", []) if isinstance(manifest, dict) else []
    expected_names = [tool.get("name") for tool in declared if isinstance(tool, dict) and isinstance(tool.get("name"), str)]
    action_tool_names = [name for name in expected_names if action_base(name, action_names)[0] is not None]

    run_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_value) if run_value else Path()
    all_events = trace_events(run_dir) if run_value else []
    native_events = unique_tool_events(all_events)
    records = simulator_records(run_dir) if run_value else []

    snapshots = tool_snapshots(all_events)
    snapshot_reports: list[dict[str, Any]] = []
    for index, snapshot in enumerate(snapshots):
        mapped: dict[str, list[dict[str, Any]]] = {name: [] for name in expected_names}
        for definition in snapshot:
            canonical = canonical_name(definition["name"], expected_names)
            if canonical:
                mapped[canonical].append(definition)
        present = [name for name in expected_names if mapped[name]]
        if not present:
            continue
        duplicate = [name for name, values in mapped.items() if len(values) != 1 and values]
        incomplete = [
            name
            for name, values in mapped.items()
            if len(values) == 1
            and (
                not values[0].get("description", "").strip()
                or not isinstance(values[0].get("schema"), dict)
                or values[0]["schema"].get("type") != "object"
            )
        ]
        snapshot_reports.append(
            {
                "snapshot_index": index,
                "present_count": len(present),
                "present_names": present,
                "missing_names": [name for name in expected_names if name not in present],
                "duplicate_names": duplicate,
                "incomplete_definitions": incomplete,
                "complete": len(present) == len(expected_names) and not duplicate and not incomplete,
            }
        )
    toolface_complete = bool(snapshot_reports) and all(row["complete"] for row in snapshot_reports)

    action_events = [
        event
        for event in native_events
        if canonical_name(tool_name(event), action_tool_names) is not None
    ]
    harness_rules = [
        rule(
            "hr_action_tool_invoked",
            bool(action_events),
            f"native action calls={len(action_events)}",
        ),
        rule(
            "hr_toolface_fully_exposed",
            toolface_complete,
            (
                f"declared={len(expected_names)}; task snapshots={len(snapshot_reports)}; "
                f"coverage={[row['present_count'] for row in snapshot_reports]}; "
                f"all complete={toolface_complete}"
            ),
        ),
    ]

    effective_signals = signal_rows(records, native_events, "EFFECTIVE item=")
    no_effect_signals = signal_rows(records, native_events, "NO_EFFECT item=")
    audit_signals = signal_rows(records, native_events, "AUDIT_BLOCK item=")
    write_packet(
        args.workspace,
        HR_RUBRICS[0],
        {
            "issued_count": len(effective_signals),
            "expected_business_item_count": len(item_order),
            "all_delivered_exactly_once": bool(effective_signals)
            and all(row["delivered_exactly_once"] for row in effective_signals),
            "signals": effective_signals,
        },
    )
    write_packet(
        args.workspace,
        HR_RUBRICS[1],
        {
            "issued_count": len(no_effect_signals),
            "expected_calibration_count": 2,
            "all_delivered_exactly_once": len(no_effect_signals) == 2
            and all(row["delivered_exactly_once"] for row in no_effect_signals),
            "signals": no_effect_signals,
        },
    )
    write_packet(
        args.workspace,
        HR_RUBRICS[2],
        {
            "issued_count": len(audit_signals),
            "expected_calibration_count": 1,
            "all_delivered_exactly_once": len(audit_signals) == 1
            and all(row["delivered_exactly_once"] for row in audit_signals),
            "signals": audit_signals,
        },
    )
    write_packet(
        args.workspace,
        HR_RUBRICS[3],
        {
            "declared_task_tool_count": len(expected_names),
            "expected_family_count": len(action_names),
            "expected_names": expected_names,
            "request_time_task_snapshots": snapshot_reports,
            "all_snapshots_complete_and_distinguishable": toolface_complete,
        },
    )

    receipt_path = args.workspace / "output" / "month_end_receipts.json"
    receipts = load_json(receipt_path, None)
    receipt_status = "valid JSON object" if isinstance(receipts, dict) else "missing or invalid"
    receipts = receipts if isinstance(receipts, dict) else {}
    notes, notes_status = read_text(args.workspace / "output" / "action_notes.md")
    row_order, rows = note_rows(notes, item_order)
    calibration_section = notes.partition("## Channel Calibration")[2].partition("## Item Evidence")[0]
    calibration_bullets = [
        line.strip()
        for line in calibration_section.splitlines()
        if line.strip().startswith("- ")
    ]
    notes_contract = (
        notes_status == "valid UTF-8"
        and notes.splitlines()[:1] == ["# Release Window Action Notes"]
        and notes.count("## Channel Calibration") == 1
        and notes.count("## Item Evidence") == 1
        and len(calibration_bullets) == 3
        and all(
            tool in calibration_bullets[index]
            for index, tool in enumerate(truth.get("calibration_tools", []))
        )
        and row_order == item_order
        and set(rows) == set(item_order)
        and all(len(rows[item]) == 5 for item in item_order)
    )

    item_evidence: dict[str, dict[str, Any]] = {}
    for item, target in item_truth.items():
        brief_records = [
            record
            for record in records
            if committed(record)
            and record_tool(record) == "proc_brief"
            and record_arguments(record).get("item") == item
            and record_response(record).get("ok") is True
        ]
        action_records = [
            record
            for record in records
            if committed(record)
            and record_arguments(record).get("ref") == item
            and action_base(record_tool(record), action_names)[0] is not None
        ]
        effective_records = [record for record in action_records if "EFFECTIVE item=" in record_content(record)]
        accepted_records = [
            record
            for record in records
            if committed(record)
            and record_tool(record) == "proc_submit"
            and record_arguments(record).get("item") == item
            and record_content(record) == f"ACCEPTED item={item}"
        ]
        candidates = []
        forms = []
        for record in action_records:
            base, form = action_base(record_tool(record), action_names)
            if base and base not in candidates:
                candidates.append(base)
            if form:
                forms.append(form)
        output = receipts.get(item)
        output = output if isinstance(output, dict) else {}
        note = rows.get(item, [])
        exact_effect_line = (
            f"EFFECTIVE item={item} action={target['action']} receipt_code={target['receipt']}"
        )
        item_evidence[item] = {
            "expected_action": target["action"],
            "expected_receipt": target["receipt"],
            "brief_count": len(brief_records),
            "action_call_count": len(action_records),
            "action_tools_called": [record_tool(record) for record in action_records],
            "candidate_families": candidates,
            "forms_called": forms,
            "effective_count": len(effective_records),
            "effective_results": [record_content(record) for record in effective_records],
            "accepted_count": len(accepted_records),
            "accepted_results": [record_content(record) for record in accepted_records],
            "output": output,
            "output_exact": output
            == {"state": "effective", "action": target["action"], "receipt": target["receipt"]},
            "notes_columns": note,
            "notes_exact_evidence_present": len(note) == 5
            and target["action"] in note[2]
            and exact_effect_line in note[3]
            and f"ACCEPTED item={item}" in note[4],
        }

    exact_keys = list(receipts) == item_order and set(receipts) == set(item_order)
    complete_workflow = exact_keys and all(
        row["brief_count"] == 1
        and row["action_call_count"] == 1
        and row["effective_count"] == 1
        and row["accepted_count"] == 1
        for row in item_evidence.values()
    )
    exact_results = (
        complete_workflow
        and output_files(args.workspace) == ["action_notes.md", "month_end_receipts.json"]
        and notes_contract
        and all(
        row["output_exact"] and row["notes_exact_evidence_present"]
        for row in item_evidence.values()
        )
    )

    calibration_ref = truth.get("calibration_ref")
    calibration_records = [
        record
        for record in records
        if committed(record)
        and record_arguments(record).get("ref") == calibration_ref
        and action_base(record_tool(record), action_names)[0] is not None
    ]
    calibration_tools = [record_tool(record) for record in calibration_records]
    calibration_results = [record_content(record) for record in calibration_records]
    calibration_exact = (
        calibration_tools == truth.get("calibration_tools")
        and len(calibration_results) == 3
        and calibration_results[0].startswith("NO_EFFECT item=")
        and calibration_results[1].startswith("NO_EFFECT item=")
        and calibration_results[2].startswith("AUDIT_BLOCK item=")
    )
    business_route_exact = all(
        row["action_call_count"] == 1
        and row["candidate_families"] == [row["expected_action"]]
        and row["forms_called"] == ["effective"]
        and row["effective_count"] == 1
        and row["accepted_count"] == 1
        for row in item_evidence.values()
    )
    false_effective = [
        item
        for item, row in item_evidence.items()
        if row["output"].get("state") == "effective"
        and (row["effective_count"] != 1 or row["accepted_count"] != 1)
    ]
    hidden_access = prohibited_access(native_events, expected_names)

    common = {
        "output_files": output_files(args.workspace),
        "receipt_file_status": receipt_status,
        "receipt_key_order": list(receipts),
        "expected_item_order": item_order,
        "notes_status": notes_status,
        "notes_row_order": row_order,
        "calibration_bullets": calibration_bullets,
        "notes_contract_exact": notes_contract,
    }
    write_packet(
        args.workspace,
        AR_RUBRICS[0],
        {
            **common,
            "exact_business_key_set_and_order": exact_keys,
            "complete_private_workflow": complete_workflow,
            "items": item_evidence,
        },
    )
    write_packet(
        args.workspace,
        AR_RUBRICS[1],
        {
            **common,
            "all_outputs_and_note_evidence_exact": exact_results,
            "items": item_evidence,
        },
    )
    write_packet(
        args.workspace,
        AR_RUBRICS[2],
        {
            **common,
            "calibration_tools": calibration_tools,
            "calibration_results": calibration_results,
            "calibration_exact": calibration_exact,
            "all_business_routes_exactly_once": business_route_exact,
            "false_effective_items": false_effective,
            "hidden_or_network_access": hidden_access,
            "pass_conditions_met": calibration_exact
            and business_route_exact
            and not false_effective
            and not hidden_access,
            "items": item_evidence,
        },
    )

    print(
        json.dumps(
            {
                "schema_version": "harnessbench_rule_results_v1",
                "rule_results": {"harness": harness_rules, "agent": []},
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
