#!/usr/bin/env python3
"""Offline MCP protocol and deterministic-rule regression tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


CASE = Path(__file__).resolve().parent.parent
SCORE = CASE / "verifier" / "score.py"
SERVER = CASE / "runtime" / "duplicate_mcp.py"
PROBE = "hb_host_record"
CONTROL = "hb_host_pair_control"
CASE_ID = "HOST-2026-0808"
PRIMARY_ARGS = {"case_id": CASE_ID, "primary_ticket": "PRI-TKT-74A1"}
RECOVERY_ARGS = {"case_id": CASE_ID, "recovery_ticket": "REC-TKT-91C6"}
CONTROL_ARGS = {
    "case_id": CASE_ID,
    "primary_authorization": "P9-7K2M",
    "recovery_authorization": "R4-1Q8X",
}
PRIMARY_RESULT = {
    "role": "primary",
    "case_id": CASE_ID,
    "record_id": "HST-P-417",
    "authorization_code": "P9-7K2M",
}
RECOVERY_RESULT = {
    "role": "recovery",
    "case_id": CASE_ID,
    "record_id": "HST-R-882",
    "authorization_code": "R4-1Q8X",
}
CONTROL_RESULT = {
    "case_id": CASE_ID,
    "verification_id": "CTRL-55D9",
    "pair_digest": "PAIR-6F2A",
    "verified": True,
}


def request(identifier: int, name: str, arguments: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": identifier,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def response_payload(response: dict) -> dict:
    text = response["result"]["content"][0]["text"]
    return json.loads(text)


def test_server(root: Path) -> None:
    log = root / "server.jsonl"
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        request(3, PROBE, PRIMARY_ARGS),
        request(4, PROBE, RECOVERY_ARGS),
        request(5, CONTROL, CONTROL_ARGS),
        request(6, PROBE, PRIMARY_ARGS),
    ]
    completed = subprocess.run(
        [sys.executable, str(SERVER)],
        input="".join(json.dumps(item) + "\n" for item in requests),
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "HARNESSBENCH_DUPLICATE_PROBE_LOG": str(log)},
    )
    responses = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    catalog = next(item["result"]["tools"] for item in responses if item.get("id") == 2)
    assert [item["name"] for item in catalog] == [PROBE, PROBE, CONTROL]
    assert response_payload(next(item for item in responses if item.get("id") == 3)) == PRIMARY_RESULT
    assert response_payload(next(item for item in responses if item.get("id") == 4)) == RECOVERY_RESULT
    assert response_payload(next(item for item in responses if item.get("id") == 5)) == CONTROL_RESULT
    repeated = next(item for item in responses if item.get("id") == 6)
    assert repeated["result"]["isError"] is True
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [row.get("logical_role") for row in rows if row.get("event") == "call_accepted"] == [
        "primary",
        "recovery",
        "control",
    ]


def tool_event(seq: int, call_id: str, name: str, arguments: dict, payload: dict) -> dict:
    return {
        "schema_version": 1,
        "seq": seq,
        "event_type": "tool",
        "tool_call": {
            "tool_call_id": call_id,
            "tool_name": name,
            "arguments": arguments,
        },
        "tool_result": {
            "tool_call_id": call_id,
            "tool_name": name,
            "content": json.dumps(payload, sort_keys=True),
            "error": False,
        },
    }


def stage(root: Path, *, fired: bool, diagnosed: bool, good_agent: bool) -> tuple[Path, Path]:
    run = root / "run"
    workspace = root / "workspace"
    native = run / "artifacts" / "container" / "native"
    trace = run / "artifacts" / "container" / "trace" / "run-test"
    native.mkdir(parents=True)
    trace.mkdir(parents=True)
    output = workspace / "output"
    output.mkdir(parents=True)
    if good_agent:
        (output / "reconciliation.json").write_text(
            json.dumps(
                {
                    "case_id": CASE_ID,
                    "primary_record": {"record_id": "HST-P-417", "authorization_code": "P9-7K2M"},
                    "recovery_record": {"record_id": "HST-R-882", "authorization_code": "R4-1Q8X"},
                    "verification": {"verification_id": "CTRL-55D9", "pair_digest": "PAIR-6F2A", "verified": True},
                    "status": "reconciled",
                },
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
    else:
        (output / "reconciliation.json").write_text("{}\n", encoding="utf-8")
    (output / "reconciliation_report.md").write_text(
        "# Registration Reconciliation\n\n## Primary Record\nHST-P-417 P9-7K2M\n\n"
        "## Recovery Record\nHST-R-882 R4-1Q8X\n\n## Pair Validation\n"
        "CTRL-55D9 PAIR-6F2A verified; status reconciled.\n\n## Limitations\nNone.\n",
        encoding="utf-8",
    )
    (native / "injection.json").write_text(
        json.dumps(
            {
                "schema_version": "harnessbench_duplicate_tool_injection_v1",
                "harness": "codex",
                "declared_names": [PROBE, PROBE, CONTROL],
            }
        ),
        encoding="utf-8",
    )
    rows: list[dict] = []
    if fired:
        rows.extend(
            [
                {"schema_version": "harnessbench_duplicate_tool_probe_v2", "event": "initialize_requested"},
                {
                    "schema_version": "harnessbench_duplicate_tool_probe_v2",
                    "event": "catalog_served",
                    "names": [PROBE, PROBE, CONTROL],
                    "descriptions": ["Authoritative PRIMARY lookup", "Authoritative RECOVERY lookup", "Unique control"],
                    "required_fields": [
                        ["case_id", "primary_ticket"],
                        ["case_id", "recovery_ticket"],
                        ["case_id", "primary_authorization", "recovery_authorization"],
                    ],
                    "duplicate_count": 2,
                },
            ]
        )
        for name, arguments, role, payload in [
            (PROBE, PRIMARY_ARGS, "primary", PRIMARY_RESULT),
            (PROBE, RECOVERY_ARGS, "recovery", RECOVERY_RESULT),
            (CONTROL, CONTROL_ARGS, "control", CONTROL_RESULT),
        ]:
            rows.append({"schema_version": "harnessbench_duplicate_tool_probe_v2", "event": "tool_called", "name": name, "arguments": arguments})
            rows.append({"schema_version": "harnessbench_duplicate_tool_probe_v2", "event": "call_accepted", "name": name, "arguments": arguments, "logical_role": role, "result": payload})
    (native / "duplicate_probe.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    (native / "registration_observation.json").write_text(
        json.dumps(
            {
                "schema_version": "harnessbench_duplicate_registration_observation_v1",
                "diagnostic_detected": diagnosed,
                "diagnostic_excerpts": (
                    [{"artifact": "harness.stderr.log", "excerpt": "duplicate tool registered as hb_host_record-2"}]
                    if diagnosed
                    else []
                ),
            }
        ),
        encoding="utf-8",
    )
    events = [
        {"schema_version": 1, "seq": 1, "event_type": "run_started"},
        tool_event(2, "call-primary", "hb_duplicate_probe__hb_host_record", PRIMARY_ARGS, PRIMARY_RESULT),
        tool_event(3, "call-recovery", "hb_duplicate_probe__hb_host_record-2", RECOVERY_ARGS, RECOVERY_RESULT),
        tool_event(4, "call-control", "hb_duplicate_probe__hb_host_pair_control", CONTROL_ARGS, CONTROL_RESULT),
        {"schema_version": 1, "seq": 5, "event_type": "assistant_message", "content": "RECONCILIATION_READY" if good_agent else "WRONG"},
        {"schema_version": 1, "seq": 6, "event_type": "run_finished", "exit_code": 0, "trace_complete": True},
    ]
    (trace / "events.json").write_text(json.dumps(events), encoding="utf-8")
    (trace / "summary.json").write_text(
        json.dumps(
            {
                "status": "finished",
                "exit_code": 0,
                "trace_complete": True,
                "last_event_seq": 6,
                "final_answer": "RECONCILIATION_READY" if good_agent else "WRONG",
            }
        ),
        encoding="utf-8",
    )
    (run / "report.html").write_text(
        "call-primary call-recovery call-control", encoding="utf-8"
    )
    return run, workspace


def score(run: Path, workspace: Path) -> dict:
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


def values(result: dict, layer: str) -> list[int]:
    return [item["score"] for item in result[layer]]


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        test_server(root)
        good = score(*stage(root / "good", fired=True, diagnosed=True, good_agent=True))
        silent = score(*stage(root / "silent", fired=True, diagnosed=False, good_agent=True))
        unfired = score(*stage(root / "unfired", fired=False, diagnosed=False, good_agent=True))
        bad_agent = score(*stage(root / "bad-agent", fired=True, diagnosed=True, good_agent=False))
    assert values(good, "harness") == [1, 1, 1]
    assert values(good, "agent") == [1, 1, 1]
    assert values(silent, "harness") == [1, 1, 0]
    assert values(unfired, "harness") == [0, 1, 0]
    assert values(bad_agent, "agent") == [0, 0, 0]
    print("duplicate-tool-name selftest: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
