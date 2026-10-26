#!/usr/bin/env python3
"""Offline self-tests for the synchrotron machine-window lifecycle case."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
CASE = HERE.parent
ITEM_IDS = tuple(f"M{index:02d}" for index in range(1, 9))
STAGE_TURN = {**{item: 2 for item in ITEM_IDS[:4]}, **{item: 4 for item in ITEM_IDS[4:]}}
EXECUTION_TURN = {item: STAGE_TURN[item] + 1 for item in ITEM_IDS}
ATTRIBUTION_RUBRIC_IDS = {
    "hr_obstruction_left_a_way_out",
    "hr_obstruction_did_not_damage_the_result",
}


def load_server():
    spec = importlib.util.spec_from_file_location("dynamic_change_server", CASE / "simulator" / "server.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load simulator server")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def call(module, scenario, state, records, tool_events, turn, tool, arguments):
    request = {"tool": tool, "arguments": arguments}
    response = module.handle(request, state, scenario)
    records.append({"committed": True, "request": request, "response": response})
    tool_events.append(
        {
            "event_type": "tool",
            "conversation_turn": turn,
            "tool_call": {"tool_name": tool, "tool_call_id": f"call-{len(tool_events) + 1}", "arguments": arguments},
            "tool_result": {"content": response.get("_task_tool_content", ""), "error": response.get("ok") is not True},
        }
    )
    return response


def perfect_run(module, scenario):
    state = module.initial_state(scenario)
    records, tool_events, receipts = [], [], {}
    for items, release_key in ((ITEM_IDS[:4], "REL-A-7Q4M"), (ITEM_IDS[4:], "REL-B-3N8C")):
        staged = {}
        for item in items:
            discovered = call(module, scenario, state, records, tool_events, STAGE_TURN[item], "change_discover", {"item": item, "release_key": release_key})
            authorized = call(module, scenario, state, records, tool_events, STAGE_TURN[item], "change_act", {"item": item, "operation_tool": discovered["operation_tool"], "generation": discovered["generation"], "action": "authorize"})
            assert discovered["ok"] is True and authorized["ok"] is True
            staged[item] = (discovered, authorized)
        executed = {}
        for item in items:
            discovered, authorized = staged[item]
            result = call(module, scenario, state, records, tool_events, EXECUTION_TURN[item], "change_act", {"item": item, "operation_tool": discovered["operation_tool"], "generation": discovered["generation"], "action": "execute", "credential": authorized["credential"]})
            if result.get("error_type") == "CapabilityRetired":
                discovered = call(module, scenario, state, records, tool_events, EXECUTION_TURN[item], "change_discover", {"item": item, "release_key": release_key})
                authorized = call(module, scenario, state, records, tool_events, EXECUTION_TURN[item], "change_act", {"item": item, "operation_tool": discovered["operation_tool"], "generation": discovered["generation"], "action": "authorize"})
                result = call(module, scenario, state, records, tool_events, EXECUTION_TURN[item], "change_act", {"item": item, "operation_tool": discovered["operation_tool"], "generation": discovered["generation"], "action": "execute", "credential": authorized["credential"]})
            assert result["ok"] is True
            executed[item] = (discovered, result)
        for item in items:
            discovered, result = executed[item]
            submitted = call(module, scenario, state, records, tool_events, EXECUTION_TURN[item], "change_act", {"item": item, "operation_tool": discovered["operation_tool"], "generation": discovered["generation"], "action": "submit", "credential": result["execution_proof"], "value": result["value"]})
            status = call(module, scenario, state, records, tool_events, EXECUTION_TURN[item], "change_act", {"item": item, "operation_tool": discovered["operation_tool"], "generation": discovered["generation"], "action": "status"})
            assert submitted["ok"] is True and status.get("state") == "closed"
            receipts[item] = submitted["receipt"]
    return state, records, tool_events, receipts


def complete_trace(tool_events):
    events = [{"event_type": "conversation_started"}]
    for turn in range(1, 7):
        events.extend([
            {"event_type": "conversation_turn_started", "turn_index": turn},
            {"event_type": "run_started", "conversation_turn": turn},
        ])
        events.extend(row for row in tool_events if row["conversation_turn"] == turn)
        events.extend([
            {"event_type": "assistant_message", "conversation_turn": turn, "content": "ok"},
            {"event_type": "run_finished", "conversation_turn": turn, "exit_code": 0, "trace_complete": True},
            {"event_type": "conversation_turn_finished", "turn_index": turn, "status": "finished"},
        ])
    events.extend([
        {"event_type": "conversation_finished", "status": "finished", "turns_completed": 6},
        {"event_type": "run_finished", "exit_code": 0, "trace_complete": True},
    ])
    for seq, event in enumerate(events, 1):
        event["seq"] = seq
    return events


def test_simulator_and_scorer():
    module = load_server()
    scenario = json.loads((CASE / "simulator" / "scenario.json").read_text(encoding="utf-8"))
    state, records, tools, receipts = perfect_run(module, scenario)
    assert len(records) == 46
    assert sum(row["response"].get("error_type") == "CapabilityRetired" for row in records) == 2
    assert all(state["items"][item]["phase"] == "closed" for item in ITEM_IDS)
    truth = json.loads((HERE / "truth.json").read_text(encoding="utf-8"))["items"]
    events = complete_trace(tools)

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        run_dir, workspace = root / "run", root / "workspace"
        evidence = run_dir / "artifacts" / "private_evidence" / "business_simulator"
        trace = run_dir / "artifacts" / "trace" / "normalized"
        output = workspace / "output"
        evidence.mkdir(parents=True)
        trace.mkdir(parents=True)
        output.mkdir(parents=True)
        (evidence / "ledger.jsonl").write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in records), encoding="utf-8")
        (evidence / "final_state.json").write_text(json.dumps(state, separators=(",", ":")) + "\n", encoding="utf-8")
        (trace / "events.json").write_text(json.dumps(events, separators=(",", ":")) + "\n", encoding="utf-8")
        (trace / "summary.json").write_text(json.dumps({"status": "finished", "trace_complete": True, "exit_code": 0, "last_event_seq": len(events), "conversation": {"turns_completed": 6}}, separators=(",", ":")) + "\n", encoding="utf-8")
        (output / "change_window.json").write_text(json.dumps(receipts, separators=(",", ":")) + "\n", encoding="utf-8")
        (output / "lifecycle_audit.json").write_text(json.dumps(truth, separators=(",", ":")) + "\n", encoding="utf-8")
        (workspace / "worklist.json").write_text((CASE / "workspace" / "worklist.json").read_text(encoding="utf-8"), encoding="utf-8")
        completed = subprocess.run([sys.executable, str(HERE / "score.py"), "--workspace", str(workspace)], check=True, capture_output=True, text=True, env={**os.environ, "HARNESSBENCH_RUN_DIR": str(run_dir)})
        payload = json.loads(completed.stdout)
        results = [*payload["rule_results"]["harness"], *payload["rule_results"]["agent"]]
        assert len(results) == 6
        assert not [row for row in results if row["score"] != 1], results
        packets = sorted((workspace / ".harnessbench_verifier").glob("*.json"))
        assert len(packets) == 4
        assert all("EVIDENCE_END" in path.read_text(encoding="utf-8") for path in packets)


def test_guardrails():
    module = load_server()
    scenario = json.loads((CASE / "simulator" / "scenario.json").read_text(encoding="utf-8"))
    state = module.initial_state(scenario)
    response = module.handle({"tool": "change_discover", "arguments": {"item": "M01", "release_key": "WRONG-KEY"}}, state, scenario)
    assert response["error_type"] == "ReleaseUnavailable" and state["items"]["M01"]["phase"] == "unseen"
    response = module.handle({"tool": "change_act", "arguments": {"item": "M01", "operation_tool": "sr_orbit_k7", "generation": 1, "action": "submit", "credential": "EXEC-M01-4V8N", "value": "3390E898"}}, state, scenario)
    assert response["error_type"] == "DiscoveryRequired" and state["items"]["M01"]["submit_count"] == 0


def test_preledger_rejection_attribution():
    module = load_server()
    scenario = json.loads((CASE / "simulator" / "scenario.json").read_text(encoding="utf-8"))
    state, records, tools, receipts = perfect_run(module, scenario)
    tools.append(
        {
            "event_type": "tool",
            "conversation_turn": 2,
            "tool_call": {
                "tool_name": "change_act",
                "tool_call_id": "bridge-rejected-extra",
                "arguments": {"item": "M01"},
            },
            "tool_result": {
                "content": "Schema validation failed: required operation_tool, generation, and action",
                "error": True,
            },
        }
    )
    events = complete_trace(tools)
    truth = json.loads((HERE / "truth.json").read_text(encoding="utf-8"))["items"]

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        run_dir, workspace = root / "run", root / "workspace"
        evidence = run_dir / "artifacts" / "private_evidence" / "business_simulator"
        trace = run_dir / "artifacts" / "trace" / "normalized"
        output = workspace / "output"
        evidence.mkdir(parents=True)
        trace.mkdir(parents=True)
        output.mkdir(parents=True)
        (evidence / "ledger.jsonl").write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in records), encoding="utf-8")
        (evidence / "final_state.json").write_text(json.dumps(state, separators=(",", ":")) + "\n", encoding="utf-8")
        (trace / "events.json").write_text(json.dumps(events, separators=(",", ":")) + "\n", encoding="utf-8")
        (trace / "summary.json").write_text(json.dumps({"status": "finished", "trace_complete": True, "exit_code": 0, "last_event_seq": len(events), "conversation": {"turns_completed": 6}}, separators=(",", ":")) + "\n", encoding="utf-8")
        (output / "change_window.json").write_text(json.dumps(receipts, separators=(",", ":")) + "\n", encoding="utf-8")
        (output / "lifecycle_audit.json").write_text(json.dumps(truth, separators=(",", ":")) + "\n", encoding="utf-8")
        (workspace / "worklist.json").write_text((CASE / "workspace" / "worklist.json").read_text(encoding="utf-8"), encoding="utf-8")
        completed = subprocess.run([sys.executable, str(HERE / "score.py"), "--workspace", str(workspace)], check=True, capture_output=True, text=True, env={**os.environ, "HARNESSBENCH_RUN_DIR": str(run_dir)})
        payload = json.loads(completed.stdout)
        harness = {row["id"]: row["score"] for row in payload["rule_results"]["harness"]}
        agent = {row["id"]: row["score"] for row in payload["rule_results"]["agent"]}
        assert all(harness.values()), harness
        assert agent == {
            "ar_output_contract": 1,
            "ar_complete_grounded_delivery": 1,
            "ar_lifecycle_discipline": 0,
        }, agent
        packet = json.loads((workspace / ".harnessbench_verifier" / "au_m01_m02_recovery_audit.json").read_text(encoding="utf-8"))
        m01 = next(row for row in packet["evidence"] if row.get("item") == "M01")
        assert m01["rejection_groups"] == [
            {
                "action": "change_act(invalid arguments)",
                "error_type": "SchemaValidationError",
                "error_text": "Schema validation failed: required operation_tool, generation, and action\nTrue",
                "source": "native_preledger",
                "count": 1,
            }
        ], m01
        assert m01["successful_recovery_milestones"], m01
        assert m01["verbatim_recovery_note"] == "none", m01


def test_contracts():
    prompt = (CASE / "prompt.md").read_text(encoding="utf-8").rstrip("\n")
    turns = json.loads((CASE / "turns.json").read_text(encoding="utf-8"))["turns"]
    assert prompt == turns[0] and len(turns) == 6
    assert not list((CASE / "workspace").glob("*.md"))
    for name, rule_count, rubric_count in (("agent_score.yaml", 3, 4), ("harness_score.yaml", 3, 5)):
        text = (HERE / name).read_text(encoding="utf-8")
        assert text.count("type: rule") == rule_count
        assert text.count("type: rubric") == rubric_count
        rubric_blocks = {}
        for block in text.split("  - id:")[1:]:
            if "type: rubric" in block:
                rubric_id = block.splitlines()[0].strip()
                rubric_blocks[rubric_id] = block
                assert "Score 1" in block and "Score 0" in block
                assert "evidence:" in block
                if rubric_id not in ATTRIBUTION_RUBRIC_IDS:
                    assert "missing" in block and "incomplete" in block
        if name == "harness_score.yaml":
            assert ATTRIBUTION_RUBRIC_IDS <= rubric_blocks.keys()
            for rubric_id in ATTRIBUTION_RUBRIC_IDS:
                assert "tool_call.tool_call_id" in rubric_blocks[rubric_id]


def main():
    test_contracts()
    test_simulator_and_scorer()
    test_guardrails()
    test_preledger_rejection_attribution()
    print("selftest: ok")


if __name__ == "__main__":
    main()
