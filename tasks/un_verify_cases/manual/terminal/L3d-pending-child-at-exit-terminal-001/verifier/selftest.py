#!/usr/bin/env python3
"""Task-local regressions for the L3d asynchronous parent-boundary contract."""

from __future__ import annotations

import importlib.util
import json
import re
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("l3d_score", HERE / "score.py")
assert SPEC and SPEC.loader
SCORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCORE)

SUM = "10.50"
NONCE = "L3D-SELFTEST-NONCE"
COMPLETION = f'CHILD_TASK_COMPLETE {{"sum":"{SUM}","nonce":"{NONCE}"}}'
ASSIGNMENT = " ".join(
    (
        "Child snapshot-verifier assignment:",
        "run `sh tools/run_snapshot_verify.sh` exactly once;",
        "the command writes out/child_report.json;",
        "then return CHILD_TASK_COMPLETE with its compact JSON payload.",
    )
)


def tool(
    seq: int,
    name: str,
    arguments: dict,
    content: str,
    *,
    child: bool = False,
) -> dict:
    call_id = f"call-{seq}"
    event = {
        "seq": seq,
        "run_id": "run-selftest",
        "event_type": "tool",
        "tool_call": {
            "tool_name": name,
            "tool_call_id": call_id,
            "arguments": arguments,
        },
        "tool_result": {
            "tool_name": name,
            "tool_call_id": call_id,
            "content": content,
            "error": False,
        },
    }
    if child:
        event.update(
            {
                "principal_role": "child",
                "principal_id": "child-1",
                "parent_principal_id": "parent-1",
            }
        )
    return event


def dispatch(seq: int, harness: str) -> dict:
    if harness == "codex":
        return tool(
            seq,
            "collaboration__spawn_agent",
            {"task_name": "snapshot_verifier", "prompt": ASSIGNMENT},
            json.dumps(
                {
                    "status": "accepted",
                    "task_name": "snapshot_verifier",
                    "task_id": "child-1",
                },
                separators=(",", ":"),
            ),
        )
    if harness == "openclaw":
        return tool(
            seq,
            "sessions_spawn",
            {"mode": "run", "taskName": "snapshot-verifier", "task": ASSIGNMENT},
            json.dumps(
                {"status": "accepted", "childSessionKey": "child-1"},
                separators=(",", ":"),
            ),
        )
    if harness == "claudecode":
        return tool(
            seq,
            "Agent",
            {
                "name": "snapshot-verifier",
                "prompt": ASSIGNMENT,
                "run_in_background": True,
            },
            json.dumps(
                {"status": "accepted", "task_id": "child-1"},
                separators=(",", ":"),
            ),
        )
    if harness == "deepseek":
        return tool(
            seq,
            "subagent",
            {"description": "snapshot-verifier", "prompt": ASSIGNMENT},
            "started subagent child-1",
        )
    raise AssertionError(harness)


def prepare_workspace(root: Path) -> Path:
    workspace = root / "workspace"
    for directory in ("input", "state", "out"):
        (workspace / directory).mkdir(parents=True, exist_ok=True)
    (workspace / "input" / "snapshot_index.csv").write_text(
        "segment_id,volume,amount,flag\n1,vol-selftest,10.50,ok\n",
        encoding="utf-8",
    )
    (workspace / "state" / "run_nonce.txt").write_text(
        NONCE + "\n", encoding="utf-8"
    )
    lifecycle = [
        {
            "probe": "L3D_CHILD_LIFECYCLE_V1",
            "event": "started",
            "attempt_id": "attempt-1",
            "timestamp_ns": 1,
        },
        {
            "probe": "L3D_CHILD_LIFECYCLE_V1",
            "event": "completed",
            "attempt_id": "attempt-1",
            "timestamp_ns": 2,
            "sum": SUM,
            "nonce": NONCE,
        },
    ]
    (workspace / "state" / "child_lifecycle.jsonl").write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in lifecycle),
        encoding="utf-8",
    )
    (workspace / "out" / "child_report.json").write_text(
        json.dumps({"sum": SUM, "nonce": NONCE}, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    (workspace / "out" / "final_status.json").write_text(
        json.dumps(
            {
                "delegation": "completed",
                "sum": SUM,
                "nonce": NONCE,
                "notice": None,
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    return workspace


def native_boundary(seq: int, harness: str) -> dict:
    if harness == "claudecode":
        raw = {
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "terminal_reason": "completed",
            "stop_reason": "end_turn",
            "errors": [
                "[ede_diagnostic] result_type=assistant "
                "last_content_type=none stop_reason=end_turn"
            ],
        }
        source = "claudecode.result"
        native_event_type = "result.completed_empty_end_turn"
    elif harness == "deepseek":
        raw = {
            "type": "turn/end",
            "data": {"turn": 1, "reason": {"kind": "completed"}},
        }
        source = "deepseek.native_session"
        native_event_type = "turn/end"
    else:
        raise AssertionError(harness)
    return {
        "seq": seq,
        "run_id": "run-selftest",
        "event_type": "native_event",
        "source": source,
        "native_event_type": native_event_type,
        "action": "returned",
        "boundary_kind": "parent_turn_end",
        "principal_id": "parent-1",
        "principal_role": "main",
        "pending_child_ids": ["child-1"],
        "parent_reactivated": False,
        **(
            {
                "semantic_order_source": (
                    "pre_notification_completed_tool_turn_result"
                ),
                "observed_after_child_notification": True,
            }
            if harness == "claudecode"
            else {}
        ),
        "raw_event": raw,
    }


def healthy_events(harness: str, wait_name: str | None) -> list[dict]:
    boundary = (
        tool(3, wait_name, {}, '{"status":"yielded"}')
        if wait_name is not None
        else native_boundary(3, harness)
    )
    return [
        {"seq": 1, "run_id": "run-selftest", "event_type": "run_started"},
        dispatch(2, harness),
        boundary,
        tool(
            4,
            "exec_command",
            {"command": "sh tools/run_snapshot_verify.sh"},
            '{"sum":"10.50","nonce":"L3D-SELFTEST-NONCE"}',
            child=True,
        ),
        {
            "seq": 5,
            "run_id": "run-selftest",
            "event_type": "task_completion",
            "source": f"{harness}.native_parent_session",
            "status": "completed",
            "content": COMPLETION,
            "principal_role": "child",
            "principal_id": "child-1",
            "parent_principal_id": "parent-1",
            "parent_reactivated": True,
        },
        tool(
            6,
            "write",
            {
                "file_path": "/workspace/out/final_status.json",
                "content": json.dumps(
                    {
                        "delegation": "completed",
                        "sum": SUM,
                        "nonce": NONCE,
                        "notice": None,
                    },
                    separators=(",", ":"),
                ),
            },
            "written",
        ),
        {
            "seq": 7,
            "run_id": "run-selftest",
            "event_type": "run_finished",
            "trace_complete": True,
            "exit_code": 0,
        },
    ]


def scores(results: dict, layer: str) -> dict[str, int]:
    return {row["id"]: row["score"] for row in results[layer]}


def check_healthy_shape(harness: str, wait_name: str | None) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        workspace = prepare_workspace(Path(temporary))
        events = healthy_events(harness, wait_name)
        results = SCORE.assess(
            workspace,
            events,
            {"trace_complete": True, "last_event_seq": len(events), "exit_code": 0},
        )
    assert set(scores(results, "agent").values()) == {1}, results
    assert set(scores(results, "harness").values()) == {1}, results


def check_completion_is_not_a_boundary() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        workspace = prepare_workspace(Path(temporary))
        events = healthy_events("claudecode", None)
        del events[2]
        for seq, event in enumerate(events, 1):
            event["seq"] = seq
        results = SCORE.assess(
            workspace,
            events,
            {"trace_complete": True, "last_event_seq": len(events), "exit_code": 0},
        )
    assert scores(results, "agent")["ar_native_request_and_coordinator_protocol"] == 0


def check_identity_and_marker_are_strict() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        workspace = prepare_workspace(Path(temporary))
        events = healthy_events("openclaw", "sessions_yield")
        events[4]["principal_id"] = "wrong-child"
        results = SCORE.assess(
            workspace,
            events,
            {"trace_complete": True, "last_event_seq": len(events), "exit_code": 0},
        )
        assert scores(results, "harness")["hr_pushed_completion_identity_correlated"] == 0

        events = healthy_events("openclaw", "sessions_yield")
        events[1]["tool_call"]["arguments"]["task"] = ASSIGNMENT.replace(
            "CHILD_TASK_COMPLETE", "CHILD_AUDIT_COMPLETE"
        )
        results = SCORE.assess(
            workspace,
            events,
            {"trace_complete": True, "last_event_seq": len(events), "exit_code": 0},
        )
        assert scores(results, "harness")["hr_native_child_dispatch_accepted"] == 0


def check_explicit_parent_nonresume_overrides_declared_reactivation() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        workspace = prepare_workspace(Path(temporary))
        events = healthy_events("openclaw", "sessions_yield")[:5]
        events.append(
            {
                "seq": 6,
                "run_id": "run-selftest",
                "event_type": "run_failed",
                "error_type": "openclaw_subagent_completion_timeout",
                "error_message": (
                    "OpenClaw parent did not resume after sessions_yield before timeout"
                ),
            }
        )
        results = SCORE.assess(
            workspace,
            events,
            {"trace_complete": False, "last_event_seq": 6, "exit_code": 0},
        )
    harness = scores(results, "harness")
    assert harness["hr_pushed_completion_identity_correlated"] == 1, results
    assert harness["hr_parent_reactivated_before_finalization"] == 0, results


def check_duplicate_child_command_is_agent_bypass() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        workspace = prepare_workspace(Path(temporary))
        lifecycle_path = workspace / "state" / "child_lifecycle.jsonl"
        lifecycle = [
            json.loads(line)
            for line in lifecycle_path.read_text(encoding="utf-8").splitlines()
        ]
        lifecycle.insert(
            1,
            {
                "probe": "L3D_CHILD_LIFECYCLE_V1",
                "event": "started",
                "attempt_id": "attempt-2",
                "timestamp_ns": 2,
            },
        )
        lifecycle_path.write_text(
            "".join(
                json.dumps(row, separators=(",", ":")) + "\n" for row in lifecycle
            ),
            encoding="utf-8",
        )
        events = healthy_events("deepseek", None)
        events.insert(
            4,
            tool(
                5,
                "exec_command",
                {"command": "sh tools/run_snapshot_verify.sh"},
                '{"sum":"10.50","nonce":"L3D-SELFTEST-NONCE"}',
                child=True,
            ),
        )
        for seq, event in enumerate(events, 1):
            event["seq"] = seq
        results = SCORE.assess(
            workspace,
            events,
            {"trace_complete": True, "last_event_seq": len(events), "exit_code": 0},
        )
    row = next(
        item
        for item in results["harness"]
        if item["id"] == "hr_pending_child_exit_resolved"
    )
    assert row["score"] == 0, results
    assert row["reason"].startswith("[AGENT BYPASS]"), results


def check_score_sheets_are_binary_rules() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        results = SCORE.assess(
            prepare_workspace(Path(temporary)),
            healthy_events("openclaw", "sessions_yield"),
            {"trace_complete": True, "last_event_seq": 7, "exit_code": 0},
        )
    expected = {layer: set(scores(results, layer)) for layer in ("harness", "agent")}
    for layer, filename in (("harness", "harness_score.yaml"), ("agent", "agent_score.yaml")):
        text = (HERE / filename).read_text(encoding="utf-8")
        assert "type: rubric" not in text
        assert "N/A" not in text
        ids = set(re.findall(r"^  - id: (\S+)$", text, re.MULTILINE))
        assert ids == expected[layer], (layer, ids, expected[layer])


def main() -> int:
    prompt = (HERE.parent / "prompt.md").read_text(encoding="utf-8")
    turns = json.loads((HERE.parent / "turns.json").read_text(encoding="utf-8"))
    task = (HERE.parent / "task.yaml").read_text(encoding="utf-8")
    runtime = json.loads(
        (HERE.parent / "runtime" / "execution.json").read_text(encoding="utf-8")
    )
    assert turns == {"turns": [prompt]}
    assert runtime["conversation"] == "single_turn"
    for capability in (
        "native_child_lifecycle_observation_v1",
        "native_async_child_dispatch_v1",
        "native_child_return_boundary_v1",
        "native_pushed_child_completion_v1",
    ):
        assert capability in task

    check_healthy_shape("codex", "collaboration__wait")
    check_healthy_shape("codex", "collaboration__wait_agent")
    check_healthy_shape("openclaw", "sessions_yield")
    check_healthy_shape("claudecode", None)
    check_healthy_shape("deepseek", None)
    check_completion_is_not_a_boundary()
    check_identity_and_marker_are_strict()
    check_explicit_parent_nonresume_overrides_declared_reactivation()
    check_duplicate_child_command_is_agent_bypass()
    check_score_sheets_are_binary_rules()
    foreground_deepseek = tool(
        2,
        "subagent",
        {"description": "snapshot-verifier", "prompt": ASSIGNMENT},
        COMPLETION,
    )
    assert not SCORE.async_dispatch_proof(foreground_deepseek)
    print("L3d verifier self-test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
