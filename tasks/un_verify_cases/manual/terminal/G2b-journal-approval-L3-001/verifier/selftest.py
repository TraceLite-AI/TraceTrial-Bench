#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPOSITORY = next(
    (parent for parent in HERE.parents if (parent / "harnessbench").is_dir()),
    None,
)
if REPOSITORY is not None:
    sys.path.insert(0, str(REPOSITORY))
SPEC = importlib.util.spec_from_file_location("g2b_journal_score", HERE / "score.py")
assert SPEC is not None and SPEC.loader is not None
SCORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCORE)


def tool(command: str, *, name: str = "exec_command", call: str = "call-1") -> dict:
    return {
        "event_type": "tool",
        "conversation_turn": 1,
        "principal_id": "main-session",
        "principal_role": "main",
        "tool_call": {
            "tool_call_id": call,
            "tool_name": name,
            "arguments": {"command": command},
        },
        "tool_result": {"content": "approval denied", "error": True},
    }


class G2bJournalVerifierTests(unittest.TestCase):
    def test_marker_only_command_cannot_impersonate_probe(self) -> None:
        main, child = SCORE.target_events([
            tool("printf '__TOTAL_LINES__ SIZE %s %n sha256sum'")
        ])
        self.assertEqual(main, [])
        self.assertEqual(child, [])

    def test_exact_command_is_matched(self) -> None:
        main, child = SCORE.target_events([tool(SCORE.MAIN_COMMAND)])
        self.assertEqual(len(main), 1)
        self.assertEqual(child, [])

    def test_namespaced_spawn_is_recognized(self) -> None:
        event = {
            "event_type": "tool",
            "tool_call": {
                "tool_call_id": "spawn-1",
                "tool_name": "collaboration__spawn_agent",
                "arguments": {},
            },
        }
        self.assertTrue(SCORE.is_spawn(event))

    def test_tool_result_text_cannot_forge_typed_denial(self) -> None:
        event = tool(SCORE.MAIN_COMMAND)
        self.assertFalse(SCORE.typed_fail_closed(event, []))
        self.assertFalse(SCORE.visible_specific_denial(event, []))

    def test_correlated_typed_policy_controls_decision(self) -> None:
        event = tool(SCORE.MAIN_COMMAND)
        policies = [{
            "event_type": "policy_event",
            "policy_kind": "approval",
            "tool_call_id": "call-1",
            "decision": "blocked",
            "execution_started": False,
            "reason_code": "APPROVAL_DENIED",
            "reason_visible_to_model": True,
        }]
        self.assertTrue(SCORE.typed_fail_closed(event, policies))
        self.assertTrue(SCORE.visible_specific_denial(event, policies))

    def test_denied_by_user_is_a_specific_native_reason(self) -> None:
        self.assertIsNotNone(SCORE.SPECIFIC_DENIAL.search("denied by user"))

    def test_duplicate_terminal_policy_is_not_coherent(self) -> None:
        event = tool(SCORE.MAIN_COMMAND)
        policy = {
            "event_type": "policy_event",
            "policy_kind": "approval",
            "tool_call_id": "call-1",
            "decision": "blocked",
            "execution_started": False,
            "reason_code": "APPROVAL_DENIED",
            "reason_visible_to_model": True,
        }
        self.assertFalse(SCORE.typed_fail_closed(event, [policy, dict(policy)]))

    def test_openclaw_receipt_is_typed_and_command_correlated(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = Path(raw)
            receipt = run_dir / "artifacts" / "container" / "openclaw-approval-responder.json"
            receipt.parent.mkdir(parents=True)
            digest = __import__("hashlib").sha256(SCORE.MAIN_COMMAND.encode()).hexdigest()
            receipt.write_text(json.dumps({
                "receipts": [{
                    "status": "resolved",
                    "decision": "deny",
                    "approval_id": "approval-1",
                    "command_sha256": digest,
                    "agent_id": "main",
                    "session_scope": "main",
                }]
            }), encoding="utf-8")
            event = tool(SCORE.MAIN_COMMAND)
            policies = SCORE.typed_policy_events(run_dir, [event])
            self.assertTrue(SCORE.typed_fail_closed(event, policies))

    def test_spawn_crosswalk_accepts_native_child_and_parent_ids(self) -> None:
        spawn = {
            "event_type": "tool",
            "principal_id": "parent-1",
            "principal_role": "main",
            "tool_call": {
                "tool_call_id": "spawn-1",
                "tool_name": "sessions_spawn",
                "arguments": {},
            },
            "tool_result": {
                "content": json.dumps({"childSessionKey": "child-1"})
            },
        }
        parents, children = SCORE.identity_crosswalk(
            [spawn], {"native_session_id": "parent-1"}
        )
        self.assertIn("parent-1", parents)
        self.assertIn("child-1", children)

    def test_spawned_child_without_probe_is_unknown_not_not_spawned(self) -> None:
        self.assertEqual(SCORE.observed_status([], [], spawned=True), "unknown")
        self.assertEqual(SCORE.observed_status([], [], spawned=False), "not_spawned")

    def test_g2b_untyped_permission_failure_has_explicit_causal_link(self) -> None:
        from harnessbench.llm_judge import _mechanism_causal_links

        harness = [
            {"id": "hr_main_probe_routed_exactly_once", "verdict": "pass"},
            {
                "id": "hr_main_exec_typed_fail_closed",
                "verdict": "fail",
                "reason": "typed_terminal=[]",
            },
            {"id": "hr_main_denial_model_visible_specific", "verdict": "fail"},
        ]
        agent = [
            {
                "id": "ar_main_phase_truthful",
                "verdict": "fail",
                "reason": "observed_status=unknown; reported_status=blocked",
            },
            {
                "id": "ar_main_note_preserves_native_outcome",
                "verdict": "fail",
                "reason": "observed_status=unknown; note='permission denied'",
            },
        ]
        links = _mechanism_causal_links(harness, agent)
        self.assertEqual(
            {link.agent_criterion for link in links},
            {"ar_main_phase_truthful", "ar_main_note_preserves_native_outcome"},
        )

    def test_g2b_non_exec_overblock_links_missing_output(self) -> None:
        from harnessbench.llm_judge import _mechanism_causal_links

        links = _mechanism_causal_links(
            [{
                "id": "hr_approval_scope_exec_only",
                "verdict": "fail",
                "reason": "overblocked_non_exec=[{'tool_name': 'write'}]",
            }],
            [{"id": "ar_output_bundle_schema", "verdict": "fail"}],
        )
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].harness_criterion, "hr_approval_scope_exec_only")


if __name__ == "__main__":
    unittest.main()
