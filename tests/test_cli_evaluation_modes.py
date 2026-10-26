from __future__ import annotations

import io
import os
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from tracetrial_bench.cli import _run, _validate_run_options, build_parser


class EvaluationModeParserTests(unittest.TestCase):
    def test_default_evaluation_mode_is_run_and_score(self) -> None:
        args = build_parser().parse_args(["run"])

        self.assertEqual(args.evaluation_mode, "run-and-score")

    def test_run_help_lists_evaluation_mode(self) -> None:
        run_parser = _run_parser()

        help_text = run_parser.format_help()

        self.assertIn(
            "--evaluation-mode {run-only,score-only,run-and-score}",
            help_text,
        )
        self.assertIn("--run-dir", help_text)
        self.assertIn("--skip-on-rate-limit", help_text)

    def test_score_only_parses_without_execution_matrix(self) -> None:
        parser = build_parser()

        try:
            args = parser.parse_args(
                [
                    "run",
                    "--mode",
                    "docker",
                    "--evaluation-mode",
                    "score-only",
                    "--run-dir",
                    "one",
                    "--run-dir",
                    "two",
                ]
            )
        except SystemExit as exc:
            self.fail(f"score-only arguments were rejected: exit {exc.code}")

        self.assertEqual(args.run_dirs, [Path("one"), Path("two")])


class EvaluationModeValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = build_parser()

    def test_run_and_score_requires_execution_matrix(self) -> None:
        args = self.parser.parse_args(["run"])

        with self.assertRaisesRegex(
            ValueError,
            "run-and-score requires a task source, --harness, --model, and --output-dir",
        ):
            _validate_run_options(args)

    def test_score_only_requires_docker(self) -> None:
        args = self.parser.parse_args(
            ["run", "--evaluation-mode", "score-only", "--run-dir", "one"]
        )

        with self.assertRaisesRegex(
            ValueError,
            "score-only requires --mode docker",
        ):
            _validate_run_options(args)


class EvaluationModeDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = build_parser()

    def test_run_only_is_forwarded_to_benchmark_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = build_parser().parse_args(
                [
                    "run",
                    "--task",
                    str(root / "task"),
                    "--harness",
                    "codex",
                    "--model",
                    "model",
                    "--mode",
                    "docker",
                    "--evaluation-mode",
                    "run-only",
                    "--model-registry",
                    str(root / "model.toml"),
                    "--output-dir",
                    str(root / "runs"),
                ]
            )
            registry = Mock()
            runner = Mock()
            runner.run.return_value = {
                "status": "run_complete",
                "result_path": str(root / "runs" / "run" / "result.json"),
            }

            with (
                patch("tracetrial_bench.cli.ModelRegistry.load", return_value=registry),
                patch("tracetrial_bench.cli.BenchmarkRunner", return_value=runner),
                redirect_stdout(io.StringIO()),
            ):
                exit_code = _run(args)

            self.assertEqual(exit_code, 0)
            config = runner.run.call_args.args[0]
            self.assertEqual(config.evaluation_mode, "run-only")

    def test_rate_limit_skip_policy_is_forwarded_to_benchmark_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = build_parser().parse_args(
                [
                    "run",
                    "--task",
                    str(root / "task"),
                    "--harness",
                    "codex",
                    "--model",
                    "model",
                    "--mode",
                    "docker",
                    "--evaluation-mode",
                    "run-only",
                    "--model-registry",
                    str(root / "model.toml"),
                    "--skip-on-rate-limit",
                    "--output-dir",
                    str(root / "runs"),
                ]
            )
            registry = Mock()
            runner = Mock()
            runner.run.return_value = {
                "status": "run_complete",
                "result_path": str(root / "runs" / "run" / "result.json"),
            }

            with (
                patch("tracetrial_bench.cli.ModelRegistry.load", return_value=registry),
                patch("tracetrial_bench.cli.BenchmarkRunner", return_value=runner),
                redirect_stdout(io.StringIO()),
            ):
                exit_code = _run(args)

            self.assertEqual(exit_code, 0)
            config = runner.run.call_args.args[0]
            self.assertTrue(config.skip_on_rate_limit)

    def test_execution_resolves_output_and_registry_before_batch_workers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry_path = root / "model.toml"
            args = build_parser().parse_args(
                [
                    "run",
                    "--task",
                    str(root / "task"),
                    "--harness",
                    "codex",
                    "--model",
                    "model",
                    "--mode",
                    "docker",
                    "--evaluation-mode",
                    "run-only",
                    "--model-registry",
                    "model.toml",
                    "--output-dir",
                    "runs",
                ]
            )
            registry = Mock()
            runner = Mock()
            runner.run.return_value = {
                "status": "run_complete",
                "result_path": str(root / "runs" / "run" / "result.json"),
            }
            original_cwd = Path.cwd()
            try:
                os.chdir(root)
                with (
                    patch("tracetrial_bench.cli.ModelRegistry.load", return_value=registry),
                    patch("tracetrial_bench.cli.BenchmarkRunner", return_value=runner),
                    redirect_stdout(io.StringIO()),
                ):
                    exit_code = _run(args)
            finally:
                os.chdir(original_cwd)

            self.assertEqual(exit_code, 0)
            config = runner.run.call_args.args[0]
            self.assertEqual(config.output_dir, (root / "runs").resolve())
            self.assertEqual(config.model_registry, registry_path.resolve())

    def test_score_only_dispatches_existing_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "existing-run"
            args = build_parser().parse_args(
                [
                    "run",
                    "--mode",
                    "docker",
                    "--evaluation-mode",
                    "score-only",
                    "--run-dir",
                    str(run_dir),
                    "--model-registry",
                    str(root / "model.toml"),
                    "--judge-model",
                    "judge",
                ]
            )
            runner = Mock()
            runner.score.return_value = {
                "status": "pass",
                "result_path": str(run_dir / "result.json"),
            }
            stdout = io.StringIO()

            with (
                patch("tracetrial_bench.cli.BenchmarkRunner", return_value=runner),
                redirect_stdout(stdout),
            ):
                exit_code = _run(args)

            self.assertEqual(exit_code, 0)
            runner.run.assert_not_called()
            score_config = runner.score.call_args.args[0]
            self.assertEqual(score_config.run_dir, run_dir.resolve())
            self.assertEqual(score_config.model_registry, root / "model.toml")
            self.assertEqual(score_config.judge_model, "judge")
            self.assertEqual(stdout.getvalue().strip(), str(run_dir / "result.json"))

    def test_score_only_preserves_input_order_with_concurrency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = (root / "first").resolve()
            second = (root / "second").resolve()
            args = build_parser().parse_args(
                [
                    "run",
                    "--mode",
                    "docker",
                    "--evaluation-mode",
                    "score-only",
                    "--run-dir",
                    str(first),
                    "--run-dir",
                    str(second),
                    "--concurrency",
                    "2",
                ]
            )
            runner = Mock()

            def score(config):
                if config.run_dir == first:
                    time.sleep(0.02)
                    status = "pass"
                else:
                    status = "fail"
                return {
                    "status": status,
                    "result_path": str(config.run_dir / "result.json"),
                }

            runner.score.side_effect = score
            stdout = io.StringIO()

            with (
                patch("tracetrial_bench.cli.BenchmarkRunner", return_value=runner),
                redirect_stdout(stdout),
            ):
                exit_code = _run(args)

            self.assertEqual(exit_code, 1)
            self.assertEqual(
                stdout.getvalue().splitlines(),
                [str(first / "result.json"), str(second / "result.json")],
            )
            self.assertEqual(runner.score.call_count, 2)

    def test_score_only_accepts_run_dirs_without_loading_registry(self) -> None:
        args = self.parser.parse_args(
            [
                "run",
                "--mode",
                "docker",
                "--evaluation-mode",
                "score-only",
                "--run-dir",
                "one",
                "--run-dir",
                "two",
            ]
        )

        _validate_run_options(args)

    def test_score_only_rejects_execution_matrix_arguments(self) -> None:
        args = self.parser.parse_args(
            [
                "run",
                "--mode",
                "docker",
                "--evaluation-mode",
                "score-only",
                "--run-dir",
                "one",
                "--task",
                "task",
                "--harness",
                "codex",
                "--model",
                "model",
                "--output-dir",
                "out",
                "--reasoning-intensity",
                "high",
            ]
        )

        with self.assertRaisesRegex(ValueError, "cannot be used with score-only"):
            _validate_run_options(args)

    def test_score_only_rejects_duplicate_run_dirs(self) -> None:
        args = self.parser.parse_args(
            [
                "run",
                "--mode",
                "docker",
                "--evaluation-mode",
                "score-only",
                "--run-dir",
                "one",
                "--run-dir",
                "one",
            ]
        )

        with self.assertRaisesRegex(ValueError, "run directories must be unique"):
            _validate_run_options(args)

    def test_run_only_rejects_run_dir(self) -> None:
        args = self.parser.parse_args(
            [
                "run",
                "--task",
                "task",
                "--harness",
                "codex",
                "--model",
                "model",
                "--output-dir",
                "out",
                "--mode",
                "docker",
                "--evaluation-mode",
                "run-only",
                "--run-dir",
                "one",
            ]
        )

        with self.assertRaisesRegex(ValueError, "--run-dir requires score-only"):
            _validate_run_options(args)

    def test_local_mode_rejects_run_only(self) -> None:
        args = self.parser.parse_args(
            [
                "run",
                "--task",
                "task",
                "--harness",
                "codex",
                "--model",
                "model",
                "--output-dir",
                "out",
                "--evaluation-mode",
                "run-only",
            ]
        )

        with self.assertRaisesRegex(
            ValueError,
            "run-only requires --mode docker",
        ):
            _validate_run_options(args)


def _run_parser():
    parser = build_parser()
    subparsers_action = next(
        action
        for action in parser._actions
        if getattr(action, "choices", None) and "run" in action.choices
    )
    return subparsers_action.choices["run"]


if __name__ == "__main__":
    unittest.main()
