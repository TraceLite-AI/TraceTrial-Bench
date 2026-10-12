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

from harnessbench.execution_capabilities import adapter_extra_args, capability_report
from harnessbench.task_resolver import resolve_task


ROOT = Path(__file__).resolve().parents[1]
VERIFIER_DIR = (
    ROOT
    / "tasks"
    / "un_verify_cases"
    / "manual"
    / "terminal"
    / "C3b-backup-verify-handoff-L3-001"
    / "verifier"
)
SCORE_PATH = VERIFIER_DIR / "score.py"
TASK_DIR = VERIFIER_DIR.parent


def _load_verifier():
    spec = importlib.util.spec_from_file_location("c3b_backup_verifier", SCORE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class C3bBackupVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verifier = _load_verifier()

    def test_duplicate_native_result_representation_is_counted_once(self) -> None:
        text = "\n".join(
            [
                '{"set":"SET-8001","seal_code":"SL-ABCDEF1234"}',
                '{"set":"SET-8001","seal_code":"SL-ABCDEF1234"}',
            ]
        )

        self.assertEqual(
            self.verifier.result_codes_from_text(text),
            {"SET-8001": ["SL-ABCDEF1234"]},
        )

    def test_replay_roles_require_a_parseable_message_array(self) -> None:
        replay = json.dumps(
            [
                {"role": role, "content": str(index)}
                for index, role in enumerate(["user", "assistant"] * 4)
            ]
        )

        self.assertEqual(
            self.verifier.replay_roles(f"prefix {replay} suffix"),
            ["user", "assistant"] * 4,
        )
        self.assertEqual(self.verifier.replay_roles("not json"), [])

    def test_all_configured_metrics_are_fixed_binary_rules_and_match_output(self) -> None:
        configured: dict[str, list[str]] = {}
        for layer in ("harness", "agent"):
            text = (VERIFIER_DIR / f"{layer}_score.yaml").read_text(
                encoding="utf-8"
            )
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

        self.assertEqual(len(configured["harness"]), 12)
        self.assertEqual(len(configured["agent"]), 17)

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
