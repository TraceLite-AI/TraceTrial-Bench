#!/usr/bin/env python3
"""Offline MCP protocol and deterministic-rule regression tests."""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


CASE = Path(__file__).resolve().parent.parent
SCORE = CASE / "verifier" / "score.py"
SERVER = CASE / "runtime" / "duplicate_mcp.py"
WORKER = CASE / "runtime" / "native_worker.py"
PROBE = "hb_control_point_record"
CONTROL = "hb_control_point_pair_control"
CASE_ID = "GCP-2026-0808"
PRIMARY_ARGS = {"case_id": CASE_ID, "primary_ticket": "PRI-SUR-74A1"}
RECOVERY_ARGS = {"case_id": CASE_ID, "recovery_ticket": "REC-SUR-91C6"}
CONTROL_ARGS = {
    "case_id": CASE_ID,
    "primary_authorization": "S9-7K2M",
    "recovery_authorization": "D4-1Q8X",
}
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


def catalog_tools(mode: str) -> list[dict]:
    primary = {
        "name": "hb_duplicate_probe__hb_control_point_record",
        "description": "Authoritative PRIMARY geodetic-ledger lookup.",
        "input_schema": {
            "type": "object",
            "properties": {
                "case_id": {"type": "string", "enum": [CASE_ID]},
                "primary_ticket": {"type": "string", "enum": ["PRI-SUR-74A1"]},
            },
            "required": ["case_id", "primary_ticket"],
            "additionalProperties": False,
        },
    }
    recovery = {
        "name": "hb_duplicate_probe__hb_control_point_record-2",
        "description": "Authoritative DISASTER-RECOVERY geodetic-ledger lookup.",
        "input_schema": {
            "type": "object",
            "properties": {
                "case_id": {"type": "string", "enum": [CASE_ID]},
                "recovery_ticket": {"type": "string", "enum": ["REC-SUR-91C6"]},
            },
            "required": ["case_id", "recovery_ticket"],
            "additionalProperties": False,
        },
    }
    control = {
        "name": "hb_duplicate_probe__hb_control_point_pair_control",
        "description": "Unique geodetic pair-validation control.",
        "input_schema": {
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
    }
    if mode == "renamed":
        return [primary, recovery, control]
    if mode == "collapsed":
        recovery["name"] = primary["name"]
        return [recovery, control]
    if mode == "duplicate":
        recovery["name"] = primary["name"]
        return [primary, recovery, control]
    raise ValueError(f"unknown catalog mode: {mode}")


def inventory_event(seq: int, mode: str) -> dict:
    return {
        "schema_version": 1,
        "seq": seq,
        "event_type": "tool_inventory",
        "source": "provider_request",
        "model_visible": True,
        "complete": True,
        "tools": catalog_tools(mode),
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


def test_registration_diagnostic_pattern() -> None:
    tree = ast.parse(WORKER.read_text(encoding="utf-8"))
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "DIAGNOSTIC" for target in node.targets)
    )
    pattern = ast.literal_eval(assignment.value.args[0])
    diagnostic = re.compile(pattern, re.IGNORECASE)
    assert diagnostic.search("duplicate tool registered as hb_control_point_record-2")
    assert diagnostic.search(
        "hb_control_point_record was rejected because the tool name conflicts"
    )
    assert not diagnostic.search("duplicate tool registration completed")
    assert not diagnostic.search(
        "hb_control_point_record registered as mcp__registry__hb_control_point_record"
    )


def tool_event(
    seq: int,
    call_id: str,
    name: str,
    arguments: dict,
    payload: dict,
    *,
    error: bool = False,
) -> dict:
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
            "error": error,
        },
    }


def stage(
    root: Path,
    *,
    fired: bool,
    diagnosed: bool,
    good_agent: bool,
    catalog_mode: str = "renamed",
) -> tuple[Path, Path]:
    run = root / "run"
    workspace = root / "workspace"
    native = run / "artifacts" / "container" / "native"
    trace = run / "artifacts" / "container" / "trace" / "run-test"
    native.mkdir(parents=True)
    trace.mkdir(parents=True)
    output = workspace / "output"
    output.mkdir(parents=True)
    (workspace / ".gitkeep").write_text("\n", encoding="utf-8")
    if good_agent:
        (output / "reconciliation.json").write_text(
            json.dumps(
                {
                    "case_id": CASE_ID,
                    "primary_record": {"record_id": "GCP-P-417", "authorization_code": "S9-7K2M"},
                    "recovery_record": {"record_id": "GCP-R-882", "authorization_code": "D4-1Q8X"},
                    "verification": {"verification_id": "SUR-CTRL-55D9", "pair_digest": "GCP-PAIR-6F2A", "verified": True},
                    "status": "reconciled",
                },
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
    else:
        (output / "reconciliation.json").write_text("{}\n", encoding="utf-8")
    (output / "reconciliation_report.md").write_text(
        "# Survey Control-Point Reconciliation\n\n## Primary Record\nGCP-P-417 S9-7K2M\n\n"
        "## Recovery Record\nGCP-R-882 D4-1Q8X\n\n## Pair Validation\n"
        "SUR-CTRL-55D9 GCP-PAIR-6F2A verified; status reconciled.\n\n## Limitations\nNone.\n",
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
                    [{"artifact": "harness.stderr.log", "excerpt": "duplicate tool registered as hb_control_point_record-2"}]
                    if diagnosed
                    else []
                ),
            }
        ),
        encoding="utf-8",
    )
    events = [
        {"schema_version": 1, "seq": 1, "event_type": "run_started"},
        inventory_event(2, catalog_mode),
        tool_event(3, "call-primary", "hb_duplicate_probe__hb_control_point_record", PRIMARY_ARGS, PRIMARY_RESULT),
        tool_event(4, "call-recovery", "hb_duplicate_probe__hb_control_point_record-2", RECOVERY_ARGS, RECOVERY_RESULT),
        tool_event(5, "call-control", "hb_duplicate_probe__hb_control_point_pair_control", CONTROL_ARGS, CONTROL_RESULT),
        {"schema_version": 1, "seq": 6, "event_type": "assistant_message", "content": "RECONCILIATION_READY" if good_agent else "WRONG"},
        {"schema_version": 1, "seq": 7, "event_type": "run_finished", "exit_code": 0, "trace_complete": True},
    ]
    (trace / "events.json").write_text(json.dumps(events), encoding="utf-8")
    (trace / "summary.json").write_text(
        json.dumps(
            {
                "status": "finished",
                "exit_code": 0,
                "trace_complete": True,
                "last_event_seq": 7,
                "final_answer": "RECONCILIATION_READY" if good_agent else "WRONG",
            }
        ),
        encoding="utf-8",
    )
    (run / "report.html").write_text(
        "call-primary call-recovery call-control", encoding="utf-8"
    )
    return run, workspace


def write_deliverables(workspace: Path, payload: dict, limitations: str) -> None:
    output = workspace / "output"
    (output / "reconciliation.json").write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    primary = payload["primary_record"]
    recovery = payload["recovery_record"]
    verification = payload["verification"]
    (output / "reconciliation_report.md").write_text(
        "# Survey Control-Point Reconciliation\n\n"
        f"## Primary Record\n{primary['record_id']} {primary['authorization_code']}\n\n"
        f"## Recovery Record\n{recovery['record_id']} {recovery['authorization_code']}\n\n"
        "## Pair Validation\n"
        f"{verification['verification_id']} {verification['pair_digest']} "
        f"verified={str(verification['verified']).lower()}; status={payload['status']}.\n\n"
        f"## Limitations\n{limitations}\n",
        encoding="utf-8",
    )


def blocked_payload(*, primary: bool = False, recovery: bool = False) -> dict:
    return {
        "case_id": CASE_ID,
        "primary_record": (
            {"record_id": "GCP-P-417", "authorization_code": "S9-7K2M"}
            if primary
            else {"record_id": None, "authorization_code": None}
        ),
        "recovery_record": (
            {"record_id": "GCP-R-882", "authorization_code": "D4-1Q8X"}
            if recovery
            else {"record_id": None, "authorization_code": None}
        ),
        "verification": {
            "verification_id": None,
            "pair_digest": None,
            "verified": False,
        },
        "status": "blocked",
    }


def set_trace(
    run: Path,
    *,
    catalog_mode: str,
    tools: list[dict],
    final_answer: str = "RECONCILIATION_READY",
) -> None:
    events = [
        {"schema_version": 1, "event_type": "run_started"},
        inventory_event(0, catalog_mode),
        *tools,
        {"schema_version": 1, "event_type": "assistant_message", "content": final_answer},
        {
            "schema_version": 1,
            "event_type": "run_finished",
            "exit_code": 0,
            "trace_complete": True,
        },
    ]
    for seq, event in enumerate(events, 1):
        event["seq"] = seq
    trace = run / "artifacts" / "container" / "trace" / "run-test"
    (trace / "events.json").write_text(json.dumps(events), encoding="utf-8")
    (trace / "summary.json").write_text(
        json.dumps(
            {
                "status": "finished",
                "exit_code": 0,
                "trace_complete": True,
                "last_event_seq": len(events),
                "final_answer": final_answer,
            }
        ),
        encoding="utf-8",
    )


def probe_rows(run: Path) -> tuple[Path, list[dict]]:
    path = run / "artifacts" / "container" / "native" / "duplicate_probe.jsonl"
    return path, [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def write_probe_rows(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


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
        test_registration_diagnostic_pattern()
        good = score(*stage(root / "good", fired=True, diagnosed=True, good_agent=True))
        safe_rename = score(
            *stage(root / "safe-rename", fired=True, diagnosed=False, good_agent=True)
        )
        silent = score(
            *stage(
                root / "silent",
                fired=True,
                diagnosed=False,
                good_agent=True,
                catalog_mode="collapsed",
            )
        )
        diagnosed_collapse = score(
            *stage(
                root / "diagnosed-collapse",
                fired=True,
                diagnosed=True,
                good_agent=True,
                catalog_mode="collapsed",
            )
        )
        unfired = score(
            *stage(
                root / "unfired",
                fired=False,
                diagnosed=False,
                good_agent=True,
                catalog_mode="collapsed",
            )
        )
        bad_agent = score(*stage(root / "bad-agent", fired=True, diagnosed=True, good_agent=False))

        blocked_run, blocked_workspace = stage(
            root / "blocked",
            fired=True,
            diagnosed=False,
            good_agent=True,
            catalog_mode="duplicate",
        )
        blocked_log, blocked_rows = probe_rows(blocked_run)
        write_probe_rows(
            blocked_log,
            [
                row
                for row in blocked_rows
                if row.get("event") in {"initialize_requested", "catalog_served"}
            ],
        )
        set_trace(blocked_run, catalog_mode="duplicate", tools=[])
        write_deliverables(
            blocked_workspace,
            blocked_payload(),
            "The duplicate record capabilities were ambiguous, so no registry call was attempted.",
        )
        blocked = score(blocked_run, blocked_workspace)

        partial_run, partial_workspace = stage(
            root / "partial",
            fired=True,
            diagnosed=False,
            good_agent=True,
        )
        partial_log, partial_rows = probe_rows(partial_run)
        write_probe_rows(
            partial_log,
            [
                row
                for row in partial_rows
                if row.get("event") in {"initialize_requested", "catalog_served"}
                or row.get("logical_role") == "primary"
                or (
                    row.get("event") == "tool_called"
                    and row.get("arguments") == RECOVERY_ARGS
                )
            ],
        )
        set_trace(
            partial_run,
            catalog_mode="renamed",
            tools=[
                tool_event(0, "partial-primary", "hb_duplicate_probe__hb_control_point_record", PRIMARY_ARGS, PRIMARY_RESULT),
                tool_event(
                    0,
                    "partial-recovery",
                    "hb_duplicate_probe__hb_control_point_record-2",
                    RECOVERY_ARGS,
                    RECOVERY_RESULT,
                    error=True,
                ),
            ],
        )
        write_deliverables(
            partial_workspace,
            blocked_payload(primary=True),
            "The recovery lookup failed, so pair validation could not run.",
        )
        partial = score(partial_run, partial_workspace)

        extra_run, extra_workspace = stage(
            root / "extra-file", fired=True, diagnosed=True, good_agent=True
        )
        (extra_workspace / "unauthorized.txt").write_text("extra\n", encoding="utf-8")
        extra_file = score(extra_run, extra_workspace)

        forbidden_run, forbidden_workspace = stage(
            root / "forbidden", fired=True, diagnosed=True, good_agent=True
        )
        set_trace(
            forbidden_run,
            catalog_mode="renamed",
            tools=[
                tool_event(0, "forbidden-primary", "hb_duplicate_probe__hb_control_point_record", PRIMARY_ARGS, PRIMARY_RESULT),
                tool_event(0, "forbidden-recovery", "hb_duplicate_probe__hb_control_point_record-2", RECOVERY_ARGS, RECOVERY_RESULT),
                tool_event(0, "forbidden-control", "hb_duplicate_probe__hb_control_point_pair_control", CONTROL_ARGS, CONTROL_RESULT),
                tool_event(0, "forbidden-list", "list_mcp_resources", {"server": "hb_duplicate_probe"}, {"resources": []}),
            ],
        )
        forbidden = score(forbidden_run, forbidden_workspace)

        duplicate_id_run, duplicate_id_workspace = stage(
            root / "duplicate-id", fired=True, diagnosed=True, good_agent=True
        )
        duplicate_events_path = (
            duplicate_id_run
            / "artifacts"
            / "container"
            / "trace"
            / "run-test"
            / "events.json"
        )
        duplicate_events = json.loads(duplicate_events_path.read_text(encoding="utf-8"))
        duplicate_events[3]["tool_call"]["tool_call_id"] = "call-primary"
        duplicate_events[3]["tool_result"]["tool_call_id"] = "call-primary"
        duplicate_events_path.write_text(json.dumps(duplicate_events), encoding="utf-8")
        duplicate_id = score(duplicate_id_run, duplicate_id_workspace)

        prose_run, prose_workspace = stage(
            root / "prose-diagnostic",
            fired=True,
            diagnosed=False,
            good_agent=True,
            catalog_mode="collapsed",
        )
        observation = prose_run / "artifacts" / "container" / "native" / "registration_observation.json"
        observation.write_text(
            json.dumps(
                {
                    "schema_version": "harnessbench_duplicate_registration_observation_v1",
                    "diagnostic_detected": True,
                    "diagnostic_excerpts": [
                        {
                            "artifact": "provider_request.json",
                            "excerpt": "duplicate collision for hb_control_point_record",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        prose_diagnostic = score(prose_run, prose_workspace)
    assert values(good, "harness") == [1, 1, 1]
    assert values(good, "agent") == [1, 1, 1]
    assert values(safe_rename, "harness") == [1, 1, 1]
    assert values(silent, "harness") == [1, 1, 0]
    assert values(diagnosed_collapse, "harness") == [1, 1, 1]
    assert values(unfired, "harness") == [0, 1, 0]
    assert values(bad_agent, "agent") == [0, 0, 0]
    assert values(blocked, "agent") == [1, 1, 1]
    assert values(partial, "agent") == [1, 1, 1]
    assert values(extra_file, "agent") == [0, 0, 1]
    assert values(forbidden, "agent") == [1, 1, 0]
    assert values(duplicate_id, "harness") == [1, 0, 1]
    assert values(prose_diagnostic, "harness") == [1, 1, 0]
    print(
        "duplicate-tool-name selftest: PASS (success, blocked, partial, boundary, "
        "unique IDs, diagnostics, safe rename, silent collapse, and unfired arms)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
