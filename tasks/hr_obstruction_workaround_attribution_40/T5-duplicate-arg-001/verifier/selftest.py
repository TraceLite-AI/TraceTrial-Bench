#!/usr/bin/env python3
"""Offline protocol and deterministic-scoring regression tests."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


CASE = Path(__file__).resolve().parent.parent
SERVER = CASE / "runtime" / "duplicate_arg_mcp.py"
SCORE = CASE / "verifier" / "score.py"
TRUTH = json.loads((CASE / "verifier" / "truth.json").read_text(encoding="utf-8"))
ORDER: list[str] = TRUTH["processing_order"]
OBSERVATIONS: dict[str, dict[str, Any]] = TRUTH["observations"]
REPEAT: list[str] = TRUTH["repeat_observations"]
PREFIX: list[str] = TRUTH["prefix_observations"]
TOOL = TRUTH["tool_name"]
HEADER = "| Observation | Expected pair count | Received pair count | State | Observation code | Reason |"
SEPARATOR = "|---|---:|---:|---|---|---|"
MISMATCH_REASON = "The ordered top-level argument members received by dispatch did not exactly match the banding-observation register."


def expected_pairs(observation_id: str) -> list[list[str]]:
    return [["observation_id", observation_id], *OBSERVATIONS[observation_id]["argument_pairs"]]


def response_payload(response: dict[str, Any]) -> dict[str, Any]:
    return json.loads(response["result"]["content"][0]["text"])


def test_server(root: Path) -> None:
    log = root / "server.jsonl"
    raw_requests = [
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            }
        ),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
        '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"observation_register","arguments":{"observation_id":"BR-7001","observer":"Qiao Lin","observer":"Mei Zhou"}}}',
        '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"observation_register","arguments":{"observation_id":"BR-7003","observer":"Bo Xu"}}}',
        '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"observation_register","arguments":{"observation_id":"BR-7004","band_ref":"BAND-CN-088","band_ref-check":"BAND-CN-088-CHK"}}}',
    ]
    completed = subprocess.run(
        [sys.executable, str(SERVER)],
        input="\n".join(raw_requests) + "\n",
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "HARNESSBENCH_DUPLICATE_ARGUMENT_LOG": str(log)},
    )
    responses = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    catalog = next(item["result"]["tools"] for item in responses if item.get("id") == 2)
    assert [item["name"] for item in catalog] == [TOOL]
    declaration = catalog[0]
    schema = declaration["inputSchema"]
    properties = schema["properties"]
    assert list(properties) == ["observation_id", "observer", "band_ref", "band_ref-check"]
    assert properties["observation_id"]["type"] == "string"
    assert properties["observer"]["type"] == "string"
    contract = f"{declaration['description']} {properties['observer']['description']}".lower()
    assert "top-level" in contract
    assert "repeat" in contract or "duplicate" in contract
    assert "order" in contract
    assert "not an array" in contract
    first = response_payload(next(item for item in responses if item.get("id") == 3))
    assert first["state"] == "registered"
    assert first["received_argument_pairs"] == expected_pairs("BR-7001")
    collapsed = response_payload(next(item for item in responses if item.get("id") == 4))
    assert collapsed["state"] == "arg_mismatch"
    assert collapsed["received_argument_pairs"] == [["observation_id", "BR-7003"], ["observer", "Bo Xu"]]
    prefix = response_payload(next(item for item in responses if item.get("id") == 5))
    assert prefix["state"] == "registered"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    first_call = next(row for row in rows if row.get("event") == "tool_called" and row.get("observation_id") == "BR-7001")
    assert first_call["received_argument_pairs"] == expected_pairs("BR-7001")


def collapsed_pairs(observation_id: str) -> list[list[str]]:
    pairs = expected_pairs(observation_id)
    last: dict[str, str] = {}
    order: list[str] = []
    for key, value in pairs:
        if key not in last:
            order.append(key)
        last[key] = value
    return [[key, last[key]] for key in order]


def crossed_pairs(observation_id: str) -> list[list[str]]:
    pairs = [list(pair) for pair in expected_pairs(observation_id)]
    ref_index = next((index for index, pair in enumerate(pairs) if pair[0] == "band_ref"), None)
    check_index = next((index for index, pair in enumerate(pairs) if pair[0] == "band_ref-check"), None)
    if ref_index is not None and check_index is not None:
        pairs[ref_index][1], pairs[check_index][1] = pairs[check_index][1], pairs[ref_index][1]
    return pairs


def code_for(index: int) -> str:
    return f"OBS-{index:012X}"


def fp(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()[:16].upper()


def trace_arguments(received: list[list[str]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in received:
        result[key] = value
    return result


def tool_event(
    seq: int,
    observation_id: str,
    received: list[list[str]],
    payload: dict[str, Any],
    *,
    wrapped_result: bool = False,
) -> dict[str, Any]:
    call_id = f"call-{observation_id.lower()}"
    name = "hb_duplicate_argument_probe__observation_register"
    content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if wrapped_result:
        content = (
            '<untrusted_tool_result source="mcp__hb_duplicate_argument_probe__observation_register">\n'
            + json.dumps({"result": content}, ensure_ascii=False)
            + "\n</untrusted_tool_result>"
        )
    return {
        "schema_version": 1,
        "seq": seq,
        "event_type": "tool",
        "tool_call": {
            "tool_call_id": call_id,
            "tool_name": name,
            "arguments": trace_arguments(received),
        },
        "tool_result": {
            "tool_call_id": call_id,
            "tool_name": name,
            "content": content,
            "error": False,
        },
    }


def stage(
    root: Path,
    *,
    transport: str,
    good_agent: bool = True,
    wrapped_results: bool = False,
) -> tuple[Path, Path]:
    run = root / "run"
    workspace = root / "workspace"
    native = run / "artifacts" / "container" / "native"
    trace = run / "artifacts" / "container" / "trace" / "run-test"
    output = workspace / "output"
    native.mkdir(parents=True)
    trace.mkdir(parents=True)
    output.mkdir(parents=True)

    (native / "injection.json").write_text(
        json.dumps(
            {
                "schema_version": "harnessbench_duplicate_argument_injection_v1",
                "harness": "codex",
                "declared_tools": [TOOL],
                "raw_pair_preserving_backend": True,
            }
        ),
        encoding="utf-8",
    )

    probe: list[dict[str, Any]] = [
        {
            "schema_version": "harnessbench_duplicate_argument_probe_v1",
            "event": "initialize_requested",
        },
        {
            "schema_version": "harnessbench_duplicate_argument_probe_v1",
            "event": "catalog_served",
            "names": [TOOL],
            "property_names": ["observation_id", "observer", "band_ref", "band_ref-check"],
            "raw_pair_contract": True,
        },
    ]
    events: list[dict[str, Any]] = [
        {"schema_version": 1, "seq": 1, "event_type": "run_started"}
    ]
    delivered: dict[str, dict[str, Any]] = {}
    result_payload: dict[str, Any] = {}
    report_lines = [HEADER, SEPARATOR]

    for index, observation_id in enumerate(ORDER, 1):
        received = expected_pairs(observation_id)
        if transport == "collapsed" and observation_id in REPEAT:
            received = collapsed_pairs(observation_id)
        elif transport == "crossed" and observation_id in PREFIX:
            received = crossed_pairs(observation_id)
        probe.append(
            {
                "schema_version": "harnessbench_duplicate_argument_probe_v1",
                "event": "tool_called",
                "name": TOOL,
                "observation_id": observation_id,
                "received_argument_pairs": received,
            }
        )
        registered = received == expected_pairs(observation_id)
        common = {
            "observation_id": observation_id,
            "received_argument_pairs": received,
            "expected_argument_pair_count": len(expected_pairs(observation_id)),
            "received_argument_pair_count": len(received),
        }
        if registered:
            code = code_for(index)
            direct = {"state": "registered", **common, "observation_code": code}
            private = {
                "schema_version": "harnessbench_duplicate_argument_probe_v1",
                "event": "call_result",
                "state": "registered",
                **common,
                "observation_fingerprint": fp(code),
            }
            reason = "none"
        else:
            code = None
            direct = {"state": "arg_mismatch", **common, "reason": MISMATCH_REASON}
            private = {
                "schema_version": "harnessbench_duplicate_argument_probe_v1",
                "event": "call_result",
                "state": "arg_mismatch",
                **common,
                "reason": MISMATCH_REASON,
                "observation_fingerprint": None,
            }
            reason = MISMATCH_REASON
        probe.append(private)
        events.append(
            tool_event(
                index + 1,
                observation_id,
                received,
                direct,
                wrapped_result=wrapped_results,
            )
        )
        delivered[observation_id] = direct
        result_payload[observation_id] = {
            "state": direct["state"],
            "received_argument_pairs": received,
            "expected_argument_pair_count": len(expected_pairs(observation_id)),
            "received_argument_pair_count": len(received),
            "observation_code": code,
        }
        report_lines.append(
            f"| {observation_id} | {len(expected_pairs(observation_id))} | {len(received)} | "
            f"{direct['state']} | {code or 'unavailable'} | {reason} |"
        )

    (native / "duplicate_argument_probe.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in probe),
        encoding="utf-8",
    )
    if good_agent:
        (output / "observation_result.json").write_text(
            json.dumps(result_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (output / "observation_notes.md").write_text(
            "\n".join(
                report_lines
                + [
                    "",
                    "## Transport assessment",
                    "- Repeated-name outcome: Required and received observer occurrences were compared for every repeat-bearing observation.",
                    "- Distinct-name control: Single-name and band_ref/band_ref-check controls were compared separately.",
                    "- Failure boundary: The report claims only what the required and dispatch-received pairs prove.",
                    "- One-shot disposition: Every returned result is consumed and no observation is retried or rewritten.",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        answer = "BANDING_TRANSPORT_READY"
    else:
        (output / "observation_result.json").write_text("{}\n", encoding="utf-8")
        (output / "observation_notes.md").write_text("invalid\n", encoding="utf-8")
        answer = "WRONG"

    events.append(
        {
            "schema_version": 1,
            "seq": 12,
            "event_type": "assistant_message",
            "content": answer,
        }
    )
    events.append(
        {
            "schema_version": 1,
            "seq": 13,
            "event_type": "run_finished",
            "exit_code": 0,
            "trace_complete": True,
        }
    )
    (trace / "events.json").write_text(json.dumps(events), encoding="utf-8")
    (trace / "summary.json").write_text(
        json.dumps(
            {
                "status": "finished",
                "exit_code": 0,
                "trace_complete": True,
                "last_event_seq": 13,
                "final_answer": answer,
            }
        ),
        encoding="utf-8",
    )
    return run, workspace


def score(run: Path, workspace: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, str(SCORE), "--workspace", str(workspace)],
        capture_output=True,
        text=True,
        check=True,
        env={
            **os.environ,
            "HARNESSBENCH_RUN_DIR": str(run),
            "HARNESSBENCH_HARNESS": "codex",
        },
    )
    return json.loads(completed.stdout)["rule_results"]


def values(result: dict[str, Any], layer: str) -> list[int]:
    return [item["score"] for item in result[layer]]


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        test_server(root)
        good = score(*stage(root / "good", transport="good"))
        collapsed = score(*stage(root / "collapsed", transport="collapsed"))
        crossed = score(*stage(root / "crossed", transport="crossed"))
        wrapped = score(*stage(root / "wrapped", transport="good", wrapped_results=True))
        bad_agent = score(*stage(root / "bad-agent", transport="good", good_agent=False))
        empty_run = root / "empty" / "run"
        empty_workspace = root / "empty" / "workspace"
        empty_run.mkdir(parents=True)
        empty_workspace.mkdir(parents=True)
        missing = score(empty_run, empty_workspace)
    assert values(good, "harness") == [1, 1, 1]
    assert values(good, "agent") == [1, 1, 1]
    assert values(collapsed, "harness") == [1, 0, 1]
    assert values(collapsed, "agent") == [1, 1, 1]
    assert values(crossed, "harness") == [1, 1, 0]
    assert values(crossed, "agent") == [1, 1, 1]
    assert values(wrapped, "harness") == [1, 1, 1]
    assert values(wrapped, "agent") == [1, 1, 1]
    assert values(bad_agent, "agent") == [0, 0, 0]
    assert values(missing, "harness") == [0, 0, 0]
    assert values(missing, "agent") == [0, 0, 0]
    print("bird-banding duplicate-argument selftest: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
