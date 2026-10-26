from __future__ import annotations

import unittest
from pathlib import Path

from harness_tracer.adapters.hermes import HermesAdapter
from tracetrial_bench.runner_policy import resolve_harness_invocation
from tracetrial_bench.task_resolver import resolve_task


ROOT = Path(__file__).resolve().parents[1]


def _hermes_policy(
    *,
    task_max_turns: int | None,
    requested_extra_args: dict[str, str] | None = None,
    strict: bool = True,
):
    return resolve_harness_invocation(
        harness="hermes",
        requested_timeout=None,
        task_max_wall_time_sec=2400,
        task_max_turns=task_max_turns,
        requested_extra_args=requested_extra_args or {},
        strict=strict,
    )


def _policy(
    harness: str,
    *,
    reasoning_intensity: str | None = None,
    requested_extra_args: dict[str, str] | None = None,
    strict: bool = True,
):
    return resolve_harness_invocation(
        harness=harness,
        requested_timeout=None,
        task_max_wall_time_sec=2400,
        task_max_turns=None,
        requested_extra_args=requested_extra_args or {},
        strict=strict,
        requested_reasoning_intensity=reasoning_intensity,
    )


class ReasoningIntensityPolicyTests(unittest.TestCase):
    def test_maps_codex_reasoning_intensity_after_strict_filtering(self) -> None:
        policy = _policy("codex", reasoning_intensity="High")

        self.assertEqual(
            policy.extra_args["config"], 'model_reasoning_effort="high"'
        )
        self.assertEqual(policy.report["reasoning_intensity"]["effective"], "high")

    def test_maps_claudecode_reasoning_intensity_after_strict_filtering(
        self,
    ) -> None:
        policy = _policy("claudecode", reasoning_intensity="xhigh")

        self.assertEqual(policy.extra_args["effort"], "xhigh")

    def test_maps_openclaw_reasoning_intensity_to_thinking(self) -> None:
        policy = _policy("openclaw", reasoning_intensity="adaptive")

        self.assertEqual(policy.extra_args["thinking"], "adaptive")

    def test_maps_deepseek_reasoning_intensity_to_sdk_config(self) -> None:
        policy = _policy("deepseek", reasoning_intensity="high")

        self.assertEqual(policy.extra_args["reasoning-effort"], "high")

    def test_maps_low_deepseek_reasoning_to_native_off(self) -> None:
        policy = _policy("deepseek", reasoning_intensity="low")

        self.assertEqual(policy.report["reasoning_intensity"]["effective"], "low")
        self.assertEqual(policy.extra_args["reasoning-effort"], "off")

    def test_rejects_unsupported_harness_reasoning_intensity(self) -> None:
        with self.assertRaisesRegex(ValueError, "only supported"):
            _policy("hermes", reasoning_intensity="high")

    def test_rejects_native_arg_conflict(self) -> None:
        with self.assertRaisesRegex(ValueError, "conflicts"):
            _policy(
                "claudecode",
                reasoning_intensity="high",
                requested_extra_args={"effort": "low"},
            )

    def test_allows_claudecode_diagnostic_soft_exit(self) -> None:
        policy = _policy(
            "claudecode",
            reasoning_intensity="high",
            requested_extra_args={
                "harnessbench-approval-claude-diagnostic-soft-exit": "true"
            },
        )

        self.assertEqual(
            policy.extra_args[
                "harnessbench-approval-claude-diagnostic-soft-exit"
            ],
            "true",
        )
        self.assertEqual(policy.extra_args["effort"], "high")

    def test_allows_bounded_deepseek_output_limit_override(self) -> None:
        policy = _policy(
            "deepseek",
            reasoning_intensity="high",
            requested_extra_args={
                "harnessbench-deepseek-max-output-bytes": "192000000"
            },
        )

        self.assertEqual(
            policy.extra_args["harnessbench-deepseek-max-output-bytes"],
            "192000000",
        )
        self.assertEqual(policy.extra_args["reasoning-effort"], "high")

    def test_rejects_unbounded_deepseek_output_limit_override(self) -> None:
        policy = _policy(
            "deepseek",
            requested_extra_args={
                "harnessbench-deepseek-max-output-bytes": "512000001"
            },
        )

        self.assertNotIn(
            "harnessbench-deepseek-max-output-bytes", policy.extra_args
        )
        self.assertIn(
            {
                "key": "harnessbench-deepseek-max-output-bytes",
                "reason": "invalid_value",
            },
            policy.report["dropped"],
        )

    def test_allows_bounded_deepseek_output_token_limit_override(self) -> None:
        policy = _policy(
            "deepseek",
            requested_extra_args={
                "harnessbench-deepseek-max-output-tokens": "128000"
            },
        )

        self.assertEqual(
            policy.extra_args["harnessbench-deepseek-max-output-tokens"],
            "128000",
        )

    def test_rejects_unbounded_deepseek_output_token_limit_override(self) -> None:
        policy = _policy(
            "deepseek",
            requested_extra_args={
                "harnessbench-deepseek-max-output-tokens": "1000001"
            },
        )

        self.assertNotIn(
            "harnessbench-deepseek-max-output-tokens", policy.extra_args
        )


class HermesInvocationPolicyTests(unittest.TestCase):
    def test_conversation_turn_budget_never_becomes_hermes_loop_limit(self) -> None:
        for task_limit in (1, 3, 14, 120, None):
            with self.subTest(task_limit=task_limit):
                policy = _hermes_policy(task_max_turns=task_limit)

                self.assertNotIn("max-turns", policy.extra_args)
                self.assertEqual(
                    policy.report["turn_budget"],
                    {
                        "task_limit": task_limit,
                        "task_limit_scope": "runner_user_input_turns",
                        "effective_harness_limit": None,
                        "effective_harness_limit_scope": None,
                        "coupled_to_task_limit": False,
                    },
                )

    def test_explicit_hermes_loop_limit_is_independent(self) -> None:
        policy = _hermes_policy(
            task_max_turns=1,
            requested_extra_args={"max_turns": "96"},
        )

        self.assertEqual(policy.extra_args["max-turns"], "96")
        self.assertEqual(
            policy.report["turn_budget"]["effective_harness_limit"], 96
        )
        self.assertFalse(policy.report["turn_budget"]["coupled_to_task_limit"])

    def test_invalid_explicit_hermes_loop_limit_fails_closed(self) -> None:
        policy = _hermes_policy(
            task_max_turns=1,
            requested_extra_args={"max-turns": "not-an-integer"},
        )

        self.assertNotIn("max-turns", policy.extra_args)
        self.assertIn(
            {"key": "max-turns", "reason": "invalid_value"},
            policy.report["dropped"],
        )

    def test_generated_hermes_command_has_no_implicit_loop_limit(self) -> None:
        policy = _hermes_policy(task_max_turns=1)
        command = HermesAdapter().build_command(
            model="test-model",
            input_query="complete the task",
            run_dir=Path("run"),
            harness_path=None,
            extra_args=policy.extra_args,
        )

        self.assertNotIn("--max-turns", command)

    def test_all_current_runnable_tasks_keep_conversation_budget_out_of_hermes(
        self,
    ) -> None:
        task_roots = (ROOT / "Task",)
        task_paths = sorted(
            task_yaml.parent
            for task_root in task_roots
            for task_yaml in task_root.glob("*/task.yaml")
            if (task_yaml.parent / "prompt.md").is_file()
            and (task_yaml.parent / "workspace").is_dir()
        )
        self.assertTrue(task_paths)

        for task_path in task_paths:
            with self.subTest(task=task_path.name):
                task = resolve_task(task_path)
                policy = _hermes_policy(task_max_turns=task.max_turns)
                self.assertNotIn("max-turns", policy.extra_args)


if __name__ == "__main__":
    unittest.main()
