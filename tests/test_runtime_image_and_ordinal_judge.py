from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harnessbench.container_runtime import ContainerRuntimeError, HarnessImage
from harnessbench.llm_judge import (
    JudgeRuntime,
    _judge_request_payload,
    _parse_binary_judge,
    run_layered_judge,
)
from harnessbench.evidence_finder import EvidenceFinder
from harnessbench.score_config import EvidenceSelector
from harnessbench.runner import _build_task_runtime_image
from harnessbench.task_resolver import resolve_task
from harnessbench.types import EvaluationResult, TaskSpec, VerifierSpec
from harnessbench.workspace import prepare_run_dir


class TaskRuntimeImageTests(unittest.TestCase):
    def test_build_rejects_dockerfile_that_bypasses_current_harness_base(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_dir = root / "task"
            (task_dir / "runtime").mkdir(parents=True)
            workspace_dir = task_dir / "workspace"
            workspace_dir.mkdir()
            (task_dir / "runtime" / "Dockerfile").write_text(
                "FROM python:3.11-slim\n",
                encoding="utf-8",
            )
            task = TaskSpec(
                task_id="task-1",
                task_dir=task_dir,
                prompt="prompt",
                workspace_dir=workspace_dir,
                verifier=VerifierSpec(entrypoint="verifier/run.sh"),
            )

            with patch(
                "harnessbench.runner._ensure_current_harness_base_image"
            ) as ensure_base:
                with self.assertRaisesRegex(ValueError, "ARG BASE_IMAGE"):
                    _build_task_runtime_image(
                        task,
                        "harnessbench/case-task-1:v1",
                        root / "artifacts" / "task_image_build.log",
                    )

            ensure_base.assert_not_called()

    def test_build_uses_current_hash_pinned_harness_image_as_base(self) -> None:
        local_image_reference = "harnessbench/openclaw:2026.6.9-local"
        local_image_id = "sha256:" + "a" * 64
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_dir = root / "task"
            runtime_dir = task_dir / "runtime"
            workspace_dir = task_dir / "workspace"
            runtime_dir.mkdir(parents=True)
            workspace_dir.mkdir()
            (runtime_dir / "Dockerfile").write_text(
                "ARG BASE_IMAGE=private/native-base:latest\n"
                "FROM ${BASE_IMAGE}\n",
                encoding="utf-8",
            )
            task = TaskSpec(
                task_id="task-1",
                task_dir=task_dir,
                prompt="prompt",
                workspace_dir=workspace_dir,
                verifier=VerifierSpec(entrypoint="verifier/run.sh"),
            )
            build_log = root / "artifacts" / "task_image_build.log"

            current_image = HarnessImage(
                reference=local_image_reference,
                image_id=local_image_id,
                digest=None,
                built=False,
            )
            with (
                patch(
                    "harnessbench.runner._ensure_current_harness_base_image",
                    return_value=current_image,
                ) as ensure_base,
                patch("harnessbench.runner.subprocess.run") as run,
            ):
                run.return_value = subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="task image built\n",
                    stderr="",
                )

                _build_task_runtime_image(
                    task,
                    "harnessbench/case-task-1:v1",
                    build_log,
                    harness="openclaw",
                )

            ensure_base.assert_called_once_with(
                "openclaw",
                build_log=build_log.parent / "harness_base_image" / "build.log",
                build_config=None,
            )
            build_command = run.call_args.args[0]
            base_argument = build_command.index("--build-arg")
            self.assertEqual(
                build_command[base_argument + 1],
                f"BASE_IMAGE={local_image_reference}",
            )
            self.assertIn(local_image_reference, build_log.read_text(encoding="utf-8"))
            self.assertIn(local_image_id, build_log.read_text(encoding="utf-8"))

    def test_build_timeout_preserves_base_identity_and_partial_output(self) -> None:
        local_image_reference = "harnessbench/openclaw:2026.6.9-local"
        local_image_id = "sha256:" + "a" * 64
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_dir = root / "task"
            runtime_dir = task_dir / "runtime"
            workspace_dir = task_dir / "workspace"
            runtime_dir.mkdir(parents=True)
            workspace_dir.mkdir()
            (runtime_dir / "Dockerfile").write_text(
                "ARG BASE_IMAGE=private/native-base:latest\n"
                "FROM ${BASE_IMAGE}\n",
                encoding="utf-8",
            )
            task = TaskSpec(
                task_id="task-1",
                task_dir=task_dir,
                prompt="prompt",
                workspace_dir=workspace_dir,
                verifier=VerifierSpec(entrypoint="verifier/run.sh"),
            )
            build_log = root / "artifacts" / "task_image_build.log"

            current_image = HarnessImage(
                reference=local_image_reference,
                image_id=local_image_id,
                digest=None,
                built=False,
            )
            with (
                patch(
                    "harnessbench.runner._ensure_current_harness_base_image",
                    return_value=current_image,
                ),
                patch("harnessbench.runner.subprocess.run") as run,
            ):
                run.side_effect = subprocess.TimeoutExpired(
                    cmd=["docker", "image", "build"],
                    timeout=1800,
                    output="partial build output\n",
                    stderr="build timed out\n",
                )

                with self.assertRaises(ContainerRuntimeError):
                    _build_task_runtime_image(
                        task,
                        "harnessbench/case-task-1:v1",
                        build_log,
                        harness="openclaw",
                    )

            log = build_log.read_text(encoding="utf-8")
            self.assertIn(local_image_reference, log)
            self.assertIn(local_image_id, log)
            self.assertIn("partial build output", log)
            self.assertIn("timed out", log)


class LayeredJudgeTests(unittest.TestCase):
    def test_deepseek_judge_keeps_thinking_disabled(self) -> None:
        for runtime in (
            JudgeRuntime(
                provider="deepseek",
                model="deepseek-v4-flash",
                base_url="https://api.deepseek.com",
                api_key="test-key",
            ),
            JudgeRuntime(
                provider="custom-gateway",
                model="deepseek-v4-flash",
                base_url="https://gateway.example/v1",
                api_key="test-key",
            ),
        ):
            with self.subTest(provider=runtime.provider):
                payload = _judge_request_payload(runtime, "Judge this evidence.")
                self.assertEqual(payload["thinking"], {"type": "disabled"})
                self.assertNotIn("reasoning_effort", payload)

    def test_binary_judge_schema_is_strict(self) -> None:
        self.assertEqual(
            _parse_binary_judge('{"score": 1, "reason": "proved"}'),
            {"score": 1, "reason": "proved"},
        )
        with self.assertRaises(ValueError):
            _parse_binary_judge('{"score": true, "reason": "wrong type"}')

    def test_layered_scoring_calls_each_rubric_and_scores_out_of_100(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_dir = root / "task"
            (task_dir / "workspace").mkdir(parents=True)
            (task_dir / "verifier").mkdir()
            (task_dir / "prompt.md").write_text("Complete the fixture task.\n")
            (task_dir / "task.yaml").write_text(
                "id: fixture\n"
                "scoring:\n"
                "  version: layered_100_v1\n"
                "  harness_score: verifier/harness_score.yaml\n"
                "  agent_score: verifier/agent_score.yaml\n"
            )
            for layer, prefix, keyword in (
                ("harness", "h", "VISIBLE_TOKEN"),
                ("agent", "a", "AGENT_ARTIFACT_TOKEN"),
            ):
                (task_dir / "verifier" / f"{layer}_score.yaml").write_text(
                    "version: score_v1\n"
                    f"layer: {layer}\n"
                    "items:\n"
                    f"  - id: {prefix}-rule\n"
                    "    type: rule\n"
                    "    criterion: deterministic check\n"
                    f"  - id: {prefix}-rubric\n"
                    "    type: rubric\n"
                    "    criterion: semantic check\n"
                    "    evidence:\n"
                    "      event_types:\n"
                    "        - tool\n"
                    "      fields:\n"
                    "        - tool_result.content\n"
                    "      keywords:\n"
                    f"        - {keyword}\n"
                    "      match: any\n"
                    "      case_sensitive: false\n"
                    "      max_events: 5\n"
                )
            (task_dir / "verifier" / "run.sh").write_text("#!/bin/sh\n")
            task = resolve_task(task_dir)
            layout = prepare_run_dir(
                task,
                root / "run",
                private_dir=root / "private",
            )
            shutil.copytree(layout.workspace_dir, layout.baseline_dir)
            artifact_dir = layout.workspace_dir / "output"
            artifact_dir.mkdir()
            (artifact_dir / "report.md").write_text(
                "AGENT_ARTIFACT_TOKEN proves the deliverable.\n",
                encoding="utf-8",
            )
            verifier_output = layout.artifacts_dir / "verifier_output"
            verifier_output.mkdir(parents=True)
            objective_score = verifier_output / "score.json"
            objective_score.write_text(
                json.dumps(
                    {
                        "schema_version": "harnessbench_rule_results_v1",
                        "rule_results": {
                            "harness": [{"id": "h-rule", "score": 1, "reason": "ok"}],
                            "agent": [{"id": "a-rule", "score": 0, "reason": "failed"}],
                        },
                    }
                ),
                encoding="utf-8",
            )
            objective_stdout = verifier_output / "stdout.log"
            objective_stderr = verifier_output / "stderr.log"
            objective_stdout.write_text("{}\n", encoding="utf-8")
            objective_stderr.write_text("", encoding="utf-8")
            trace_dir = layout.artifacts_dir / "trace" / "run-1"
            trace_dir.mkdir(parents=True)
            events_path = trace_dir / "events.json"
            events_path.write_text(
                json.dumps([{"seq": 1, "event_type": "tool", "tool_result": {"content": "VISIBLE_TOKEN"}}]),
                encoding="utf-8",
            )
            objective = EvaluationResult(
                status="pass",
                score=None,
                exit_code=0,
                stdout_path=objective_stdout,
                stderr_path=objective_stderr,
                score_path=objective_score,
            )
            prompts: list[str] = []

            def requester(_runtime: JudgeRuntime, prompt: str) -> str:
                prompts.append(prompt)
                return json.dumps({"score": 1, "reason": "fixture says pass"})

            result = run_layered_judge(
                task=task,
                layout=layout,
                objective=objective,
                trace_summary={"events_path": str(events_path)},
                runtime=JudgeRuntime(
                    provider="fake",
                    model="judge",
                    base_url="https://judge.invalid/v1",
                    api_key="test-key",
                ),
                requester=requester,
            )

            self.assertEqual(result.status, "fail")
            self.assertEqual(result.score, 75.0)
            self.assertEqual(len(prompts), 2)
            self.assertTrue(all("Complete the fixture task" in prompt for prompt in prompts))
            self.assertIn("AGENT rubric", prompts[0])
            self.assertIn("Artifact matches are the primary", prompts[0])
            self.assertNotIn("<agent_scoring_results>", prompts[0])
            self.assertIn("HARNESS rubric", prompts[1])
            self.assertIn("<agent_scoring_results>", prompts[1])
            self.assertIn("id=a-rule; type=rule; score=0", prompts[1])
            self.assertIn("id=a-rubric; type=rubric; score=1", prompts[1])
            combined = json.loads(result.score_path.read_text(encoding="utf-8"))
            self.assertEqual(combined["harness_score"]["points"], 50.0)
            self.assertEqual(combined["agent_score"]["points"], 25.0)
            agent_rubric = combined["agent_score"]["items"][1]
            self.assertFalse(agent_rubric["evidence_forced_zero"])
            self.assertEqual(agent_rubric["evidence_focus"], "artifacts")
            self.assertEqual(agent_rubric["evidence_artifact_count"], 1)
            self.assertEqual(
                combined["judge_runtime"]["scoring_order"],
                [
                    "rules:harness",
                    "rules:agent",
                    "rubric:agent:a-rubric",
                    "rubric:harness:h-rubric",
                ],
            )

    def test_evidence_finder_matches_only_selected_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            events_path = run_dir / "artifacts" / "trace" / "events.json"
            events_path.parent.mkdir(parents=True)
            events_path.write_text(
                json.dumps(
                    [
                        {"seq": 1, "event_type": "tool", "tool_result": {"content": "needle"}},
                        {"seq": 2, "event_type": "tool", "raw_event": {"content": "needle"}},
                    ]
                )
            )
            finder = EvidenceFinder(
                run_dir=run_dir,
                trace_summary={"events_path": str(events_path)},
            )
            found = finder.find(
                EvidenceSelector(
                    event_types=("tool",),
                    fields=("tool_result.content",),
                    keywords=("NEEDLE",),
                )
            )
            self.assertEqual(found.matched_event_count, 1)
            self.assertEqual(found.matches[0].seq, 1)

    def test_evidence_finder_bounds_large_fields_and_keeps_hit_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            events_path = run_dir / "artifacts" / "trace" / "events.json"
            events_path.parent.mkdir(parents=True)
            large = "HEADER SNAPSHOT_ID=snap_test\n" + ("A" * 400_000)
            large += "... 123456 bytes OMITTED ..." + ("Z" * 400_000)
            large += "\nLINE=009012 END"
            events_path.write_text(
                json.dumps(
                    [
                        {
                            "seq": 1,
                            "event_type": "tool",
                            "tool_result": {"content": large},
                        }
                    ]
                )
            )
            finder = EvidenceFinder(
                run_dir=run_dir,
                trace_summary={"events_path": str(events_path)},
            )
            found = finder.find(
                EvidenceSelector(
                    event_types=("tool",),
                    fields=("tool_result.content",),
                    keywords=("SNAPSHOT_ID", "OMITTED", "LINE=009012"),
                )
            )

            selected = found.matches[0].matched_fields["tool_result.content"]
            self.assertLessEqual(len(selected), 20_000)
            self.assertIn("SNAPSHOT_ID=snap_test", selected)
            self.assertIn("123456 bytes OMITTED", selected)
            self.assertIn("LINE=009012 END", selected)
            self.assertIn("evidence chars", selected)

    def test_evidence_finder_reduces_large_tool_catalog_to_complete_matching_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            events_path = run_dir / "artifacts" / "trace" / "events.json"
            events_path.parent.mkdir(parents=True)
            irrelevant = [
                {
                    "name": f"unrelated_{index}",
                    "description": "ordinary helper " + ("x" * 1_000),
                    "input_schema": {"type": "object", "properties": {}},
                }
                for index in range(80)
            ]
            relevant = [
                {
                    "name": "violet_relay",
                    "description": "Return the protected token.",
                    "input_schema": {"type": "object", "properties": {}},
                },
                {
                    "name": "copper_lens",
                    "description": "Validate the protected token.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "mode": {"type": "string", "enum": ["bench-audit"]}
                        },
                    },
                },
            ]
            events_path.write_text(
                json.dumps(
                    [
                        {
                            "seq": 1,
                            "event_type": "tool_inventory",
                            "source": "provider_request",
                            "model_visible": True,
                            "complete": True,
                            "tool_count": len(irrelevant) + len(relevant),
                            "tools": [*irrelevant, *relevant],
                        }
                    ]
                )
            )
            finder = EvidenceFinder(
                run_dir=run_dir,
                trace_summary={"events_path": str(events_path)},
            )
            found = finder.find(
                EvidenceSelector(
                    event_types=("tool_inventory",),
                    fields=("source", "tools"),
                    keywords=("provider_request", "violet_relay", "copper_lens", "bench-audit"),
                    match="all",
                )
            )

            selected = found.matches[0].matched_fields["tools"]
            serialized = json.dumps(selected)
            self.assertLess(len(serialized), 35_000)
            self.assertIn("violet_relay", serialized)
            self.assertIn("copper_lens", serialized)
            self.assertIn("bench-audit", serialized)
            self.assertNotIn("unrelated_0", serialized)
            self.assertEqual(selected[-1]["_evidence_reduction"]["original_items"], 82)

    def test_agent_evidence_prefers_changed_artifacts_over_baseline_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            workspace = run_dir / "agent" / "workspace"
            baseline = run_dir / "private" / ".baseline_workspace"
            artifacts = run_dir / "artifacts"
            workspace.mkdir(parents=True)
            baseline.mkdir(parents=True)
            (workspace / "input.md").write_text("ARTIFACT_TOKEN in unchanged input\n")
            (baseline / "input.md").write_text("ARTIFACT_TOKEN in unchanged input\n")
            output = workspace / "output"
            output.mkdir()
            (output / "result.md").write_text("ARTIFACT_TOKEN in Agent result\n")
            events_path = artifacts / "trace" / "events.json"
            events_path.parent.mkdir(parents=True)
            events_path.write_text(
                json.dumps(
                    [
                        {
                            "seq": 1,
                            "event_type": "assistant_message",
                            "content": "ARTIFACT_TOKEN was written",
                        }
                    ]
                )
            )
            finder = EvidenceFinder(
                run_dir=run_dir,
                workspace_dir=workspace,
                baseline_dir=baseline,
                trace_summary={"events_path": str(events_path)},
            )
            found = finder.find(
                EvidenceSelector(
                    event_types=("assistant_message",),
                    fields=("content",),
                    keywords=("ARTIFACT_TOKEN",),
                ),
                focus="artifacts",
            )
            self.assertEqual(found.focus, "artifacts")
            self.assertEqual(found.returned_artifact_count, 1)
            self.assertEqual(found.artifact_matches[0].path, "output/result.md")
            self.assertNotIn("input.md", json.dumps(found.to_dict()))

    def test_small_artifact_match_keeps_the_complete_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            workspace = run_dir / "agent" / "workspace"
            baseline = run_dir / "private" / ".baseline_workspace"
            artifacts = run_dir / "artifacts"
            workspace.mkdir(parents=True)
            baseline.mkdir(parents=True)
            output = workspace / "output"
            output.mkdir()
            content = "FIRST_TOKEN\n" + ("middle\n" * 200) + "LAST_TOKEN\n"
            (output / "facts.txt").write_text(content)
            events_path = artifacts / "trace" / "events.json"
            events_path.parent.mkdir(parents=True)
            events_path.write_text("[]")
            finder = EvidenceFinder(
                run_dir=run_dir,
                workspace_dir=workspace,
                baseline_dir=baseline,
                trace_summary={"events_path": str(events_path)},
            )

            found = finder.find(
                EvidenceSelector(
                    event_types=("artifact",),
                    fields=("path", "content"),
                    keywords=("FIRST_TOKEN", "LAST_TOKEN"),
                    match="all",
                ),
                focus="artifacts",
            )

            self.assertEqual(found.artifact_matches[0].snippet, content)
            self.assertNotIn("\n...\n", found.artifact_matches[0].snippet)


if __name__ == "__main__":
    unittest.main()
