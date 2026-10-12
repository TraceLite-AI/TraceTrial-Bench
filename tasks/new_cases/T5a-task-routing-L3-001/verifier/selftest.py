#!/usr/bin/env python3
"""Offline end-to-end self-test for the national-park dense-routing case."""

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


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("task_routing_dense_server", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load simulator server")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    prompt = (CASE / "prompt.md").read_text(encoding="utf-8")
    turns = json.loads((CASE / "turns.json").read_text(encoding="utf-8"))["turns"]
    manifest = json.loads((CASE / "simulator" / "tools.json").read_text(encoding="utf-8"))
    truth = json.loads((HERE / "truth.json").read_text(encoding="utf-8"))
    scenario = json.loads((CASE / "simulator" / "scenario.json").read_text(encoding="utf-8"))
    tools = manifest["tools"]
    names = [tool["name"] for tool in tools]

    assert turns[0] == prompt and len(turns) == 10
    assert prompt.isascii() and all(turn.isascii() for turn in turns)
    assert len(names) == 38 and len(set(names)) == 38
    assert not list((CASE / "workspace").glob("*.md"))
    assert all(
        not (CASE / "workspace" / name).exists()
        for name in (
            "AGENTS.md",
            "BOOTSTRAP.md",
            "HEARTBEAT.md",
            "IDENTITY.md",
            "SOUL.md",
            "TOOLS.md",
            "USER.md",
        )
    )

    server = load_module(CASE / "simulator" / "server.py")

    # The audit state must remain sticky after the third distinct base family.
    audit_state = server.initial_state(scenario)
    for tool in truth["calibration_tools"]:
        audit_response = server.handle(
            {"tool": tool, "arguments": {"ref": truth["calibration_ref"]}},
            audit_state,
            scenario,
        )
    assert audit_response["_task_tool_content"].startswith("ROUTING_AUDIT assignment=")
    sticky_response = server.handle(
        {
            "tool": "skill_field_access_badge_reissue_preview",
            "arguments": {"ref": truth["calibration_ref"]},
        },
        audit_state,
        scenario,
    )
    assert sticky_response["_task_tool_content"].startswith("ROUTING_AUDIT assignment=")

    state = server.initial_state(scenario)
    records: list[dict] = []

    def call(tool, arguments):
        response = server.handle(
            {"tool": tool, "arguments": arguments}, state, scenario
        )
        records.append(
            {
                "seq": len(records) + 1,
                "request": {"tool": tool, "arguments": arguments},
                "response": response,
                "committed": True,
            }
        )
        return response["_task_tool_content"]

    calibration_results = [
        call(tool, {"ref": truth["calibration_ref"]})
        for tool in truth["calibration_tools"]
    ]
    assert calibration_results[0].startswith("NO_EFFECT assignment=")
    assert calibration_results[1].startswith("NO_EFFECT assignment=")
    assert calibration_results[2].startswith("ROUTING_AUDIT assignment=")

    evidence: dict[str, dict[str, str]] = {}
    for assignment, target in truth["assignments"].items():
        brief = call("ranger_brief", {"assignment": assignment})
        execution = call(target["skill"], {"ref": assignment})
        if target.get("outcome") == "truncated_execution":
            accepted = "unavailable"
        else:
            accepted = call(
                "ranger_submit", {"assignment": assignment, "value": target["receipt"]}
            )
        evidence[assignment] = {
            "brief": brief,
            "execution": execution,
            "accepted": accepted,
        }
    assert len(state["executed"]) == 11 and len(state["accepted"]) == 10

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        output = workspace / "output"
        output.mkdir(parents=True)
        routing = {}
        for assignment, target in truth["assignments"].items():
            degraded = target.get("outcome") in {
                "truncated_execution",
                "validation_unavailable",
            }
            routing[assignment] = {
                "state": "unavailable" if degraded else "executed",
                "receipt": None if degraded else target["receipt"],
                "skill": target["skill"],
            }
        (output / "ranger_routing.json").write_text(
            json.dumps(routing, indent=2) + "\n", encoding="utf-8"
        )
        note_lines = [
            "# Ranger Routing Notes",
            "",
            "## Channel Calibration",
            "",
            *[
                f"- `{tool}`: `{result}`"
                for tool, result in zip(
                    truth["calibration_tools"], calibration_results
                )
            ],
            "",
            "## Assignment Evidence",
            "",
            "| Assignment | Decision basis | Selected skill | Execution evidence | Submission evidence |",
            "|---|---|---|---|---|",
        ]
        for assignment, target in truth["assignments"].items():
            outcome = target.get("outcome", "normal")
            execution_evidence = (
                "unavailable"
                if outcome == "truncated_execution"
                else evidence[assignment]["execution"]
            )
            submission_evidence = (
                "unavailable"
                if outcome in {"truncated_execution", "validation_unavailable"}
                else evidence[assignment]["accepted"]
            )
            note_lines.append(
                f"| {assignment} | {scenario['assignments'][assignment]['brief']} Therefore {target['closest_sibling']} does not apply. | {target['skill']} | "
                f"{execution_evidence} | {submission_evidence} |"
            )
        (output / "ranger_routing_notes.md").write_text(
            "\n".join(note_lines) + "\n", encoding="utf-8"
        )

        run_dir = root / "run"
        ledger = run_dir / "artifacts" / "private_evidence" / "business_simulator"
        trace = run_dir / "artifacts" / "trace" / "synthetic"
        ledger.mkdir(parents=True)
        trace.mkdir(parents=True)
        (ledger / "ledger.jsonl").write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )

        events = [
            {"event_type": "conversation_started", "seq": 1},
            {
                "event_type": "model_input",
                "seq": 2,
                "conversation_turn": 1,
                "tools": tools,
            },
        ]
        sequence = 3
        for index, record in enumerate(records, start=1):
            arguments = record["request"]["arguments"]
            assignment = arguments.get("ref") or arguments.get("assignment")
            if assignment == truth["calibration_ref"] or assignment in list(truth["assignments"])[0:4]:
                turn = 2
            elif assignment in list(truth["assignments"])[4:8]:
                turn = 4
            else:
                turn = 7
            events.append(
                {
                    "event_type": "tool",
                    "seq": sequence,
                    "conversation_turn": turn,
                    "tool_call": {
                        "tool_call_id": f"call-{index}",
                        "tool_name": "mcp__harnessbench__" + record["request"]["tool"],
                        "arguments": arguments,
                    },
                    "tool_result": record["response"]["_task_tool_content"],
                }
            )
            sequence += 1
        (trace / "events.json").write_text(
            json.dumps(events, indent=2) + "\n", encoding="utf-8"
        )

        provider_evidence = (
            run_dir / "artifacts" / "container" / "model_call_budget"
        )
        provider_evidence.mkdir(parents=True)
        snapshot = {
            "request_body_valid": True,
            "provider_tool_count": len(tools),
            "declared_task_tool_count": len(tools),
            "observed_task_tool_count": len(tools),
            "present_names": names,
            "missing_names": [],
            "duplicate_names": [],
            "tools": [
                {
                    "canonical_name": tool["name"],
                    "observed_name": "mcp__harnessbench__" + tool["name"],
                    "description": tool["description"],
                    "input_schema": tool["inputSchema"],
                }
                for tool in tools
            ],
        }
        (provider_evidence / "events.jsonl").write_text(
            json.dumps({"admitted": True, "provider_tool_snapshot": snapshot})
            + "\n",
            encoding="utf-8",
        )

        completed = subprocess.run(
            [sys.executable, str(HERE / "score.py"), "--workspace", str(workspace)],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "HARNESSBENCH_RUN_DIR": str(run_dir)},
        )
        rule_layers = json.loads(completed.stdout)["rule_results"]
        assert [row["score"] for row in rule_layers["harness"]] == [1, 1, 1], rule_layers
        assert [row["score"] for row in rule_layers["agent"]] == [1, 1, 1], rule_layers

        evidence_root = workspace / ".harnessbench_verifier"
        rubric_ids = ["ar_decision_basis_quality"]
        for rubric_id in rubric_ids:
            packet = json.loads(
                (evidence_root / f"{rubric_id}.json").read_text(encoding="utf-8")
            )
            assert packet["rubric_id"] == rubric_id
            assert packet["deterministic_pass"] is True, packet
            assert "EVIDENCE_END" in packet["evidence"]
        assert len(list(evidence_root.glob("ar_decision_basis_quality*.json"))) == 3

        # Empty protected evidence must not be replaceable by model-authored names or files.
        negative_workspace = root / "negative-workspace"
        negative_workspace.mkdir()
        negative = subprocess.run(
            [
                sys.executable,
                str(HERE / "score.py"),
                "--workspace",
                str(negative_workspace),
            ],
            check=True,
            capture_output=True,
            text=True,
            env={key: value for key, value in os.environ.items() if key != "HARNESSBENCH_RUN_DIR"},
        )
        negative_layers = json.loads(negative.stdout)["rule_results"]
        assert [row["score"] for row in negative_layers["harness"]] == [0, 0, 0]
        assert [row["score"] for row in negative_layers["agent"]] == [0, 0, 0]
        for rubric_id in rubric_ids:
            packet = json.loads(
                (
                    negative_workspace
                    / ".harnessbench_verifier"
                    / f"{rubric_id}.json"
                ).read_text(encoding="utf-8")
            )
            assert packet["deterministic_pass"] is False, packet

    print(
        "T5a ranger-routing self-test passed: 38 native tools, 10 turns, "
        "calibrated signals, exact park routing, and protected reconciliation."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
