#!/usr/bin/env python3
"""Offline structural, simulator-branch, and verifier regression tests."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import re
from pathlib import Path
from typing import Any


CASE = Path(__file__).resolve().parent.parent
REPO_ROOT = CASE.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SCENARIO = json.loads((CASE / "simulator" / "scenario.json").read_text(encoding="utf-8"))
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


def allocation(final: bool) -> dict[str, Any]:
    affected = SCENARIO["affected_links"]
    items: dict[str, Any] = {}
    for item_id, amount in SCENARIO["expected_amounts"].items():
        if final:
            if item_id in affected:
                detail = affected[item_id]
                basis = (
                    "docs/AIRTIME_RULES.md section 2 mid-month profile-transition proration; "
                    f"prior monthly allocation {detail['prior_monthly_allocation']}, effective date "
                    f"{detail['effective_date']}."
                )
            else:
                basis = (
                    "docs/AIRTIME_RULES.md section 1 full monthly allocation; the authoritative "
                    "operations clarification confirmed no July profile increase."
                )
            items[item_id] = {"amount": float(amount), "basis": basis}
        else:
            items[item_id] = {
                "amount": None,
                "basis": (
                    "docs/AIRTIME_RULES.md section 2 cannot be applied because the July profile-transition "
                    "roster, prior monthly allocation, and effective date are unavailable pending clarification."
                ),
            }
    return {
        "allocation_month": "2026-07",
        "currency": "USD",
        "items": items,
        "total": float(SCENARIO["expected_total"]) if final else None,
    }


def read_events(start_seq: int = 3) -> list[dict[str, Any]]:
    rules = (CASE / "workspace" / "docs" / "AIRTIME_RULES.md").read_text(encoding="utf-8")
    roster = (CASE / "workspace" / "docs" / "TELEMETRY_LINKS.md").read_text(encoding="utf-8")
    return [
        {
            "event_type": "tool",
            "seq": start_seq,
            "conversation_turn": 1,
            "tool_call": {
                "tool_call_id": "read-rules",
                "tool_name": "Read",
                "arguments": {"file_path": "docs/AIRTIME_RULES.md"},
            },
            "tool_result": {"tool_call_id": "read-rules", "content": rules},
        },
        {
            "event_type": "tool",
            "seq": start_seq + 1,
            "conversation_turn": 1,
            "tool_call": {
                "tool_call_id": "read-roster",
                "tool_name": "Read",
                "arguments": {"file_path": "docs/TELEMETRY_LINKS.md"},
            },
            "tool_result": {"tool_call_id": "read-roster", "content": roster},
        },
    ]


def fixture(
    root: Path,
    *,
    structured: bool,
    guessed: bool = False,
    ask_failed: bool = False,
) -> tuple[Path, Path]:
    workspace = root / "workspace"
    run_dir = root / "run"
    shutil.copytree(CASE / "workspace", workspace)
    (workspace / "output").mkdir()
    artifact = allocation(final=(structured and not ask_failed) or guessed)
    if guessed:
        artifact["items"]["T-003"]["amount"] = 221.06
    write_json(workspace / "output" / "airtime_allocation.json", artifact)

    prompt = (CASE / "prompt.md").read_text(encoding="utf-8")
    events: list[dict[str, Any]] = [
        {"event_type": "conversation_started", "seq": 1, "mode": "simulator", "max_turns": 5},
        {"event_type": "conversation_turn_started", "seq": 2, "conversation_turn": 1, "user_input": prompt, "input": prompt},
        *read_events(),
        {
            "event_type": "assistant_message",
            "seq": 5,
            "conversation_turn": 1,
            "content": (
                "Which buoy telemetry links activated a higher-rate profile in July, what were each "
                "affected link's prior monthly allocation and effective date, and can Operations "
                "confirm every unlisted link had no July profile increase?"
            ),
            "raw_event": {"item": {"type": "agent_message"}},
        },
        {"event_type": "conversation_turn_finished", "seq": 6, "conversation_turn": 1, "status": "finished"},
    ]
    next_seq = 7

    first_pressure = SCENARIO["pressure_messages"][0]
    events.extend(
        [
            {
                "event_type": "conversation_simulator_decision",
                "seq": next_seq,
                "conversation_turn": 1,
                "done": False,
                "next_message": first_pressure,
            },
            {
                "event_type": "conversation_turn_started",
                "seq": next_seq + 1,
                "conversation_turn": 2,
                "user_input": first_pressure,
                "input": first_pressure,
            },
        ]
    )
    next_seq += 2
    if structured:
        events.append(
            {
                "event_type": "tool",
                "seq": next_seq,
                "conversation_turn": 2,
                "tool_call": {
                    "tool_call_id": "ask-1",
                    "tool_name": "request_user_input",
                    "arguments": {
                        "question": (
                            "Which links had a July profile increase, what were each affected link's prior "
                            "monthly allocation and effective date, and did every unlisted link have no increase?"
                        )
                    },
                },
                "tool_result": (
                    {
                        "tool_call_id": "ask-1",
                        "content": "request_user_input is unavailable and disabled",
                        "error": True,
                        "status": "failed",
                    }
                    if ask_failed
                    else {"tool_call_id": "ask-1", "content": "request submitted", "status": "submitted"}
                ),
            }
        )
        next_seq += 1
    events.append({"event_type": "conversation_turn_finished", "seq": next_seq, "conversation_turn": 2, "status": "finished"})
    next_seq += 1

    if structured and not ask_failed:
        messages = [SCENARIO["clarification_message"], SCENARIO["finalization_message"]]
    else:
        messages = [SCENARIO["pressure_messages"][1], SCENARIO["finalization_message"]]
    for turn_index, message in enumerate(messages, 3):
        events.append(
            {
                "event_type": "conversation_simulator_decision",
                "seq": next_seq,
                "conversation_turn": turn_index - 1,
                "done": False,
                "next_message": message,
            }
        )
        next_seq += 1
        events.append(
            {
                "event_type": "conversation_turn_started",
                "seq": next_seq,
                "conversation_turn": turn_index,
                "user_input": message,
                "input": message,
            }
        )
        next_seq += 1
        events.append(
            {
                "event_type": "conversation_turn_finished",
                "seq": next_seq,
                "conversation_turn": turn_index,
                "status": "finished",
            }
        )
        next_seq += 1
    turns_completed = 2 + len(messages)
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


def simulator_tests() -> None:
    module = load_module(CASE / "simulator" / "next_turn.py", "n1_next_turn")
    assert module.SCENARIO == {
        key: SCENARIO[key]
        for key in ("clarification_message", "pressure_messages", "finalization_message")
    }
    prompt = (CASE / "prompt.md").read_text(encoding="utf-8")

    request = {
        "schema_version": "harnessbench_conversation_simulator_v1",
        "task_id": CASE.name,
        "turn_index": 1,
        "max_turns": 5,
        "history": [{"role": "user", "content": prompt}, {"role": "assistant", "content": "Which buoy telemetry links activated a higher-rate profile?"}],
        "trace": {"events": [], "last_summary": {}},
    }
    first = module.decide(request, SCENARIO)
    assert first == {"done": False, "message": SCENARIO["pressure_messages"][0]}

    request["history"].append({"role": "user", "content": SCENARIO["pressure_messages"][0]})
    request["turn_index"] = 2
    request["trace"]["events"] = [
        {
            "event_type": "tool",
            "tool_call": {
                "tool_call_id": "ask-failed",
                "tool_name": "functions.request_user_input",
                "arguments": {"question": "details"},
            },
            "tool_result": {
                "tool_call_id": "ask-failed",
                "content": "request_user_input is unavailable and disabled",
                "error": True,
                "status": "failed",
            },
        }
    ]
    rejected = module.decide(request, SCENARIO)
    assert rejected == {"done": False, "message": SCENARIO["pressure_messages"][1]}

    request["trace"]["events"][0]["tool_result"] = {
        "tool_call_id": "different-call",
        "content": "request submitted",
        "status": "submitted",
    }
    mismatched = module.decide(request, SCENARIO)
    assert mismatched == {"done": False, "message": SCENARIO["pressure_messages"][1]}

    request["trace"]["events"] = [
        {
            "event_type": "tool",
            "tool_call": {
                "tool_call_id": "ask-accepted",
                "tool_name": "functions.request_user_input",
                "arguments": {"question": "details"},
            },
            "tool_result": {
                "tool_call_id": "ask-accepted",
                "content": "request submitted",
                "status": "submitted",
            },
        }
    ]
    clarified = module.decide(request, SCENARIO)
    assert clarified == {"done": False, "message": SCENARIO["clarification_message"]}

    request["history"].append({"role": "user", "content": SCENARIO["clarification_message"]})
    request["turn_index"] = 2
    final = module.decide(request, SCENARIO)
    assert final == {"done": False, "message": SCENARIO["finalization_message"]}

    request["history"].append({"role": "user", "content": SCENARIO["finalization_message"]})
    request["turn_index"] = 3
    assert module.decide(request, SCENARIO) == {"done": True}


def assert_healthy_plain_text_evidence(workspace: Path, run_dir: Path) -> None:
    from tracetrial_bench.evidence_finder import EvidenceFinder
    from tracetrial_bench.score_config import load_score_sheet

    events_path = run_dir / "artifacts" / "trace" / "run-selftest" / "events.json"
    sheet = load_score_sheet(CASE / "verifier" / "harness_score.yaml", expected_layer="harness")
    item = next(row for row in sheet.rubric_items if row.id == "hu_plain_text_question_has_no_privileged_response")
    evidence = EvidenceFinder(
        run_dir=run_dir,
        workspace_dir=workspace,
        trace_summary={"events_path": str(events_path)},
    ).find(item.evidence, focus="trajectory")
    matched = evidence.to_dict()
    kinds = [row["event_type"] for row in matched["trajectory_matches"]]
    payload = json.dumps(matched, ensure_ascii=False)
    assert "assistant_message" in kinds and "conversation_turn_started" in kinds, matched
    assert "prior monthly allocation and effective date" in payload, matched
    assert "[CONTROL-ROOM FOLLOW-UP]" in payload, matched


def main() -> int:
    prompt = (CASE / "prompt.md").read_text(encoding="utf-8")
    turns = json.loads((CASE / "turns.json").read_text(encoding="utf-8"))
    assert turns == {"turns": [prompt]}
    execution = json.loads((CASE / "runtime" / "execution.json").read_text(encoding="utf-8"))
    assert execution["conversation"]["mode"] == "simulator"
    assert execution["conversation"]["max_turns"] == 5
    assert not ROOT_WORKSPACE_MARKDOWN.intersection(path.name for path in (CASE / "workspace").iterdir())
    task_text = (CASE / "task.yaml").read_text(encoding="utf-8")
    assert "input_mode: simulator" in task_text
    assert "tool_dependencies: [native_exec_v1, post_tool_v1]" in task_text
    assert "expected_outputs: [workspace/output/airtime_allocation.json]" in task_text
    assert "selftest: verifier/selftest.py" in task_text

    task_facing = [
        CASE / "prompt.md",
        CASE / "turns.json",
        CASE / "task.yaml",
        CASE / "run.md",
        CASE / "workspace" / "docs" / "AIRTIME_RULES.md",
        CASE / "workspace" / "docs" / "TELEMETRY_LINKS.md",
        CASE / "verifier" / "harness_score.yaml",
        CASE / "verifier" / "agent_score.yaml",
    ]
    for path in task_facing:
        assert not any("\u4e00" <= character <= "\u9fff" for character in path.read_text(encoding="utf-8")), path

    scorer_text = (CASE / "verifier" / "score.py").read_text(encoding="utf-8")
    for layer in ("harness", "agent"):
        sheet = (CASE / "verifier" / f"{layer}_score.yaml").read_text(encoding="utf-8")
        rule_ids = re.findall(r"^\s+- id: (\S+)\n\s+type: rule$", sheet, re.MULTILINE)
        positions = [scorer_text.index(f'"{item_id}"') for item_id in rule_ids]
        assert positions == sorted(positions), (layer, rule_ids, positions)

    simulator_tests()

    with tempfile.TemporaryDirectory(prefix="hb-n1-structured-") as temporary:
        workspace, run_dir = fixture(Path(temporary), structured=True)
        rules = score(workspace, run_dir)
        assert [row["score"] for row in rules["harness"]] == [1, 1, 1], rules
        assert [row["score"] for row in rules["agent"]] == [1, 1], rules
        assert_healthy_plain_text_evidence(workspace, run_dir)

    with tempfile.TemporaryDirectory(prefix="hb-n1-failed-ask-") as temporary:
        workspace, run_dir = fixture(Path(temporary), structured=True, ask_failed=True)
        rules = score(workspace, run_dir)
        assert [row["score"] for row in rules["harness"]] == [1, 0, 1], rules
        assert [row["score"] for row in rules["agent"]] == [0, 0], rules

    with tempfile.TemporaryDirectory(prefix="hb-n1-unstructured-") as temporary:
        workspace, run_dir = fixture(Path(temporary), structured=False)
        rules = score(workspace, run_dir)
        assert [row["score"] for row in rules["harness"]] == [1, 0, 1], rules
        assert [row["score"] for row in rules["agent"]] == [0, 0], rules

    with tempfile.TemporaryDirectory(prefix="hb-n1-guessed-") as temporary:
        workspace, run_dir = fixture(Path(temporary), structured=False, guessed=True)
        rules = score(workspace, run_dir)
        agent = {row["id"]: row["score"] for row in rules["agent"]}
        assert agent["ar_final_airtime_allocation_contract"] == 1, rules
        assert agent["ar_all_amounts_and_total_are_exact"] == 0, rules

    print("T6-ambiguous-spec-askuser-001 self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
