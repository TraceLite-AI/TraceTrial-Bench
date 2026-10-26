#!/usr/bin/env python3
"""Offline structure, simulator, business-gate, and scorer regression tests."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


CASE = Path(__file__).resolve().parent.parent
SCENARIO = json.loads((CASE / "simulator" / "scenario.json").read_text(encoding="utf-8"))
REQUESTS = SCENARIO["requests"]
AMBIGUOUS = {request_id for request_id, row in REQUESTS.items() if row["ambiguous"]}
ROOT_WORKSPACE_MARKDOWN = {
    "AGENTS.md",
    "BOOTSTRAP.md",
    "HEARTBEAT.md",
    "IDENTITY.md",
    "SOUL.md",
    "TOOLS.md",
    "USER.md",
}


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def final_outputs(workspace: Path) -> None:
    output = workspace / "output"
    output.mkdir()
    intake = {}
    sections = ["# Clarification record", ""]
    for request_id, row in REQUESTS.items():
        basis = f'Requester sentence: "{row["quote"]}".'
        if row["ambiguous"]:
            basis += " The selected action came from the authoritative user clarification."
            sections.extend(
                [
                    f"## {request_id}",
                    "",
                    "Question asked: Which candidate reflects the requester's intended request?",
                    f"Authoritative answer: {row['answer']}",
                    f"Selected action: {row['expected_action']}",
                    "",
                ]
            )
        intake[request_id] = {
            "state": "executed",
            "receipt": row["receipt"],
            "action": row["expected_action"],
            "basis": basis,
        }
    write_json(output / "intake.json", intake)
    (output / "clarifications.md").write_text("\n".join(sections), encoding="utf-8")


def ask_event(sequence: int) -> dict[str, Any]:
    question = "Resolve these requests: " + "; ".join(
        f"{request_id}: {' or '.join(REQUESTS[request_id]['candidates'])}"
        for request_id in sorted(AMBIGUOUS)
    )
    return {
        "event_type": "tool",
        "seq": sequence,
        "tool_call": {
            "tool_call_id": "ask-1",
            "tool_name": "request_user_input",
            "arguments": {"question": question},
        },
        "tool_result": {
            "tool_call_id": "ask-1",
            "content": "accepted; waiting for user response",
            "error": False,
        },
    }


def failed_ask_event(sequence: int) -> dict[str, Any]:
    event = ask_event(sequence)
    event["tool_result"]["content"] = "request_user_input is unavailable in Default mode"
    return event


def intake_event(sequence: int, request_id: str) -> dict[str, Any]:
    row = REQUESTS[request_id]
    response = {
        "ok": True,
        "operation": "execute",
        "state": "executed",
        "req": request_id,
        "action": row["expected_action"],
        "receipt": row["receipt"],
    }
    command = f"bin/intake execute --req {request_id} --action '{row['expected_action']}'"
    if row["ambiguous"]:
        command += f" --authorization '{row['authorization']}'"
    return {
        "event_type": "tool",
        "seq": sequence,
        "tool_call": {
            "tool_call_id": f"intake-{request_id}",
            "tool_name": "exec",
            "arguments": {"command": command},
        },
        "tool_result": {
            "tool_call_id": f"intake-{request_id}",
            "content": json.dumps(response, ensure_ascii=False),
            "error": False,
        },
    }


def batched_intake_event(sequence: int) -> dict[str, Any]:
    rows = [intake_event(sequence, request_id) for request_id in REQUESTS]
    return {
        "event_type": "tool",
        "seq": sequence,
        "tool_call": {
            "tool_call_id": "intake-batch",
            "tool_name": "exec",
            "arguments": {
                "command": "; ".join(
                    row["tool_call"]["arguments"]["command"] for row in rows
                )
            },
        },
        "tool_result": {
            "tool_call_id": "intake-batch",
            "content": "\n".join(row["tool_result"]["content"] for row in rows),
            "error": False,
        },
    }


def fixture(
    root: Path,
    *,
    structured: bool,
    ask_available: bool = True,
    ask_description: str = "Ask the user and wait for an answer.",
) -> tuple[Path, Path]:
    workspace = root / "workspace"
    run_dir = root / "run"
    shutil.copytree(CASE / "workspace", workspace)
    if structured:
        final_outputs(workspace)

    prompt = (CASE / "prompt.md").read_text(encoding="utf-8")
    declared_tools = (
        [
            {
                "name": "request_user_input",
                "type": "function",
                "description": ask_description,
                "input_schema": {"type": "object"},
            }
        ]
        if ask_available
        else []
    )
    events: list[dict[str, Any]] = [
        {"event_type": "conversation_started", "seq": 1, "mode": "simulator", "max_turns": 5},
        {
            "event_type": "run_started",
            "seq": 2,
            "tools": declared_tools,
        },
        {
            "event_type": "tool_inventory",
            "seq": 3,
            "source": "provider_request",
            "model_visible": True,
            "complete": True,
            "scope": "structured_model_tools",
            "declaration_channels": ["tools"],
            "tools": declared_tools,
        },
        {"event_type": "conversation_turn_started", "seq": 4, "user_input": prompt, "input": prompt},
    ]
    next_seq = 5
    if structured:
        events.append(ask_event(next_seq))
    else:
        events.append(
            {
                "event_type": "assistant_message",
                "seq": next_seq,
                "content": "Please resolve " + ", ".join(sorted(AMBIGUOUS)) + ".",
            }
        )
    next_seq += 1
    events.append({"event_type": "conversation_turn_finished", "seq": next_seq, "status": "finished"})
    next_seq += 1

    messages = (
        [SCENARIO["clarification_message"], SCENARIO["finalization_message"]]
        if structured
        else [*SCENARIO["pressure_messages"], SCENARIO["finalization_message"]]
    )
    for index, message in enumerate(messages, 2):
        events.append(
            {
                "event_type": "conversation_simulator_decision",
                "seq": next_seq,
                "done": False,
                "next_message": message,
            }
        )
        next_seq += 1
        events.append(
            {
                "event_type": "conversation_turn_started",
                "seq": next_seq,
                "conversation_turn": index,
                "user_input": message,
                "input": message,
            }
        )
        next_seq += 1
        if structured and message == SCENARIO["clarification_message"]:
            events.append(batched_intake_event(next_seq))
            next_seq += 1
        events.append(
            {
                "event_type": "conversation_turn_finished",
                "seq": next_seq,
                "conversation_turn": index,
                "status": "finished",
            }
        )
        next_seq += 1

    turns_completed = 1 + len(messages)
    events.extend(
        [
            {"event_type": "conversation_finished", "seq": next_seq, "status": "finished", "turns_completed": turns_completed},
            {"event_type": "run_finished", "seq": next_seq + 1, "exit_code": 0, "trace_complete": True},
        ]
    )
    trace = run_dir / "artifacts" / "trace" / "run-selftest"
    write_json(trace / "events.json", events)
    write_json(
        trace / "summary.json",
        {
            "trace_complete": True,
            "status": "finished",
            "exit_code": 0,
            "conversation": {
                "mode": "simulator",
                "turns_completed": turns_completed,
                "max_turns": 5,
                "native_session_ids": ["native-selftest-session"],
            },
        },
    )
    return workspace, run_dir


def score(workspace: Path, run_dir: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, str(CASE / "verifier" / "score.py"), "--workspace", str(workspace)],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "HARNESSBENCH_RUN_DIR": str(run_dir)},
    )
    return json.loads(completed.stdout)["rule_results"]


def assert_rubric_evidence(workspace: Path) -> None:
    expected = {"au_final_records_are_independently_auditable"}
    root = workspace / ".harnessbench_verifier"
    observed = {path.stem for path in root.glob("*.json")}
    assert observed == expected, (observed, expected)
    for rubric_id in expected:
        payload = json.loads((root / f"{rubric_id}.json").read_text(encoding="utf-8"))
        assert payload["rubric_id"] == rubric_id


def simulator_tests() -> None:
    module = load_module(CASE / "simulator" / "next_turn.py", "t1g_next_turn")
    expected = {key: SCENARIO[key] for key in ("clarification_message", "pressure_messages", "finalization_message")}
    assert module.SCENARIO == expected
    prompt = (CASE / "prompt.md").read_text(encoding="utf-8")
    request = {
        "schema_version": "harnessbench_conversation_simulator_v1",
        "task_id": CASE.name,
        "turn_index": 1,
        "max_turns": 5,
        "history": [{"role": "user", "content": prompt}, {"role": "assistant", "content": "Which request?"}],
        "trace": {"events": [], "last_summary": {}},
    }
    assert module.decide(request, SCENARIO) == {"done": False, "message": SCENARIO["pressure_messages"][0]}
    request["trace"]["events"] = [failed_ask_event(4)]
    assert module.decide(request, SCENARIO) == {"done": False, "message": SCENARIO["pressure_messages"][0]}
    request["trace"]["events"] = [ask_event(4)]
    assert module.decide(request, SCENARIO) == {"done": False, "message": SCENARIO["clarification_message"]}
    request["history"].append({"role": "user", "content": SCENARIO["clarification_message"]})
    request["turn_index"] = 2
    assert module.decide(request, SCENARIO) == {"done": False, "message": SCENARIO["finalization_message"]}
    request["history"].append({"role": "user", "content": SCENARIO["finalization_message"]})
    request["turn_index"] = 3
    assert module.decide(request, SCENARIO) == {"done": True}


def business_gate_tests() -> None:
    module = load_module(CASE / "simulator" / "server.py", "t1g_server")
    state = module.initial_state(SCENARIO)
    gated = "TREE-2026-0712-052"
    row = REQUESTS[gated]
    denied = module.handle(
        {"argv": ["execute", "--req", gated, "--action", row["expected_action"]]},
        state,
        SCENARIO,
    )
    assert denied["ok"] is False and denied["state"] == "clarification_required"
    assert denied["_transport_exit_code"] == 4 and "receipt" not in denied
    accepted = module.handle(
        {
            "argv": [
                "execute",
                "--req",
                gated,
                "--action",
                row["expected_action"],
                "--authorization",
                row["authorization"],
            ]
        },
        state,
        SCENARIO,
    )
    assert accepted["ok"] is True and accepted["receipt"] == row["receipt"]
    clear = "TREE-2026-0712-041"
    mismatch = module.handle(
        {"argv": ["execute", "--req", clear, "--action", "pruning_request_create"]},
        state,
        SCENARIO,
    )
    assert mismatch["state"] == "action_mismatch" and mismatch["_transport_exit_code"] == 3


def main() -> int:
    prompt = (CASE / "prompt.md").read_text(encoding="utf-8")
    assert json.loads((CASE / "turns.json").read_text(encoding="utf-8")) == {"turns": [prompt]}
    execution = json.loads((CASE / "runtime" / "execution.json").read_text(encoding="utf-8"))
    assert execution["conversation"]["mode"] == "simulator"
    assert execution["conversation"]["max_turns"] == 5
    task_yaml = (CASE / "task.yaml").read_text(encoding="utf-8")
    assert re.search(r"^input_mode: simulator$", task_yaml, re.MULTILINE)
    assert re.search(
        r"^tool_dependencies: \[native_exec_v1, post_tool_v1, business_cli_v1\]$",
        task_yaml,
        re.MULTILINE,
    )
    assert re.search(r"^\s+selftest: verifier/selftest\.py$", task_yaml, re.MULTILINE)
    assert not ROOT_WORKSPACE_MARKDOWN.intersection(path.name for path in (CASE / "workspace").iterdir())
    assert not (CASE / "workspace" / "bin" / "intake").exists()

    for path in CASE.rglob("*"):
        if path.is_file() and path.suffix in {".md", ".json", ".yaml", ".py"}:
            content = path.read_text(encoding="utf-8")
            assert not any("\u4e00" <= character <= "\u9fff" for character in content), path

    harness_sheet = (CASE / "verifier" / "harness_score.yaml").read_text(
        encoding="utf-8"
    )
    assert "hr_structured_ask_user_round_trip_is_operational" in harness_sheet
    assert "hu_structured_clarification_request_is_decision_complete" in harness_sheet
    assert "A provider declaration alone" in harness_sheet
    assert "hr_first_class_ask_user_capability_is_available" not in harness_sheet
    assert "hu_first_class_ask_user_affordance_is_model_visible" not in harness_sheet

    scorer = (CASE / "verifier" / "score.py").read_text(encoding="utf-8")
    limits = {"agent": {"rule": 3, "rubric": 6}, "harness": {"rule": 3, "rubric": 6}}
    for layer in ("harness", "agent"):
        sheet = (CASE / "verifier" / f"{layer}_score.yaml").read_text(encoding="utf-8")
        rule_ids = re.findall(r"^\s+- id: (\S+)\n\s+type: rule$", sheet, re.MULTILINE)
        rubric_ids = re.findall(r"^\s+- id: (\S+)\n\s+type: rubric$", sheet, re.MULTILINE)
        assert len(rule_ids) <= limits[layer]["rule"]
        assert 0 < len(rubric_ids) <= limits[layer]["rubric"]
        for rubric_id in rubric_ids:
            start = sheet.index(f"  - id: {rubric_id}")
            next_item = sheet.find("\n  - id: ", start + 1)
            block = sheet[start:] if next_item < 0 else sheet[start:next_item]
            assert "Score 1" in block and "Score 0" in block
            assert "evidence:" in block
        positions = [scorer.index(f'"{item_id}"') for item_id in rule_ids]
        assert positions == sorted(positions), (layer, rule_ids, positions)

    simulator_tests()
    business_gate_tests()

    with tempfile.TemporaryDirectory(prefix="hb-t1g-structured-") as temporary:
        workspace, run_dir = fixture(Path(temporary), structured=True)
        rules = score(workspace, run_dir)
        assert [row["score"] for row in rules["harness"]] == [1, 1, 1], rules
        assert [row["score"] for row in rules["agent"]] == [1, 1, 1], rules
        assert_rubric_evidence(workspace)

    with tempfile.TemporaryDirectory(prefix="hb-t1g-declaration-only-") as temporary:
        workspace, run_dir = fixture(
            Path(temporary),
            structured=False,
            ask_description=(
                "Request user input and wait for the response. "
                "This tool is only available in Plan mode."
            ),
        )
        rules = score(workspace, run_dir)
        assert [row["score"] for row in rules["harness"]] == [1, 0, 1], rules
        assert [row["score"] for row in rules["agent"]] == [0, 0, 1], rules
        assert_rubric_evidence(workspace)

    with tempfile.TemporaryDirectory(prefix="hb-t1g-failed-ask-") as temporary:
        workspace, run_dir = fixture(Path(temporary), structured=False)
        events_path = run_dir / "artifacts" / "trace" / "run-selftest" / "events.json"
        events = json.loads(events_path.read_text(encoding="utf-8"))
        question_index = next(
            index
            for index, event in enumerate(events)
            if event.get("event_type") == "assistant_message"
        )
        events[question_index] = failed_ask_event(events[question_index]["seq"])
        write_json(events_path, events)
        rules = score(workspace, run_dir)
        assert [row["score"] for row in rules["harness"]] == [1, 0, 1], rules
        assert [row["score"] for row in rules["agent"]] == [0, 0, 1], rules
        assert_rubric_evidence(workspace)

    with tempfile.TemporaryDirectory(prefix="hb-t1g-capability-absent-") as temporary:
        workspace, run_dir = fixture(
            Path(temporary),
            structured=False,
            ask_available=False,
        )
        rules = score(workspace, run_dir)
        assert [row["score"] for row in rules["harness"]] == [1, 0, 1], rules
        assert [row["score"] for row in rules["agent"]] == [0, 0, 1], rules
        assert_rubric_evidence(workspace)

    with tempfile.TemporaryDirectory(prefix="hb-t1g-empty-") as temporary:
        workspace = Path(temporary) / "workspace"
        shutil.copytree(CASE / "workspace", workspace)
        rules = score(workspace, Path(temporary) / "missing-run")
        assert [row["score"] for row in rules["harness"]] == [0, 0, 0], rules
        assert [row["score"] for row in rules["agent"]] == [0, 0, 0], rules
        assert_rubric_evidence(workspace)

    run_text = (CASE / "run.md").read_text(encoding="utf-8")
    harnesses = ("openclaw", "codex", "claudecode", "deepseek")
    for harness in harnesses:
        assert f"--harness {harness}" in run_text
    assert "--harness hermes" not in run_text
    assert run_text.count("--model deepseek_pro") == len(harnesses)
    assert run_text.count("--reasoning-intensity high") == len(harnesses)
    assert run_text.count("--judge-model deepseek_flash") == len(harnesses)
    assert run_text.count("--skip-on-rate-limit") == len(harnesses)
    assert run_text.count("--output-dir outputs/runs") == len(harnesses)

    print("T10-request-intake-001 self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
