from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.task_fixtures import paper_task

from tracetrial_bench import container_runtime
from tracetrial_bench.model_registry import ModelRuntimeSpec
from tracetrial_bench.runner import _container_limits_for_task
from tracetrial_bench.task_resolver import _container_resources, resolve_task


ROOT = Path(__file__).resolve().parents[1]


class RuntimeGatewayTokenTests(unittest.TestCase):
    def test_hermes_does_not_allocate_a_gateway_token(self) -> None:
        self.assertTrue(
            hasattr(container_runtime, "_resolve_runtime_gateway_token"),
            "runtime gateway-token selection is missing",
        )

        def token_factory() -> str:
            self.fail("Hermes must not allocate a gateway token")

        token = container_runtime._resolve_runtime_gateway_token(
            "hermes", token_factory
        )

        self.assertIsNone(token)

    def test_openclaw_receives_a_per_run_gateway_token(self) -> None:
        self.assertTrue(
            hasattr(container_runtime, "_resolve_runtime_gateway_token"),
            "runtime gateway-token selection is missing",
        )
        calls = 0

        def token_factory() -> str:
            nonlocal calls
            calls += 1
            return "test-gateway-token"

        token = container_runtime._resolve_runtime_gateway_token(
            "openclaw", token_factory
        )

        self.assertEqual(token, "test-gateway-token")
        self.assertEqual(calls, 1)


class RuntimeBudgetMetadataTests(unittest.TestCase):
    def test_direct_budget_harness_does_not_require_litellm_metadata(self) -> None:
        self.assertIsNone(
            container_runtime.validate_worker_gateway_summary(
                {"status": "finished"},
                "openclaw",
                model_call_budget=True,
            )
        )

    def test_budget_startup_failure_without_summary_preserves_root_cause(self) -> None:
        self.assertIsNone(
            container_runtime.validate_worker_model_call_budget_summary(
                {"status": "failed"},
                {
                    "version": "provider_requests_v1",
                    "limit": 18,
                    "finalization_reserve": 5,
                },
            )
        )

    def test_opt_in_budget_allocates_gateway_token_for_hermes(self) -> None:
        calls = 0

        def token_factory() -> str:
            nonlocal calls
            calls += 1
            return "budget-gateway-token"

        token = container_runtime._resolve_runtime_gateway_token(
            "hermes",
            token_factory,
            model_call_budget=True,
        )

        self.assertEqual(token, "budget-gateway-token")
        self.assertEqual(calls, 1)


class ContainerArchiveLimitTests(unittest.TestCase):
    def test_canonical_trace_can_use_the_bounded_total_budget(self) -> None:
        self.assertEqual(
            container_runtime._export_file_limit(
                Path("trace/run-abc/events.json")
            ),
            container_runtime.MAX_EXPORTED_TOTAL_BYTES,
        )
        self.assertEqual(
            container_runtime._export_file_limit(
                Path("trace/run-abc/turns/run-def/events.json")
            ),
            container_runtime.MAX_EXPORTED_TOTAL_BYTES,
        )

    def test_non_trace_file_keeps_the_ordinary_per_file_limit(self) -> None:
        self.assertEqual(
            container_runtime._export_file_limit(Path("agent/workspace/blob.bin")),
            container_runtime.MAX_EXPORTED_FILE_BYTES,
        )
        self.assertEqual(
            container_runtime._export_file_limit(Path("trace/run-abc/summary.json")),
            container_runtime.MAX_EXPORTED_FILE_BYTES,
        )


class RuntimeSubscriptionAuthTests(unittest.TestCase):
    def test_subscription_auth_does_not_require_gateway_metadata(self) -> None:
        self.assertIsNone(
            container_runtime.validate_worker_gateway_summary(
                {"status": "finished"},
                "codex",
                auth_mode="subscription",
            )
        )

    def test_subscription_auth_does_not_require_provider_request_metadata(self) -> None:
        self.assertIsNone(
            container_runtime.validate_worker_provider_request_summary(
                {"status": "finished"},
                configured=False,
            )
        )

    def test_subscription_request_accepts_codex_without_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            artifacts = Path(tmp) / "artifacts"
            workspace.mkdir()
            request = container_runtime.ContainerRunRequest(
                task_id="subscription-smoke",
                harness="codex",
                model=ModelRuntimeSpec(
                    alias="gpt-5-codex",
                    provider="subscription",
                    model="gpt-5-codex",
                    base_url="",
                    api_mode="native_subscription",
                    harness="codex",
                    harness_model="gpt-5-codex",
                    harness_provider=None,
                    api_key="",
                ),
                prompt="Say ok.",
                workspace=workspace,
                artifacts_dir=artifacts,
                timeout=60,
                auth_mode="subscription",
                subscription_auth={
                    "schema_version": "harnessbench_subscription_auth_v1",
                    "harness": "codex",
                    "files": {"auth.json": "e30K"},
                    "env": {},
                },
            )

        self.assertEqual(request.auth_mode, "subscription")


class ProviderRequestEvidenceMetadataTests(unittest.TestCase):
    def test_valid_provider_request_summary_is_accepted(self) -> None:
        summary = {
            "schema_version": "harnessbench_provider_request_evidence_v1",
            "version": "model_visible_tools_v1",
            "request_count": 3,
            "model_visible_request_count": 2,
            "complete_tool_inventory_count": 1,
            "events_path": "provider_requests/events.jsonl",
        }

        self.assertEqual(
            container_runtime.validate_worker_provider_request_summary(
                {"status": "finished", "provider_request_evidence": summary}
            ),
            summary,
        )

    def test_finished_run_requires_provider_request_summary(self) -> None:
        with self.assertRaisesRegex(
            container_runtime.ContainerRuntimeError,
            "provider-request evidence metadata",
        ):
            container_runtime.validate_worker_provider_request_summary(
                {"status": "finished"}
            )

    def test_recorder_startup_failure_preserves_worker_failure(self) -> None:
        self.assertIsNone(
            container_runtime.validate_worker_provider_request_summary(
                {"status": "failed"}
            )
        )


class PerTaskContainerResourceTests(unittest.TestCase):
    def test_unconfigured_task_keeps_four_gib_default(self) -> None:
        task = resolve_task(paper_task("E1b-pipeline-stderr-L3-001"))

        self.assertIsNone(task.container_resources)
        self.assertEqual(
            _container_limits_for_task(task).memory_bytes,
            container_runtime.DEFAULT_MEMORY_BYTES,
        )

    def test_t2d_uses_explicit_eight_gib_limit(self) -> None:
        task = resolve_task(
            paper_task("T2d-coercion-advice-L3-001")
        )

        self.assertIsNotNone(task.container_resources)
        self.assertEqual(task.container_resources.memory_bytes, 8 * 1024**3)
        self.assertEqual(
            _container_limits_for_task(task).memory_bytes,
            8 * 1024**3,
        )

    def test_resource_schema_rejects_unknown_version_and_excessive_memory(self) -> None:
        with self.assertRaisesRegex(ValueError, "container_limits_v1"):
            _container_resources(
                {"container_resources": {"version": "future", "memory_bytes": 1}}
            )
        with self.assertRaisesRegex(ValueError, "must not exceed"):
            _container_resources(
                {
                    "container_resources": {
                        "version": "container_limits_v1",
                        "memory_bytes": 65 * 1024**3,
                    }
                }
            )


if __name__ == "__main__":
    unittest.main()
