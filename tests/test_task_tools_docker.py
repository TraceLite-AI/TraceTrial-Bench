from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from harnessbench.business_simulator import BusinessSimulatorRuntime
from harnessbench.task_resolver import resolve_task
from harnessbench.task_tool_mcp import BRIDGE_RELATIVE_PATH, task_tool_environment
from harnessbench.workspace import prepare_run_dir


ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "tasks" / "cases" / "C1c-escalation-export-L3-001"


class DockerTaskToolTransportSmokeTests(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("HARNESSBENCH_DOCKER_SMOKE_IMAGE"),
        "set HARNESSBENCH_DOCKER_SMOKE_IMAGE to run the Docker transport smoke test",
    )
    def test_container_bridge_reaches_host_simulator(self) -> None:
        image = os.environ["HARNESSBENCH_DOCKER_SMOKE_IMAGE"]
        task = resolve_task(CASE)
        assert task.task_tools is not None
        with tempfile.TemporaryDirectory() as temporary:
            layout = prepare_run_dir(task, Path(temporary) / "run")
            with BusinessSimulatorRuntime(
                task,
                layout,
                enable_container_transport=True,
            ) as runtime:
                environment = task_tool_environment(
                    task.task_tools.tools,
                    endpoint=runtime.task_tool_endpoint,
                )
                request = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "tickets_export", "arguments": {}},
                }
                process_environment = {**os.environ, **environment}
                command = [
                    "docker",
                    "run",
                    "--rm",
                    "--interactive",
                    "--network",
                    "bridge",
                    "--add-host",
                    "harnessbench-host:host-gateway",
                    "--mount",
                    f"type=bind,source={layout.workspace_dir},target=/workspace",
                ]
                for name in sorted(environment):
                    command.extend(["--env", name])
                command.extend(
                    [
                        "--entrypoint",
                        "python3",
                        image,
                        str(Path("/workspace") / BRIDGE_RELATIVE_PATH),
                    ]
                )
                completed = subprocess.run(
                    command,
                    input=json.dumps(request) + "\n",
                    text=True,
                    capture_output=True,
                    env=process_environment,
                    check=True,
                    timeout=60,
                )

            response = json.loads(completed.stdout)
            content = response["result"]["content"][0]["text"]
            self.assertFalse(response["result"]["isError"])
            self.assertGreater(len(content.encode("utf-8")), 5_000_000)
            ledger = layout.private_dir / "simulator" / "ledger.jsonl"
            self.assertEqual(len(ledger.read_text(encoding="utf-8").splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
