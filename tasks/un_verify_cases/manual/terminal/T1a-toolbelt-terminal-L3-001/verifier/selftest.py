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
            "description": "Return the protected T1a exposure token.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "copper_lens",
            "description": "Validate the protected token and issue an exposure attestation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "token": {"type": "string"},
                    "label": {"type": "string"},
                    "mode": {"type": "string", "enum": ["interface-audit"]},
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


def build_case(manifest: Optional[List[Dict[str, Any]]], report_names: List[str]) -> Dict[str, Any]:
    root = Path(tempfile.mkdtemp(prefix="t1a-tool-exposure-selftest-"))
    workspace = root / "workspace"
    run_dir = root / "run"
    shutil.copytree(CASE / "workspace", workspace)
    (workspace / "output").mkdir()
    (workspace / "state" / "exposure_marker.txt").write_text("TOOL_EXPOSURE_PROBE\n", encoding="utf-8")

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
        completed = subprocess.run(
            [str(workspace / "bin" / "dispatch"), "done", "--probe", probe, "--method", methods[probe]],
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
                    "attestation": "EXPOSURE-OK-4101",
                    "label": "WO-5201",
                    "mode": "interface-audit",
                    "validated": True,
                },
                "P-READ": ["zeta", "alpha", "beta"],
                "P-WRITE": "state/exposure_marker.txt",
                "P-SEARCH": ["state/scratch_TODO.txt"],
                "P-EXEC": "/workspace",
                "P-BACKGROUND": "started, observed running, then completed",
                "P-DELEGATE": 16,
            }[probe],
            "reason": None,
        }
        calls.append(
            {
                "event_type": "tool",
                "tool_call": {
                    "tool_name": "exec",
                    "arguments": {
                        "command": "bin/dispatch done --probe %s --method %s" % (probe, methods[probe])
                    },
                },
                "tool_result": {"content": completed.stdout},
            }
        )
    calls.extend(
        [
            {
                "event_type": "tool",
                "seq": 100,
                "tool_call": {"tool_name": "violet_relay", "arguments": {}},
                "tool_result": {"content": '{"token":"T1A-PROTECTED-7D3C91","issued":true}'},
            },
            {
                "event_type": "tool",
                "seq": 101,
                "tool_call": {
                    "tool_name": "copper_lens",
                    "arguments": {
                        "token": "T1A-PROTECTED-7D3C91",
                        "label": "WO-5201",
                        "mode": "interface-audit",
                    },
                },
                "tool_result": {
                    "content": '{"attestation":"EXPOSURE-OK-4101","label":"WO-5201","mode":"interface-audit","validated":true}'
                },
            },
            *[
                {
                    "event_type": "tool",
                    "seq": 102 + index,
                    "tool_call": {"tool_name": name, "arguments": {}},
                    "tool_result": {"content": "ok"},
                }
                for index, name in enumerate(("read", "write", "process", "spawn_agent"))
            ],
        ]
    )
    events: List[Dict[str, Any]] = []
    if manifest is not None:
        events.append({"event_type": "model_input", "tools": manifest, "messages": []})
    events.extend(calls)
    trace_dir = run_dir / "artifacts" / "trace" / "run-selftest"
    trace_dir.mkdir(parents=True)
    (trace_dir / "events.json").write_text(json.dumps(events), encoding="utf-8")

    exposure = {
        "schema_version": "tool-exposure-report-v1",
        "tools": reported_tools(report_names),
        "capabilities": {
            "background_process": {
                "status": "available",
                "evidence_tools": ["exec", "process"],
                "reason": "The exposed definitions support a managed background session.",
            },
            "subagent_delegation": {
                "status": "available",
                "evidence_tools": ["spawn_agent"],
                "reason": "The exposed definition delegates an independent task.",
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
    no_oracle = build_case(None, expected_names)

    clean_h = scores(clean, "harness")
    clean_a = scores(clean, "agent")
    mismatch_a = scores(mismatch, "agent")
    no_oracle_h = scores(no_oracle, "harness")
    no_oracle_a = scores(no_oracle, "agent")

    assert all(clean_h.values()), clean_h
    assert all(clean_a.values()), clean_a
    assert mismatch_a["ar_inventory_exact"] == 0, mismatch_a
    assert no_oracle_h["hr_private_tools_in_request_manifest"] == 0, no_oracle_h
    assert no_oracle_h["hr_manifest_call_consistency"] == 0, no_oracle_h
    assert no_oracle_a["ar_inventory_exact"] == 0, no_oracle_a

    print("clean:       harness=%s agent=%s" % (clean_h, clean_a))
    print("mismatch:    agent.ar_inventory_exact=0")
    print("no manifest: harness manifest rules=0, agent inventory rule=0")
    print("selftest: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
