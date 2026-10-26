from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import tracetrial_bench.result as result_module
import tracetrial_bench.batch as batch_module
from tracetrial_bench.types import (
    EvaluationResult,
    HarnessRunSpec,
    ModelBinding,
    TaskSpec,
    VerifierSpec,
)


class PendingEvaluationTests(unittest.TestCase):
    def test_scored_failed_execution_is_a_formal_fail_never_a_pass(self) -> None:
        self.assertEqual(result_module._overall_status("failed", "fail"), "fail")
        self.assertEqual(result_module._overall_status("failed", "pass"), "fail")
        self.assertEqual(result_module._overall_status("failed", "error"), "error")

    def test_pending_evaluation_has_no_score(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            factory = getattr(result_module, "pending_evaluation", None)

            self.assertTrue(callable(factory), "pending_evaluation must exist")
            evaluation = factory(run_dir)

            self.assertEqual(evaluation.status, "pending")
            self.assertIsNone(evaluation.score)
            self.assertEqual(
                evaluation.stdout_path,
                run_dir / "artifacts" / "verifier_output" / "stdout.log",
            )
            self.assertFalse(evaluation.stdout_path.exists())

    def test_write_pending_result_marks_run_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            trace_dir = run_dir / "artifacts" / "trace"
            trace_dir.mkdir(parents=True)
            diff_path = run_dir / "artifacts" / "workspace.diff"
            diff_path.write_text("No workspace changes detected.\n", encoding="utf-8")
            task = TaskSpec(
                task_id="task-1",
                task_dir=root / "task",
                prompt="prompt",
                workspace_dir=root / "task" / "workspace",
                verifier=VerifierSpec(entrypoint="verifier/run.sh"),
            )
            run_spec = HarnessRunSpec(
                harness="codex",
                model="model-alias",
                mode="docker",
            )
            binding = ModelBinding(
                requested="model-alias",
                provider="provider",
                canonical="model",
                harness_arg="model",
                binding_status="bound",
                binding_mode="model_registry",
                flag="--model",
                validation_status="registry_resolved",
            )
            trace_summary = {
                "status": "finished",
                "trace_complete": True,
                "exit_code": 0,
                "summary_path": str(trace_dir / "summary.json"),
                "events_path": str(trace_dir / "events.json"),
                "container": {
                    "archive_status": "complete",
                    "cleanup_status": "complete",
                },
                "token_usage": {"cached_input_tokens": 128},
                "step_count": 3,
                "step_count_details": {
                    "status": "derived",
                    "source": "per_call_token_usage",
                },
                "timing_metrics": {
                    "ttft_ms": 300.0,
                    "output_throughput_tokens_per_sec": 40.0,
                    "end_to_end_duration_ms": 5_000.0,
                },
                "cache_savings": {
                    "status": "token_only",
                    "reused_input_tokens": 128,
                },
                "total_cost": {
                    "status": "available",
                    "currency": "USD",
                    "amount": "0.01",
                },
            }

            try:
                result = result_module.write_result(
                    task=task,
                    run_spec=run_spec,
                    model_binding=binding,
                    run_dir=run_dir,
                    trace_summary=trace_summary,
                    evaluation=result_module.pending_evaluation(run_dir),
                    workspace_diff=diff_path,
                    evaluation_mode="run-only",
                )
            except TypeError as exc:
                self.fail(f"write_result rejected evaluation_mode: {exc}")

            self.assertEqual(result["status"], "run_complete")
            self.assertEqual(result["evaluation_mode"], "run-only")
            self.assertEqual(result["input_query"], "prompt")
            self.assertEqual(result["evaluation"]["status"], "pending")
            self.assertEqual(
                result["metrics"]["token_usage"]["cached_input_tokens"],
                128,
            )
            self.assertEqual(
                result["metrics"]["total_usage"]["cached_input_tokens"],
                128,
            )
            self.assertEqual(result["metrics"]["step_count"], 3)
            self.assertEqual(
                result["metrics"]["step_count_details"]["status"],
                "derived",
            )
            self.assertEqual(
                result["metrics"]["timing_metrics"]["ttft_ms"],
                300.0,
            )
            self.assertEqual(
                result["metrics"]["cache_savings"]["reused_input_tokens"],
                128,
            )
            self.assertEqual(result["metrics"]["total_cost"]["amount"], "0.01")
            self.assertIsNone(result["artifacts"]["verifier_stdout"])
            self.assertIsNone(result["artifacts"]["verifier_stderr"])

    def test_rate_limited_trace_is_reported_as_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            trace_dir = run_dir / "artifacts" / "trace"
            trace_dir.mkdir(parents=True)
            diff_path = run_dir / "artifacts" / "workspace.diff"
            diff_path.write_text("No workspace changes detected.\n", encoding="utf-8")
            task = TaskSpec(
                task_id="task-1",
                task_dir=root / "task",
                prompt="prompt",
                workspace_dir=root / "task" / "workspace",
                verifier=VerifierSpec(entrypoint="verifier/run.sh"),
            )
            run_spec = HarnessRunSpec(
                harness="codex",
                model="model-alias",
                mode="docker",
            )
            binding = ModelBinding(
                requested="model-alias",
                provider="provider",
                canonical="model",
                harness_arg="model",
                binding_status="bound",
                binding_mode="model_registry",
                flag="--model",
                validation_status="registry_resolved",
            )
            skip = {
                "schema_version": "harnessbench_rate_limit_skip_v1",
                "status": "skipped",
                "reason": "provider_rate_limit",
                "classification": "tpm",
            }
            evaluation = EvaluationResult(
                status="skipped",
                score=None,
                exit_code=None,
                stdout_path=run_dir / "artifacts" / "verifier_output" / "stdout.log",
                stderr_path=run_dir / "artifacts" / "verifier_output" / "stderr.log",
                score_path=None,
            )

            result = result_module.write_result(
                task=task,
                run_spec=run_spec,
                model_binding=binding,
                run_dir=run_dir,
                trace_summary={
                    "status": "failed",
                    "trace_complete": False,
                    "summary_path": str(trace_dir / "summary.json"),
                    "events_path": str(trace_dir / "events.json"),
                    "skip": skip,
                },
                evaluation=evaluation,
                workspace_diff=diff_path,
            )

            self.assertEqual(result["status"], "skipped")
            self.assertEqual(result["skip"], skip)
            self.assertEqual(result["execution"]["status"], "failed")
            self.assertEqual(result["evaluation"]["status"], "skipped")


class BatchStatusTests(unittest.TestCase):
    def test_run_complete_is_successful_but_not_a_score_pass(self) -> None:
        summarize = getattr(batch_module, "summarize_statuses", None)

        self.assertTrue(callable(summarize), "summarize_statuses must exist")
        counts = summarize(["run_complete", "pass", "fail", "error", "skipped"])

        self.assertEqual(counts["run_complete"], 1)
        self.assertEqual(counts["pass"], 1)
        self.assertEqual(counts["fail"], 1)
        self.assertEqual(counts["error"], 1)
        self.assertEqual(counts["skipped"], 1)
        self.assertEqual(counts["non_pass"], 2)
        self.assertEqual(counts["total"], 5)

    def test_resume_rejects_evaluation_mode_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            batch_dir = Path(temporary)
            work_items = [
                {"task": "/task", "harness": "codex", "model": "model"}
            ]
            (batch_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": "harnessbench_batch_v1",
                        "evaluation_mode": "run-only",
                        "work_items": work_items,
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "evaluation mode"):
                batch_module._validate_resume_manifest(
                    batch_dir,
                    {
                        "schema_version": "harnessbench_batch_v1",
                        "evaluation_mode": "run-and-score",
                        "work_items": work_items,
                    },
                )

    def test_resume_rejects_rate_limit_policy_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            batch_dir = Path(temporary)
            work_items = [
                {"task": "/task", "harness": "codex", "model": "model"}
            ]
            (batch_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": "harnessbench_batch_v1",
                        "evaluation_mode": "run-and-score",
                        "skip_on_rate_limit": True,
                        "work_items": work_items,
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "rate-limit policy"):
                batch_module._validate_resume_manifest(
                    batch_dir,
                    {
                        "schema_version": "harnessbench_batch_v1",
                        "evaluation_mode": "run-and-score",
                        "skip_on_rate_limit": False,
                        "work_items": work_items,
                    },
                )

    def test_batch_records_skipped_run_without_counting_it_as_non_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            item = batch_module.WorkItem(
                task=root / "task",
                harness="codex",
                model="model",
            )

            summary, summary_path = batch_module.execute_batch(
                work_items=[item],
                concurrency=1,
                output_dir=root / "runs",
                execute=lambda _: {
                    "status": "skipped",
                    "result_path": str(root / "run" / "result.json"),
                    "skip": {"reason": "provider_rate_limit"},
                },
                skip_on_rate_limit=True,
            )

            self.assertTrue(summary_path.is_file())
            self.assertEqual(summary["counts"]["skipped"], 1)
            self.assertEqual(summary["counts"]["non_pass"], 0)
            self.assertEqual(
                summary["results"][0]["skip_reason"],
                "provider_rate_limit",
            )
            manifest = json.loads(
                (summary_path.parent / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertTrue(manifest["skip_on_rate_limit"])


class ScoredResultUpdateTests(unittest.TestCase):
    def test_update_scored_result_preserves_execution_and_replaces_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            verifier_dir = run_dir / "artifacts" / "verifier_output"
            verifier_dir.mkdir(parents=True)
            stdout_path = verifier_dir / "stdout.log"
            stderr_path = verifier_dir / "stderr.log"
            score_path = verifier_dir / "score.json"
            stdout_path.write_text("score output\n", encoding="utf-8")
            stderr_path.write_text("", encoding="utf-8")
            score_path.write_text('{"score": 0.5}\n', encoding="utf-8")
            policy_path = run_dir / "artifacts" / "runner_policy.json"
            policy_path.write_text("{}\n", encoding="utf-8")
            execution = {
                "mode": "docker",
                "status": "finished",
                "container": {
                    "archive_status": "complete",
                    "cleanup_status": "complete",
                },
            }
            existing = {
                "schema_version": "0.1",
                "status": "run_complete",
                "evaluation_mode": "run-only",
                "result_path": str(run_dir / "result.json"),
                "execution": execution,
                "provider": {"source": "model_registry"},
                "model": {"requested": "model-alias"},
                "evaluation": {"status": "pending", "score": None},
                "artifacts": {
                    "trace_summary": "artifacts/trace/summary.json",
                    "verifier_stdout": None,
                    "verifier_stderr": None,
                    "verifier_score": None,
                },
            }
            evaluation = EvaluationResult(
                status="fail",
                score=0.5,
                exit_code=1,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                score_path=score_path,
            )
            updater = getattr(result_module, "update_scored_result", None)

            self.assertTrue(callable(updater), "update_scored_result must exist")
            updated = updater(
                run_dir=run_dir,
                existing_result=existing,
                evaluation=evaluation,
                raw_evaluation=evaluation,
                runner_policy={"hard_gates": {}, "violations": []},
                runner_policy_path=policy_path,
            )

            self.assertEqual(updated["status"], "fail")
            self.assertEqual(updated["evaluation_mode"], "score-only")
            self.assertEqual(updated["execution"], execution)
            self.assertEqual(updated["provider"], existing["provider"])
            self.assertEqual(updated["evaluation"]["effective_score"], 0.5)
            self.assertEqual(
                updated["artifacts"]["verifier_score"],
                "artifacts/verifier_output/score.json",
            )
            persisted = json.loads(
                (run_dir / "result.json").read_text(encoding="utf-8")
            )
            self.assertEqual(persisted, updated)


if __name__ == "__main__":
    unittest.main()
