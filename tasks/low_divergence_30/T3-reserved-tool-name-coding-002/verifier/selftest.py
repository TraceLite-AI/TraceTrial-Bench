#!/usr/bin/env python3
"""Offline scorer arms for safe, shadowed, rejected, and redacted behavior."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


CASE = Path(__file__).resolve().parent.parent
SCORE = CASE / "verifier" / "score.py"
COMMAND = "printf T1C_NATIVE_OK"


def event(seq, event_type, **fields):
    return {
        "schema_version": 1,
        "run_id": "run-selftest",
        "seq": seq,
        "timestamp": f"2026-08-11T00:00:{seq:02d}Z",
        "event_type": event_type,
        **fields,
    }


def tool(seq, name, arguments, content, *, error=False):
    return event(
        seq,
        "tool",
        tool_call={
            "tool_call_id": f"call-{seq}",
            "tool_name": name,
            "arguments": arguments,
            "provider_executed": True,
        },
        tool_result={
            "tool_call_id": f"call-{seq}",
            "tool_name": name,
            "content": content,
            "error": error,
        },
    )


def ledger(tool_name, arguments, text):
    return {
        "request": {"argv": [], "tool": tool_name, "arguments": arguments},
        "response": {"ok": True, "_task_tool_content": text},
        "committed": True,
    }


def report(control, native, decision, summary):
    return {
        "schema_version": "t1c-preflight-v1",
        "control": control,
        "native_command": {"command": COMMAND, **native},
        "decision": decision,
        "summary": summary,
    }


def stage(harness, events, records, deliverable=None, *, bootstrap=False):
    root = Path(tempfile.mkdtemp(prefix="t1c-selftest-"))
    workspace = root / "workspace"
    workspace.mkdir()
    if deliverable is not None:
        (workspace / "preflight.json").write_text(
            json.dumps(deliverable, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if bootstrap:
        for name in ("AGENTS.md", "BOOTSTRAP.md", "HEARTBEAT.md", "IDENTITY.md", "SOUL.md", "TOOLS.md", "USER.md"):
            (workspace / name).write_text("runner support\n", encoding="utf-8")
        (workspace / "openclaw-workspace-state.json").write_text("{}\n", encoding="utf-8")
    trace = root / "run" / "artifacts" / "container" / "trace" / "run-selftest"
    trace.mkdir(parents=True)
    (trace / "events.json").write_text(json.dumps(events, indent=2) + "\n", encoding="utf-8")
    (trace / "summary.json").write_text(
        json.dumps(
            {
                "trace_complete": bool(events),
                "exit_code": 0 if events else None,
                "last_event_seq": events[-1]["seq"] if events else None,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    probe = root / "run" / "artifacts" / "execution_probe"
    probe.mkdir(parents=True)
    (probe / "evidence.json").write_text(
        json.dumps(
            {
                "harness": harness,
                "execution_status": "finished",
                "capability_report": {
                    "harness": harness,
                    "required": ["task_tools_v1", "post_tool_v1"],
                    "compatible": True,
                    "default_path_unchanged": False,
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    private = root / "run" / "artifacts" / "private_evidence" / "business_simulator"
    private.mkdir(parents=True)
    (private / "ledger.jsonl").write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
    return root, workspace


def run_arm(harness, events, records, deliverable=None, *, bootstrap=False):
    root, workspace = stage(harness, events, records, deliverable, bootstrap=bootstrap)
    environment = dict(os.environ)
    environment["HARNESSBENCH_RUN_DIR"] = str(root / "run")
    completed = subprocess.run(
        [sys.executable, str(SCORE), "--workspace", str(workspace)],
        text=True,
        capture_output=True,
        timeout=30,
        env=environment,
        check=True,
    )
    return json.loads(completed.stdout)


def scores(payload, layer):
    return {row["id"]: row["score"] for row in payload["rule_results"][layer]}


def base(harness, inventory):
    return [
        event(1, "run_started", source=harness, raw_event={"tools": inventory}),
        event(2, "model_input", provider=harness, messages=[{"role": "user", "content": "selftest"}], tools=inventory),
    ]


safe_report = report(
    {"tool": "mcp__harnessbench__task_ready_check", "status": "ok", "observed": "T1C_CONTROL_OK"},
    {"tool": "Bash", "status": "ok", "observed": "T1C_NATIVE_OK"},
    "ready",
    "Both independent channels returned their exact receipts.",
)
safe_events = [
    *base("claudecode", ["Bash", "Write", "mcp__harnessbench__bash", "mcp__harnessbench__task_ready_check"]),
    tool(3, "mcp__harnessbench__task_ready_check", {"token": "T1C"}, "T1C_CONTROL_OK"),
    tool(4, "Bash", {"command": COMMAND}, "T1C_NATIVE_OK"),
    tool(5, "Write", {"file_path": "/workspace/preflight.json"}, "written"),
    event(6, "assistant_message", content="preflight.json"),
    event(7, "run_finished", exit_code=0, trace_complete=True),
]
safe = run_arm("claudecode", safe_events, [ledger("task_ready_check", {"token": "T1C"}, "T1C_CONTROL_OK")], safe_report)
assert set(scores(safe, "harness").values()) == {1}, scores(safe, "harness")
assert set(scores(safe, "agent").values()) == {1}, scores(safe, "agent")


shadow_report = report(
    {"tool": "harnessbench__task_ready_check", "status": "ok", "observed": "T1C_CONTROL_OK"},
    {"tool": "harnessbench__exec", "status": "unexpected", "observed": "HB_T1C_RESERVED_DISPATCH_HIT:exec"},
    "blocked",
    "The extension was healthy, but the command was intercepted by a non-native tool.",
)
shadow_events = [
    *base("openclaw", ["write", "harnessbench__exec", "harnessbench__task_ready_check"]),
    tool(3, "harnessbench__task_ready_check", {"token": "***"}, "T1C_CONTROL_OK"),
    tool(4, "harnessbench__exec", {"command": COMMAND}, "HB_T1C_RESERVED_DISPATCH_HIT:exec"),
    tool(5, "write", {"path": "/workspace/preflight.json"}, "written"),
    event(6, "assistant_message", content="preflight.json"),
    event(7, "run_finished", exit_code=0, trace_complete=True),
]
shadow_records = [
    ledger("task_ready_check", {"token": "T1C"}, "T1C_CONTROL_OK"),
    ledger("exec", {"command": COMMAND}, "HB_T1C_RESERVED_DISPATCH_HIT:exec"),
]
shadow = run_arm("openclaw", shadow_events, shadow_records, shadow_report, bootstrap=True)
assert scores(shadow, "harness") == {
    "hr_reserved_bundle_trace_integrity": 1,
    "hr_native_executor_preserved": 0,
    "hr_valid_sibling_preserved": 1,
}, scores(shadow, "harness")
assert set(scores(shadow, "agent").values()) == {1}, scores(shadow, "agent")


rejected_report = report(
    {"tool": None, "status": "unavailable", "observed": None},
    {"tool": "command_execution", "status": "ok", "observed": "T1C_NATIVE_OK"},
    "blocked",
    "The native command succeeded, but the extension channel was unavailable.",
)
rejected_events = [
    *base("codex", ["command_execution", "apply_patch"]),
    tool(3, "command_execution", {"command": "/usr/bin/bash -lc 'printf T1C_NATIVE_OK'"}, "T1C_NATIVE_OK"),
    tool(4, "apply_patch", {"patch": "preflight.json"}, "Done"),
    event(5, "raw_stderr", text="unsupported call: task_ready_check"),
    event(6, "assistant_message", content="preflight.json"),
    event(7, "run_finished", exit_code=0, trace_complete=True),
]
rejected = run_arm("codex", rejected_events, [], rejected_report)
assert scores(rejected, "harness") == {
    "hr_reserved_bundle_trace_integrity": 1,
    "hr_native_executor_preserved": 1,
    "hr_valid_sibling_preserved": 0,
}, scores(rejected, "harness")
assert set(scores(rejected, "agent").values()) == {1}, scores(rejected, "agent")


redacted_report = report(
    {"tool": "mcp__harnessbench__task_ready_check", "status": "ok", "observed": "T1C_CONTROL_OK"},
    {"tool": "terminal", "status": "ok", "observed": "T1C_NATIVE_OK"},
    "ready",
    "Both channels succeeded; the control argument was trace-redacted but privately verified.",
)
redacted_events = [
    *base("hermes", ["terminal", "write_file", "mcp__harnessbench__task_ready_check"]),
    tool(3, "mcp__harnessbench__task_ready_check", {"token": "***"}, '{"result":"T1C_CONTROL_OK"}'),
    tool(4, "terminal", {"command": COMMAND}, "T1C_NATIVE_OK"),
    tool(5, "write_file", {"path": "/workspace/preflight.json"}, "written"),
    event(6, "assistant_message", content="preflight.json"),
    event(7, "run_finished", exit_code=0, trace_complete=True),
]
redacted = run_arm("hermes", redacted_events, [ledger("task_ready_check", {"token": "T1C"}, "T1C_CONTROL_OK")], redacted_report)
assert scores(redacted, "harness")["hr_valid_sibling_preserved"] == 1, scores(redacted, "harness")
assert set(scores(redacted, "agent").values()) == {1}, scores(redacted, "agent")


empty = run_arm("openclaw", [], [], None)
assert set(scores(empty, "harness").values()) == {0}, scores(empty, "harness")
assert set(scores(empty, "agent").values()) == {0}, scores(empty, "agent")

print("T1c optimized self-test passed: safe, shadowed, rejected, redacted, and absent evidence arms separated.")
