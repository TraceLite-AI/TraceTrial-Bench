from __future__ import annotations

import json
import importlib
import hashlib
import py_compile
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import tracetrial_bench.runner as runner_module
import tracetrial_bench.workspace as workspace_module
from tracetrial_bench.container_runtime import ContainerExecution, HarnessImage
from tracetrial_bench.runner import BenchmarkRunConfig, BenchmarkRunner
from tracetrial_bench.types import (
    BusinessSimulatorSpec,
    ExecutionProbeSpec,
    NativeInstructionsSpec,
    TaskSpec,
    VerifierSpec,
)
from tracetrial_bench.workspace import RunLayout


class ScoreableExecutionTests(unittest.TestCase):
    def _task(self, *, probe: bool) -> TaskSpec:
        return TaskSpec(
            task_id="task-1",
            task_dir=Path("/task"),
            prompt="prompt",
            workspace_dir=Path("/task/workspace"),
            verifier=VerifierSpec(entrypoint="verifier/run.sh"),
            execution_probe=(
                ExecutionProbeSpec(
                    version="lifecycle_v1",
                    requires=(
                        "native_async_child_dispatch_v1",
                        "native_child_return_boundary_v1",
                        "native_pushed_child_completion_v1",
                    ),
                )
                if probe
                else None
            ),
        )

    def test_finished_execution_remains_scoreable(self) -> None:
        self.assertTrue(
            runner_module._execution_is_scoreable(
                self._task(probe=False),
                execution_status="finished",
                trace_summary={"status": "finished"},
            )
        )

    def test_auditable_failed_async_child_probe_is_scoreable(self) -> None:
        self.assertTrue(
            runner_module._execution_is_scoreable(
                self._task(probe=True),
                execution_status="failed",
                trace_summary={
                    "status": "failed",
                    "structured_trace_status": "available",
                },
            )
        )

    def test_ordinary_or_unstructured_failure_is_not_scoreable(self) -> None:
        failed_summary = {
            "status": "failed",
            "structured_trace_status": "available",
        }
        self.assertFalse(
            runner_module._execution_is_scoreable(
                self._task(probe=False),
                execution_status="failed",
                trace_summary=failed_summary,
            )
        )
        self.assertFalse(
            runner_module._execution_is_scoreable(
                self._task(probe=True),
                execution_status="failed",
                trace_summary={"status": "failed"},
            )
        )


class ExistingRunLayoutTests(unittest.TestCase):
    def test_scoring_layout_copies_workspace_and_keeps_verifier_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_dir = root / "task"
            (task_dir / "verifier").mkdir(parents=True)
            (task_dir / "workspace").mkdir()
            (task_dir / "task.yaml").write_text("id: task-1\n", encoding="utf-8")
            (task_dir / "turns.json").write_text(
                '{"turns": ["first", "second"]}\n',
                encoding="utf-8",
            )
            (task_dir / "verifier" / "run.sh").write_text(
                "#!/bin/sh\n",
                encoding="utf-8",
            )
            task = TaskSpec(
                task_id="task-1",
                task_dir=task_dir,
                prompt="prompt",
                workspace_dir=task_dir / "workspace",
                verifier=VerifierSpec(entrypoint="verifier/run.sh"),
            )
            run_dir = root / "run"
            (run_dir / "agent" / "workspace").mkdir(parents=True)
            (run_dir / "artifacts").mkdir()
            private_dir = root / "private"
            factory = getattr(workspace_module, "prepare_scoring_layout", None)

            self.assertTrue(callable(factory), "prepare_scoring_layout must exist")
            layout = factory(task, run_dir, private_dir=private_dir)

            self.assertNotEqual(
                layout.workspace_dir,
                (run_dir / "agent" / "workspace").resolve(),
            )
            self.assertTrue(layout.workspace_dir.is_dir())
            self.assertTrue((private_dir / "verifier" / "run.sh").is_file())
            self.assertEqual(
                (private_dir / "turns.json").read_text(encoding="utf-8"),
                '{"turns": ["first", "second"]}\n',
            )
            self.assertTrue((private_dir / "workspace").exists())
            self.assertFalse((run_dir / "private").exists())

    def test_scoring_layout_reconstructs_private_inputs_without_linking_workspace(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_dir = root / "task"
            workspace_source = task_dir / "workspace"
            verifier_dir = task_dir / "verifier"
            simulator_dir = task_dir / "simulator"
            workspace_source.mkdir(parents=True)
            verifier_dir.mkdir()
            simulator_dir.mkdir()
            (task_dir / "task.yaml").write_text("id: task-1\n", encoding="utf-8")
            (verifier_dir / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (workspace_source / "baseline.txt").write_text("baseline\n", encoding="utf-8")
            (simulator_dir / "scenario.json").write_text("{}\n", encoding="utf-8")
            (simulator_dir / "server.py").write_text("\n", encoding="utf-8")
            task = TaskSpec(
                task_id="task-1",
                task_dir=task_dir,
                prompt="prompt",
                workspace_dir=workspace_source,
                verifier=VerifierSpec(entrypoint="verifier/run.sh"),
                business_simulator=BusinessSimulatorSpec(
                    version="unix_json_v1",
                    scenario_path=simulator_dir / "scenario.json",
                    server_path=simulator_dir / "server.py",
                    cli_path=Path("taskctl"),
                ),
            )
            run_dir = root / "run"
            persisted_workspace = run_dir / "agent" / "workspace"
            persisted_workspace.mkdir(parents=True)
            (persisted_workspace / "answer.txt").write_text("answer\n", encoding="utf-8")
            (run_dir / "artifacts").mkdir()
            baseline = root / "persisted-baseline"
            baseline.mkdir()
            (baseline / "baseline.txt").write_text("baseline\n", encoding="utf-8")
            simulator_evidence_dir = root / "simulator-evidence"
            simulator_evidence_dir.mkdir()
            evidence = {}
            for key, filename, payload in (
                ("ledger", "ledger.jsonl", "{\"seq\": 1}\n"),
                ("final_state", "final_state.json", "{\"finalized\": true}\n"),
                ("scenario", "scenario.json", "{}\n"),
                ("instance", "instance.json", "{}\n"),
                ("release_manifest", "release_manifest.json", "{}\n"),
            ):
                path = simulator_evidence_dir / filename
                path.write_text(payload, encoding="utf-8")
                evidence[key] = str(path)

            layout = workspace_module.prepare_scoring_layout(
                task,
                run_dir,
                private_dir=root / "private",
                baseline_workspace=baseline,
                simulator_evidence=evidence,
            )

            self.assertNotEqual(layout.workspace_dir, persisted_workspace)
            self.assertEqual(
                (layout.workspace_dir / "answer.txt").read_text(encoding="utf-8"),
                "answer\n",
            )
            (layout.workspace_dir / "answer.txt").write_text("changed\n", encoding="utf-8")
            self.assertEqual(
                (persisted_workspace / "answer.txt").read_text(encoding="utf-8"),
                "answer\n",
            )
            self.assertTrue((layout.baseline_dir / "baseline.txt").is_file())
            self.assertTrue((layout.private_dir / "simulator" / "scenario.json").is_file())
            self.assertTrue((layout.private_dir / "simulator" / "state.json").is_file())
            self.assertTrue((layout.private_dir / "simulator" / "ledger.jsonl").is_file())

    def test_legacy_native_instruction_run_requires_persisted_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_dir = root / "task"
            (task_dir / "workspace").mkdir(parents=True)
            (task_dir / "verifier").mkdir()
            (task_dir / "task.yaml").write_text("id: task-1\n", encoding="utf-8")
            (task_dir / "verifier" / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            native_manifest = task_dir / "native.json"
            native_manifest.write_text("{}\n", encoding="utf-8")
            task = TaskSpec(
                task_id="task-1",
                task_dir=task_dir,
                prompt="prompt",
                workspace_dir=task_dir / "workspace",
                verifier=VerifierSpec(entrypoint="verifier/run.sh"),
                native_instructions=NativeInstructionsSpec(
                    version="scoped_files_v1",
                    manifest_path=native_manifest,
                ),
            )
            run_dir = root / "run"
            (run_dir / "agent" / "workspace").mkdir(parents=True)
            (run_dir / "artifacts").mkdir()

            with self.assertRaisesRegex(ValueError, "exact verifier baseline"):
                workspace_module.prepare_scoring_layout(
                    task,
                    run_dir,
                    private_dir=root / "private",
                )


class ExistingRunContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.task_dir = self.root / "task"
        (self.task_dir / "workspace").mkdir(parents=True)
        (self.task_dir / "verifier").mkdir()
        (self.task_dir / "task.yaml").write_text("id: task-1\n", encoding="utf-8")
        (self.task_dir / "prompt.md").write_text("prompt\n", encoding="utf-8")
        (self.task_dir / "verifier" / "run.sh").write_text(
            "#!/bin/sh\nprintf '{\"pass\": true, \"score\": 1.0}\\n'\n",
            encoding="utf-8",
        )
        self.run_dir = self.root / "run"
        (self.run_dir / "agent" / "workspace").mkdir(parents=True)
        self.container_root = self.run_dir / "artifacts" / "container"
        (self.container_root / "trace").mkdir(parents=True)
        self.container_id = "a" * 64
        self.image_id = "sha256:" + "b" * 64
        self.lifecycle = {
            "schema_version": "harnessbench_container_lifecycle_v1",
            "run_token": "fixture-run",
            "container_name": "harnessbench-codex-fixture-run",
            "container_id": self.container_id,
            "task_id": "task-1",
            "harness": "codex",
            "model": {
                "alias": "model-alias",
                "provider": "openai",
                "model": "model",
                "base_url": "http://example.invalid/v1",
                "api_mode": "chat_completions",
                "harness": "codex",
                "harness_model": "model",
                "harness_provider": None,
            },
            "image": {
                "reference": "harnessbench/codex:test",
                "image_id": self.image_id,
                "digest": None,
                "built": False,
            },
            "gateway": None,
            "archive_status": "complete",
            "cleanup_status": "complete",
        }
        self.trace_summary = {
            "status": "finished",
            "trace_complete": True,
            "structured_trace_status": "available",
            "exit_code": 0,
            "summary_path": str(self.container_root / "trace" / "summary.json"),
            "events_path": str(self.container_root / "trace" / "events.json"),
        }
        self.result = {
            "schema_version": "0.1",
            "status": "run_complete",
            "task_id": "task-1",
            "task_dir": str(self.task_dir),
            "harness": {"name": "codex"},
            "model": {
                "requested": "model-alias",
                "provider": "openai",
                "canonical": "model",
                "harness_arg": "model",
            },
            "execution": {
                "mode": "docker",
                "status": "finished",
                "docker_image": self.image_id,
                "gateway": None,
                "container": {
                    "container_id": self.container_id,
                    "image_reference": "harnessbench/codex:test",
                    "image_id": self.image_id,
                    "image_digest": None,
                    "image_built": False,
                    "archive_status": "complete",
                    "cleanup_status": "complete",
                    "gateway": None,
                    "lifecycle": "artifacts/container_lifecycle.json",
                },
            },
            "artifacts": {
                "trace_summary": "artifacts/container/trace/summary.json",
                "trace_events": "artifacts/container/trace/events.json",
                "container_archive_manifest": "artifacts/container/archive_manifest.json",
                "verifier_gate": "artifacts/verifier_gate.json",
                "business_simulator_evidence": None,
            },
        }
        self.resolved_config = {
            "task": str(self.task_dir),
            "harness": "codex",
            "model": "model-alias",
            "mode": "docker",
            "effective_timeout": 30,
            "model_registry": str(self.root / "model.toml"),
            "judge_model": None,
            "invocation_policy": {},
        }
        self._write_metadata()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_loads_valid_finished_docker_run(self) -> None:
        loader = getattr(runner_module, "load_existing_run_context", None)

        self.assertTrue(callable(loader), "load_existing_run_context must exist")
        context = loader(self.run_dir)

        self.assertEqual(context.task.task_id, "task-1")
        self.assertEqual(context.trace_summary["status"], "finished")
        self.assertEqual(context.container_evidence["cleanup_status"], "complete")

    def test_rejects_incomplete_container_cleanup(self) -> None:
        self.result["execution"]["container"]["cleanup_status"] = "failed"
        self._write_metadata()
        loader = getattr(runner_module, "load_existing_run_context", None)
        self.assertTrue(callable(loader), "load_existing_run_context must exist")

        with self.assertRaisesRegex(ValueError, "archive and cleanup"):
            loader(self.run_dir)

    def test_rejects_lifecycle_outside_run_artifacts(self) -> None:
        outside = self.root / "outside.json"
        outside.write_text(json.dumps(self.lifecycle), encoding="utf-8")
        self.result["execution"]["container"]["lifecycle"] = str(outside)
        self._write_metadata()
        loader = getattr(runner_module, "load_existing_run_context", None)
        self.assertTrue(callable(loader), "load_existing_run_context must exist")

        with self.assertRaisesRegex(ValueError, "lifecycle evidence"):
            loader(self.run_dir)

    def test_rejects_task_identity_mismatch(self) -> None:
        self.result["task_id"] = "different-task"
        self._write_metadata()
        loader = getattr(runner_module, "load_existing_run_context", None)
        self.assertTrue(callable(loader), "load_existing_run_context must exist")

        with self.assertRaisesRegex(ValueError, "task metadata does not match"):
            loader(self.run_dir)

    def test_rejects_modified_container_archive(self) -> None:
        (self.container_root / "trace" / "events.json").write_text(
            '[{"tampered": true}]\n',
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "archive manifest"):
            runner_module.load_existing_run_context(self.run_dir)

    def test_rejects_rewritten_container_archive_manifest(self) -> None:
        (self.container_root / "trace" / "events.json").write_text(
            '[{"tampered": true}]\n',
            encoding="utf-8",
        )
        _write_archive_manifest(self.container_root)

        with self.assertRaisesRegex(ValueError, "verifier gate"):
            runner_module.load_existing_run_context(self.run_dir)

    def test_resolves_relative_trace_paths_from_container_archive_root(self) -> None:
        self.trace_summary["summary_path"] = "trace/summary.json"
        self.trace_summary["events_path"] = "trace/events.json"
        self._write_metadata()

        context = runner_module.load_existing_run_context(self.run_dir)

        self.assertEqual(context.trace_summary["summary_path"], "trace/summary.json")
        self.assertEqual(context.trace_summary["events_path"], "trace/events.json")

    def test_resolves_legacy_trace_paths_written_from_relative_output_dir(self) -> None:
        legacy_prefix = Path("outputs") / self.run_dir.name / "artifacts" / "container"
        self.trace_summary["summary_path"] = str(
            legacy_prefix / "trace" / "summary.json"
        )
        self.trace_summary["events_path"] = str(
            legacy_prefix / "trace" / "events.json"
        )
        self._write_metadata()

        context = runner_module.load_existing_run_context(self.run_dir)

        self.assertEqual(
            context.trace_summary["summary_path"],
            str(legacy_prefix / "trace" / "summary.json"),
        )

    def _write_metadata(self) -> None:
        (self.run_dir / "artifacts" / "container_lifecycle.json").write_text(
            json.dumps(self.lifecycle),
            encoding="utf-8",
        )
        (self.container_root / "trace" / "summary.json").write_text(
            json.dumps(self.trace_summary),
            encoding="utf-8",
        )
        (self.container_root / "trace" / "events.json").write_text(
            "[]\n",
            encoding="utf-8",
        )
        _write_archive_manifest(self.container_root)
        lifecycle_sha256 = hashlib.sha256(
            (self.run_dir / "artifacts" / "container_lifecycle.json").read_bytes()
        ).hexdigest()
        (self.run_dir / "artifacts" / "verifier_gate.json").write_text(
            json.dumps(
                {
                    "schema_version": "harnessbench_verifier_gate_v1",
                    "verifier_execution": "host-only",
                    "container_id": self.container_id,
                    "image_id": self.image_id,
                    "archive_status": "complete",
                    "cleanup_status": "complete",
                    "container_lifecycle": "artifacts/container_lifecycle.json",
                    "container_lifecycle_sha256": lifecycle_sha256,
                    "container_archive_manifest": (
                        "artifacts/container/archive_manifest.json"
                    ),
                    "container_archive_manifest_sha256": hashlib.sha256(
                        (self.container_root / "archive_manifest.json").read_bytes()
                    ).hexdigest(),
                }
            ),
            encoding="utf-8",
        )
        (self.run_dir / "result.json").write_text(
            json.dumps(self.result),
            encoding="utf-8",
        )
        (self.run_dir / "config.resolved.json").write_text(
            json.dumps(self.resolved_config),
            encoding="utf-8",
        )


class ScoreOnlyTests(ExistingRunContextTests):
    def test_scores_existing_run_without_starting_container(self) -> None:
        registry = self.root / "model.toml"
        registry.write_text(
            "[models.model-alias]\n"
            'provider = "openai"\n'
            'model = "model"\n'
            'base_url = "http://example.invalid/v1"\n'
            'api_mode = "chat_completions"\n'
            'api_key = "secret"\n',
            encoding="utf-8",
        )
        registry.chmod(0o600)
        runtime = Mock()
        runner = BenchmarkRunner(container_runtime=runtime)
        config_type = getattr(runner_module, "BenchmarkScoreConfig", None)

        self.assertTrue(callable(config_type), "BenchmarkScoreConfig must exist")
        self.assertTrue(callable(getattr(runner, "score", None)), "runner.score must exist")
        result = runner.score(
            config_type(run_dir=self.run_dir, model_registry=registry)
        )

        runtime.run.assert_not_called()
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["evaluation_mode"], "score-only")
        self.assertEqual(result["evaluation"]["score"], 1.0)
        self.assertTrue(
            (self.run_dir / "artifacts" / "verifier_output" / "score.json").is_file()
        )
        persisted = json.loads(
            (self.run_dir / "result.json").read_text(encoding="utf-8")
        )
        self.assertEqual(persisted["evaluation"]["status"], "pass")

    def test_repeated_score_replaces_judge_outputs_only(self) -> None:
        self._enable_llm_judge()
        registry = self.root / "model.toml"
        registry.write_text(
            "[models.model-alias]\n"
            'provider = "openai"\n'
            'model = "model"\n'
            'base_url = "http://example.invalid/v1"\n'
            'api_mode = "chat_completions"\n'
            'api_key = "secret"\n',
            encoding="utf-8",
        )
        registry.chmod(0o600)
        runner = BenchmarkRunner(
            container_runtime=Mock(),
            judge_requester=_passing_judge,
        )
        config = runner_module.BenchmarkScoreConfig(
            run_dir=self.run_dir,
            model_registry=registry,
        )
        protected_paths = (
            self.run_dir / "artifacts" / "container_lifecycle.json",
            self.container_root / "trace" / "summary.json",
            self.container_root / "trace" / "events.json",
        )
        before = {path: path.read_bytes() for path in protected_paths}

        first = runner.score(config)
        second = runner.score(config)

        self.assertEqual(first["status"], "pass")
        self.assertEqual(second["status"], "pass")
        self.assertEqual(before, {path: path.read_bytes() for path in protected_paths})
        self.assertTrue(
            (self.run_dir / "artifacts" / "llm_judge" / "combined_score.json").is_file()
        )
        combined_score = json.loads(
            (
                self.run_dir
                / "artifacts"
                / "llm_judge"
                / "combined_score.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(combined_score["score_scale"], "0_100")
        self.assertEqual(combined_score["score"], 100.0)
        self.assertEqual(
            list((self.run_dir / "artifacts").glob(".scoring-stage-*")),
            [],
        )

    def test_rejected_credential_scan_does_not_modify_persisted_workspace(self) -> None:
        registry = self.root / "model.toml"
        registry.write_text(
            "[models.model-alias]\n"
            'provider = "openai"\n'
            'model = "model"\n'
            'base_url = "http://example.invalid/v1"\n'
            'api_mode = "chat_completions"\n'
            'api_key = "secret"\n',
            encoding="utf-8",
        )
        registry.chmod(0o600)
        credential_file = self.run_dir / "agent" / "workspace" / "credential.txt"
        credential_file.write_text("token=secret\n", encoding="utf-8")
        before = credential_file.read_bytes()

        with self.assertRaisesRegex(ValueError, "credential"):
            BenchmarkRunner().score(
                runner_module.BenchmarkScoreConfig(
                    run_dir=self.run_dir,
                    model_registry=registry,
                )
            )

        self.assertEqual(credential_file.read_bytes(), before)

    def test_workspace_preflight_does_not_change_source_directory_metadata(self) -> None:
        registry = self.root / "model.toml"
        registry.write_text(
            "[models.model-alias]\n"
            'provider = "openai"\n'
            'model = "model"\n'
            'base_url = "http://example.invalid/v1"\n'
            'api_mode = "chat_completions"\n'
            'api_key = "secret"\n',
            encoding="utf-8",
        )
        registry.chmod(0o600)
        restricted = self.run_dir / "agent" / "workspace" / "restricted"
        restricted.mkdir()
        restricted.chmod(0)
        before = restricted.stat()

        try:
            try:
                BenchmarkRunner().score(
                    runner_module.BenchmarkScoreConfig(
                        run_dir=self.run_dir,
                        model_registry=registry,
                    )
                )
            except (OSError, ValueError):
                pass
            after = restricted.stat()
        finally:
            restricted.chmod(0o700)

        self.assertEqual(after.st_mode, before.st_mode)
        self.assertEqual(after.st_ctime_ns, before.st_ctime_ns)

    def test_scoring_exception_writes_error_without_destroying_previous_artifacts(
        self,
    ) -> None:
        registry = self.root / "model.toml"
        registry.write_text(
            "[models.model-alias]\n"
            'provider = "openai"\n'
            'model = "model"\n'
            'base_url = "http://example.invalid/v1"\n'
            'api_mode = "chat_completions"\n'
            'api_key = "secret"\n',
            encoding="utf-8",
        )
        registry.chmod(0o600)
        previous_score = self.run_dir / "artifacts" / "verifier_output" / "score.json"
        previous_score.parent.mkdir()
        previous_score.write_text('{"score": 1.0}\n', encoding="utf-8")
        previous_bytes = previous_score.read_bytes()
        self.result["status"] = "pass"
        self.result["evaluation"] = {"status": "pass", "score": 1.0}
        self.result["artifacts"]["verifier_score"] = (
            "artifacts/verifier_output/score.json"
        )
        self._write_metadata()

        with patch(
            "tracetrial_bench.runner.score_layout",
            side_effect=FileNotFoundError("missing verifier secret"),
        ):
            result = BenchmarkRunner().score(
                runner_module.BenchmarkScoreConfig(
                    run_dir=self.run_dir,
                    model_registry=registry,
                )
            )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["evaluation"]["status"], "error")
        self.assertIn("missing verifier", result["evaluation"]["error_message"])
        self.assertNotIn("secret", result["evaluation"]["error_message"])
        self.assertIn("<redacted>", result["evaluation"]["error_message"])
        self.assertEqual(previous_score.read_bytes(), previous_bytes)
        self.assertIsNone(result["artifacts"]["verifier_score"])
        self.assertTrue((self.run_dir / "artifacts" / "scoring_error.json").is_file())
        self.assertEqual(
            list((self.run_dir / "artifacts").glob(".scoring-stage-*")),
            [],
        )

    def test_result_commit_failure_rolls_back_scoring_artifacts_and_report(self) -> None:
        registry = self.root / "model.toml"
        registry.write_text(
            "[models.model-alias]\n"
            'provider = "openai"\n'
            'model = "model"\n'
            'base_url = "http://example.invalid/v1"\n'
            'api_mode = "chat_completions"\n'
            'api_key = "secret"\n',
            encoding="utf-8",
        )
        registry.chmod(0o600)
        previous_score = self.run_dir / "artifacts" / "verifier_output" / "score.json"
        previous_score.parent.mkdir()
        previous_score.write_text('{"score": 0.25}\n', encoding="utf-8")
        previous_report = self.run_dir / "report.html"
        previous_report.write_text("previous report\n", encoding="utf-8")
        score_before = previous_score.read_bytes()
        report_before = previous_report.read_bytes()
        self.result["status"] = "pass"
        self.result["evaluation"] = {"status": "pass", "score": 0.25}
        self.result["artifacts"]["verifier_score"] = (
            "artifacts/verifier_output/score.json"
        )
        self.result["artifacts"]["trace_report"] = "report.html"
        self._write_metadata()

        with patch(
            "tracetrial_bench.runner.update_scored_result",
            side_effect=OSError("result commit failed"),
        ):
            result = BenchmarkRunner().score(
                runner_module.BenchmarkScoreConfig(
                    run_dir=self.run_dir,
                    model_registry=registry,
                )
            )

        self.assertEqual(result["status"], "error")
        self.assertEqual(previous_score.read_bytes(), score_before)
        self.assertEqual(previous_report.read_bytes(), report_before)
        self.assertIsNone(result["artifacts"]["verifier_score"])
        self.assertIsNone(result["artifacts"]["trace_report"])
        self.assertTrue((self.run_dir / "artifacts" / "scoring_error.json").is_file())
        self.assertEqual(
            list((self.run_dir / "artifacts").glob(".scoring-backup-*")),
            [],
        )

    def test_report_failure_does_not_leave_stale_report_reference(self) -> None:
        registry = self.root / "model.toml"
        registry.write_text(
            "[models.model-alias]\n"
            'provider = "openai"\n'
            'model = "model"\n'
            'base_url = "http://example.invalid/v1"\n'
            'api_mode = "chat_completions"\n'
            'api_key = "secret"\n',
            encoding="utf-8",
        )
        registry.chmod(0o600)
        previous_report = self.run_dir / "report.html"
        previous_report.write_text("previous report\n", encoding="utf-8")
        self.result["artifacts"]["trace_report"] = "report.html"
        self._write_metadata()

        with patch(
            "tracetrial_bench.runner.generate_trace_report",
            side_effect=OSError("report write failed"),
        ):
            result = BenchmarkRunner().score(
                runner_module.BenchmarkScoreConfig(
                    run_dir=self.run_dir,
                    model_registry=registry,
                )
            )

        self.assertEqual(result["status"], "pass")
        self.assertIsNone(result["artifacts"]["trace_report"])
        self.assertFalse(previous_report.exists())
        self.assertTrue(
            (self.run_dir / "artifacts" / "trace_report_error.log").is_file()
        )

    def test_partial_report_result_write_cannot_corrupt_scored_result(self) -> None:
        registry = self.root / "model.toml"
        registry.write_text(
            "[models.model-alias]\n"
            'provider = "openai"\n'
            'model = "model"\n'
            'base_url = "http://example.invalid/v1"\n'
            'api_mode = "chat_completions"\n'
            'api_key = "secret"\n',
            encoding="utf-8",
        )
        registry.chmod(0o600)
        result_path = self.run_dir / "result.json"
        original_write_text = Path.write_text

        def fail_after_partial_report_result(
            path: Path,
            data: str,
            *args,
            **kwargs,
        ) -> int:
            if (
                path.parent == self.run_dir
                and path.name.startswith("result.json")
                and '"trace_report": "report.html"' in data
            ):
                original_write_text(path, '{"partial"', encoding="utf-8")
                raise OSError("injected partial result write")
            return original_write_text(path, data, *args, **kwargs)

        with patch.object(Path, "write_text", new=fail_after_partial_report_result):
            result = BenchmarkRunner().score(
                runner_module.BenchmarkScoreConfig(
                    run_dir=self.run_dir,
                    model_registry=registry,
                )
            )

        persisted = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "pass")
        self.assertEqual(persisted["status"], "pass")
        self.assertIsNone(persisted["artifacts"]["trace_report"])
        self.assertTrue(
            (self.run_dir / "artifacts" / "trace_report_error.log").is_file()
        )
        self.assertEqual(list(self.run_dir.glob("result.json*.tmp")), [])

    def _enable_llm_judge(self) -> None:
        (self.task_dir / "task.yaml").write_text(
            "id: task-1\n"
            "verifier:\n"
            "  entrypoint: verifier/run.sh\n"
            "scoring:\n"
            "  version: layered_100_v1\n"
            "  harness_score: verifier/harness_score.yaml\n"
            "  agent_score: verifier/agent_score.yaml\n",
            encoding="utf-8",
        )
        for layer, item_id in (("harness", "h-rule"), ("agent", "a-rule")):
            (self.task_dir / "verifier" / f"{layer}_score.yaml").write_text(
                "version: score_v1\n"
                f"layer: {layer}\n"
                "items:\n"
                f"  - id: {item_id}\n"
                "    type: rule\n"
                "    criterion: fixture rule\n",
                encoding="utf-8",
            )
        (self.task_dir / "verifier" / "run.sh").write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' '{\"schema_version\":\"harnessbench_rule_results_v1\","
            "\"rule_results\":{\"harness\":[{\"id\":\"h-rule\",\"score\":1,\"reason\":\"ok\"}],"
            "\"agent\":[{\"id\":\"a-rule\",\"score\":1,\"reason\":\"ok\"}]}}'\n",
            encoding="utf-8",
        )


def _passing_judge(_runtime, prompt: str) -> str:
    if "Return exactly one JSON object" not in prompt:
        raise AssertionError("judge prompt is missing output instructions")
    return json.dumps({"score": 1, "reason": "fixture evidence passes"})


class SharedScoringTests(unittest.TestCase):
    def test_score_layout_runs_verifier_and_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            private_dir = root / "private"
            verifier_dir = private_dir / "verifier"
            workspace_dir = run_dir / "agent" / "workspace"
            artifacts_dir = run_dir / "artifacts"
            verifier_dir.mkdir(parents=True)
            workspace_dir.mkdir(parents=True)
            artifacts_dir.mkdir()
            verifier = verifier_dir / "run.sh"
            verifier.write_text(
                "#!/bin/sh\nprintf '{\"pass\": true, \"score\": 1.0}\\n'\n",
                encoding="utf-8",
            )
            verifier.chmod(0o755)
            task = TaskSpec(
                task_id="task-1",
                task_dir=root / "task",
                prompt="prompt",
                workspace_dir=root / "task" / "workspace",
                verifier=VerifierSpec(entrypoint="verifier/run.sh"),
            )
            layout = RunLayout(
                run_dir=run_dir,
                agent_dir=run_dir / "agent",
                workspace_dir=workspace_dir,
                private_dir=private_dir,
                verifier_dir=verifier_dir,
                artifacts_dir=artifacts_dir,
                baseline_dir=private_dir / ".baseline_workspace",
            )
            try:
                scoring_module = importlib.import_module("tracetrial_bench.scoring")
            except ModuleNotFoundError as exc:
                self.fail(f"shared scoring module is missing: {exc}")

            outcome = scoring_module.score_layout(
                task=task,
                layout=layout,
                trace_summary={
                    "status": "finished",
                    "trace_complete": True,
                    "structured_trace_status": "available",
                    "tool_stats": {"total_tool_calls": 0},
                },
                invocation_report={},
                simulator_evidence=None,
                harness="codex",
                model="model",
                timeout=30,
            )

            self.assertEqual(outcome.raw.status, "pass")
            self.assertEqual(outcome.effective.score, 1.0)
            self.assertTrue(outcome.policy_path.is_file())
            self.assertEqual(outcome.policy["execution_status"], "finished")

    def test_reset_scoring_artifacts_preserves_execution_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            (run_dir / "artifacts" / "verifier_output").mkdir(parents=True)
            (run_dir / "artifacts" / "llm_judge").mkdir()
            (run_dir / "artifacts" / "trace").mkdir()
            (run_dir / "agent" / "workspace").mkdir(parents=True)
            (run_dir / "artifacts" / "runner_policy.json").write_text(
                "{}\n",
                encoding="utf-8",
            )
            (run_dir / "artifacts" / "container_lifecycle.json").write_text(
                "{}\n",
                encoding="utf-8",
            )
            (run_dir / "result.json").write_text("{}\n", encoding="utf-8")
            scoring_module = importlib.import_module("tracetrial_bench.scoring")
            reset = getattr(scoring_module, "reset_scoring_artifacts", None)

            self.assertTrue(callable(reset), "reset_scoring_artifacts must exist")
            reset(run_dir)

            self.assertFalse((run_dir / "artifacts" / "verifier_output").exists())
            self.assertFalse((run_dir / "artifacts" / "llm_judge").exists())
            self.assertFalse((run_dir / "artifacts" / "runner_policy.json").exists())
            self.assertTrue((run_dir / "artifacts" / "trace").is_dir())
            self.assertTrue((run_dir / "agent" / "workspace").is_dir())
            self.assertTrue((run_dir / "artifacts" / "container_lifecycle.json").is_file())
            self.assertTrue((run_dir / "result.json").is_file())


class RunnerPhaseTests(unittest.TestCase):
    def test_runner_declares_enabled_simulator_socket_for_container_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_dir = root / "task"
            (task_dir / "workspace").mkdir(parents=True)
            (task_dir / "verifier").mkdir()
            (task_dir / "task.yaml").write_text("id: task-1\n", encoding="utf-8")
            (task_dir / "prompt.md").write_text("prompt\n", encoding="utf-8")
            verifier = task_dir / "verifier" / "run.sh"
            verifier.write_text("#!/bin/sh\n", encoding="utf-8")
            verifier.chmod(0o755)
            registry = root / "model.toml"
            registry.write_text(
                "[models.test]\n"
                'provider = "openai"\n'
                'model = "model"\n'
                'base_url = "http://example.invalid/v1"\n'
                'api_mode = "chat_completions"\n'
                'api_key = "secret"\n',
                encoding="utf-8",
            )
            registry.chmod(0o600)
            runtime = _FinishedContainerRuntime()
            simulator_socket_paths: list[Path] = []

            class _EnabledSimulator:
                enabled = True

                def __init__(self, _task, layout, **_kwargs) -> None:
                    self.socket_path = (
                        layout.workspace_dir
                        / ".harnessbench-business-simulator.sock"
                    )
                    simulator_socket_paths.append(self.socket_path)

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb) -> None:
                    return None

            with patch.object(
                runner_module,
                "BusinessSimulatorRuntime",
                _EnabledSimulator,
            ):
                BenchmarkRunner(container_runtime=runtime).run(
                    BenchmarkRunConfig(
                        task=task_dir,
                        harness="codex",
                        model="test",
                        mode="docker",
                        output_dir=root / "runs",
                        model_registry=registry,
                        evaluation_mode="run-only",
                    )
                )

            self.assertEqual(len(runtime.requests), 1)
            self.assertEqual(len(simulator_socket_paths), 1)
            self.assertEqual(
                runtime.requests[0].transient_workspace_sockets,
                (simulator_socket_paths[0],),
            )

    def test_run_only_skips_verifier_and_writes_resumable_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_dir = root / "task"
            (task_dir / "workspace").mkdir(parents=True)
            (task_dir / "verifier").mkdir()
            (task_dir / "task.yaml").write_text("id: task-1\n", encoding="utf-8")
            (task_dir / "prompt.md").write_text("prompt\n", encoding="utf-8")
            verifier = task_dir / "verifier" / "run.sh"
            verifier.write_text(
                "#!/bin/sh\n"
                "touch \"$HARNESSBENCH_RUN_DIR/verifier-ran\"\n"
                "printf '{\"pass\": true, \"score\": 1.0}\\n'\n",
                encoding="utf-8",
            )
            verifier.chmod(0o755)
            registry = root / "model.toml"
            registry.write_text(
                "[models.test]\n"
                'provider = "openai"\n'
                'model = "model"\n'
                'base_url = "http://example.invalid/v1"\n'
                'api_mode = "chat_completions"\n'
                'api_key = "secret"\n',
                encoding="utf-8",
            )
            registry.chmod(0o600)
            output_dir = root / "runs"
            runtime = _FinishedContainerRuntime()
            runner = BenchmarkRunner(container_runtime=runtime)

            try:
                config = BenchmarkRunConfig(
                    task=task_dir,
                    harness="codex",
                    model="test",
                    mode="docker",
                    output_dir=output_dir,
                    model_registry=registry,
                    evaluation_mode="run-only",
                )
            except TypeError as exc:
                self.fail(f"BenchmarkRunConfig rejected evaluation_mode: {exc}")
            result = runner.run(config)
            run_dir = Path(result["result_path"]).parent

            self.assertEqual(result["status"], "run_complete")
            self.assertEqual(result["evaluation"]["status"], "pending")
            self.assertFalse((run_dir / "verifier-ran").exists())
            self.assertTrue((run_dir / "artifacts" / "verifier_gate.json").is_file())
            self.assertTrue((run_dir / "report.html").is_file())
            self.assertFalse(
                (run_dir / "artifacts" / "trace_report_error.log").exists()
            )
            resolved = json.loads(
                (run_dir / "config.resolved.json").read_text(encoding="utf-8")
            )
            self.assertEqual(resolved["evaluation_mode"], "run-only")

    def test_resolved_config_persists_absolute_model_registry_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            config = BenchmarkRunConfig(
                task=Path("task"),
                harness="codex",
                model="model",
                output_dir=Path("outputs"),
                mode="docker",
                model_registry=Path("relative-model.toml"),
            )

            runner_module._write_resolved_config(
                run_dir,
                config,
                {"execution": {"docker_image": None}},
            )

            payload = json.loads(
                (run_dir / "config.resolved.json").read_text(encoding="utf-8")
            )
            self.assertTrue(Path(payload["model_registry"]).is_absolute())

    def test_resolved_config_persists_absolute_task_and_normalized_harness(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            config = BenchmarkRunConfig(
                task=Path("relative-task"),
                harness="claude-code",
                model="model",
                output_dir=Path("outputs"),
                mode="docker",
                model_registry=Path("relative-model.toml"),
            )

            runner_module._write_resolved_config(
                run_dir,
                config,
                {"execution": {"docker_image": None}},
            )

            payload = json.loads(
                (run_dir / "config.resolved.json").read_text(encoding="utf-8")
            )
            self.assertTrue(Path(payload["task"]).is_absolute())
            self.assertEqual(payload["harness"], "claudecode")

    def test_score_only_rejects_changed_verifier_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_dir = root / "task"
            (task_dir / "workspace").mkdir(parents=True)
            (task_dir / "verifier").mkdir()
            (task_dir / "task.yaml").write_text("id: task-1\n", encoding="utf-8")
            (task_dir / "prompt.md").write_text("prompt\n", encoding="utf-8")
            verifier = task_dir / "verifier" / "run.sh"
            verifier.write_text(
                "#!/bin/sh\nprintf '{\"pass\": true, \"score\": 1.0}\\n'\n",
                encoding="utf-8",
            )
            verifier.chmod(0o755)
            registry = root / "model.toml"
            registry.write_text(
                "[models.test]\n"
                'provider = "openai"\n'
                'model = "model"\n'
                'base_url = "http://example.invalid/v1"\n'
                'api_mode = "chat_completions"\n'
                'api_key = "secret"\n',
                encoding="utf-8",
            )
            registry.chmod(0o600)
            runner = BenchmarkRunner(container_runtime=_FinishedContainerRuntime())
            result = runner.run(
                BenchmarkRunConfig(
                    task=task_dir,
                    harness="codex",
                    model="test",
                    mode="docker",
                    output_dir=root / "runs",
                    model_registry=registry,
                    evaluation_mode="run-only",
                )
            )
            verifier.write_text(
                "#!/bin/sh\nprintf '{\"pass\": false, \"score\": 0.0}\\n'\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "scoring inputs manifest"):
                runner.score(
                    runner_module.BenchmarkScoreConfig(
                        run_dir=Path(result["result_path"]).parent,
                        model_registry=registry,
                    )
                )

    def test_score_only_does_not_execute_unmanifested_python_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_dir = root / "task"
            verifier_dir = task_dir / "verifier"
            (task_dir / "workspace").mkdir(parents=True)
            verifier_dir.mkdir()
            (task_dir / "task.yaml").write_text(
                "id: task-1\n"
                "verifier:\n"
                "  entrypoint: python3 verifier/run.py\n",
                encoding="utf-8",
            )
            (task_dir / "prompt.md").write_text("prompt\n", encoding="utf-8")
            helper = verifier_dir / "helper.py"
            helper.write_text(
                'payload = {"pass": True, "score": 1.0}\n',
                encoding="utf-8",
            )
            (verifier_dir / "run.py").write_text(
                "import json\n"
                "from helper import payload\n"
                "print(json.dumps(payload))\n",
                encoding="utf-8",
            )
            registry = root / "model.toml"
            registry.write_text(
                "[models.test]\n"
                'provider = "openai"\n'
                'model = "model"\n'
                'base_url = "http://example.invalid/v1"\n'
                'api_mode = "chat_completions"\n'
                'api_key = "secret"\n',
                encoding="utf-8",
            )
            registry.chmod(0o600)
            runner = BenchmarkRunner(container_runtime=_FinishedContainerRuntime())
            run_only = runner.run(
                BenchmarkRunConfig(
                    task=task_dir,
                    harness="codex",
                    model="test",
                    mode="docker",
                    output_dir=root / "runs",
                    model_registry=registry,
                    evaluation_mode="run-only",
                )
            )
            malicious = root / "malicious.py"
            malicious.write_text(
                'payload = {"pass": False, "score": 0.0}\n',
                encoding="utf-8",
            )
            cache_path = Path(importlib.util.cache_from_source(str(helper)))
            cache_path.parent.mkdir(exist_ok=True)
            py_compile.compile(
                str(malicious),
                cfile=str(cache_path),
                doraise=True,
                invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
            )

            result = runner.score(
                runner_module.BenchmarkScoreConfig(
                    run_dir=Path(run_only["result_path"]).parent,
                    model_registry=registry,
                )
            )

            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["evaluation"]["score"], 1.0)

    def test_run_and_score_still_executes_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_dir = root / "task"
            (task_dir / "workspace").mkdir(parents=True)
            (task_dir / "verifier").mkdir()
            (task_dir / "task.yaml").write_text("id: task-1\n", encoding="utf-8")
            (task_dir / "prompt.md").write_text("prompt\n", encoding="utf-8")
            verifier = task_dir / "verifier" / "run.sh"
            verifier.write_text(
                "#!/bin/sh\n"
                "touch \"$HARNESSBENCH_RUN_DIR/verifier-ran\"\n"
                "printf '{\"pass\": true, \"score\": 1.0}\\n'\n",
                encoding="utf-8",
            )
            registry = root / "model.toml"
            registry.write_text(
                "[models.test]\n"
                'provider = "openai"\n'
                'model = "model"\n'
                'base_url = "http://example.invalid/v1"\n'
                'api_mode = "chat_completions"\n'
                'api_key = "secret"\n',
                encoding="utf-8",
            )
            registry.chmod(0o600)

            result = BenchmarkRunner(
                container_runtime=_FinishedContainerRuntime()
            ).run(
                BenchmarkRunConfig(
                    task=task_dir,
                    harness="codex",
                    model="test",
                    mode="docker",
                    output_dir=root / "runs",
                    model_registry=registry,
                    evaluation_mode="run-and-score",
                )
            )
            run_dir = Path(result["result_path"]).parent

            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["evaluation_mode"], "run-and-score")
            self.assertTrue((run_dir / "verifier-ran").is_file())
            self.assertTrue(
                (run_dir / "artifacts" / "runner_policy.json").is_file()
            )


class RepresentativeTaskParityTests(unittest.TestCase):
    @patch("tracetrial_bench.runner._build_task_runtime_image")
    def test_run_only_then_score_only_matches_run_and_score_verifier_output(
        self,
        build_task_image: Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_dir = (
                Path(__file__).resolve().parents[1]
                / "test_task"
                / "C1a-academic-L3-001"
            )
            registry = root / "model.toml"
            registry.write_text(
                "[models.model-alias]\n"
                'provider = "openai"\n'
                'model = "model"\n'
                'base_url = "http://example.invalid/v1"\n'
                'api_mode = "chat_completions"\n'
                'api_key = "secret"\n',
                encoding="utf-8",
            )
            registry.chmod(0o600)
            runner = BenchmarkRunner(
                container_runtime=_FinishedContainerRuntime(),
                judge_requester=_passing_judge,
            )

            run_only = runner.run(
                BenchmarkRunConfig(
                    task=task_dir,
                    harness="openclaw",
                    model="model-alias",
                    mode="docker",
                    output_dir=root / "split",
                    model_registry=registry,
                    evaluation_mode="run-only",
                    instance_seed="shared-seed",
                )
            )
            split_run_dir = Path(run_only["result_path"]).parent
            workspace_before = _tree_digest(split_run_dir / "agent" / "workspace")
            split_result = runner.score(
                runner_module.BenchmarkScoreConfig(
                    run_dir=split_run_dir,
                    model_registry=registry,
                )
            )
            workspace_after = _tree_digest(split_run_dir / "agent" / "workspace")

            combined_result = runner.run(
                BenchmarkRunConfig(
                    task=task_dir,
                    harness="openclaw",
                    model="model-alias",
                    mode="docker",
                    output_dir=root / "combined",
                    model_registry=registry,
                    evaluation_mode="run-and-score",
                    instance_seed="shared-seed",
                )
            )
            combined_run_dir = Path(combined_result["result_path"]).parent
            split_score = json.loads(
                (split_run_dir / "artifacts" / "verifier_output" / "score.json").read_text(
                    encoding="utf-8"
                )
            )
            combined_score = json.loads(
                (
                    combined_run_dir
                    / "artifacts"
                    / "verifier_output"
                    / "score.json"
                ).read_text(encoding="utf-8")
            )
            split_three_score = json.loads(
                (
                    split_run_dir
                    / "artifacts"
                    / "llm_judge"
                    / "combined_score.json"
                ).read_text(encoding="utf-8")
            )
            combined_three_score = json.loads(
                (
                    combined_run_dir
                    / "artifacts"
                    / "llm_judge"
                    / "combined_score.json"
                ).read_text(encoding="utf-8")
            )

            self.assertEqual(workspace_before, workspace_after)
            self.assertEqual(split_score, combined_score)
            self.assertEqual(split_result["status"], "fail")
            self.assertEqual(combined_result["status"], "fail")
            self.assertEqual(
                split_result["evaluation"]["raw_score"],
                combined_result["evaluation"]["raw_score"],
            )
            self.assertEqual(split_three_score, combined_three_score)
            self.assertEqual(split_three_score["score_scale"], "0_100")
            self.assertEqual(split_three_score["max_score"], 100.0)
            self.assertEqual(build_task_image.call_count, 2)

    @patch("tracetrial_bench.runner._build_task_runtime_image")
    def test_score_only_rejects_tampered_simulator_evidence(
        self,
        build_task_image: Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_dir = (
                Path(__file__).resolve().parents[1]
                / "test_task"
                / "C1a-academic-L3-001"
            )
            registry = root / "model.toml"
            registry.write_text(
                "[models.model-alias]\n"
                'provider = "openai"\n'
                'model = "model"\n'
                'base_url = "http://example.invalid/v1"\n'
                'api_mode = "chat_completions"\n'
                'api_key = "secret"\n',
                encoding="utf-8",
            )
            registry.chmod(0o600)
            runner = BenchmarkRunner(
                container_runtime=_FinishedContainerRuntime(),
                judge_requester=_passing_judge,
            )
            run_only = runner.run(
                BenchmarkRunConfig(
                    task=task_dir,
                    harness="openclaw",
                    model="model-alias",
                    mode="docker",
                    output_dir=root / "runs",
                    model_registry=registry,
                    evaluation_mode="run-only",
                )
            )
            run_dir = Path(run_only["result_path"]).parent
            ledger = (
                run_dir
                / "artifacts"
                / "private_evidence"
                / "business_simulator"
                / "ledger.jsonl"
            )
            ledger.write_text('{"tampered": true}\n', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "business simulator evidence"):
                runner.score(
                    runner_module.BenchmarkScoreConfig(
                        run_dir=run_dir,
                        model_registry=registry,
                    )
                )
            build_task_image.assert_called_once()

    def test_openclaw_native_task_rejects_incompatible_image_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_dir = (
                Path(__file__).resolve().parents[1]
                / "test_task"
                / "C1a-academic-L3-001"
            )
            registry = root / "model.toml"
            registry.write_text(
                "[models.model-alias]\n"
                'provider = "openai"\n'
                'model = "model"\n'
                'base_url = "http://example.invalid/v1"\n'
                'api_mode = "chat_completions"\n'
                'api_key = "secret"\n',
                encoding="utf-8",
            )
            registry.chmod(0o600)

            runner = BenchmarkRunner(container_runtime=_FinishedContainerRuntime())
            with self.assertRaisesRegex(
                ValueError,
                "requires an OpenClaw-native Docker adapter",
            ):
                runner.run(
                    BenchmarkRunConfig(
                        task=task_dir,
                        harness="codex",
                        model="model-alias",
                        mode="docker",
                        docker_image="custom/runtime:test",
                        output_dir=root / "runs",
                        model_registry=registry,
                        evaluation_mode="run-only",
                    )
                )


class _FinishedContainerRuntime:
    def __init__(self) -> None:
        self.requests = []

    def run(self, request) -> ContainerExecution:
        self.requests.append(request)
        lifecycle_path = request.artifacts_dir / "container_lifecycle.json"
        container_id = "a" * 64
        image = HarnessImage(
            reference="harnessbench/codex:test",
            image_id="sha256:" + "b" * 64,
            digest=None,
            built=False,
        )
        lifecycle_path.write_text(
            json.dumps(
                {
                    "schema_version": "harnessbench_container_lifecycle_v1",
                    "run_token": "fixture-run",
                    "container_name": f"harnessbench-{request.harness}-fixture-run",
                    "container_id": container_id,
                    "task_id": request.task_id,
                    "harness": request.harness,
                    "model": request.model.public_summary(),
                    "image": {
                        "reference": image.reference,
                        "image_id": image.image_id,
                        "digest": image.digest,
                        "built": image.built,
                    },
                    "gateway": None,
                    "archive_status": "complete",
                    "cleanup_status": "complete",
                }
            ),
            encoding="utf-8",
        )
        trace_dir = request.artifacts_dir / "container" / "trace" / "run-1"
        trace_dir.mkdir(parents=True)
        events_path = trace_dir / "events.json"
        summary_path = trace_dir / "summary.json"
        events_path.write_text("[]\n", encoding="utf-8")
        summary = {
            "status": "finished",
            "trace_complete": True,
            "structured_trace_status": "available",
            "tool_stats": {"total_tool_calls": 0},
            "exit_code": 0,
            "events_path": str(events_path),
            "summary_path": str(summary_path),
        }
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        _write_archive_manifest(request.artifacts_dir / "container")
        return ContainerExecution(
            trace_summary=summary,
            image=image,
            container_id=container_id,
            lifecycle_path=lifecycle_path,
            archive_status="complete",
            cleanup_status="complete",
        )


def _write_archive_manifest(root: Path) -> None:
    files = []
    for path in sorted(value for value in root.rglob("*") if value.is_file()):
        if path.name == "archive_manifest.json":
            continue
        payload = path.read_bytes()
        files.append(
            {
                "path": str(path.relative_to(root)),
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    (root / "archive_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "harnessbench_container_archive_v1",
                "files": files,
            }
        ),
        encoding="utf-8",
    )


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


if __name__ == "__main__":
    unittest.main()
