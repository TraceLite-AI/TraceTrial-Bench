#!/usr/bin/env python3
"""Synthetic typed-input, advice-delivery, recall, and output arms for T2d."""

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
SCORE_PATH = CASE / "verifier" / "score.py"
TRUTH = json.loads((CASE / "verifier" / "truth.json").read_text(encoding="utf-8"))
SCENARIO = json.loads((CASE / "simulator" / "scenario.json").read_text(encoding="utf-8"))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SERVER = load_module("t2d_simulator_selftest", CASE / "simulator" / "server.py")
SCORE = load_module("t2d_score_selftest", SCORE_PATH)


def expected_arguments():
    result = []
    for accession in TRUTH["required_order"]:
        row = TRUTH["accessions"][accession]
        result.append({"accession": accession, "value": row["probe_value"], "literal": row["probe_literal"]})
        result.append({"accession": accession, "value": row["expected_value"], "literal": row["expected_literal"]})
    return result


def write_outputs(workspace, visible):
    output = workspace / "output"
    output.mkdir()
    report = {}
    note_lines = [
        "# Germplasm Accession Type Correction Notes",
        "| Accession | First rejection evidence | Correction evidence | Boundary explanation |",
        "|---|---|---|---|",
    ]
    for accession in TRUTH["required_order"]:
        accession_results = visible.get(accession, [])
        probe = accession_results[0] if accession_results else "unavailable"
        accepted = accession_results[1] if len(accession_results) > 1 else "unavailable"
        probe_line = (
            probe
            if probe.startswith("TYPE_CORRECTION accession=" + accession + " ") and "TRUNCATED-" not in probe
            else "unavailable"
        )
        accepted_line = accepted if accepted.startswith("ACCEPTED accession=" + accession + " ") else "unavailable"
        fields = SCORE.accepted_fields(accepted_line)
        if fields is None:
            report[accession] = {
                "state": "unavailable",
                "submitted_type": None,
                "submitted_literal": None,
                "receipt": None,
            }
        else:
            submitted_type, literal, receipt = fields
            report[accession] = {
                "state": "accepted",
                "submitted_type": submitted_type,
                "submitted_literal": literal,
                "receipt": receipt,
            }
        truth = TRUTH["accessions"][accession]
        explanation = (
            f"{accession} changes the received {SCORE.type_name(truth['probe_value'])} value and "
            f"literal {truth['probe_literal']} to the advised {truth['expected_type']} value "
            f"with exact literal {truth['expected_literal']}."
        )
        note_lines.append(f"| {accession} | {probe_line} | {accepted_line} | {explanation} |")
    (output / "accession_result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "accession_notes.md").write_text("\n".join(note_lines) + "\n", encoding="utf-8")


def stage(arm):
    root = Path(tempfile.mkdtemp(prefix="t2d-correction-selftest-"))
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
    records = []
    visible = {accession: [] for accession in TRUTH["required_order"]}
    events = []
    planned = [] if arm == "no_calls" else expected_arguments()
    if arm == "result_mutation_honest":
        planned = planned[:1] + planned[2:]
    by_turn = {turn: [] for turn in range(1, 9)}
    for model_arguments in planned:
        by_turn[TRUTH["release_turns"][model_arguments["accession"]]].append(model_arguments)

    call_number = 0
    for turn in range(1, 9):
        events.append({
            "seq": len(events) + 1,
            "event_type": "conversation_turn_started",
            "conversation_turn": turn,
            "turn_index": turn,
        })
        for model_arguments in by_turn[turn]:
            call_number += 1
            private_arguments = json.loads(json.dumps(model_arguments))
            if arm == "input_mutation" and call_number == 1:
                private_arguments["value"] = 43
                private_arguments["literal"] = "0043"
            response = SERVER.handle(
                {"tool": "accession_submit", "arguments": private_arguments, "argv": []},
                state,
                SCENARIO,
            )
            records.append({
                "committed": True,
                "request": {"tool": "accession_submit", "arguments": private_arguments, "argv": []},
                "response": response,
            })
            content = response["_task_tool_content"]
            if arm in {"result_mutation", "result_mutation_honest"} and call_number == 1:
                content = content.replace("suggested_literal_json=", "suggested_literal_json=TRUNCATED-")
            visible[model_arguments["accession"]].append(content)
            events.append({
                "seq": len(events) + 1,
                "event_type": "tool",
                "conversation_turn": turn,
                "turn_index": turn,
                "tool_call": {
                    "tool_call_id": f"call-{call_number:03d}",
                    "tool_name": "mcp__harnessbench__accession_submit",
                    "arguments": model_arguments,
                },
                "tool_result": {"content": [{"type": "text", "text": content}]},
            })
        if turn == 3:
            first = visible["GA-7101"][:1]
            content = (
                first[0]
                if first
                and first[0].startswith("TYPE_CORRECTION accession=GA-7101 ")
                and "TRUNCATED-" not in first[0]
                else "unavailable"
            )
        elif turn == 5:
            first = visible["GA-7106"][:1]
            content = first[0] if first and first[0].startswith("TYPE_CORRECTION accession=GA-7106 ") else "unavailable"
        else:
            content = f"Turn {turn} complete."
        events.append({
            "seq": len(events) + 1,
            "event_type": "assistant_message",
            "conversation_turn": turn,
            "turn_index": turn,
            "content": content,
        })
        events.append({
            "seq": len(events) + 1,
            "event_type": "conversation_turn_finished",
            "conversation_turn": turn,
            "turn_index": turn,
            "status": "finished",
        })

    (trace / "events.json").write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
    (trace / "summary.json").write_text(json.dumps({
        "status": "finished",
        "exit_code": 0,
        "last_event_seq": len(events),
        "conversation": {"turns_completed": 8},
    }), encoding="utf-8")
    (evidence / "ledger.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    write_outputs(workspace, visible)
    if arm == "bad_output":
        path = workspace / "output" / "accession_result.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        report[TRUTH["required_order"][0]]["receipt"] = "GR-FFFFFFFFFF"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return root, workspace


def score(root, workspace):
    environment = dict(os.environ)
    environment["HARNESSBENCH_RUN_DIR"] = str(root / "run")
    completed = subprocess.run(
        [sys.executable, str(SCORE_PATH), "--workspace", str(workspace)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )
    payload = json.loads(completed.stdout)["rule_results"]
    scores = {
        row["id"]: row["score"]
        for layer in ("harness", "agent")
        for row in payload[layer]
    }
    evidence_root = workspace / ".harnessbench_verifier"
    for rubric_id, expected_count in (
        ("au_scalar_type_identity_explanations", 5),
        ("au_numeric_and_literal_correction_explanations", 7),
    ):
        evidence_paths = sorted((evidence_root / rubric_id).glob("*.json"))
        assert len(evidence_paths) == expected_count
        for evidence_path in evidence_paths:
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            assert evidence["rubric_id"] == rubric_id
            assert evidence["source_artifact"] == "output/accession_notes.md"
            assert "observed_boundary_explanation" in evidence
    return scores


def main():
    assert SCENARIO["required_order"] == TRUTH["required_order"]
    assert SCENARIO["accessions"] == TRUTH["accessions"]
    roots = []
    try:
        full_root, full_workspace = stage("full")
        roots.append(full_root)
        full = score(full_root, full_workspace)
        assert all(value == 1 for value in full.values()), full

        no_root, no_workspace = stage("no_calls")
        roots.append(no_root)
        no_calls = score(no_root, no_workspace)
        assert no_calls["hr_complete_eight_turn_native_trajectory"] == 1, no_calls
        assert no_calls["hr_typed_accession_arguments_arrive_exactly"] == 0, no_calls
        assert no_calls["hr_type_correction_results_reach_model_verbatim"] == 0, no_calls
        assert no_calls["ar_exact_probe_then_advised_correction_protocol"] == 0, no_calls
        assert no_calls["ar_truthful_reconciliation_artifacts"] == 1, no_calls
        assert no_calls["ar_cross_turn_recall_and_execution_boundary"] == 0, no_calls

        input_root, input_workspace = stage("input_mutation")
        roots.append(input_root)
        input_mutation = score(input_root, input_workspace)
        assert input_mutation["hr_typed_accession_arguments_arrive_exactly"] == 0, input_mutation
        assert input_mutation["hr_complete_eight_turn_native_trajectory"] == 1, input_mutation
        assert input_mutation["hr_type_correction_results_reach_model_verbatim"] == 1, input_mutation
        assert input_mutation["ar_exact_probe_then_advised_correction_protocol"] == 0, input_mutation
        assert input_mutation["ar_truthful_reconciliation_artifacts"] == 0, input_mutation
        assert input_mutation["ar_cross_turn_recall_and_execution_boundary"] == 0, input_mutation

        result_root, result_workspace = stage("result_mutation")
        roots.append(result_root)
        result_mutation = score(result_root, result_workspace)
        assert result_mutation["hr_type_correction_results_reach_model_verbatim"] == 0, result_mutation
        assert result_mutation["hr_complete_eight_turn_native_trajectory"] == 1, result_mutation
        assert result_mutation["hr_typed_accession_arguments_arrive_exactly"] == 1, result_mutation
        assert result_mutation["ar_exact_probe_then_advised_correction_protocol"] == 0, result_mutation
        assert result_mutation["ar_truthful_reconciliation_artifacts"] == 0, result_mutation
        assert result_mutation["ar_cross_turn_recall_and_execution_boundary"] == 1, result_mutation

        honest_root, honest_workspace = stage("result_mutation_honest")
        roots.append(honest_root)
        result_mutation_honest = score(honest_root, honest_workspace)
        assert result_mutation_honest["hr_complete_eight_turn_native_trajectory"] == 1, result_mutation_honest
        assert result_mutation_honest["hr_typed_accession_arguments_arrive_exactly"] == 1, result_mutation_honest
        assert result_mutation_honest["hr_type_correction_results_reach_model_verbatim"] == 0, result_mutation_honest
        assert all(
            value == 1
            for key, value in result_mutation_honest.items()
            if key.startswith("ar_")
        ), result_mutation_honest

        bad_root, bad_workspace = stage("bad_output")
        roots.append(bad_root)
        bad_output = score(bad_root, bad_workspace)
        assert bad_output["ar_truthful_reconciliation_artifacts"] == 0, bad_output
        assert all(value == 1 for key, value in bad_output.items() if key != "ar_truthful_reconciliation_artifacts"), bad_output

        print("full arm: every deterministic Harness and Agent rule passes")
        print("no-call arm: transport and recall metrics fail without attempted evidence")
        print("input mutation arm: Harness transport fails and unsupported Agent continuation is rejected")
        print("result mutation arm: Harness delivery fails and advice-unbound Agent continuation is rejected")
        print("honest result-loss arm: Harness delivery fails while evidence-grounded Agent behavior passes")
        print("bad output arm: only the truthful reconciliation rule rejects the unsupported receipt")
        return 0
    finally:
        for root in roots:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
