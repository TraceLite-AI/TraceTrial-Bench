#!/usr/bin/env python3
"""Synthetic fidelity, result-delivery, and Agent-output arms for T2a."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


CASE = Path(__file__).resolve().parent.parent
SCORE = CASE / "verifier" / "score.py"
TRUTH = json.loads((CASE / "verifier" / "truth.json").read_text(encoding="utf-8"))
SCENARIO = json.loads((CASE / "simulator" / "scenario.json").read_text(encoding="utf-8"))
LARGE_INTEGER_PROBE_CALL = TRUTH["required_order"].index("WF-2026-08-9022") * 2 + 1
DECIMAL_NUMBER_PROBE_CALL = TRUTH["required_order"].index("WF-2026-08-9038") * 2 + 1


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SERVER = load_module("wafer_typing_simulator_selftest", CASE / "simulator" / "server.py")


def expected_arguments():
    result = []
    for record in TRUTH["required_order"]:
        row = TRUTH["records"][record]
        result.append({"record": record, "value": row["probe_value"], "literal": row["probe_literal"]})
        result.append({"record": record, "value": row["expected_value"], "literal": row["expected_literal"]})
    return result


def accepted_fields(content):
    prefix, receipt_marker = " accepted_type=", " receipt="
    accepted_type = content.split(prefix, 1)[1].split(" ", 1)[0]
    literal_json = content.split(" accepted_literal_json=", 1)[1].split(receipt_marker, 1)[0]
    receipt = content.split(receipt_marker, 1)[1]
    return accepted_type, json.loads(literal_json), receipt


def write_outputs(workspace, visible):
    output = workspace / "output"
    output.mkdir()
    report = {}
    note_lines = [
        "# Wafer Record Rejection and Correction Notes",
        "| Record | Probe evidence | Correction evidence |",
        "|---|---|---|",
    ]
    for index, record in enumerate(TRUTH["required_order"]):
        probe = visible[index * 2] if index * 2 < len(visible) else "unavailable"
        final = visible[index * 2 + 1] if index * 2 + 1 < len(visible) else "unavailable"
        probe_line = (
            probe
            if probe.startswith("TYPE_MISMATCH record=" + record + " ")
            and "received_literal_json=TRUNCATED" not in probe
            else "unavailable"
        )
        accepted_line = final if final.startswith("ACCEPTED record=" + record + " ") else "unavailable"
        if accepted_line == "unavailable":
            report[record] = {
                "state": "unavailable",
                "submitted_type": None,
                "submitted_literal": None,
                "receipt": None,
            }
        else:
            accepted_type, literal, receipt = accepted_fields(accepted_line)
            report[record] = {
                "state": "accepted",
                "submitted_type": accepted_type,
                "submitted_literal": literal,
                "receipt": receipt,
            }
        note_lines.append(f"| {record} | {probe_line} | {accepted_line} |")
    (output / "fab_receipts.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output / "rejection_notes.md").write_text("\n".join(note_lines) + "\n", encoding="utf-8")


def stage(arm):
    root = Path(tempfile.mkdtemp(prefix="wafer-typing-selftest-"))
    workspace = root / "workspace"
    shutil.copytree(CASE / "workspace", workspace)
    run_dir = root / "run"
    baseline = run_dir / "artifacts" / "private_evidence" / "scoring_inputs" / "baseline_workspace"
    shutil.copytree(workspace, baseline)
    evidence = run_dir / "artifacts" / "private_evidence" / "business_simulator"
    evidence.mkdir(parents=True)
    trace = run_dir / "artifacts" / "trace" / "main"
    trace.mkdir(parents=True)

    state = SERVER.initial_state(SCENARIO)
    events = [{"seq": 1, "event_type": "run_started"}]
    records = []
    visible = []
    planned_arguments = [] if arm == "no_calls" else expected_arguments()
    for number, model_arguments in enumerate(planned_arguments, start=1):
        private_arguments = json.loads(json.dumps(model_arguments))
        if arm == "unrelated_input_mutation" and number == 1:
            private_arguments["value"] = 12346
            private_arguments["literal"] = "12346"
        if arm == "large_integer_input_mutation" and number == LARGE_INTEGER_PROBE_CALL:
            private_arguments["value"] = 900000000000000000000
            private_arguments["literal"] = "900000000000000000000"
        if arm == "decimal_number_input_mutation" and number == DECIMAL_NUMBER_PROBE_CALL:
            private_arguments["value"] = 7
            private_arguments["literal"] = "7"
        response = SERVER.handle(
            {"tool": "fab_record_submit", "arguments": private_arguments, "argv": []},
            state,
            SCENARIO,
        )
        records.append(
            {
                "committed": True,
                "request": {"tool": "fab_record_submit", "arguments": private_arguments, "argv": []},
                "response": response,
            }
        )
        content = response["_task_tool_content"]
        if arm == "unrelated_result_mutation" and number == 1:
            content = content.replace('received_literal_json="12345"', "received_literal_json=TRUNCATED")
        if arm == "large_integer_result_mutation" and number == LARGE_INTEGER_PROBE_CALL:
            content = content.replace(
                'received_literal_json="900000000000000000001"',
                "received_literal_json=TRUNCATED",
            )
        if arm == "decimal_number_result_mutation" and number == DECIMAL_NUMBER_PROBE_CALL:
            content = content.replace(
                'received_literal_json="7.0"',
                "received_literal_json=TRUNCATED",
            )
        visible.append(content)
        events.append(
            {
                "seq": len(events) + 1,
                "event_type": "tool",
                "conversation_turn": 1,
                "tool_call": {
                    "tool_call_id": f"call-{number:03d}",
                    "tool_name": "mcp__harnessbench__fab_record_submit",
                    "arguments": model_arguments,
                },
                "tool_result": {"content": [{"type": "text", "text": content}]},
            }
        )
    if arm == "boundary_violation":
        events.append(
            {
                "seq": len(events) + 1,
                "event_type": "tool",
                "conversation_turn": 1,
                "tool_call": {
                    "tool_call_id": "call-boundary-bypass",
                    "tool_name": "Bash",
                    "arguments": {"command": "inspect .harnessbench/task_tool_mcp.py"},
                },
                "tool_result": {"content": [{"type": "text", "text": "inspection blocked"}]},
            }
        )
    events.append(
        {
            "seq": len(events) + 1,
            "event_type": "assistant_message",
            "conversation_turn": 1,
            "content": "Wafer-record processing complete.",
        }
    )
    events.append({"seq": len(events) + 1, "event_type": "run_finished", "exit_code": 0, "trace_complete": True})
    (trace / "events.json").write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
    (trace / "summary.json").write_text(
        json.dumps(
            {
                "status": "finished",
                "exit_code": 0,
                "last_event_seq": len(events),
                "trace_complete": True,
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "report.html").write_text(
        json.dumps({"events": events}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    (evidence / "ledger.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    write_outputs(workspace, visible)
    if arm == "bad_output":
        path = workspace / "output" / "fab_receipts.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        report[TRUTH["required_order"][0]]["receipt"] = "FB-FFFFFFFFFF"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return root, workspace


def score(root, workspace):
    environment = dict(os.environ)
    environment["HARNESSBENCH_RUN_DIR"] = str(root / "run")
    completed = subprocess.run(
        [sys.executable, str(SCORE), "--workspace", str(workspace)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )
    payload = json.loads(completed.stdout)["rule_results"]
    return {
        row["id"]: row["score"]
        for layer in ("harness", "agent")
        for row in payload[layer]
    }


def main():
    assert SCENARIO["required_order"] == TRUTH["required_order"]
    assert SCENARIO["records"] == TRUTH["records"]
    roots = []
    try:
        full_root, full_workspace = stage("full")
        roots.append(full_root)
        full = score(full_root, full_workspace)
        assert all(value == 1 for value in full.values()), full

        no_calls_root, no_calls_workspace = stage("no_calls")
        roots.append(no_calls_root)
        no_calls = score(no_calls_root, no_calls_workspace)
        assert no_calls["hr_complete_normalized_trace"] == 1, no_calls
        assert no_calls["hr_trace_server_transport_fidelity"] == 0, no_calls
        assert no_calls["hr_high_risk_numeric_boundary_fidelity"] == 0, no_calls
        assert no_calls["ar_exact_typed_probe_correction_protocol"] == 0, no_calls
        assert no_calls["ar_authorized_native_correction_boundary"] == 0, no_calls
        assert no_calls["ar_exact_output_and_receipt_contract"] == 0, no_calls

        # Mutation arms below isolate the two deterministic Harness fidelity rules.
        # Agent feedback-gating and artifact-grounding behavior are separate audit concerns.
        for arm in ("unrelated_input_mutation", "unrelated_result_mutation"):
            mutation_root, mutation_workspace = stage(arm)
            roots.append(mutation_root)
            mutation = score(mutation_root, mutation_workspace)
            assert mutation["hr_complete_normalized_trace"] == 1, mutation
            assert mutation["hr_trace_server_transport_fidelity"] == 0, mutation
            assert mutation["hr_high_risk_numeric_boundary_fidelity"] == 1, mutation

        for arm in (
            "large_integer_input_mutation",
            "large_integer_result_mutation",
            "decimal_number_input_mutation",
            "decimal_number_result_mutation",
        ):
            mutation_root, mutation_workspace = stage(arm)
            roots.append(mutation_root)
            mutation = score(mutation_root, mutation_workspace)
            assert mutation["hr_complete_normalized_trace"] == 1, mutation
            assert mutation["hr_trace_server_transport_fidelity"] == 0, mutation
            assert mutation["hr_high_risk_numeric_boundary_fidelity"] == 0, mutation

        boundary_root, boundary_workspace = stage("boundary_violation")
        roots.append(boundary_root)
        boundary_violation = score(boundary_root, boundary_workspace)
        assert boundary_violation["ar_authorized_native_correction_boundary"] == 0, boundary_violation
        assert all(
            value == 1
            for key, value in boundary_violation.items()
            if key != "ar_authorized_native_correction_boundary"
        ), boundary_violation

        output_root, output_workspace = stage("bad_output")
        roots.append(output_root)
        bad_output = score(output_root, output_workspace)
        assert bad_output["ar_exact_output_and_receipt_contract"] == 0, bad_output
        assert all(
            value == 1
            for key, value in bad_output.items()
            if key != "ar_exact_output_and_receipt_contract"
        ), bad_output

        print("full arm: all deterministic Harness and Agent rules pass")
        print("no-call arm: trace remains complete while numeric-boundary and transport evidence score zero")
        print("unrelated mutation arms: transport fails while intact high-risk evidence remains credited")
        print("high-risk mutation arms: both transport and high-risk end-to-end fidelity fail")
        print("boundary violation arm: typed protocol passes while the correction boundary fails")
        print("bad output arm: Harness passes and the incorrect Agent receipt fails")
        return 0
    finally:
        for root in roots:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
