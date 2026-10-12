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
    spec = importlib.util.spec_from_file_location("submersible_dense_server", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load simulator server")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def score(workspace: Path, run_dir: Path) -> dict:
    completed = subprocess.run(
        [sys.executable, str(HERE / "score.py"), "--workspace", str(workspace)],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "HARNESSBENCH_RUN_DIR": str(run_dir)},
    )
    return json.loads(completed.stdout)["rule_results"]


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
        brief = call("dive_brief", {"item": item})
        effect = call(target["action"], {"ref": item})
        accepted = call("dive_submit", {"item": item, "value": target["receipt"]})
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
        (output / "dive_action_receipts.json").write_text(
            json.dumps(receipt_payload, indent=2) + "\n", encoding="utf-8"
        )
        note_lines = [
            "# Submersible Dive Action Notes",
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
        (output / "dive_action_notes.md").write_text(
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
        events = [
            {"event_type": "conversation_started", "seq": 1},
            {"event_type": "model_input", "seq": 2, "conversation_turn": 1, "tools": tools},
        ]
        recall = truth["recall_expectations"]
        turn_answers = {
            3: (
                f"{recall['turn_3']['item']}: action={recall['turn_3']['action']}; "
                f"receipt={recall['turn_3']['receipt']}"
            ),
            6: "The matching released items are " + ", ".join(recall["turn_6"]["items"]) + ".",
            8: "; ".join(
                f"{item}: receipt={receipt}"
                for item, receipt in recall["turn_8"]["receipts"].items()
            ),
        }
        sequence = 3
        for turn in range(1, 11):
            events.extend(
                [
                    {"event_type": "conversation_turn_started", "seq": sequence, "turn_index": turn},
                    {"event_type": "run_started", "seq": sequence + 1, "conversation_turn": turn},
                    {
                        "event_type": "assistant_message",
                        "seq": sequence + 2,
                        "conversation_turn": turn,
                        "content": turn_answers.get(turn, "synthetic non-recall response"),
                    },
                ]
            )
            sequence += 3
        for index, record in enumerate(records, start=1):
            arguments = record["request"]["arguments"]
            item = arguments.get("ref") or arguments.get("item")
            turn = 2 if item == truth["calibration_ref"] or item.endswith(("001", "002", "003", "004")) else 4 if item.endswith(("005", "006", "007", "008")) else 7
            events.append(
                {
                    "event_type": "tool",
                    "seq": sequence,
                    "conversation_turn": turn,
                    "tool_call": {
                        "tool_call_id": f"call-{index}",
                        "tool_name": "mcp__harnessbench__" + record["request"]["tool"],
                        "arguments": record["request"]["arguments"],
                    },
                    "tool_result": record["response"]["_task_tool_content"],
                }
            )
            sequence += 1
        for turn in range(1, 11):
            events.extend(
                [
                    {"event_type": "run_finished", "seq": sequence, "conversation_turn": turn, "exit_code": 0, "trace_complete": True},
                    {"event_type": "conversation_turn_finished", "seq": sequence + 1, "turn_index": turn, "status": "finished"},
                ]
            )
            sequence += 2
        events.append({"event_type": "conversation_finished", "seq": sequence, "status": "finished", "turns_completed": 10})
        (trace / "events.json").write_text(
            json.dumps(events, indent=2) + "\n", encoding="utf-8"
        )
        provider_evidence = run_dir / "artifacts" / "container" / "model_call_budget"
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
            json.dumps({"admitted": True, "provider_tool_snapshot": snapshot}) + "\n",
            encoding="utf-8",
        )

        rule_layers = score(workspace, run_dir)
        assert [row["score"] for row in rule_layers["harness"]] == [1, 1, 1], rule_layers
        assert [row["score"] for row in rule_layers["agent"]] == [1, 1, 1], rule_layers
        evidence_root = workspace / ".harnessbench_verifier"
        for rubric in (
            "ar_audit_note_grounding_first_half",
            "ar_audit_note_grounding_second_half",
            "ar_cross_turn_recall_is_exact",
        ):
            packet = json.loads((evidence_root / f"{rubric}.json").read_text())
            assert packet["rubric_id"] == rubric and "EVIDENCE_END" in packet["evidence"], packet

        recall_packet = json.loads(
            (evidence_root / "ar_cross_turn_recall_is_exact.json").read_text()
        )
        assert all(
            row["required_tokens_present"]
            for row in json.loads(
                recall_packet["evidence"].removeprefix("EVIDENCE_START ").removesuffix(
                    " EVIDENCE_END"
                )
            ).values()
        ), recall_packet

        # Real fixed-turn runs contain the authoritative composite trace plus
        # nested per-turn traces. The scorer must select the former, not merge
        # both levels and double every native call.
        nested_trace = trace / "turns" / "raw-turn" / "events.json"
        nested_trace.parent.mkdir(parents=True)
        nested_events = []
        for event in events:
            if event.get("event_type") != "tool":
                continue
            duplicate = json.loads(json.dumps(event))
            duplicate["tool_call"]["tool_call_id"] = (
                "native-" + duplicate["tool_call"]["tool_call_id"]
            )
            nested_events.append(duplicate)
        nested_trace.write_text(json.dumps(nested_events), encoding="utf-8")
        nested_layers = score(workspace, run_dir)
        assert [row["score"] for row in nested_layers["harness"]] == [1, 1, 1], nested_layers
        assert [row["score"] for row in nested_layers["agent"]] == [1, 1, 1], nested_layers

        # Codex may export every MCP call through both its live item stream and
        # its native archive, with unrelated item_* and call_* IDs. The local
        # scorer must pair those representations without treating them as two
        # executions.
        codex_events = json.loads(json.dumps(events))
        archive_duplicates = []
        for event in codex_events:
            if event.get("event_type") != "tool":
                continue
            call_payload = event["tool_call"]
            call_id = call_payload["tool_call_id"]
            result_text = event["tool_result"]
            event["raw_event"] = {
                "tool_call": {
                    "type": "item.started",
                    "item": {"id": call_id, "type": "mcp_tool_call"},
                },
                "tool_result": {
                    "type": "item.completed",
                    "item": {
                        "id": call_id,
                        "type": "mcp_tool_call",
                        "result": {"content": [{"type": "text", "text": result_text}]},
                    },
                },
            }
            duplicate = json.loads(json.dumps(event))
            native_id = "native-" + call_id
            duplicate["tool_call"]["tool_call_id"] = native_id
            duplicate["tool_result"] = {
                "tool_call_id": native_id,
                "content": "Wall time: 0.001 seconds\nOutput:\n"
                + json.dumps([{"type": "text", "text": result_text}]),
            }
            duplicate["raw_event"] = {
                "tool_call": {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "call_id": native_id,
                    },
                },
                "tool_result": {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": native_id,
                        "output": "Wall time: 0.001 seconds\nOutput:\n"
                        + json.dumps([{"type": "text", "text": result_text}]),
                    },
                },
            }
            archive_duplicates.append(duplicate)
        codex_events[-1:-1] = archive_duplicates
        (trace / "events.json").write_text(json.dumps(codex_events), encoding="utf-8")
        codex_layers = score(workspace, run_dir)
        assert [row["score"] for row in codex_layers["harness"]] == [1, 1, 1], codex_layers
        assert [row["score"] for row in codex_layers["agent"]] == [1, 1, 1], codex_layers

        # A second real invocation still appears once per export source after
        # pairing, so task-local de-duplication cannot hide a one-shot breach.
        repeated_pair = []
        first_stream_event = next(
            event for event in codex_events if event.get("event_type") == "tool"
        )
        for source_event in (first_stream_event, archive_duplicates[0]):
            duplicate = json.loads(json.dumps(source_event))
            duplicate_id = "repeat-" + duplicate["tool_call"]["tool_call_id"]
            duplicate["tool_call"]["tool_call_id"] = duplicate_id
            if isinstance(duplicate.get("tool_result"), dict):
                duplicate["tool_result"]["tool_call_id"] = duplicate_id
            repeated_pair.append(duplicate)
        repeated_events = codex_events[:-1] + repeated_pair + codex_events[-1:]
        (trace / "events.json").write_text(json.dumps(repeated_events), encoding="utf-8")
        repeated_layers = score(workspace, run_dir)
        assert repeated_layers["harness"][2]["score"] == 0, repeated_layers
        assert repeated_layers["agent"][2]["score"] == 0, repeated_layers
        (trace / "events.json").write_text(json.dumps(events), encoding="utf-8")

        # Shape-correct fabricated calibration and item evidence must not pass
        # the exact grounded-results rule.
        notes_path = output / "dive_action_notes.md"
        healthy_notes = notes_path.read_text(encoding="utf-8")
        corrupted_lines = []
        for line in healthy_notes.splitlines():
            if line.startswith("- `"):
                corrupted_lines.append("- fabricated_tool: fabricated_result")
            elif line.startswith("| DS-2026-08-"):
                cells = [cell.strip() for cell in line[1:-1].split("|")]
                corrupted_lines.append(
                    f"| {cells[0]} | {cells[1]} | fabricated_action | "
                    "fabricated_effect | fabricated_submission |"
                )
            else:
                corrupted_lines.append(line)
        notes_path.write_text("\n".join(corrupted_lines) + "\n", encoding="utf-8")
        corrupted_layers = score(workspace, run_dir)
        assert [row["score"] for row in corrupted_layers["agent"]] == [1, 0, 1], corrupted_layers
        notes_path.write_text(healthy_notes, encoding="utf-8")

        # Duplicate JSON members are invalid even when a normal JSON parser
        # would silently retain the last copy.
        receipts_path = output / "dive_action_receipts.json"
        healthy_receipts = receipts_path.read_text(encoding="utf-8")
        duplicate_entry = json.dumps(
            {"DS-2026-08-001": receipt_payload["DS-2026-08-001"]}, indent=2
        )[1:-1].strip()
        receipts_path.write_text(
            "{\n" + duplicate_entry + ",\n" + healthy_receipts.lstrip()[1:],
            encoding="utf-8",
        )
        duplicate_layers = score(workspace, run_dir)
        assert duplicate_layers["agent"][0]["score"] == 0, duplicate_layers
        receipts_path.write_text(healthy_receipts, encoding="utf-8")

        # Wrong recall answers stay visible in a dedicated Judge packet rather
        # than disappearing from the evaluation entirely.
        bad_events = json.loads((trace / "events.json").read_text(encoding="utf-8"))
        for event in bad_events:
            if (
                event.get("event_type") == "assistant_message"
                and event.get("conversation_turn") in {3, 6, 8}
            ):
                event["content"] = "synthetic"
        (trace / "events.json").write_text(json.dumps(bad_events), encoding="utf-8")
        score(workspace, run_dir)
        bad_recall_packet = json.loads(
            (evidence_root / "ar_cross_turn_recall_is_exact.json").read_text()
        )
        bad_recall = json.loads(
            bad_recall_packet["evidence"].removeprefix("EVIDENCE_START ").removesuffix(
                " EVIDENCE_END"
            )
        )
        assert all(
            row["assistant_response"] == "synthetic"
            and row["required_tokens_present"] is False
            for row in bad_recall.values()
        ), bad_recall_packet

    print("T1e submersible dense-tool self-test passed: 50 tools, 10 turns, full signal and output reconciliation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
