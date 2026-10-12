#!/usr/bin/env python3
"""MCP server exposing two authoritative capabilities under one raw tool name."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any


PROBE_NAME = "hb_control_point_record"
CONTROL_NAME = "hb_control_point_pair_control"
CASE_ID = "GCP-2026-0808"
PRIMARY_TICKET = "PRI-SUR-74A1"
RECOVERY_TICKET = "REC-SUR-91C6"
PRIMARY_RESULT = {
    "role": "primary",
    "case_id": CASE_ID,
    "record_id": "GCP-P-417",
    "authorization_code": "S9-7K2M",
}
RECOVERY_RESULT = {
    "role": "recovery",
    "case_id": CASE_ID,
    "record_id": "GCP-R-882",
    "authorization_code": "D4-1Q8X",
}
CONTROL_RESULT = {
    "case_id": CASE_ID,
    "verification_id": "SUR-CTRL-55D9",
    "pair_digest": "GCP-PAIR-6F2A",
    "verified": True,
}
LOG_PATH = Path(
    os.environ.get(
        "HARNESSBENCH_DUPLICATE_PROBE_LOG",
        "/hb-output/native/duplicate_probe.jsonl",
    )
)

TOOLS = [
    {
        "name": PROBE_NAME,
        "description": (
            "Authoritative PRIMARY geodetic-ledger lookup. Use only for the "
            f"primary survey record of control point {CASE_ID}; submit the schema's primary_ticket exactly."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "case_id": {"type": "string", "enum": [CASE_ID]},
                "primary_ticket": {"type": "string", "enum": [PRIMARY_TICKET]},
            },
            "required": ["case_id", "primary_ticket"],
            "additionalProperties": False,
        },
    },
    {
        "name": PROBE_NAME,
        "description": (
            "Authoritative DISASTER-RECOVERY geodetic-ledger lookup. Use only for the "
            f"recovery survey record of control point {CASE_ID}; submit the schema's recovery_ticket exactly."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "case_id": {"type": "string", "enum": [CASE_ID]},
                "recovery_ticket": {"type": "string", "enum": [RECOVERY_TICKET]},
            },
            "required": ["case_id", "recovery_ticket"],
            "additionalProperties": False,
        },
    },
    {
        "name": CONTROL_NAME,
        "description": (
            "Unique geodetic pair-validation control. Call only after both authoritative "
            "control-point records are available, using their authorization codes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "case_id": {"type": "string", "enum": [CASE_ID]},
                "primary_authorization": {"type": "string"},
                "recovery_authorization": {"type": "string"},
            },
            "required": [
                "case_id",
                "primary_authorization",
                "recovery_authorization",
            ],
            "additionalProperties": False,
        },
    },
]

ACCEPTED_ROLES: set[str] = set()


def log(event: str, **payload: Any) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "schema_version": "harnessbench_duplicate_tool_probe_v2",
        "event": event,
        "pid": os.getpid(),
        "time_ns": time.time_ns(),
        **payload,
    }
    with LOG_PATH.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def result(identifier: Any, value: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": identifier, "result": value}


def error(identifier: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": identifier,
        "error": {"code": code, "message": message},
    }


def tool_result(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            }
        ],
        "isError": is_error,
    }


def reject_call(identifier: Any, name: Any, arguments: Any, reason: str) -> dict[str, Any]:
    log("call_rejected", name=name, arguments=arguments, reason=reason)
    return result(identifier, tool_result({"error": reason}, is_error=True))


def dispatch_call(identifier: Any, name: Any, arguments: Any) -> dict[str, Any]:
    log("tool_called", name=name, arguments=arguments)
    if not isinstance(arguments, dict):
        return reject_call(identifier, name, arguments, "arguments must be an object")

    role: str | None = None
    payload: dict[str, Any] | None = None
    if name == PROBE_NAME and set(arguments) == {"case_id", "primary_ticket"}:
        if arguments == {"case_id": CASE_ID, "primary_ticket": PRIMARY_TICKET}:
            role, payload = "primary", PRIMARY_RESULT
        else:
            return reject_call(identifier, name, arguments, "invalid primary request")
    elif name == PROBE_NAME and set(arguments) == {"case_id", "recovery_ticket"}:
        if arguments == {"case_id": CASE_ID, "recovery_ticket": RECOVERY_TICKET}:
            role, payload = "recovery", RECOVERY_RESULT
        else:
            return reject_call(identifier, name, arguments, "invalid recovery request")
    elif name == CONTROL_NAME:
        expected = {
            "case_id": CASE_ID,
            "primary_authorization": PRIMARY_RESULT["authorization_code"],
            "recovery_authorization": RECOVERY_RESULT["authorization_code"],
        }
        if arguments == expected:
            role, payload = "control", CONTROL_RESULT
        else:
            return reject_call(identifier, name, arguments, "pair validation failed")
    else:
        return reject_call(identifier, name, arguments, f"unknown or mismatched tool: {name}")

    assert role is not None and payload is not None
    if role in ACCEPTED_ROLES:
        return reject_call(identifier, name, arguments, f"{role} capability is single-use")
    ACCEPTED_ROLES.add(role)
    log("call_accepted", name=name, logical_role=role, arguments=arguments, result=payload)
    return result(identifier, tool_result(payload))


def dispatch(request: dict[str, Any]) -> dict[str, Any] | None:
    identifier = request.get("id")
    method = request.get("method")
    if method == "initialize":
        params = request.get("params")
        requested = params.get("protocolVersion") if isinstance(params, dict) else None
        protocol = requested if isinstance(requested, str) and requested else "2024-11-05"
        log("initialize_requested", protocol_version=protocol)
        return result(
            identifier,
            {
                "protocolVersion": protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "HarnessBench Duplicate Geodetic Control-Point Registry",
                    "version": "2.0",
                },
            },
        )
    if method == "notifications/initialized":
        log("initialized_notification")
        return None
    if method == "ping":
        return result(identifier, {})
    if method == "tools/list":
        log(
            "catalog_served",
            names=[tool["name"] for tool in TOOLS],
            descriptions=[tool["description"] for tool in TOOLS],
            required_fields=[tool["inputSchema"].get("required", []) for tool in TOOLS],
            duplicate_count=sum(tool["name"] == PROBE_NAME for tool in TOOLS),
        )
        return result(identifier, {"tools": TOOLS})
    if method == "tools/call":
        params = request.get("params")
        name = params.get("name") if isinstance(params, dict) else None
        arguments = params.get("arguments") if isinstance(params, dict) else None
        return dispatch_call(identifier, name, arguments)
    if identifier is None:
        log("notification_ignored", method=method)
        return None
    return error(identifier, -32601, f"method not found: {method}")


def main() -> int:
    log("server_started")
    for line_number, line in enumerate(sys.stdin, 1):
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("request is not an object")
            response = dispatch(request)
        except Exception as exc:
            log("request_error", line_number=line_number, error=f"{type(exc).__name__}: {exc}")
            response = error(None, -32700, "invalid JSON-RPC request")
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    log("server_stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
