from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from harness_tracer.provider_requests import (
    PROVIDER_REQUEST_EVIDENCE_SCHEMA,
    PROVIDER_REQUEST_EVIDENCE_VERSION,
    extract_structured_tool_inventory,
    read_provider_request_events,
)


PROVIDER_REQUEST_RECORDER_HOST = "127.0.0.1"
PROVIDER_REQUEST_RECORDER_PORT = 4002
MAX_PROXY_REQUEST_BYTES = 64 * 1024 * 1024
LEGACY_PROVIDER_TIMING_SCHEMA = "harnessbench_provider_timing_v1"
PROVIDER_TIMING_SCHEMA = "harnessbench_provider_timing_v2"
PROVIDER_DECODE_BASIS = "first_semantic_output_to_response_eof"
PROVIDER_USAGE_SCHEMA = "harnessbench_provider_usage_v1"
NOT_AVAILABLE = "N/A"
_USAGE_TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)
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


def _unavailable_call_timing() -> dict[str, object]:
    """Represent a recorded request whose response observer is still settling."""
    return {
        "ttft_ms": NOT_AVAILABLE,
        "decode_duration_ms": NOT_AVAILABLE,
        "semantic_span_ms": NOT_AVAILABLE,
        "response_duration_ms": NOT_AVAILABLE,
        "semantic_output_observation_count": 0,
    }


def _unavailable_call_usage() -> dict[str, object]:
    return {
        "status": "unavailable",
        **{name: NOT_AVAILABLE for name in _USAGE_TOKEN_FIELDS},
    }


@dataclass(frozen=True)
class ProviderRequestRecorderInfo:
    base_url: str
    events_path: Path


class ProviderRequestRecorderProxy:
    """Forward provider traffic while recording sanitized numeric metadata.

    The recorder stores neither prompts, model output, nor authorization
    headers. It is placed at the last runner-owned HTTP boundary before the
    configured model provider, so its inventory and numeric response evidence
    represent the traffic actually exchanged upstream.
    """

    def __init__(
        self,
        *,
        output_root: Path,
        upstream_base_url: str,
        upstream_api_key: str | None = None,
        listen_host: str = PROVIDER_REQUEST_RECORDER_HOST,
        listen_port: int = PROVIDER_REQUEST_RECORDER_PORT,
    ) -> None:
        parsed = urlsplit(upstream_base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("provider-request recorder upstream URL is invalid")
        self.upstream_origin = f"{parsed.scheme}://{parsed.netloc}"
        self._opener = (
            build_opener(ProxyHandler({}))
            if parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            else build_opener()
        )
        self.upstream_api_key = upstream_api_key
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.output_root = Path(output_root) / "provider_requests"
        self.events_path = self.output_root / "events.jsonl"
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._event_seq = 0
        self._timings: dict[int, dict[str, object]] = {}
        self._usages: dict[int, dict[str, object]] = {}

    def start(self) -> ProviderRequestRecorderInfo:
        if self._server is not None or self._thread is not None:
            raise RuntimeError("provider-request recorder is already started")
        with self._lock:
            self._event_seq = 0
            self._timings.clear()
            self._usages.clear()
        self.output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.events_path.write_text("", encoding="utf-8")
        self.events_path.chmod(0o600)
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_GET(self) -> None:  # noqa: N802
                proxy._forward(self, record=False)

            def do_HEAD(self) -> None:  # noqa: N802
                proxy._forward(self, record=False)

            def do_OPTIONS(self) -> None:  # noqa: N802
                proxy._forward(self, record=False)

            def do_POST(self) -> None:  # noqa: N802
                proxy._forward(self, record=True)

            def log_message(self, format: str, *args: object) -> None:
                del format, args

        self._server = ThreadingHTTPServer(
            (self.listen_host, self.listen_port), Handler
        )
        self.listen_port = int(self._server.server_address[1])
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="harnessbench-provider-request-recorder",
            daemon=True,
        )
        self._thread.start()
        return ProviderRequestRecorderInfo(
            base_url=f"http://{self.listen_host}:{self.listen_port}",
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
                raise RuntimeError("provider-request recorder did not stop")

    def public_summary(self) -> dict[str, object]:
        rows = read_provider_request_events(self.events_path)
        request_count = len(rows)
        with self._lock:
            call_timings = [
                {
                    "request_seq": sequence,
                    **dict(
                        self._timings.get(sequence, _unavailable_call_timing())
                    ),
                }
                for sequence in range(1, request_count + 1)
            ]
            call_usages = [
                {
                    "request_seq": sequence,
                    **dict(self._usages.get(sequence, _unavailable_call_usage())),
                }
                for sequence in range(1, request_count + 1)
            ]
        timing = _timing_summary(
            call_timings,
            request_count=request_count,
        )
        usage = _usage_summary(
            call_usages,
            request_count=request_count,
        )
        return {
            "schema_version": PROVIDER_REQUEST_EVIDENCE_SCHEMA,
            "version": PROVIDER_REQUEST_EVIDENCE_VERSION,
            "request_count": request_count,
            "model_visible_request_count": sum(
                row.get("model_visible") is True for row in rows
            ),
            "complete_tool_inventory_count": sum(
                row.get("model_visible") is True
                and isinstance(row.get("tool_inventory"), dict)
                and row["tool_inventory"].get("complete") is True
                for row in rows
            ),
            "events_path": "provider_requests/events.jsonl",
            "timing": timing,
            "usage": usage,
        }
    def _forward(self, handler: BaseHTTPRequestHandler, *, record: bool) -> None:
        request_started_ns = time.monotonic_ns()
        timing_observer = _ResponseTimingObserver(request_started_ns)
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
        upstream_status: int | None = None
        error_type: str | None = None
        evidence_written = False
        provider_request_seq: int | None = None
        response_finished_ns: int | None = None
        response = None
        try:
            request = Request(
                self.upstream_origin + handler.path,
                data=body,
                headers=headers,
                method=handler.command,
            )
            try:
                response = self._opener.open(request, timeout=3600)
            except HTTPError as exc:
                # HTTP errors are still complete upstream exchanges and their
                # response bodies must be forwarded unchanged.
                response = exc
            upstream_status = int(response.status)
            if record:
                provider_request_seq = self._record(
                    path=handler.path,
                    body=body,
                    upstream_status=upstream_status,
                    error_type=None,
                )
                evidence_written = True
            handler.send_response(response.status, response.reason)
            for name, value in response.headers.items():
                if name.lower() not in _HOP_BY_HOP_HEADERS | {"content-length"}:
                    handler.send_header(name, value)
            handler.send_header("Connection", "close")
            handler.end_headers()
            if handler.command != "HEAD":
                while True:
                    reader = getattr(response, "read1", response.read)
                    chunk = reader(64 * 1024)
                    if not chunk:
                        break
                    timing_observer.feed(chunk, time.monotonic_ns())
                    handler.wfile.write(chunk)
                    handler.wfile.flush()
            response_finished_ns = time.monotonic_ns()
        except (BrokenPipeError, ConnectionError, OSError, URLError) as exc:
            error_type = type(exc).__name__
            if not getattr(handler, "_headers_buffer", None):
                self._write_json_error(
                    handler,
                    502,
                    f"model upstream unavailable: {error_type}",
                )
        finally:
            if record and not evidence_written:
                provider_request_seq = self._record(
                    path=handler.path,
                    body=body,
                    upstream_status=upstream_status,
                    error_type=error_type or "UnknownForwardingError",
                )
            if record and provider_request_seq is not None:
                timing = timing_observer.finish(response_finished_ns)
                with self._lock:
                    self._timings[provider_request_seq] = timing
                    self._usages[provider_request_seq] = timing_observer.usage()
            if response is not None:
                response.close()

    def _record(
        self,
        *,
        path: str,
        body: bytes | None,
        upstream_status: int | None,
        error_type: str | None,
    ) -> int:
        with self._lock:
            self._event_seq += 1
            event = {
                "schema_version": PROVIDER_REQUEST_EVIDENCE_SCHEMA,
                "version": PROVIDER_REQUEST_EVIDENCE_VERSION,
                "seq": self._event_seq,
                "timestamp": _utc_now(),
                "method": "POST",
                "path": path.split("?", 1)[0],
                "upstream_status": upstream_status,
                "model_visible": (
                    isinstance(upstream_status, int)
                    and 200 <= upstream_status < 300
                ),
                "error_type": error_type,
                "tool_inventory": extract_structured_tool_inventory(body),
            }
            with self.events_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
                stream.write("\n")
            return self._event_seq

    def _write_json_error(
        self,
        handler: BaseHTTPRequestHandler,
        status: int,
        message: str,
    ) -> None:
        payload = json.dumps(
            {"error": {"type": "proxy_error", "message": message}},
            ensure_ascii=False,
        ).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(payload)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.write(payload)


class _ResponseTimingObserver:
    """Observe streamed response metadata without retaining model output."""

    def __init__(self, request_started_ns: int) -> None:
        self.request_started_ns = request_started_ns
        self.first_output_ns: int | None = None
        self.last_output_ns: int | None = None
        self.semantic_output_observation_count = 0
        self._line_buffer = bytearray()
        self._disabled = False
        self._usage: dict[str, int] = {}

    def feed(self, chunk: bytes, observed_ns: int) -> None:
        if self._disabled:
            return
        try:
            self._line_buffer.extend(chunk)
            # Provider streams are line-delimited. A large line is a regular
            # non-streaming response; do not retain model output just to time it.
            if len(self._line_buffer) > 1024 * 1024:
                self._disabled = True
                self._line_buffer.clear()
                return
            while True:
                newline = self._line_buffer.find(b"\n")
                if newline < 0:
                    break
                line = bytes(self._line_buffer[:newline]).strip()
                del self._line_buffer[: newline + 1]
                self._observe_line(line, observed_ns)
        except Exception:
            # Timing is diagnostic only and must never affect proxy forwarding.
            self._disabled = True
            self._line_buffer.clear()

    def finish(self, response_finished_ns: int | None) -> dict[str, object]:
        # A valid SSE/JSONL event is line-terminated. Ignore any residual bytes
        # so a regular JSON response is never mistaken for streamed output.
        self._line_buffer.clear()
        return {
            "ttft_ms": _elapsed_ms(self.request_started_ns, self.first_output_ns),
            "decode_duration_ms": _elapsed_ms(
                self.first_output_ns, response_finished_ns
            ),
            "semantic_span_ms": _elapsed_ms(
                self.first_output_ns, self.last_output_ns
            ),
            "response_duration_ms": _elapsed_ms(
                self.request_started_ns, response_finished_ns
            ),
            "semantic_output_observation_count": (
                self.semantic_output_observation_count
            ),
        }

    def usage(self) -> dict[str, object]:
        input_tokens = self._usage.get("input_tokens")
        output_tokens = self._usage.get("output_tokens")
        total_tokens = (
            input_tokens + output_tokens
            if input_tokens is not None and output_tokens is not None
            else NOT_AVAILABLE
        )
        return {
            "status": (
                "available"
                if input_tokens is not None and output_tokens is not None
                else "partial" if self._usage else "unavailable"
            ),
            **{
                name: self._usage.get(name, NOT_AVAILABLE)
                for name in _USAGE_TOKEN_FIELDS
                if name != "total_tokens"
            },
            "total_tokens": total_tokens,
        }

    def _observe_line(self, line: bytes, observed_ns: int) -> None:
        if not line:
            return
        if line.startswith(b"data:"):
            line = line[5:].strip()
        if line in {b"[DONE]", b"[done]"}:
            return
        try:
            payload = json.loads(line)
        except (UnicodeError, json.JSONDecodeError):
            return
        self._usage.update(_normalized_provider_usage(payload))
        if _has_semantic_output(payload):
            self.semantic_output_observation_count += 1
            if self.first_output_ns is None:
                self.first_output_ns = observed_ns
            self.last_output_ns = observed_ns


def _has_semantic_output(payload: object) -> bool:
    if not isinstance(payload, Mapping):
        return False

    event_type = payload.get("type")
    if isinstance(event_type, str) and event_type.endswith(".delta"):
        if _nonempty_output(payload.get("delta")):
            return True
    if event_type in {"content_block_start", "response.output_item.added"}:
        block = payload.get("content_block", payload.get("item"))
        if isinstance(block, Mapping) and any(
            _nonempty_output(block.get(key))
            for key in ("text", "thinking", "name", "arguments")
        ):
            return True

    choices = payload.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, Mapping):
                continue
            delta = choice.get("delta")
            if isinstance(delta, Mapping) and any(
                _nonempty_output(delta.get(key))
                for key in (
                    "content",
                    "reasoning_content",
                    "reasoning",
                    "tool_calls",
                    "function_call",
                )
            ):
                return True
    delta = payload.get("delta")
    if isinstance(delta, Mapping) and any(
        _nonempty_output(delta.get(key))
        for key in ("text", "thinking", "partial_json", "content")
    ):
        return True

    return False


def _nonempty_output(value: object) -> bool:
    if isinstance(value, str):
        return bool(value)
    if isinstance(value, (list, Mapping)):
        return bool(value)
    return False


def _normalized_provider_usage(payload: object) -> dict[str, int]:
    if not isinstance(payload, Mapping):
        return {}
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        response = payload.get("response")
        usage = response.get("usage") if isinstance(response, Mapping) else None
    if not isinstance(usage, Mapping):
        return {}

    result: dict[str, int] = {}
    prompt_tokens = _first_token_integer(usage, ("prompt_tokens",))
    raw_input_tokens = _first_token_integer(usage, ("input_tokens",))
    cache_read_tokens = _first_token_integer(
        usage,
        ("cached_input_tokens", "cache_read_input_tokens"),
    )
    if cache_read_tokens is None:
        cache_read_tokens = _nested_token_integer(
            usage,
            ("prompt_tokens_details", "input_tokens_details"),
            ("cached_tokens", "cache_read_tokens"),
        )
    cache_write_tokens = _first_token_integer(
        usage,
        ("cache_write_input_tokens", "cache_creation_input_tokens"),
    )
    if cache_write_tokens is None:
        cache_write_tokens = _nested_token_integer(
            usage,
            ("prompt_tokens_details", "input_tokens_details"),
            ("cache_write_tokens", "cache_creation_tokens"),
        )

    if prompt_tokens is not None:
        result["input_tokens"] = prompt_tokens
    elif raw_input_tokens is not None:
        # Anthropic-style usage reports uncached input separately from cache
        # reads and writes. OpenAI Responses-style input_tokens already
        # includes its nested cached-token breakdown, so only add the
        # Anthropic top-level counters.
        anthropic_cache_read = _first_token_integer(
            usage, ("cache_read_input_tokens",)
        )
        anthropic_cache_write = _first_token_integer(
            usage, ("cache_creation_input_tokens",)
        )
        result["input_tokens"] = (
            raw_input_tokens
            + (anthropic_cache_read or 0)
            + (anthropic_cache_write or 0)
        )
    if cache_read_tokens is not None:
        result["cached_input_tokens"] = cache_read_tokens
    if cache_write_tokens is not None:
        result["cache_write_input_tokens"] = cache_write_tokens

    output_tokens = _first_token_integer(
        usage,
        ("completion_tokens", "output_tokens"),
    )
    if output_tokens is not None:
        result["output_tokens"] = output_tokens
    reasoning_tokens = _first_token_integer(
        usage,
        ("reasoning_output_tokens",),
    )
    if reasoning_tokens is None:
        reasoning_tokens = _nested_token_integer(
            usage,
            ("completion_tokens_details", "output_tokens_details"),
            ("reasoning_tokens",),
        )
    if reasoning_tokens is not None:
        result["reasoning_output_tokens"] = reasoning_tokens
    return result


def _first_token_integer(
    value: Mapping[object, object], names: tuple[str, ...]
) -> int | None:
    for name in names:
        token_count = _token_integer(value.get(name))
        if token_count is not None:
            return token_count
    return None


def _nested_token_integer(
    value: Mapping[object, object],
    containers: tuple[str, ...],
    names: tuple[str, ...],
) -> int | None:
    for container in containers:
        nested = value.get(container)
        if isinstance(nested, Mapping):
            token_count = _first_token_integer(nested, names)
            if token_count is not None:
                return token_count
    return None


def _token_integer(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _elapsed_ms(start_ns: int | None, end_ns: int | None) -> float | str:
    if start_ns is None or end_ns is None or end_ns < start_ns:
        return "N/A"
    return round((end_ns - start_ns) / 1_000_000, 3)


def _timing_summary(
    timings: list[dict[str, object]], *, request_count: int
) -> dict[str, object]:
    ttfts = [
        float(value)
        for row in timings
        if isinstance((value := row.get("ttft_ms")), (int, float))
        and not isinstance(value, bool)
    ]
    decodes = [
        float(value)
        for row in timings
        if isinstance((value := row.get("decode_duration_ms")), (int, float))
        and not isinstance(value, bool)
        and value >= 0
    ]
    return {
        "schema_version": PROVIDER_TIMING_SCHEMA,
        "status": (
            "available"
            if (
                request_count > 0
                and len(ttfts) == request_count
                and len(decodes) == request_count
            )
            else "partial" if ttfts or decodes else "unavailable"
        ),
        "ttft_mean_ms": round(sum(ttfts) / len(ttfts), 3) if ttfts else "N/A",
        "ttft_observed_count": len(ttfts),
        "decode_duration_ms": round(sum(decodes), 3) if decodes else "N/A",
        "decode_observed_count": len(decodes),
        "request_count": request_count,
        "decode_basis": PROVIDER_DECODE_BASIS,
        "calls": [dict(row) for row in timings],
    }


def _usage_summary(
    usages: list[dict[str, object]], *, request_count: int
) -> dict[str, object]:
    observed_count = sum(
        row.get("status") in {"available", "partial"} for row in usages
    )
    complete_count = sum(row.get("status") == "available" for row in usages)
    return {
        "schema_version": PROVIDER_USAGE_SCHEMA,
        "status": (
            "available"
            if request_count > 0 and complete_count == request_count
            else "partial" if observed_count else "unavailable"
        ),
        "request_count": request_count,
        "observed_count": observed_count,
        "complete_count": complete_count,
        "calls": [dict(row) for row in usages],
    }


def validate_provider_request_summary(
    value: Mapping[str, object],
) -> dict[str, object]:
    required = {
        "schema_version",
        "version",
        "request_count",
        "model_visible_request_count",
        "complete_tool_inventory_count",
        "events_path",
    }
    extra = set(value) - required
    if not required.issubset(value) or not extra <= {"timing", "usage"}:
        raise ValueError("provider-request recorder summary fields are invalid")
    request_count = value.get("request_count")
    visible_count = value.get("model_visible_request_count")
    complete_count = value.get("complete_tool_inventory_count")
    if (
        value.get("schema_version") != PROVIDER_REQUEST_EVIDENCE_SCHEMA
        or value.get("version") != PROVIDER_REQUEST_EVIDENCE_VERSION
        or value.get("events_path") != "provider_requests/events.jsonl"
        or type(request_count) is not int
        or type(visible_count) is not int
        or type(complete_count) is not int
        or not 0 <= complete_count <= visible_count <= request_count
    ):
        raise ValueError("provider-request recorder summary values are invalid")
    timing = value.get("timing")
    if timing is not None:
        _validate_timing_summary(timing, request_count=request_count)
    usage = value.get("usage")
    if usage is not None:
        _validate_usage_summary(usage, request_count=request_count)
    return dict(value)


def _validate_timing_summary(value: object, *, request_count: int) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("provider timing summary is invalid")
    legacy_required = {
        "schema_version",
        "status",
        "ttft_mean_ms",
        "ttft_observed_count",
        "decode_duration_ms",
        "decode_observed_count",
        "request_count",
    }
    schema_version = value.get("schema_version")
    if schema_version == LEGACY_PROVIDER_TIMING_SCHEMA:
        required = legacy_required
    elif schema_version == PROVIDER_TIMING_SCHEMA:
        required = legacy_required | {"decode_basis", "calls"}
    else:
        raise ValueError("provider timing summary is invalid")
    ttft_count = value.get("ttft_observed_count")
    decode_count = value.get("decode_observed_count")
    if (
        set(value) != required
        or value.get("status") not in {"available", "partial", "unavailable"}
        or type(ttft_count) is not int
        or type(decode_count) is not int
        or not 0 <= ttft_count <= request_count
        or not 0 <= decode_count <= request_count
        or value.get("request_count") != request_count
        or not _metric_number_or_na(value.get("ttft_mean_ms"))
        or not _metric_number_or_na(value.get("decode_duration_ms"))
    ):
        raise ValueError("provider timing summary is invalid")
    if schema_version == PROVIDER_TIMING_SCHEMA:
        _validate_call_timings(
            value.get("calls"),
            request_count=request_count,
            ttft_count=ttft_count,
            decode_count=decode_count,
        )
        if value.get("decode_basis") != PROVIDER_DECODE_BASIS:
            raise ValueError("provider timing summary is invalid")


def _validate_call_timings(
    value: object,
    *,
    request_count: int,
    ttft_count: int,
    decode_count: int,
) -> None:
    if not isinstance(value, list) or len(value) != request_count:
        raise ValueError("provider timing call evidence is invalid")
    required = {
        "request_seq",
        "ttft_ms",
        "decode_duration_ms",
        "semantic_span_ms",
        "response_duration_ms",
        "semantic_output_observation_count",
    }
    if [
        row.get("request_seq") if isinstance(row, Mapping) else None
        for row in value
    ] != list(range(1, request_count + 1)):
        raise ValueError("provider timing call evidence is invalid")
    for row in value:
        if not isinstance(row, Mapping):
            raise ValueError("provider timing call evidence is invalid")
        observation_count = row.get("semantic_output_observation_count")
        if (
            set(row) != required
            or type(observation_count) is not int
            or observation_count < 0
            or any(
                not _metric_number_or_na(row.get(name))
                for name in (
                    "ttft_ms",
                    "decode_duration_ms",
                    "semantic_span_ms",
                    "response_duration_ms",
                )
            )
        ):
            raise ValueError("provider timing call evidence is invalid")
    observed_ttfts = sum(row.get("ttft_ms") != "N/A" for row in value)
    observed_decodes = sum(
        row.get("decode_duration_ms") != "N/A" for row in value
    )
    if observed_ttfts != ttft_count or observed_decodes != decode_count:
        raise ValueError("provider timing call evidence is inconsistent")


def _validate_usage_summary(value: object, *, request_count: int) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("provider usage summary is invalid")
    required = {
        "schema_version",
        "status",
        "request_count",
        "observed_count",
        "complete_count",
        "calls",
    }
    observed_count = value.get("observed_count")
    complete_count = value.get("complete_count")
    calls = value.get("calls")
    if (
        set(value) != required
        or value.get("schema_version") != PROVIDER_USAGE_SCHEMA
        or value.get("status") not in {"available", "partial", "unavailable"}
        or value.get("request_count") != request_count
        or type(observed_count) is not int
        or type(complete_count) is not int
        or not 0 <= complete_count <= observed_count <= request_count
        or not isinstance(calls, list)
        or len(calls) != request_count
    ):
        raise ValueError("provider usage summary is invalid")
    required_call_fields = {"request_seq", "status", *_USAGE_TOKEN_FIELDS}
    if [
        row.get("request_seq") if isinstance(row, Mapping) else None
        for row in calls
    ] != list(range(1, request_count + 1)):
        raise ValueError("provider usage call evidence is invalid")
    for row in calls:
        if (
            not isinstance(row, Mapping)
            or set(row) != required_call_fields
            or row.get("status") not in {"available", "partial", "unavailable"}
            or any(
                not _token_number_or_na(row.get(name))
                for name in _USAGE_TOKEN_FIELDS
            )
        ):
            raise ValueError("provider usage call evidence is invalid")
        input_tokens = row.get("input_tokens")
        output_tokens = row.get("output_tokens")
        total_tokens = row.get("total_tokens")
        expected_status = (
            "available"
            if isinstance(input_tokens, int) and isinstance(output_tokens, int)
            else (
                "partial"
                if any(isinstance(row.get(name), int) for name in _USAGE_TOKEN_FIELDS)
                else "unavailable"
            )
        )
        if (
            row.get("status") != expected_status
            or (
                isinstance(input_tokens, int)
                and isinstance(output_tokens, int)
                and total_tokens != input_tokens + output_tokens
            )
            or (
                (
                    not isinstance(input_tokens, int)
                    or not isinstance(output_tokens, int)
                )
                and total_tokens != NOT_AVAILABLE
            )
        ):
            raise ValueError("provider usage call evidence is inconsistent")
    observed_calls = sum(row.get("status") != "unavailable" for row in calls)
    complete_calls = sum(row.get("status") == "available" for row in calls)
    expected_summary_status = (
        "available"
        if request_count > 0 and complete_calls == request_count
        else "partial" if observed_calls else "unavailable"
    )
    if (
        observed_calls != observed_count
        or complete_calls != complete_count
        or value.get("status") != expected_summary_status
    ):
        raise ValueError("provider usage summary is inconsistent")


def _metric_number_or_na(value: object) -> bool:
    return value == "N/A" or (
        isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
    )


def _token_number_or_na(value: object) -> bool:
    return value == NOT_AVAILABLE or _token_integer(value) is not None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
