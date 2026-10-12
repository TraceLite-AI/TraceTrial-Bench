#!/usr/bin/env python3
"""Offline end-to-end self-test for the migrated dense-tool case."""

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
    spec = importlib.util.spec_from_file_location("monthend_dense_server", path)
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
    assert len(names) == 50 and len(set(names)) == 50
    assert not list((CASE / "workspace").glob("*.md"))

    server = load_module(CASE / "simulator" / "server.py")
    state = server.initial_state(scenario)
    records = []

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
    assert calibration_results[0].startswith("NO_EFFECT item=")
    assert calibration_results[1].startswith("NO_EFFECT item=")
    assert "AUDIT_BLOCK item=" in calibration_results[2]

    evidence = {}
    for item, target in truth["items"].items():
        brief = call("proc_brief", {"item": item})
        effect = call(target["action"], {"ref": item})
        accepted = call("proc_submit", {"item": item, "value": target["receipt"]})
        evidence[item] = {"brief": brief, "effect": effect, "accepted": accepted}
    assert len(state["effective"]) == 12 and len(state["accepted"]) == 12

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        output = workspace / "output"
        output.mkdir(parents=True)
        receipt_payload = {
            item: {
                "state": "effective",
                "action": target["action"],
                "receipt": target["receipt"],
            }
            for item, target in truth["items"].items()
        }
        (output / "month_end_receipts.json").write_text(
            json.dumps(receipt_payload, indent=2) + "\n", encoding="utf-8"
        )
        note_lines = [
            "# Release Window Action Notes",
            "",
            "## Channel Calibration",
            "",
            *[
                f"- `{tool}`: `{result}`"
                for tool, result in zip(truth["calibration_tools"], calibration_results)
            ],
            "",
            "## Item Evidence",
            "",
            "| Item | Brief basis | Selected action | Effective evidence | Submission evidence |",
            "|---|---|---|---|---|",
        ]
        for item, target in truth["items"].items():
            note_lines.append(
                f"| {item} | {scenario['items'][item]['brief']} | {target['action']} | "
                f"{evidence[item]['effect']} | {evidence[item]['accepted']} |"
            )
        (output / "action_notes.md").write_text(
            "\n".join(note_lines) + "\n", encoding="utf-8"
        )

        run_dir = root / "run"
        ledger = run_dir / "artifacts" / "private_evidence" / "business_simulator"
        trace = run_dir / "artifacts" / "trace" / "synthetic"
        ledger.mkdir(parents=True)
        trace.mkdir(parents=True)
        (ledger / "ledger.jsonl").write_text(
            "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
        )
        events = [{"event_type": "model_input", "seq": 1, "tools": tools}]
        for index, record in enumerate(records, start=2):
            events.append(
                {
                    "event_type": "tool",
                    "seq": index,
                    "tool_call": {
                        "tool_call_id": f"call-{index}",
                        "tool_name": "mcp__harnessbench__" + record["request"]["tool"],
                        "arguments": record["request"]["arguments"],
                    },
                    "tool_result": record["response"]["_task_tool_content"],
                }
            )
        (trace / "events.json").write_text(
            json.dumps(events, indent=2) + "\n", encoding="utf-8"
        )

        environment = {**os.environ, "HARNESSBENCH_RUN_DIR": str(run_dir)}
        completed = subprocess.run(
            [sys.executable, str(HERE / "score.py"), "--workspace", str(workspace)],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        rules = json.loads(completed.stdout)["rule_results"]["harness"]
        assert [row["score"] for row in rules] == [1, 1], rules
        evidence_root = workspace / ".harnessbench_verifier"
        expected_flags = {
            "hr_effective_receipt_reaches_model": "all_delivered_exactly_once",
            "hr_no_effect_reaches_model": "all_delivered_exactly_once",
            "hr_audit_block_reaches_model": "all_delivered_exactly_once",
            "hr_variant_names_distinguishable": "all_snapshots_complete_and_distinguishable",
            "ar_all_12_covered_and_effective": "complete_private_workflow",
            "ar_action_and_receipt_exact": "all_outputs_and_note_evidence_exact",
            "ar_no_blind_trying_and_honest_state": "pass_conditions_met",
        }
        for rubric, flag in expected_flags.items():
            packet = json.loads((evidence_root / f"{rubric}.json").read_text())
            assert packet[flag] is True, (rubric, flag, packet)

    print("T1e dense-tool self-test passed: 50 tools, 10 turns, full signal and output reconciliation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
