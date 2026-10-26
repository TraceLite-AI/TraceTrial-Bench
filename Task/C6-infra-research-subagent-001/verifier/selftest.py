#!/usr/bin/env python3
"""Adversarial regressions for the D1 native-handoff verifier."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("d1_score", HERE / "score.py")
assert SPEC and SPEC.loader
SCORE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SCORE
SPEC.loader.exec_module(SCORE)

URLS = {
    angle["id"]: angle["url"]
    for angle in json.loads((HERE / "truth.json").read_text(encoding="utf-8"))["angles"]
}


def payload(angle: str) -> dict:
    return {
        f"{angle}-{index}": {
            "angle": angle,
            "value": f"value-{angle}-{index}",
            "quote": f"subject-{angle}-{index} value-{angle}-{index}",
            "source_url": URLS[angle],
        }
        for index in range(1, 6)
    }


def tool(seq: int, turn: int, name: str, arguments: dict, content: str) -> dict:
    return {
        "seq": seq,
        "conversation_turn": turn,
        "event_type": "tool",
        "tool_call": {
            "tool_name": name,
            "tool_call_id": f"call-{seq}",
            "arguments": arguments,
        },
        "tool_result": {
            "tool_name": name,
            "tool_call_id": f"call-{seq}",
            "content": content,
            "error": False,
        },
    }


def dispatch(seq: int, angle: str) -> dict:
    facts = "\n".join(
        f"- {angle}-{index}: question for `{angle.lower()}_{index}`"
        for index in range(1, 6)
    )
    prompt = (
        f"Only angle {angle}. Only URL {URLS[angle]}\n{facts}\n"
        'Return JSON fields "angle", "value", "quote", "source_url". '
        "Do not receive prior results or write files."
    )
    return tool(
        seq,
        1,
        "sessions_spawn",
        {"task": prompt},
        json.dumps({"status": "accepted", "childSessionKey": f"child-{angle}"}),
    )


def completion(seq: int, turn: int, angle: str, body: dict, child: str | None = None) -> dict:
    return {
        "seq": seq,
        "conversation_turn": turn,
        "event_type": "task_completion",
        "status": "completed",
        "content": json.dumps(body, separators=(",", ":")),
        "principal_role": "child",
        "principal_id": child or f"child-{angle}",
        "parent_principal_id": "parent",
        "parent_reactivated": True,
    }


def healthy() -> tuple[list[dict], dict]:
    events: list[dict] = [
        {"seq": 1, "event_type": "conversation_started"},
        {"seq": 2, "event_type": "conversation_turn_started", "conversation_turn": 1},
    ]
    merged: dict = {}
    seq = 3
    for angle in SCORE.ANGLES:
        events.append(dispatch(seq, angle))
        seq += 1
    for angle in SCORE.ANGLES:
        body = payload(angle)
        merged.update(body)
        events.append(completion(seq, 1, angle, body))
        seq += 1
    events.extend(
        [
            {"seq": seq, "event_type": "conversation_turn_finished", "conversation_turn": 1},
            {"seq": seq + 1, "event_type": "conversation_turn_started", "conversation_turn": 2},
        ]
    )
    seq += 2
    update = {"A1-1": {**merged["A1-1"], "quote": "subject-A1-1 value-A1-1 reverified"}}
    events.append(
        tool(
            seq,
            2,
            "sessions_send",
            {"sessionKey": "child-A1", "message": "Re-verify exactly A1-1 and return its four-field JSON."},
            '{"status":"accepted"}',
        )
    )
    continuation_seq = seq
    seq += 1
    events.append(completion(seq, 2, "A1", update))
    merged.update(update)
    seq += 1
    events.extend(
        [
            {"seq": seq, "event_type": "conversation_turn_finished", "conversation_turn": 2},
            {"seq": seq + 1, "event_type": "conversation_turn_started", "conversation_turn": 3},
        ]
    )
    seq += 2
    events.append(
        tool(
            seq,
            3,
            "write",
            {"path": "/workspace/output/research.json", "content": json.dumps(merged)},
            '{"status":"ok"}',
        )
    )
    seq += 1
    events.extend(
        [
            {"seq": seq, "event_type": "conversation_turn_finished", "conversation_turn": 3},
            {"seq": seq + 1, "event_type": "conversation_finished"},
            {"seq": seq + 2, "event_type": "run_finished", "trace_complete": True},
        ]
    )
    assert continuation_seq < seq
    return events, merged


def score_map(events: list[dict], delivery: dict) -> dict[str, int]:
    with tempfile.TemporaryDirectory() as temporary:
        workspace = Path(temporary)
        results = SCORE.harness_assessment(
            workspace,
            events,
            {"delivery": delivery, "contract_ok": True},
        )
    return {row["id"]: row["score"] for row in results}


def main() -> int:
    events, delivery = healthy()
    scores = score_map(events, delivery)
    assert scores and set(scores.values()) == {1}, scores

    request_only = [
        event
        for event in events
        if not (
            event.get("event_type") == "task_completion"
            and event.get("conversation_turn") == 2
        )
    ]
    request_scores = score_map(request_only, delivery)
    assert request_scores["hr_same_child_reverification_requested"] == 1
    assert request_scores["hr_same_child_reverification_returned"] == 0
    assert request_scores["hr_turn_protocol_and_finalization_order"] == 0

    failed_then_success = copy.deepcopy(events)
    success_index = next(
        index
        for index, event in enumerate(failed_then_success)
        if event.get("event_type") == "tool"
        and event.get("conversation_turn") == 2
        and SCORE.tool_name(event) in SCORE.CONTINUATION_TOOLS
    )
    for event in failed_then_success:
        if isinstance(event.get("seq"), int) and event["seq"] >= 15:
            event["seq"] += 1
    failed_call = tool(
        15,
        2,
        "send_message",
        {"agent_id": "wrong-name", "message": "Re-verify exactly A1-1."},
        '{"success":false,"message":"not reachable"}',
    )
    failed_then_success.insert(success_index, failed_call)
    failed_scores = score_map(failed_then_success, delivery)
    assert failed_scores["hr_same_child_reverification_requested"] == 1
    assert SCORE.tool_success(failed_call) is False

    direct_update = copy.deepcopy(events)
    for event in direct_update:
        if event.get("event_type") == "task_completion" and event.get("conversation_turn") == 2:
            event["content"] = json.dumps(delivery["A1-1"])
    direct_scores = score_map(direct_update, delivery)
    assert direct_scores["hr_same_child_reverification_returned"] == 1
    assert direct_scores["hr_final_output_derived_from_handoffs"] == 1

    todo_mentions_output = copy.deepcopy(events)
    todo_mentions_output.insert(
        -4,
        tool(
            15,
            2,
            "todo_write",
            {"todo": "Later create output/research.json"},
            '{"status":"ok"}',
        ),
    )
    assert score_map(todo_mentions_output, delivery)["hr_turn_protocol_and_finalization_order"] == 1

    parent_fake = copy.deepcopy(request_only)
    parent_fake.insert(
        -4,
        {
            "seq": 15,
            "conversation_turn": 2,
            "event_type": "assistant_message",
            "content": json.dumps({"A1-1": delivery["A1-1"]}),
        },
    )
    assert score_map(parent_fake, delivery)["hr_same_child_reverification_returned"] == 0

    wrong_child = copy.deepcopy(events)
    for event in wrong_child:
        if event.get("event_type") == "task_completion" and event.get("conversation_turn") == 2:
            event["principal_id"] = "child-A2"
    assert score_map(wrong_child, delivery)["hr_same_child_reverification_returned"] == 0

    four_fact = copy.deepcopy(events)
    for event in four_fact:
        if event.get("event_type") == "task_completion" and "A3-1" in str(event.get("content")):
            body = json.loads(event["content"])
            body.pop("A3-5")
            event["content"] = json.dumps(body)
            break
    assert score_map(four_fact, delivery)["hr_initial_native_handoffs_complete"] == 0

    rendered = SCORE.source_variants(HERE / "docs" / "sysctl.txt")
    quote = (
        "oops_limit Number of kernel oopses after which the kernel should panic when "
        "``panic_on_oops`` is not set. Setting this to 0 disables checking the count. "
        "Setting this to 1 has the same effect as setting ``panic_on_oops=1``. "
        "The default value is 10000."
    )
    assert any(SCORE.normalized(quote) in variant for variant in rendered)
    print("D1 verifier selftest: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
