from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.task_fixtures import paper_task

from tracetrial_bench.evaluator import run_verifier
from tracetrial_bench.llm_judge import JudgeRuntime, run_layered_judge
from tracetrial_bench.score_config import load_score_sheet
from tracetrial_bench.task_resolver import resolve_task
from tracetrial_bench.workspace import prepare_run_dir


class MigratedTaskScoringTests(unittest.TestCase):
    def test_migrated_tasks_complete_rule_and_rubric_pipeline(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        fixtures = {
            "T2d-coercion-advice-L3-001": (
                '{"state":"rejected","suggested_form":"1000"} '
                "claim_notes.md 第一次被拒"
            ),
            "V3-refund-batch-L3-001": (
                "accepted settled queued into today's bank batch "
                "refund_report.md 待结算 银行批次 T+1"
            ),
        }
        for task_name, evidence_text in fixtures.items():
            with self.subTest(task=task_name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                task = resolve_task(paper_task(task_name))
                layout = prepare_run_dir(task, root / "run", private_dir=root / "private")
                shutil.copytree(layout.workspace_dir, layout.baseline_dir)
                assert task.scoring is not None
                harness_sheet = load_score_sheet(
                    task.scoring.harness_score_path,
                    expected_layer="harness",
                )
                agent_sheet = load_score_sheet(
                    task.scoring.agent_score_path,
                    expected_layer="agent",
                )
                rule_payload = {
                    "schema_version": "harnessbench_rule_results_v1",
                    "rule_results": {
                        "harness": [
                            {"id": item.id, "score": 1, "reason": "fixture"}
                            for item in harness_sheet.rule_items
                        ],
                        "agent": [
                            {"id": item.id, "score": 1, "reason": "fixture"}
                            for item in agent_sheet.rule_items
                        ],
                    },
                }
                # Exercise the layered judge with the current verifier schema
                # without making the fixture depend on case-specific trace rules.
                (layout.verifier_dir / "score.py").write_text(
                    "import json\nprint(json.dumps("
                    + repr(rule_payload)
                    + ", ensure_ascii=False))\n",
                    encoding="utf-8",
                )
                trace_dir = layout.artifacts_dir / "trace" / "run-1"
                trace_dir.mkdir(parents=True)
                events_path = trace_dir / "events.json"
                events_path.write_text(
                    json.dumps(
                        [
                            {
                                "seq": 1,
                                "event_type": "tool",
                                "tool_call": {
                                    "arguments": {"command": evidence_text}
                                },
                                "tool_result": {"content": evidence_text},
                            }
                        ],
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                objective = run_verifier(
                    task,
                    layout,
                    harness="openclaw",
                    model="fixture",
                )
                prompts: list[str] = []

                def requester(_runtime, prompt: str) -> str:
                    prompts.append(prompt)
                    return '{"score":1,"reason":"matched fixture evidence proves the criterion"}'

                scored = run_layered_judge(
                    task=task,
                    layout=layout,
                    objective=objective,
                    trace_summary={"events_path": str(events_path)},
                    runtime=JudgeRuntime(
                        provider="fixture",
                        model="fixture-judge",
                        base_url="https://invalid.example/v1",
                        api_key="fixture",
                    ),
                    requester=requester,
                )
                expected_calls = len(harness_sheet.rubric_items) + len(
                    agent_sheet.rubric_items
                )
                self.assertEqual(len(prompts), expected_calls)
                self.assertIsNotNone(scored.score)
                self.assertGreaterEqual(scored.score or 0, 0)
                self.assertLessEqual(scored.score or 0, 100)
                combined = json.loads(scored.score_path.read_text(encoding="utf-8"))
                self.assertEqual(combined["score_scale"], "0_100")
                self.assertEqual(
                    combined["judge_runtime"]["logical_calls"], expected_calls
                )


if __name__ == "__main__":
    unittest.main()
