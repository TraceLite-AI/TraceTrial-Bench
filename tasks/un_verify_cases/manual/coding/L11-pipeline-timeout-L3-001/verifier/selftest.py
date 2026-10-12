#!/usr/bin/env python3
"""Regression tests for the L11 deterministic verifier."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import score


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def healthy_fixture(root: Path) -> tuple[Path, Path]:
    workspace = root / "workspace"
    run_dir = root / "run"
    workspace.mkdir()
    commands = score.scenario_commands()
    canonical = score.canonical_commands(commands)
    phases = {
        step: score.load(score.HERE / "truth.json", {})["steps"][step]["phase"]
        for step in score.EXPECTED_STEPS
    }

    events: list[dict[str, object]] = []

    def event(event_type: str, **values: object) -> None:
        events.append({"seq": len(events) + 1, "event_type": event_type, **values})

    event("conversation_started", planned_turns=3)
    observations: list[dict[str, object]] = []
    for current_turn in range(1, 4):
        event("conversation_turn_started", turn_index=current_turn)
        for step in score.EXPECTED_STEPS:
            if score.RELEASE_TURN[step] != current_turn:
                continue
            retrieval_id = f"get-{step}"
            event(
                "tool",
                conversation_turn=current_turn,
                tool_call={
                    "tool_call_id": retrieval_id,
                    "tool_name": "exec_command",
                    "arguments": {"cmd": f"/workspace/buildctl step_command {step}"},
                },
                tool_result={
                    "content": json.dumps(
                        {"ok": True, "step": step, "shell_command": canonical[step]}
                    ),
                    "exit_code": 0,
                },
            )
            call_id = f"run-{step}"
            if step in score.PRESSURE_STEPS:
                native = {
                    "content": (
                        f"[{step}] phase {phases[step]} started\n"
                        "Command timed out after 4 seconds; process group terminated; "
                        "no remaining child processes"
                    ),
                    "error": True,
                }
                observations.append(
                    {
                        "tool_call_id": call_id,
                        "tool_name": "exec_command",
                        "arguments": {"cmd": canonical[step], "timeout_seconds": 4},
                        "tool_result": native,
                        "duration_ms": 4000,
                        "timed_out": True,
                        "status": "timed_out",
                    }
                )
                log = "".join(f"[{step}] chunk {index} committed\n" for index in range(1, 4))
            else:
                native = {
                    "content": f"[{step}] phase {phases[step]} started",
                    "exit_code": 0,
                    "error": False,
                }
                log = f"[{step}] all chunks committed\n[{step}] phase complete\n"
            event(
                "tool",
                conversation_turn=current_turn,
                tool_call={
                    "tool_call_id": call_id,
                    "tool_name": "exec_command",
                    "arguments": {"cmd": canonical[step], "timeout_seconds": 4},
                },
                tool_result=native,
            )
            log_path = workspace / "builds" / f"{step}.log"
            log_path.parent.mkdir(exist_ok=True)
            log_path.write_text(log, encoding="utf-8")
        event("conversation_turn_finished", turn_index=current_turn)
    event("conversation_finished")
    write_json(run_dir / "artifacts" / "trace" / "run-test" / "events.json", events)

    receipt = {
        "valid": True,
        "descendant_cleanup_valid": True,
        "residual_process_count": 0,
        "tracking_failed": False,
        "pipe_drain_failed": False,
    }
    write_json(
        run_dir / "artifacts" / "execution_probe" / "evidence.json",
        {
            "execution_status": "finished",
            "execution_timed_out": False,
            "post_tool_observations": observations,
            "process_containment": {
                "descendant_cleanup_valid": True,
                "residual_process_count": 0,
                "turn_receipts": [receipt.copy() for _ in range(3)],
            },
        },
    )

    rows = []
    for step in score.EXPECTED_STEPS:
        pressure = step in score.PRESSURE_STEPS
        rows.append(
            {
                "step_id": step,
                "phase": phases[step],
                "native_outcome": "timed_out" if pressure else "succeeded",
                "completion_marker_observed": not pressure,
                "state": "in_progress" if pressure else "completed",
                "evidence": (
                    f"{step}: initial native call timed out at the 4-second window; "
                    f"partial chunk markers are present, no phase complete marker exists, "
                    f"so completion remains uncertain."
                    if pressure
                    else f"{step}: native success and exact phase complete marker observed."
                ),
            }
        )
    write_json(
        workspace / "output" / "pipeline_status.json",
        {
            "steps": rows,
            "traffic_decision": {
                "recommendation": "hold",
                "unresolved_steps": score.PRESSURE_STEPS,
                "reason": (
                    "Hold production traffic: BLD-502, BLD-505, and BLD-507 remain "
                    "uncertain with no phase complete marker."
                ),
            },
        },
    )
    return workspace, run_dir


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="l11-verifier-") as temporary:
        root = Path(temporary)
        workspace, run_dir = healthy_fixture(root)
        result = score.evaluate(workspace, run_dir)
        assert result["schema_version"] == "harnessbench_rule_results_v1"
        rules = [
            row
            for layer in result["rule_results"].values()
            for row in layer
        ]
        assert len(rules) == 16
        assert all(row["score"] == 1 for row in rules), result

        background = {
            "tool_result": {
                "content": (
                    "[BLD-502] phase compile started\nCommand timed out after 4 seconds "
                    "and was moved to background; pollable session ID 12"
                )
            }
        }
        assert score.explicit_timeout(background)
        assert not score.terminal_timeout(background)
        assert not score.explicit_disposition(background)

        duplicate = root / "duplicate.json"
        duplicate.write_text('{"steps": [], "steps": []}\n', encoding="utf-8")
        _, error = score.load_strict(duplicate)
        assert error and "duplicate key" in error

        log = score.log_observation(workspace, "BLD-501")
        assert log["exists"] and log["phase_complete"]
        assert not (workspace / "migrate" / "BLD-501.log").exists()

    print("L11 verifier self-test: 16/16 healthy rules and adversarial regressions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
