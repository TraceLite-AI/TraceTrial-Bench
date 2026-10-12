from __future__ import annotations

import http.client
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit


MODEL_CALL_BUDGET_SCHEMA = "harnessbench_model_call_budget_v1"
MODEL_CALL_BUDGET_VERSION = "provider_requests_v1"
MODEL_CALL_BUDGET_HOST = "127.0.0.1"
MODEL_CALL_BUDGET_PORT = 4001
MODEL_CALL_BUDGET_STATE_ENV = "HARNESSBENCH_MODEL_CALL_BUDGET_STATE"
MAX_PROXY_REQUEST_BYTES = 64 * 1024 * 1024
_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def validate_model_call_budget(value: Mapping[str, object]) -> dict[str, object]:
    if set(value) != {"version", "limit", "finalization_reserve"}:
        raise ValueError("model-call budget has invalid fields")
    if value.get("version") != MODEL_CALL_BUDGET_VERSION:
        raise ValueError("model-call budget has an unsupported version")
    limit = value.get("limit")
    reserve = value.get("finalization_reserve")
    if type(limit) is not int or limit < 2:
        raise ValueError("model-call budget limit must be an integer of at least 2")
    if type(reserve) is not int or reserve < 1 or reserve >= limit:
        raise ValueError("model-call budget finalization reserve is invalid")
    return {
        "version": MODEL_CALL_BUDGET_VERSION,
        "limit": limit,
        "finalization_reserve": reserve,
    }


@dataclass(frozen=True)
class ModelCallBudgetInfo:
    base_url: str
    state_path: Path
    events_path: Path

    def environment(self) -> dict[str, str]:
        return {MODEL_CALL_BUDGET_STATE_ENV: str(self.state_path)}


class ModelCallBudgetProxy:
    """Count and cap provider-bound HTTP requests for an opt-in task.

    The proxy is intentionally protocol-agnostic: OpenAI Chat Completions,
    Responses, and Anthropic Messages requests all pass through the same
    request boundary before reaching either the original OpenAI-compatible
    provider or the local LiteLLM compatibility gateway.
    """

    def __init__(
        self,
        *,
        budget: Mapping[str, object],
        output_root: Path,
        task_tools: Mapping[str, object] | None = None,
        upstream_host: str = "127.0.0.1",
        upstream_port: int = 4000,
        upstream_base_url: str | None = None,
        upstream_api_key: str | None = None,
        listen_host: str = MODEL_CALL_BUDGET_HOST,
        listen_port: int = MODEL_CALL_BUDGET_PORT,
    ) -> None:
        self.budget = validate_model_call_budget(budget)
        self.task_tool_names = _task_tool_names(task_tools)
        self.output_root = Path(output_root) / "model_call_budget"
        if upstream_base_url is None:
            self.upstream_scheme = "http"
            self.upstream_host = upstream_host
            self.upstream_port = upstream_port
        else:
            parsed = urlsplit(upstream_base_url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("model-call budget upstream URL is invalid")
            self.upstream_scheme = parsed.scheme
            self.upstream_host = parsed.hostname
            self.upstream_port = parsed.port or (
                443 if parsed.scheme == "https" else 80
            )
        self.upstream_api_key = upstream_api_key
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.state_path = self.output_root / "state.json"
        self.events_path = self.output_root / "events.jsonl"
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._started = 0
        self._rejected = 0
        self._event_seq = 0

    def start(self) -> ModelCallBudgetInfo:
        if self._server is not None or self._thread is not None:
            raise RuntimeError("model-call budget proxy is already started")
        self.output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.events_path.write_text("", encoding="utf-8")
        self.events_path.chmod(0o600)
        self._write_state()
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_GET(self) -> None:  # noqa: N802
                proxy._forward(self, counted=False)

            def do_HEAD(self) -> None:  # noqa: N802
                proxy._forward(self, counted=False)

            def do_OPTIONS(self) -> None:  # noqa: N802
                proxy._forward(self, counted=False)

            def do_POST(self) -> None:  # noqa: N802
                proxy._forward(self, counted=True)

            def log_message(self, format: str, *args: object) -> None:
                del format, args

        self._server = ThreadingHTTPServer(
            (self.listen_host, self.listen_port), Handler
        )
        self.listen_port = int(self._server.server_address[1])
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="harnessbench-model-call-budget",
            daemon=True,
        )
        self._thread.start()
        return ModelCallBudgetInfo(
            base_url=f"http://{self.listen_host}:{self.listen_port}",
            state_path=self.state_path,
            events_path=self.events_path,
        )

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5.0)
            if thread.is_alive():
                raise RuntimeError("model-call budget proxy did not stop")

    def public_summary(self) -> dict[str, object]:
        state = self.read_state()
        return {
            "schema_version": MODEL_CALL_BUDGET_SCHEMA,
            "version": MODEL_CALL_BUDGET_VERSION,
            "limit": state["limit"],
            "finalization_reserve": state["finalization_reserve"],
            "requests_used": state["requests_used"],
            "requests_remaining": state["requests_remaining"],
            "rejected_after_limit": state["rejected_after_limit"],
            "state_path": "model_call_budget/state.json",
            "events_path": "model_call_budget/events.jsonl",
        }

    def read_state(self) -> dict[str, object]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("model-call budget state is unreadable") from exc
        if not isinstance(value, dict):
            raise RuntimeError("model-call budget state is invalid")
        return value

    def _admit(
        self, path: str, request_body: bytes | None
    ) -> tuple[bool, dict[str, object]]:
        with self._lock:
            limit = int(self.budget["limit"])
            if self._started >= limit:
                self._rejected += 1
                admitted = False
            else:
                self._started += 1
                admitted = True
            self._event_seq += 1
            event = {
                "schema_version": MODEL_CALL_BUDGET_SCHEMA,
                "seq": self._event_seq,
                "timestamp": _utc_now(),
                "method": "POST",
                "path": path.split("?", 1)[0],
                "admitted": admitted,
                "requests_used": self._started,
                "requests_remaining": max(0, limit - self._started),
                "rejected_after_limit": self._rejected,
            }
            if self.task_tool_names:
                event["provider_tool_snapshot"] = _provider_tool_snapshot(
                    request_body, self.task_tool_names
                )
            with self.events_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
                stream.write("\n")
            self._write_state()
            return admitted, event

    def _write_state(self) -> None:
        limit = int(self.budget["limit"])
        reserve = int(self.budget["finalization_reserve"])
        remaining = max(0, limit - self._started)
        state = {
            "schema_version": MODEL_CALL_BUDGET_SCHEMA,
            "version": MODEL_CALL_BUDGET_VERSION,
            "limit": limit,
            "finalization_reserve": reserve,
            "requests_used": self._started,
            "requests_remaining": remaining,
            "rejected_after_limit": self._rejected,
            "finalization_window_open": remaining <= reserve,
            "exhausted": remaining == 0,
            "updated_at": _utc_now(),
        }
        temporary = self.state_path.with_name(".state.json.tmp")
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        os.replace(temporary, self.state_path)

    def _forward(self, handler: BaseHTTPRequestHandler, *, counted: bool) -> None:
        content_length = handler.headers.get("Content-Length")
        try:
            body_length = int(content_length) if content_length else 0
        except ValueError:
            self._write_json_error(handler, 400, "invalid Content-Length")
            return
        if body_length < 0 or body_length > MAX_PROXY_REQUEST_BYTES:
            self._write_json_error(handler, 413, "request body is too large")
            return
        body = handler.rfile.read(body_length) if body_length else None
        if counted:
            admitted, event = self._admit(handler.path, body)
            if not admitted:
                self._write_exhausted(handler, event)
                return
        headers = {
            name: value
            for name, value in handler.headers.items()
            if name.lower() not in _HOP_BY_HOP_HEADERS
            and name.lower() not in {"host", "content-length"}
            and not (
                self.upstream_api_key is not None
                and name.lower() in {"authorization", "x-api-key"}
            )
        }
        if self.upstream_api_key is not None:
            headers["Authorization"] = f"Bearer {self.upstream_api_key}"
        if body is not None:
            headers["Content-Length"] = str(len(body))
        connection_class = (
            http.client.HTTPSConnection
            if self.upstream_scheme == "https"
            else http.client.HTTPConnection
        )
        connection = connection_class(
            self.upstream_host,
            self.upstream_port,
            timeout=3600,
        )
        try:
            connection.request(
                handler.command,
                handler.path,
                body=body,
                headers=headers,
            )
            response = connection.getresponse()
            handler.send_response(response.status, response.reason)
            for name, value in response.getheaders():
                if name.lower() not in _HOP_BY_HOP_HEADERS | {"content-length"}:
                    handler.send_header(name, value)
            handler.send_header("Connection", "close")
            handler.end_headers()
            if handler.command != "HEAD":
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    handler.wfile.write(chunk)
                    handler.wfile.flush()
        except (BrokenPipeError, ConnectionError, OSError) as exc:
            if not getattr(handler, "_headers_buffer", None):
                self._write_json_error(
                    handler,
                    502,
                    f"model upstream unavailable: {type(exc).__name__}",
                )
        finally:
            connection.close()

    def _write_exhausted(
        self,
        handler: BaseHTTPRequestHandler,
        event: Mapping[str, object],
    ) -> None:
        limit = int(self.budget["limit"])
        message = (
            "MODEL_CALL_BUDGET_EXHAUSTED: the runner admitted exactly "
            f"{limit} provider requests; this terminal response was generated "
            "locally and was not sent to the provider"
        )
        payload = _terminal_response_payload(handler.path, message, event)
        self._write_json(handler, 200, payload)

    def _write_json_error(
        self,
        handler: BaseHTTPRequestHandler,
        status: int,
        message: str,
    ) -> None:
        self._write_json(
            handler,
            status,
            {"error": {"type": "proxy_error", "message": message}},
        )

    @staticmethod
    def _write_json(
        handler: BaseHTTPRequestHandler,
        status: int,
        payload: Mapping[str, object],
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.write(body)


def _task_tool_names(task_tools: Mapping[str, object] | None) -> tuple[str, ...]:
    if not isinstance(task_tools, Mapping):
        return ()
    raw_tools = task_tools.get("tools")
    if not isinstance(raw_tools, list):
        return ()
    names = tuple(
        row.get("name")
        for row in raw_tools
        if isinstance(row, Mapping) and isinstance(row.get("name"), str)
    )
    if len(names) != len(raw_tools) or len(set(names)) != len(names):
        raise ValueError("task-tool snapshot configuration is invalid")
    return names


def _canonical_task_tool_name(
    observed: str, expected_names: tuple[str, ...]
) -> str | None:
    lowered = observed.casefold()
    matches = [
        expected
        for expected in expected_names
        if lowered == expected.casefold()
        or lowered.endswith("__" + expected.casefold())
        or lowered.endswith("." + expected.casefold())
        or lowered.endswith("_" + expected.casefold())
    ]
    return matches[0] if len(matches) == 1 else None


def _provider_tool_snapshot(
    request_body: bytes | None, expected_names: tuple[str, ...]
) -> dict[str, object]:
    """Extract only declared task-tool definitions from a provider request."""

    try:
        payload = json.loads((request_body or b"").decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        payload = None
    raw_tools = payload.get("tools") if isinstance(payload, dict) else None
    tools = raw_tools if isinstance(raw_tools, list) else []
    observed: list[dict[str, object]] = []
    for raw in tools:
        if not isinstance(raw, Mapping):
            continue
        core = raw.get("function") if isinstance(raw.get("function"), Mapping) else raw
        name = core.get("name") or core.get("tool_name")
        if not isinstance(name, str):
            continue
        canonical = _canonical_task_tool_name(name, expected_names)
        if canonical is None:
            continue
        description = core.get("description")
        schema = None
        for key in ("parameters", "input_schema", "inputSchema", "schema"):
            if key in core:
                schema = core.get(key)
                break
        observed.append(
            {
                "canonical_name": canonical,
                "observed_name": name,
                "description": description if isinstance(description, str) else "",
                "input_schema": schema,
            }
        )
    counts = {name: 0 for name in expected_names}
    for row in observed:
        counts[str(row["canonical_name"])] += 1
    return {
        "request_body_valid": isinstance(payload, dict),
        "provider_tool_count": len(tools),
        "declared_task_tool_count": len(expected_names),
        "observed_task_tool_count": len(observed),
        "present_names": [name for name in expected_names if counts[name]],
        "missing_names": [name for name in expected_names if counts[name] == 0],
        "duplicate_names": [name for name in expected_names if counts[name] > 1],
        "tools": observed,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _terminal_response_payload(
    path: str,
    message: str,
    event: Mapping[str, object],
) -> dict[str, object]:
    normalized_path = path.split("?", 1)[0].rstrip("/")
    sequence = int(event["seq"])
    model = "harnessbench-model-call-budget"
    if normalized_path.endswith("/messages"):
        return {
            "id": f"msg_budget_{sequence}",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": message}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
    if normalized_path.endswith("/responses"):
        return {
            "id": f"resp_budget_{sequence}",
            "object": "response",
            "created_at": int(datetime.now(timezone.utc).timestamp()),
            "status": "completed",
            "error": None,
            "incomplete_details": None,
            "instructions": None,
            "max_output_tokens": None,
            "model": model,
            "output": [
                {
                    "id": f"msg_budget_{sequence}",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": message,
                            "annotations": [],
                        }
                    ],
                }
            ],
            "parallel_tool_calls": True,
            "previous_response_id": None,
            "reasoning": {"effort": None, "summary": None},
            "store": False,
            "temperature": 1.0,
            "text": {"format": {"type": "text"}},
            "tool_choice": "auto",
            "tools": [],
            "top_p": 1.0,
            "truncation": "disabled",
            "usage": {
                "input_tokens": 0,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": 0,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 0,
            },
            "metadata": {},
        }
    return {
        "id": f"chatcmpl-budget-{sequence}",
        "object": "chat.completion",
        "created": int(datetime.now(timezone.utc).timestamp()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": message},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }
