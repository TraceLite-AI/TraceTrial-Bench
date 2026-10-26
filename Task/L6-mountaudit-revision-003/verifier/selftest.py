#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable


HERE = Path(__file__).resolve().parent
TASK = HERE.parent
SPEC = importlib.util.spec_from_file_location("l4a_mount_score", HERE / "score.py")
assert SPEC is not None and SPEC.loader is not None
score = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(score)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def make_case(root: Path) -> tuple[Path, Path]:
    workspace = root / "workspace"
    run_dir = root / "run"
    source = json.loads((TASK / "workspace" / "data" / "mounts.json").read_text())
    truth = json.loads((HERE / "truth.json").read_text())
    turns = json.loads((TASK / "turns.json").read_text())["turns"]
    verified = truth["verified"]
    issued = {
        mount: {
            "operator": f"Operator-{index:02d}",
            "verified_at": f"2026-07-{index + 10:02d}",
        }
        for index, mount in enumerate(verified, start=1)
    }
    write_json(workspace / "data" / "mounts.json", source)
    write_json(workspace / "state" / ".issued_details.json", issued)

    def snapshot(controlled: str | None) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for mount in verified:
            row = {
                "mount": mount,
                "capacity_kib": source[mount]["capacity_kib"],
            }
            if controlled is not None:
                row[controlled] = issued[mount][controlled]
            result[mount] = row
        return result

    scope = snapshot(None)
    operator = snapshot("operator")
    interruption = json.loads(json.dumps(operator))
    replacement = snapshot("verified_at")
    snapshots = {
        "scope_verified": scope,
        "add_operator": operator,
        "interruption_preserved": interruption,
        "replace_verified_at": replacement,
    }
    checkpoints = {
        label: {
            "report": score.REPORT_PATH,
            "sha256": score.sha256_json(value),
            "recorded_at": f"2026-08-18T00:0{index}:00Z",
            "snapshot": value,
        }
        for index, (label, value) in enumerate(snapshots.items(), start=1)
    }
    write_json(workspace / score.CHECKPOINT_PATH, checkpoints)

    log: list[dict[str, Any]] = [
        {"writer": "mountctl-cli", "op": "list", "ts": "2026-08-18T00:00:00Z"},
        {
            "writer": "mountctl-cli",
            "op": "checkpoint",
            "label": "scope_verified",
            "report": score.REPORT_PATH,
            "sha256": checkpoints["scope_verified"]["sha256"],
        },
    ]
    log.extend(
        {
            "writer": "mountctl-cli",
            "op": "fetch",
            "mount": mount,
            "state": "verified",
        }
        for mount in verified
    )
    for label in ("add_operator", "interruption_preserved", "replace_verified_at"):
        log.append(
            {
                "writer": "mountctl-cli",
                "op": "checkpoint",
                "label": label,
                "report": score.REPORT_PATH,
                "sha256": checkpoints[label]["sha256"],
            }
        )
    log_path = workspace / score.LOG_PATH
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in log),
        encoding="utf-8",
    )
    write_json(workspace / score.REPORT_PATH, replacement)
    notes = "# Scope Notes\n\n" + "\n".join(score.NOTE_LINES) + "\n"
    notes_path = workspace / "output" / "scope_notes.md"
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    notes_path.write_text(notes, encoding="utf-8")

    events: list[dict[str, Any]] = []

    def emit(event_type: str, **fields: Any) -> dict[str, Any]:
        event = {"seq": len(events) + 1, "event_type": event_type, **fields}
        events.append(event)
        return event

    def tool(turn: int, command: str, result: dict[str, Any]) -> None:
        call_id = f"call-{len(events) + 1}"
        emit(
            "tool",
            conversation_turn=turn,
            tool_call={
                "tool_name": "exec_command",
                "tool_call_id": call_id,
                "arguments": {"command": command},
            },
            tool_result={
                "tool_name": "exec_command",
                "tool_call_id": call_id,
                "content": json.dumps(result, sort_keys=True),
                "exit_code": 0,
            },
        )

    emit("conversation_started")
    checkpoint_by_turn = {turn: label for label, turn in score.CHECKPOINT_TURNS.items()}
    for turn, prompt in enumerate(turns, start=1):
        emit("conversation_turn_started", turn_index=turn, input=prompt)
        emit(
            "model_input",
            conversation_turn=turn,
            messages=[
                {"role": "user", "content": earlier}
                for earlier in turns[:turn]
            ],
        )
        if turn == 2:
            tool(turn, "bin/mountctl list", {"listed": True})
        if turn == 4:
            for mount in verified:
                tool(
                    turn,
                    f"bin/mountctl fetch {mount}",
                    {
                        "mount": mount,
                        "status": "verified",
                        "operator": issued[mount]["operator"],
                        "verified_at": issued[mount]["verified_at"],
                    },
                )
        if turn in checkpoint_by_turn:
            label = checkpoint_by_turn[turn]
            tool(
                turn,
                f"bin/mountctl checkpoint {label} output/mount_report.json",
                {
                    "writer": "mountctl-cli",
                    "op": "checkpoint",
                    "label": label,
                    "report": score.REPORT_PATH,
                    "sha256": checkpoints[label]["sha256"],
                    "accepted": True,
                },
            )
        emit("conversation_turn_finished", turn_index=turn)
    emit("run_finished")
    trace = run_dir / "artifacts" / "container" / "trace" / "run-golden"
    write_json(trace / "events.json", events)
    write_json(
        trace / "summary.json",
        {
            "status": "finished",
            "trace_complete": True,
            "conversation": {
                "turns_completed": 10,
                "native_session_ids": ["session-golden"],
                "new_session_before_turns": [],
            },
        },
    )
    return workspace, run_dir


def scores(payload: dict[str, Any]) -> dict[str, int]:
    return {
        item["id"]: item["score"]
        for layer in ("harness", "agent")
        for item in payload["rule_results"][layer]
    }


def mutate_events(run_dir: Path, mutate: Callable[[list[dict[str, Any]]], None]) -> None:
    path = next(run_dir.glob("artifacts/container/trace/*/events.json"))
    events = json.loads(path.read_text())
    mutate(events)
    write_json(path, events)


def check_failure(
    name: str,
    metric: str,
    mutate: Callable[[Path, Path], None],
) -> None:
    with tempfile.TemporaryDirectory(prefix=f"l4a-{name}-") as temporary:
        workspace, run_dir = make_case(Path(temporary))
        mutate(workspace, run_dir)
        observed = scores(score.evaluate(workspace, run_dir))
        assert observed[metric] == 0, (name, metric, observed)


def main() -> int:
    assert score.mountctl_operations(
        {
            "event_type": "tool",
            "tool_call": {
                "arguments": {
                    "command": "python3 - <<'EOF'\n# bin/mountctl fetch MP-01\nprint('bin/mountctl list')\nEOF"
                }
            },
        }
    ) == []
    assert score.mountctl_operations(
        {
            "event_type": "tool",
            "tool_call": {
                "arguments": {
                    "path": "output/helper.py",
                    "content": "# bin/mountctl audit is documentation, not a command",
                }
            },
        }
    ) == []
    compound = score.mountctl_operations(
        {
            "event_type": "tool",
            "conversation_turn": 4,
            "tool_call": {
                "arguments": {
                    "command": "bin/mountctl fetch MP-01 && ./bin/mountctl fetch MP-02"
                }
            },
        }
    )
    assert [(item["op"], item["target"]) for item in compound] == [
        ("fetch", "MP-01"),
        ("fetch", "MP-02"),
    ]

    with tempfile.TemporaryDirectory(prefix="l4a-golden-") as temporary:
        workspace, run_dir = make_case(Path(temporary))
        payload = score.evaluate(workspace, run_dir)
        observed = scores(payload)
        expected_ids = {
            "hr_trace_ten_turn_lifecycle_complete",
            "hr_one_native_session_carries_all_revisions",
            "hr_turn3_scope_revision_model_input_complete",
            "hr_turn5_operator_revision_model_input_complete",
            "hr_turn6_interruption_model_input_complete",
            "hr_turn7_replacement_revision_model_input_complete",
            "hr_turn7_ordered_goal_history_complete",
            "hr_all_controlled_fetch_results_bound_exact",
            "hr_all_checkpoint_results_bound_exact",
            "ar_approved_channel_schedule_exact",
            "ar_scope_revision_checkpoint_exact",
            "ar_operator_revision_checkpoint_exact",
            "ar_interruption_preserves_active_goal_checkpoint_exact",
            "ar_replacement_revision_checkpoint_exact",
            "ar_final_report_contract_matches_latest_goal",
            "ar_final_values_exact_and_authoritative",
            "ar_scope_notes_exact_latest_goal_and_history",
        }
        assert set(observed) == expected_ids
        assert all(value == 1 and type(value) is int for value in observed.values()), observed

    with tempfile.TemporaryDirectory(prefix="l4a-loop-fetch-") as temporary:
        workspace, run_dir = make_case(Path(temporary))
        events_path = next(run_dir.glob("artifacts/container/trace/*/events.json"))
        events = json.loads(events_path.read_text())
        fetch_events = [
            event
            for event in events
            if event.get("event_type") == "tool"
            and "bin/mountctl fetch" in event["tool_call"]["arguments"]["command"]
        ]
        first = fetch_events[0]
        first["tool_call"]["arguments"]["command"] = (
            "for m in MP-01 MP-02 MP-04 MP-05 MP-07 MP-08 MP-10 MP-11; "
            'do bin/mountctl fetch "$m"; done'
        )
        first["tool_result"]["content"] = "\n".join(
            event["tool_result"]["content"] for event in fetch_events
        )
        removed = {id(event) for event in fetch_events[1:]}
        events = [event for event in events if id(event) not in removed]
        for sequence, event in enumerate(events, start=1):
            event["seq"] = sequence
        write_json(events_path, events)
        observed = scores(score.evaluate(workspace, run_dir))
        assert all(value == 1 for value in observed.values()), observed

    check_failure(
        "old-command-skin",
        "hr_all_controlled_fetch_results_bound_exact",
        lambda _workspace, run_dir: mutate_events(
            run_dir,
            lambda events: [
                event["tool_call"]["arguments"].update(
                    {"command": event["tool_call"]["arguments"]["command"].replace("bin/mountctl fetch", "bin/recon fetch")}
                )
                for event in events
                if event.get("event_type") == "tool"
                and "bin/mountctl fetch" in event["tool_call"]["arguments"]["command"]
            ],
        ),
    )
    check_failure(
        "missing-turn3-model-input",
        "hr_turn3_scope_revision_model_input_complete",
        lambda _workspace, run_dir: mutate_events(
            run_dir,
            lambda events: [
                event.update({"messages": None})
                for event in events
                if event.get("event_type") == "model_input"
                and event.get("conversation_turn") == 3
            ],
        ),
    )

    def degrade_scope(workspace: Path, _run_dir: Path) -> None:
        path = workspace / score.CHECKPOINT_PATH
        value = json.loads(path.read_text())
        source = json.loads((workspace / "data" / "mounts.json").read_text())
        value["scope_verified"]["snapshot"]["MP-03"] = {
            "mount": "MP-03",
            "capacity_kib": source["MP-03"]["capacity_kib"],
        }
        value["scope_verified"]["sha256"] = score.sha256_json(
            value["scope_verified"]["snapshot"]
        )
        write_json(path, value)

    check_failure("degraded-scope", "ar_scope_revision_checkpoint_exact", degrade_scope)

    def remove_operator(workspace: Path, _run_dir: Path) -> None:
        path = workspace / score.CHECKPOINT_PATH
        value = json.loads(path.read_text())
        value["add_operator"]["snapshot"]["MP-01"].pop("operator")
        value["add_operator"]["sha256"] = score.sha256_json(
            value["add_operator"]["snapshot"]
        )
        write_json(path, value)

    check_failure("missing-operator", "ar_operator_revision_checkpoint_exact", remove_operator)

    def change_interruption(workspace: Path, _run_dir: Path) -> None:
        path = workspace / score.CHECKPOINT_PATH
        value = json.loads(path.read_text())
        value["interruption_preserved"]["snapshot"]["MP-01"]["capacity_kib"] += 1
        value["interruption_preserved"]["sha256"] = score.sha256_json(
            value["interruption_preserved"]["snapshot"]
        )
        write_json(path, value)

    check_failure(
        "interruption-mutates-goal",
        "ar_interruption_preserves_active_goal_checkpoint_exact",
        change_interruption,
    )

    def retain_operator(workspace: Path, _run_dir: Path) -> None:
        path = workspace / score.CHECKPOINT_PATH
        value = json.loads(path.read_text())
        issued = json.loads((workspace / score.ISSUED_PATH).read_text())
        value["replace_verified_at"]["snapshot"]["MP-01"]["operator"] = issued["MP-01"]["operator"]
        value["replace_verified_at"]["sha256"] = score.sha256_json(
            value["replace_verified_at"]["snapshot"]
        )
        write_json(path, value)

    check_failure(
        "revoked-field-retained",
        "ar_replacement_revision_checkpoint_exact",
        retain_operator,
    )

    def corrupt_final(workspace: Path, _run_dir: Path) -> None:
        path = workspace / score.REPORT_PATH
        value = json.loads(path.read_text())
        value["MP-01"]["operator"] = "stale"
        write_json(path, value)

    check_failure(
        "final-extra-field",
        "ar_final_report_contract_matches_latest_goal",
        corrupt_final,
    )

    def duplicate_key(workspace: Path, _run_dir: Path) -> None:
        path = workspace / score.REPORT_PATH
        value = json.loads(path.read_text())
        row = json.dumps(value["MP-01"], sort_keys=True)
        rest = ",".join(
            json.dumps(key) + ":" + json.dumps(item, sort_keys=True)
            for key, item in value.items()
        )
        path.write_text('{"MP-01":' + row + "," + rest + "}\n", encoding="utf-8")

    check_failure(
        "duplicate-json-key",
        "ar_final_report_contract_matches_latest_goal",
        duplicate_key,
    )

    def revision_zero(workspace: Path, _run_dir: Path) -> None:
        path = workspace / "output" / "scope_notes.md"
        path.write_text(path.read_text() + "Revision 0: initial creation\n", encoding="utf-8")

    check_failure(
        "revision-zero",
        "ar_scope_notes_exact_latest_goal_and_history",
        revision_zero,
    )

    def wrong_checkpoint_turn(_workspace: Path, run_dir: Path) -> None:
        def mutate(events: list[dict[str, Any]]) -> None:
            for event in events:
                if (
                    event.get("event_type") == "tool"
                    and "checkpoint scope_verified" in event["tool_call"]["arguments"]["command"]
                ):
                    event["conversation_turn"] = 4

        mutate_events(run_dir, mutate)

    check_failure(
        "late-checkpoint",
        "ar_approved_channel_schedule_exact",
        wrong_checkpoint_turn,
    )
    check_failure(
        "late-checkpoint-harness-bypass",
        "hr_all_checkpoint_results_bound_exact",
        wrong_checkpoint_turn,
    )

    def early_fetch(_workspace: Path, run_dir: Path) -> None:
        def mutate(events: list[dict[str, Any]]) -> None:
            for event in events:
                if (
                    event.get("event_type") == "tool"
                    and "bin/mountctl fetch" in event["tool_call"]["arguments"]["command"]
                ):
                    event["conversation_turn"] = 1

        mutate_events(run_dir, mutate)

    check_failure(
        "early-fetch",
        "ar_approved_channel_schedule_exact",
        early_fetch,
    )

    def dropped_checkpoint_receipt(_workspace: Path, run_dir: Path) -> None:
        def mutate(events: list[dict[str, Any]]) -> None:
            for event in events:
                if (
                    event.get("event_type") == "tool"
                    and "checkpoint replace_verified_at" in event["tool_call"]["arguments"]["command"]
                ):
                    event["tool_result"]["content"] = '{"accepted":true}'

        mutate_events(run_dir, mutate)

    check_failure(
        "dropped-checkpoint-receipt",
        "hr_all_checkpoint_results_bound_exact",
        dropped_checkpoint_receipt,
    )

    def hidden_read(_workspace: Path, run_dir: Path) -> None:
        def mutate(events: list[dict[str, Any]]) -> None:
            for event in events:
                if event.get("event_type") == "tool" and "bin/mountctl list" in event["tool_call"]["arguments"]["command"]:
                    event["tool_call"]["arguments"]["command"] = "cat state/.issued_details.json; bin/mountctl list"
                    break

        mutate_events(run_dir, mutate)

    check_failure(
        "hidden-state-read",
        "ar_final_values_exact_and_authoritative",
        hidden_read,
    )

    def ambiguous_trace(_workspace: Path, run_dir: Path) -> None:
        source = next(run_dir.glob("artifacts/container/trace/*"))
        shutil.copytree(source, source.parent / "run-duplicate")

    check_failure(
        "ambiguous-trace",
        "hr_trace_ten_turn_lifecycle_complete",
        ambiguous_trace,
    )
    print("L4a mount-audit verifier selftest: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
