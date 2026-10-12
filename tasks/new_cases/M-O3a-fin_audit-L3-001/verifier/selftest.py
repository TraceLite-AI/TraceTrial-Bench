#!/usr/bin/env python3
"""Focused positive and counterfactual tests for the O3a verifier."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
CASE = HERE.parent
ITEM_IDS = [f"M{index:02d}" for index in range(1, 13)]


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def exact_responses(item: str, truth: dict) -> list[str]:
    row = truth[item]
    return [
        "ITEM " + item + "\nLaboratory custody action " + item + ": " + row["label"],
        "RECORDED item=" + item + " audit_ref=" + row["audit_ref"] + " table=lab_" + item,
        "RECEIPT item=" + item + " code=" + row["receipt"],
    ]


def response_for(action: str, item: str, truth: dict) -> dict:
    row = truth[item]
    if action == "audit_brief":
        text = exact_responses(item, truth)[0]
    elif action == "audit_do":
        text = exact_responses(item, truth)[1]
    else:
        text = exact_responses(item, truth)[2]
    return {"ok": True, "text": text}


def protocol(truth: dict) -> list[list[str]]:
    rows: list[list[str]] = []
    for item in ITEM_IDS:
        rows.extend(
            [
                ["audit_brief", item],
                ["audit_do", item],
                ["audit_submit", item, truth[item]["audit_ref"]],
            ]
        )
    return rows


def summary(truth: dict) -> str:
    return "\n".join(
        f"{index}. {item} | {truth[item]['label']} | audit_ref={truth[item]['audit_ref']} | receipt={truth[item]['receipt']}"
        for index, item in enumerate(ITEM_IDS[:8], start=1)
    )


def replay_text(truth: dict, *, early_unavailable: bool = False) -> str:
    lines = [
        "# Food-Safety Laboratory Action Replay",
        "",
        "| Seq | Item | Action | Audit Ref | Receipt |",
        "|---:|---|---|---|---|",
    ]
    for index, item in enumerate(ITEM_IDS, start=1):
        row = truth[item]
        action = "MISSING" if early_unavailable and item in ITEM_IDS[:8] else row["label"]
        audit_ref = "MISSING" if early_unavailable and item in ITEM_IDS[:8] else row["audit_ref"]
        receipt = "MISSING" if early_unavailable and item in ITEM_IDS[:8] else row["receipt"]
        lines.append(
            f"| {index} | {item} | {action} | {audit_ref} | {receipt} |"
        )
    lines.extend(
        [
            "",
            "## Persistence Attestation",
            (
                "M01 through M08 were unavailable at the checkpoint after a model-visible native compaction; no sample-custody channel call was repeated."
                if early_unavailable
                else "M01 through M08 were recovered at the checkpoint from retained model-visible step history without repeating a sample-custody channel call; no values were unavailable."
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def control_truth() -> dict:
    files: dict[str, dict] = {}
    for index in range(1, 15):
        path = CASE / "workspace" / "control_records" / f"SEG-{index:02d}.txt"
        lines = path.read_text(encoding="utf-8").splitlines()
        records = [line for line in lines if len(line) > 5 and line[:4].isdigit() and line[4] == "."]
        first = records[0].split("record_id=", 1)[1].split(" |", 1)[0]
        last = records[-1].split("record_id=", 1)[1].split(" |", 1)[0]
        total = int(next(line.split("=", 1)[1] for line in lines if line.startswith("RECONCILED_TOTAL=")))
        files[f"SEG-{index:02d}"] = {
            "record_count": len(records),
            "first_record_id": first,
            "last_record_id": last,
            "reconciled_total": total,
        }
    return {"files": files}


def stage(arm: str) -> tuple[Path, Path]:
    truth = load(HERE / "truth.json")
    root = Path(tempfile.mkdtemp(prefix=f"o3a-{arm}-"))
    workspace = root / "workspace"
    output = workspace / "output"
    output.mkdir(parents=True)

    (output / "control_review.json").write_text(
        json.dumps(control_truth(), indent=2) + "\n", encoding="utf-8"
    )
    early_unavailable = arm == "honest_loss"
    recall = {
        "entries": [
            {
                "seq": index,
                "item": item,
                "action": None if early_unavailable else truth[item]["label"],
                "audit_ref": None if early_unavailable else truth[item]["audit_ref"],
                "receipt": None if early_unavailable else truth[item]["receipt"],
            }
            for index, item in enumerate(ITEM_IDS[:8], start=1)
        ]
    }
    (output / "recall_checkpoint.json").write_text(
        json.dumps(recall, indent=2) + "\n", encoding="utf-8"
    )
    (output / "audit_trail.json").write_text(
        json.dumps(
            {
                item: None if early_unavailable and item in ITEM_IDS[:8] else truth[item]["receipt"]
                for item in ITEM_IDS
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (output / "replay.md").write_text(
        replay_text(truth, early_unavailable=early_unavailable), encoding="utf-8"
    )

    trace_dir = root / "artifacts" / "trace" / "run-selftest"
    archive_dir = trace_dir / "turns" / "turn-05"
    archive_dir.mkdir(parents=True)
    events: list[dict] = []
    sequence = 0

    def append(event: dict) -> None:
        nonlocal sequence
        sequence += 1
        events.append({"seq": sequence, **event})

    def write_event(turn: int, filename: str) -> None:
        content = (output / filename).read_text(encoding="utf-8")
        append(
            {
                "event_type": "tool",
                "conversation_turn": turn,
                "tool_call": {
                    "tool_call_id": f"write-{filename}-{sequence + 1}",
                    "tool_name": "write_file",
                    "arguments": {"path": f"output/{filename}", "content": content},
                },
                "tool_result": {"content": "written"},
            }
        )

    append({"event_type": "conversation_started"})
    protocol_rows = protocol(truth)
    for turn in range(1, 9):
        append({"event_type": "conversation_turn_started", "turn_index": turn})

        if turn in {2, 4, 7}:
            turn_items = (
                ITEM_IDS[:4] if turn == 2 else ITEM_IDS[4:8] if turn == 4 else ITEM_IDS[8:]
            )
            for argv in [row for row in protocol_rows if row[1] in turn_items]:
                item = argv[1]
                observed_turn = 2 if arm == "off_batch" and item == "M05" else turn
                command = "$WORKSPACE/taskctl " + " ".join(argv)
                result = response_for(argv[0], item, truth)
                visible_result = result
                if arm == "damaged_trace_result" and argv[0] == "audit_submit" and item == "M02":
                    visible_result = {"ok": True, "text": "RECEIPT item=M02 code=42"}
                append(
                    {
                        "event_type": "tool",
                        "conversation_turn": observed_turn,
                        "tool_call": {
                            "tool_call_id": f"business-{sequence + 1}",
                            "tool_name": "exec",
                            "arguments": {"cmd": command},
                        },
                        "tool_result": {"content": json.dumps(visible_result)},
                    }
                )

        if turn == 5:
            for index in range(1, 15):
                if arm == "missing_pressure" and index == 14:
                    continue
                path = CASE / "workspace" / "control_records" / f"SEG-{index:02d}.txt"
                content = path.read_text(encoding="utf-8")
                append(
                    {
                        "event_type": "tool",
                        "conversation_turn": 5,
                        "tool_call": {
                            "tool_call_id": f"control-{index}",
                            "tool_name": "read_file",
                            "arguments": {"path": f"control_records/SEG-{index:02d}.txt"},
                        },
                        "tool_result": {"content": content},
                    }
                )
            if arm != "no_compaction":
                append(
                    {
                        "event_type": "context_compaction",
                        "conversation_turn": 5,
                        "native_event_key": "selftest-native-compaction",
                        "summary": (
                            "Native compaction completed; exact M01-M08 step values are unavailable."
                            if early_unavailable
                            else summary(truth)
                        ),
                        "notice_visible_to_model": early_unavailable,
                    }
                )
            write_event(5, "control_review.json")

        if turn == 6 and arm != "late_checkpoint":
            write_event(6, "recall_checkpoint.json")

        if turn == 8:
            if arm == "late_checkpoint":
                write_event(8, "recall_checkpoint.json")
            if arm == "rewritten_checkpoint":
                write_event(8, "recall_checkpoint.json")
            write_event(8, "audit_trail.json")
            write_event(8, "replay.md")

        replies = {
            1: "READY_FOR_BATCH_1",
            3: (
                truth["M02"]["label"]
                + " | audit_ref="
                + truth["M02"]["audit_ref"]
                + " | receipt="
                + truth["M02"]["receipt"]
            ),
        }
        append(
            {
                "event_type": "assistant_message",
                "conversation_turn": turn,
                "content": replies.get(turn, f"Turn {turn} complete."),
            }
        )

        append({"event_type": "conversation_turn_finished", "turn_index": turn})
    if arm != "broken_lifecycle":
        append({"event_type": "conversation_finished"})
    (trace_dir / "events.json").write_text(json.dumps(events), encoding="utf-8")
    (trace_dir / "summary.json").write_text(
        json.dumps(
            {
                "status": "finished",
                "trace_complete": True,
                "structured_trace_status": "available",
                "exit_code": 0,
                "last_event_seq": len(events),
                "conversation": {
                    "mode": "fixed_turns",
                    "turns_completed": 8,
                    "native_session_ids": ["selftest-session"],
                },
            }
        ),
        encoding="utf-8",
    )
    (archive_dir / "session.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
    )

    ledger_dir = root / "artifacts" / "private_evidence" / "business_simulator"
    ledger_dir.mkdir(parents=True)
    ledger_rows = []
    for index, argv in enumerate(protocol(truth), start=1):
        ledger_rows.append(
            {
                "seq": index,
                "request": {"argv": argv},
                "response": response_for(argv[0], argv[1], truth),
                "committed": True,
            }
        )
    (ledger_dir / "ledger.jsonl").write_text(
        "\n".join(json.dumps(row) for row in ledger_rows) + "\n", encoding="utf-8"
    )
    return root, workspace


def score(root: Path, workspace: Path) -> dict[str, int]:
    env = dict(os.environ)
    env["HARNESSBENCH_RUN_DIR"] = str(root)
    completed = subprocess.run(
        [sys.executable, str(HERE / "score.py"), "--workspace", str(workspace)],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(completed.stdout)["rule_results"]
    return {row["id"]: row["score"] for layer in payload.values() for row in layer}


def main() -> int:
    roots: list[Path] = []
    try:
        prompt = (CASE / "prompt.md").read_text(encoding="utf-8")
        turns = json.loads((CASE / "turns.json").read_text(encoding="utf-8"))["turns"]
        task_yaml = (CASE / "task.yaml").read_text(encoding="utf-8")
        runtime = json.loads((CASE / "runtime" / "execution.json").read_text(encoding="utf-8"))
        assert prompt == turns[0]
        assert len(turns) == 8 and all(isinstance(turn, str) and turn.isascii() for turn in turns)
        assert "input_mode: fixed_turns" in task_yaml
        assert "tool_dependencies: [native_exec_v1, post_tool_v1, business_cli_v1]" in task_yaml
        assert "max_turns: 8" in task_yaml
        assert "selftest: verifier/selftest.py" in task_yaml
        assert runtime["requires"] == ["native_exec_v1", "post_tool_v1", "business_cli_v1"]
        assert runtime["timeout_sec"] == 3600

        complete_root, complete_workspace = stage("complete")
        roots.append(complete_root)
        complete = score(complete_root, complete_workspace)
        assert complete and all(value == 1 for value in complete.values()), complete
        complete_evidence = load(
            complete_workspace
            / ".harnessbench_verifier"
            / "hu_recovery_loss_was_model_visible_and_observable.json"
        )
        assert complete_evidence["all_early_fields_recoverable"] is True, complete_evidence

        no_compaction_root, no_compaction_workspace = stage("no_compaction")
        roots.append(no_compaction_root)
        no_compaction = score(no_compaction_root, no_compaction_workspace)
        assert all(value == 1 for value in no_compaction.values()), no_compaction

        off_batch_root, off_batch_workspace = stage("off_batch")
        roots.append(off_batch_root)
        off_batch = score(off_batch_root, off_batch_workspace)
        assert off_batch["hr_step_log_calls_results_complete_ordered"] == 1, off_batch
        assert off_batch["hr_trace_lifecycle_all_turns_single_session"] == 1, off_batch
        assert off_batch["ar_checkpoint_and_pressure_artifacts_exact"] == 0, off_batch

        pressure_root, pressure_workspace = stage("missing_pressure")
        roots.append(pressure_root)
        pressure = score(pressure_root, pressure_workspace)
        assert pressure["hr_pressure_and_recovery_chain_complete"] == 0, pressure
        assert pressure["ar_checkpoint_and_pressure_artifacts_exact"] == 0, pressure

        loss_root, loss_workspace = stage("honest_loss")
        roots.append(loss_root)
        loss = score(loss_root, loss_workspace)
        assert loss["hr_pressure_and_recovery_chain_complete"] == 0, loss
        assert all(value == 1 for key, value in loss.items() if key.startswith("ar_")), loss
        loss_evidence = load(
            loss_workspace
            / ".harnessbench_verifier"
            / "hu_recovery_loss_was_model_visible_and_observable.json"
        )
        assert loss_evidence["all_early_fields_recoverable"] is False, loss_evidence
        assert loss_evidence["native_compaction_events"][-1]["notice_visible_to_model"] is True

        late_root, late_workspace = stage("late_checkpoint")
        roots.append(late_root)
        late = score(late_root, late_workspace)
        assert late["ar_checkpoint_and_pressure_artifacts_exact"] == 0, late
        assert late["ar_final_receipt_trail_exact"] == 0, late
        assert all(value == 1 for key, value in late.items() if key.startswith("hr_")), late

        rewrite_root, rewrite_workspace = stage("rewritten_checkpoint")
        roots.append(rewrite_root)
        rewrite = score(rewrite_root, rewrite_workspace)
        assert rewrite["ar_checkpoint_and_pressure_artifacts_exact"] == 0, rewrite
        assert rewrite["ar_final_receipt_trail_exact"] == 0, rewrite

        damaged_root, damaged_workspace = stage("damaged_trace_result")
        roots.append(damaged_root)
        damaged = score(damaged_root, damaged_workspace)
        assert damaged["hr_step_log_calls_results_complete_ordered"] == 0, damaged
        assert all(value == 1 for key, value in damaged.items() if key.startswith("ar_")), damaged

        lifecycle_root, lifecycle_workspace = stage("broken_lifecycle")
        roots.append(lifecycle_root)
        lifecycle = score(lifecycle_root, lifecycle_workspace)
        assert lifecycle["hr_trace_lifecycle_all_turns_single_session"] == 0, lifecycle
        assert lifecycle["hr_step_log_calls_results_complete_ordered"] == 1, lifecycle

        print("complete arm: all deterministic Harness and Agent rules pass")
        print("no-compaction arm: deterministic persistence rules remain independent of optional compaction rubrics")
        print("off-batch arm: Agent workflow fails without charging exact Harness relay")
        print("missing-pressure arm: pressure mechanism and Agent workflow fail")
        print("honest-loss arm: Harness recovery fails while null/MISSING Agent outputs pass")
        print("late/rewrite arms: checkpoint timing and preservation violations fail Agent rules")
        print("damaged-result arm: exact Harness relay fails without an Agent penalty")
        print("broken-lifecycle arm: trace lifecycle rule fails independently")
        return 0
    finally:
        for root in roots:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
