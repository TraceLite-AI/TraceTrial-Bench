#!/usr/bin/env python3
"""Adversarial self-tests for the C6a native-subagent verifier."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("d1_toolchain_score", HERE / "score.py")
assert SPEC is not None and SPEC.loader is not None
score = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = score
SPEC.loader.exec_module(score)


def payloads() -> dict[str, dict[str, object]]:
    truth = json.loads((HERE / "truth.json").read_text(encoding="utf-8"))
    result: dict[str, dict[str, object]] = {}
    for angle in truth["angles"]:
        angle_id = angle["id"]
        result[angle_id] = {
            fact["fid"]: {
                "angle": angle_id,
                "value": fact["answer"],
                "quote": f"initial evidence for {fact['fid']} and {fact['answer']}",
                "source_url": angle["url"],
            }
            for fact in angle["facts"]
        }
    return result


def assignment(angle: str, body: dict[str, object]) -> str:
    truth = json.loads((HERE / "truth.json").read_text(encoding="utf-8"))
    url = next(item["url"] for item in truth["angles"] if item["id"] == angle)
    ids = "\n".join(f"- {fid}" for fid in sorted(body))
    return (
        f"Assigned angle {angle} only.\n{ids}\nCanonical URL: {url}\n"
        'Return exactly one JSON object using fields "angle", "value", "quote", '
        'and "source_url". Use at most one canonical URL retrieval, at most 12 '
        'tool calls, and at most 60,000 UTF-8 bytes. No retrieval retries.'
    )


def perfect_fixture() -> tuple[list[dict[str, object]], dict[str, object]]:
    by_angle = payloads()
    events: list[dict[str, object]] = [
        {"seq": 1, "event_type": "conversation_started"},
        {"seq": 2, "event_type": "conversation_turn_started", "conversation_turn": 1},
    ]
    seq = 3
    for angle in score.ANGLES:
        child = f"child-{angle}"
        events.append(
            {
                "seq": seq,
                "event_type": "tool",
                "conversation_turn": 1,
                "tool_call": {
                    "tool_name": "spawn_agent",
                    "arguments": {"task": assignment(angle, by_angle[angle])},
                },
                "tool_result": {
                    "content": f"started subagent {child}",
                    "structured_output": {"agent_id": child},
                },
            }
        )
        seq += 1
        events.append(
            {
                "seq": seq,
                "event_type": "task_completion",
                "conversation_turn": 1,
                "status": "completed",
                "principal_id": child,
                "principal_role": "child",
                "parent_principal_id": "parent",
                "parent_reactivated": True,
                "content": json.dumps(by_angle[angle], ensure_ascii=False),
            }
        )
        seq += 1
    events.extend(
        [
            {"seq": seq, "event_type": "conversation_turn_finished", "conversation_turn": 1},
            {"seq": seq + 1, "event_type": "conversation_turn_started", "conversation_turn": 2},
        ]
    )
    seq += 2
    events.append(
        {
            "seq": seq,
            "event_type": "tool",
            "conversation_turn": 2,
            "principal_id": "parent",
            "tool_call": {
                "tool_name": "send_message",
                "arguments": {
                    "target": "child-A1",
                    "message": "Re-verify exactly A1-1 from the same canonical URL.",
                },
            },
            "tool_result": {"content": "accepted"},
        }
    )
    seq += 1
    updated = copy.deepcopy(by_angle["A1"]["A1-1"])
    assert isinstance(updated, dict)
    updated["quote"] = "updated evidence for A1-1 and false"
    update_payload = {"A1-1": updated}
    events.append(
        {
            "seq": seq,
            "event_type": "task_completion",
            "conversation_turn": 2,
            "status": "completed",
            "principal_id": "child-A1",
            "principal_role": "child",
            "parent_principal_id": "parent",
            "parent_reactivated": True,
            "content": json.dumps(update_payload, ensure_ascii=False),
        }
    )
    seq += 1
    events.extend(
        [
            {"seq": seq, "event_type": "conversation_turn_finished", "conversation_turn": 2},
            {"seq": seq + 1, "event_type": "conversation_turn_started", "conversation_turn": 3},
        ]
    )
    seq += 2
    final: dict[str, object] = {}
    for angle in score.ANGLES:
        final.update(by_angle[angle])
    final.update(update_payload)
    events.append(
        {
            "seq": seq,
            "event_type": "tool",
            "conversation_turn": 3,
            "principal_id": "parent",
            "tool_call": {
                "tool_name": "write",
                "arguments": {
                    "path": "output/research.json",
                    "content": json.dumps(final, ensure_ascii=False),
                },
            },
            "tool_result": {"content": "ok"},
        }
    )
    events.extend(
        [
            {"seq": seq + 1, "event_type": "conversation_turn_finished", "conversation_turn": 3},
            {"seq": seq + 2, "event_type": "conversation_finished"},
            {"seq": seq + 3, "event_type": "run_finished", "trace_complete": True},
        ]
    )
    return events, {"delivery": final, "delivery_available": True, "contract_ok": True}


def evaluate(events: list[dict[str, object]], agent: dict[str, object]) -> dict[str, int]:
    with tempfile.TemporaryDirectory() as temporary:
        harness, protocol = score.trajectory_assessment(Path(temporary), events, agent)
    return {row["id"]: row["score"] for row in [*harness, *protocol]}


class VerifierSelfTest(unittest.TestCase):
    def test_perfect_native_trace_passes_every_binary_rule(self) -> None:
        events, agent = perfect_fixture()
        results = evaluate(events, agent)
        self.assertEqual(len(results), 16)
        self.assertEqual(set(results.values()), {1})

    def test_continuation_request_is_not_an_updated_handoff(self) -> None:
        events, agent = perfect_fixture()
        events = [
            event
            for event in events
            if not (
                event.get("event_type") == "task_completion"
                and event.get("conversation_turn") == 2
            )
        ]
        results = evaluate(events, agent)
        self.assertEqual(results["hr_same_child_continuation_accepted"], 1)
        self.assertEqual(results["hr_updated_completion_delivered"], 0)
        self.assertEqual(results["ar_updated_handoff_well_formed"], 0)
        self.assertEqual(results["ar_turn_protocol_and_finalization_order"], 0)

    def test_wrong_child_identity_cannot_be_guessed_from_angle(self) -> None:
        events, agent = perfect_fixture()
        completion = next(
            event
            for event in events
            if event.get("event_type") == "task_completion"
            and event.get("conversation_turn") == 1
            and "A3-1" in str(event.get("content"))
        )
        completion["principal_id"] = "unrelated-child"
        results = evaluate(events, agent)
        self.assertEqual(results["hr_distinct_child_identities_observable"], 1)
        self.assertEqual(results["hr_initial_completions_delivered"], 0)

    def test_cross_angle_assignment_fails_isolation(self) -> None:
        events, agent = perfect_fixture()
        dispatch = next(
            event
            for event in events
            if event.get("event_type") == "tool"
            and event.get("conversation_turn") == 1
            and "A2-1" in str(event.get("tool_call"))
        )
        dispatch["tool_call"]["arguments"]["task"] += "\nDo A3-1 too."
        results = evaluate(events, agent)
        self.assertEqual(results["ar_child_assignments_isolated"], 0)

    def test_large_child_result_fails_compactness_only(self) -> None:
        events, agent = perfect_fixture()
        completion = next(
            event
            for event in events
            if event.get("event_type") == "task_completion"
            and event.get("conversation_turn") == 1
        )
        completion["content"] = ("x" * (score.HANDOFF_MAX_BYTES + 1)) + str(completion["content"])
        results = evaluate(events, agent)
        self.assertEqual(results["ar_initial_handoffs_well_formed"], 1)
        self.assertEqual(results["ar_native_handoffs_compact"], 0)

    def test_json_commentary_fails_clean_handoff_only(self) -> None:
        events, agent = perfect_fixture()
        completion = next(
            event
            for event in events
            if event.get("event_type") == "task_completion"
            and event.get("conversation_turn") == 1
        )
        completion["content"] = "Here are the results:\n" + str(completion["content"])
        results = evaluate(events, agent)
        self.assertEqual(results["ar_initial_handoffs_well_formed"], 1)
        self.assertEqual(results["ar_native_handoffs_compact"], 0)

    def test_missing_local_budget_fails_assignment_isolation(self) -> None:
        events, agent = perfect_fixture()
        dispatch = next(
            event
            for event in events
            if event.get("event_type") == "tool"
            and event.get("conversation_turn") == 1
        )
        task = dispatch["tool_call"]["arguments"]["task"]
        dispatch["tool_call"]["arguments"]["task"] = task.split(" Use at most one", 1)[0]
        results = evaluate(events, agent)
        self.assertEqual(results["ar_child_assignments_isolated"], 0)

    def test_raw_parent_prompt_does_not_contaminate_child_assignment(self) -> None:
        events, agent = perfect_fixture()
        parent_prompt = "Parent task mentions " + " ".join(
            f"A{angle}-{fact}"
            for angle in range(1, 6)
            for fact in range(1, 6)
        )
        for event in events:
            if event.get("event_type") == "tool" and event.get("conversation_turn") == 1:
                event["raw_event"] = {"prompt": parent_prompt}
        results = evaluate(events, agent)
        self.assertEqual(results["ar_exact_native_dispatch_set"], 1)
        self.assertEqual(results["ar_child_assignments_isolated"], 1)

    def test_read_only_output_reference_is_not_a_write(self) -> None:
        events, agent = perfect_fixture()
        write_index = next(
            index
            for index, event in enumerate(events)
            if event.get("event_type") == "tool" and event.get("conversation_turn") == 3
        )
        write_seq = events[write_index]["seq"]
        events.insert(
            write_index,
            {
                "seq": write_seq - 1,
                "event_type": "tool",
                "conversation_turn": 3,
                "tool_call": {
                    "tool_name": "exec",
                    "arguments": {"command": "ls output/research.json"},
                },
                "tool_result": {"content": "missing"},
            },
        )
        results = evaluate(events, agent)
        self.assertEqual(results["ar_turn_protocol_and_finalization_order"], 1)

    def test_output_urls_are_not_misclassified_as_parent_retrieval(self) -> None:
        events, agent = perfect_fixture()
        results = evaluate(events, agent)
        self.assertEqual(results["ar_parent_did_not_research_sources"], 1)

    def test_parent_web_retrieval_is_detected(self) -> None:
        events, agent = perfect_fixture()
        events.insert(
            -3,
            {
                "seq": 10_000,
                "event_type": "tool",
                "conversation_turn": 2,
                "principal_id": "parent",
                "tool_call": {
                    "tool_name": "web_fetch",
                    "arguments": {"url": "https://example.invalid/source"},
                },
                "tool_result": {"content": "source"},
            },
        )
        results = evaluate(events, agent)
        self.assertEqual(results["ar_parent_did_not_research_sources"], 0)

    def test_stale_initial_fact_cannot_replace_updated_handoff(self) -> None:
        events, agent = perfect_fixture()
        first = next(
            json.loads(str(event["content"]))["A1-1"]
            for event in events
            if event.get("event_type") == "task_completion"
            and event.get("conversation_turn") == 1
            and "A1-1" in str(event.get("content"))
        )
        stale = copy.deepcopy(agent)
        stale_delivery = copy.deepcopy(agent["delivery"])
        assert isinstance(stale_delivery, dict)
        stale_delivery["A1-1"] = first
        stale["delivery"] = stale_delivery
        results = evaluate(events, stale)
        self.assertEqual(results["ar_final_output_derived_from_handoffs"], 0)


if __name__ == "__main__":
    unittest.main()
