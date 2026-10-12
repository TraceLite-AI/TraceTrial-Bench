from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from harnessbench.model_call_budget import (
    MODEL_CALL_BUDGET_SCHEMA,
    ModelCallBudgetProxy,
    _terminal_response_payload,
    validate_model_call_budget,
)


def test_validate_model_call_budget_rejects_invalid_reserve() -> None:
    with pytest.raises(ValueError, match="reserve"):
        validate_model_call_budget(
            {
                "version": "provider_requests_v1",
                "limit": 4,
                "finalization_reserve": 4,
            }
        )


def test_proxy_counts_forwards_and_caps_provider_requests(tmp_path: Path) -> None:
    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            payload = self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    proxy = ModelCallBudgetProxy(
        budget={
            "version": "provider_requests_v1",
            "limit": 2,
            "finalization_reserve": 1,
        },
        output_root=tmp_path,
        upstream_port=int(upstream.server_address[1]),
        listen_port=0,
    )
    info = proxy.start()
    opener = build_opener(ProxyHandler({}))
    try:
        for number in (1, 2):
            body = json.dumps({"request": number}).encode("utf-8")
            request = Request(
                info.base_url + "/v1/chat/completions",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            with opener.open(request, timeout=5) as response:
                assert response.status == 200
                assert json.loads(response.read()) == {"request": number}

        body = json.dumps({"request": 3}).encode("utf-8")
        with opener.open(
            Request(
                info.base_url + "/v1/chat/completions",
                data=body,
                headers={"Content-Type": "application/json"},
            ),
            timeout=5,
        ) as response:
            assert response.status == 200
            terminal = json.loads(response.read())
            assert terminal["choices"][0]["finish_reason"] == "stop"
            assert "MODEL_CALL_BUDGET_EXHAUSTED" in terminal["choices"][0][
                "message"
            ]["content"]
    finally:
        proxy.stop()
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=5)

    state = json.loads(info.state_path.read_text(encoding="utf-8"))
    assert state["schema_version"] == MODEL_CALL_BUDGET_SCHEMA
    assert state["requests_used"] == 2
    assert state["requests_remaining"] == 0
    assert state["rejected_after_limit"] == 1
    rows = [
        json.loads(line)
        for line in info.events_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [row["seq"] for row in rows] == [1, 2, 3]
    assert [row["admitted"] for row in rows] == [True, True, False]


def test_proxy_records_only_declared_provider_tool_definitions(tmp_path: Path) -> None:
    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    proxy = ModelCallBudgetProxy(
        budget={
            "version": "provider_requests_v1",
            "limit": 2,
            "finalization_reserve": 1,
        },
        task_tools={
            "tools": [
                {"name": "alpha", "description": "Alpha", "inputSchema": {"type": "object"}},
                {"name": "alpha_draft", "description": "Draft", "inputSchema": {"type": "object"}},
            ]
        },
        output_root=tmp_path,
        upstream_port=int(upstream.server_address[1]),
        listen_port=0,
    )
    info = proxy.start()
    body = json.dumps(
        {
            "model": "test",
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "mcp__harnessbench__alpha",
                        "description": "Production alpha",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "name": "mcp__harnessbench__alpha_draft",
                    "description": "Draft alpha",
                    "input_schema": {"type": "object", "properties": {}},
                },
                {
                    "type": "function",
                    "function": {
                        "name": "unrelated_builtin",
                        "description": "Not task scoped",
                        "parameters": {"type": "object"},
                    },
                },
            ],
        }
    ).encode("utf-8")
    try:
        request = Request(
            info.base_url + "/v1/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
            assert response.status == 200
    finally:
        proxy.stop()
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=5)

    event = json.loads(info.events_path.read_text(encoding="utf-8"))
    snapshot = event["provider_tool_snapshot"]
    assert snapshot["provider_tool_count"] == 3
    assert snapshot["observed_task_tool_count"] == 2
    assert snapshot["present_names"] == ["alpha", "alpha_draft"]
    assert snapshot["missing_names"] == []
    assert snapshot["duplicate_names"] == []
    assert [row["canonical_name"] for row in snapshot["tools"]] == [
        "alpha",
        "alpha_draft",
    ]
    assert all(row["input_schema"]["type"] == "object" for row in snapshot["tools"])


@pytest.mark.parametrize(
    ("path", "expected_type"),
    [
        ("/v1/chat/completions", "chat.completion"),
        ("/v1/responses", "response"),
        ("/v1/messages", "message"),
    ],
)
def test_terminal_response_matches_provider_protocol(
    path: str, expected_type: str
) -> None:
    payload = _terminal_response_payload(
        path,
        "MODEL_CALL_BUDGET_EXHAUSTED",
        {"seq": 19},
    )

    assert payload.get("object", payload.get("type")) == expected_type


def test_direct_upstream_replaces_harness_credential(tmp_path: Path) -> None:
    observed: dict[str, str] = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            observed["path"] = self.path
            observed["authorization"] = self.headers.get("Authorization", "")
            observed["x_api_key"] = self.headers.get("x-api-key", "")
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    proxy = ModelCallBudgetProxy(
        budget={
            "version": "provider_requests_v1",
            "limit": 2,
            "finalization_reserve": 1,
        },
        output_root=tmp_path,
        upstream_base_url=(
            f"http://127.0.0.1:{int(upstream.server_address[1])}/v1"
        ),
        upstream_api_key="real-provider-key",
        listen_port=0,
    )
    info = proxy.start()
    try:
        request = Request(
            info.base_url + "/v1/chat/completions?sample=1",
            data=b"{}",
            headers={
                "Authorization": "Bearer harness-budget-token",
                "x-api-key": "harness-budget-token",
            },
        )
        with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
            assert response.status == 200
    finally:
        proxy.stop()
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=5)

    assert observed == {
        "path": "/v1/chat/completions?sample=1",
        "authorization": "Bearer real-provider-key",
        "x_api_key": "",
    }
