#!/usr/bin/env python3
"""Regression tests for the K2 native child-result aggregation verifier."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("k2_score", HERE / "score.py")
assert SPEC and SPEC.loader
score = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(score)


def payload(group: frozenset[str]) -> dict[str, object]:
    return {
        component: {
            "verdict": "pass",
            "rule": None,
            "evidence": {"r1": "R1 evidence", "r2": "R2 evidence", "r3": "R3 evidence"},
        }
        for component in sorted(group)
    }


def prompt(group: frozenset[str]) -> str:
    files = ", ".join(
        f"/workspace/components/{component}_Fixture.md" for component in sorted(group)
    )
    ids = ", ".join(sorted(group))
    return (
        f"Read /workspace/docs/POLICY.md and exactly {files}. Review {ids}. "
        "Apply R1, R2, R3. Return verdict, rule, evidence with r1, r2, r3. "
        "Do not write workspace files."
    )


def complete_trace(*, prose_group: int | None = None, extra_dispatch: bool = False):
    events: list[dict[str, object]] = [
        {"seq": 1, "run_id": "r", "event_type": "run_started"}
    ]
    identities: dict[int, str] = {}
    merged: dict[str, object] = {}
    seq = 2
    for group_index, group in enumerate(score.GROUPS, 1):
        identity = f"child-{group_index}"
        identities[group_index] = identity
        events.append(
            {
                "seq": seq,
                "run_id": "r",
                "event_type": "tool",
                "tool_call": {
                    "tool_name": "collaboration__spawn_agent",
                    "tool_call_id": f"spawn-{group_index}",
                    "arguments": {},
                },
                "tool_result": {"tool_call_id": f"spawn-{group_index}"},
                "raw_event": {
                    "tool_call": {"item": {"prompt": prompt(group)}},
                    "tool_result": {
                        "item": {
                            "status": "completed",
                            "receiver_thread_ids": [identity],
                        }
                    },
                },
            }
        )
        seq += 1
    if extra_dispatch:
        events.append(
            {
                "seq": seq,
                "run_id": "r",
                "event_type": "tool",
                "tool_call": {
                    "tool_name": "sessions_spawn",
                    "tool_call_id": "extra",
                    "arguments": {"task": prompt(score.GROUPS[0])},
                },
                "tool_result": {"content": '{"status":"accepted","agentId":"extra-child"}'},
            }
        )
        seq += 1
    for group_index, group in enumerate(score.GROUPS, 1):
        group_payload = payload(group)
        merged.update(group_payload)
        message = json.dumps(group_payload, ensure_ascii=False)
        if prose_group == group_index:
            message = "completed result follows\n" + message
        events.append(
            {
                "seq": seq,
                "run_id": "r",
                "event_type": "tool",
                "tool_call": {"tool_name": "collaboration__wait", "arguments": {}},
                "tool_result": {},
                "raw_event": {
                    "tool_result": {
                        "item": {
                            "agents_states": {
                                identities[group_index]: {
                                    "status": "completed",
                                    "message": message,
                                }
                            }
                        }
                    }
                },
            }
        )
        seq += 1
    events.append(
        {
            "seq": seq,
            "run_id": "r",
            "event_type": "tool",
            "tool_call": {
                "tool_name": "exec_command",
                "arguments": {"command": "cat > output/review.json"},
            },
            "tool_result": {"content": "ok", "exit_code": 0},
        }
    )
    seq += 1
    events.append(
        {
            "seq": seq,
            "run_id": "r",
            "event_type": "run_finished",
            "trace_complete": True,
            "exit_code": 0,
        }
    )
    return events, merged


def deepseek_subagent_trace():
    events: list[dict[str, object]] = [
        {"seq": 1, "run_id": "dsh", "event_type": "run_started"}
    ]
    merged: dict[str, object] = {}
    seq = 2
    for group_index, group in enumerate(score.GROUPS, 1):
        group_payload = payload(group)
        merged.update(group_payload)
        message = json.dumps(group_payload, ensure_ascii=False)
        identity = f"dsh-child-{group_index}"
        events.append(
            {
                "seq": seq,
                "run_id": "dsh",
                "event_type": "subagent_lifecycle",
                "action": "finished",
                "principal_role": "child",
                "principal_id": identity,
                "parent_principal_id": "dsh-main",
                "status": "ok",
                "stop_reason": "completed",
                "last_assistant_message": [{"text": message}],
            }
        )
        seq += 1
        events.append(
            {
                "seq": seq,
                "run_id": "dsh",
                "event_type": "tool",
                "principal_role": "main",
                "principal_id": "dsh-main",
                "tool_call": {
                    "tool_name": "subagent",
                    "tool_call_id": f"subagent-{group_index}",
                    "arguments": {
                        "description": f"review group {group_index}",
                        "prompt": prompt(group),
                    },
                },
                "tool_result": {"content": message, "error": False},
            }
        )
        seq += 1
    events.append(
        {
            "seq": seq,
            "run_id": "dsh",
            "event_type": "tool",
            "principal_role": "child",
            "principal_id": "dsh-child-1",
            "parent_principal_id": "dsh-main",
            "tool_call": {
                "tool_name": "read",
                "arguments": {"file_path": "/workspace/components/V01_Fixture.md"},
            },
            "tool_result": {"content": "checklist", "error": False},
        }
    )
    seq += 1
    events.append(
        {
            "seq": seq,
            "run_id": "dsh",
            "event_type": "tool",
            "principal_role": "main",
            "tool_call": {
                "tool_name": "write",
                "arguments": {"file_path": "/workspace/output/review.json"},
            },
            "tool_result": {"content": "ok", "error": False},
        }
    )
    seq += 1
    events.append(
        {
            "seq": seq,
            "run_id": "dsh",
            "event_type": "run_finished",
            "trace_complete": True,
            "exit_code": 0,
        }
    )
    return events, merged


def by_id(rows: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(row["id"]): row for row in rows}


class K2VerifierTests(unittest.TestCase):
    def assess(self, events, delivery):
        return score.harness_assessment(
            Path("."),
            events,
            {"delivery_available": delivery is not None, "delivery": delivery},
        )

    def test_codex_namespaced_dispatch_and_wait_payloads_pass(self) -> None:
        events, delivery = complete_trace()
        harness, agent = self.assess(events, delivery)
        self.assertTrue(all(row["score"] == 1 for row in harness), harness)
        self.assertTrue(all(row["score"] == 1 for row in agent), agent)

    def test_prose_wrapped_payload_is_transport_intact_but_agent_malformed(self) -> None:
        events, delivery = complete_trace(prose_group=5)
        harness, agent = self.assess(events, delivery)
        self.assertEqual(by_id(harness)["hr_initial_completion_payloads_intact"]["score"], 1)
        self.assertEqual(by_id(agent)["ar_initial_handoffs_well_formed"]["score"], 0)
        self.assertEqual(by_id(agent)["ar_final_output_derived_from_handoffs"]["score"], 1)

    def test_extra_successful_session_is_agent_failure_not_dispatch_rejection(self) -> None:
        events, delivery = complete_trace(extra_dispatch=True)
        harness, agent = self.assess(events, delivery)
        self.assertEqual(by_id(harness)["hr_native_dispatch_calls_accepted"]["score"], 1)
        self.assertEqual(by_id(agent)["ar_exact_native_dispatch_plan"]["score"], 0)

    def test_stderr_redirection_is_not_a_child_workspace_write(self) -> None:
        events, delivery = complete_trace()
        events.insert(
            -2,
            {
                "seq": 11.5,
                "run_id": "r",
                "event_type": "tool",
                "principal_role": "child",
                "parent_tool_use_id": "spawn-1",
                "tool_call": {
                    "tool_name": "Bash",
                    "arguments": {"command": "grep needle input.txt 2>/dev/null"},
                },
                "tool_result": {"content": "match", "exit_code": 0},
                "raw_event": {"parent_tool_use_id": "spawn-1"},
            },
        )
        _, agent = self.assess(events, delivery)
        self.assertEqual(by_id(agent)["ar_parent_delegation_boundary_respected"]["score"], 1)

    def test_deepseek_subagent_lifecycle_joins_identity_and_completion(self) -> None:
        events, delivery = deepseek_subagent_trace()
        harness, agent = self.assess(events, delivery)
        self.assertTrue(all(row["score"] == 1 for row in harness), harness)
        self.assertTrue(all(row["score"] == 1 for row in agent), agent)

    def test_failure_reason_never_collapses_empty_container(self) -> None:
        self.assertEqual(score.compact({}), "{}")

    def test_parent_component_read_and_missing_handoffs_fail_separately(self) -> None:
        events, _ = complete_trace()
        events = [
            event
            for event in events
            if score.tool_name(event) != "wait" and not score.output_write_event(event)
        ]
        events.insert(
            -1,
            {
                "seq": 8,
                "run_id": "r",
                "event_type": "tool",
                "tool_call": {
                    "tool_name": "read",
                    "arguments": {"path": "/workspace/components/V01_Fixture.md"},
                },
                "tool_result": {"content": "checklist"},
            },
        )
        harness, agent = self.assess(events, None)
        self.assertEqual(by_id(harness)["hr_initial_completions_delivered"]["score"], 0)
        self.assertEqual(by_id(agent)["ar_parent_delegation_boundary_respected"]["score"], 0)


if __name__ == "__main__":
    unittest.main()
