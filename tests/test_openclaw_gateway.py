from __future__ import annotations

import json
import io
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tracetrial_bench.container_images import WORKER_SOURCE_PATHS
from tracetrial_bench.openclaw_gateway import (
    _APPROVAL_RESPONDER_SCRIPT,
    OpenClawGateway,
)


class _FakeProcess:
    pid = 31415

    def __init__(self) -> None:
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float) -> int:
        self.returncode = 0
        return 0


class _FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeApprovalProcess:
    def __init__(self, lines: list[dict[str, object]]) -> None:
        self.stdout = io.StringIO(
            "".join(json.dumps(line) + "\n" for line in lines)
        )
        self.returncode = 0
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -signal.SIGKILL


class _FakeContainmentReport:
    valid = True

    def public_receipt(self) -> dict[str, object]:
        return {
            "schema_version": "harness-process-containment-v3",
            "valid": True,
            "descendant_cleanup_valid": True,
            "tracked_descendant_count": 1,
            "residual_process_count": 0,
        }


class _FakeTracker:
    def __init__(self, process: _FakeProcess) -> None:
        self.process = process
        self.terminated = False

    def terminate_all(self) -> _FakeContainmentReport:
        self.terminated = True
        self.process.returncode = 0
        return _FakeContainmentReport()


class OpenClawGatewayTests(unittest.TestCase):
    def test_gateway_module_is_in_container_worker_build_context(self) -> None:
        self.assertIn(Path("openclaw_gateway.py"), WORKER_SOURCE_PATHS)
        self.assertIn(Path("conversation_runner.py"), WORKER_SOURCE_PATHS)

    def test_lifecycle_keeps_token_out_of_argv_and_public_log(self) -> None:
        secret = "test-openclaw-gateway-token"
        process = _FakeProcess()
        captured: dict[str, object] = {}
        killed: list[tuple[int, int]] = []
        connection = _FakeConnection()
        tracker = _FakeTracker(process)
        resumed: list[tuple[int, int]] = []

        def popen(command: list[str], **kwargs: object) -> _FakeProcess:
            captured["command"] = command
            captured["environment"] = kwargs["env"]
            raw_log = kwargs["stdout"]
            raw_log.write(f"gateway token accidentally logged: {secret}\n")
            raw_log.flush()
            return process

        def killpg(pid: int, sig: int) -> None:
            killed.append((pid, sig))
            process.returncode = 0

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output_root = root / "output"
            gateway = OpenClawGateway(
                state_root=root / "state",
                output_root=output_root,
                environment={
                    "OPENCLAW_GATEWAY_TOKEN": secret,
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                },
                cwd=root,
                secret_values=(secret,),
                popen_factory=popen,
                connect_factory=lambda address, timeout: connection,
                killpg=killpg,
                tracker_factory=lambda pid: tracker,
                wait_until_stopped=lambda process: None,
                signal_process=lambda pid, sig: resumed.append((pid, sig)),
            )

            gateway.start()
            gateway.stop()

            public_log = (output_root / "openclaw-gateway.log").read_text(
                encoding="utf-8"
            )
            receipt = json.loads(
                (output_root / "openclaw-gateway-containment.json").read_text(
                    encoding="utf-8"
                )
            )

        command = captured["command"]
        expected_gateway_command = [
            "openclaw",
            "gateway",
            "run",
            "--bind",
            "loopback",
            "--port",
            "18789",
            "--auth",
            "token",
        ]
        self.assertEqual(
            command[-len(expected_gateway_command) :], expected_gateway_command
        )
        self.assertNotIn(secret, command)
        self.assertEqual(
            captured["environment"]["OPENCLAW_GATEWAY_TOKEN"], secret
        )
        self.assertTrue(connection.closed)
        self.assertEqual(resumed, [(process.pid, signal.SIGCONT)])
        self.assertTrue(tracker.terminated)
        self.assertEqual(killed, [])
        self.assertIn("<redacted>", public_log)
        self.assertNotIn(secret, public_log)
        self.assertTrue(receipt["valid"])
        self.assertEqual(receipt["residual_process_count"], 0)

    def test_startup_timeout_stops_the_gateway(self) -> None:
        process = _FakeProcess()
        killed: list[tuple[int, int]] = []
        clock = iter((0.0, 1.0))
        tracker = _FakeTracker(process)

        def popen(command: list[str], **kwargs: object) -> _FakeProcess:
            return process

        def connect(address: tuple[str, int], timeout: float) -> object:
            raise ConnectionRefusedError

        def killpg(pid: int, sig: int) -> None:
            killed.append((pid, sig))
            process.returncode = 0

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gateway = OpenClawGateway(
                state_root=root / "state",
                output_root=root / "output",
                environment={"OPENCLAW_GATEWAY_TOKEN": "test-token"},
                cwd=root,
                secret_values=("test-token",),
                startup_timeout=0.5,
                popen_factory=popen,
                connect_factory=connect,
                monotonic=lambda: next(clock),
                sleep=lambda _: None,
                killpg=killpg,
                tracker_factory=lambda pid: tracker,
                wait_until_stopped=lambda process: None,
                signal_process=lambda pid, sig: None,
            )

            with self.assertRaisesRegex(
                RuntimeError, "OpenClaw gateway did not become ready"
            ):
                gateway.start()

            self.assertTrue(tracker.terminated)

    def test_unattended_responder_denies_requested_approvals_without_command_text(self) -> None:
        calls: list[list[str]] = []

        def approval_popen(command: list[str], **kwargs: object):
            calls.append(command)
            return _FakeApprovalProcess(
                [
                    {"status": "connected"},
                    {
                        "status": "resolved",
                        "approval_id": "approval-1",
                        "decision": "deny",
                        "agent_id": "main",
                        "session_scope": "child",
                        "session_key_sha256": "1" * 64,
                        "command_sha256": "0" * 64,
                    },
                ]
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "output").mkdir()
            gateway = OpenClawGateway(
                state_root=root / "state",
                output_root=root / "output",
                environment={
                    "OPENCLAW_GATEWAY_TOKEN": "test-token",
                    "HARNESSBENCH_OPENCLAW_AUTO_DENY_APPROVALS": "1",
                },
                cwd=root,
                secret_values=("test-token",),
                approval_popen_factory=approval_popen,
                gateway_client_module=root / "client-test.js",
                approval_runtime_token_module=root / "approval-token-test.js",
            )
            gateway._start_unattended_approval_responder()
            gateway._approval_thread.join(timeout=1)
            gateway._stop_unattended_approval_responder()
            receipt = json.loads(
                (root / "output" / "openclaw-approval-responder.json").read_text(
                    encoding="utf-8"
                )
            )

        command = calls[0]
        self.assertEqual(command[0], "node")
        self.assertIn("operator.approvals", command[3])
        self.assertIn("approvalRuntimeToken", command[3])
        self.assertEqual(command[-1], (root / "approval-token-test.js").as_uri())
        self.assertNotIn("test-token", command)
        self.assertEqual(receipt["resolved_count"], 1)
        self.assertEqual(receipt["receipts"][-1]["session_scope"], "child")
        self.assertNotIn("synthetic secret-free command", json.dumps(receipt))
        self.assertIn("exec.approval.requested", _APPROVAL_RESPONDER_SCRIPT)
        self.assertNotIn("exec.approval.list", _APPROVAL_RESPONDER_SCRIPT)

    def test_stop_terminates_child_that_detached_with_setsid(self) -> None:
        helper = (
            "import pathlib,subprocess,sys,time\n"
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],"
            " start_new_session=True)\n"
            "pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding='ascii')\n"
            "time.sleep(60)\n"
        )
        root_process: subprocess.Popen[str] | None = None
        child_pid: int | None = None

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            child_pid_path = root / "child.pid"

            def popen(command: list[str], **kwargs: object) -> subprocess.Popen[str]:
                nonlocal root_process
                root_process = subprocess.Popen(
                    [sys.executable, "-c", helper, str(child_pid_path)],
                    **kwargs,
                )
                return root_process

            gateway = OpenClawGateway(
                state_root=root / "state",
                output_root=root / "output",
                environment={"OPENCLAW_GATEWAY_TOKEN": "test-token"},
                cwd=root,
                secret_values=("test-token",),
                popen_factory=popen,
                connect_factory=lambda address, timeout: _FakeConnection(),
                wait_until_stopped=lambda process: None,
                signal_process=lambda pid, sig: None,
            )
            try:
                gateway.start()
                deadline = time.monotonic() + 2
                while not child_pid_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(child_pid_path.exists())
                child_pid = int(child_pid_path.read_text(encoding="ascii"))

                gateway.stop()

                receipt = json.loads(
                    (root / "output" / "openclaw-gateway-containment.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertTrue(receipt["valid"])
                self.assertGreaterEqual(receipt["tracked_descendant_count"], 1)
                self.assertEqual(receipt["residual_process_count"], 0)
                self.assertIsNotNone(root_process)
                self.assertIsNotNone(root_process.returncode)
                self.assertFalse(_is_live_process(child_pid))
            finally:
                for pid in (
                    child_pid,
                    root_process.pid if root_process is not None else None,
                ):
                    if pid is None:
                        continue
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass


def _is_live_process(pid: int) -> bool:
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except OSError:
        return False
    fields = stat_text[stat_text.rfind(")") + 2 :].split()
    return bool(fields and fields[0] != "Z")


if __name__ == "__main__":
    unittest.main()
