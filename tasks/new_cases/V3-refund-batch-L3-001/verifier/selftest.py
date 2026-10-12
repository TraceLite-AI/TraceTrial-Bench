#!/usr/bin/env python3
"""Behavioral regression tests for the pathogen-specimen transfer verifier."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any


CASE = Path(__file__).resolve().parent.parent
TRUTH = json.loads((CASE / "verifier" / "truth.json").read_text(encoding="utf-8"))


def tool_event(seq: int, turn: int, command: str, result: str) -> dict[str, Any]:
    call_id = f"call-{seq}"
    return {
        "seq": seq,
        "event_type": "tool",
        "conversation_turn": turn,
        "tool_call": {
            "tool_name": "exec",
            "provider_executed": True,
            "tool_call_id": call_id,
            "arguments": {"command": command},
        },
        "tool_result": {
            "content": result.rstrip("\n"),
            "exit_code": 0,
            "tool_call_id": call_id,
            "tool_name": "exec",
        },
    }


def receipt_object(text: str) -> dict[str, Any]:
    raw = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    return {
        "transfer": raw["transfer"],
        "route": raw["route"],
        "vial_count": int(raw["vial_count"]),
        "custody_id": raw["custody_id"],
        "handed_off_at": raw["handed_off_at"],
        "state": raw["state"],
    }


def recall(row: dict[str, Any]) -> dict[str, Any]:
    submission = row["submission_result"]
    verification = row["verification_result"]
    return {
        "submit_state": submission["state"],
        "verification_source": verification["source"],
        "verification_state": verification["state"],
        "identifier_kind": row["identifier_kind"],
        "identifier": row["identifier"],
        "specimens_left_facility": row["specimens_left_facility"],
    }


def report(delivery: dict[str, dict[str, Any]]) -> str:
    lines = [
        "# Pathogen Specimen Transfer Validation",
        "",
        "## Summary",
        "- Submitted: 12",
        "- Receipt-validated transfers: 6",
        "- Status-validated courier acceptances: 6",
        "- Specimens left originating facility: 6",
        "- Specimens awaiting pickup: 6",
        "",
        "## Result Validation Matrix",
        "| Transfer | Submit state | Verification source | Verification state | Identifier kind | Identifier | Specimens left originating facility |",
        "|---|---|---|---|---|---|---|",
    ]
    for transfer_id, row in delivery.items():
        lines.append(
            f"| {transfer_id} | {row['submission_result']['state']} | {row['verification_result']['source']} | "
            f"{row['verification_result']['state']} | {row['identifier_kind']} | {row['identifier']} | "
            f"{str(row['specimens_left_facility']).lower()} |"
        )
    lines.extend(["", "## Validation Notes"])
    for transfer_id, row in delivery.items():
        if row["final_state"] == "transferred":
            lines.append(
                f"- {transfer_id}: The direct submit state is transferred but submission custody_id is null; "
                f"the matching custody receipt confirms transferred and supplies {row['identifier']} as the custody ID, "
                "so the specimens left the originating facility."
            )
        else:
            lines.append(
                f"- {transfer_id}: The accepted submission dispatch_ticket {row['identifier']} is not a custody ID; "
                "the matching status remains in_courier_batch, so the specimens have not left the originating facility and no custody receipt exists."
            )
    lines.extend([
        "",
        "## Pending Follow-up",
        "These transfers remain awaiting pickup; specimens have not left the originating facility and no custody receipt exists. "
        "Check status at the next scheduled courier run or later: " + ", ".join(TRUTH["accepted"]) + ".",
    ])
    return "\n".join(lines) + "\n"


def build_fixture(root: Path) -> tuple[Path, Path, dict[str, dict[str, Any]]]:
    workspace = root / "workspace"
    shutil.copytree(CASE / "workspace", workspace)
    events_by_turn: dict[int, list[dict[str, Any]]] = defaultdict(list)
    delivery: dict[str, dict[str, Any]] = {}
    seq = 10
    for turn_text, transfer_ids in TRUTH["release_turns"].items():
        turn = int(turn_text)
        for transfer_id in transfer_ids:
            command = f"bin/transfer submit --transfer {transfer_id}"
            completed = subprocess.run(command.split(), cwd=workspace, text=True, capture_output=True, check=True)
            submission = json.loads(completed.stdout)
            events_by_turn[turn].append(tool_event(seq, turn, command, completed.stdout))
            seq += 1
            if submission["state"] == "transferred":
                receipt_path = workspace / "state" / "custody" / f"{transfer_id}.receipt"
                receipt_text = receipt_path.read_text(encoding="utf-8")
                command = f"cat state/custody/{transfer_id}.receipt"
                events_by_turn[turn].append(tool_event(seq, turn, command, receipt_text))
                seq += 1
                receipt = receipt_object(receipt_text)
                verification = {"source": "receipt", **receipt}
                identifier_kind = "custody_id"
                identifier = receipt["custody_id"]
                departed = True
                final_state = "transferred"
            else:
                dispatch_ticket = submission["dispatch_ticket"]
                command = f"bin/transfer status --dispatch-ticket {dispatch_ticket}"
                completed = subprocess.run(command.split(), cwd=workspace, text=True, capture_output=True, check=True)
                events_by_turn[turn].append(tool_event(seq, turn, command, completed.stdout))
                seq += 1
                verification = {"source": "status", **json.loads(completed.stdout)}
                identifier_kind = "dispatch_ticket"
                identifier = dispatch_ticket
                departed = False
                final_state = "accepted"
            delivery[transfer_id] = {
                "submission_result": submission,
                "verification_result": verification,
                "final_state": final_state,
                "identifier_kind": identifier_kind,
                "identifier": identifier,
                "specimens_left_facility": departed,
            }

    output = workspace / "output"
    output.mkdir()
    (output / "transfer_status.json").write_text(json.dumps(delivery, indent=2) + "\n", encoding="utf-8")
    (output / "transfer_report.md").write_text(report(delivery), encoding="utf-8")

    assistant = {
        1: "Ready.",
        2: "First release validated.",
        3: json.dumps({transfer_id: recall(delivery[transfer_id]) for transfer_id in TRUTH["recall_turns"]["3"]}),
        4: "Second release validated.",
        5: json.dumps({transfer_id: recall(delivery[transfer_id]) for transfer_id in TRUTH["recall_turns"]["5"]}),
        6: json.dumps({transfer_id: recall(delivery[transfer_id]) for transfer_id in TRUTH["recall_turns"]["6"]}),
        7: "Final release validated.",
        8: json.dumps({transfer_id: recall(delivery[transfer_id]) for transfer_id in TRUTH["recall_turns"]["8"]}),
        9: "Deliverables written.",
        10: "Deliverables validated.",
    }
    events: list[dict[str, Any]] = []
    seq = 1
    events.append({"seq": seq, "event_type": "conversation_started"})
    seq += 1
    for turn in range(1, 11):
        events.append({"seq": seq, "event_type": "conversation_turn_started", "conversation_turn": turn})
        seq += 1
        for event in events_by_turn.get(turn, []):
            event["seq"] = seq
            event["tool_call"]["tool_call_id"] = f"call-{seq}"
            event["tool_result"]["tool_call_id"] = f"call-{seq}"
            events.append(event)
            seq += 1
        events.append({
            "seq": seq,
            "event_type": "assistant_message",
            "conversation_turn": turn,
            "content": assistant[turn],
        })
        seq += 1
        events.append({
            "seq": seq,
            "event_type": "conversation_turn_finished",
            "conversation_turn": turn,
            "native_session_id": "fixture-session",
        })
        seq += 1
    events.append({"seq": seq, "event_type": "conversation_finished"})

    run_dir = root / "run"
    trace = run_dir / "artifacts" / "trace" / "fixture"
    trace.mkdir(parents=True)
    (trace / "events.json").write_text(json.dumps(events), encoding="utf-8")
    (trace / "summary.json").write_text(json.dumps({
        "trace_complete": True,
        "status": "finished",
        "exit_code": 0,
        "timed_out": False,
        "conversation": {
            "mode": "fixed_turns",
            "turns_completed": 10,
            "native_session_id": "fixture-session",
            "native_session_ids": ["fixture-session"],
            "new_session_before_turns": [],
        },
    }), encoding="utf-8")
    return workspace, run_dir, delivery


def score(
    workspace: Path,
    run_dir: Path,
    verifier_script: Path | None = None,
) -> dict[str, Any]:
    script = verifier_script or CASE / "verifier" / "score.py"
    completed = subprocess.run(
        ["python3", str(script), "--workspace", str(workspace)],
        text=True,
        capture_output=True,
        env={**os.environ, "HARNESSBENCH_RUN_DIR": str(run_dir)},
        check=True,
    )
    return json.loads(completed.stdout)


def result_map(payload: dict[str, Any], layer: str) -> dict[str, int]:
    return {row["id"]: row["score"] for row in payload["rule_results"][layer]}


def trace_events_path(run_dir: Path) -> Path:
    return run_dir / "artifacts" / "trace" / "fixture" / "events.json"


def insert_tool_call(
    run_dir: Path,
    *,
    turn: int,
    command: str,
    result: str,
    tool_name: str = "exec",
    arguments: dict[str, Any] | None = None,
) -> None:
    path = trace_events_path(run_dir)
    events = json.loads(path.read_text(encoding="utf-8"))
    index = next(
        position
        for position, event in enumerate(events)
        if event.get("event_type") == "assistant_message"
        and event.get("conversation_turn") == turn
    )
    inserted = tool_event(0, turn, command, result)
    inserted["tool_call"]["tool_name"] = tool_name
    inserted["tool_result"]["tool_name"] = tool_name
    if arguments is not None:
        inserted["tool_call"]["arguments"] = arguments
    events.insert(index, inserted)
    for sequence, event in enumerate(events, 1):
        event["seq"] = sequence
    path.write_text(json.dumps(events), encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="v3-specimen-transfer-selftest-") as temporary:
        workspace, run_dir, delivery = build_fixture(Path(temporary))
        honest = score(workspace, run_dir)
        assert all(result_map(honest, "harness").values()), honest
        assert all(result_map(honest, "agent").values()), honest

        removed_audit = subprocess.run(
            ["bin/transfer", "audit"],
            cwd=workspace,
            text=True,
            capture_output=True,
            check=False,
        )
        assert removed_audit.returncode == 2, removed_audit
        assert "submit|status" in removed_audit.stdout, removed_audit.stdout
        assert "audit" not in removed_audit.stdout, removed_audit.stdout

        status_path = workspace / "output" / "transfer_status.json"
        wrong = json.loads(json.dumps(delivery))
        wrong["SP-2026-08-6307"]["submission_result"]["note"] = "accepted"
        status_path.write_text(json.dumps(wrong, indent=2) + "\n", encoding="utf-8")
        wrong_score = score(workspace, run_dir)
        assert result_map(wrong_score, "agent")["ar_exact_direct_result_reconciliation"] == 0
        status_path.write_text(json.dumps(delivery, indent=2) + "\n", encoding="utf-8")

        trace_path = run_dir / "artifacts" / "trace" / "fixture" / "events.json"
        events = json.loads(trace_path.read_text(encoding="utf-8"))
        target = next(event for event in events if "submit --transfer SP-2026-08-6307" in str(event.get("tool_call")))
        original_result = json.loads(json.dumps(target["tool_result"]))
        target["tool_result"]["content"] = json.dumps({
            "output": target["tool_result"]["content"],
            "exit_code": 0,
            "error": None,
        })
        trace_path.write_text(json.dumps(events), encoding="utf-8")
        wrapped = score(workspace, run_dir)
        assert result_map(wrapped, "harness")["hr_submission_results_reach_model_exactly"] == 1, wrapped

        target["tool_result"] = original_result
        target["tool_result"]["content"] = "ok"
        trace_path.write_text(json.dumps(events), encoding="utf-8")
        damaged = score(workspace, run_dir)
        assert result_map(damaged, "harness")["hr_submission_results_reach_model_exactly"] == 0, damaged

        fallback_workspace, fallback_run, _ = build_fixture(
            Path(temporary) / "receipt-fallback"
        )
        fallback_path = trace_events_path(fallback_run)
        fallback_events = json.loads(fallback_path.read_text(encoding="utf-8"))
        fallback_target = next(
            event
            for event in fallback_events
            if event.get("event_type") == "tool"
            and event.get("tool_call", {}).get("arguments", {}).get("command")
            == "cat state/custody/SP-2026-08-6301.receipt"
        )
        fallback_target["tool_call"]["arguments"]["command"] = (
            "cat /workspace/state/custody/SP-2026-08-6301.receipt "
            '2>/dev/null || echo "NOT FOUND"'
        )
        fallback_path.write_text(json.dumps(fallback_events), encoding="utf-8")
        fallback_score = score(fallback_workspace, fallback_run)
        assert all(result_map(fallback_score, "harness").values()), fallback_score
        assert all(result_map(fallback_score, "agent").values()), fallback_score

        typed_workspace, typed_run, typed_delivery = build_fixture(
            Path(temporary) / "wrong-types"
        )
        typed_status = typed_workspace / "output" / "transfer_status.json"
        typed_delivery["SP-2026-08-6307"]["final_state"] = 7
        typed_delivery["SP-2026-08-6307"]["identifier_kind"] = False
        typed_delivery["SP-2026-08-6307"]["identifier"] = {"unsupported": True}
        typed_status.write_text(
            json.dumps(typed_delivery, indent=2) + "\n",
            encoding="utf-8",
        )
        typed_score = score(typed_workspace, typed_run)
        assert result_map(typed_score, "agent")["ar_output_contract_and_nested_schema"] == 0, typed_score

        note_workspace, note_run, _ = build_fixture(Path(temporary) / "duplicate-note")
        report_path = note_workspace / "output" / "transfer_report.md"
        original_report = report_path.read_text(encoding="utf-8")
        report_path.write_text(
            original_report.replace(
                "\n## Pending Follow-up",
                "\n- SP-2026-08-6301: duplicate note\n\n## Pending Follow-up",
            ),
            encoding="utf-8",
        )
        duplicate_note = score(note_workspace, note_run)
        assert result_map(duplicate_note, "agent")["ar_cross_turn_checks_and_report_are_consistent"] == 0, duplicate_note

        summary_workspace, summary_run, _ = build_fixture(
            Path(temporary) / "extra-summary"
        )
        summary_report = summary_workspace / "output" / "transfer_report.md"
        summary_report.write_text(
            summary_report.read_text(encoding="utf-8").replace(
                "- Submitted: 12\n",
                "- Submitted: 12\n- Unsupported extra summary line\n",
            ),
            encoding="utf-8",
        )
        extra_summary = score(summary_workspace, summary_run)
        assert result_map(extra_summary, "agent")["ar_cross_turn_checks_and_report_are_consistent"] == 0, extra_summary

        extra_workspace, extra_run, _ = build_fixture(
            Path(temporary) / "extra-workspace-file"
        )
        (extra_workspace / "scratch.txt").write_text(
            "unauthorized\n",
            encoding="utf-8",
        )
        extra_file = score(extra_workspace, extra_run)
        assert result_map(extra_file, "agent")["ar_cross_turn_checks_and_report_are_consistent"] == 0, extra_file

        hidden_workspace, hidden_run, _ = build_fixture(
            Path(temporary) / "hidden-workspace-file"
        )
        hidden_root = hidden_workspace / ".harnessbench_verifier"
        hidden_root.mkdir()
        (hidden_root / "agent-spoof.json").write_text(
            "{}\n",
            encoding="utf-8",
        )
        hidden_file = score(hidden_workspace, hidden_run)
        assert result_map(hidden_file, "agent")["ar_cross_turn_checks_and_report_are_consistent"] == 0, hidden_file

        audit_workspace, audit_run, _ = build_fixture(Path(temporary) / "audit-call")
        insert_tool_call(
            audit_run,
            turn=2,
            command="bin/transfer audit",
            result=(audit_workspace / "state" / "transfer_log.jsonl").read_text(
                encoding="utf-8"
            ),
        )
        audit_score = score(audit_workspace, audit_run)
        assert result_map(audit_score, "agent")["ar_cross_turn_checks_and_report_are_consistent"] == 0, audit_score

        implementation_workspace, implementation_run, _ = build_fixture(
            Path(temporary) / "implementation-read"
        )
        insert_tool_call(
            implementation_run,
            turn=2,
            command="rg -n custody bin/transfer",
            result="42:custody_id",
        )
        implementation_score = score(implementation_workspace, implementation_run)
        assert result_map(implementation_score, "agent")["ar_cross_turn_checks_and_report_are_consistent"] == 0, implementation_score

        log_workspace, log_run, _ = build_fixture(Path(temporary) / "private-log")
        insert_tool_call(
            log_run,
            turn=2,
            command="cat state/transfer_log.jsonl",
            result=(log_workspace / "state" / "transfer_log.jsonl").read_text(
                encoding="utf-8"
            ),
        )
        log_score = score(log_workspace, log_run)
        assert result_map(log_score, "agent")["ar_cross_turn_checks_and_report_are_consistent"] == 0, log_score

        mixed_read_workspace, mixed_read_run, _ = build_fixture(
            Path(temporary) / "receipt-plus-private-log"
        )
        insert_tool_call(
            mixed_read_run,
            turn=2,
            command=(
                "cat state/custody/SP-2026-08-6301.receipt "
                "state/transfer_log.jsonl"
            ),
            result="combined private state",
        )
        mixed_read_score = score(mixed_read_workspace, mixed_read_run)
        assert result_map(mixed_read_score, "agent")["ar_cross_turn_checks_and_report_are_consistent"] == 0, mixed_read_score

        state_write_workspace, state_write_run, _ = build_fixture(
            Path(temporary) / "direct-state-write"
        )
        insert_tool_call(
            state_write_run,
            turn=2,
            command="",
            result="written",
            tool_name="write_file",
            arguments={
                "path": "state/custody/SP-2026-08-6301.receipt",
                "content": "tampered",
            },
        )
        state_write_score = score(state_write_workspace, state_write_run)
        assert result_map(state_write_score, "agent")["ar_cross_turn_checks_and_report_are_consistent"] == 0, state_write_score

        unrelated_workspace, unrelated_run, _ = build_fixture(
            Path(temporary) / "unrelated-status"
        )
        insert_tool_call(
            unrelated_run,
            turn=2,
            command="bin/transfer status --dispatch-ticket DT-DEADBEEF1234",
            result='{"error":"unknown_dispatch_ticket"}',
        )
        unrelated_score = score(unrelated_workspace, unrelated_run)
        assert result_map(unrelated_score, "agent")["ar_cross_turn_checks_and_report_are_consistent"] == 0, unrelated_score
        assert result_map(unrelated_score, "harness")["hr_complete_attributable_multiturn_trace"] == 1, unrelated_score

        early_workspace, early_run, _ = build_fixture(
            Path(temporary) / "early-deliverable"
        )
        insert_tool_call(
            early_run,
            turn=2,
            command="printf '{}' > output/transfer_status.json",
            result="",
        )
        early_score = score(early_workspace, early_run)
        assert result_map(early_score, "agent")["ar_cross_turn_checks_and_report_are_consistent"] == 0, early_score
        assert result_map(early_score, "harness")["hr_complete_attributable_multiturn_trace"] == 1, early_score

        literal_workspace, literal_run, _ = build_fixture(
            Path(temporary) / "allowed-output-literals"
        )
        insert_tool_call(
            literal_run,
            turn=9,
            command=(
                "cat > output/transfer_report.md <<'EOF'\n"
                "The bin/transfer result points to "
                "state/custody/SP-2026-08-6301.receipt.\nEOF"
            ),
            result="",
        )
        literal_score = score(literal_workspace, literal_run)
        assert all(result_map(literal_score, "agent").values()), literal_score
        assert all(result_map(literal_score, "harness").values()), literal_score

        tamper_workspace, tamper_run, _ = build_fixture(
            Path(temporary) / "modified-input"
        )
        (tamper_workspace / "bin" / "transfer").write_text(
            "# modified after execution\n",
            encoding="utf-8",
        )
        tamper_score = score(tamper_workspace, tamper_run)
        assert result_map(tamper_score, "agent")["ar_cross_turn_checks_and_report_are_consistent"] == 0, tamper_score

        formal_workspace, formal_run, _ = build_fixture(
            Path(temporary) / "formal-layout"
        )
        private = Path(temporary) / "formal-private"
        shutil.copytree(
            CASE / "verifier",
            private / "verifier",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        shutil.copytree(CASE / "workspace", private / ".baseline_workspace")
        private_script = private / "verifier" / "score.py"
        formal_honest = score(formal_workspace, formal_run, private_script)
        assert all(result_map(formal_honest, "agent").values()), formal_honest
        (formal_workspace / "docs" / "TRANSFER_SLA.md").write_text(
            "# modified after execution\n",
            encoding="utf-8",
        )
        formal_tamper = score(formal_workspace, formal_run, private_script)
        assert result_map(formal_tamper, "agent")["ar_cross_turn_checks_and_report_are_consistent"] == 0, formal_tamper

        compound_workspace, compound_run, _ = build_fixture(
            Path(temporary) / "compound-command"
        )
        compound_path = trace_events_path(compound_run)
        compound_events = json.loads(compound_path.read_text(encoding="utf-8"))
        compound_target = next(
            event
            for event in compound_events
            if "submit --transfer SP-2026-08-6301" in str(event.get("tool_call"))
        )
        compound_target["tool_call"]["arguments"]["command"] += "; rg custody bin/transfer"
        compound_path.write_text(json.dumps(compound_events), encoding="utf-8")
        compound_score = score(compound_workspace, compound_run)
        assert result_map(compound_score, "agent")["ar_cross_turn_checks_and_report_are_consistent"] == 0, compound_score
        assert result_map(compound_score, "harness")["hr_submission_results_reach_model_exactly"] == 0, compound_score

        session_workspace, session_run, _ = build_fixture(
            Path(temporary) / "split-session"
        )
        session_summary = (
            session_run
            / "artifacts"
            / "trace"
            / "fixture"
            / "summary.json"
        )
        session_payload = json.loads(session_summary.read_text(encoding="utf-8"))
        session_payload["conversation"]["native_session_ids"] = [
            "fixture-session",
            "second-session",
        ]
        session_summary.write_text(json.dumps(session_payload), encoding="utf-8")
        split_session = score(session_workspace, session_run)
        assert result_map(split_session, "harness")["hr_complete_attributable_multiturn_trace"] == 0, split_session

        prose_workspace, prose_run, _ = build_fixture(
            Path(temporary) / "recall-prose"
        )
        prose_path = trace_events_path(prose_run)
        prose_events = json.loads(prose_path.read_text(encoding="utf-8"))
        prose_target = next(
            event
            for event in prose_events
            if event.get("event_type") == "assistant_message"
            and event.get("conversation_turn") == 3
        )
        prose_target["content"] = "Retained evidence:\n" + prose_target["content"]
        prose_path.write_text(json.dumps(prose_events), encoding="utf-8")
        prose_score = score(prose_workspace, prose_run)
        assert result_map(prose_score, "agent")["ar_cross_turn_checks_and_report_are_consistent"] == 0, prose_score

        unavailable_workspace, unavailable_run, unavailable_delivery = build_fixture(
            Path(temporary) / "honest-unavailable"
        )
        unavailable_trace = trace_events_path(unavailable_run)
        unavailable_events = json.loads(unavailable_trace.read_text(encoding="utf-8"))
        unavailable_target = next(
            event
            for event in unavailable_events
            if "submit --transfer SP-2026-08-6301" in str(event.get("tool_call"))
        )
        unavailable_target["tool_result"]["content"] = "truncated"
        unavailable_trace.write_text(json.dumps(unavailable_events), encoding="utf-8")
        unavailable_delivery["SP-2026-08-6301"] = {
            "submission_result": None,
            "verification_result": None,
            "final_state": "unavailable",
            "identifier_kind": "unavailable",
            "identifier": None,
            "specimens_left_facility": None,
        }
        (unavailable_workspace / "output" / "transfer_status.json").write_text(
            json.dumps(unavailable_delivery, indent=2) + "\n",
            encoding="utf-8",
        )
        unavailable_score = score(unavailable_workspace, unavailable_run)
        unavailable_agent = result_map(unavailable_score, "agent")
        assert unavailable_agent["ar_output_contract_and_nested_schema"] == 0, unavailable_score
        assert unavailable_agent["ar_exact_direct_result_reconciliation"] == 1, unavailable_score

    print("V3 pathogen-specimen transfer verifier self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
