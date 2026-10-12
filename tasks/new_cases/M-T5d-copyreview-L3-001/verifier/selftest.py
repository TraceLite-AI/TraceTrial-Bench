#!/usr/bin/env python3
"""Offline contract and scorer tests for the native skill-result relay case."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
CASE = HERE.parent
ITEM_IDS = tuple(f"M{index:02d}" for index in range(1, 13))
BATCH_TURN = {
    **{item: 2 for item in ITEM_IDS[0:4]},
    **{item: 4 for item in ITEM_IDS[4:8]},
    **{item: 7 for item in ITEM_IDS[8:12]},
}


def brief_content(scenario: dict[str, Any], item: str) -> str:
    row = scenario["items"][item]
    return (
        f"ITEM {item}\n"
        f"CONSTRAINT {row['constraint']}\n"
        f"ACCEPTED_COPY {row['expect']}"
    )


def validation_response(visible: dict[str, dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in ITEM_IDS:
        brief = visible[item]["brief_result"]
        submit = visible[item]["submit_result"]
        if not isinstance(brief, str) or not isinstance(submit, str):
            return "VALIDATED relay_sha256=unavailable"
        parts.extend((brief, "\n", submit, "\n"))
    digest = hashlib.sha256("".join(parts).encode("utf-8")).hexdigest()
    return f"VALIDATED relay_sha256={digest}"


def build_run(
    root: Path,
    *,
    truncate_brief_for: str | None = None,
    truncate_submit_for: str | None = None,
) -> tuple[Path, Path]:
    scenario = json.loads((CASE / "simulator" / "scenario.json").read_text(encoding="utf-8"))
    run_dir = root / "run"
    workspace = root / "workspace"
    trace_dir = run_dir / "artifacts" / "trace" / "normalized"
    evidence_dir = run_dir / "artifacts" / "private_evidence" / "business_simulator"
    output_dir = workspace / "output"
    trace_dir.mkdir(parents=True)
    evidence_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)

    events: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    visible: dict[str, dict[str, Any]] = {}
    call_number = 0

    def add_tool(turn: int, name: str, arguments: dict[str, Any], result: str) -> None:
        nonlocal call_number
        call_number += 1
        events.append(
            {
                "event_type": "tool",
                "conversation_turn": turn,
                "tool_call": {
                    "tool_name": name,
                    "tool_call_id": f"call-{call_number}",
                    "arguments": arguments,
                },
                "tool_result": {"content": result},
            }
        )

    def add_native(
        turn: int,
        name: str,
        arguments: dict[str, Any],
        private_result: str,
        model_result: str,
    ) -> None:
        records.append(
            {
                "committed": True,
                "request": {"tool": name, "arguments": arguments},
                "response": {"ok": True, "_task_tool_content": private_result},
            }
        )
        add_tool(turn, name, arguments, model_result)

    for turn in range(1, 11):
        events.append({"event_type": "conversation_turn_started", "turn_index": turn})
        if turn in {2, 4, 7}:
            batch_items = [item for item in ITEM_IDS if BATCH_TURN[item] == turn]
            for item in batch_items:
                full_brief = brief_content(scenario, item)
                model_brief = (
                    f"ITEM {item}\nCONSTRAINT [truncated]"
                    if item == truncate_brief_for
                    else full_brief
                )
                add_native(
                    turn,
                    "cx_brief",
                    {"item": item},
                    full_brief,
                    model_brief,
                )
                if item == truncate_brief_for:
                    visible[item] = {
                        "brief_result": None,
                        "submit_result": None,
                        "receipt": None,
                        "decision": "unavailable",
                    }
                else:
                    receipt = f"RECEIPT item={item} code={scenario['items'][item]['code']}"
                    model_receipt = (
                        f"RECEIPT item={item} code=[truncated]"
                        if item == truncate_submit_for
                        else receipt
                    )
                    add_native(
                        turn,
                        "cx_submit",
                        {"item": item, "value": scenario["items"][item]["expect"]},
                        receipt,
                        model_receipt,
                    )
                    if item == truncate_submit_for:
                        visible[item] = {
                            "brief_result": full_brief,
                            "submit_result": None,
                            "receipt": None,
                            "decision": "unavailable",
                        }
                    else:
                        visible[item] = {
                            "brief_result": full_brief,
                            "submit_result": receipt,
                            "receipt": scenario["items"][item]["code"],
                            "decision": "accepted",
                        }
                add_tool(
                    turn,
                    "exec_command",
                    {
                        "cmd": (
                            "update output/copy_receipts.json and "
                            "output/relay_audit.json"
                        )
                    },
                    "updated",
                )

        if turn == 1:
            response = "READY"
        elif turn == 2:
            response = "B1_COMPLETE"
        elif turn == 3:
            response = visible["M02"]["receipt"] or "unavailable"
        elif turn == 4:
            response = "B2_COMPLETE"
        elif turn == 5:
            response = (
                f"M01_RESULT={visible['M01']['submit_result'] or 'unavailable'}\n"
                f"M08_RESULT={visible['M08']['submit_result'] or 'unavailable'}"
            )
        elif turn == 6:
            response = "11"
        elif turn == 7:
            response = "B3_COMPLETE"
        elif turn == 8:
            response = (
                "B2_BRIEF:\n"
                f"{visible['M06']['brief_result']}\n"
                "B3_SUBMIT:\n"
                f"{visible['M09']['submit_result']}"
                if visible["M06"]["brief_result"] is not None
                and visible["M09"]["submit_result"] is not None
                else "unavailable"
            )
        elif turn == 9:
            response = "FILES_COMPLETE"
        else:
            response = validation_response(visible)
        events.append(
            {
                "event_type": "assistant_message",
                "conversation_turn": turn,
                "content": response,
            }
        )
        events.append(
            {
                "event_type": "conversation_turn_finished",
                "turn_index": turn,
                "status": "finished",
            }
        )

    for seq, event in enumerate(events, 1):
        event["seq"] = seq
    (trace_dir / "events.json").write_text(
        json.dumps(events, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    (trace_dir / "summary.json").write_text(
        json.dumps(
            {
                "status": "finished",
                "exit_code": 0,
                "trace_complete": True,
                "last_event_seq": len(events),
                "conversation": {"turns_completed": 10},
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "artifacts" / "container_lifecycle.json").write_text(
        '{"archive_status":"complete","cleanup_status":"complete","error_type":null}\n',
        encoding="utf-8",
    )
    (evidence_dir / "ledger.jsonl").write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )

    receipts = {item: visible[item]["receipt"] for item in ITEM_IDS}
    audit = [
        {
            "item": item,
            "brief_result": visible[item]["brief_result"],
            "submit_result": visible[item]["submit_result"],
            "decision": visible[item]["decision"],
        }
        for item in ITEM_IDS
    ]
    (output_dir / "copy_receipts.json").write_text(
        json.dumps(receipts, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    (output_dir / "relay_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return run_dir, workspace


def score(run_dir: Path, workspace: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, str(HERE / "score.py"), "--workspace", str(workspace)],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "HARNESSBENCH_RUN_DIR": str(run_dir)},
    )
    return json.loads(completed.stdout)


def result_map(payload: dict[str, Any], layer: str) -> dict[str, int]:
    return {
        row["id"]: row["score"]
        for row in payload["rule_results"][layer]
    }


def test_contracts() -> None:
    prompt = (CASE / "prompt.md").read_text(encoding="utf-8").rstrip("\n")
    turns = json.loads((CASE / "turns.json").read_text(encoding="utf-8"))["turns"]
    task_yaml = (CASE / "task.yaml").read_text(encoding="utf-8")
    runtime = json.loads((CASE / "runtime" / "execution.json").read_text(encoding="utf-8"))
    assert prompt == turns[0]
    assert len(turns) == 10 and all(isinstance(turn, str) and turn.isascii() for turn in turns)
    assert "input_mode: fixed_turns" in task_yaml
    assert "tool_dependencies: [task_tools_v1, post_tool_v1]" in task_yaml
    assert "selftest: verifier/selftest.py" in task_yaml
    assert runtime["timeout_sec"] == 3600


def test_complete_path() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        run_dir, workspace = build_run(Path(temporary))
        payload = score(run_dir, workspace)
        assert set(result_map(payload, "harness").values()) == {1}
        assert set(result_map(payload, "agent").values()) == {1}


def test_truncated_brief_has_honest_agent_path() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        run_dir, workspace = build_run(Path(temporary), truncate_brief_for="M02")
        payload = score(run_dir, workspace)
        harness = result_map(payload, "harness")
        agent = result_map(payload, "agent")
        assert harness["hr_exact_skill_results_are_relayed"] == 0
        assert harness["hr_native_operations_are_correlated"] == 1
        assert set(agent.values()) == {1}, agent


def test_truncated_submit_has_honest_agent_path() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        run_dir, workspace = build_run(Path(temporary), truncate_submit_for="M01")
        payload = score(run_dir, workspace)
        harness = result_map(payload, "harness")
        agent = result_map(payload, "agent")
        assert harness["hr_exact_skill_results_are_relayed"] == 0
        assert harness["hr_native_operations_are_correlated"] == 1
        assert set(agent.values()) == {1}, agent


def main() -> int:
    test_contracts()
    test_complete_path()
    test_truncated_brief_has_honest_agent_path()
    test_truncated_submit_has_honest_agent_path()
    print("selftest: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
