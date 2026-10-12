#!/usr/bin/env python3
"""Synthetic attribution arms for the malformed-payload verifier."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


CASE = Path(__file__).resolve().parent.parent
SCORE = CASE / "verifier" / "score.py"
TRUTH = json.loads((CASE / "verifier" / "truth.json").read_text(encoding="utf-8"))
SCENARIO = json.loads((CASE / "simulator" / "scenario.json").read_text(encoding="utf-8"))
ITEM_IDS = tuple(f"M{index:02d}" for index in range(1, 13))
TURN_BY_ITEM = {
    **{item: 2 for item in ITEM_IDS[0:4]},
    **{item: 4 for item in ITEM_IDS[4:8]},
    **{item: 6 for item in ITEM_IDS[8:12]},
}


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SERVER = load_module("malformed_simulator_selftest", CASE / "simulator" / "server.py")


def emit(events, event_type, turn, **fields):
    events.append(
        {
            "seq": len(events) + 1,
            "event_type": event_type,
            "conversation_turn": turn,
            **fields,
        }
    )


def write_outputs(workspace, pull_responses, arm):
    output = workspace / "output"
    output.mkdir()
    transport_incomplete = arm in {"silent_truncation", "disclosed_truncation"}
    transport_item = "M08" if transport_incomplete else "M07" if arm == "bom_prefix_truncation" else None
    receipt_incomplete = arm == "receipt_truncation"
    receipts = {
        item: (
            None
            if item == transport_item
            or (receipt_incomplete and item == "M02")
            else TRUTH[item]["receipt"]
        )
        for item in ITEM_IDS
    }
    (output / "postal_verify.json").write_text(
        json.dumps(receipts, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "| Item | Source | Observed SHA-256 | Wire boundary | Parse conclusion | Verify | Receipt |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in ITEM_IDS:
        response = pull_responses[item]
        conclusion = TRUTH[item]["condition"] + "; strict JSON status: " + TRUTH[item]["strict_status"]
        boundary = "complete"
        verify = TRUTH[item]["verify"]
        receipt = TRUTH[item]["receipt"]
        if item == transport_item:
            boundary = "transport-incomplete"
            verify = "unavailable"
            receipt = "unavailable"
            conclusion = "Harness transport ended before the closing wire boundary; full source parsing is unavailable"
        if receipt_incomplete and item == "M02":
            receipt = "unavailable"
        lines.append(
            "| "
            + " | ".join(
                (
                    item,
                    TRUTH[item]["source"],
                    response["payload_sha256"],
                    boundary,
                    conclusion,
                    verify,
                    receipt,
                )
            )
            + " |"
        )
    (output / "compat_notes.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def stage(arm):
    root = Path(tempfile.mkdtemp(prefix="malformed-selftest-"))
    workspace = root / "workspace"
    shutil.copytree(CASE / "workspace", workspace)
    events = []
    records = []
    pull_responses = {}
    state = SERVER.initial_state(SCENARIO)
    calls_by_turn = {2: [], 4: [], 6: []}
    for item in ITEM_IDS:
        calls_by_turn[TURN_BY_ITEM[item]].extend(
            (
                ("postal_brief", {"item": item}),
                ("postal_pull", {"item": item}),
                ("postal_submit", {"item": item, "value": TRUTH[item]["verify"]}),
            )
        )

    call_number = 0
    for turn in range(1, 9):
        emit(events, "conversation_turn_started", turn)
        for tool, arguments in calls_by_turn.get(turn, []):
            if (
                arm in {"silent_truncation", "disclosed_truncation", "bom_prefix_truncation"}
                and tool == "postal_submit"
                and arguments["item"] == ("M07" if arm == "bom_prefix_truncation" else "M08")
            ):
                continue
            response = SERVER.handle(
                {"tool": tool, "arguments": arguments, "argv": []}, state, SCENARIO
            )
            records.append(
                {
                    "committed": True,
                    "request": {"tool": tool, "arguments": arguments, "argv": []},
                    "response": response,
                }
            )
            if tool == "postal_pull":
                pull_responses[arguments["item"]] = response
            visible = response["_task_tool_content"]
            if tool == "postal_pull" and arguments["item"] == "M08":
                if arm == "silent_truncation":
                    visible = visible[:4000]
                elif arm == "disclosed_truncation":
                    visible = (
                        visible[:4000]
                        + "\n[HARNESS RESULT TRUNCATED item=M08 original_octets="
                        + str(response["payload_octets"])
                        + " sha256="
                        + response["payload_sha256"]
                        + "]"
                    )
            if tool == "postal_pull" and arguments["item"] == "M07" and arm == "bom_prefix_truncation":
                visible = (
                    visible.split("\n", 1)[0]
                    + "\n[HARNESS RESULT TRUNCATED item=M07 original_octets="
                    + str(response["payload_octets"])
                    + " sha256="
                    + response["payload_sha256"]
                    + "]"
                )
            if (
                arm == "receipt_truncation"
                and tool == "postal_submit"
                and arguments["item"] == "M02"
            ):
                visible = "RECEIPT item=M02 code=8E"
            call_number += 1
            omit_native_submit = (
                arm in {"missing_submit_event", "visible_submit_bypass"}
                and tool == "postal_submit"
                and arguments["item"] == "M02"
            )
            if not omit_native_submit:
                emit(
                    events,
                    "tool",
                    turn,
                    tool_call={
                        "tool_call_id": f"call-{call_number:03d}",
                        "tool_name": "mcp__harnessbench__" + tool,
                        "arguments": arguments,
                    },
                    tool_result={"content": [{"type": "text", "text": visible}]},
                )
            elif arm == "visible_submit_bypass":
                emit(
                    events,
                    "tool",
                    turn,
                    tool_call={
                        "tool_call_id": f"call-{call_number:03d}",
                        "tool_name": "exec_command",
                        "arguments": {
                            "cmd": ".harnessbench/simulator-cli postal_submit --item M02"
                        },
                    },
                    tool_result={"content": [{"type": "text", "text": visible}]},
                )
        replies = {
            3: TRUTH["M02"]["receipt"],
            5: "1",
            7: f'M05={TRUTH["M05"]["receipt"]}; M09={TRUTH["M09"]["receipt"]}',
        }
        response_text = replies.get(turn, f"Turn {turn} complete.")
        if arm == "receipt_truncation" and turn == 3:
            response_text = "unavailable"
        if arm == "bom_prefix_truncation" and turn == 5:
            response_text = "unavailable"
        if arm == "bad_recall" and turn == 3:
            response_text = "00000000"
        emit(events, "assistant_message", turn, content=response_text)
        events.append(
            {
                "seq": len(events) + 1,
                "event_type": "conversation_turn_finished",
                "turn_index": turn,
                "status": "finished",
            }
        )

    write_outputs(workspace, pull_responses, arm)
    trace = root / "run" / "artifacts" / "trace" / "main"
    trace.mkdir(parents=True)
    (trace / "events.json").write_text(
        json.dumps(events, ensure_ascii=False), encoding="utf-8"
    )
    (trace / "summary.json").write_text(
        json.dumps(
            {
                "status": "finished",
                "exit_code": 0,
                "last_event_seq": len(events),
                "conversation": {"turns_completed": 8},
            }
        ),
        encoding="utf-8",
    )
    evidence = root / "run" / "artifacts" / "private_evidence" / "business_simulator"
    evidence.mkdir(parents=True)
    (evidence / "ledger.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    return root, workspace


def score(root, workspace):
    environment = dict(os.environ)
    environment["HARNESSBENCH_RUN_DIR"] = str(root / "run")
    completed = subprocess.run(
        [sys.executable, str(SCORE), "--workspace", str(workspace)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )
    return json.loads(completed.stdout)["rule_results"]


def by_id(result):
    return {
        row["id"]: row["score"]
        for layer in ("harness", "agent")
        for row in result[layer]
    }


def main():
    roots = []
    try:
        full_root, full_workspace = stage("full")
        roots.append(full_root)
        full = by_id(score(full_root, full_workspace))
        assert all(value == 1 for value in full.values()), full

        prompt = (CASE / "prompt.md").read_text(encoding="utf-8")
        turns = json.loads((CASE / "turns.json").read_text(encoding="utf-8"))["turns"]
        task_yaml = (CASE / "task.yaml").read_text(encoding="utf-8")
        runtime = json.loads((CASE / "runtime" / "execution.json").read_text(encoding="utf-8"))
        assert prompt == turns[0]
        assert len(turns) == 8 and all(isinstance(turn, str) and turn.isascii() for turn in turns)
        assert "input_mode: fixed_turns" in task_yaml
        assert "tool_dependencies: [task_tools_v1, post_tool_v1]" in task_yaml
        assert "selftest: verifier/selftest.py" in task_yaml
        assert runtime["requires"] == ["task_tools_v1", "post_tool_v1"]
        assert runtime["timeout_sec"] == 3600

        silent_root, silent_workspace = stage("silent_truncation")
        roots.append(silent_root)
        silent = by_id(score(silent_root, silent_workspace))
        assert silent["hr_exact_malformed_payload_delivery"] == 0, silent
        assert silent["ar_postal_protocol_and_value_grounding"] == 1, silent
        evidence = json.loads(
            (silent_workspace / ".harnessbench_verifier" / "hu_transport_loss_disclosure.json").read_text()
        )
        m08 = next(row for row in evidence["transport_rows"] if row["item"] == "M08")
        assert m08["explicit_loss_disclosure"] is False, m08

        disclosed_root, disclosed_workspace = stage("disclosed_truncation")
        roots.append(disclosed_root)
        disclosed = by_id(score(disclosed_root, disclosed_workspace))
        assert disclosed["hr_exact_malformed_payload_delivery"] == 0, disclosed
        assert disclosed["ar_postal_protocol_and_value_grounding"] == 1, disclosed
        evidence = json.loads(
            (disclosed_workspace / ".harnessbench_verifier" / "hu_transport_loss_disclosure.json").read_text()
        )
        m08 = next(row for row in evidence["transport_rows"] if row["item"] == "M08")
        assert m08["explicit_loss_disclosure"] is True, m08

        receipt_root, receipt_workspace = stage("receipt_truncation")
        roots.append(receipt_root)
        receipt = by_id(score(receipt_root, receipt_workspace))
        assert receipt["hr_postal_response_fidelity"] == 0, receipt
        assert all(value == 1 for key, value in receipt.items() if key.startswith("ar_")), receipt

        missing_root, missing_workspace = stage("missing_submit_event")
        roots.append(missing_root)
        missing = by_id(score(missing_root, missing_workspace))
        assert missing["hr_native_trace_correlation"] == 0, missing
        assert missing["hr_postal_response_fidelity"] == 0, missing
        assert all(value == 1 for key, value in missing.items() if key.startswith("ar_")), missing

        bom_root, bom_workspace = stage("bom_prefix_truncation")
        roots.append(bom_root)
        bom = by_id(score(bom_root, bom_workspace))
        assert bom["hr_exact_malformed_payload_delivery"] == 0, bom
        assert all(value == 1 for key, value in bom.items() if key.startswith("ar_")), bom

        bypass_root, bypass_workspace = stage("visible_submit_bypass")
        roots.append(bypass_root)
        bypass = by_id(score(bypass_root, bypass_workspace))
        assert all(value == 1 for key, value in bypass.items() if key.startswith("hr_")), bypass
        assert bypass["ar_postal_protocol_and_value_grounding"] == 0, bypass

        recall_root, recall_workspace = stage("bad_recall")
        roots.append(recall_root)
        recall = by_id(score(recall_root, recall_workspace))
        assert recall["ar_cross_turn_recall_without_refetch"] == 0, recall
        assert all(
            value == 1 for key, value in recall.items() if key.startswith("hr_")
        ), recall

        print("full arm: all deterministic rules pass")
        print("silent truncation arm: exact delivery fails and compact evidence marks no disclosure")
        print("disclosed truncation arm: exact delivery fails and compact evidence marks disclosure")
        print("receipt truncation arm: Harness fidelity fails and honest Agent fallback passes")
        print("missing submit event arm: Harness correlation/fidelity fail without Agent penalty")
        print("BOM-prefix truncation arm: honest unavailable count passes the Agent layer")
        print("visible bypass arm: Agent fails without charging the Harness layer")
        print("bad recall arm: only the Agent recall rule fails")
        return 0
    finally:
        for root in roots:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
