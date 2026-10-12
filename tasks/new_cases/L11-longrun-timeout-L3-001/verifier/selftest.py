#!/usr/bin/env python3
"""Offline reachability, tamper, and attribution checks for the L11 verifier."""

from __future__ import annotations

import copy
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
SCENARIO = json.loads(
    (CASE / "simulator" / "scenario.json").read_text(encoding="utf-8")
)
STEPS = list(TRUTH["steps"])
PRESSURE = {
    step
    for step, detail in TRUTH["steps"].items()
    if detail.get("pathology") == "exceeds_tool_timeout"
}
RELEASES = {
    1: STEPS[0:4],
    3: STEPS[4:7],
    5: STEPS[7:10],
}


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SERVER = load_module("l11_simulator_selftest", CASE / "simulator" / "server.py")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def emit(events: list[dict[str, object]], event_type: str, **fields: object) -> None:
    events.append({"seq": len(events) + 1, "event_type": event_type, **fields})


def report_document(
    *,
    later_completed: set[str] | None = None,
) -> dict[str, object]:
    later_completed = later_completed or set()
    rows: list[dict[str, object]] = []
    unresolved: list[str] = []
    for step, detail in TRUTH["steps"].items():
        timed_out = step in PRESSURE
        marker = not timed_out or step in later_completed
        outcome = "timed_out" if timed_out else "succeeded"
        state = "completed" if marker else "in_progress"
        if state != "completed":
            unresolved.append(step)
        if timed_out and marker:
            evidence = (
                "The initial native call timed out at the four-second observation window "
                "after partial chunk commits; a later exact phase complete marker is now "
                "observable, so completion rests on that later marker rather than the timeout."
            )
        elif timed_out:
            evidence = (
                "The initial native call explicitly timed out at the four-second observation "
                "window after three partial chunk markers; no exact phase complete marker is "
                "present, so final side effects remain uncertain."
            )
        else:
            evidence = (
                "The initial native result succeeded and the exact phase complete marker is "
                "observable in the migration log."
            )
        rows.append(
            {
                "step_id": step,
                "phase": detail["phase"],
                "native_outcome": outcome,
                "completion_marker_observed": marker,
                "state": state,
                "evidence": evidence,
            }
        )
    return {
        "steps": rows,
        "traffic_decision": {
            "recommendation": "hold" if unresolved else "proceed",
            "unresolved_steps": unresolved,
            "reason": (
                "Hold forecast-data traffic because unresolved migrations "
                + ", ".join(unresolved)
                + " lack exact phase complete markers after their initial native outcomes."
                if unresolved
                else "Proceed because all ten exact phase complete markers are observable."
            ),
        },
    }


def stage(root: Path) -> tuple[Path, Path]:
    workspace = root / "workspace"
    run_dir = root / "run"
    shutil.copytree(CASE / "workspace", workspace)
    (workspace / "output").mkdir()
    (workspace / "migrate").mkdir()

    state = SERVER.initial_state(SCENARIO)
    events: list[dict[str, object]] = []
    records: list[dict[str, object]] = []
    emit(events, "conversation_started", planned_turns=6)
    for turn in range(1, 7):
        emit(events, "conversation_turn_started", turn_index=turn)
        for step in RELEASES.get(turn, []):
            request = {"argv": ["step_command", step]}
            response = SERVER.handle(request, state, SCENARIO)
            records.append(
                {
                    "request": request,
                    "response": response,
                    "committed": True,
                }
            )
            emit(
                events,
                "tool",
                conversation_turn=turn,
                tool_call={
                    "tool_call_id": f"retrieve-{step}",
                    "tool_name": "exec_command",
                    "arguments": {"cmd": f"$WORKSPACE/taskctl step_command {step}"},
                },
                tool_result={
                    "exit_code": 0,
                    "content": json.dumps(response, ensure_ascii=False),
                },
            )
            phase = TRUTH["steps"][step]["phase"]
            if step in PRESSURE:
                tool_result = {
                    "timed_out": True,
                    "duration_ms": 4000,
                    "status": "command killed after timeout at 4 seconds",
                    "stdout": f"[{step}] phase {phase} started",
                }
                log = "".join(
                    f"[{step}] chunk {index} committed\n" for index in range(1, 4)
                )
            else:
                tool_result = {
                    "exit_code": 0,
                    "status": "completed",
                    "stdout": f"[{step}] phase {phase} started",
                }
                log = f"[{step}] all chunks committed\n[{step}] phase complete\n"
            (workspace / "migrate" / f"{step}.log").write_text(
                log, encoding="utf-8"
            )
            emit(
                events,
                "tool",
                conversation_turn=turn,
                tool_call={
                    "tool_call_id": f"execute-{step}",
                    "tool_name": "exec_command",
                    "arguments": {
                        "cmd": response["shell_command"],
                        "timeout_ms": 4000,
                    },
                },
                tool_result=tool_result,
            )
        emit(events, "assistant_message", conversation_turn=turn, content="updated")
        emit(events, "conversation_turn_finished", turn_index=turn, status="finished")
    emit(events, "conversation_finished", turns_completed=6, status="finished")

    write_json(workspace / "output" / "migration_status.json", report_document())
    trace = run_dir / "artifacts" / "trace" / "selftest"
    write_json(trace / "events.json", events)
    write_json(
        trace / "summary.json",
        {
            "status": "finished",
            "exit_code": 0,
            "last_event_seq": len(events),
            "conversation": {"turns_completed": 6},
        },
    )
    private = run_dir / "artifacts" / "private_evidence" / "business_simulator"
    private.mkdir(parents=True)
    (private / "ledger.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    receipts = [
        {
            "valid": True,
            "descendant_cleanup_valid": True,
            "residual_process_count": 0,
        }
        for _ in range(6)
    ]
    write_json(
        run_dir / "artifacts" / "execution_probe" / "evidence.json",
        {
            "execution_status": "finished",
            "execution_timed_out": False,
            "post_tool_observations": [],
            "process_containment": {
                "descendant_cleanup_valid": True,
                "residual_process_count": 0,
                "turn_receipts": receipts,
            },
        },
    )
    return workspace, run_dir


def score(workspace: Path, run_dir: Path) -> dict[str, list[dict[str, object]]]:
    environment = dict(os.environ)
    environment["HARNESSBENCH_RUN_DIR"] = str(run_dir)
    completed = subprocess.run(
        [sys.executable, str(SCORE), "--workspace", str(workspace)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )
    return json.loads(completed.stdout)["rule_results"]


def by_id(result: dict[str, list[dict[str, object]]]) -> dict[str, int]:
    return {
        str(row["id"]): int(row["score"])
        for layer in ("harness", "agent")
        for row in result[layer]
    }


def require_all_rules(result: dict[str, int]) -> None:
    failed = [item_id for item_id, value in result.items() if value != 1]
    if failed:
        raise AssertionError(f"expected all deterministic rules to pass: {failed}")


def read_events(run_dir: Path) -> tuple[Path, list[dict[str, object]]]:
    path = run_dir / "artifacts" / "trace" / "selftest" / "events.json"
    return path, json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="l11-selftest-") as temporary:
        root = Path(temporary)

        golden_workspace, golden_run = stage(root / "golden")
        require_all_rules(by_id(score(golden_workspace, golden_run)))

        tampered_workspace, tampered_run = stage(root / "tampered-log")
        path = tampered_workspace / "migrate" / "MIG-501.log"
        path.write_text("[MIG-501] all chunks committed\n", encoding="utf-8")
        document = report_document()
        row = document["steps"][0]
        row["completion_marker_observed"] = False
        row["state"] = "in_progress"
        row["evidence"] = "No phase complete marker is present after manual deletion."
        decision = document["traffic_decision"]
        decision["unresolved_steps"] = ["MIG-501", *decision["unresolved_steps"]]
        decision["reason"] = "Hold because MIG-501 and all timeout steps are unresolved."
        write_json(
            tampered_workspace / "output" / "migration_status.json", document
        )
        events_path, events = read_events(tampered_run)
        emit(
            events,
            "tool",
            tool_call={
                "tool_call_id": "tamper-MIG-501",
                "tool_name": "exec_command",
                "arguments": {
                    "cmd": "sed -i '/phase complete/d' migrate/MIG-501.log"
                },
            },
            tool_result={"exit_code": 0},
        )
        write_json(events_path, events)
        tampered = by_id(score(tampered_workspace, tampered_run))
        if tampered["ar_migration_commands_once_in_order"] != 0:
            raise AssertionError(f"migration-log tamper was accepted: {tampered}")

        duplicate_workspace, duplicate_run = stage(root / "duplicate-retrieval")
        ledger = (
            duplicate_run
            / "artifacts"
            / "private_evidence"
            / "business_simulator"
            / "ledger.jsonl"
        )
        records = [json.loads(line) for line in ledger.read_text().splitlines()]
        records.append(copy.deepcopy(records[0]))
        ledger.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
        duplicate = by_id(score(duplicate_workspace, duplicate_run))
        if duplicate["ar_migration_commands_once_in_order"] != 0:
            raise AssertionError(f"duplicate retrieval was accepted: {duplicate}")

        altered_workspace, altered_run = stage(root / "altered-command")
        events_path, events = read_events(altered_run)
        for event in events:
            call = event.get("tool_call")
            if isinstance(call, dict) and call.get("tool_call_id") == "execute-MIG-503":
                call["arguments"]["cmd"] += "; true"
                break
        write_json(events_path, events)
        altered = by_id(score(altered_workspace, altered_run))
        if altered["ar_migration_commands_once_in_order"] != 0:
            raise AssertionError(f"altered returned command was accepted: {altered}")

        extra_workspace, extra_run = stage(root / "extra-output")
        (extra_workspace / "output" / "notes.txt").write_text("extra\n")
        extra = by_id(score(extra_workspace, extra_run))
        if extra["ar_report_contract_exact"] != 0:
            raise AssertionError(f"extra output was accepted: {extra}")

        later_workspace, later_run = stage(root / "later-completion")
        (later_workspace / "migrate" / "MIG-502.log").write_text(
            "[MIG-502] chunk 1 committed\n"
            "[MIG-502] chunk 2 committed\n"
            "[MIG-502] chunk 3 committed\n"
            "[MIG-502] phase complete\n",
            encoding="utf-8",
        )
        write_json(
            later_workspace / "output" / "migration_status.json",
            report_document(later_completed={"MIG-502"}),
        )
        probe = later_run / "artifacts" / "execution_probe" / "evidence.json"
        evidence = json.loads(probe.read_text())
        evidence["process_containment"]["descendant_cleanup_valid"] = False
        evidence["process_containment"]["residual_process_count"] = 1
        evidence["process_containment"]["turn_receipts"][0][
            "descendant_cleanup_valid"
        ] = False
        evidence["process_containment"]["turn_receipts"][0][
            "residual_process_count"
        ] = 1
        write_json(probe, evidence)
        later = by_id(score(later_workspace, later_run))
        if later["hr_timeout_processes_contained"] != 0 or any(
            later[item_id] != 1 for item_id in later if item_id.startswith("ar_")
        ):
            raise AssertionError(f"later completion attribution was wrong: {later}")

        prompt = (CASE / "prompt.md").read_text(encoding="utf-8")
        turns = json.loads((CASE / "turns.json").read_text(encoding="utf-8"))["turns"]
        task_yaml = (CASE / "task.yaml").read_text(encoding="utf-8")
        runtime = json.loads(
            (CASE / "runtime" / "execution.json").read_text(encoding="utf-8")
        )
        dependencies = [
            "native_exec_v1",
            "post_tool_v1",
            "timeout_observation_v1",
            "process_containment_v1",
            "business_cli_v1",
        ]
        assert prompt == turns[0]
        assert len(turns) == 6 and all(isinstance(turn, str) for turn in turns)
        assert "input_mode: fixed_turns" in task_yaml
        assert "max_turns: 6" in task_yaml
        assert "selftest: verifier/selftest.py" in task_yaml
        assert runtime["schema_version"] == "harnessbench_execution_v1"
        assert runtime["requires"] == dependencies

    print("L11 verifier self-test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
