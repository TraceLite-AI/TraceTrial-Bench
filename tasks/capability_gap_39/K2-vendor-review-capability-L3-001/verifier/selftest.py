#!/usr/bin/env python3
"""Task-local regression tests for four native child-result trace shapes."""

from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("k2_score", HERE / "score.py")
assert SPEC and SPEC.loader
SCORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCORE)


def payload(group: frozenset[str]) -> str:
    return json.dumps(
        {
            vendor: {
                "verdict": "pass",
                "rule": None,
                "evidence": {"r1": "a", "r2": "b", "r3": "c"},
            }
            for vendor in group
        }
    )


def tool(seq: int, name: str, arguments: dict, result: dict) -> dict:
    return {
        "seq": seq,
        "run_id": "run-test",
        "event_type": "tool",
        "tool_call": {
            "tool_name": name,
            "tool_call_id": f"call-{seq}",
            "arguments": arguments,
        },
        "tool_result": result,
    }


def events_for(harness: str) -> list[dict]:
    events: list[dict] = [
        {"seq": 1, "run_id": "run-test", "event_type": "run_started"}
    ]
    identities: list[str] = []
    seq = 2
    for index, group in enumerate(SCORE.GROUPS, 1):
        identity = f"child-{index}"
        identities.append(identity)
        assignment = " ".join(sorted(group))
        if harness == "codex":
            events.append(
                tool(
                    seq,
                    "collaboration__spawn_agent",
                    {"prompt": assignment},
                    {
                        "structured_output": {
                            "receiver_thread_ids": [identity],
                            "agents_states": {identity: {"status": "pending_init"}},
                        }
                    },
                )
            )
        elif harness == "claudecode":
            events.append(tool(seq, "Agent", {"prompt": assignment}, {"content": json.dumps({"task_id": identity})}))
        elif harness == "openclaw":
            events.append(tool(seq, "sessions_spawn", {"task": assignment}, {"content": json.dumps({"childSessionKey": identity})}))
        else:
            events.append(tool(seq, "subagent", {"prompt": assignment}, {"content": json.dumps({"agentId": identity})}))
        seq += 1

    for index, group in enumerate(SCORE.GROUPS, 1):
        identity = identities[index - 1]
        body = payload(group)
        if harness == "codex":
            events.append(
                tool(
                    seq,
                    "collaboration__wait",
                    {"receiver_thread_ids": [identity]},
                    {"structured_output": {"agents_states": {identity: {"status": "completed", "message": body}}}},
                )
            )
        elif harness in {"claudecode", "openclaw"}:
            events.append(
                {
                    "seq": seq,
                    "run_id": "run-test",
                    "event_type": "task_completion",
                    "principal_id": identity,
                    "principal_role": "child",
                    "parent_principal_id": "parent",
                    "content": body,
                    "source": f"{harness}.native_session_join",
                    "parent_reactivated": True,
                }
            )
        else:
            events.append(
                {
                    "seq": seq,
                    "run_id": "run-test",
                    "event_type": "subagent_lifecycle",
                    "action": "finished",
                    "principal_id": identity,
                    "principal_role": "child",
                    "parent_principal_id": "parent",
                    "last_assistant_message": body,
                    "source": "deepseek.native_session",
                }
            )
        seq += 1

    events.append(
        {
            "seq": seq,
            "run_id": "run-test",
            "event_type": "raw_stdout",
            "raw_event": {
                "type": "item.completed",
                "item": {
                    "type": "file_change",
                    "status": "completed",
                    "changes": [{"kind": "add", "path": "/workspace/output/review.json"}],
                },
            },
        }
    )
    events.append(
        {
            "seq": seq + 1,
            "run_id": "run-test",
            "event_type": "run_finished",
            "trace_complete": True,
            "exit_code": 0,
        }
    )
    return events


def check_harness_shape(harness: str) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        workspace = Path(temporary)
        rules = SCORE.harness_assessment(
            workspace,
            events_for(harness),
            {"delivery_available": True, "delivery": {}},
        )
        assert {item["id"]: item["score"] for item in rules} == {
            "hr_single_turn_trace_complete": 1,
            "hr_exactly_five_native_assignments": 1,
            "hr_parent_isolated_without_recovery": 1,
        }
        evidence = json.loads(
            (workspace / ".harnessbench_verifier" / "hu_parent_aggregates_after_all_handoffs.json").read_text()
        )
        assert len(evidence["successful_session_rows"]) == 5
        assert len(evidence["native_handoff_rows"]) == 5
        assert len(evidence["handoffs_before_first_write"]) == 5
        assert evidence["first_output_write_seq"] == 12


def main() -> int:
    task = (HERE.parent / "task.yaml").read_text(encoding="utf-8")
    prompt = (HERE.parent / "prompt.md").read_text(encoding="utf-8")
    turns = json.loads((HERE.parent / "turns.json").read_text(encoding="utf-8"))
    assert turns == {"turns": [prompt]}
    assert "input_mode: single_turn" in task
    assert "execution_probe:" in task
    assert "requires: [native_child_delegation_v1]" in task
    for harness in ("codex", "claudecode", "openclaw", "deepseek"):
        check_harness_shape(harness)
    print("K2 verifier self-test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
