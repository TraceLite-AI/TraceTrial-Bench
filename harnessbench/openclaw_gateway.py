from __future__ import annotations

import json
import os
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Mapping

from harness_tracer.process import (
    _STOP_BEFORE_EXEC,
    _DescendantTracker,
    _wait_until_stopped,
)


OPENCLAW_GATEWAY_HOST = "127.0.0.1"
OPENCLAW_GATEWAY_PORT = 18789
OPENCLAW_GATEWAY_TOKEN_ENV = "OPENCLAW_GATEWAY_TOKEN"
OPENCLAW_INSTALL_DIST = Path("/usr/local/lib/node_modules/openclaw/dist")
_APPROVAL_RESPONDER_SCRIPT = """
import { createHash } from "node:crypto";
const [moduleUrl, approvalTokenModuleUrl] = process.argv.slice(1);
const { n: GatewayClient } = await import(moduleUrl);
const { t: getOperatorApprovalRuntimeToken } = await import(approvalTokenModuleUrl);
const resolved = new Set();
const resolving = new Set();
let client;
const emit = (payload) => process.stdout.write(JSON.stringify(payload) + "\\n");
const denyRequestedApproval = async (event) => {
  const item = event?.payload;
  const id = typeof item?.id === "string" ? item.id : "";
  if (!id || resolved.has(id) || resolving.has(id)) return;
  resolving.add(id);
  try {
    await client.request(
      "exec.approval.resolve",
      { id, decision: "deny" },
      { timeoutMs: 2000 },
    );
    resolved.add(id);
    const request = item?.request && typeof item.request === "object" ? item.request : {};
    const command = item?.commandText ?? item?.command ?? request.command ?? "";
    const sessionKey = String(item?.sessionKey ?? request.sessionKey ?? "");
    emit({
      status: "resolved",
      approval_id: id,
      decision: "deny",
      agent_id: item?.agentId ?? request.agentId ?? null,
      session_scope: sessionKey.includes(":subagent:") ? "child" : "main",
      session_key_sha256: createHash("sha256").update(sessionKey).digest("hex"),
      command_sha256: createHash("sha256").update(String(command)).digest("hex"),
    });
  } catch {
    emit({ status: "resolve_error", error_type: "GatewayRequestError" });
  } finally {
    resolving.delete(id);
  }
};
client = new GatewayClient({
  url: "ws://127.0.0.1:18789",
  token: process.env.OPENCLAW_GATEWAY_TOKEN,
  approvalRuntimeToken: getOperatorApprovalRuntimeToken(),
  deviceIdentity: null,
  role: "operator",
  scopes: ["operator.approvals"],
  clientName: "gateway-client",
  clientDisplayName: "harnessbench-approval-deny",
  clientVersion: "harnessbench",
  mode: "backend",
  requestTimeoutMs: 2000,
  onHelloOk: () => emit({ status: "connected" }),
  onEvent: (event) => {
    if (event?.event === "exec.approval.requested") {
      void denyRequestedApproval(event);
    }
  },
  onConnectError: () => emit({ status: "connection_error" }),
  onClose: () => emit({ status: "closed" }),
});
process.on("SIGTERM", () => {
  client.stop();
  process.exit(0);
});
client.start();
""".strip()


class OpenClawGateway:
    def __init__(
        self,
        *,
        state_root: Path,
        output_root: Path,
        environment: Mapping[str, str],
        cwd: Path,
        secret_values: tuple[str, ...],
        startup_timeout: float = 30.0,
        shutdown_timeout: float = 5.0,
        health_poll_interval: float = 0.1,
        popen_factory: Callable[..., object] = subprocess.Popen,
        connect_factory: Callable[..., object] = socket.create_connection,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        killpg: Callable[[int, int], None] = os.killpg,
        tracker_factory: Callable[[int], object] = _DescendantTracker,
        wait_until_stopped: Callable[[object], None] = _wait_until_stopped,
        signal_process: Callable[[int, int], None] = os.kill,
        approval_popen_factory: Callable[..., object] = subprocess.Popen,
        gateway_client_module: Path | None = None,
        approval_runtime_token_module: Path | None = None,
    ) -> None:
        if startup_timeout <= 0 or shutdown_timeout <= 0:
            raise ValueError("OpenClaw gateway timeouts must be positive")
        if health_poll_interval < 0:
            raise ValueError("OpenClaw gateway poll interval must not be negative")
        token = environment.get(OPENCLAW_GATEWAY_TOKEN_ENV)
        if not isinstance(token, str) or not token:
            raise ValueError("OpenClaw gateway token is missing")
        self.state_root = Path(state_root) / "openclaw-gateway"
        self.output_root = Path(output_root)
        self.environment = dict(environment)
        self.cwd = Path(cwd)
        self.secret_values = tuple(secret_values)
        self.startup_timeout = startup_timeout
        self.shutdown_timeout = shutdown_timeout
        self.health_poll_interval = health_poll_interval
        self.popen_factory = popen_factory
        self.connect_factory = connect_factory
        self.monotonic = monotonic
        self.sleep = sleep
        self.killpg = killpg
        self.tracker_factory = tracker_factory
        self.wait_until_stopped = wait_until_stopped
        self.signal_process = signal_process
        self.approval_popen_factory = approval_popen_factory
        self.gateway_client_module = gateway_client_module
        self.approval_runtime_token_module = approval_runtime_token_module
        self.raw_log_path = self.state_root / "openclaw-gateway.raw.log"
        self.public_log_path = self.output_root / "openclaw-gateway.log"
        self.containment_path = (
            self.output_root / "openclaw-gateway-containment.json"
        )
        self._process: object | None = None
        self._tracker: object | None = None
        self._raw_log = None
        self._approval_stop = threading.Event()
        self._approval_thread: threading.Thread | None = None
        self._approval_process: object | None = None
        self._approval_receipts: list[dict[str, object]] = []

    def start(self) -> None:
        if self._process is not None or self._raw_log is not None:
            raise RuntimeError("OpenClaw gateway is already started")
        _ensure_private_directory(self.state_root)
        _ensure_private_directory(self.output_root)
        self._raw_log = _open_private_log(self.raw_log_path)
        command = [
            "openclaw",
            "gateway",
            "run",
            "--bind",
            "loopback",
            "--port",
            str(OPENCLAW_GATEWAY_PORT),
            "--auth",
            "token",
        ]
        try:
            self._process = self.popen_factory(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    _STOP_BEFORE_EXEC,
                    *command,
                ],
                stdout=self._raw_log,
                stderr=subprocess.STDOUT,
                env=self.environment,
                cwd=str(self.cwd),
                text=True,
                start_new_session=True,
            )
            self.wait_until_stopped(self._process)
            self._tracker = self.tracker_factory(self._process.pid)
            self.signal_process(self._process.pid, signal.SIGCONT)
            self._wait_until_ready()
            self._start_unattended_approval_responder()
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        process = self._process
        tracker = self._tracker
        errors: list[Exception] = []
        try:
            self._stop_unattended_approval_responder()
            if tracker is not None:
                try:
                    report = tracker.terminate_all()
                    receipt = report.public_receipt()
                    _write_private_bytes(
                        self.containment_path,
                        (
                            json.dumps(receipt, indent=2, sort_keys=True) + "\n"
                        ).encode("utf-8"),
                    )
                    if not report.valid:
                        errors.append(
                            RuntimeError(
                                "OpenClaw gateway process containment failed "
                                f"(tracking_failed={receipt.get('tracking_failed')}, "
                                "residual_process_count="
                                f"{receipt.get('residual_process_count')})"
                            )
                        )
                except Exception as exc:
                    errors.append(exc)
                if process is not None:
                    try:
                        process.wait(timeout=self.shutdown_timeout)
                    except subprocess.TimeoutExpired as exc:
                        errors.append(exc)
            elif process is not None and process.poll() is None:
                try:
                    self.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=self.shutdown_timeout)
                except subprocess.TimeoutExpired:
                    try:
                        self.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=self.shutdown_timeout)
        finally:
            self._process = None
            self._tracker = None
            self._publish_sanitized_log()
        if errors:
            raise RuntimeError(
                "; ".join(f"{type(error).__name__}: {error}" for error in errors)
            )

    def _start_unattended_approval_responder(self) -> None:
        if self.environment.get("HARNESSBENCH_OPENCLAW_AUTO_DENY_APPROVALS") != "1":
            return
        self._approval_stop.clear()
        self._approval_receipts.clear()
        self._approval_receipts.append({"status": "started"})
        self._publish_approval_receipts()
        module = self.gateway_client_module or _resolve_gateway_client_module()
        token_module = (
            self.approval_runtime_token_module
            or _resolve_approval_runtime_token_module()
        )
        self.gateway_client_module = module
        self.approval_runtime_token_module = token_module
        self._approval_process = self.approval_popen_factory(
            [
                "node",
                "--input-type=module",
                "--eval",
                _APPROVAL_RESPONDER_SCRIPT,
                module.as_uri(),
                token_module.as_uri(),
            ],
            cwd=str(self.cwd),
            env=self.environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._approval_thread = threading.Thread(
            target=self._read_unattended_approval_receipts,
            name="harnessbench-openclaw-approval-receipts",
            daemon=True,
        )
        self._approval_thread.start()

    def _stop_unattended_approval_responder(self) -> None:
        thread = self._approval_thread
        if (
            thread is None
            and self.environment.get("HARNESSBENCH_OPENCLAW_AUTO_DENY_APPROVALS")
            != "1"
        ):
            return
        self._approval_stop.set()
        process = self._approval_process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5.0)
        if thread is not None:
            thread.join(timeout=5.0)
            if thread.is_alive():
                raise RuntimeError("OpenClaw approval responder did not stop")
        self._approval_thread = None
        self._approval_process = None
        self._publish_approval_receipts()

    def _publish_approval_receipts(self) -> None:
        _write_private_bytes(
            self.output_root / "openclaw-approval-responder.json",
            (
                json.dumps(
                    {
                        "schema_version": "harnessbench_openclaw_approval_responder_v1",
                        "mode": "deny",
                        "resolved_count": sum(
                            receipt.get("status") == "resolved"
                            for receipt in self._approval_receipts
                        ),
                        "receipts": self._approval_receipts,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8"),
        )

    def _read_unattended_approval_receipts(self) -> None:
        process = self._approval_process
        stdout = getattr(process, "stdout", None)
        if process is None or stdout is None:
            self._approval_receipts.append(
                {"status": "responder_error", "error_type": "MissingStdout"}
            )
            self._publish_approval_receipts()
            return
        for line in stdout:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                payload = {"status": "responder_error", "error_type": "InvalidJson"}
            if not isinstance(payload, dict):
                continue
            safe = {
                key: payload.get(key)
                for key in (
                    "status",
                    "pending_count",
                    "approval_id",
                    "decision",
                    "agent_id",
                    "session_scope",
                    "session_key_sha256",
                    "command_sha256",
                    "error_type",
                )
                if key in payload
            }
            if safe and self._approval_receipts[-1:] != [safe]:
                self._approval_receipts.append(safe)
                self._publish_approval_receipts()
        returncode = process.wait()
        if not self._approval_stop.is_set() and returncode != 0:
            self._approval_receipts.append(
                {
                    "status": "responder_error",
                    "error_type": f"ProcessExit{returncode}",
                }
            )
            self._publish_approval_receipts()

    def _wait_until_ready(self) -> None:
        if self._process is None:
            raise RuntimeError("OpenClaw gateway process was not created")
        deadline = self.monotonic() + self.startup_timeout
        while True:
            returncode = self._process.poll()
            if returncode is not None:
                raise RuntimeError(f"OpenClaw gateway exited with {returncode}")
            try:
                connection = self.connect_factory(
                    (OPENCLAW_GATEWAY_HOST, OPENCLAW_GATEWAY_PORT),
                    timeout=1.0,
                )
            except (OSError, TimeoutError):
                pass
            else:
                connection.close()
                return
            if self.monotonic() >= deadline:
                raise RuntimeError("OpenClaw gateway did not become ready")
            self.sleep(self.health_poll_interval)

    def _publish_sanitized_log(self) -> None:
        if self._raw_log is not None:
            self._raw_log.close()
            self._raw_log = None
        content = _read_private_regular_bytes(self.raw_log_path)
        if content is None:
            return
        try:
            for secret_value in self.secret_values:
                if secret_value:
                    content = content.replace(
                        secret_value.encode("utf-8"), b"<redacted>"
                    )
            _write_private_bytes(self.public_log_path, content)
        finally:
            self.raw_log_path.unlink(missing_ok=True)


def _resolve_gateway_client_module() -> Path:
    for candidate in sorted(OPENCLAW_INSTALL_DIST.glob("client-*.js")):
        try:
            source = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        if "var GatewayClient = class" in source and "GatewayClient as n" in source:
            return candidate.resolve()
    raise RuntimeError("OpenClaw Gateway client module is unavailable")


def _resolve_approval_runtime_token_module() -> Path:
    for candidate in sorted(
        OPENCLAW_INSTALL_DIST.glob("operator-approval-runtime-token-*.js")
    ):
        try:
            source = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        if (
            "function getOperatorApprovalRuntimeToken" in source
            and "getOperatorApprovalRuntimeToken as t" in source
        ):
            return candidate.resolve()
    raise RuntimeError("OpenClaw approval runtime token module is unavailable")


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError("private OpenClaw runtime path is not a real directory")
    path.chmod(0o700)


def _open_private_log(path: Path):
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise RuntimeError("cannot open private OpenClaw gateway log") from exc
    os.fchmod(descriptor, 0o600)
    return os.fdopen(descriptor, "w", encoding="utf-8")


def _read_private_regular_bytes(path: Path) -> bytes | None:
    try:
        expected_info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError("cannot inspect private OpenClaw gateway log") from exc
    if not stat.S_ISREG(expected_info.st_mode):
        raise RuntimeError("private OpenClaw gateway log is not a regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError("cannot open private OpenClaw gateway log") from exc
    try:
        opened_info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened_info.st_mode)
            or opened_info.st_dev != expected_info.st_dev
            or opened_info.st_ino != expected_info.st_ino
        ):
            raise RuntimeError("private OpenClaw gateway log changed during inspection")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(descriptor)


def _write_private_bytes(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise RuntimeError("cannot write OpenClaw gateway artifact") from exc
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
