#!/usr/bin/env python3
"""Raw-pair-preserving MCP backend for native duplicate-argument transport."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any


TOOL_NAME = "observation_register"
LOG_PATH = Path(
    os.environ.get(
        "HARNESSBENCH_DUPLICATE_ARGUMENT_LOG",
        "/hb-output/native/duplicate_argument_probe.jsonl",
    )
)

EXPECTED: dict[str, list[list[str]]] = {
    "BR-7001": [["observer", "Qiao Lin"], ["observer", "Mei Zhou"]],
    "BR-7002": [["observer", "Han Yu"]],
    "BR-7003": [["observer", "Ari Chen"], ["observer", "Rui Tang"], ["observer", "Bo Xu"]],
    "BR-7004": [["band_ref", "BAND-CN-088"], ["band_ref-check", "BAND-CN-088-CHK"]],
    "BR-7005": [["band_ref", "BAND-CN-091"]],
    "BR-7006": [["band_ref", "BAND-CN-093"], ["band_ref-check", "BAND-CN-093-CHK"], ["observer", "Ning Guo"], ["observer", "Jia Wen"]],
    "BR-7007": [["observer", "Tao Sun"]],
    "BR-7008": [["band_ref", "BAND-CN-097"], ["band_ref-check", "BAND-CN-097-CHK"]],
    "BR-7009": [["observer", "Le Qi"], ["observer", "Ming He"], ["band_ref", "BAND-CN-099"], ["band_ref-check", "BAND-CN-099-CHK"]],
    "BR-7010": [["observer", "Yan Luo"]],
}

TOOL = {
    "name": TOOL_NAME,
    "description": (
        "Authoritative one-shot channel for a migratory-bird banding station. Its observation register may require "
        "the same top-level JSON member name more than once. Preserve raw top-level member "
        "order and duplicate observer members exactly; observer is not an array. band_ref and band_ref-check "
        "are distinct exact names. The result reports the ordered argument pairs received by "
        "dispatch."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "observation_id": {"type": "string", "enum": list(EXPECTED)},
            "observer": {
                "type": "string",
                "description": "One observer occurrence. Repeat this raw top-level member when the observation lists multiple observers.",
            },
            "band_ref": {"type": "string"},
            "band_ref-check": {"type": "string"},
        },
        "required": ["observation_id"],
        "additionalProperties": False,
    },
}


class PairObject:
    """JSON object represented as ordered pairs, including duplicate names."""

    def __init__(self, pairs: list[tuple[str, Any]]) -> None:
        self.pairs = pairs

    def get(self, name: str, default: Any = None) -> Any:
        values = [value for key, value in self.pairs if key == name]
        return values[-1] if values else default

    def values(self, name: str) -> list[Any]:
        return [value for key, value in self.pairs if key == name]


def log(event: str, **payload: Any) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "schema_version": "harnessbench_duplicate_argument_probe_v1",
        "event": event,
        "pid": os.getpid(),
        "time_ns": time.time_ns(),
        **payload,
    }
    with LOG_PATH.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def rpc_result(identifier: Any, value: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": identifier, "result": value}


def rpc_error(identifier: Any, code: int, message: str) -> dict[str, Any]:
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
                "text": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            }
        ],
        "isError": is_error,
    }


def scalar_pairs(arguments: Any) -> list[list[str]]:
    if not isinstance(arguments, PairObject):
        return []
    pairs: list[list[str]] = []
    for key, value in arguments.pairs:
        if isinstance(value, str):
            pairs.append([key, value])
        else:
            pairs.append([key, json.dumps(render(value), ensure_ascii=False, separators=(",", ":"))])
    return pairs


def render(value: Any) -> Any:
    if isinstance(value, PairObject):
        return [[key, render(child)] for key, child in value.pairs]
    if isinstance(value, list):
        return [render(child) for child in value]
    return value


ATTEMPTED: set[str] = set()


def dispatch_call(identifier: Any, name: Any, arguments: Any) -> dict[str, Any]:
    received = scalar_pairs(arguments)
    observation_values = arguments.values("observation_id") if isinstance(arguments, PairObject) else []
    observation_id = observation_values[0] if len(observation_values) == 1 and isinstance(observation_values[0], str) else None
    log("tool_called", name=name, observation_id=observation_id, received_argument_pairs=received)

    if name != TOOL_NAME:
        return rpc_result(identifier, tool_result({"error": f"unknown tool: {name}"}, is_error=True))

    expected_pairs = (
        [["observation_id", observation_id], *EXPECTED[observation_id]]
        if observation_id in EXPECTED
        else []
    )
    expected_count = len(expected_pairs) if expected_pairs else None
    base = {
        "observation_id": observation_id,
        "received_argument_pairs": received,
        "expected_argument_pair_count": expected_count,
        "received_argument_pair_count": len(received),
    }

    reason = ""
    if observation_id is None or observation_id not in EXPECTED:
        reason = "The dispatch request did not contain exactly one recognized observation_id member."
    elif observation_id in ATTEMPTED:
        reason = "This observation was already attempted; the observation channel is one-shot."
    elif received != expected_pairs:
        reason = "The ordered top-level argument members received by dispatch did not exactly match the banding-observation register."

    if observation_id in EXPECTED:
        ATTEMPTED.add(observation_id)

    if reason:
        payload = {"state": "arg_mismatch", **base, "reason": reason}
        log("call_result", **payload, observation_fingerprint=None)
        return rpc_result(identifier, tool_result(payload))

    code = "OBS-" + "".join(secrets.choice("0123456789ABCDEF") for _ in range(12))
    fingerprint = hashlib.sha256(code.encode("utf-8")).hexdigest()[:16].upper()
    payload = {"state": "registered", **base, "observation_code": code}
    log(
        "call_result",
        state="registered",
        observation_id=observation_id,
        received_argument_pairs=received,
        expected_argument_pair_count=expected_count,
        received_argument_pair_count=len(received),
        observation_fingerprint=fingerprint,
    )
    return rpc_result(identifier, tool_result(payload))


def dispatch(request: PairObject) -> dict[str, Any] | None:
    identifier = request.get("id")
    method = request.get("method")
    if method == "initialize":
        params = request.get("params")
        requested = params.get("protocolVersion") if isinstance(params, PairObject) else None
        protocol = requested if isinstance(requested, str) and requested else "2024-11-05"
        log("initialize_requested", protocol_version=protocol)
        return rpc_result(
            identifier,
            {
                "protocolVersion": protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "HarnessBench Migratory Bird Banding Observation Register",
                    "version": "1.0",
                },
            },
        )
    if method == "notifications/initialized":
        log("initialized_notification")
        return None
    if method == "ping":
        return rpc_result(identifier, {})
    if method == "tools/list":
        log(
            "catalog_served",
            names=[TOOL_NAME],
            property_names=list(TOOL["inputSchema"]["properties"]),
            raw_pair_contract=True,
        )
        return rpc_result(identifier, {"tools": [TOOL]})
    if method == "tools/call":
        params = request.get("params")
        name = params.get("name") if isinstance(params, PairObject) else None
        arguments = params.get("arguments") if isinstance(params, PairObject) else None
        return dispatch_call(identifier, name, arguments)
    if identifier is None:
        log("notification_ignored", method=method)
        return None
    return rpc_error(identifier, -32601, f"method not found: {method}")


def main() -> int:
    log("server_started")
    for line_number, line in enumerate(sys.stdin, 1):
        if not line.strip():
            continue
        try:
            request = json.loads(line, object_pairs_hook=PairObject)
            if not isinstance(request, PairObject):
                raise ValueError("request is not an object")
            response = dispatch(request)
        except Exception as exc:
            log("request_error", line_number=line_number, error=f"{type(exc).__name__}: {exc}")
            response = rpc_error(None, -32700, "invalid JSON-RPC request")
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    log("server_stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
