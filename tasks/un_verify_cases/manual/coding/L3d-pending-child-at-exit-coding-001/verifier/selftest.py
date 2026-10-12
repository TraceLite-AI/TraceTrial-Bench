#!/usr/bin/env python3
"""Regression and mutation tests for the coding L3d verifier."""

from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("l3d_score", HERE / "score.py")
assert SPEC and SPEC.loader
SCORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCORE)

SUM = "10.50"
NONCE = "L3D-SELFTEST-NONCE"
PAYLOAD = f'CHILD_TASK_COMPLETE {{"sum":"{SUM}","nonce":"{NONCE}"}}'
ASSIGNMENT = (
    "Work only inside /workspace. From the workspace root, run "
    "`sh tools/run_coverage_merge.sh` exactly once. The command writes "
    "out/child_report.json. Respond with exactly CHILD_TASK_COMPLETE and its JSON."
)


def tool(seq: int, name: str, args: dict, content: str, *, child: bool = False) -> dict:
    call_id = f"call-{seq}"
    event = {
        "seq": seq,
        "run_id": "run-selftest",
        "event_type": "tool",
        "tool_call": {"tool_name": name, "tool_call_id": call_id, "arguments": args},
        "tool_result": {
            "tool_name": name, "tool_call_id": call_id,
            "content": content, "error": False,
        },
    }
    if child:
        event.update({
            "principal_role": "child", "principal_id": "child-1",
            "parent_principal_id": "parent-1",
        })
    return event


def dispatch(seq: int, harness: str) -> dict:
    if harness == "codex":
        return tool(seq, "collaboration__spawn_agent",
                    {"task_name": "coverage_merger", "message": ASSIGNMENT},
                    '{"status":"accepted","task_name":"coverage_merger","task_id":"child-1"}')
    if harness == "openclaw":
        return tool(seq, "sessions_spawn",
                    {"mode": "run", "taskName": "coverage-merger", "task": ASSIGNMENT},
                    '{"status":"accepted","childSessionKey":"child-1"}')
    if harness == "claudecode":
        return tool(seq, "Agent",
                    {"name": "coverage-merger", "prompt": ASSIGNMENT, "run_in_background": True},
                    '{"status":"accepted","task_id":"child-1"}')
    if harness == "deepseek":
        return tool(seq, "subagent",
                    {"description": "coverage-merger", "prompt": ASSIGNMENT},
                    "started subagent child-1")
    raise AssertionError(harness)


def boundary(seq: int, harness: str) -> dict:
    if harness == "codex":
        return tool(seq, "collaboration__wait", {}, '{"status":"yielded"}')
    if harness == "openclaw":
        return tool(seq, "sessions_yield", {}, '{"status":"yielded"}')
    if harness == "claudecode":
        raw = {
            "type": "result", "subtype": "error_during_execution", "is_error": True,
            "terminal_reason": "completed", "stop_reason": "end_turn",
            "errors": ["[ede_diagnostic] result_type=assistant last_content_type=none stop_reason=end_turn"],
        }
        source, native_type = "claudecode.result", "result.completed_empty_end_turn"
        extra = {"semantic_order_source": "direct_completed_tool_turn_result"}
    else:
        raw = {"type": "turn/end"}
        source, native_type, extra = "deepseek.native_session", "turn/end", {}
    return {
        "seq": seq, "run_id": "run-selftest", "event_type": "native_event",
        "source": source, "native_event_type": native_type, "action": "returned",
        "boundary_kind": "parent_turn_end", "principal_role": "main",
        "principal_id": "parent-1", "pending_child_ids": ["child-1"],
        "raw_event": raw, **extra,
    }


def workspace(root: Path) -> Path:
    ws = root / "workspace"
    for directory in ("input", "state", "out"):
        (ws / directory).mkdir(parents=True, exist_ok=True)
    (ws / "input" / "coverage_shards.csv").write_text(
        "id,amount\n1,10.50\n", encoding="utf-8")
    (ws / "state" / "run_nonce.txt").write_text(NONCE + "\n", encoding="utf-8")
    lifecycle = [
        {"probe": "L3D_CHILD_LIFECYCLE_V1", "event": "started",
         "attempt_id": "attempt-1", "timestamp_ns": 1},
        {"probe": "L3D_CHILD_LIFECYCLE_V1", "event": "completed",
         "attempt_id": "attempt-1", "timestamp_ns": 2, "sum": SUM, "nonce": NONCE},
    ]
    (ws / "state" / "child_lifecycle.jsonl").write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in lifecycle),
        encoding="utf-8")
    (ws / "out" / "child_report.json").write_text(
        json.dumps({"sum": SUM, "nonce": NONCE}, separators=(",", ":")) + "\n",
        encoding="utf-8")
    (ws / "out" / "final_status.json").write_text(
        json.dumps({"delegation": "completed", "sum": SUM, "nonce": NONCE, "notice": None},
                   separators=(",", ":")) + "\n", encoding="utf-8")
    return ws


def healthy(harness: str) -> list[dict]:
    return [
        {"seq": 1, "run_id": "run-selftest", "event_type": "run_started"},
        dispatch(2, harness),
        boundary(3, harness),
        tool(4, "exec", {"command": "sh tools/run_coverage_merge.sh"},
             '{"sum":"10.50","nonce":"L3D-SELFTEST-NONCE"}', child=True),
        {"seq": 5, "run_id": "run-selftest", "event_type": "task_completion",
         "source": f"{harness}.native_parent_session", "status": "completed",
         "content": PAYLOAD, "principal_role": "child", "principal_id": "child-1",
         "parent_principal_id": "parent-1", "parent_reactivated": True},
        {"seq": 6, "run_id": "run-selftest", "event_type": "run_finished",
         "trace_complete": True, "exit_code": 0},
    ]


def scores(result: dict, layer: str) -> dict[str, int]:
    return {row["id"]: row["score"] for row in result[layer]}


def assess(ws: Path, events: list[dict], code: int = 0) -> dict:
    return SCORE.assess(ws, events, {
        "trace_complete": True, "last_event_seq": len(events), "exit_code": code,
    })


def main() -> int:
    prompt = (HERE.parent / "prompt.md").read_text(encoding="utf-8")
    turns = json.loads((HERE.parent / "turns.json").read_text(encoding="utf-8"))
    task = (HERE.parent / "task.yaml").read_text(encoding="utf-8")
    assert turns == {"turns": [prompt]}
    assert "input_mode: single_turn" in task
    assert "native_pushed_child_completion_v1" in task
    with tempfile.TemporaryDirectory() as temporary:
        ws = workspace(Path(temporary))
        for harness in ("codex", "openclaw", "claudecode", "deepseek"):
            result = assess(ws, healthy(harness))
            assert set(scores(result, "agent").values()) == {1}, (harness, result)
            assert set(scores(result, "harness").values()) == {1}, (harness, result)

        missing_push = healthy("openclaw")
        del missing_push[4]
        for seq, event in enumerate(missing_push, 1):
            event["seq"] = seq
        result = assess(ws, missing_push)
        assert scores(result, "harness")["hr_pushed_outcome_direct_and_correlated"] == 0
        assert scores(result, "harness")["hr_pending_child_exit_honest"] == 0

        misleading_prompt_event = {
            "seq": 1, "event_type": "native_event", "source": "deepseek.native_session",
            "native_event_type": "agent/inbox/spliced", "principal_role": "main",
            "raw_event": {"prompt": "pending child must be reported"},
        }
        assert SCORE.harness_notices({}, [misleading_prompt_event]) == []

        missing_result = healthy("openclaw")
        missing_result[1]["tool_result"] = None
        result = assess(ws, missing_result)
        assert scores(result, "harness")["hr_tool_call_results_correlated"] == 0
        assert scores(result, "agent")["ar_prescribed_async_dispatch"] == 0

        protected = healthy("openclaw")
        protected.insert(5, tool(6, "exec", {"command": "cat out/child_report.json"}, "{}"))
        for seq, event in enumerate(protected, 1):
            event["seq"] = seq
        result = assess(ws, protected)
        assert scores(result, "agent")["ar_coordinator_protected_data_boundary"] == 0

        nonzero_completion = healthy("claudecode")
        nonzero_completion[-1]["exit_code"] = 1
        result = assess(ws, nonzero_completion, code=1)
        assert scores(result, "harness")["hr_terminal_exit_matches_outcome"] == 0

        duplicated = (ws / "state" / "child_lifecycle.jsonl").read_text(encoding="utf-8")
        (ws / "state" / "child_lifecycle.jsonl").write_text(
            duplicated + duplicated.splitlines()[0] + "\n", encoding="utf-8")
        result = assess(ws, healthy("openclaw"))
        assert scores(result, "agent")["ar_single_child_attempt_integrity"] == 0
        assert scores(result, "harness")["hr_no_orphan_after_terminal"] == 0
    print("L3d coding verifier self-test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
