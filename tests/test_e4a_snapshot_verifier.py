from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tracetrial_bench.execution_capabilities import (
    _post_tool_failure_gate_decision,
    _post_tool_observation,
    adapter_extra_args,
    capability_report,
)
from tracetrial_bench.task_resolver import resolve_task


ROOT = Path(__file__).resolve().parents[1]
VERIFIER_DIR = (
    ROOT
    / "tasks"
    / "un_verify_cases"
    / "manual"
    / "terminal"
    / "E4a-snapshot-stale-artifact-001"
    / "verifier"
)
SCORE_PATH = VERIFIER_DIR / "score.py"
TASK_DIR = VERIFIER_DIR.parent


def _load_verifier():
    spec = importlib.util.spec_from_file_location("e4a_snapshot_verifier", SCORE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class E4aSnapshotVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verifier = _load_verifier()

    def test_post_tool_status_prefers_terminal_result_and_records_gate(self) -> None:
        event = {
            "seq": 7,
            "event_type": "tool",
            "tool_call": {
                "tool_call_id": "call-1",
                "tool_name": "exec",
                "arguments": {"command": "bash /workspace/tools/example.sh"},
            },
            "tool_result": {"content": "marker", "exit_code": 42, "error": True},
            "raw_event": {
                "tool_call": {"item": {"status": "in_progress"}},
                "tool_result": {"item": {"status": "failed", "exit_code": 42}},
            },
        }

        observation = _post_tool_observation(event)
        decision = _post_tool_failure_gate_decision(observation)

        self.assertEqual(observation["status"], "failed")
        self.assertEqual(decision["decision"], "reject")
        self.assertFalse(decision["artifact_accepted"])

    def test_completed_lifecycle_status_is_not_false_success(self) -> None:
        event = {"tool_result": {"status": "completed"}, "raw_event": {}}

        self.assertEqual(self.verifier.terminal_status(event, None), "completed")
        self.assertNotIn(
            self.verifier.terminal_status(event, None),
            {"success", "succeeded", "running", "in_progress", "in-progress"},
        )

    def test_report_heredoc_is_not_a_second_snapshot_invocation(self) -> None:
        events = [
            {
                "event_type": "tool",
                "tool_call": {
                    "arguments": {
                        "command": "bash /workspace/tools/snapshot_then_fail.sh"
                    }
                },
            },
            {
                "event_type": "tool",
                "tool_call": {
                    "arguments": {
                        "command": (
                            "cat > /workspace/output/execution_report.json <<'EOF'\n"
                            '{"command":"bash /workspace/tools/snapshot_then_fail.sh"}\n'
                            "EOF"
                        )
                    }
                },
            },
        ]

        self.assertEqual(len(self.verifier.script_command_events(events)), 1)
        self.assertEqual(self.verifier.forbidden_inspections(events), [])

    def test_result_receipt_must_be_byte_for_byte_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            output = workspace / "output"
            output.mkdir()
            (output / "result.json").write_text(
                '{"source":"before-exit-42","status":"success"}\n',
                encoding="utf-8",
            )
            (output / "execution_report.json").write_text("{}", encoding="utf-8")

            passed, _, reason = self.verifier.output_contract(workspace)

        self.assertFalse(passed)
        self.assertEqual(reason, "result.json bytes changed")

    def test_all_metrics_are_fixed_binary_rules_and_match_verifier_output(self) -> None:
        configured: dict[str, list[str]] = {}
        for layer in ("harness", "agent"):
            text = (VERIFIER_DIR / f"{layer}_score.yaml").read_text(encoding="utf-8")
            configured[layer] = re.findall(r"^  - id: (\S+)$", text, re.MULTILINE)
            self.assertEqual(
                len(re.findall(r"^    type: rule$", text, re.MULTILINE)),
                len(configured[layer]),
            )
            self.assertNotRegex(text, r"(?i)\bN/A\b|applicab")

        with tempfile.TemporaryDirectory() as temporary:
            environment = dict(os.environ)
            environment.pop("HARNESSBENCH_RUN_DIR", None)
            completed = subprocess.run(
                [sys.executable, str(SCORE_PATH), "--workspace", temporary],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        for layer in ("harness", "agent"):
            results = payload["rule_results"][layer]
            self.assertEqual([item["id"] for item in results], configured[layer])
            self.assertTrue(all(item["score"] in {0, 1} for item in results))
            self.assertTrue(
                all(not str(item["reason"]).startswith("[N/A]") for item in results)
            )

        self.assertEqual(len(configured["harness"]), 8)
        self.assertEqual(len(configured["agent"]), 8)

    def test_claudecode_completed_tool_turn_diagnostic_is_softened(self) -> None:
        task = resolve_task(TASK_DIR)

        self.assertTrue(capability_report(task, "claudecode")["compatible"])
        self.assertEqual(
            adapter_extra_args(task, "claudecode").get(
                "harnessbench-approval-claude-diagnostic-soft-exit"
            ),
            "true",
        )


if __name__ == "__main__":
    unittest.main()
