from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path

from harnessbench.container_worker import (
    _read_request,
    materialize_harness_state,
    resolve_harness_extra_args,
)
from harnessbench.model_registry import INTERNAL_API_KEY_ENV, ModelRuntimeSpec
from harnessbench.litellm_gateway import (
    OutputSecurityError,
    assert_output_has_no_secrets,
    redact_output_secrets,
)
from harnessbench.task_tool_mcp import BRIDGE_RELATIVE_PATH
from harness_tracer.writer import EventWriter


def _hermes_model() -> ModelRuntimeSpec:
    return ModelRuntimeSpec(
        alias="test-model",
        provider="test-provider",
        model="upstream-model",
        base_url="https://model.invalid/v1",
        api_mode="chat_completions",
        harness="hermes",
        harness_model="hermes-model",
        harness_provider="custom:test-provider",
        api_key="test-api-key",
    )


def _openclaw_model() -> ModelRuntimeSpec:
    return ModelRuntimeSpec(
        alias="test-model",
        provider="test-provider",
        model="upstream-model",
        base_url="https://model.invalid/v1",
        api_mode="chat_completions",
        harness="openclaw",
        harness_model="test-provider/upstream-model",
        harness_provider=None,
        api_key="test-api-key",
    )


def _codex_model() -> ModelRuntimeSpec:
    return ModelRuntimeSpec(
        alias="test-model",
        provider="test-provider",
        model="upstream-model",
        base_url="https://model.invalid/v1",
        api_mode="chat_completions",
        harness="codex",
        harness_model="upstream-model",
        harness_provider=None,
        api_key="test-api-key",
    )


def _deepseek_model(
    *, reasoning_effort: str = "max"
) -> ModelRuntimeSpec:
    return ModelRuntimeSpec(
        alias="test-model",
        provider="test-provider",
        model="deepseek-v4-flash",
        base_url="https://model.invalid/v1",
        api_mode="chat_completions",
        harness="deepseek",
        harness_model="deepseek-v4-flash",
        harness_provider=None,
        api_key="test-api-key",
        deepseek_reasoning_effort=reasoning_effort,
    )


class HermesContainerWorkerTests(unittest.TestCase):
    def test_materialized_state_enables_delegation_toolset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()

            environment = materialize_harness_state(
                model=_hermes_model(),
                state_root=root / "state",
                workspace=workspace,
            )

            config_path = Path(environment["HERMES_CONFIG"])
            config = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(
            config["platform_toolsets"]["cli"],
            ["terminal", "file", "delegation", "no_mcp"],
        )

    def test_extra_args_force_delegation_toolset(self) -> None:
        resolved = resolve_harness_extra_args(
            _hermes_model(),
            {
                "provider": "untrusted-provider",
                "toolsets": "all",
                "max-turns": 12,
            },
        )

        self.assertEqual(resolved["provider"], "custom:test-provider")
        self.assertEqual(resolved["toolsets"], "terminal,file,delegation")
        self.assertEqual(resolved["harnessbench-stateless"], "true")
        self.assertEqual(resolved["max-turns"], "12")

    def test_model_call_budget_routes_hermes_through_local_gateway(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            environment = materialize_harness_state(
                model=_hermes_model(),
                state_root=root / "state",
                workspace=workspace,
                gateway_base_url="http://127.0.0.1:4001",
                gateway_token="budget-token",
                model_call_budget={
                    "version": "provider_requests_v1",
                    "limit": 18,
                    "finalization_reserve": 5,
                },
            )
            config = json.loads(Path(environment["HERMES_CONFIG"]).read_text())

        provider = config["custom_providers"][0]
        self.assertEqual(provider["base_url"], "http://127.0.0.1:4001/v1")
        self.assertEqual(provider["key_env"], "HARNESSBENCH_GATEWAY_TOKEN")
        self.assertEqual(environment["HARNESSBENCH_GATEWAY_TOKEN"], "budget-token")

    def test_provider_capture_routes_hermes_through_recorder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            environment = materialize_harness_state(
                model=_hermes_model(),
                state_root=root / "state",
                workspace=workspace,
                gateway_base_url="http://127.0.0.1:4002",
                provider_request_capture=True,
            )
            config = json.loads(Path(environment["HERMES_CONFIG"]).read_text())

        provider = config["custom_providers"][0]
        self.assertEqual(provider["base_url"], "http://127.0.0.1:4002/v1")
        self.assertEqual(provider["key_env"], INTERNAL_API_KEY_ENV)


class CodexContainerWorkerTests(unittest.TestCase):
    def test_docker_task_tools_use_private_preconfigured_server(self) -> None:
        resolved = resolve_harness_extra_args(
            _codex_model(),
            {"harnessbench-task-tools": "true"},
        )

        self.assertEqual(resolved["harnessbench-task-tools-preconfigured"], "true")
        self.assertEqual(
            resolved["harnessbench-task-tool-bridge"],
            "/workspace/.harnessbench/task_tool_mcp.py",
        )

    def test_subscription_state_materializes_codex_home_with_task_tools(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            bridge = workspace / BRIDGE_RELATIVE_PATH
            bridge.parent.mkdir(parents=True)
            bridge.write_text("pass\n", encoding="utf-8")
            environment = materialize_harness_state(
                model=_codex_model(),
                state_root=root / "state",
                workspace=workspace,
                auth_mode="subscription",
                subscription_auth={
                    "schema_version": "harnessbench_subscription_auth_v1",
                    "harness": "codex",
                    "files": {
                        "auth.json": base64.b64encode(b'{"token":"abc"}').decode(
                            "ascii"
                        ),
                        "config.toml": base64.b64encode(
                            b'model_reasoning_effort = "medium"\n'
                        ).decode("ascii"),
                    },
                    "env": {},
                },
                task_tools={
                    "version": "harnessbench_task_tools_v1",
                    "tools": [{"name": "lookup", "description": "lookup"}],
                },
                task_tool_endpoint={
                    "schema_version": "harnessbench_task_tool_endpoint_v1",
                    "transport": "tcp",
                    "host": "harnessbench-host",
                    "port": 49172,
                    "auth_token": "task-tool-token",
                    "task_id": "test-task",
                    "run_id": "test-run",
                },
            )
            codex_home = Path(environment["CODEX_HOME"])
            config_text = (codex_home / "config.toml").read_text(encoding="utf-8")
            auth_mode = (codex_home / "auth.json").stat().st_mode & 0o777

        self.assertEqual(codex_home.parent, root / "state")
        self.assertEqual(environment["HOME"], str(root / "state" / "codex-home"))
        self.assertEqual(auth_mode, 0o600)
        self.assertIn('model_reasoning_effort = "medium"', config_text)
        self.assertIn("[mcp_servers.harnessbench]", config_text)


class ClaudeCodeContainerWorkerTests(unittest.TestCase):
    def test_subscription_state_materializes_claude_config_dir_with_env(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            bridge = workspace / BRIDGE_RELATIVE_PATH
            bridge.parent.mkdir(parents=True)
            bridge.write_text("pass\n", encoding="utf-8")
            environment = materialize_harness_state(
                model=ModelRuntimeSpec(
                    alias="test-model",
                    provider="test-provider",
                    model="upstream-model",
                    base_url="https://model.invalid/v1",
                    api_mode="messages",
                    harness="claudecode",
                    harness_model="sonnet",
                    harness_provider=None,
                    api_key="test-api-key",
                ),
                state_root=root / "state",
                workspace=workspace,
                auth_mode="subscription",
                subscription_auth={
                    "schema_version": "harnessbench_subscription_auth_v1",
                    "harness": "claudecode",
                    "files": {
                        ".credentials.json": base64.b64encode(b"{}").decode("ascii")
                    },
                    "env": {"CLAUDE_CODE_OAUTH_TOKEN": "oauth-token"},
                },
                task_tools={
                    "version": "harnessbench_task_tools_v1",
                    "tools": [{"name": "lookup", "description": "lookup"}],
                },
                task_tool_endpoint={
                    "schema_version": "harnessbench_task_tool_endpoint_v1",
                    "transport": "tcp",
                    "host": "harnessbench-host",
                    "port": 49172,
                    "auth_token": "task-tool-token",
                    "task_id": "test-task",
                    "run_id": "test-run",
                },
            )
            claude_config = Path(environment["CLAUDE_CONFIG_DIR"])
            mcp_config = json.loads(
                (claude_config / "task-tools.mcp.json").read_text(
                    encoding="utf-8"
                )
            )
            credentials_mode = (
                (claude_config / ".credentials.json").stat().st_mode & 0o777
            )

        self.assertEqual(claude_config.parent, root / "state")
        self.assertEqual(
            environment["HOME"], str(root / "state" / "claudecode-home")
        )
        self.assertEqual(environment["CLAUDE_CODE_OAUTH_TOKEN"], "oauth-token")
        self.assertEqual(credentials_mode, 0o600)
        self.assertIn("harnessbench", mcp_config["mcpServers"])


class DeepSeekContainerWorkerTests(unittest.TestCase):
    def test_materialized_state_uses_official_jsonrpc_composition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            environment = materialize_harness_state(
                model=_deepseek_model(),
                state_root=root / "state",
                workspace=workspace,
            )
            config_text = Path(environment["DSH_CORDIS_CONFIG"]).read_text(
                encoding="utf-8"
            )
            modules_target = (
                Path(environment["DSH_CORDIS_CONFIG"])
                .parent.joinpath("node_modules")
                .readlink()
            )

        self.assertEqual(environment["DEEPSEEK_API_KEY"], "test-api-key")
        self.assertEqual(environment["DEEPSEEK_BASE_URL"], "https://model.invalid/v1")
        self.assertEqual(environment["DSH_PERMISSION_MODE"], "danger-full-access")
        self.assertEqual(environment["DSH_REASONING_EFFORT"], "max")
        self.assertEqual(modules_target, Path("/opt/deepseek-runtime/node_modules"))
        self.assertIn("@deepseek-ai/dsh-sdk-jsonrpc-server", config_text)
        self.assertIn("@deepseek-ai/dsh-agent-spine-demo", config_text)
        self.assertIn("@deepseek-ai/dsh-tool-terminal", config_text)
        self.assertIn("@deepseek-ai/dsh-session-persistence-jsonl", config_text)
        self.assertIn("@deepseek-ai/dsh-subagent", config_text)
        self.assertIn("reasoningEffort: max", config_text)
        self.assertIn("enableRunInBackground: false", config_text)
        self.assertNotIn("backgroundMode: continuable", config_text)
        self.assertNotIn("test-api-key", config_text)
        self.assertGreater(
            config_text.index("@deepseek-ai/dsh-sdk-jsonrpc-server"),
            config_text.index("@deepseek-ai/dsh-compaction-basic"),
        )

    def test_continuable_subagents_are_enabled_only_by_private_task_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            environment = materialize_harness_state(
                model=_deepseek_model(),
                state_root=root / "state",
                workspace=workspace,
                harness_settings={
                    "harnessbench-deepseek-continuable-subagents": "true"
                },
            )
            config_text = Path(environment["DSH_CORDIS_CONFIG"]).read_text(
                encoding="utf-8"
            )

        tool_subagent = config_text.split("- id: tool-subagent", 1)[1].split(
            "- id: tool-todo", 1
        )[0]
        self.assertIn("enableRunInBackground: true", tool_subagent)
        self.assertIn("backgroundMode: continuable", tool_subagent)
        self.assertNotIn("enableRunInBackground: false", tool_subagent)

    def test_reasoning_intensity_configures_deepseek_effort(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            environment = materialize_harness_state(
                model=_deepseek_model(reasoning_effort="low"),
                state_root=root / "state",
                workspace=workspace,
                harness_settings={"reasoning-effort": "high"},
            )
            config_text = Path(environment["DSH_CORDIS_CONFIG"]).read_text(
                encoding="utf-8"
            )

        self.assertEqual(environment["DSH_REASONING_EFFORT"], "high")
        self.assertIn("reasoningEffort: high", config_text)

    def test_low_reasoning_intensity_uses_native_off(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            environment = materialize_harness_state(
                model=_deepseek_model(reasoning_effort="max"),
                state_root=root / "state",
                workspace=workspace,
                harness_settings={"reasoning-effort": "low"},
            )
            config_text = Path(environment["DSH_CORDIS_CONFIG"]).read_text(
                encoding="utf-8"
            )

        self.assertEqual(environment["DSH_REASONING_EFFORT"], "low")
        self.assertIn("reasoningEffort: off", config_text)

    def test_materialized_state_uses_model_reasoning_effort(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()

            environment = materialize_harness_state(
                model=_deepseek_model(reasoning_effort="high"),
                state_root=root / "state",
                workspace=workspace,
            )
            config_text = Path(environment["DSH_CORDIS_CONFIG"]).read_text(
                encoding="utf-8"
            )

        self.assertEqual(environment["DSH_REASONING_EFFORT"], "high")
        self.assertIn("reasoningEffort: high", config_text)

    def test_provider_capture_routes_deepseek_through_recorder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            environment = materialize_harness_state(
                model=_deepseek_model(),
                state_root=root / "state",
                workspace=workspace,
                gateway_base_url="http://127.0.0.1:4002",
                provider_request_capture=True,
            )

        self.assertEqual(
            environment["DEEPSEEK_BASE_URL"], "http://127.0.0.1:4002/v1"
        )
        self.assertEqual(
            environment["DEEPSEEK_API_KEY"], "harnessbench-provider-recorder"
        )

    def test_model_call_budget_routes_deepseek_through_gateway(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            environment = materialize_harness_state(
                model=_deepseek_model(),
                state_root=root / "state",
                workspace=workspace,
                gateway_base_url="http://127.0.0.1:4001",
                gateway_token="budget-token",
                model_call_budget={
                    "version": "provider_requests_v1",
                    "limit": 18,
                    "finalization_reserve": 5,
                },
            )

        self.assertEqual(
            environment["DEEPSEEK_BASE_URL"], "http://127.0.0.1:4001/v1"
        )
        self.assertEqual(environment["DEEPSEEK_API_KEY"], "budget-token")

    def test_approval_probe_uses_workspace_sandbox_and_native_ask(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            environment = materialize_harness_state(
                model=_deepseek_model(),
                state_root=root / "state",
                workspace=workspace,
                security_settings={
                    "harnessbench-approval-policy": "ask_always",
                    "harnessbench-approval-fallback": "unavailable",
                },
            )
            config_text = Path(environment["DSH_CORDIS_CONFIG"]).read_text(
                encoding="utf-8"
            )

        self.assertEqual(environment["DSH_PERMISSION_MODE"], "workspace-write")
        self.assertIn("@deepseek-ai/dsh-sandbox-policy", config_text)
        self.assertIn("@deepseek-ai/dsh-user-approval", config_text)
        self.assertIn("? 'never' : 'ask'", config_text)


class OpenClawContainerWorkerTests(unittest.TestCase):
    def test_explicit_thinking_marks_custom_model_as_reasoning_capable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()

            environment = materialize_harness_state(
                model=_openclaw_model(),
                state_root=root / "state",
                workspace=workspace,
                gateway_token="test-gateway-token",
                harness_settings={"thinking": "high"},
            )
            config = json.loads(
                Path(environment["OPENCLAW_CONFIG_PATH"]).read_text(encoding="utf-8")
            )

        model_entry = config["models"]["providers"]["test-provider"]["models"][0]
        self.assertTrue(model_entry["reasoning"])

    def test_default_thinking_does_not_change_custom_model_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()

            environment = materialize_harness_state(
                model=_openclaw_model(),
                state_root=root / "state",
                workspace=workspace,
                gateway_token="test-gateway-token",
            )
            config = json.loads(
                Path(environment["OPENCLAW_CONFIG_PATH"]).read_text(encoding="utf-8")
            )

        model_entry = config["models"]["providers"]["test-provider"]["models"][0]
        self.assertNotIn("reasoning", model_entry)

    def test_materialized_state_requires_gateway_token(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()

            with self.assertRaisesRegex(ValueError, "gateway token is missing"):
                materialize_harness_state(
                    model=_openclaw_model(),
                    state_root=root / "state",
                    workspace=workspace,
                )

    def test_materialized_state_uses_ephemeral_gateway_token_from_environment(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()

            environment = materialize_harness_state(
                model=_openclaw_model(),
                state_root=root / "state",
                workspace=workspace,
                transport_environment={
                    "http_proxy": "http://proxy.invalid:8080",
                    "no_proxy": "model.invalid",
                },
                gateway_token="test-gateway-token",
            )

            config_path = Path(environment["OPENCLAW_CONFIG_PATH"])
            config_text = config_path.read_text(encoding="utf-8")
            config = json.loads(config_text)

        self.assertEqual(
            environment["OPENCLAW_GATEWAY_TOKEN"], "test-gateway-token"
        )
        self.assertEqual(
            environment["no_proxy"],
            "model.invalid,127.0.0.1,localhost,::1",
        )
        self.assertEqual(
            config["gateway"],
            {
                "mode": "local",
                "bind": "loopback",
                "port": 18789,
                "auth": {"mode": "token"},
            },
        )
        self.assertNotIn("test-gateway-token", config_text)

    def test_extra_args_dispatch_parent_through_managed_gateway(self) -> None:
        resolved = resolve_harness_extra_args(_openclaw_model(), {})

        self.assertEqual(resolved["harnessbench-gateway"], "true")

    def test_model_call_budget_routes_openclaw_provider_through_local_gateway(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            environment = materialize_harness_state(
                model=_openclaw_model(),
                state_root=root / "state",
                workspace=workspace,
                gateway_base_url="http://127.0.0.1:4001",
                gateway_token="budget-token",
                model_call_budget={
                    "version": "provider_requests_v1",
                    "limit": 18,
                    "finalization_reserve": 5,
                },
            )
            config = json.loads(
                Path(environment["OPENCLAW_CONFIG_PATH"]).read_text()
            )

        provider = config["models"]["providers"]["test-provider"]
        self.assertEqual(provider["baseUrl"], "http://127.0.0.1:4001/v1")
        self.assertEqual(provider["apiKey"], "budget-token")

    def test_provider_capture_routes_openclaw_provider_through_recorder(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            environment = materialize_harness_state(
                model=_openclaw_model(),
                state_root=root / "state",
                workspace=workspace,
                gateway_base_url="http://127.0.0.1:4002",
                gateway_token="gateway-token",
                provider_request_capture=True,
            )
            config = json.loads(
                Path(environment["OPENCLAW_CONFIG_PATH"]).read_text()
            )

        provider = config["models"]["providers"]["test-provider"]
        self.assertEqual(provider["baseUrl"], "http://127.0.0.1:4002/v1")
        self.assertEqual(provider["apiKey"], "test-api-key")

    def test_approval_probe_materializes_fail_closed_openclaw_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            environment = materialize_harness_state(
                model=_openclaw_model(),
                state_root=root / "state",
                workspace=workspace,
                gateway_token="test-gateway-token",
                security_settings={
                    "harnessbench-approval-policy": "ask_always",
                    "harnessbench-approval-fallback": "unavailable",
                },
            )
            config = json.loads(
                Path(environment["OPENCLAW_CONFIG_PATH"]).read_text(encoding="utf-8")
            )
            approvals = json.loads(
                (Path(environment["OPENCLAW_STATE_DIR"]) / "exec-approvals.json").read_text(
                    encoding="utf-8"
                )
            )
        self.assertEqual(
            config["tools"]["exec"],
            {"security": "full", "ask": "always"},
        )
        self.assertEqual(approvals["defaults"]["ask"], "always")
        self.assertEqual(approvals["defaults"]["askFallback"], "deny")
        self.assertEqual(approvals["socket"]["token"], "test-gateway-token")
        self.assertEqual(
            environment["HARNESSBENCH_OPENCLAW_AUTO_DENY_APPROVALS"],
            "1",
        )

    def test_approval_probe_enables_hermes_native_ask_surface(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            environment = materialize_harness_state(
                model=_hermes_model(),
                state_root=root / "state",
                workspace=workspace,
                security_settings={
                    "harnessbench-approval-policy": "ask_always",
                    "harnessbench-approval-fallback": "unavailable",
                },
            )
            config = json.loads(
                Path(environment["HERMES_CONFIG"]).read_text(encoding="utf-8")
            )

        self.assertEqual(environment["HERMES_EXEC_ASK"], "1")
        self.assertEqual(config["approvals"], {"mode": "manual"})


class ContainerConversationRequestTests(unittest.TestCase):
    def _request(self, boundaries: object, context: object = "none") -> str:
        return json.dumps(
            {
                "schema_version": "harnessbench_container_request_v1",
                "task_id": "session-boundary-test",
                "harness": "codex",
                "model": {},
                "prompt": "first",
                "conversation": {
                    "mode": "fixed_turns",
                    "turns": ["first", "second", "third"],
                    "max_turns": 3,
                    "new_session_before_turns": boundaries,
                    "new_session_context": context,
                },
            }
        )

    def test_fixed_turn_request_accepts_explicit_session_boundary(self) -> None:
        request = _read_request(self._request([3], "replay"))

        self.assertEqual(
            request["conversation"]["new_session_before_turns"],
            [3],
        )
        self.assertEqual(request["conversation"]["new_session_context"], "replay")

    def test_fixed_turn_request_rejects_invalid_session_boundary(self) -> None:
        for boundaries in ([1], [4], [3, 3], "3"):
            with self.subTest(boundaries=boundaries):
                with self.assertRaisesRegex(ValueError, "session boundaries"):
                    _read_request(self._request(boundaries))

    def test_fixed_turn_request_rejects_replay_without_boundary(self) -> None:
        with self.assertRaisesRegex(ValueError, "new-session context"):
            _read_request(self._request([], "replay"))


class OutputCredentialRedactionTests(unittest.TestCase):
    def test_event_writer_redacts_before_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.json"
            writer = EventWriter(
                path,
                run_id="run-1",
                redact_values=("private-gateway-token",),
            )
            writer.write(
                "raw_stdout",
                text="authorization=private-gateway-token",
            )
            writer.close()
            persisted = path.read_text(encoding="utf-8")

        self.assertNotIn("private-gateway-token", persisted)
        self.assertIn("<redacted>", persisted)

    def test_output_redaction_remediates_text_and_preserves_hard_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "events.json"
            artifact.write_text(
                '{"authorization":"private-gateway-token"}',
                encoding="utf-8",
            )

            result = redact_output_secrets(root, ("private-gateway-token",))
            validation = assert_output_has_no_secrets(
                root,
                ("private-gateway-token",),
            )

        self.assertEqual(result, {"files_redacted": 1, "matches_redacted": 1})
        self.assertEqual(validation["files"], 1)

    def test_output_redaction_rejects_unknown_binary_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "trace.bin").write_bytes(b"private-gateway-token")

            with self.assertRaisesRegex(
                OutputSecurityError,
                "non-text output artifact",
            ):
                redact_output_secrets(root, ("private-gateway-token",))


if __name__ == "__main__":
    unittest.main()
