from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness_tracer.adapters import adapter_for
from tracetrial_bench.business_simulator import BusinessSimulatorRuntime
from tracetrial_bench.container_worker import materialize_harness_state
from tracetrial_bench.model_registry import ModelRuntimeSpec
from tracetrial_bench.task_resolver import resolve_task
from tracetrial_bench.task_tool_mcp import (
    BRIDGE_RELATIVE_PATH,
    _dispatch,
    install_task_tool_bridge,
    probe_task_tool_endpoint,
    task_tool_environment,
)
from tracetrial_bench.workspace import prepare_run_dir


ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "tasks" / "cases" / "C1c-escalation-export-L3-001"
TOOLS = {
    "schema_version": "harnessbench_task_tools_v1",
    "tools": [
        {
            "name": "tickets_export",
            "description": "Export tickets.",
            "inputSchema": {"type": "object", "properties": {}},
        }
    ],
}


def model(harness: str) -> ModelRuntimeSpec:
    return ModelRuntimeSpec(
        alias="test-model",
        provider="test-provider",
        model="upstream-model",
        base_url="https://model.invalid/v1",
        api_mode="chat_completions",
        harness=harness,
        harness_model=(
            "test-provider/upstream-model" if harness == "openclaw" else "harness-model"
        ),
        harness_provider=("custom:test-provider" if harness == "hermes" else None),
        api_key="test-api-key",
    )


class TaskToolContractTests(unittest.TestCase):
    def test_legacy_c1c_resolves_one_english_native_tool(self) -> None:
        task = resolve_task(CASE)

        self.assertEqual(task.conversation.mode, "single_turn")
        self.assertIsNotNone(task.task_tools)
        assert task.task_tools is not None
        self.assertEqual([tool["name"] for tool in task.task_tools.tools], ["tickets_export"])
        self.assertTrue(task.task_tools.tools[0]["description"].isascii())

    def test_mcp_bridge_lists_and_calls_the_logical_tool(self) -> None:
        tools = TOOLS["tools"]
        indexed = {tool["name"]: tool for tool in tools}
        listed = _dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            tools,
            indexed,
        )

        self.assertEqual(listed["result"]["tools"], tools)
        with mock.patch(
            "tracetrial_bench.task_tool_mcp._call_simulator",
            return_value={"ok": True, "_task_tool_content": "TOTAL_TICKETS=9012\n"},
        ):
            called = _dispatch(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "tickets_export", "arguments": {}},
                },
                tools,
                indexed,
            )

        self.assertEqual(called["result"]["content"][0]["text"], "TOTAL_TICKETS=9012\n")
        self.assertFalse(called["result"]["isError"])

    def test_real_bridge_calls_private_simulator_and_returns_large_export(self) -> None:
        task = resolve_task(CASE)
        assert task.task_tools is not None
        with tempfile.TemporaryDirectory() as temporary:
            layout = prepare_run_dir(task, Path(temporary) / "run")
            environment = {
                **os.environ,
                **task_tool_environment(
                    task.task_tools.tools,
                    socket_path=layout.workspace_dir
                    / ".harnessbench-business-simulator.sock",
                ),
            }
            requests = [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05"},
                },
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "tickets_export", "arguments": {}},
                },
            ]
            stdin = "".join(json.dumps(request) + "\n" for request in requests)
            with BusinessSimulatorRuntime(task, layout):
                completed = subprocess.run(
                    [sys.executable, str(layout.workspace_dir / BRIDGE_RELATIVE_PATH)],
                    input=stdin,
                    text=True,
                    capture_output=True,
                    env=environment,
                    check=True,
                    timeout=30,
                )

            responses = [json.loads(line) for line in completed.stdout.splitlines()]
            self.assertEqual(
                responses[1]["result"]["tools"][0]["name"],
                "tickets_export",
            )
            export = responses[2]["result"]["content"][0]["text"]
            self.assertFalse(
                responses[2]["result"]["isError"],
                export[:500],
            )
            self.assertGreater(len(export.encode("utf-8")), 5_000_000)
            self.assertIn("TOTAL_ESCALATED=12 TOTAL_TICKETS=9012", export[:200])

    def test_authenticated_tcp_endpoint_reaches_the_same_private_simulator(self) -> None:
        task = resolve_task(CASE)
        assert task.task_tools is not None
        with tempfile.TemporaryDirectory() as temporary:
            layout = prepare_run_dir(task, Path(temporary) / "run")
            with BusinessSimulatorRuntime(
                task,
                layout,
                enable_container_transport=True,
            ) as runtime:
                endpoint = runtime.task_tool_endpoint
                endpoint["host"] = "127.0.0.1"
                probe_task_tool_endpoint(endpoint)
                environment = {
                    **os.environ,
                    **task_tool_environment(
                        task.task_tools.tools,
                        endpoint=endpoint,
                    ),
                }
                request = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "tickets_export", "arguments": {}},
                }
                completed = subprocess.run(
                    [sys.executable, str(layout.workspace_dir / BRIDGE_RELATIVE_PATH)],
                    input=json.dumps(request) + "\n",
                    text=True,
                    capture_output=True,
                    env=environment,
                    check=True,
                    timeout=30,
                )

            response = json.loads(completed.stdout)
            self.assertFalse(response["result"]["isError"])
            self.assertIn(
                "TOTAL_TICKETS=9012",
                response["result"]["content"][0]["text"][:200],
            )
            ledger = layout.private_dir / "simulator" / "ledger.jsonl"
            self.assertEqual(len(ledger.read_text().splitlines()), 1)

    def test_tcp_transport_rejects_invalid_auth_without_ledger_action(self) -> None:
        task = resolve_task(CASE)
        with tempfile.TemporaryDirectory() as temporary:
            layout = prepare_run_dir(task, Path(temporary) / "run")
            with BusinessSimulatorRuntime(
                task,
                layout,
                enable_container_transport=True,
            ) as runtime:
                endpoint = runtime.task_tool_endpoint
                endpoint["host"] = "127.0.0.1"
                endpoint["auth_token"] = "invalid-auth-token"
                with self.assertRaisesRegex(RuntimeError, "health check failed"):
                    probe_task_tool_endpoint(endpoint)

            ledger = layout.private_dir / "simulator" / "ledger.jsonl"
            self.assertEqual(ledger.read_text(encoding="utf-8"), "")

    def test_all_five_container_harnesses_receive_the_same_server(self) -> None:
        for harness in ("codex", "claudecode", "hermes", "openclaw", "deepseek"):
            with self.subTest(harness=harness), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                workspace = root / "workspace"
                workspace.mkdir()
                install_task_tool_bridge(workspace)
                kwargs = {
                    "model": model(harness),
                    "state_root": root / "state",
                    "workspace": workspace,
                    "task_tools": TOOLS,
                    "task_tool_endpoint": {
                        "schema_version": "harnessbench_task_tool_endpoint_v1",
                        "transport": "tcp",
                        "host": "harnessbench-host",
                        "port": 49172,
                        "auth_token": "task-tool-token",
                        "task_id": "test-task",
                        "run_id": "test-run",
                    },
                }
                if harness in {"codex", "claudecode"}:
                    kwargs.update(
                        gateway_base_url="http://127.0.0.1:4000",
                        gateway_token="gateway-token",
                    )
                elif harness == "openclaw":
                    kwargs.update(gateway_token="gateway-token")
                environment = materialize_harness_state(**kwargs)
                self.assertIn(
                    "HARNESSBENCH_TASK_TOOL_ENDPOINT",
                    environment,
                )
                self.assertNotIn(
                    "HARNESSBENCH_TASK_TOOL_SOCKET",
                    environment,
                )

                if harness == "codex":
                    config = Path(environment["CODEX_HOME"]) / "config.toml"
                    self.assertIn("[mcp_servers.harnessbench]", config.read_text())
                elif harness == "claudecode":
                    config = Path(environment["CLAUDE_CONFIG_DIR"]) / "task-tools.mcp.json"
                    self.assertIn("harnessbench", json.loads(config.read_text())["mcpServers"])
                elif harness == "hermes":
                    config = json.loads(Path(environment["HERMES_CONFIG"]).read_text())
                    self.assertIn("harnessbench", config["mcp_servers"])
                    self.assertIn("harnessbench", config["platform_toolsets"]["cli"])
                elif harness == "openclaw":
                    config = json.loads(Path(environment["OPENCLAW_CONFIG_PATH"]).read_text())
                    server = config["mcp"]["servers"]["harnessbench"]
                    self.assertEqual(server["command"], sys.executable)
                    self.assertNotIn("transport", server)
                else:
                    config = Path(environment["DSH_CORDIS_CONFIG"]).read_text()
                    self.assertIn("@deepseek-ai/dsh-mcp-client", config)
                    self.assertIn(str(workspace / ".harnessbench" / "task_tool_mcp.py"), config)
                    self.assertLess(
                        config.index("@deepseek-ai/dsh-mcp-client"),
                        config.index("@deepseek-ai/dsh-sdk-jsonrpc-server"),
                    )

    def test_new_case_runbooks_cover_the_four_formal_harnesses(self) -> None:
        runbooks = sorted((ROOT / "tasks" / "new_cases").glob("*/run.md"))
        self.assertTrue(runbooks)
        for runbook in runbooks:
            content = runbook.read_text(encoding="utf-8")
            for harness in ("openclaw", "codex", "claudecode", "deepseek"):
                with self.subTest(runbook=runbook.parent.name, harness=harness):
                    self.assertEqual(content.count(f"--harness {harness}"), 1)
            self.assertNotIn("--harness hermes", content)

    def test_codex_and_claude_adapters_consume_internal_mcp_arguments(self) -> None:
        common = {
            "model": "model",
            "input_query": "prompt",
            "run_dir": Path("run"),
            "harness_path": None,
        }
        codex = adapter_for("codex").build_command(
            **common,
            extra_args={
                "harnessbench-task-tools": "true",
                "harnessbench-task-tool-bridge": "/workspace/bridge.py",
            },
        )
        claude = adapter_for("claudecode").build_command(
            **common,
            extra_args={
                "harnessbench-task-tools": "true",
                "harnessbench-task-tool-config": "/state/mcp.json",
            },
        )

        self.assertIn("mcp_servers.harnessbench.command=\"python3\"", codex)
        self.assertIn("--mcp-config", claude)
        self.assertNotIn("--harnessbench-task-tools", codex)
        self.assertNotIn("--harnessbench-task-tools", claude)

        preconfigured_codex = adapter_for("codex").build_command(
            **common,
            extra_args={
                "harnessbench-task-tools": "true",
                "harnessbench-task-tool-bridge": "/workspace/bridge.py",
                "harnessbench-task-tools-preconfigured": "true",
            },
        )
        self.assertFalse(
            any("mcp_servers.harnessbench" in argument for argument in preconfigured_codex)
        )
        self.assertNotIn("--harnessbench-task-tools-preconfigured", preconfigured_codex)


if __name__ == "__main__":
    unittest.main()
