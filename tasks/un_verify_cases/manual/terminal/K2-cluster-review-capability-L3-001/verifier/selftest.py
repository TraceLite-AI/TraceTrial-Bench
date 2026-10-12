#!/usr/bin/env python3
"""Four-harness and adversarial regressions for the K2 L3c verifier."""

from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("k2_cluster_score", HERE / "score.py")
assert SPEC and SPEC.loader
SCORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCORE)


def group_payload(group: frozenset[str], marker: str = "native") -> dict:
    return {
        cluster: {
            "verdict": "pass",
            "rule": None,
            "evidence": {
                "r1": f"{marker}-{cluster}-r1",
                "r2": f"{marker}-{cluster}-r2",
                "r3": f"{marker}-{cluster}-r3",
            },
        }
        for cluster in group
    }


def tool(
    seq: int,
    name: str,
    arguments: dict,
    *,
    content: object = '{"status":"ok"}',
    structured_output: object | None = None,
    child: bool = False,
) -> dict:
    result = {
        "tool_name": name,
        "tool_call_id": f"call-{seq}",
        "content": content,
        "error": False,
    }
    if structured_output is not None:
        result["structured_output"] = structured_output
    event = {
        "seq": seq,
        "run_id": "run-selftest",
        "event_type": "tool",
        "tool_call": {
            "tool_name": name,
            "tool_call_id": f"call-{seq}",
            "arguments": arguments,
        },
        "tool_result": result,
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


def dispatch(seq: int, harness: str, group: frozenset[str], identity: str) -> dict:
    assignment = "Review exactly " + " ".join(sorted(group))
    if harness == "codex":
        return tool(
            seq,
            "collaboration__spawn_agent",
            {"task_name": identity, "prompt": assignment},
            content="accepted",
            structured_output={
                "receiver_thread_ids": [identity],
                "agents_states": {identity: {"status": "pending_init"}},
            },
        )
    if harness == "openclaw":
        return tool(
            seq,
            "sessions_spawn",
            {"task": assignment},
            content=json.dumps({"status": "accepted", "childSessionKey": identity}),
        )
    if harness == "cc":
        return tool(
            seq,
            "Agent",
            {"prompt": assignment, "run_in_background": True},
            content=json.dumps({"status": "accepted", "task_id": identity}),
        )
    if harness == "dsh":
        return tool(
            seq,
            "subagent",
            {"description": identity, "prompt": assignment},
            content=f"started subagent {identity}",
        )
    raise AssertionError(harness)


def completion(seq: int, harness: str, identity: str, body: dict) -> dict:
    return {
        "seq": seq,
        "run_id": "run-selftest",
        "event_type": "task_completion",
        "source": f"{harness}.native_session_join",
        "status": "completed",
        "content": json.dumps(body, ensure_ascii=False, separators=(",", ":")),
        "principal_role": "child",
        "principal_id": identity,
        "parent_principal_id": "parent-1",
        "parent_reactivated": True,
    }


def task_output(seq: int, identity: str, body: dict, *, truncated: bool = False) -> dict:
    payload = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    if truncated:
        payload = (
            "[Truncated. Full output: /tmp/claude-test/tasks/"
            + identity
            + ".output]\n"
            + payload
        )
    return tool(
        seq,
        "TaskOutput",
        {"task_id": identity, "block": True},
        content=(
            "<retrieval_status>success</retrieval_status>\n"
            f"<task_id>{identity}</task_id>\n"
            "<status>completed</status>\n"
            f"<output>{payload}</output>"
        ),
    )


def output_write(seq: int, harness: str, delivery: dict) -> dict:
    rendered = json.dumps(delivery, ensure_ascii=False, separators=(",", ":"))
    if harness == "codex":
        return {
            "seq": seq,
            "run_id": "run-selftest",
            "event_type": "raw_stdout",
            "raw_event": {
                "type": "item.completed",
                "item": {
                    "type": "file_change",
                    "status": "completed",
                    "changes": [
                        {"kind": "add", "path": "/workspace/output/review.json"}
                    ],
                },
            },
        }
    if harness == "cc":
        return tool(
            seq,
            "exec_command",
            {"command": "python -c 'open(\"output/review.json\",\"w\").write(data)'"},
        )
    return tool(
        seq,
        "write" if harness == "openclaw" else "write_file",
        {"path": "/workspace/output/review.json", "content": rendered},
    )


def healthy(harness: str) -> tuple[list[dict], dict]:
    events: list[dict] = [
        {"seq": 1, "run_id": "run-selftest", "event_type": "run_started"}
    ]
    delivery: dict = {}
    for index, group in enumerate(SCORE.GROUPS, 1):
        events.append(dispatch(index + 1, harness, group, f"child-{index}"))
    for index, group in enumerate(SCORE.GROUPS, 1):
        body = group_payload(group)
        delivery.update(body)
        events.append(completion(index + 6, harness, f"child-{index}", body))
    events.append(output_write(12, harness, delivery))
    events.append(
        {
            "seq": 13,
            "run_id": "run-selftest",
            "event_type": "run_finished",
            "trace_complete": True,
            "exit_code": 0,
        }
    )
    return events, delivery


def score_map(events: list[dict], delivery: dict | None) -> dict[str, int]:
    with tempfile.TemporaryDirectory() as temporary:
        workspace = Path(temporary)
        results = SCORE.harness_assessment(
            workspace,
            events,
            {"delivery_available": delivery is not None, "delivery": delivery},
        )
    scores = {row["id"]: row["score"] for row in results}
    assert set(scores.values()) <= {0, 1}, scores
    return scores


def main() -> int:
    task = (HERE.parent / "task.yaml").read_text(encoding="utf-8")
    prompt = (HERE.parent / "prompt.md").read_text(encoding="utf-8")
    turns = json.loads((HERE.parent / "turns.json").read_text(encoding="utf-8"))
    harness_score = (HERE / "harness_score.yaml").read_text(encoding="utf-8")
    agent_score = (HERE / "agent_score.yaml").read_text(encoding="utf-8")
    assert turns == {"turns": [prompt]}
    assert "input_mode: single_turn" in task
    for capability in (
        "native_child_delegation_v1",
        "native_child_lifecycle_observation_v1",
        "native_async_child_dispatch_v1",
        "native_child_return_boundary_v1",
        "native_pushed_child_completion_v1",
    ):
        assert capability in task
    assert "type: rubric" not in harness_score
    assert "N/A" not in harness_score + agent_score
    assert harness_score.count("type: rule") == 6

    for harness in ("codex", "openclaw", "cc", "dsh"):
        events, delivery = healthy(harness)
        scores = score_map(events, delivery)
        assert len(scores) == 6 and set(scores.values()) == {1}, (harness, scores)

    events, delivery = healthy("codex")
    missing = [event for event in events if not (event.get("event_type") == "task_completion" and event.get("principal_id") == "child-3")]
    scores = score_map(missing, delivery)
    assert scores["hr_exactly_five_native_assignments"] == 1
    assert scores["hr_five_complete_native_handoffs"] == 0
    assert scores["hr_parent_aggregates_after_all_handoffs"] == 0
    assert scores["hr_final_review_preserves_handoff_values"] == 0

    premature = copy.deepcopy(events)
    write = next(event for event in premature if SCORE.file_change_writes_target(event))
    write["seq"] = 7
    for event in premature:
        if event.get("event_type") == "task_completion":
            event["seq"] += 1
    scores = score_map(premature, delivery)
    assert scores["hr_five_complete_native_handoffs"] == 1
    assert scores["hr_parent_aggregates_after_all_handoffs"] == 0

    altered = copy.deepcopy(delivery)
    altered["V01"]["evidence"]["r1"] = "parent-authored-change"
    scores = score_map(events, altered)
    assert scores["hr_five_complete_native_handoffs"] == 1
    assert scores["hr_final_review_preserves_handoff_values"] == 0

    parent_glob = copy.deepcopy(events)
    parent_glob.insert(-1, tool(99, "exec_command", {"command": "cat clusters/*"}))
    assert score_map(parent_glob, delivery)["hr_parent_isolated_without_recovery"] == 0

    listing = copy.deepcopy(events)
    listing.insert(-1, tool(99, "exec_command", {"command": "find clusters -maxdepth 1 -type f -print"}))
    assert score_map(listing, delivery)["hr_parent_isolated_without_recovery"] == 1

    child_write = copy.deepcopy(events)
    child_write.insert(-1, tool(99, "write", {"path": "scratch.txt", "content": "x"}, child=True))
    assert score_map(child_write, delivery)["hr_parent_isolated_without_recovery"] == 0

    # Child-side nested delegation does not become an extra root assignment;
    # an actual sixth root child still fails the exact-five criterion.
    nested_dispatch = copy.deepcopy(events)
    nested = dispatch(99, "codex", SCORE.GROUPS[0], "grandchild")
    nested.update({"principal_role": "child", "principal_id": "child-1"})
    nested_dispatch.insert(-1, nested)
    assert score_map(nested_dispatch, delivery)["hr_exactly_five_native_assignments"] == 1
    rejected_spawn = copy.deepcopy(events)
    rejected_spawn.insert(
        -1,
        tool(
            99,
            "collaboration__spawn_agent",
            {"task_name": "rejected", "message": "Review V01 V02 V03 V04"},
            content="collab spawn failed: agent thread limit reached",
        ),
    )
    assert score_map(rejected_spawn, delivery)["hr_exactly_five_native_assignments"] == 1
    root_extra = copy.deepcopy(events)
    root_extra.insert(-1, dispatch(99, "codex", SCORE.GROUPS[0], "child-6"))
    assert score_map(root_extra, delivery)["hr_exactly_five_native_assignments"] == 0

    # TaskOutput is a native join. Complete inline content is accepted, while
    # a truncated result remains incomplete even if its visible tail has JSON.
    cc_events, cc_delivery = healthy("cc")
    cc_events = [
        event for event in cc_events
        if not (event.get("event_type") == "task_completion" and event.get("principal_id") == "child-1")
    ]
    cc_events.insert(-2, task_output(11, "child-1", group_payload(SCORE.GROUPS[0])))
    assert set(score_map(cc_events, cc_delivery).values()) == {1}
    truncated_join = copy.deepcopy(cc_events)
    truncated_join[-3] = task_output(
        11, "child-1", group_payload(SCORE.GROUPS[0]), truncated=True
    )
    assert score_map(truncated_join, cc_delivery)["hr_five_complete_native_handoffs"] == 0
    direct_recovery = copy.deepcopy(cc_events)
    direct_recovery.insert(
        -1,
        tool(99, "exec_command", {"command": "cat /tmp/claude-test/tasks/child-1.output"}),
    )
    assert score_map(direct_recovery, cc_delivery)["hr_parent_isolated_without_recovery"] == 0

    child_temp_write = copy.deepcopy(events)
    child_temp_write.insert(
        -1, tool(99, "write", {"path": "/tmp/child-note", "content": "x"}, child=True)
    )
    assert score_map(child_temp_write, delivery)["hr_parent_isolated_without_recovery"] == 1

    duplicate_start = copy.deepcopy(events)
    duplicate_start.insert(1, {"seq": 1, "run_id": "run-selftest", "event_type": "run_started"})
    assert score_map(duplicate_start, delivery)["hr_single_turn_trace_complete"] == 0

    exit_one = copy.deepcopy(events)
    exit_one[-1]["exit_code"] = 1
    assert score_map(exit_one, delivery)["hr_single_turn_trace_complete"] == 0
    exit_one.insert(
        -1,
        {
            "seq": 12,
            "run_id": "run-selftest",
            "event_type": "native_diagnostic",
            "source": "claudecode.result",
            "diagnostic_type": "completed_tool_turn_ede_diagnostic",
            "process_exit_may_be_softened": True,
        },
    )
    assert score_map(exit_one, delivery)["hr_single_turn_trace_complete"] == 1

    # A factually wrong child value copied unchanged is an Agent-quality failure,
    # not a harness transport/preservation failure.
    wrong_child = copy.deepcopy(events)
    wrong_delivery = copy.deepcopy(delivery)
    wrong_delivery["V01"]["verdict"] = "fail"
    wrong_delivery["V01"]["rule"] = 1
    first_completion = next(event for event in wrong_child if event.get("principal_id") == "child-1")
    body = json.loads(first_completion["content"])
    body["V01"] = copy.deepcopy(wrong_delivery["V01"])
    first_completion["content"] = json.dumps(body, separators=(",", ":"))
    assert set(score_map(wrong_child, wrong_delivery).values()) == {1}

    with tempfile.TemporaryDirectory() as temporary:
        agent_rules, _ = SCORE.agent_assessment(Path(temporary))
    assert all(row["score"] in {0, 1} for row in agent_rules)
    print("K2 cluster verifier self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
