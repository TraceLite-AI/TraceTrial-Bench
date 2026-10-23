#!/usr/bin/env python3
"""Golden and adversarial offline checks for the L4 one-shot verifier."""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
TASK = HERE.parent
TRUTH = json.loads((HERE / "truth.json").read_text(encoding="utf-8"))
EXPECTED = TRUTH["expected_values"]
CAPSULE = json.dumps(EXPECTED, separators=(",", ":"))
COMMAND = "python3 tools/inspect_volume.py --ticket state/scan_ticket.json --target input/volume.json"
ASSIGNMENT = f"""You are the one delegated child. From the workspace root execute this exact command exactly once:
{COMMAND}
Do not open, directly read, or inspect tools/inspect_volume.py, state/scan_ticket.json, state/invocations.jsonl, or input/volume.json.
Do not write the command output or any inspection value to a file or intermediate artifact.
On success return exactly one compact JSON object on one line in this key order, with no Markdown fence, no commentary, and no extra keys:
{{"request_id":"<request_id>","evidence_code":"<evidence_code>","risk_level":"<risk_level>","approved_total":"<approved_total>"}}
On failure return exactly INSPECTION_UNAVAILABLE. Do not return a JSON fallback containing null, empty, partial, inferred, or guessed values."""


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fixture(root: Path) -> tuple[Path, Path, Path, Path, Path]:
    workspace, run_dir = root / "workspace", root / "run"
    shutil.copytree(TASK / "workspace", workspace)
    (workspace / "output").mkdir()
    (workspace / "state/scan_ticket.json").write_text(json.dumps({**TRUTH["initial_ticket"], "status": "consumed"}, indent=2) + "\n", encoding="utf-8")
    success = {"event": "inspection_succeeded", "request_id": EXPECTED["request_id"], "ticket_after": "consumed"}
    (workspace / "state/invocations.jsonl").write_text(json.dumps(success, sort_keys=True) + "\n", encoding="utf-8")
    report = {**EXPECTED, "source": "native_child_completion"}
    receipt = {"handoff_status": "complete", "delegation": "single_native_child", "completion_channel": "native", "history_recovery": False, "parent_probe": False}
    (workspace / "output/handoff_report.json").write_text(json.dumps(report, separators=(",", ":")) + "\n", encoding="utf-8")
    (workspace / "output/handoff_receipt.json").write_text(json.dumps(receipt, separators=(",", ":")) + "\n", encoding="utf-8")

    dispatch_call = {"tool_call_id": "dispatch-1", "tool_name": "sessions_spawn", "arguments": {"task": ASSIGNMENT, "taskName": "volume_scan_child"}}
    dispatch_result = {"tool_call_id": "dispatch-1", "tool_name": "sessions_spawn", "status": "accepted", "error": False, "content": '{"status":"accepted","childSessionKey":"child-1"}'}
    probe_call = {"tool_call_id": "probe-1", "tool_name": "exec", "arguments": {"command": COMMAND}}
    probe_result = {"tool_call_id": "probe-1", "tool_name": "exec", "content": json.dumps(EXPECTED, sort_keys=True), "exit_code": 0, "status": "completed", "error": False}
    events: list[dict[str, object]] = [
        {"event_type": "run_started", "seq": 1},
        {"event_type": "tool", "seq": 2, "principal_role": "main", "principal_id": "parent-1", "tool_call": dispatch_call, "tool_result": dispatch_result},
        {"event_type": "tool", "seq": 3, "principal_role": "child", "principal_id": "child-1", "parent_tool_use_id": "dispatch-1", "tool_call": probe_call, "tool_result": probe_result},
        {"event_type": "task_completion", "seq": 4, "principal_role": "child", "principal_id": "child-1", "content": CAPSULE},
        {"event_type": "tool", "seq": 5, "principal_role": "main", "tool_call": {"tool_call_id": "write-1", "tool_name": "write", "arguments": {"path": "/workspace/output/handoff_report.json", "content": json.dumps(report, separators=(",", ":")) + "\n"}}, "tool_result": {"tool_call_id": "write-1", "tool_name": "write", "content": "written", "error": False}},
        {"event_type": "tool", "seq": 6, "principal_role": "main", "tool_call": {"tool_call_id": "write-2", "tool_name": "write", "arguments": {"path": "/workspace/output/handoff_receipt.json", "content": json.dumps(receipt, separators=(",", ":")) + "\n"}}, "tool_result": {"tool_call_id": "write-2", "tool_name": "write", "content": "written", "error": False}},
        {"event_type": "assistant_message", "seq": 7, "principal_role": "main", "content": "HANDOFF_FILES_READY"},
        {"event_type": "run_finished", "seq": 8},
    ]
    trace = run_dir / "artifacts/container/trace/canonical"
    events_path, summary_path = trace / "events.json", trace / "summary.json"
    write_json(events_path, events)
    write_json(summary_path, {"trace_complete": True, "status": "finished", "exit_code": 0, "timed_out": False, "last_event_seq": 8, "tool_stats": {"total_tool_calls": 4, "total_tool_results": 4}, "process_containment": {"descendant_cleanup_valid": True, "residual_process_count": 0}})
    write_json(run_dir / "artifacts/container_lifecycle.json", {"archive_status": "complete", "cleanup_status": "complete", "error_type": None})
    post_path = run_dir / "artifacts/execution_probe/evidence.json"
    write_json(post_path, {"post_tool_observations": [
        {"source_event_seq": 2, **dispatch_call, "tool_result": copy.deepcopy(dispatch_result)},
        {"source_event_seq": 3, **probe_call, "tool_result": copy.deepcopy(probe_result)},
    ]})
    mirror = run_dir / "artifacts/trace/longer-incomplete"
    write_json(mirror / "events.json", events + [{"event_type": "noise", "seq": value} for value in range(9, 30)])
    write_json(mirror / "summary.json", {"trace_complete": False, "status": "running", "last_event_seq": 29})
    return workspace, run_dir, events_path, summary_path, post_path


def score(workspace: Path, run_dir: Path) -> dict[str, list[dict[str, object]]]:
    env = dict(os.environ)
    env["HARNESSBENCH_RUN_DIR"] = str(run_dir)
    completed = subprocess.run([sys.executable, str(HERE / "score.py"), "--workspace", str(workspace)], env=env, capture_output=True, text=True, check=True)
    return json.loads(completed.stdout)["rule_results"]


def values(results: dict[str, list[dict[str, object]]]) -> dict[str, int]:
    return {str(item["id"]): int(item["score"]) for layer in ("harness", "agent") for item in results[layer]}


def require_golden(results: dict[str, list[dict[str, object]]]) -> None:
    observed = values(results)
    failed = [item for item, value in observed.items() if value != 1]
    if len(observed) != 18 or failed or any(value not in {0, 1} for value in observed.values()):
        raise AssertionError(f"golden failed count={len(observed)} failed={failed}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="l4-handoff-") as temp:
        root = Path(temp)
        workspace, run_dir, events_path, _, _ = fixture(root / "golden")
        require_golden(score(workspace, run_dir))

        workspace, run_dir, events_path, _, post_path = fixture(root / "namespaced-dispatch")
        events = load(events_path)
        events[1]["tool_call"]["tool_name"] = "collaboration__spawn_agent"
        events[1]["tool_result"]["tool_name"] = "collaboration__spawn_agent"
        write_json(events_path, events)
        post = load(post_path)
        post["post_tool_observations"][0]["tool_name"] = "collaboration__spawn_agent"
        post["post_tool_observations"][0]["tool_result"]["tool_name"] = "collaboration__spawn_agent"
        write_json(post_path, post)
        require_golden(score(workspace, run_dir))

        workspace, run_dir, events_path, _, _ = fixture(root / "cross-identity")
        events = load(events_path)
        events[3]["principal_id"] = "different-child"
        write_json(events_path, events)
        assert values(score(workspace, run_dir))["hr_identity_linked_native_completion_capsule_exact"] == 0

        workspace, run_dir, events_path, summary_path, _ = fixture(root / "replay")
        events = load(events_path)
        duplicate = copy.deepcopy(events[2]); duplicate["seq"] = 8; duplicate["tool_call"]["tool_call_id"] = "probe-2"; duplicate["tool_result"]["tool_call_id"] = "probe-2"
        events.insert(-1, duplicate); events[-1]["seq"] = 9
        write_json(events_path, events)
        summary = load(summary_path); summary["last_event_seq"] = 9; summary["tool_stats"] = {"total_tool_calls": 5, "total_tool_results": 5}; write_json(summary_path, summary)
        ledger = workspace / "state/invocations.jsonl"
        ledger.write_text(ledger.read_text() + json.dumps({"event": "inspection_rejected", "request_id": EXPECTED["request_id"], "reason": "ticket_already_consumed"}) + "\n", encoding="utf-8")
        assert values(score(workspace, run_dir))["hr_child_lineage_and_single_probe_exact"] == 0

        workspace, run_dir, events_path, _, post_path = fixture(root / "nested-metadata")
        events = load(events_path); probe = events[2]["tool_result"]; probe["metadata"] = {"exit_code": probe.pop("exit_code"), "status": probe.pop("status")}; write_json(events_path, events)
        post = load(post_path); observed = post["post_tool_observations"][1]["tool_result"]; observed["metadata"] = {"exit_code": observed.pop("exit_code"), "status": observed.pop("status")}; write_json(post_path, post)
        scored = values(score(workspace, run_dir))
        assert scored["hr_child_probe_typed_terminal_metadata_exact"] == 0 and scored["hr_child_probe_post_tool_metadata_exact"] == 0

        workspace, run_dir, events_path, _, post_path = fixture(root / "weak-assignment")
        events = load(events_path); events[1]["tool_call"]["arguments"]["task"] = ASSIGNMENT.replace("guessed", "") ; write_json(events_path, events)
        post = load(post_path); post["post_tool_observations"][0]["arguments"]["task"] = events[1]["tool_call"]["arguments"]["task"]; write_json(post_path, post)
        assert values(score(workspace, run_dir))["hr_child_assignment_contract_exact"] == 0

        workspace, run_dir, events_path, _, _ = fixture(root / "leaky-completion")
        events = load(events_path); events[3]["content"] = CAPSULE + "\ntokens: 123 duration_ms: 50"; write_json(events_path, events)
        assert values(score(workspace, run_dir))["hr_parent_visible_completion_payload_minimal"] == 0

        workspace, run_dir, events_path, summary_path, _ = fixture(root / "history")
        events = load(events_path)
        read = {"event_type": "tool", "seq": 8, "principal_role": "main", "tool_call": {"tool_call_id": "read-1", "tool_name": "read_agent", "arguments": {"agent_id": "child-1"}}, "tool_result": {"tool_call_id": "read-1", "tool_name": "read_agent", "content": CAPSULE, "error": False}}
        events.insert(-1, read); events[-1]["seq"] = 9; write_json(events_path, events)
        summary = load(summary_path); summary["last_event_seq"] = 9; summary["tool_stats"] = {"total_tool_calls": 5, "total_tool_results": 5}; write_json(summary_path, summary)
        scored = values(score(workspace, run_dir))
        assert scored["hr_passive_completion_reactivation_order_exact"] == 0 and scored["ar_parent_boundary_and_protected_protocol_exact"] == 0

        workspace, run_dir, events_path, _, _ = fixture(root / "dishonest")
        events = [event for event in load(events_path) if event.get("event_type") != "task_completion"]; write_json(events_path, events)
        assert values(score(workspace, run_dir))["ar_output_mode_and_values_match_native_handoff"] == 0

        workspace, run_dir, events_path, _, _ = fixture(root / "premature")
        events = load(events_path); events[4]["seq"] = 3; write_json(events_path, events)
        scored = values(score(workspace, run_dir))
        assert scored["hr_passive_completion_reactivation_order_exact"] == 0 and scored["ar_completion_consumed_in_single_finalization"] == 0

        workspace, run_dir, events_path, summary_path, _ = fixture(root / "readback")
        events = load(events_path)
        readback = {"event_type": "tool", "seq": 8, "principal_role": "main", "tool_call": {"tool_call_id": "read-output", "tool_name": "read", "arguments": {"path": "/workspace/output/handoff_report.json"}}, "tool_result": {"tool_call_id": "read-output", "tool_name": "read", "content": CAPSULE, "error": False}}
        events.insert(-1, readback); events[-1]["seq"] = 9; write_json(events_path, events)
        summary = load(summary_path); summary["last_event_seq"] = 9; summary["tool_stats"] = {"total_tool_calls": 5, "total_tool_results": 5}; write_json(summary_path, summary)
        assert values(score(workspace, run_dir))["ar_completion_consumed_in_single_finalization"] == 0

        workspace, run_dir, events_path, summary_path, _ = fixture(root / "nested-dispatch")
        events = load(events_path)
        nested = {"event_type": "tool", "seq": 8, "principal_role": "child", "principal_id": "child-1", "tool_call": {"tool_call_id": "nested-dispatch", "tool_name": "collaboration__spawn_agent", "arguments": {"message": ASSIGNMENT}}, "tool_result": {"tool_call_id": "nested-dispatch", "tool_name": "collaboration__spawn_agent", "content": '{"task_name":"/root/child/grandchild"}', "error": False}}
        events.insert(-1, nested); events[-1]["seq"] = 9; write_json(events_path, events)
        summary = load(summary_path); summary["last_event_seq"] = 9; summary["tool_stats"] = {"total_tool_calls": 5, "total_tool_results": 5}; write_json(summary_path, summary)
        assert values(score(workspace, run_dir))["hr_exactly_one_native_child_dispatch_accepted"] == 0

    print("selftest: 18 binary rules pass golden including namespaced dispatch; 10 adversarial classes rejected")
    return 0


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
