from __future__ import annotations

import hashlib
import json
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness_tracer.writer import EventWriter


class CodexAppServerError(RuntimeError):
    pass


class CodexAppServerTimeout(CodexAppServerError):
    pass


class CodexAppServerUnsupported(CodexAppServerError):
    pass


@dataclass(frozen=True)
class RpcReceipt:
    request_id: int
    result: Any


@dataclass(frozen=True)
class CodexStagedContextResult:
    session_id: str
    server_user_agent: str
    episode_receipts: list[dict[str, Any]]
    compactions: list[dict[str, Any]]
    final_prompt_event: str
    final_turn_id: str
    final_answer: str | None
    argv: list[str]
    final_tools_enabled: list[str] | None = None


class CodexAppServerClient:
    """Small newline-delimited JSON-RPC client for `codex app-server --stdio`."""

    def __init__(
        self,
        *,
        executable: str,
        cwd: Path,
        env: dict[str, str],
        request_timeout: float,
    ) -> None:
        self.argv = [executable, "app-server", "--stdio", "-c", "mcp_servers={}"]
        self.cwd = cwd
        self.env = env
        self.request_timeout = request_timeout
        self.process: subprocess.Popen[str] | None = None
        self.notifications: list[dict[str, Any]] = []
        self.stderr_lines: list[str] = []
        self._messages: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._pending_responses: dict[int, dict[str, Any]] = {}
        self._request_id = 0
        self._write_lock = threading.Lock()

    def __enter__(self) -> "CodexAppServerClient":
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def start(self) -> None:
        if self.process is not None:
            return
        self.process = subprocess.Popen(
            self.argv,
            cwd=self.cwd,
            env=self.env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert self.process.stdout is not None
        assert self.process.stderr is not None
        threading.Thread(
            target=self._read_stdout, args=(self.process.stdout,), daemon=True
        ).start()
        threading.Thread(
            target=self._read_stderr, args=(self.process.stderr,), daemon=True
        ).start()

    def close(self) -> None:
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()
        self.process = None

    def initialize(self) -> dict[str, Any]:
        receipt = self.request(
            "initialize",
            {
                "clientInfo": {"name": "harnessbench", "version": "0.1"},
                "capabilities": {"experimentalApi": True},
            },
        )
        self.notify("initialized")
        if not isinstance(receipt.result, dict):
            raise CodexAppServerError("initialize returned a non-object result")
        return receipt.result

    def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> RpcReceipt:
        self._request_id += 1
        request_id = self._request_id
        self._send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + (timeout or self.request_timeout)

        while True:
            pending = self._pending_responses.pop(request_id, None)
            if pending is not None:
                return self._response_receipt(method, request_id, pending)
            message = self._next_message(deadline, method)
            if message is None:
                raise CodexAppServerError(
                    f"codex app-server exited while waiting for {method}: {self.stderr_tail()}"
                )
            if message.get("id") == request_id and (
                "result" in message or "error" in message
            ):
                return self._response_receipt(method, request_id, message)
            self._route_unmatched(message)

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"method": method}
        if params is not None:
            payload["params"] = params
        self._send(payload)

    def notification_cursor(self) -> int:
        return len(self.notifications)

    def wait_for_notification(
        self,
        method: str,
        *,
        after: int,
        predicate: Any = None,
        timeout: float | None = None,
    ) -> tuple[int, dict[str, Any]]:
        deadline = time.monotonic() + (timeout or self.request_timeout)
        index = after
        while True:
            while index < len(self.notifications):
                message = self.notifications[index]
                current_index = index
                index += 1
                if message.get("method") != method:
                    continue
                if predicate is None or predicate(message.get("params") or {}):
                    return current_index, message
            message = self._next_message(deadline, method)
            if message is None:
                raise CodexAppServerError(
                    f"codex app-server exited while waiting for notification {method}: "
                    f"{self.stderr_tail()}"
                )
            self._route_unmatched(message)

    def stderr_tail(self, limit: int = 20) -> str:
        return "\n".join(self.stderr_lines[-limit:])[-4000:]

    def _response_receipt(
        self,
        method: str,
        request_id: int,
        message: dict[str, Any],
    ) -> RpcReceipt:
        if "error" in message:
            error = message["error"]
            if isinstance(error, dict) and error.get("code") in {-32601, -32600}:
                raise CodexAppServerUnsupported(f"{method} is unsupported: {error}")
            raise CodexAppServerError(f"{method} failed: {message['error']}")
        return RpcReceipt(request_id=request_id, result=message.get("result"))

    def _route_unmatched(self, message: dict[str, Any]) -> None:
        if "method" in message:
            if "id" in message:
                self._reply_unsupported_server_request(message)
            else:
                self.notifications.append(message)
            return
        response_id = message.get("id")
        if isinstance(response_id, int):
            self._pending_responses[response_id] = message

    def _reply_unsupported_server_request(self, message: dict[str, Any]) -> None:
        self._send(
            {
                "id": message.get("id"),
                "error": {
                    "code": -32601,
                    "message": f"HarnessBench does not service {message.get('method')}",
                },
            }
        )

    def _next_message(self, deadline: float, waiting_for: str) -> dict[str, Any] | None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CodexAppServerTimeout(f"timed out waiting for {waiting_for}")
        try:
            return self._messages.get(timeout=remaining)
        except queue.Empty as exc:
            raise CodexAppServerTimeout(f"timed out waiting for {waiting_for}") from exc

    def _send(self, payload: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None or process.poll() is not None:
            raise CodexAppServerError("codex app-server is not running")
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            process.stdin.write(serialized + "\n")
            process.stdin.flush()

    def _read_stdout(self, stream: Any) -> None:
        try:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    message = {
                        "method": "raw/stdout",
                        "params": {"text": line.rstrip()},
                    }
                self._messages.put(message)
        finally:
            self._messages.put(None)

    def _read_stderr(self, stream: Any) -> None:
        for line in stream:
            self.stderr_lines.append(line.rstrip())
            if len(self.stderr_lines) > 2000:
                del self.stderr_lines[:1000]


class CodexStagedContextDriver:
    adapter_name = "codex_app_server_staged_context_v1"

    def __init__(
        self,
        *,
        model: str,
        harness_path: str | None,
        cwd: Path,
        env: dict[str, str],
        timeout: int | None,
    ) -> None:
        self.model = model
        self.harness_path = harness_path or "codex"
        self.cwd = cwd
        self.env = {**os.environ, **env}
        self.timeout = float(timeout or 1800)

    def execute(
        self,
        *,
        episodes: list[dict[str, Any]],
        compaction_checkpoints: list[int],
        final_prompt: str,
        final_output_schema: dict[str, Any],
        writer: EventWriter,
    ) -> CodexStagedContextResult:
        deadline = time.monotonic() + self.timeout
        with CodexAppServerClient(
            executable=self.harness_path,
            cwd=self.cwd,
            env=self.env,
            request_timeout=min(300.0, self.timeout),
        ) as client:
            initialized = client.initialize()
            server_user_agent = str(initialized.get("userAgent") or "unknown")
            start = client.request(
                "thread/start",
                {
                    "cwd": str(self.cwd),
                    "model": self.model,
                    "approvalPolicy": "never",
                    "permissions": ":workspace",
                    "ephemeral": True,
                    "environments": [],
                    "runtimeWorkspaceRoots": [str(self.cwd)],
                    "developerInstructions": (
                        "This is a controlled HarnessBench run. Use only the supplied session "
                        "history and the mounted workspace. Do not access external services."
                    ),
                },
                timeout=self._remaining(deadline),
            )
            result = start.result if isinstance(start.result, dict) else {}
            thread = (
                result.get("thread") if isinstance(result.get("thread"), dict) else {}
            )
            session_id = str(thread.get("id") or "")
            if not session_id:
                raise CodexAppServerError("thread/start did not return a thread id")
            session_event = writer.write(
                "session_created",
                protocol="staged_context_v1",
                session_id=session_id,
                native_method="thread/start",
                native_request_id=start.request_id,
                server_user_agent=server_user_agent,
            )

            episode_receipts: list[dict[str, Any]] = []
            compactions: list[dict[str, Any]] = []
            checkpoints = set(compaction_checkpoints)
            for index, episode in enumerate(episodes, start=1):
                episode_id = str(episode.get("episode_id") or "")
                content = str(episode.get("content") or "")
                expected_ack = str(episode.get("expected_ack") or f"ACK {episode_id}")
                items = [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": content}],
                    },
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": expected_ack}],
                    },
                ]
                injected = client.request(
                    "thread/inject_items",
                    {"threadId": session_id, "items": items},
                    timeout=self._remaining(deadline),
                )
                event = writer.write(
                    "episode_injected",
                    session_id=session_id,
                    episode_sequence=index,
                    episode_id=episode_id,
                    content_sha256=_sha256_text(content),
                    acknowledgement_sha256=_sha256_text(expected_ack),
                    native_method="thread/inject_items",
                    native_request_id=injected.request_id,
                    tools_enabled=False,
                )
                episode_receipts.append(
                    {
                        "seq": index,
                        "episode_id": episode_id,
                        "thread_id": session_id,
                        "content_sha256": _sha256_text(content),
                        "acknowledgement_sha256": _sha256_text(expected_ack),
                        "native_method": "thread/inject_items",
                        "native_request_id": injected.request_id,
                        "source_event": f"trace:{event['seq']}",
                    }
                )
                if index in checkpoints:
                    compactions.append(
                        self._compact(
                            client=client,
                            session_id=session_id,
                            after_episode=index,
                            writer=writer,
                            timeout=self._remaining(deadline),
                        )
                    )

            final_cursor = client.notification_cursor()
            final_started = client.request(
                "turn/start",
                {
                    "threadId": session_id,
                    "input": [{"type": "text", "text": final_prompt}],
                    "cwd": str(self.cwd),
                    "model": self.model,
                    "approvalPolicy": "never",
                    "permissions": ":read-only",
                    "runtimeWorkspaceRoots": [str(self.cwd)],
                    "environments": [],
                    "outputSchema": final_output_schema,
                    "responsesapiClientMetadata": {
                        "benchmark_protocol": "staged_context_v1",
                        "benchmark_phase": "final_query",
                    },
                },
                timeout=self._remaining(deadline),
            )
            turn_result = (
                final_started.result if isinstance(final_started.result, dict) else {}
            )
            turn = (
                turn_result.get("turn")
                if isinstance(turn_result.get("turn"), dict)
                else {}
            )
            turn_id = str(turn.get("id") or "")
            if not turn_id:
                raise CodexAppServerError("turn/start did not return a turn id")
            _, completed = client.wait_for_notification(
                "turn/completed",
                after=final_cursor,
                predicate=lambda params: (
                    params.get("threadId") == session_id
                    and isinstance(params.get("turn"), dict)
                    and params["turn"].get("id") == turn_id
                ),
                timeout=self._remaining(deadline),
            )
            completed_turn = (completed.get("params") or {}).get("turn") or {}
            if completed_turn.get("status") not in {None, "completed"}:
                raise CodexAppServerError(
                    f"final turn ended with status {completed_turn.get('status')!r}"
                )
            tool_items = _final_tool_items(
                client.notifications, after=final_cursor, turn_id=turn_id
            )
            if tool_items:
                raise CodexAppServerError(
                    f"final turn used tools even though the protocol disables them: {tool_items}"
                )
            final_answer = _last_agent_message(
                client.notifications, after=final_cursor, turn_id=turn_id
            )
            final_event = writer.write(
                "final_prompt_completed",
                session_id=session_id,
                turn_id=turn_id,
                prompt_sha256=_sha256_text(final_prompt),
                native_method="turn/start",
                native_request_id=final_started.request_id,
            )
            if final_answer:
                writer.write(
                    "assistant_message",
                    session_id=session_id,
                    turn_id=turn_id,
                    content=final_answer,
                )
            writer.write(
                "app_server_notifications",
                count=len(client.notifications),
                methods=_notification_method_counts(client.notifications),
                session_event=f"trace:{session_event['seq']}",
            )
            return CodexStagedContextResult(
                session_id=session_id,
                server_user_agent=server_user_agent,
                episode_receipts=episode_receipts,
                compactions=compactions,
                final_prompt_event=f"trace:{final_event['seq']}",
                final_turn_id=turn_id,
                final_answer=final_answer,
                argv=client.argv,
            )

    def _compact(
        self,
        *,
        client: CodexAppServerClient,
        session_id: str,
        after_episode: int,
        writer: EventWriter,
        timeout: float,
    ) -> dict[str, Any]:
        cursor = client.notification_cursor()
        receipt = client.request(
            "thread/compact/start",
            {"threadId": session_id},
            timeout=timeout,
        )
        _, notification = client.wait_for_notification(
            "thread/compacted",
            after=cursor,
            predicate=lambda params: params.get("threadId") == session_id,
            timeout=timeout,
        )
        params = notification.get("params") or {}
        event = writer.write(
            "context_compaction",
            session_id=session_id,
            after_episode=after_episode,
            native=True,
            observed=True,
            native_method="thread/compact/start",
            native_request_id=receipt.request_id,
            native_notification="thread/compacted",
            compaction_turn_id=params.get("turnId"),
        )
        return {
            "after_episode": after_episode,
            "observed": True,
            "native": True,
            "thread_id": session_id,
            "turn_id": params.get("turnId"),
            "native_method": "thread/compact/start",
            "native_request_id": receipt.request_id,
            "native_notification": "thread/compacted",
            "source_event": f"trace:{event['seq']}",
        }

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CodexAppServerTimeout(
                "staged context protocol exceeded its wall-time budget"
            )
        return remaining


class CodexFreshSessionsDriver:
    """Run each private episode and the final query in distinct native threads."""

    adapter_name = "codex_app_server_fresh_sessions_v1"

    def __init__(
        self,
        *,
        model: str,
        harness_path: str | None,
        cwd: Path,
        env: dict[str, str],
        timeout: int | None,
        final_tools_enabled: list[str],
    ) -> None:
        self.model = model
        self.harness_path = harness_path or "codex"
        self.cwd = cwd
        self.env = {**os.environ, **env}
        self.timeout = float(timeout or 1800)
        self.final_tools_enabled = final_tools_enabled

    def execute(
        self,
        *,
        episodes: list[dict[str, Any]],
        compaction_checkpoints: list[int],
        final_prompt: str,
        final_output_schema: dict[str, Any],
        writer: EventWriter,
    ) -> CodexStagedContextResult:
        if compaction_checkpoints:
            raise CodexAppServerError(
                "fresh_sessions_v1 does not accept compaction checkpoints"
            )
        deadline = time.monotonic() + self.timeout
        memory_root = self.cwd / "workspace/.harness-memory"
        if memory_root.exists():
            raise CodexAppServerError(
                "fresh-session durable memory exists before the first session"
            )

        with CodexAppServerClient(
            executable=self.harness_path,
            cwd=self.cwd,
            env=self.env,
            request_timeout=min(300.0, self.timeout),
        ) as client:
            initialized = client.initialize()
            server_user_agent = str(initialized.get("userAgent") or "unknown")
            episode_receipts: list[dict[str, Any]] = []

            for index, episode in enumerate(episodes, start=1):
                episode_id = str(episode.get("episode_id") or "")
                content = str(episode.get("content") or "")
                expected_ack = str(
                    episode.get("expected_ack") or f"ACK {episode_id}"
                )
                thread_id, start_request_id = self._start_thread(
                    client=client,
                    deadline=deadline,
                    phase=f"episode_{index}",
                    permissions=":workspace",
                    developer_instructions=(
                        "This is one isolated HarnessBench memory-lifecycle session. "
                        "Use only this event and durable files below "
                        "workspace/.harness-memory. Do not inspect benchmark-private "
                        "inputs or external services. Persist the event before acknowledging."
                    ),
                )
                session_event = writer.write(
                    "session_created",
                    protocol="fresh_sessions_v1",
                    session_id=thread_id,
                    episode_sequence=index,
                    episode_id=episode_id,
                    native_method="thread/start",
                    native_request_id=start_request_id,
                    server_user_agent=server_user_agent,
                )
                turn_id, turn_request_id, answer, tool_items = self._run_turn(
                    client=client,
                    thread_id=thread_id,
                    text=content,
                    deadline=deadline,
                    permissions=":workspace",
                    output_schema=None,
                    phase=f"episode_{index}",
                )
                if expected_ack not in (answer or ""):
                    raise CodexAppServerError(
                        f"fresh session {episode_id} did not return its acknowledgement"
                    )
                memory_sha, memory_files = _tree_digest(memory_root)
                if memory_sha is None or not memory_files:
                    raise CodexAppServerError(
                        f"fresh session {episode_id} did not persist durable memory"
                    )
                event = writer.write(
                    "fresh_session_completed",
                    session_id=thread_id,
                    turn_id=turn_id,
                    episode_sequence=index,
                    episode_id=episode_id,
                    content_sha256=_sha256_text(content),
                    acknowledgement_sha256=_sha256_text(expected_ack),
                    durable_memory_sha256=memory_sha,
                    durable_memory_files=memory_files,
                    native_method="turn/start",
                    native_request_id=turn_request_id,
                    session_event=f"trace:{session_event['seq']}",
                    tool_item_types=tool_items,
                )
                episode_receipts.append(
                    {
                        "seq": index,
                        "episode_id": episode_id,
                        "thread_id": thread_id,
                        "turn_id": turn_id,
                        "content_sha256": _sha256_text(content),
                        "acknowledgement_sha256": _sha256_text(expected_ack),
                        "durable_memory_sha256": memory_sha,
                        "durable_memory_files": memory_files,
                        "native_method": "thread/start+turn/start",
                        "native_thread_request_id": start_request_id,
                        "native_turn_request_id": turn_request_id,
                        "source_event": f"trace:{event['seq']}",
                    }
                )

            final_workspace_write = self.final_tools_enabled == [
                "workspace_without_memory_mutation"
            ]
            final_permissions = ":workspace" if final_workspace_write else ":read-only"
            final_thread_id, final_start_request_id = self._start_thread(
                client=client,
                deadline=deadline,
                phase="final_query",
                permissions=final_permissions,
                developer_instructions=(
                    "This is a new, isolated final HarnessBench session. Recover the "
                    "answer only from the task prompt and durable state below "
                    "workspace/.harness-memory. "
                    + (
                        "You may update assigned workspace deliverables, but must not "
                        "change durable memory. "
                        if final_workspace_write else
                        "Use read-only workspace tools and do not change memory. "
                    )
                    + "Return only JSON matching the output schema."
                ),
            )
            final_session_event = writer.write(
                "session_created",
                protocol="fresh_sessions_v1",
                session_id=final_thread_id,
                episode_sequence=None,
                episode_id=None,
                native_method="thread/start",
                native_request_id=final_start_request_id,
                server_user_agent=server_user_agent,
            )
            before_final_sha, before_final_files = _tree_digest(memory_root)
            turn_id, turn_request_id, final_answer, final_tool_items = self._run_turn(
                client=client,
                thread_id=final_thread_id,
                text=final_prompt,
                deadline=deadline,
                permissions=final_permissions,
                output_schema=final_output_schema,
                phase="final_query",
            )
            after_final_sha, after_final_files = _tree_digest(memory_root)
            if (
                before_final_sha is None
                or before_final_sha != after_final_sha
                or before_final_files != after_final_files
            ):
                raise CodexAppServerError(
                    "final read-only session changed durable memory"
                )
            final_event = writer.write(
                "final_prompt_completed",
                session_id=final_thread_id,
                turn_id=turn_id,
                prompt_sha256=_sha256_text(final_prompt),
                durable_memory_sha256=after_final_sha,
                durable_memory_files=after_final_files,
                native_method="turn/start",
                native_request_id=turn_request_id,
                session_event=f"trace:{final_session_event['seq']}",
                tool_item_types=final_tool_items,
            )
            if final_answer:
                writer.write(
                    "assistant_message",
                    session_id=final_thread_id,
                    turn_id=turn_id,
                    content=final_answer,
                )
            writer.write(
                "app_server_notifications",
                count=len(client.notifications),
                methods=_notification_method_counts(client.notifications),
            )
            return CodexStagedContextResult(
                session_id=final_thread_id,
                server_user_agent=server_user_agent,
                episode_receipts=episode_receipts,
                compactions=[],
                final_prompt_event=f"trace:{final_event['seq']}",
                final_turn_id=turn_id,
                final_answer=final_answer,
                argv=client.argv,
                final_tools_enabled=self.final_tools_enabled,
            )

    def _start_thread(
        self,
        *,
        client: CodexAppServerClient,
        deadline: float,
        phase: str,
        permissions: str,
        developer_instructions: str,
        workdir: Path | None = None,
    ) -> tuple[str, int]:
        active_cwd = workdir or self.cwd
        receipt = client.request(
            "thread/start",
            {
                "cwd": str(active_cwd),
                "model": self.model,
                "approvalPolicy": "never",
                "permissions": permissions,
                "ephemeral": True,
                "environments": [],
                "runtimeWorkspaceRoots": [str(active_cwd)],
                "developerInstructions": developer_instructions,
            },
            timeout=self._remaining(deadline),
        )
        result = receipt.result if isinstance(receipt.result, dict) else {}
        thread = result.get("thread") if isinstance(result.get("thread"), dict) else {}
        thread_id = str(thread.get("id") or "")
        if not thread_id:
            raise CodexAppServerError("thread/start did not return a thread id")
        return thread_id, receipt.request_id

    def _run_turn(
        self,
        *,
        client: CodexAppServerClient,
        thread_id: str,
        text: str,
        deadline: float,
        permissions: str,
        output_schema: dict[str, Any] | None,
        phase: str,
        workdir: Path | None = None,
        protocol_version: str = "fresh_sessions_v1",
    ) -> tuple[str, int, str | None, list[str]]:
        active_cwd = workdir or self.cwd
        cursor = client.notification_cursor()
        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": text}],
            "cwd": str(active_cwd),
            "model": self.model,
            "approvalPolicy": "never",
            "permissions": permissions,
            "runtimeWorkspaceRoots": [str(active_cwd)],
            "environments": [],
            "responsesapiClientMetadata": {
                "benchmark_protocol": protocol_version,
                "benchmark_phase": phase,
            },
        }
        if output_schema is not None:
            params["outputSchema"] = output_schema
        receipt = client.request(
            "turn/start", params, timeout=self._remaining(deadline)
        )
        result = receipt.result if isinstance(receipt.result, dict) else {}
        turn = result.get("turn") if isinstance(result.get("turn"), dict) else {}
        turn_id = str(turn.get("id") or "")
        if not turn_id:
            raise CodexAppServerError("turn/start did not return a turn id")
        _, completed = client.wait_for_notification(
            "turn/completed",
            after=cursor,
            predicate=lambda value: (
                value.get("threadId") == thread_id
                and isinstance(value.get("turn"), dict)
                and value["turn"].get("id") == turn_id
            ),
            timeout=self._remaining(deadline),
        )
        completed_turn = (completed.get("params") or {}).get("turn") or {}
        if completed_turn.get("status") not in {None, "completed"}:
            raise CodexAppServerError(
                f"turn ended with status {completed_turn.get('status')!r}"
            )
        return (
            turn_id,
            receipt.request_id,
            _last_agent_message(
                client.notifications, after=cursor, turn_id=turn_id
            ),
            _final_tool_items(
                client.notifications, after=cursor, turn_id=turn_id
            ),
        )

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CodexAppServerTimeout(
                "fresh sessions protocol exceeded its wall-time budget"
            )
        return remaining


class CodexDelegatedRolesDriver(CodexFreshSessionsDriver):
    """Run runner-provisioned role views in isolated native threads, then join."""

    adapter_name = "codex_app_server_delegated_roles_v1"

    def __init__(
        self,
        *,
        model: str,
        harness_path: str | None,
        cwd: Path,
        role_root: Path,
        env: dict[str, str],
        timeout: int | None,
        final_tools_enabled: list[str],
    ) -> None:
        super().__init__(
            model=model,
            harness_path=harness_path,
            cwd=cwd,
            env=env,
            timeout=timeout,
            final_tools_enabled=final_tools_enabled,
        )
        self.role_root = role_root

    def execute(
        self,
        *,
        episodes: list[dict[str, Any]],
        compaction_checkpoints: list[int],
        final_prompt: str,
        final_output_schema: dict[str, Any],
        writer: EventWriter,
    ) -> CodexStagedContextResult:
        if compaction_checkpoints:
            raise CodexAppServerError(
                "delegated_roles_v1 does not accept compaction checkpoints"
            )
        if self.role_root.exists():
            raise CodexAppServerError(
                "delegated role root exists before runner provisioning"
            )
        self.role_root.mkdir(parents=True)
        collected_root = self.cwd / "workspace/collected"
        if collected_root.exists():
            raise CodexAppServerError(
                "collected role outputs exist before delegation"
            )
        collected_root.mkdir(parents=True)
        deadline = time.monotonic() + self.timeout

        with CodexAppServerClient(
            executable=self.harness_path,
            cwd=self.cwd,
            env=self.env,
            request_timeout=min(300.0, self.timeout),
        ) as client:
            initialized = client.initialize()
            server_user_agent = str(initialized.get("userAgent") or "unknown")
            episode_receipts: list[dict[str, Any]] = []

            for index, episode in enumerate(episodes, start=1):
                episode_id = str(episode.get("episode_id") or "")
                role_id = str(episode.get("role_id") or "")
                content = str(episode.get("content") or "")
                input_files = episode.get("input_files")
                output_schema = episode.get("output_schema")
                if (
                    not role_id
                    or not isinstance(input_files, dict)
                    or not isinstance(output_schema, dict)
                ):
                    raise CodexAppServerError(
                        f"delegated episode {episode_id} lacks a role contract"
                    )
                role_dir = self.role_root / role_id
                role_dir.mkdir()
                for relative, value in input_files.items():
                    if not isinstance(relative, str) or not isinstance(value, str):
                        raise CodexAppServerError("role input files must map strings")
                    target = (role_dir / relative).resolve()
                    try:
                        target.relative_to(role_dir.resolve())
                    except ValueError as exc:
                        raise CodexAppServerError(
                            f"role input escapes isolated root: {relative}"
                        ) from exc
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(value, encoding="utf-8")
                view_sha, view_files = _tree_digest(role_dir)
                if view_sha is None or not view_files:
                    raise CodexAppServerError(
                        f"delegated role {role_id} has an empty view"
                    )
                thread_id, start_request_id = self._start_thread(
                    client=client,
                    deadline=deadline,
                    phase=f"role_{role_id}",
                    permissions=":read-only",
                    workdir=role_dir,
                    developer_instructions=(
                        f"You are the isolated HarnessBench role {role_id}. Use only "
                        "the mounted role view. You cannot inspect the parent, another "
                        "role, benchmark-private inputs, or external services. Return "
                        "only JSON matching the supplied schema."
                    ),
                )
                session_event = writer.write(
                    "delegated_child_created",
                    protocol="delegated_roles_v1",
                    parent_id="runner-parent",
                    child_id=thread_id,
                    role_id=role_id,
                    episode_id=episode_id,
                    role_view_sha256=view_sha,
                    role_view_files=view_files,
                    native_method="thread/start",
                    native_request_id=start_request_id,
                    server_user_agent=server_user_agent,
                )
                turn_id, turn_request_id, answer, tool_items = self._run_turn(
                    client=client,
                    thread_id=thread_id,
                    text=content,
                    deadline=deadline,
                    permissions=":read-only",
                    output_schema=output_schema,
                    phase=f"role_{role_id}",
                    workdir=role_dir,
                    protocol_version="delegated_roles_v1",
                )
                payload = _parse_json_object(answer, f"delegated role {role_id}")
                serialized = (
                    json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n"
                )
                handoff = collected_root / f"{role_id}.json"
                handoff.write_text(serialized, encoding="utf-8")
                result_sha = _sha256_text(serialized)
                event = writer.write(
                    "delegated_child_completed",
                    protocol="delegated_roles_v1",
                    parent_id="runner-parent",
                    child_id=thread_id,
                    turn_id=turn_id,
                    role_id=role_id,
                    episode_id=episode_id,
                    role_view_sha256=view_sha,
                    result_sha256=result_sha,
                    handoff_path=str(handoff.relative_to(self.cwd)),
                    native_method="turn/start",
                    native_request_id=turn_request_id,
                    session_event=f"trace:{session_event['seq']}",
                    tool_item_types=tool_items,
                )
                episode_receipts.append(
                    {
                        "seq": index,
                        "episode_id": episode_id,
                        "role_id": role_id,
                        "parent_id": "runner-parent",
                        "thread_id": thread_id,
                        "turn_id": turn_id,
                        "content_sha256": _sha256_text(content),
                        "role_view_sha256": view_sha,
                        "role_view_files": view_files,
                        "result_sha256": result_sha,
                        "handoff_path": str(handoff.relative_to(self.cwd)),
                        "native_method": "thread/start+turn/start",
                        "native_thread_request_id": start_request_id,
                        "native_turn_request_id": turn_request_id,
                        "source_event": f"trace:{event['seq']}",
                    }
                )

            parent_workspace_write = self.final_tools_enabled == [
                "workspace_after_join"
            ]
            parent_permissions = (
                ":workspace" if parent_workspace_write else ":read-only"
            )
            final_thread_id, final_start_request_id = self._start_thread(
                client=client,
                deadline=deadline,
                phase="parent_join",
                permissions=parent_permissions,
                developer_instructions=(
                    "You are the parent integration role in an isolated HarnessBench "
                    "join. Use only the task prompt and runner-collected handoffs under "
                    "workspace/collected. Do not inspect private role workspaces. "
                    + (
                        "You may update the assigned workspace deliverables. "
                        if parent_workspace_write else
                        "Keep the workspace read-only. "
                    )
                    + "Return only JSON matching the output schema."
                ),
            )
            parent_event = writer.write(
                "delegated_parent_created",
                protocol="delegated_roles_v1",
                parent_id=final_thread_id,
                child_ids=[
                    item["thread_id"] for item in episode_receipts
                ],
                native_method="thread/start",
                native_request_id=final_start_request_id,
                server_user_agent=server_user_agent,
            )
            turn_id, turn_request_id, final_answer, final_tool_items = self._run_turn(
                client=client,
                thread_id=final_thread_id,
                text=final_prompt,
                deadline=deadline,
                permissions=parent_permissions,
                output_schema=final_output_schema,
                phase="parent_join",
                protocol_version="delegated_roles_v1",
            )
            final_event = writer.write(
                "final_prompt_completed",
                session_id=final_thread_id,
                turn_id=turn_id,
                prompt_sha256=_sha256_text(final_prompt),
                native_method="turn/start",
                native_request_id=turn_request_id,
                session_event=f"trace:{parent_event['seq']}",
                tool_item_types=final_tool_items,
            )
            if final_answer:
                writer.write(
                    "assistant_message",
                    session_id=final_thread_id,
                    turn_id=turn_id,
                    content=final_answer,
                )
            writer.write(
                "app_server_notifications",
                count=len(client.notifications),
                methods=_notification_method_counts(client.notifications),
            )
            return CodexStagedContextResult(
                session_id=final_thread_id,
                server_user_agent=server_user_agent,
                episode_receipts=episode_receipts,
                compactions=[],
                final_prompt_event=f"trace:{final_event['seq']}",
                final_turn_id=turn_id,
                final_answer=final_answer,
                argv=client.argv,
                final_tools_enabled=self.final_tools_enabled,
            )


def _tree_digest(root: Path) -> tuple[str | None, list[str]]:
    if not root.is_dir():
        return None, []
    digest = hashlib.sha256()
    files: list[str] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = str(path.relative_to(root))
        files.append(relative)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return (digest.hexdigest(), files) if files else (None, [])


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _parse_json_object(value: str | None, label: str) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        raise CodexAppServerError(f"{label} returned no structured output")
    text = value.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:] if lines and lines[0].startswith("```") else lines
        lines = lines[:-1] if lines and lines[-1].strip() == "```" else lines
        text = "\n".join(lines).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CodexAppServerError(f"{label} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise CodexAppServerError(f"{label} returned a non-object")
    return payload


def _last_agent_message(
    notifications: list[dict[str, Any]],
    *,
    after: int,
    turn_id: str,
) -> str | None:
    for notification in reversed(notifications[after:]):
        if notification.get("method") != "item/completed":
            continue
        params = notification.get("params") or {}
        if params.get("turnId") != turn_id:
            continue
        item = params.get("item") if isinstance(params.get("item"), dict) else {}
        if item.get("type") != "agentMessage":
            continue
        text = item.get("text") or item.get("content")
        if isinstance(text, str):
            return text
    return None


def _final_tool_items(
    notifications: list[dict[str, Any]],
    *,
    after: int,
    turn_id: str,
) -> list[str]:
    disallowed = {
        "commandExecution",
        "fileChange",
        "mcpToolCall",
        "dynamicToolCall",
        "collabAgentToolCall",
        "subAgentActivity",
        "webSearch",
        "imageView",
        "imageGeneration",
    }
    found: list[str] = []
    for notification in notifications[after:]:
        if notification.get("method") not in {"item/started", "item/completed"}:
            continue
        params = notification.get("params") or {}
        if params.get("turnId") != turn_id:
            continue
        item = params.get("item") if isinstance(params.get("item"), dict) else {}
        item_type = item.get("type")
        if item_type in disallowed and item_type not in found:
            found.append(item_type)
    return found


def _notification_method_counts(notifications: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in notifications:
        method = str(item.get("method") or "unknown")
        counts[method] = counts.get(method, 0) + 1
    return counts
