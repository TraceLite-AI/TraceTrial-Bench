from __future__ import annotations

import json
import os
import re
import select
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from tracetrial_bench.business_simulator import (
    BUSINESS_ENDPOINT_ENV,
    BusinessSimulatorRuntime,
    probe_business_endpoint,
)
from tracetrial_bench.container_worker import materialize_harness_state
from tracetrial_bench.model_registry import ModelRuntimeSpec
from tracetrial_bench.score_config import load_score_sheet
from tracetrial_bench.task_resolver import resolve_task
from tracetrial_bench.workspace import prepare_run_dir


ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "tasks" / "new_cases" / "C1c-escalation-export-L3-001"
PTY_CASE = ROOT / "tasks" / "new_cases" / "M-E4c-integration-L3-001"
ESCALATION = re.compile(
    r"^LINE=(\d{6}) ESCALATED ticket=(\S+).* escalation_code=([0-9A-F]{10})$",
    re.MULTILINE,
)


def model(harness: str) -> ModelRuntimeSpec:
    return ModelRuntimeSpec(
        alias="test-model",
        provider="test-provider",
        model="upstream-model",
        base_url="https://model.invalid/v1",
        api_mode="chat_completions",
        harness=harness,
        harness_model=(
            "test-provider/upstream-model"
            if harness == "openclaw"
            else "harness-model"
        ),
        harness_provider=("custom:test-provider" if harness == "hermes" else None),
        api_key="test-api-key",
    )


class BusinessCliContractTests(unittest.TestCase):
    def test_opt_in_interactive_read_timeout_returns_model_visible_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copied_case = root / "case"
            shutil.copytree(PTY_CASE, copied_case)
            scenario_path = copied_case / "simulator" / "scenario.json"
            scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
            scenario["read_timeout_sec"] = 0.1
            scenario_path.write_text(json.dumps(scenario), encoding="utf-8")
            task = resolve_task(copied_case)
            layout = prepare_run_dir(task, root / "run")
            cli = layout.workspace_dir / "taskctl"
            master_fd, slave_fd = os.openpty()
            try:
                with BusinessSimulatorRuntime(task, layout):
                    process = subprocess.Popen(
                        [str(cli), "pty_probe"],
                        cwd=layout.workspace_dir,
                        stdin=slave_fd,
                        stdout=slave_fd,
                        stderr=slave_fd,
                    )
                    os.close(slave_fd)
                    slave_fd = -1
                    chunks = []
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        readable, _, _ = select.select([master_fd], [], [], 0.2)
                        if readable:
                            try:
                                chunk = os.read(master_fd, 4096)
                            except OSError:
                                break
                            if chunk:
                                chunks.append(chunk)
                        if process.poll() is not None and not readable:
                            break
                    self.assertEqual(process.wait(timeout=1), 3)
                output = b"".join(chunks).decode("utf-8", "replace")
            finally:
                os.close(master_fd)
                if slave_fd >= 0:
                    os.close(slave_fd)

            self.assertIn("PTY_LINE> ", output)
            self.assertIn("INTERACTIVE_INPUT_TIMEOUT", output)
            self.assertIn('"timeout_sec": 0.1', output)

    def test_new_c1c_is_single_turn_cli_without_task_mcp(self) -> None:
        task = resolve_task(CASE)

        self.assertEqual(task.conversation.mode, "single_turn")
        self.assertIsNone(task.task_tools)
        assert task.business_simulator is not None
        self.assertEqual(task.business_simulator.cli_path, Path("bin/ticketctl"))
        self.assertFalse((CASE / "simulator" / "tools.json").exists())
        self.assertIn("as the sole command in its own native command-execution tool call", task.prompt)
        self.assertIn("Do not wrap, combine, pipe, redirect, filter", task.prompt)
        self.assertIn("cover every source-line coordinate", task.prompt)
        self.assertNotIn("command substitution", task.prompt)
        self.assertNotIn("Harness", task.prompt)
        self.assertNotIn("Runner", task.prompt)
        self.assertEqual(task.metadata["input_mode"], "single_turn")
        self.assertEqual(
            task.metadata["tool_dependencies"],
            ["native_exec_v1", "post_tool_v1", "business_cli_v1"],
        )
        self.assertIsNotNone(task.execution_probe)
        self.assertTrue((CASE / "verifier" / "selftest.py").is_file())
        turns = json.loads((CASE / "turns.json").read_text(encoding="utf-8"))
        self.assertEqual(turns["turns"], [task.prompt])

        harness_sheet = load_score_sheet(
            CASE / "verifier" / "harness_score.yaml",
            expected_layer="harness",
        )
        agent_sheet = load_score_sheet(
            CASE / "verifier" / "agent_score.yaml",
            expected_layer="agent",
        )
        self.assertEqual(len(harness_sheet.rule_items), 3)
        self.assertEqual(len(harness_sheet.rubric_items), 6)
        self.assertEqual(len(agent_sheet.rule_items), 3)
        self.assertEqual(len(agent_sheet.rubric_items), 2)

    def test_complete_export_is_one_time_and_ranges_are_bounded(self) -> None:
        task = resolve_task(CASE)
        with tempfile.TemporaryDirectory() as temporary:
            layout = prepare_run_dir(task, Path(temporary) / "run")
            cli = layout.workspace_dir / "bin" / "ticketctl"
            with BusinessSimulatorRuntime(task, layout):
                complete = subprocess.run(
                    [str(cli), "export"],
                    cwd=layout.workspace_dir,
                    text=True,
                    capture_output=True,
                    check=True,
                    timeout=30,
                )
                snapshot = re.search(
                    r"^SNAPSHOT_ID=(\S+)$", complete.stdout, re.MULTILINE
                ).group(1)
                repeated = subprocess.run(
                    [str(cli), "export"],
                    cwd=layout.workspace_dir,
                    text=True,
                    capture_output=True,
                    timeout=30,
                )
                oversized = subprocess.run(
                    [
                        str(cli),
                        "range",
                        "--snapshot",
                        snapshot,
                        "--start-line",
                        "1",
                        "--end-line",
                        "201",
                    ],
                    cwd=layout.workspace_dir,
                    text=True,
                    capture_output=True,
                    timeout=30,
                )

            self.assertEqual(repeated.returncode, 2)
            self.assertIn("already been issued", repeated.stderr)
            self.assertEqual(oversized.returncode, 2)
            self.assertIn("at most 200 source lines", oversized.stderr)
            state = json.loads(
                (layout.private_dir / "simulator" / "state.json").read_text()
            )
            self.assertEqual(state["complete_export_calls"], 1)
            self.assertEqual(state["rejected_export_calls"], 1)
            self.assertEqual(state["range_export_calls"], 0)

    def test_complete_export_and_range_share_stable_coordinates_and_codes(self) -> None:
        task = resolve_task(CASE)
        with tempfile.TemporaryDirectory() as temporary:
            layout = prepare_run_dir(task, Path(temporary) / "run")
            cli = layout.workspace_dir / "bin" / "ticketctl"
            with BusinessSimulatorRuntime(task, layout):
                complete = subprocess.run(
                    [str(cli), "export"],
                    cwd=layout.workspace_dir,
                    text=True,
                    capture_output=True,
                    check=True,
                    timeout=30,
                )
                matches = ESCALATION.findall(complete.stdout)
                self.assertEqual(len(matches), 12)
                snapshot = re.search(
                    r"^SNAPSHOT_ID=(\S+)$", complete.stdout, re.MULTILINE
                ).group(1)
                line_number, ticket_id, code = matches[5]
                ranged = subprocess.run(
                    [
                        str(cli),
                        "range",
                        "--snapshot",
                        snapshot,
                        "--start-line",
                        str(int(line_number) - 1),
                        "--end-line",
                        str(int(line_number) + 1),
                    ],
                    cwd=layout.workspace_dir,
                    text=True,
                    capture_output=True,
                    check=True,
                    timeout=30,
                )

            self.assertGreater(len(complete.stdout.encode("utf-8")), 5_000_000)
            self.assertEqual(len(complete.stdout.splitlines()), 9014)
            self.assertIn(
                "TOTAL_ESCALATED=12 TOTAL_TICKETS=9012 SOURCE_LINE_COUNT=9012",
                complete.stdout[:250],
            )
            self.assertIn(
                f"LINE={line_number} ESCALATED ticket={ticket_id}", ranged.stdout
            )
            self.assertIn(f"escalation_code={code}", ranged.stdout)
            self.assertIn("SCANNED_LINE_COUNT=3 MATCH_COUNT=1", ranged.stdout)
            self.assertEqual(len(ranged.stdout.splitlines()), 4)
            ledger = layout.private_dir / "simulator" / "ledger.jsonl"
            self.assertEqual(len(ledger.read_text(encoding="utf-8").splitlines()), 2)

    def test_generated_cli_reaches_authenticated_tcp_backend(self) -> None:
        task = resolve_task(CASE)
        with tempfile.TemporaryDirectory() as temporary:
            layout = prepare_run_dir(task, Path(temporary) / "run")
            cli = layout.workspace_dir / "bin" / "ticketctl"
            with BusinessSimulatorRuntime(
                task,
                layout,
                enable_container_transport=True,
            ) as runtime:
                endpoint = runtime.business_endpoint
                endpoint["host"] = "127.0.0.1"
                probe_business_endpoint(endpoint)
                environment = {
                    **os.environ,
                    BUSINESS_ENDPOINT_ENV: json.dumps(endpoint),
                }
                completed = subprocess.run(
                    [str(cli), "range", "--snapshot", "missing", "--start-line", "1", "--end-line", "1"],
                    cwd=layout.workspace_dir,
                    text=True,
                    capture_output=True,
                    env=environment,
                    timeout=30,
                )

            self.assertEqual(completed.returncode, 2)
            self.assertIn("Run the complete export", completed.stderr)
            ledger = layout.private_dir / "simulator" / "ledger.jsonl"
            self.assertEqual(len(ledger.read_text(encoding="utf-8").splitlines()), 1)

    def test_all_five_harness_environments_receive_only_the_business_endpoint(self) -> None:
        endpoint = {
            "schema_version": "harnessbench_business_simulator_endpoint_v1",
            "transport": "tcp",
            "host": "harnessbench-host",
            "port": 49172,
            "auth_token": "business-token",
            "task_id": "test-task",
            "run_id": "test-run",
        }
        for harness in ("codex", "claudecode", "hermes", "openclaw", "deepseek"):
            with self.subTest(harness=harness), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                workspace = root / "workspace"
                workspace.mkdir()
                kwargs = {
                    "model": model(harness),
                    "state_root": root / "state",
                    "workspace": workspace,
                    "business_simulator_endpoint": endpoint,
                }
                if harness in {"codex", "claudecode"}:
                    kwargs.update(
                        gateway_base_url="http://127.0.0.1:4000",
                        gateway_token="gateway-token",
                    )
                elif harness == "openclaw":
                    kwargs.update(gateway_token="gateway-token")
                environment = materialize_harness_state(**kwargs)

                self.assertIn(BUSINESS_ENDPOINT_ENV, environment)
                self.assertNotIn("HARNESSBENCH_TASK_TOOLS", environment)
                self.assertNotIn("HARNESSBENCH_TASK_TOOL_ENDPOINT", environment)


if __name__ == "__main__":
    unittest.main()
