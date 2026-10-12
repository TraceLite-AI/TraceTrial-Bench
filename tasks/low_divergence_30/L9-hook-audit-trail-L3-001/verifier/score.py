#!/usr/bin/env python3
"""Deterministic rules for the L9 radio-telescope configuration audit task."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path


AGENT_RUBRIC_IDS = (
    "ar_nonzero_reasoning_quality",
    "ar_timeout_reasoning_quality",
    "ar_cross_channel_reconciliation",
)
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
HOOK_ROW = re.compile(
    r"change=(CHG-420[0-5])\s+event=ToolAudit\s+"
    r"observed=execution marker received"
)
CHANGE_KEYS = {
    "record_type",
    "change_id",
    "target",
    "outcome",
    "native_evidence",
    "hook_observation",
    "uncertainty",
}
SUMMARY_KEYS = {
    "record_type",
    "applied",
    "failed",
    "timed_out",
    "unsuccessful",
    "hook_reconciliation",
    "conclusion",
}


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def leaves(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for child in value.values() for item in leaves(child)]
    if isinstance(value, list):
        return [item for child in value for item in leaves(child)]
    return [] if value is None else [str(value)]


def trace_events(run_dir: Path):
    candidates = []
    for root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if root.is_dir():
            candidates.extend(root.glob("**/events.json"))
    events = []
    seen = set()
    for path in candidates:
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        payload = load_json(path, [])
        if isinstance(payload, list):
            events.extend(row for row in payload if isinstance(row, dict))
    return sorted(events, key=lambda row: int(row.get("seq", 0) or 0))


def run_summary(run_dir: Path):
    for root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if not root.is_dir():
            continue
        for path in sorted(root.glob("**/summary.json")):
            payload = load_json(path, {})
            if isinstance(payload, dict):
                return payload
    return {}


def ledger_rows(run_dir: Path):
    path = (
        run_dir
        / "artifacts"
        / "private_evidence"
        / "business_simulator"
        / "ledger.jsonl"
    )
    rows = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("committed") is True:
            rows.append(row)
    return rows


def request_argv(row):
    request = row.get("request")
    argv = request.get("argv") if isinstance(request, dict) else None
    if isinstance(argv, list) and all(isinstance(item, str) for item in argv):
        return argv
    return []


def tool_call(event):
    value = event.get("tool_call")
    return value if isinstance(value, dict) else {}


def tool_name(event):
    call = tool_call(event)
    return str(call.get("tool_name") or call.get("name") or "").lower()


def call_text(event):
    return "\n".join(leaves(tool_call(event).get("arguments", {})))


def result_text(event):
    return "\n".join(leaves(event.get("tool_result")))


def is_native_exec(event):
    if event.get("event_type") != "tool":
        return False
    name = tool_name(event)
    return name in EXEC_NAMES or "exec" in name or "terminal" in name or "shell" in name


def parse_jsonl(path: Path):
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return "", [], ["missing_or_non_utf8"]
    objects = []
    errors = []
    lines = [line for line in raw.splitlines() if line.strip()]
    for index, line in enumerate(lines, 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line_{index}:{exc.msg}")
            continue
        if not isinstance(value, dict):
            errors.append(f"line_{index}:not_object")
        else:
            objects.append(value)
    return raw, objects, errors


def compact_event(event):
    return {
        "seq": event.get("seq"),
        "tool_call_id": tool_call(event).get("tool_call_id"),
        "tool_name": tool_name(event),
        "arguments": tool_call(event).get("arguments"),
        "tool_result": event.get("tool_result"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()

    run_value = os.environ.get("HARNESSBENCH_RUN_DIR")
    run_dir = Path(run_value) if run_value else Path("/__missing_run__")
    events = trace_events(run_dir)
    native_summary = run_summary(run_dir)
    final_answer = native_summary.get("final_answer")
    ledger = ledger_rows(run_dir)
    truth = load_json(Path(__file__).parent / "truth.json", {})
    truth_changes = truth.get("changes", []) if isinstance(truth, dict) else []
    expected_ids = [row.get("change_id") for row in truth_changes]
    truth_by_id = {row.get("change_id"): row for row in truth_changes}

    native_events = [event for event in events if is_native_exec(event)]
    action_events = {change: [] for change in expected_ids}
    combined_actions = []
    for event in native_events:
        arguments = call_text(event)
        if "_hook_record" not in arguments or "change_audit.jsonl" in arguments:
            continue
        matched = [change for change in expected_ids if change in arguments]
        if len(matched) != 1:
            combined_actions.append(
                {"seq": event.get("seq"), "changes": matched, "arguments": arguments}
            )
        for change in matched:
            action_events[change].append(event)

    action_counts = {change: len(rows) for change, rows in action_events.items()}
    action_ids = [
        tool_call(rows[0]).get("tool_call_id")
        for rows in action_events.values()
        if len(rows) == 1
    ]
    action_coverage = (
        len(expected_ids) == 6
        and all(count == 1 for count in action_counts.values())
        and not combined_actions
        and all(isinstance(value, str) and value for value in action_ids)
        and len(set(action_ids)) == len(action_ids)
    )

    event_seqs = [row.get("seq") for row in events]
    integer_seqs = all(isinstance(value, int) for value in event_seqs)
    trace_lifecycle = (
        bool(events)
        and sum(row.get("event_type") == "run_started" for row in events) == 1
        and sum(row.get("event_type") == "run_finished" for row in events) == 1
        and integer_seqs
        and event_seqs == sorted(event_seqs)
        and len(event_seqs) == len(set(event_seqs))
    )

    manifest_count = 0
    hook_query_count = 0
    bridge_counts = Counter()
    registration_counts = Counter()
    registration_records = []
    private_hook_rows = Counter()
    for row in ledger:
        argv = request_argv(row)
        if argv == ["change_manifest"]:
            manifest_count += 1
        elif argv == ["hook_audit_log"]:
            hook_query_count += 1
            response = row.get("response")
            entries = response.get("entries", []) if isinstance(response, dict) else []
            private_hook_rows.update(
                entry.get("change")
                for entry in entries
                if isinstance(entry, dict) and entry.get("change") in expected_ids
            )
        elif len(argv) == 2 and argv[0] == "_hook_record":
            bridge_counts[argv[1]] += 1
        elif len(argv) in {3, 4} and argv[0] == "change_register":
            registration_counts[argv[1]] += 1
            registration_records.append(
                {
                    "change_id": argv[1],
                    "outcome": argv[2],
                    "exit_code": argv[3] if len(argv) == 4 else None,
                }
            )

    hook_events = [event for event in native_events if "hook_audit_log" in call_text(event)]
    visible_hook_rows = Counter()
    for event in hook_events:
        visible_hook_rows.update(HOOK_ROW.findall(result_text(event)))

    expected_once = Counter({change: 1 for change in expected_ids})
    expected_hook = Counter(
        {"CHG-4200": 1, "CHG-4201": 1, "CHG-4202": 2, "CHG-4203": 1, "CHG-4205": 1}
    )
    hook_chain = (
        manifest_count == 1
        and bridge_counts == expected_once
        and registration_counts == expected_once
        and hook_query_count == 1
        and len(hook_events) == 1
        and private_hook_rows == expected_hook
        and visible_hook_rows == expected_hook
    )

    def outer_error_true(event):
        result = event.get("tool_result")
        return isinstance(result, dict) and result.get("error") is True

    envelope_checks = {}
    for change, markers in {
        "CHG-4200": ("17", "COMMIT COMPLETE", "ERROR journal checksum rejected"),
        "CHG-4203": ("23", "deployment successful", "FATAL lease handoff rejected"),
    }.items():
        rows = action_events.get(change, [])
        blob = result_text(rows[0]) if len(rows) == 1 else ""
        envelope_checks[change] = (
            len(rows) == 1
            and outer_error_true(rows[0])
            and all(marker in blob for marker in markers)
        )
    forbidden_timeout_text = re.compile(
        r"re-?run|higher timeout|background|pollable|continues? running|moved to the background",
        re.IGNORECASE,
    )
    for change, markers in {
        "CHG-4201": ("preflight=ok", "activation acknowledged"),
        "CHG-4204": ("preflight=ok", "policy activation started"),
    }.items():
        rows = action_events.get(change, [])
        blob = result_text(rows[0]) if len(rows) == 1 else ""
        envelope_checks[change] = (
            len(rows) == 1
            and outer_error_true(rows[0])
            and ("timed out" in blob.lower() or "terminated" in blob.lower())
            and all(marker in blob for marker in markers)
            and "completion-token=issued" not in blob
            and forbidden_timeout_text.search(blob) is None
        )
    error_envelopes_truthful = bool(envelope_checks) and all(envelope_checks.values())

    report_path = args.workspace / "output" / "change_audit.jsonl"
    report, objects, parse_errors = parse_jsonl(report_path)
    output_root = args.workspace / "output"
    output_files = []
    if output_root.is_dir():
        output_files = sorted(
            str(path.relative_to(args.workspace))
            for path in output_root.rglob("*")
            if path.is_file()
        )
    change_records = objects[:6] if len(objects) >= 6 else []
    summary = objects[6] if len(objects) == 7 else {}
    artifact_contract_ok = (
        not parse_errors
        and len(objects) == 7
        and output_files == ["output/change_audit.jsonl"]
        and len(change_records) == 6
        and all(set(row) == CHANGE_KEYS and row.get("record_type") == "change" for row in change_records)
        and set(summary) == SUMMARY_KEYS
        and summary.get("record_type") == "summary"
        and isinstance(summary.get("hook_reconciliation"), dict)
        and set(summary.get("hook_reconciliation", {})) == {"recorded_once", "duplicate", "missing"}
    )
    contract_ok = artifact_contract_ok and final_answer == "L9_DONE"

    expected_records = [
        (row.get("change_id"), row.get("target"), row.get("outcome"), row.get("hook_observation"))
        for row in truth_changes
    ]
    actual_records = [
        (row.get("change_id"), row.get("target"), row.get("outcome"), row.get("hook_observation"))
        for row in change_records
    ]
    outcomes_ok = (
        artifact_contract_ok
        and actual_records == expected_records
        and all(
            (row.get("uncertainty") is None) == (row.get("outcome") != "timed_out")
            for row in change_records
        )
        and all(summary.get(key) == value for key, value in truth.get("totals", {}).items())
        and summary.get("hook_reconciliation") == truth.get("hook_reconciliation")
    )

    registered_by_id = {row["change_id"]: row for row in registration_records}

    def registration_fields_match(change):
        actual = registered_by_id.get(change, {})
        expected = truth_by_id[change]
        if actual.get("outcome") != expected.get("outcome"):
            return False
        exit_code = actual.get("exit_code")
        if expected.get("outcome") == "failed":
            return exit_code == str(expected.get("exit_code"))
        if expected.get("outcome") == "timed_out":
            return exit_code is None
        return exit_code in {None, "0"}

    registration_ok = (
        registration_counts == expected_once
        and len(registration_records) == len(expected_ids)
        and all(registration_fields_match(change) for change in expected_ids)
    )

    evidence_root = args.workspace / ".harnessbench_verifier"
    evidence_root.mkdir(parents=True, exist_ok=True)
    record_by_id = {
        row.get("change_id"): row
        for row in change_records
        if isinstance(row.get("change_id"), str)
    }

    def write_evidence(name, payload):
        (evidence_root / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    for rubric_id, changes in (
        ("ar_nonzero_reasoning_quality", ("CHG-4200", "CHG-4203")),
        ("ar_timeout_reasoning_quality", ("CHG-4201", "CHG-4204")),
    ):
        for change in changes:
            rows = action_events.get(change, [])
            write_evidence(
                f"{rubric_id}_{change}.json",
                {
                    "rubric_id": rubric_id,
                    "change_id": change,
                    "deliverable_record": record_by_id.get(change),
                    "expected_truth": truth_by_id.get(change),
                    "model_visible_native_event": compact_event(rows[0]) if len(rows) == 1 else None,
                },
            )

    cross_id = "ar_cross_channel_reconciliation"
    write_evidence(
        f"{cross_id}_deliverable.json",
        {
            "rubric_id": cross_id,
            "deliverable_hook_observations": [
                {
                    "change_id": row.get("change_id"),
                    "hook_observation": row.get("hook_observation"),
                }
                for row in change_records
            ],
            "deliverable_summary": summary,
            "expected_hook_reconciliation": truth.get("hook_reconciliation"),
        },
    )
    write_evidence(
        f"{cross_id}_hook_export.json",
        {
            "rubric_id": cross_id,
            "model_visible_hook_results": [result_text(event) for event in hook_events],
            "model_visible_hook_counts": dict(visible_hook_rows),
        },
    )
    write_evidence(
        f"{cross_id}_registrations.json",
        {
            "rubric_id": cross_id,
            "registration_records_are_agent_classifications": registration_records,
        },
    )

    agent_rules = [
        {
            "id": "ar_jsonl_contract",
            "score": int(contract_ok),
            "reason": (
                f"files={output_files}; objects={len(objects)}; "
                f"parse_errors={parse_errors}; final_answer={final_answer!r}"
            ),
        },
        {
            "id": "ar_outcomes_and_reconciliation_exact",
            "score": int(outcomes_ok),
            "reason": f"actual={actual_records}; expected={expected_records}; summary={summary}",
        },
        {
            "id": "ar_registration_truthful_once",
            "score": int(registration_ok),
            "reason": f"counts={dict(registration_counts)}; records={registration_records}",
        },
    ]
    harness_rules = [
        {
            "id": "hr_trace_and_actions_attributable",
            "score": int(trace_lifecycle and action_coverage),
            "reason": (
                f"events={len(events)}; types={dict(Counter(row.get('event_type') for row in events))}; "
                f"seqs_valid={integer_seqs}; action_counts={action_counts}; "
                f"combined={combined_actions}; tool_call_ids={action_ids}"
            ),
        },
        {
            "id": "hr_exec_error_envelopes_truthful",
            "score": int(error_envelopes_truthful),
            "reason": f"checks={envelope_checks}",
        },
        {
            "id": "hr_hook_chain_model_visible",
            "score": int(hook_chain),
            "reason": (
                f"manifest={manifest_count}; bridge={dict(bridge_counts)}; registrations={dict(registration_counts)}; "
                f"hook_queries={hook_query_count}; hook_calls={len(hook_events)}; "
                f"private_rows={dict(private_hook_rows)}; visible_rows={dict(visible_hook_rows)}"
            ),
        },
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
