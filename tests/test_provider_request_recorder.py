from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

from harness_tracer.process import CompletedProcessData
from harness_tracer.provider_requests import (
    PROVIDER_REQUEST_EVIDENCE_SCHEMA,
    PROVIDER_REQUEST_EVIDENCE_VERSION,
    extract_structured_tool_inventory,
    read_provider_request_events,
    trace_tool_inventory_payloads,
)
from harness_tracer.runner import RunConfig, TraceRunner
from harnessbench.provider_request_recorder import (
    LEGACY_PROVIDER_TIMING_SCHEMA,
    PROVIDER_DECODE_BASIS,
    PROVIDER_TIMING_SCHEMA,
    PROVIDER_USAGE_SCHEMA,
    ProviderRequestRecorderProxy,
    _ResponseTimingObserver,
    _timing_summary,
    _usage_summary,
    validate_provider_request_summary,
)


def test_recorder_forwards_and_captures_sanitized_tool_inventory(
    tmp_path: Path,
) -> None:
    received: list[dict[str, object]] = []

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            received.append(
                {
                    "authorization": self.headers.get("Authorization"),
                    "body": json.loads(self.rfile.read(length)),
                }
            )
            payload = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    proxy = ProviderRequestRecorderProxy(
        output_root=tmp_path,
        upstream_base_url=f"http://127.0.0.1:{upstream.server_address[1]}",
        upstream_api_key="private-upstream-key",
        listen_port=0,
    )
    info = proxy.start()
    opener = build_opener(ProxyHandler({}))
    requests = [
        {
            "model": "test",
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "request_user_input",
                        "description": "Ask the user for a decision.",
                        "parameters": {
                            "type": "object",
                            "properties": {"question": {"type": "string"}},
                            "required": ["question"],
                        },
                    },
                },
                {
                    "name": "read_file",
                    "description": "Read one file.",
                    "input_schema": {"type": "object"},
                },
            ],
        },
        {"model": "test", "messages": []},
    ]
    try:
        for payload in requests:
            request = Request(
                info.base_url + "/v1/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer harness-key",
                },
            )
            with opener.open(request, timeout=5) as response:
                assert response.status == 200
                assert json.loads(response.read()) == {"ok": True}
    finally:
        proxy.stop()
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=5)

    rows = read_provider_request_events(info.events_path)
    assert len(rows) == 2
    assert rows[0]["model_visible"] is True
    assert rows[0]["tool_inventory"]["complete"] is True
    assert [tool["name"] for tool in rows[0]["tool_inventory"]["tools"]] == [
        "request_user_input",
        "read_file",
    ]
    assert rows[1]["tool_inventory"]["complete"] is False
    assert rows[1]["tool_inventory"]["declaration_field_present"] is False
    assert all(row["authorization"] == "Bearer private-upstream-key" for row in received)
    assert "private-upstream-key" not in info.events_path.read_text(encoding="utf-8")
    summary = proxy.public_summary()
    assert summary["request_count"] == 2
    assert summary["model_visible_request_count"] == 2
    assert summary["complete_tool_inventory_count"] == 1
    assert summary["timing"]["status"] == "unavailable"
    assert summary["timing"]["schema_version"] == PROVIDER_TIMING_SCHEMA
    assert summary["timing"]["decode_basis"] == PROVIDER_DECODE_BASIS
    assert len(summary["timing"]["calls"]) == 2
    assert summary["usage"] == {
        "schema_version": PROVIDER_USAGE_SCHEMA,
        "status": "unavailable",
        "request_count": 2,
        "observed_count": 0,
        "complete_count": 0,
        "calls": [
            {
                "request_seq": sequence,
                "status": "unavailable",
                "input_tokens": "N/A",
                "cached_input_tokens": "N/A",
                "cache_write_input_tokens": "N/A",
                "output_tokens": "N/A",
                "reasoning_output_tokens": "N/A",
                "total_tokens": "N/A",
            }
            for sequence in (1, 2)
        ],
    }
    assert validate_provider_request_summary(summary) == summary


def test_public_summary_keeps_inflight_request_evidence_valid(
    tmp_path: Path,
) -> None:
    proxy = ProviderRequestRecorderProxy(
        output_root=tmp_path,
        upstream_base_url="https://example.invalid",
        listen_port=0,
    )
    proxy.output_root.mkdir(parents=True)
    rows = [
        {
            "schema_version": PROVIDER_REQUEST_EVIDENCE_SCHEMA,
            "version": PROVIDER_REQUEST_EVIDENCE_VERSION,
            "seq": sequence,
            "timestamp": "2026-01-01T00:00:00Z",
            "method": "POST",
            "path": "/chat/completions",
            "upstream_status": 200,
            "model_visible": True,
            "error_type": None,
            "tool_inventory": {
                "complete": True,
                "declaration_channels": ["tools"],
                "declaration_field_present": True,
                "invalid_tool_count": 0,
                "request_body_valid": True,
                "scope": "structured_model_tools",
                "tool_count": 0,
                "tools": [],
            },
        }
        for sequence in (1, 2)
    ]
    proxy.events_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    proxy._timings[1] = {
        "ttft_ms": "N/A",
        "decode_duration_ms": "N/A",
        "semantic_span_ms": "N/A",
        "response_duration_ms": 1.0,
        "semantic_output_observation_count": 0,
    }
    proxy._usages[1] = {
        "status": "unavailable",
        "input_tokens": "N/A",
        "cached_input_tokens": "N/A",
        "cache_write_input_tokens": "N/A",
        "output_tokens": "N/A",
        "reasoning_output_tokens": "N/A",
        "total_tokens": "N/A",
    }

    summary = proxy.public_summary()

    assert len(summary["timing"]["calls"]) == 2
    assert summary["timing"]["calls"][1]["request_seq"] == 2
    assert summary["timing"]["calls"][1]["response_duration_ms"] == "N/A"
    assert len(summary["usage"]["calls"]) == 2
    assert summary["usage"]["calls"][1]["status"] == "unavailable"
    assert validate_provider_request_summary(summary) == summary


def test_recorder_observes_streamed_model_output_without_changing_response(
    tmp_path: Path,
) -> None:
    chunks = [
        b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":" world"}}]}\n\n',
        (
            b'data: {"choices":[],"usage":{"prompt_tokens":120,'
            b'"completion_tokens":8,"total_tokens":128,'
            b'"prompt_tokens_details":{"cached_tokens":64},'
            b'"completion_tokens_details":{"reasoning_tokens":3}}}\n\n'
        ),
        b"data: [DONE]\n\n",
    ]

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in chunks:
                self.wfile.write(chunk)
                self.wfile.flush()
                time.sleep(0.01)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    proxy = ProviderRequestRecorderProxy(
        output_root=tmp_path,
        upstream_base_url=f"http://127.0.0.1:{upstream.server_address[1]}",
        listen_port=0,
    )
    info = proxy.start()
    try:
        request = Request(
            info.base_url + "/v1/chat/completions",
            data=b'{"model":"test","stream":true}',
            headers={"Content-Type": "application/json"},
        )
        with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
            assert response.read() == b"".join(chunks)
    finally:
        proxy.stop()
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=5)

    timing = proxy.public_summary()["timing"]
    assert timing["status"] == "available"
    assert timing["ttft_observed_count"] == 1
    assert isinstance(timing["ttft_mean_ms"], float)
    assert timing["decode_observed_count"] == 1
    assert isinstance(timing["decode_duration_ms"], float)
    assert timing["decode_basis"] == PROVIDER_DECODE_BASIS
    assert timing["calls"][0]["request_seq"] == 1
    assert timing["calls"][0]["semantic_output_observation_count"] == 2
    assert "hello" not in json.dumps(timing)
    usage = proxy.public_summary()["usage"]
    assert usage["status"] == "available"
    assert usage["observed_count"] == 1
    assert usage["complete_count"] == 1
    assert usage["calls"] == [
        {
            "request_seq": 1,
            "status": "available",
            "input_tokens": 120,
            "cached_input_tokens": 64,
            "cache_write_input_tokens": "N/A",
            "output_tokens": 8,
            "reasoning_output_tokens": 3,
            "total_tokens": 128,
        }
    ]
    assert "hello" not in json.dumps(usage)


def test_coalesced_semantic_output_uses_response_eof_for_decode_timing() -> None:
    observer = _ResponseTimingObserver(request_started_ns=1_000_000_000)
    observer.feed(
        (
            b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
            b'data: {"choices":[{"delta":{"content":" world"}}]}\n\n'
            b"data: [DONE]\n\n"
        ),
        observed_ns=1_100_000_000,
    )

    timing = observer.finish(response_finished_ns=1_300_000_000)

    assert timing == {
        "ttft_ms": 100.0,
        "decode_duration_ms": 200.0,
        "semantic_span_ms": 0.0,
        "response_duration_ms": 300.0,
        "semantic_output_observation_count": 2,
    }


def test_incomplete_response_keeps_decode_timing_partial() -> None:
    observer = _ResponseTimingObserver(request_started_ns=1_000_000_000)
    observer.feed(
        b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n',
        observed_ns=1_100_000_000,
    )
    call = {
        "request_seq": 1,
        **observer.finish(response_finished_ns=None),
    }

    timing = _timing_summary([call], request_count=1)

    assert timing["status"] == "partial"
    assert timing["ttft_observed_count"] == 1
    assert timing["decode_observed_count"] == 0
    assert timing["decode_duration_ms"] == "N/A"


def test_provider_usage_normalizes_anthropic_counters_without_content() -> None:
    observer = _ResponseTimingObserver(request_started_ns=1_000_000_000)
    observer.feed(
        (
            b'data: {"type":"message_delta","delta":{"text":"secret-output"},'
            b'"usage":{"input_tokens":10,"cache_read_input_tokens":20,'
            b'"cache_creation_input_tokens":5,"output_tokens":7}}\n\n'
        ),
        observed_ns=1_100_000_000,
    )

    usage = observer.usage()

    assert usage == {
        "status": "available",
        "input_tokens": 35,
        "cached_input_tokens": 20,
        "cache_write_input_tokens": 5,
        "output_tokens": 7,
        "reasoning_output_tokens": "N/A",
        "total_tokens": 42,
    }
    assert "secret-output" not in json.dumps(usage)


def test_provider_usage_summary_keeps_partial_calls_explicit() -> None:
    usage = _usage_summary(
        [
            {
                "request_seq": 1,
                "status": "available",
                "input_tokens": 10,
                "cached_input_tokens": 4,
                "cache_write_input_tokens": "N/A",
                "output_tokens": 2,
                "reasoning_output_tokens": "N/A",
                "total_tokens": 12,
            },
            {
                "request_seq": 2,
                "status": "partial",
                "input_tokens": 12,
                "cached_input_tokens": "N/A",
                "cache_write_input_tokens": "N/A",
                "output_tokens": "N/A",
                "reasoning_output_tokens": "N/A",
                "total_tokens": "N/A",
            },
        ],
        request_count=2,
    )

    assert usage["status"] == "partial"
    assert usage["observed_count"] == 2
    assert usage["complete_count"] == 1


def test_provider_usage_validator_rejects_non_numeric_response_content() -> None:
    summary = {
        "schema_version": PROVIDER_REQUEST_EVIDENCE_SCHEMA,
        "version": PROVIDER_REQUEST_EVIDENCE_VERSION,
        "request_count": 1,
        "model_visible_request_count": 1,
        "complete_tool_inventory_count": 0,
        "events_path": "provider_requests/events.jsonl",
        "usage": _usage_summary(
            [
                {
                    "request_seq": 1,
                    "status": "available",
                    "input_tokens": 10,
                    "cached_input_tokens": 0,
                    "cache_write_input_tokens": "N/A",
                    "output_tokens": 2,
                    "reasoning_output_tokens": "N/A",
                    "total_tokens": 12,
                }
            ],
            request_count=1,
        ),
    }
    summary["usage"]["calls"][0]["content"] = "must-not-be-retained"

    try:
        validate_provider_request_summary(summary)
    except ValueError as exc:
        assert "provider usage call evidence" in str(exc)
    else:
        raise AssertionError("provider usage accepted response content")


def test_validator_accepts_legacy_provider_timing_summary() -> None:
    summary = {
        "schema_version": PROVIDER_REQUEST_EVIDENCE_SCHEMA,
        "version": PROVIDER_REQUEST_EVIDENCE_VERSION,
        "request_count": 1,
        "model_visible_request_count": 1,
        "complete_tool_inventory_count": 0,
        "events_path": "provider_requests/events.jsonl",
        "timing": {
            "schema_version": LEGACY_PROVIDER_TIMING_SCHEMA,
            "status": "available",
            "ttft_mean_ms": 100.0,
            "ttft_observed_count": 1,
            "decode_duration_ms": 0.0,
            "decode_observed_count": 0,
            "request_count": 1,
        },
    }

    assert validate_provider_request_summary(summary) == summary


def test_inventory_extractor_rejects_malformed_or_implicit_catalogs() -> None:
    malformed = extract_structured_tool_inventory(
        json.dumps({"tools": [{"description": "missing name"}]}).encode()
    )
    implicit = extract_structured_tool_inventory(
        json.dumps({"messages": [{"role": "system", "content": "use tools"}]}).encode()
    )
    explicit_empty = extract_structured_tool_inventory(
        json.dumps({"tools": []}).encode()
    )
    provider_native = extract_structured_tool_inventory(
        json.dumps({"tools": [{"type": "web_search"}]}).encode()
    )

    assert malformed["complete"] is False
    assert malformed["invalid_tool_count"] == 1
    assert implicit["complete"] is False
    assert implicit["declaration_field_present"] is False
    assert explicit_empty["complete"] is True
    assert explicit_empty["tools"] == []
    assert provider_native["complete"] is True
    assert provider_native["tools"][0]["name"] == "web_search"


def test_trace_runner_inserts_complete_inventory_before_model_activity(
    tmp_path: Path,
) -> None:
    evidence_path = tmp_path / "provider_requests" / "events.jsonl"
    evidence_path.parent.mkdir()
    evidence_path.write_text("", encoding="utf-8")
    inventory = extract_structured_tool_inventory(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "exec",
                        "description": "Run a command.",
                        "input_schema": {"type": "object"},
                    }
                ]
            }
        ).encode()
    )

    class EvidenceWritingRunner:
        def run(self, command: list[str], **kwargs: object) -> CompletedProcessData:
            del kwargs
            evidence_path.write_text(
                json.dumps(
                    {
                        "schema_version": PROVIDER_REQUEST_EVIDENCE_SCHEMA,
                        "version": PROVIDER_REQUEST_EVIDENCE_VERSION,
                        "seq": 1,
                        "timestamp": "2026-08-12T00:00:00Z",
                        "method": "POST",
                        "path": "/v1/chat/completions",
                        "upstream_status": 200,
                        "model_visible": True,
                        "error_type": None,
                        "tool_inventory": inventory,
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            return CompletedProcessData(
                args=command,
                returncode=0,
                stdout="model output",
                stderr="",
                timed_out=False,
            )

    output = tmp_path / "trace"
    output.mkdir()
    summary = TraceRunner(process_runner=EvidenceWritingRunner()).run(
        RunConfig(
            harness="opencode",
            model="test-model",
            input_query="test",
            output_dir=output,
            cwd=tmp_path,
            provider_request_events_path=evidence_path,
        )
    )
    events = json.loads(Path(summary["events_path"]).read_text(encoding="utf-8"))

    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert events[0]["event_type"] == "run_started"
    assert events[1]["event_type"] == "tool_inventory"
    assert events[1]["complete"] is True
    assert events[1]["model_visible"] is True
    assert [tool["name"] for tool in events[1]["tools"]] == ["exec"]
    assert summary["tool_inventory"] == {
        "event_count": 1,
        "complete_model_visible_event_count": 1,
        "tool_names_from_complete_inventories": ["exec"],
        "sources": ["provider_request"],
    }


def test_repeated_identical_request_inventories_are_deduplicated(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    inventory = extract_structured_tool_inventory(
        json.dumps({"tools": [{"name": "read", "parameters": {}}]}).encode()
    )
    rows = [
        {
            "schema_version": PROVIDER_REQUEST_EVIDENCE_SCHEMA,
            "version": PROVIDER_REQUEST_EVIDENCE_VERSION,
            "seq": sequence,
            "timestamp": f"2026-08-12T00:00:0{sequence}Z",
            "method": "POST",
            "path": "/v1/responses",
            "upstream_status": 200,
            "model_visible": True,
            "error_type": None,
            "tool_inventory": inventory,
        }
        for sequence in (1, 2)
    ]
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )

    payloads = trace_tool_inventory_payloads(path, after_seq=0)

    assert len(payloads) == 1
    assert payloads[0]["request_count"] == 2
    assert payloads[0]["provider_request_seqs"] == [1, 2]
