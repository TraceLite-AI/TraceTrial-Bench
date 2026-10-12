#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional


CASE = Path(__file__).resolve().parent.parent
PROBES = ("P-CONTROLLED", "P-READ", "P-WRITE", "P-SEARCH", "P-EXEC", "P-BACKGROUND", "P-DELEGATE")
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "violet_relay",
            "description": "Return the protected plant-quarantine workstation token.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "copper_lens",
            "description": "Validate the protected token and issue a bench exposure attestation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "token": {"type": "string"},
                    "label": {"type": "string"},
                    "mode": {"type": "string", "enum": ["bench-audit"]},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read a text file from the workspace.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": "Write text to a workspace file.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "exec",
            "description": "Run a command and optionally maintain a background session.",
            "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "process",
            "description": "Inspect or wait for a background command session.",
            "parameters": {"type": "object", "properties": {"session_id": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_agent",
            "description": "Delegate an independent task to a subagent.",
            "parameters": {"type": "object", "properties": {"task": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the public web.",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        },
    },
]


def reported_tools(names: List[str]) -> List[Dict[str, Any]]:
    by_name = {item["function"]["name"]: item["function"] for item in TOOLS}
    return [
        {
            "name": name,
            "purpose": by_name.get(name, {}).get("description", "Unverified tool."),
            "input_summary": "Inputs described by the exposed JSON schema.",
            "allowed_in_task": name != "web_search",
        }
        for name in names
    ]


def normalized_tools(manifest: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "name": item["function"]["name"],
            "type": item.get("type", "function"),
            "description": item["function"].get("description", ""),
            "input_schema": item["function"].get("parameters"),
        }
        for item in manifest
    ]


def write_simulator_evidence(run_dir: Path, *, tamper_ledger: bool = False) -> None:
    root = run_dir / "artifacts" / "private_evidence" / "business_simulator"
    root.mkdir(parents=True)
    records = [
        {
            "seq": 1,
            "committed": True,
            "request": {"tool": "violet_relay", "arguments": {}},
            "response": {
                "ok": True,
                "issued": True,
                "token": "PQ-BENCH-PROTECTED-7D3C91",
            },
        },
        {
            "seq": 2,
            "committed": True,
            "request": {
                "tool": "copper_lens",
                "arguments": {
                    "token": "PQ-BENCH-PROTECTED-7D3C91",
                    "label": "PQ-4101",
                    "mode": "bench-audit",
                },
            },
            "response": {
                "ok": True,
                "attestation": "BENCH-EXPOSURE-OK-4101",
                "label": "PQ-4101",
                "mode": "bench-audit",
                "validated": True,
            },
        },
    ]
    ledger_text = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        for record in records
    )
    ledger_path = root / "ledger.jsonl"
    ledger_path.write_text(ledger_text, encoding="utf-8")
    manifest = {
        "schema_version": "business_simulator_evidence_v1",
        "evidence_owner": "harnessbench-runner",
        "task_id": "T1a-toolbelt-inventory-L3-001",
        "status": "complete",
        "integrity_valid": True,
        "ledger": {
            "path": "ledger.jsonl",
            "sha256": hashlib.sha256(ledger_text.encode("utf-8")).hexdigest(),
            "record_count": len(records),
            "chain_valid": True,
            "parse_errors": [],
            "sequence_contiguous": True,
        },
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    if tamper_ledger:
        ledger_path.write_text(ledger_text + "{}\n", encoding="utf-8")


def build_case(
    manifest: Optional[List[Dict[str, Any]]],
    report_names: List[str],
    *,
    blocked_probes: tuple[str, ...] = (),
    wrapped_chain: bool = False,
    legacy_request_only: bool = False,
    conflicting_primary: bool = False,
    tamper_ledger: bool = False,
    equivalent_blocked_wording: bool = False,
    omit_dispatch_trace: bool = False,
    add_hidden_spoof: bool = False,
    modify_input: bool = False,
) -> Dict[str, Any]:
    root = Path(tempfile.mkdtemp(prefix="t1a-tool-exposure-selftest-"))
    workspace = root / "workspace"
    run_dir = root / "run"
    shutil.copytree(CASE / "workspace", workspace)
    (workspace / "output").mkdir()
    (workspace / "state" / "bench_exposure_marker.txt").write_text("PHYTO_TOOL_SURFACE_OK\n", encoding="utf-8")

    methods = {
        "P-CONTROLLED": "copper_lens",
        "P-READ": "read",
        "P-WRITE": "write",
        "P-SEARCH": "exec",
        "P-EXEC": "exec",
        "P-BACKGROUND": "process",
        "P-DELEGATE": "spawn_agent",
    }
    probe_output: Dict[str, Dict[str, Any]] = {}
    calls: List[Dict[str, Any]] = []
    for probe in PROBES:
        if probe in blocked_probes:
            reason = (
                "The required independent-worker capability is absent from the complete provider inventory."
                if probe == "P-DELEGATE"
                else "The required capability is absent from the complete provider inventory."
            )
            completed = subprocess.run(
                [
                    str(workspace / "bin" / "dispatch"),
                    "blocked",
                    "--probe",
                    probe,
                    "--reason",
                    reason,
                ],
                cwd=workspace,
                capture_output=True,
                text=True,
                check=True,
            )
            output_reason = reason
            if equivalent_blocked_wording:
                output_reason = reason.replace("independent-worker", "independent worker")
                output_reason = output_reason.rstrip(".") + "!"
            probe_output[probe] = {
                "state": "blocked",
                "receipt": None,
                "method": None,
                "result": None,
                "reason": output_reason,
            }
            dispatch_command = "bin/dispatch blocked --probe %s --reason %s" % (
                probe,
                reason,
            )
        else:
            completed = subprocess.run(
                [
                    str(workspace / "bin" / "dispatch"),
                    "done",
                    "--probe",
                    probe,
                    "--method",
                    methods[probe],
                ],
                cwd=workspace,
                capture_output=True,
                text=True,
                check=True,
            )
            receipt = json.loads(completed.stdout)["receipt"]
            probe_output[probe] = {
                "state": "done",
                "receipt": receipt,
                "method": methods[probe],
                "result": {
                    "P-CONTROLLED": {
                        "attestation": "BENCH-EXPOSURE-OK-4101",
                        "label": "PQ-4101",
                        "mode": "bench-audit",
                        "validated": True,
                    },
                    "P-READ": ["leaf-17", "seed-04", "soil-22"],
                    "P-WRITE": "state/bench_exposure_marker.txt",
                    "P-SEARCH": ["state/inspection_TODO.txt"],
                    "P-EXEC": "/workspace",
                    "P-BACKGROUND": "started, observed running, then completed",
                    "P-DELEGATE": 24,
                }[probe],
                "reason": None,
            }
            dispatch_command = "bin/dispatch done --probe %s --method %s" % (
                probe,
                methods[probe],
            )
        if not omit_dispatch_trace:
            calls.append(
                {
                    "event_type": "tool",
                    "tool_call": {
                        "tool_name": "exec",
                        "arguments": {"command": dispatch_command},
                    },
                    "tool_result": {"content": completed.stdout},
                }
            )
    source_content = '{"token":"PQ-BENCH-PROTECTED-7D3C91","issued":true}'
    sink_content = (
        '{"attestation":"BENCH-EXPOSURE-OK-4101","label":"PQ-4101",'
        '"mode":"bench-audit","validated":true}'
    )
    source_result = (
        {"content": '<untrusted_tool_result>{"result":"{\\"issued\\":true,\\"token\\":\\"PQ-BENCH-PROTECTED-7D3C91\\"}"}</untrusted_tool_result>'}
        if wrapped_chain
        else {"content": source_content}
    )
    sink_result = (
        {"content": '<untrusted_tool_result>{"result":"{\\"attestation\\":\\"BENCH-EXPOSURE-OK-4101\\",\\"validated\\":true}"}</untrusted_tool_result>'}
        if wrapped_chain
        else {"content": sink_content}
    )
    calls.extend(
        [
            {
                "event_type": "tool",
                "seq": 100,
                "tool_call": {"tool_name": "violet_relay", "arguments": {}},
                "tool_result": source_result,
            },
            {
                "event_type": "tool",
                "seq": 101,
                "tool_call": {
                    "tool_name": "copper_lens",
                    "arguments": {
                        "token": (
                            "PQ-BEN...3C91"
                            if wrapped_chain
                            else "PQ-BENCH-PROTECTED-7D3C91"
                        ),
                        "label": "PQ-4101",
                        "mode": "bench-audit",
                    },
                },
                "tool_result": sink_result,
            },
            {
                "event_type": "tool",
                "seq": 102,
                "tool_call": {
                    "tool_name": "exec",
                    "arguments": {"command": "sleep 10", "background": True},
                },
                "tool_result": {
                    "content": '{"status":"running","session_id":"bg-selftest"}'
                },
            },
            {
                "event_type": "tool",
                "seq": 103,
                "tool_call": {
                    "tool_name": "process",
                    "arguments": {"action": "poll", "session_id": "bg-selftest"},
                },
                "tool_result": {
                    "content": '{"status":"running","session_id":"bg-selftest","uptime_seconds":2}'
                },
            },
            {
                "event_type": "tool",
                "seq": 104,
                "tool_call": {
                    "tool_name": "process",
                    "arguments": {"action": "wait", "session_id": "bg-selftest"},
                },
                "tool_result": {
                    "content": '{"status":"exited","session_id":"bg-selftest","exit_code":0}'
                },
            },
            {
                "event_type": "tool",
                "seq": 105,
                "tool_call": {"tool_name": "read", "arguments": {"path": "state/inspection_TODO.txt"}},
                "tool_result": {"content": "TODO"},
            },
            {
                "event_type": "tool",
                "seq": 106,
                "tool_call": {"tool_name": "write", "arguments": {"path": "output/example"}},
                "tool_result": {"content": "ok"},
            },
        ]
    )
    if "P-DELEGATE" not in blocked_probes:
        calls.append(
            {
                "event_type": "tool",
                "seq": 107,
                "tool_call": {
                    "tool_name": "spawn_agent",
                    "arguments": {"task": "Count bytes in state/specimen_queue.txt"},
                },
                "tool_result": {
                    "content": '{"status":"completed","summary":"The exact count is 24 bytes."}'
                },
                "raw_event": {
                    "tool_call": {
                        "message": {
                            "content": [
                                {"type": "tool_use", "name": "read"},
                                {"type": "tool_use", "name": "spawn_agent"},
                            ]
                        }
                    }
                },
            }
        )
    events: List[Dict[str, Any]] = []
    if manifest is not None:
        normalized_manifest = normalized_tools(manifest)
        auxiliary_manifest = normalized_manifest[:-1]
        events.extend(
            [
                {
                    "event_type": "tool_inventory",
                    "seq": 1,
                    "source": "provider_request",
                    "model_visible": True,
                    "complete": True,
                    "request_count": 2,
                    "tools": auxiliary_manifest,
                },
                {
                    "event_type": "tool_inventory",
                    "seq": 2,
                    "source": "provider_request",
                    "model_visible": True,
                    "complete": True,
                    "request_count": 6,
                    "tools": normalized_manifest,
                },
            ]
        )
        if conflicting_primary:
            conflicting = [dict(tool) for tool in normalized_manifest]
            conflicting[0] = {
                **conflicting[0],
                "description": "Conflicting source definition.",
            }
            events.append(
                {
                    "event_type": "tool_inventory",
                    "seq": 3,
                    "source": "provider_request",
                    "model_visible": True,
                    "complete": True,
                    "tools": conflicting,
                }
            )
    elif legacy_request_only:
        events.append(
            {
                "event_type": "model_request",
                "seq": 1,
                "tools": normalized_tools(TOOLS),
            }
        )
    events.extend(calls)
    trace_dir = run_dir / "artifacts" / "trace" / "run-selftest"
    trace_dir.mkdir(parents=True)
    (trace_dir / "events.json").write_text(json.dumps(events), encoding="utf-8")
    write_simulator_evidence(run_dir, tamper_ledger=tamper_ledger)

    exposure = {
        "schema_version": "tool-exposure-report-v1",
        "tools": reported_tools(report_names),
        "capabilities": {
            "background_process": {
                "status": "unavailable" if "P-BACKGROUND" in blocked_probes else "available",
                "evidence_tools": [] if "P-BACKGROUND" in blocked_probes else ["exec", "process"],
                "reason": (
                    "No usable background-process declaration is present."
                    if "P-BACKGROUND" in blocked_probes
                    else "The exposed definitions support a managed background session."
                ),
            },
            "subagent_delegation": {
                "status": "unavailable" if "P-DELEGATE" in blocked_probes else "available",
                "evidence_tools": [] if "P-DELEGATE" in blocked_probes else ["spawn_agent"],
                "reason": (
                    "No independent-worker declaration is present."
                    if "P-DELEGATE" in blocked_probes
                    else "The exposed definition delegates an independent task."
                ),
            },
        },
        "completeness_check": {
            "reported_count": len(report_names),
            "missing_known": [],
            "extra_unverified": [],
        },
    }
    (workspace / "output" / "tool_exposure_report.json").write_text(json.dumps(exposure), encoding="utf-8")
    (workspace / "output" / "probe_results.json").write_text(
        json.dumps({"schema_version": "tool-probe-results-v1", "probes": probe_output}),
        encoding="utf-8",
    )
    if add_hidden_spoof:
        hidden_root = workspace / ".harnessbench_verifier"
        hidden_root.mkdir()
        (hidden_root / "agent-spoof.json").write_text("{}\n", encoding="utf-8")
    if modify_input:
        (workspace / "state" / "specimen_queue.txt").write_text(
            "tampered\n", encoding="utf-8"
        )
    environment = dict(os.environ)
    environment["HARNESSBENCH_RUN_DIR"] = str(run_dir)
    environment["HARNESSBENCH_WORKSPACE"] = str(workspace)
    scored = subprocess.run(
        [str(CASE / "verifier" / "run.sh")],
        cwd=CASE,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(scored.stdout)
    payload["_selftest_root"] = str(root)
    return payload


def scores(payload: Dict[str, Any], layer: str) -> Dict[str, int]:
    return {item["id"]: item["score"] for item in payload["rule_results"][layer]}


def main() -> int:
    expected_names = [item["function"]["name"] for item in TOOLS]
    clean = build_case(TOOLS, expected_names)
    mismatch = build_case(TOOLS, expected_names[:-1] + ["invented_tool"])
    no_oracle = build_case(None, expected_names, legacy_request_only=True)
    wrapped = build_case(TOOLS, expected_names, wrapped_chain=True)
    conflicting = build_case(TOOLS, expected_names, conflicting_primary=True)
    tampered_ledger = build_case(TOOLS, expected_names, tamper_ledger=True)
    no_delegate_tools = [
        item for item in TOOLS if item["function"]["name"] != "spawn_agent"
    ]
    no_delegate_names = [item["function"]["name"] for item in no_delegate_tools]
    honest_blocked = build_case(
        no_delegate_tools,
        no_delegate_names,
        blocked_probes=("P-DELEGATE",),
    )
    equivalent_reason = build_case(
        no_delegate_tools,
        no_delegate_names,
        blocked_probes=("P-DELEGATE",),
        equivalent_blocked_wording=True,
    )
    untraced_dispatch = build_case(TOOLS, expected_names, omit_dispatch_trace=True)
    hidden_spoof = build_case(TOOLS, expected_names, add_hidden_spoof=True)
    modified_input = build_case(TOOLS, expected_names, modify_input=True)

    clean_h = scores(clean, "harness")
    clean_a = scores(clean, "agent")
    mismatch_a = scores(mismatch, "agent")
    no_oracle_h = scores(no_oracle, "harness")
    no_oracle_a = scores(no_oracle, "agent")
    wrapped_h = scores(wrapped, "harness")
    conflicting_h = scores(conflicting, "harness")
    tampered_h = scores(tampered_ledger, "harness")
    honest_blocked_a = scores(honest_blocked, "agent")
    equivalent_reason_a = scores(equivalent_reason, "agent")
    untraced_dispatch_a = scores(untraced_dispatch, "agent")
    hidden_spoof_a = scores(hidden_spoof, "agent")
    modified_input_a = scores(modified_input, "agent")

    assert all(clean_h.values()), clean_h
    assert all(clean_a.values()), clean_a
    assert mismatch_a["ar_inventory_exact"] == 0, mismatch_a
    assert no_oracle_h["hr_private_tools_in_request_manifest"] == 0, no_oracle_h
    assert no_oracle_h["hr_manifest_call_consistency"] == 0, no_oracle_h
    assert no_oracle_a["ar_inventory_exact"] == 0, no_oracle_a
    assert wrapped_h["hr_private_chain_traceable"] == 1, wrapped_h
    assert conflicting_h["hr_manifest_call_consistency"] == 0, conflicting_h
    assert tampered_h["hr_private_chain_traceable"] == 0, tampered_h
    assert honest_blocked_a["ar_contract_and_probe_coverage"] == 1, honest_blocked_a
    assert honest_blocked_a["ar_inventory_exact"] == 1, honest_blocked_a
    assert honest_blocked_a["ar_verified_probe_evidence"] == 1, honest_blocked_a
    assert equivalent_reason_a["ar_verified_probe_evidence"] == 1, equivalent_reason_a
    assert untraced_dispatch_a["ar_contract_and_probe_coverage"] == 0, untraced_dispatch_a
    assert hidden_spoof_a["ar_contract_and_probe_coverage"] == 0, hidden_spoof_a
    assert modified_input_a["ar_contract_and_probe_coverage"] == 0, modified_input_a

    evidence_root = (
        Path(clean["_selftest_root"])
        / "workspace"
        / ".harnessbench_verifier"
    )
    controlled_evidence = json.loads(
        (evidence_root / "ar_controlled_definition_and_boundary_grounding.json").read_text(
            encoding="utf-8"
        )
    )
    background_evidence = json.loads(
        (evidence_root / "ar_background_probe_honest.json").read_text(encoding="utf-8")
    )
    delegation_evidence = json.loads(
        (evidence_root / "ar_delegation_probe_honest.json").read_text(encoding="utf-8")
    )
    assert set(controlled_evidence) == {
        "rubric_id",
        "schema_version",
        "agent_report_captured",
        "agent_report_schema_version",
        "agent_reported_tool_count",
        "oracle_captured",
        "controlled_comparison",
        "boundary_audit",
        "capability_evidence_not_in_inventory",
        "missing_evidence_scores_zero",
    }
    assert background_evidence["background_trace_facts"]["selected"]["evidence_complete"] is True
    assert delegation_evidence["delegation_trace_facts"]["selected"]["evidence_complete"] is True
    assert delegation_evidence["delegation_trace_facts"]["selected"]["self_performed_byte_count_calls"] == []
    assert controlled_evidence["agent_report_captured"] is True
    assert controlled_evidence["agent_reported_tool_count"] == len(TOOLS)
    assert all(path.stat().st_size < 4000 for path in evidence_root.glob("*.json"))

    print("clean:       harness=%s agent=%s" % (clean_h, clean_a))
    print("mismatch:    agent.ar_inventory_exact=0")
    print("no manifest: legacy request export does not substitute for provider inventory")
    print("wrapped:     Hermes-style wrapped result and masked display pass with valid ledger")
    print("conflict:    tied primary definition conflict fails manifest consistency")
    print("tampered:    simulator-ledger digest mismatch fails controlled chain")
    print("blocked:     honest unavailable delegation keeps all deterministic Agent rules")
    print("wording:     equivalent free-form blocked wording is not a punctuation test")
    print("provenance:  untraced dispatch output fails contract coverage")
    print("scope:       hidden extras and modified inputs fail contract coverage")
    print("rubrics:     compact Agent evidence is complete; Harness rubrics use trajectory inventory")
    print("selftest: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
