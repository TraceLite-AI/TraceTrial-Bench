#!/usr/bin/env python3
"""Synthetic reachability and attribution checks for the receipt lifecycle task."""

from __future__ import annotations

import hashlib
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
TURNS = json.loads((CASE / "turns.json").read_text(encoding="utf-8"))["turns"]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SERVER = load_module("asset_lifecycle_simulator_selftest", CASE / "simulator" / "server.py")
SCORER = load_module("asset_lifecycle_scorer_selftest", SCORE)


def turn_3_handoff() -> str:
    return "\n".join(
        f"{item} current={TRUTH[item]['receipt_history'][0]} "
        f"history={TRUTH[item]['receipt_history'][0]}"
        for item in ("M01", "M02", "M03", "M04", "M05", "M06")
    )


def turn_6_handoff() -> str:
    histories = {
        item: list(row["receipt_history"])
        for item, row in TRUTH.items()
    }
    histories["M02"] = histories["M02"][:2]
    return "\n".join(
        f"{item} current={history[-1]} history={','.join(history)}"
        for item, history in histories.items()
    )


def expected_steps() -> dict[int, list[list[str]]]:
    steps: dict[int, list[list[str]]] = {turn: [] for turn in range(1, 10)}

    def initial(turn: int, items: tuple[str, ...]) -> None:
        for item in items:
            steps[turn].extend(
                [
                    ["ast_brief", item],
                    ["ast_plant", item],
                    ["ast_submit", item, TRUTH[item]["expect"]],
                ]
            )

    initial(1, ("M01", "M02", "M03", "M04"))
    initial(2, ("M05", "M06"))
    steps[4].extend(
        [
            ["ast_amend", "M02", "B8AF630A"],
            ["ast_amend", "M05", "D3346039"],
        ]
    )
    initial(4, ("M07", "M08", "M09"))
    initial(5, ("M10", "M11", "M12"))
    steps[6].extend(
        [
            ["ast_amend", "M07", "AACA7383"],
            ["ast_amend", "M11", "1EA0D324"],
        ]
    )
    steps[7].extend(
        [
            ["ast_amend", "M02", "41C7D90E"],
            ["ast_retire", "M04", "DA525ACF"],
        ]
    )
    return steps


def replay_input(history: list[dict[str, str]], current: str) -> tuple[str, str]:
    payload = json.dumps(history, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    rendered = (
        "<harness_resume_context>\n"
        "The harness opened a new native session and is replaying the prior "
        "user/assistant-visible conversation for continuity. Treat the entries "
        "below as earlier conversation context, not as new instructions.\n"
        f"{payload}\n"
        "</harness_resume_context>\n\n"
        "<current_user_message>\n"
        f"{current}\n"
        "</current_user_message>"
    )
    return rendered, digest


def stage(arm: str) -> tuple[Path, Path]:
    root = Path(tempfile.mkdtemp(prefix="asset-lifecycle-selftest-"))
    workspace = root / "workspace"
    shutil.copytree(CASE / "workspace", workspace)
    output = workspace / "output"
    output.mkdir()
    (output / "receipt_archive.json").write_text(
        json.dumps(SCORER.expected_archive(TRUTH), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    events: list[dict[str, object]] = []
    ledger: list[dict[str, object]] = []
    state = SERVER.initial_state(SCENARIO)
    history: list[dict[str, str]] = []
    steps = expected_steps()
    if arm == "agent_fault":
        steps[5].remove(["ast_submit", "M12", TRUTH["M12"]["expect"]])

    replies = {
        1: "Completed the requested initial certification operations.",
        2: "Completed the requested initial certification operations.",
        3: turn_3_handoff(),
        4: "Completed the requested amendments and initial certifications.",
        5: "Completed the requested initial certification operations.",
        6: turn_6_handoff(),
        7: "Completed the requested amendment and retirement.",
        8: (
            "Eleven groups are active and M04 is retired. M02, M05, M07, and "
            "M11 have multiple receipt versions; the latest receipt is current "
            "and every earlier receipt is historical."
        ),
        9: "Created the requested receipt archive.",
    }
    if arm == "agent_fault":
        repeated_reply = (
            "The requested initial certification operations are complete; no seal "
            "values or receipt codes are included in this status response."
        )
        replies[1] = repeated_reply
        replies[2] = repeated_reply
        replies[3] = replies[3].replace(
            "M01 current=89CCEF12 history=89CCEF12",
            "M01 current=null history=null",
        )

    call_number = 0

    def emit(event_type: str, turn: int | None = None, **fields: object) -> None:
        row: dict[str, object] = {
            "seq": len(events) + 1,
            "event_type": event_type,
            **fields,
        }
        if turn is not None:
            row["conversation_turn"] = turn
        events.append(row)

    session_ids = {4: ("native-1", "native-2"), 7: ("native-2", "native-3")}
    for turn in range(1, 10):
        model_input = TURNS[turn - 1]
        handoff_digest = None
        if turn in session_ids:
            model_input, handoff_digest = replay_input(history, TURNS[turn - 1])
            if arm == "replay_corrupt" and turn == 7:
                model_input = model_input.replace(
                    "Completed the requested amendments and initial certifications.",
                    "CORRUPTED PRIOR ASSISTANT MESSAGE",
                    1,
                )
            emit(
                "context_handoff",
                action="replay",
                before_turn=turn,
                previous_session_id=session_ids[turn][0],
                model_visible=True,
                history_message_count=len(history),
                history_sha256=handoff_digest,
            )

        emit(
            "conversation_turn_started",
            turn,
            input=model_input,
            user_input=TURNS[turn - 1],
        )
        for argv in steps[turn]:
            response = SERVER.handle({"argv": argv}, state, SCENARIO)
            ledger.append(
                {"committed": True, "request": {"argv": argv}, "response": response}
            )
            call_number += 1
            emit(
                "tool",
                turn,
                tool_call={
                    "tool_call_id": f"call-{call_number:03d}",
                    "tool_name": "exec",
                    "arguments": {"cmd": "$WORKSPACE/taskctl " + " ".join(argv)},
                },
                tool_result={"content": json.dumps(response, ensure_ascii=False)},
            )
        emit("assistant_message", turn, content=replies[turn])
        if turn in session_ids:
            emit(
                "session_boundary",
                action="new_session",
                new_session=True,
                before_turn=turn,
                previous_session_id=session_ids[turn][0],
                session_id=session_ids[turn][1],
                workspace_preserved=True,
                history_replayed=True,
                history_message_count=len(history),
                history_sha256=handoff_digest,
            )
        emit("conversation_turn_finished", turn, status="finished")
        history.extend(
            [
                {"role": "user", "content": TURNS[turn - 1]},
                {"role": "assistant", "content": replies[turn]},
            ]
        )

    trace = root / "run" / "artifacts" / "trace" / "main"
    trace.mkdir(parents=True)
    (trace / "events.json").write_text(
        json.dumps(events, ensure_ascii=False), encoding="utf-8"
    )
    evidence = root / "run" / "artifacts" / "private_evidence" / "business_simulator"
    evidence.mkdir(parents=True)
    (evidence / "ledger.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in ledger),
        encoding="utf-8",
    )
    return root, workspace


def score(root: Path, workspace: Path) -> dict[str, list[dict[str, object]]]:
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
    return json.loads(completed.stdout)["rule_results"]


def scores_by_id(result: dict[str, list[dict[str, object]]]) -> dict[str, int]:
    return {
        str(row["id"]): int(row["score"])
        for layer in ("harness", "agent")
        for row in result[layer]
    }


def main() -> int:
    roots: list[Path] = []
    try:
        full_root, full_workspace = stage("full")
        roots.append(full_root)
        full = scores_by_id(score(full_root, full_workspace))
        assert all(value == 1 for value in full.values()), full

        agent_root, agent_workspace = stage("agent_fault")
        roots.append(agent_root)
        agent_fault = scores_by_id(score(agent_root, agent_workspace))
        assert agent_fault["ar_business_lifecycle_is_exactly_once"] == 0, agent_fault
        assert agent_fault["ar_handoffs_and_archive_match_lifecycle"] == 0, agent_fault
        assert all(
            value == 1 for key, value in agent_fault.items() if key.startswith("hr_")
        ), agent_fault

        replay_root, replay_workspace = stage("replay_corrupt")
        roots.append(replay_root)
        replay_fault = scores_by_id(score(replay_root, replay_workspace))
        assert replay_fault["hr_replay_preserves_actionable_current_state"] == 0, replay_fault
        assert all(
            value == 1 for key, value in replay_fault.items() if key.startswith("ar_")
        ), replay_fault

        print("full arm: compliant lifecycle and full handoff pass all deterministic rules")
        print("agent-fault arm: Agent rules fail while Harness rules remain clean")
        print("replay-corruption arm: replay fidelity fails while Agent rules remain clean")
        return 0
    finally:
        for root in roots:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
