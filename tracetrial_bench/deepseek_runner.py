from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from harness_tracer.adapters.deepseek import DeepSeekAdapter
from harness_tracer.process import (
    _DescendantTracker,
    _STOP_BEFORE_EXEC,
    _wait_until_stopped,
)
from harness_tracer.provider_requests import (
    provider_request_cursor,
    trace_tool_inventory_payloads,
)
from harness_tracer.runner import (
    RunConfig,
    _environment_summary,
    _insert_provider_tool_inventories,
    _redact_writer_events,
    _safe_isolation_summary,
)
from harness_tracer.summary import build_summary, write_last_message, write_summary
from harness_tracer.timeutils import utc_now_iso
from harness_tracer.writer import EventWriter


DEFAULT_BRIDGE = Path("/opt/harnessbench-src/tracetrial_bench/deepseek_bridge.mjs")
DEFAULT_RUNTIME = Path(
    "/opt/deepseek-runtime/node_modules/.bin/dsh-jsonrpc-agent"
)


class DeepSeekBridgeError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        notifications: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.notifications = list(notifications or [])


class DeepSeekBridgeTimeout(TimeoutError):
    def __init__(
        self, message: str, *, notifications: list[dict[str, Any]] | None = None
    ) -> None:
        super().__init__(message)
        self.notifications = list(notifications or [])


class DeepSeekBridgeOutputLimit(DeepSeekBridgeError):
    pass


class DeepSeekBridgeResourceError(DeepSeekBridgeError):
    def __init__(
        self,
        message: str,
        *,
        error_type: str,
        notifications: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message, notifications=notifications)
        self.error_type = error_type


class _StderrCollector:
    def __init__(self, stream: Any, *, max_bytes: int) -> None:
        self.stream = stream
        self.max_bytes = max_bytes
        self.buffer = bytearray()
        self.observed_bytes = 0
        self.failed = False
        self.thread = threading.Thread(
            target=self._collect,
            name="deepseek-bridge-stderr",
            daemon=True,
        )

    def start(self) -> None:
        self.thread.start()

    def finish(self) -> None:
        self.thread.join(timeout=3)
        if self.thread.is_alive():
            self.failed = True

    @property
    def limit_exceeded(self) -> bool:
        return self.observed_bytes > self.max_bytes

    def text(self) -> str:
        return bytes(self.buffer).decode("utf-8", errors="replace")

    def _collect(self) -> None:
        try:
            while chunk := self.stream.read(64 * 1024):
                self.observed_bytes += len(chunk)
                remaining = self.max_bytes - len(self.buffer)
                if remaining > 0:
                    self.buffer.extend(chunk[:remaining])
        except BaseException:
            self.failed = True
        finally:
            try:
                self.stream.close()
            except OSError:
                pass


_DEEPSEEK_DEFAULT_MAX_OUTPUT_BYTES = 64_000_000


class DeepSeekSdkBridge:
    """Own one official SDK client and its complete runtime process tree."""

    def __init__(
        self,
        *,
        environment: dict[str, str],
        cwd: Path,
        model: str,
        output_root: Path,
        redact_values: tuple[str, ...] = (),
        max_output_bytes: int | None = None,
        max_output_tokens: int | None = None,
        max_rss_bytes: int | None = None,
        bridge_path: Path = DEFAULT_BRIDGE,
        runtime_path: Path = DEFAULT_RUNTIME,
        terminal_event_return: bool = False,
    ) -> None:
        self.environment = dict(environment)
        self.cwd = cwd
        self.model = model
        self.output_root = output_root
        self.redact_values = redact_values
        self.max_output_bytes = (
            _DEEPSEEK_DEFAULT_MAX_OUTPUT_BYTES
            if max_output_bytes is None
            else max_output_bytes
        )
        self.max_output_tokens = max_output_tokens
        self.max_rss_bytes = max_rss_bytes
        self.bridge_path = bridge_path
        self.runtime_path = runtime_path
        self.terminal_event_return = terminal_event_return
        self.terminal_event_returned = False
        self.process: subprocess.Popen[bytes] | None = None
        self.tracker: _DescendantTracker | None = None
        self.stderr_collector: _StderrCollector | None = None
        self.stdout_buffer = bytearray()
        self.stdout_observed_bytes = 0
        self.request_id = 0
        self.initialized = False
        self.receipt: dict[str, object] | None = None

    def run(
        self,
        input_query: str,
        *,
        session_id: str,
        timeout: int | None,
        wait_for_continuable_children: bool = False,
    ) -> dict[str, Any]:
        deadline = None if timeout is None else time.monotonic() + timeout
        self._ensure_started(deadline)
        payload = self._rpc(
            "run",
            {
                "input": input_query,
                "sessionId": session_id,
                "waitForContinuableChildren": wait_for_continuable_children,
            },
            deadline=deadline,
        )
        if not isinstance(payload, dict):
            raise DeepSeekBridgeError("DeepSeek SDK returned a non-object run result")
        return payload

    def close(self) -> dict[str, object]:
        if self.receipt is not None:
            return self.receipt
        close_error: Exception | None = None
        process = self.process
        if (
            process is not None
            and process.poll() is None
            and not self.terminal_event_returned
        ):
            try:
                self._rpc("close", {}, deadline=time.monotonic() + 12.0)
            except Exception as exc:
                close_error = exc
        receipt = self._terminate_and_receipt()
        receipt["lifecycle_shutdown_valid"] = close_error is None
        if self.terminal_event_returned:
            receipt["lifecycle_shutdown_mode"] = "native_parent_turn_end"
        if close_error is not None:
            receipt["valid"] = False
        self._write_logs()
        return receipt

    def abort(self) -> dict[str, object]:
        if self.receipt is None:
            self._terminate_and_receipt()
            self._write_logs()
        assert self.receipt is not None
        return self.receipt

    def _ensure_started(self, deadline: float | None) -> None:
        if self.initialized:
            return
        self._start_process()
        params = {
            "runtimeCommand": str(self.runtime_path),
            "runtimeArgs": [self.environment["DSH_CORDIS_CONFIG"]],
            "cwd": str(self.cwd),
            "env": self.environment,
            "provider": "deepseek-official",
            "model": self.model,
        }
        if self.max_output_tokens is not None:
            params["maxTokens"] = self.max_output_tokens
        result = self._rpc("initialize", params, deadline=deadline)
        if not isinstance(result, dict) or result.get("initialized") is not True:
            raise DeepSeekBridgeError("DeepSeek SDK bridge initialization failed")
        self.initialized = True

    def _start_process(self) -> None:
        if self.process is not None:
            return
        if not self.bridge_path.is_file():
            raise FileNotFoundError(f"DeepSeek SDK bridge is missing: {self.bridge_path}")
        if not self.runtime_path.is_file():
            raise FileNotFoundError(f"DeepSeek runtime is missing: {self.runtime_path}")
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-c",
                _STOP_BEFORE_EXEC,
                "node",
                str(self.bridge_path),
            ],
            cwd=str(self.cwd),
            env=self.environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        self.process = process
        assert process.stderr is not None
        collector = _StderrCollector(
            process.stderr,
            max_bytes=self.max_output_bytes,
        )
        self.stderr_collector = collector
        collector.start()
        try:
            _wait_until_stopped(process)
            self.tracker = _DescendantTracker(
                process.pid,
                max_rss_bytes=self.max_rss_bytes,
            )
            if self.tracker.rss_measurement_failed:
                raise DeepSeekBridgeResourceError(
                    "DeepSeek Harness memory measurement failed",
                    error_type="resource_measurement_failure",
                )
            if self.tracker.rss_limit_exceeded:
                raise DeepSeekBridgeResourceError(
                    "DeepSeek Harness exceeded its memory limit before launch",
                    error_type="memory_limit_exceeded",
                )
            os.kill(process.pid, signal.SIGCONT)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            collector.finish()
            raise

    def _rpc(
        self,
        method: str,
        params: dict[str, Any],
        *,
        deadline: float | None,
    ) -> Any:
        process = self.process
        if process is None or process.stdin is None or process.stdout is None:
            raise DeepSeekBridgeError("DeepSeek SDK bridge is not running")
        self.request_id += 1
        request_id = self.request_id
        request = json.dumps(
            {"id": request_id, "method": method, "params": params},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        try:
            process.stdin.write(request)
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise DeepSeekBridgeError("DeepSeek SDK bridge input closed") from exc

        notifications: list[dict[str, Any]] = []
        while True:
            # A busy notification stream can leave complete lines buffered at
            # all times.  _read_line() returns buffered lines without polling,
            # so enforce the absolute turn deadline here as well; otherwise a
            # runtime that never sends the terminal RPC response can bypass
            # the configured timeout indefinitely.
            if deadline is not None and time.monotonic() >= deadline:
                self.abort()
                raise DeepSeekBridgeTimeout(
                    "DeepSeek Harness turn timed out",
                    notifications=notifications,
                )
            try:
                line = self._read_line(deadline)
            except TimeoutError as exc:
                self.abort()
                raise DeepSeekBridgeTimeout(
                    "DeepSeek Harness turn timed out",
                    notifications=notifications,
                ) from exc
            except DeepSeekBridgeResourceError as exc:
                self.abort()
                raise DeepSeekBridgeResourceError(
                    str(exc),
                    error_type=exc.error_type,
                    notifications=[*notifications, *exc.notifications],
                ) from exc
            except DeepSeekBridgeError as exc:
                self.abort()
                error_type = type(exc)
                raise error_type(
                    str(exc),
                    notifications=[*notifications, *exc.notifications],
                ) from exc
            try:
                response = json.loads(line)
            except json.JSONDecodeError as exc:
                self.abort()
                raise DeepSeekBridgeError(
                    "DeepSeek SDK bridge returned invalid JSON",
                    notifications=notifications,
                ) from exc
            response_id = response.get("id") if isinstance(response, dict) else None
            # A task-scoped native turn/end may let _rpc() return before the
            # SDK's redundant aggregate response is written.  The bridge is
            # reused for fixed-turn conversations, so discard that completed
            # request's trailing frames rather than mistaking them for the
            # next request's response.  Future and malformed ids remain hard
            # protocol errors.
            if type(response_id) is int and response_id < request_id:
                continue
            if not isinstance(response, dict) or response_id != request_id:
                self.abort()
                raise DeepSeekBridgeError(
                    "DeepSeek SDK bridge response id is invalid",
                    notifications=notifications,
                )
            if response.get("event") == "notification":
                notification = response.get("notification")
                if isinstance(notification, dict):
                    notifications.append(notification)
                    terminal = self._terminal_event_result(
                        method=method,
                        params=params,
                        notification=notification,
                        notifications=notifications,
                    )
                    if terminal is not None:
                        self.terminal_event_returned = True
                        return terminal
                continue
            if response.get("ok") is not True:
                error = response.get("error")
                message = (
                    error.get("message")
                    if isinstance(error, dict) and isinstance(error.get("message"), str)
                    else "DeepSeek SDK request failed"
                )
                raise DeepSeekBridgeError(message, notifications=notifications)
            result = response.get("result")
            if method == "run" and isinstance(result, dict):
                result = dict(result)
                if notifications:
                    result["notifications"] = notifications
            return result

    def _terminal_event_result(
        self,
        *,
        method: str,
        params: dict[str, Any],
        notification: dict[str, Any],
        notifications: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Finish a task-scoped run on its authoritative parent turn/end.

        Some DeepSeek subagent turns emit the complete native notification
        stream, including the parent ``turn/end``, but the SDK's aggregate
        ``run()`` promise remains pending.  The adapter consumes that same
        notification stream, so waiting for a redundant aggregate response
        loses an otherwise complete trace.  This fallback is private to
        lifecycle-probe tasks and never triggers on a child session end.
        """

        if not self.terminal_event_return or method != "run":
            return None
        envelope = notification.get("params")
        if not isinstance(envelope, dict):
            return None
        expected_session = params.get("sessionId")
        if envelope.get("sessionId") != expected_session:
            return None
        native = envelope.get("event")
        if not isinstance(native, dict) or native.get("type") != "turn/end":
            return None
        data = native.get("data") if isinstance(native.get("data"), dict) else {}
        reason = data.get("reason")
        finish_reason = reason if isinstance(reason, dict) else {"kind": "completed"}
        return {
            "sessionId": expected_session,
            "finalResponse": _last_root_assistant_text(
                notifications,
                session_id=str(expected_session or ""),
            ),
            "finishReason": finish_reason,
            "notifications": list(notifications),
            "completionSource": "native_parent_turn_end",
        }

    def _read_line(self, deadline: float | None) -> bytes:
        process = self.process
        if process is None or process.stdout is None:
            raise DeepSeekBridgeError("DeepSeek SDK bridge is not running")
        while True:
            newline = self.stdout_buffer.find(b"\n")
            if newline >= 0:
                self._raise_if_runtime_limit_failed()
                line = bytes(self.stdout_buffer[:newline])
                del self.stdout_buffer[: newline + 1]
                return line
            if process.poll() is not None:
                raise DeepSeekBridgeError(
                    f"DeepSeek SDK bridge exited with code {process.returncode}"
                )
            self._raise_if_runtime_limit_failed()
            timeout = None
            if deadline is not None:
                timeout = deadline - time.monotonic()
                if timeout <= 0:
                    raise TimeoutError("DeepSeek SDK bridge response timed out")
            poll_timeout = 0.05 if timeout is None else min(timeout, 0.05)
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                ready = selector.select(poll_timeout)
            if not ready:
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError("DeepSeek SDK bridge response timed out")
                continue
            chunk = os.read(process.stdout.fileno(), 64 * 1024)
            if not chunk:
                raise DeepSeekBridgeError("DeepSeek SDK bridge output closed")
            self.stdout_observed_bytes += len(chunk)
            self.stdout_buffer.extend(chunk)

    def _raise_if_runtime_limit_failed(self) -> None:
        collector = self.stderr_collector
        stderr_bytes = collector.observed_bytes if collector is not None else 0
        if self.stdout_observed_bytes + stderr_bytes > self.max_output_bytes:
            raise DeepSeekBridgeOutputLimit(
                "DeepSeek SDK output exceeded the configured byte limit"
            )
        tracker = self.tracker
        if tracker is None:
            return
        if tracker.rss_measurement_failed:
            raise DeepSeekBridgeResourceError(
                "DeepSeek Harness memory measurement failed",
                error_type="resource_measurement_failure",
            )
        if tracker.rss_limit_exceeded:
            raise DeepSeekBridgeResourceError(
                "DeepSeek Harness exceeded the configured memory limit",
                error_type="memory_limit_exceeded",
            )

    def _terminate_and_receipt(self) -> dict[str, object]:
        tracker = self.tracker
        process = self.process
        if tracker is None:
            receipt: dict[str, object] = {
                "schema_version": "harness-process-containment-v3",
                "valid": process is None,
                "descendant_cleanup_valid": process is None,
                "tracked_descendant_count": 0,
                "tracked_identity_sha256": None,
                "residual_process_count": 0,
                "tracking_failed": process is not None,
                "pipe_drain_failed": False,
                "peak_rss_bytes": None,
                "rss_sample_count": 0,
                "rss_measurement_failed": False,
                "rss_limit_bytes": None,
                "rss_limit_exceeded": False,
                "sandbox_tag_scan_failed": False,
                "output_bytes_captured": 0,
                "output_limit_bytes": self.max_output_bytes,
                "output_limit_exceeded": False,
                "lifecycle_shutdown_valid": True,
                "raw_pids_persisted": False,
                "raw_paths_persisted": False,
            }
        else:
            report = tracker.terminate_all()
            receipt = report.public_receipt()
        if process is not None:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                receipt["valid"] = False
                receipt["descendant_cleanup_valid"] = False
                receipt["tracking_failed"] = True
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
        collector = self.stderr_collector
        if collector is not None:
            collector.finish()
            receipt["pipe_drain_failed"] = collector.failed
            total_output = collector.observed_bytes + self.stdout_observed_bytes
            receipt["output_bytes_captured"] = min(
                total_output,
                self.max_output_bytes,
            )
            receipt["output_limit_bytes"] = self.max_output_bytes
            receipt["output_limit_exceeded"] = (
                total_output > self.max_output_bytes
            )
            if collector.failed or total_output > self.max_output_bytes:
                receipt["valid"] = False
        receipt.setdefault("lifecycle_shutdown_valid", True)
        self.receipt = receipt
        return receipt

    def _write_logs(self) -> None:
        self.output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        stderr = self.stderr_collector.text() if self.stderr_collector else ""
        for secret in sorted(
            (value for value in self.redact_values if value), key=len, reverse=True
        ):
            stderr = stderr.replace(secret, "<redacted>")
        (self.output_root / "harness.stdout.log").write_text("", encoding="utf-8")
        (self.output_root / "harness.stderr.log").write_text(stderr, encoding="utf-8")


BridgeFactory = Callable[..., DeepSeekSdkBridge]


class DeepSeekTraceRunner:
    """Trace runner for persistent, Docker-owned DeepSeek SDK sessions."""

    def __init__(
        self,
        *,
        output_root: Path,
        secret_values: tuple[str, ...] = (),
        bridge_factory: BridgeFactory = DeepSeekSdkBridge,
    ) -> None:
        self.output_root = output_root
        self.secret_values = secret_values
        self.bridge_factory = bridge_factory
        self.adapter = DeepSeekAdapter()
        self.bridge: DeepSeekSdkBridge | None = None
        self.launch_key: tuple[Any, ...] | None = None
        self.summaries: list[tuple[dict[str, Any], Path]] = []
        self.closed = False

    def run(self, config: RunConfig) -> dict[str, Any]:
        if self.closed:
            raise RuntimeError("DeepSeek trace runner is closed")
        if not config.input_query:
            raise ValueError("input query must not be empty")
        if config.harness.lower() not in {"deepseek", "deepseek-harness", "dsh"}:
            raise ValueError("DeepSeek trace runner received another harness")

        session_id = (
            config.extra_args.get("harnessbench-session-id")
            or config.session_id
            or f"harnessbench-{uuid.uuid4().hex}"
        )
        adapter = self.adapter
        run_id = f"run-{uuid.uuid4().hex}"
        run_dir = Path(config.output_dir) / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        events_path = run_dir / "events.json"
        summary_path = run_dir / "summary.json"
        last_message_path = run_dir / "last_message.txt"
        started_at = utc_now_iso()
        started = time.monotonic()
        command = adapter.build_command(
            model=config.model,
            input_query=config.input_query,
            run_dir=run_dir,
            harness_path=config.harness_path,
            extra_args={**config.extra_args, "harnessbench-session-id": session_id},
        )
        environment = dict(config.env)
        environment_summary = _environment_summary(
            environment,
            inherit_environment=not config.replace_environment,
            redact_paths=config.redact_paths,
        )
        writer = EventWriter(
            events_path,
            run_id=run_id,
            redact_values=config.redact_values,
            # DeepSeek emits token-granular reasoning notifications.  Keep
            # bounded on-disk checkpoints without rewriting the growing JSON
            # array for every delta; close() still flushes the complete trace.
            flush_interval=128,
        )
        status = "failed"
        trace_complete = False
        structured_status = "unavailable"
        error_type: str | None = None
        error_message: str | None = None
        timed_out = False
        exit_code: int | None = 0
        provider_cursor = provider_request_cursor(config.provider_request_events_path)
        try:
            writer.write(
                "run_started",
                harness="deepseek",
                model=config.model,
                input_query=config.input_query,
                session_id=session_id,
                cwd=str(config.cwd),
                argv=command,
                environment=environment_summary,
                isolation=_safe_isolation_summary(config.isolation),
                transport="official_sdk_jsonrpc",
            )
            bridge = self._bridge_for(config, environment)
            try:
                result_payload = bridge.run(
                    config.input_query,
                    session_id=session_id,
                    timeout=config.timeout,
                    wait_for_continuable_children=(
                        config.extra_args.get(
                            "harnessbench-deepseek-continuable-subagents"
                        )
                        == "true"
                    ),
                )
            except (DeepSeekBridgeTimeout, DeepSeekBridgeError) as exc:
                partial = {
                    "sessionId": session_id,
                    "finishReason": {
                        "kind": "aborted" if isinstance(exc, DeepSeekBridgeTimeout) else "error",
                        "error": {"message": str(exc)},
                    },
                    "notifications": exc.notifications,
                }
                adapter_result = adapter.consume_result(
                    partial,
                    writer=writer,
                    run_dir=run_dir,
                )
                status = "failed"
                trace_complete = False
                structured_status = adapter_result.structured_trace_status
                timed_out = isinstance(exc, DeepSeekBridgeTimeout)
                exit_code = 124 if timed_out else 1
                if timed_out:
                    error_type = "timeout"
                elif isinstance(exc, DeepSeekBridgeOutputLimit):
                    error_type = "output_limit_exceeded"
                elif isinstance(exc, DeepSeekBridgeResourceError):
                    error_type = exc.error_type
                else:
                    error_type = "deepseek_sdk_error"
                error_message = str(exc)
                _rewrite_failure_event(
                    writer,
                    error_type=error_type,
                    error_message=error_message,
                    exit_code=exit_code,
                    timed_out=timed_out,
                )
            else:
                adapter_result = adapter.consume_result(
                    result_payload,
                    writer=writer,
                    run_dir=run_dir,
                )
                status = adapter_result.status
                trace_complete = adapter_result.trace_complete
                structured_status = adapter_result.structured_trace_status
                error_type = adapter_result.error_type
                error_message = adapter_result.error_message

            _insert_provider_tool_inventories(
                writer,
                trace_tool_inventory_payloads(
                    config.provider_request_events_path,
                    after_seq=provider_cursor,
                ),
            )
            _redact_writer_events(writer, config.redact_paths)
            if status == "finished":
                writer.write(
                    "run_finished",
                    exit_code=exit_code,
                    summary_path=str(summary_path),
                    trace_complete=trace_complete,
                )
            elif not any(
                event.get("event_type") == "run_failed" for event in writer.events
            ):
                writer.write(
                    "run_failed",
                    error_type=error_type,
                    error_message=error_message,
                    exit_code=exit_code,
                    timed_out=timed_out,
                    events_written=writer.seq,
                )
        except Exception as exc:
            status = "failed"
            trace_complete = False
            structured_status = "unavailable"
            error_type = type(exc).__name__
            error_message = str(exc) or type(exc).__name__
            exit_code = 1
            if not any(
                event.get("event_type") == "run_failed" for event in writer.events
            ):
                writer.write(
                    "run_failed",
                    error_type=error_type,
                    error_message=error_message,
                    exit_code=exit_code,
                    timed_out=False,
                )
        finally:
            _redact_writer_events(writer, config.redact_paths)
            writer.close()

        summary = build_summary(
            writer=writer,
            run_id=run_id,
            run_dir=run_dir,
            harness="deepseek",
            model=config.model,
            input_query=config.input_query,
            status=status,
            trace_complete=trace_complete,
            structured_trace_status=structured_status,
            exit_code=exit_code,
            timed_out=timed_out,
            error_type=error_type,
            error_message=error_message,
            started_at=started_at,
            finished_at=utc_now_iso(),
            duration_ms=int((time.monotonic() - started) * 1000),
            session_id=session_id,
            cwd=str(config.cwd),
            argv=command,
            environment=environment_summary,
            summary_path=summary_path,
        )
        summary["isolation"] = _safe_isolation_summary(config.isolation)
        summary["process_containment"] = _active_containment_receipt()
        summary["resource_usage"] = _resource_usage(summary["process_containment"])
        write_summary(summary, summary_path)
        write_last_message(summary, last_message_path)
        self.summaries.append((summary, summary_path))
        return summary

    def close(self) -> dict[str, object]:
        if self.closed:
            return (
                self.bridge.receipt
                if self.bridge is not None and self.bridge.receipt is not None
                else _empty_containment_receipt()
            )
        self.closed = True
        if self.bridge is None:
            receipt = _empty_containment_receipt()
        else:
            receipt = self.bridge.close()
        for summary, path in self.summaries:
            _patch_summary_containment(summary, receipt)
            write_summary(summary, path)
        return receipt

    def _bridge_for(
        self, config: RunConfig, environment: dict[str, str]
    ) -> DeepSeekSdkBridge:
        max_output_bytes = config.max_output_bytes
        configured_limit = config.extra_args.get(
            "harnessbench-deepseek-max-output-bytes"
        )
        if configured_limit is not None:
            try:
                max_output_bytes = int(configured_limit)
            except ValueError as exc:
                raise ValueError(
                    "harnessbench-deepseek-max-output-bytes must be a positive integer"
                ) from exc
            if max_output_bytes <= 0:
                raise ValueError(
                    "harnessbench-deepseek-max-output-bytes must be a positive integer"
                )
        max_output_tokens = None
        configured_token_limit = config.extra_args.get(
            "harnessbench-deepseek-max-output-tokens"
        )
        if configured_token_limit is not None:
            try:
                max_output_tokens = int(configured_token_limit)
            except ValueError as exc:
                raise ValueError(
                    "harnessbench-deepseek-max-output-tokens must be a positive integer"
                ) from exc
            if max_output_tokens <= 0:
                raise ValueError(
                    "harnessbench-deepseek-max-output-tokens must be a positive integer"
                )
        launch_key = (
            config.model,
            str(config.cwd.resolve()),
            tuple(sorted(environment.items())),
            max_output_bytes,
            max_output_tokens,
            config.max_rss_bytes,
            config.extra_args.get("harnessbench-deepseek-terminal-event-return"),
        )
        if self.launch_key is not None and self.launch_key != launch_key:
            raise RuntimeError("DeepSeek conversation changed its runtime launch configuration")
        if self.bridge is None:
            self.launch_key = launch_key
            self.bridge = self.bridge_factory(
                environment=environment,
                cwd=config.cwd,
                model=config.model,
                output_root=self.output_root,
                redact_values=self.secret_values,
                max_output_bytes=max_output_bytes,
                max_output_tokens=max_output_tokens,
                max_rss_bytes=config.max_rss_bytes,
                terminal_event_return=(
                    config.extra_args.get(
                        "harnessbench-deepseek-terminal-event-return"
                    )
                    == "true"
                ),
            )
        return self.bridge


def _last_root_assistant_text(
    notifications: list[dict[str, Any]], *, session_id: str
) -> str | None:
    for notification in reversed(notifications):
        params = notification.get("params")
        if not isinstance(params, dict) or params.get("sessionId") != session_id:
            continue
        native = params.get("event")
        if not isinstance(native, dict) or native.get("type") != "assistant/message":
            continue
        data = native.get("data") if isinstance(native.get("data"), dict) else {}
        message = data.get("message") if isinstance(data.get("message"), dict) else {}
        content = message.get("content")
        if isinstance(content, str) and content:
            return content
        if not isinstance(content, list):
            continue
        texts = [
            str(block.get("text"))
            for block in content
            if isinstance(block, dict)
            and block.get("type") in {"text", "output_text"}
            and isinstance(block.get("text"), str)
            and block.get("text")
        ]
        if texts:
            return "\n".join(texts)
    return None


def patch_root_summary_containment(
    summary: dict[str, Any], receipt: dict[str, object]
) -> None:
    """Attach final persistent-runtime cleanup evidence to a root summary."""

    _patch_summary_containment(summary, receipt)
    path = Path(str(summary.get("summary_path") or ""))
    if path.is_file():
        write_summary(summary, path)


def _active_containment_receipt() -> dict[str, object]:
    return {
        "schema_version": "harness-process-containment-v3",
        "valid": None,
        "descendant_cleanup_valid": None,
        "lifecycle": "active_persistent_runtime",
        "raw_pids_persisted": False,
        "raw_paths_persisted": False,
    }


def _empty_containment_receipt() -> dict[str, object]:
    return {
        "schema_version": "harness-process-containment-v3",
        "valid": True,
        "descendant_cleanup_valid": True,
        "tracked_descendant_count": 0,
        "tracked_identity_sha256": None,
        "residual_process_count": 0,
        "tracking_failed": False,
        "pipe_drain_failed": False,
        "peak_rss_bytes": None,
        "rss_sample_count": 0,
        "rss_measurement_failed": False,
        "rss_limit_bytes": None,
        "rss_limit_exceeded": False,
        "sandbox_tag_scan_failed": False,
        "output_bytes_captured": 0,
        "output_limit_bytes": None,
        "output_limit_exceeded": False,
        "lifecycle_shutdown_valid": True,
        "raw_pids_persisted": False,
        "raw_paths_persisted": False,
    }


def _failed_containment_receipt() -> dict[str, object]:
    receipt = _empty_containment_receipt()
    receipt.update(
        {
            "valid": False,
            "descendant_cleanup_valid": False,
            "tracking_failed": True,
            "lifecycle_shutdown_valid": False,
        }
    )
    return receipt


def _resource_usage(receipt: dict[str, object]) -> dict[str, object]:
    return {
        "peak_rss_bytes": receipt.get("peak_rss_bytes"),
        "rss_limit_bytes": receipt.get("rss_limit_bytes"),
        "rss_limit_exceeded": receipt.get("rss_limit_exceeded", False),
        "output_bytes_captured": receipt.get("output_bytes_captured", 0),
        "output_limit_bytes": receipt.get("output_limit_bytes"),
        "output_limit_exceeded": receipt.get("output_limit_exceeded", False),
    }


def _patch_summary_containment(
    summary: dict[str, Any], receipt: dict[str, object]
) -> None:
    summary["process_containment"] = receipt
    summary["resource_usage"] = _resource_usage(receipt)
    failure = _containment_failure(receipt)
    if failure is None:
        return
    error_type, error_message = failure
    summary["status"] = "failed"
    summary["trace_complete"] = False
    summary["error_type"] = error_type
    summary["error_message"] = error_message


def _containment_failure(
    receipt: dict[str, object],
) -> tuple[str, str] | None:
    if receipt.get("lifecycle_shutdown_valid") is False:
        return (
            "deepseek_sdk_close",
            "DeepSeek SDK runtime did not complete its shutdown handshake",
        )
    if receipt.get("rss_measurement_failed") is True:
        return (
            "resource_measurement_failure",
            "DeepSeek Harness memory measurement failed",
        )
    if receipt.get("rss_limit_exceeded") is True:
        return (
            "memory_limit_exceeded",
            "DeepSeek Harness exceeded the configured memory limit",
        )
    if receipt.get("output_limit_exceeded") is True:
        return (
            "output_limit_exceeded",
            "DeepSeek Harness exceeded the configured output limit",
        )
    if receipt.get("valid") is not True:
        return (
            "process_containment_failure",
            "DeepSeek SDK process containment could not be verified",
        )
    return None


def _rewrite_failure_event(
    writer: EventWriter,
    *,
    error_type: str,
    error_message: str,
    exit_code: int,
    timed_out: bool,
) -> None:
    """Keep the normalized failure event aligned with runner-level failures.

    The adapter first records the native ``turn/end`` interpretation so partial
    native evidence is never lost.  Transport limits and wall-clock timeouts
    are runner facts, though, and must be the public failure seen in both the
    event stream and summary.
    """

    for event in reversed(writer.events):
        if event.get("event_type") != "run_failed":
            continue
        event.update(
            {
                "error_type": error_type,
                "error_message": error_message,
                "exit_code": exit_code,
                "timed_out": timed_out,
            }
        )
        return
