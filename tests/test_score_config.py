from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harnessbench.score_config import load_score_sheet


REPOSITORY = Path(__file__).resolve().parents[1]
NEW_CASES = REPOSITORY / "tasks" / "new_cases"
MANUAL_CODING = REPOSITORY / "tasks" / "un_verify_cases" / "manual" / "coding"


class ScoreConfigTests(unittest.TestCase):
    def test_quoted_colon_in_block_list_remains_a_string(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "harness_score.yaml"
            path.write_text(
                """\
version: score_v1
layer: harness
items:
  - id: rubric_with_urls
    type: rubric
    criterion: Score the supplied URL evidence.
    evidence:
      event_types: [\"tool\"]
      fields: [\"tool_call.arguments\"]
      keywords:
        - \"sessions_spawn\"
        - \"https://example.invalid/evidence\"
      match: any
      case_sensitive: false
      max_events: 20
""",
                encoding="utf-8",
            )

            sheet = load_score_sheet(path, expected_layer="harness")

        assert sheet.items[0].evidence is not None
        self.assertEqual(
            sheet.items[0].evidence.keywords,
            ("sessions_spawn", "https://example.invalid/evidence"),
        )

    def test_d1_harness_score_sheet_parses_url_keywords(self) -> None:
        path = (
            NEW_CASES
            / "D1-research-subagent-L3-001"
            / "verifier"
            / "harness_score.yaml"
        )

        sheet = load_score_sheet(path, expected_layer="harness")
        item = next(
            item for item in sheet.items
            if item.id == "hu_child_inputs_are_angle_scoped"
        )

        assert item.evidence is not None
        self.assertIn(
            "https://docs.python.org/3/whatsnew/3.12.html",
            item.evidence.keywords,
        )
        self.assertTrue(
            all(isinstance(value, str) for value in item.evidence.keywords)
        )

    def test_c3b_verifier_rule_ids_match_score_sheet_order(self) -> None:
        task_dir = NEW_CASES / "C3b-resume-fidelity-L3-001"
        verifier_dir = task_dir / "verifier"
        harness_sheet = load_score_sheet(
            verifier_dir / "harness_score.yaml",
            expected_layer="harness",
        )
        agent_sheet = load_score_sheet(
            verifier_dir / "agent_score.yaml",
            expected_layer="agent",
        )
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            shutil.copytree(task_dir / "workspace", workspace)
            environment = dict(os.environ)
            environment.pop("HARNESSBENCH_RUN_DIR", None)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(verifier_dir / "score.py"),
                    "--workspace",
                    str(workspace),
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(
            [item["id"] for item in payload["rule_results"]["harness"]],
            [item.id for item in harness_sheet.rule_items],
        )
        self.assertEqual(
            [item["id"] for item in payload["rule_results"]["agent"]],
            [item.id for item in agent_sheet.rule_items],
        )
        pre_boundary = next(
            item for item in payload["rule_results"]["agent"]
            if item["id"] == "ar_pre_boundary_codes_all_preserved"
        )
        self.assertEqual(pre_boundary["score"], 0)

    def test_artifact_sign_verifier_ids_match_binary_score_sheets(self) -> None:
        task_dir = MANUAL_CODING / "C3b-artifact-sign-handoff-L3-001"
        verifier_dir = task_dir / "verifier"
        harness_sheet = load_score_sheet(
            verifier_dir / "harness_score.yaml",
            expected_layer="harness",
        )
        agent_sheet = load_score_sheet(
            verifier_dir / "agent_score.yaml",
            expected_layer="agent",
        )
        self.assertFalse(harness_sheet.rubric_items)
        self.assertFalse(agent_sheet.rubric_items)

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            shutil.copytree(task_dir / "workspace", workspace)
            environment = dict(os.environ)
            environment.pop("HARNESSBENCH_RUN_DIR", None)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(verifier_dir / "score.py"),
                    "--workspace",
                    str(workspace),
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(
            [item["id"] for item in payload["rule_results"]["harness"]],
            [item.id for item in harness_sheet.rule_items],
        )
        self.assertEqual(
            [item["id"] for item in payload["rule_results"]["agent"]],
            [item.id for item in agent_sheet.rule_items],
        )
        scores = [
            item["score"]
            for layer in payload["rule_results"].values()
            for item in layer
        ]
        self.assertTrue(scores)
        self.assertTrue(all(score in {0, 1} for score in scores))

    def test_e3d_timeout_verifier_ids_match_binary_score_sheets(self) -> None:
        task_dir = MANUAL_CODING / "E3d-suite-timeout-channel-001"
        verifier_dir = task_dir / "verifier"
        harness_sheet = load_score_sheet(
            verifier_dir / "harness_score.yaml",
            expected_layer="harness",
        )
        agent_sheet = load_score_sheet(
            verifier_dir / "agent_score.yaml",
            expected_layer="agent",
        )
        self.assertFalse(harness_sheet.rubric_items)
        self.assertFalse(agent_sheet.rubric_items)

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            shutil.copytree(task_dir / "workspace", workspace)
            environment = dict(os.environ)
            environment.pop("HARNESSBENCH_RUN_DIR", None)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(verifier_dir / "score.py"),
                    "--workspace",
                    str(workspace),
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(
            [item["id"] for item in payload["rule_results"]["harness"]],
            [item.id for item in harness_sheet.rule_items],
        )
        self.assertEqual(
            [item["id"] for item in payload["rule_results"]["agent"]],
            [item.id for item in agent_sheet.rule_items],
        )
        scores = [
            item["score"]
            for layer in payload["rule_results"].values()
            for item in layer
        ]
        self.assertEqual(len(scores), 10)
        self.assertTrue(all(score in {0, 1} for score in scores))


if __name__ == "__main__":
    unittest.main()
