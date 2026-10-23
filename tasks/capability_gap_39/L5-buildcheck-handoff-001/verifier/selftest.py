#!/usr/bin/env python3
"""Adversarial self-tests for the L4 one-shot handoff verifier."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile

import score


ASSIGNMENT = """Run exactly once:
python3 tools/inspect_bundle.py --ticket state/audit_ticket.json --target input/bundle.json
You may access only tools/inspect_bundle.py, state/audit_ticket.json,
state/invocations.jsonl, and input/bundle.json. Do not write an intermediate artifact.
Return exactly one compact one line JSON value with no markdown and no extra text:
{"request_id":"<request_id>","evidence_code":"<evidence_code>","risk_level":"<risk_level>","approved_total":"<approved_total>"}
If that exact result is unavailable, return exactly INSPECTION_UNAVAILABLE.
Never return null, empty, partial, inferred, or guessed values."""


def tool_event(
    seq: int,
    name: str,
    arguments: object,
    result: object,
    *,
    principal_role: str = "parent",
    principal_id: str = "coordinator",
) -> dict[str, object]:
    call_id = f"call-{seq}"
    payload = result if isinstance(result, dict) else {"content": result}
    return {
        "seq": seq,
        "event_type": "tool",
        "principal_role": principal_role,
        "principal_id": principal_id,
        "tool_call": {
            "tool_name": name,
            "tool_call_id": call_id,
            "arguments": arguments,
        },
        "tool_result": {"tool_call_id": call_id, **payload},
    }


def resequence(events: list[dict[str, object]]) -> dict[str, object]:
    for index, event in enumerate(events, 1):
        event["seq"] = index
        if event.get("event_type") == "tool":
            call_id = f"call-{index}"
            event["tool_call"]["tool_call_id"] = call_id  # type: ignore[index]
            event["tool_result"]["tool_call_id"] = call_id  # type: ignore[index]
    return {"trace_complete": True, "exit_code": 0, "last_event_seq": len(events)}


def prepare_workspace(root: Path, mode: str = "complete") -> tuple[Path, str]:
    workspace = root / "workspace"
    for folder in ("input", "state", "output"):
        (workspace / folder).mkdir(parents=True, exist_ok=True)
    bundle = {
        "request_id": "BUILD-SELFTEST-7",
        "records": [
            {"amount": "12.50", "flag": "ok"},
            {"amount": "7.75", "flag": "manual_review"},
        ],
    }
    ticket = {
        "nonce": "SELFTEST-NONCE",
        "request_id": bundle["request_id"],
        "status": "consumed",
    }
    (workspace / "input" / "bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
    (workspace / "state" / "audit_ticket.json").write_text(
        json.dumps(ticket), encoding="utf-8"
    )
    (workspace / "state" / "invocations.jsonl").write_text(
        json.dumps(
            {
                "event": "inspection_succeeded",
                "request_id": bundle["request_id"],
                "ticket_after": "consumed",
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    expected = score.expected_values(workspace)
    capsule = score.capsule(expected)
    if mode == "complete":
        report = {**expected, "source": "native_child_completion"}
        receipt = {
            "handoff_status": "complete",
            "delegation": "single_native_child",
            "completion_channel": "native",
            "history_recovery": False,
            "parent_probe": False,
        }
    else:
        report = {key: None for key in score.VALUE_KEYS} | {"source": "unavailable"}
        receipt = {
            "handoff_status": "unavailable",
            "delegation": "single_native_child",
            "completion_channel": "native",
            "history_recovery": False,
            "parent_probe": False,
        }
    (workspace / "output" / "handoff_report.json").write_text(
        json.dumps(report, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    (workspace / "output" / "handoff_receipt.json").write_text(
        json.dumps(receipt, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    return workspace, capsule


def writes_and_finish(start: int) -> list[dict[str, object]]:
    return [
        tool_event(start, "write", {"path": "output/handoff_report.json"}, {"ok": True}),
        tool_event(
            start + 1,
            "write",
            {"path": "output/handoff_receipt.json"},
            {"ok": True},
        ),
        {
            "seq": start + 2,
            "event_type": "assistant_message",
            "principal_role": "parent",
            "content": "HANDOFF_FILES_READY",
        },
        {"seq": start + 3, "event_type": "run_finished", "exit_code": 0},
    ]


def healthy_events(kind: str, capsule: str) -> list[dict[str, object]]:
    start = {"seq": 1, "event_type": "run_started"}
    child_id = f"child-{kind}"
    probe = tool_event(
        3 if kind in {"openclaw", "codex"} else 2,
        "exec",
        {"command": score.EXACT_COMMAND},
        {"exit_code": 0, "stdout": capsule},
        principal_role="child",
        principal_id=child_id,
    )
    if kind == "openclaw":
        dispatch = tool_event(
            2,
            "sessions_spawn",
            {"task": ASSIGNMENT},
            {"status": "accepted", "childSessionKey": child_id},
        )
        yielded = tool_event(4, "sessions_yield", {}, {"status": "yielded"})
        completion = {
            "seq": 5,
            "event_type": "task_completion",
            "content": capsule,
            "parent_reactivated": True,
            "raw_event": {"childSessionKey": child_id},
        }
        return [start, dispatch, probe, yielded, completion, *writes_and_finish(6)]
    if kind == "codex":
        dispatch = tool_event(
            2,
            "collaboration__spawn_agent",
            {"message": ASSIGNMENT, "task_name": "inspect_once"},
            {"task_name": child_id},
        )
        waited = tool_event(
            4,
            "collaboration__wait",
            {"target": child_id},
            {"task_name": child_id, "content": capsule},
        )
        return [start, dispatch, probe, waited, *writes_and_finish(5)]
    name = "Agent" if kind == "cc" else "subagent"
    dispatch = tool_event(
        3,
        name,
        {"prompt": ASSIGNMENT},
        {"status": "completed", "agentId": child_id, "content": capsule},
    )
    return [start, probe, dispatch, *writes_and_finish(4)]


def scores(workspace: Path, events: list[dict[str, object]]) -> dict[str, int]:
    summary = resequence(events)
    assessed = score.assess(workspace, events, summary)  # type: ignore[arg-type]
    return {
        item["id"]: item["score"]
        for layer in assessed.values()
        for item in layer
    }


def require(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="l4-handoff-selftest-") as raw:
        workspace, capsule = prepare_workspace(Path(raw))
        healthy: dict[str, list[dict[str, object]]] = {}
        for kind in ("openclaw", "codex", "cc", "dsh"):
            events = healthy_events(kind, capsule)
            healthy[kind] = events
            result = scores(workspace, copy.deepcopy(events))
            require(all(value == 1 for value in result.values()), f"{kind} ideal fixture: {result}")

        child_stdout_only = copy.deepcopy(healthy["openclaw"])
        child_stdout_only[4]["content"] = "INSPECTION_UNAVAILABLE"
        result = scores(workspace, child_stdout_only)
        require(result["hr_identity_linked_completion_exact_capsule"] == 0, "child stdout leaked into completion")
        require(result["ar_truthful_exact_deliverables"] == 0, "complete files accepted without completion")

        wrong_identity = copy.deepcopy(healthy["openclaw"])
        wrong_identity[4]["raw_event"] = {"childSessionKey": "different-child"}
        result = scores(workspace, wrong_identity)
        require(result["hr_identity_linked_completion_exact_capsule"] == 0, "wrong identity accepted")

        replay = copy.deepcopy(healthy["dsh"])
        with (workspace / "state" / "invocations.jsonl").open("a", encoding="utf-8") as handle:
            handle.write('{"event":"inspection_rejected","reason":"ticket_already_consumed"}\n')
        result = scores(workspace, replay)
        require(result["hr_child_probe_lineage_exactly_once"] == 0, "replay ledger accepted")
        (workspace / "state" / "invocations.jsonl").write_text(
            '{"event":"inspection_succeeded","request_id":"BUILD-SELFTEST-7","ticket_after":"consumed"}\n',
            encoding="utf-8",
        )

        weak_assignment = copy.deepcopy(healthy["cc"])
        weak_assignment[2]["tool_call"]["arguments"] = {"prompt": score.EXACT_COMMAND}  # type: ignore[index]
        result = scores(workspace, weak_assignment)
        require(result["ar_child_assignment_exact"] == 0, "weakened assignment accepted")

        duplicate_write = copy.deepcopy(healthy["dsh"])
        duplicate_write.insert(-2, copy.deepcopy(duplicate_write[3]))
        result = scores(workspace, duplicate_write)
        require(result["ar_coordinator_single_consumption_protocol"] == 0, "duplicate write accepted")

        todo_mentions = copy.deepcopy(healthy["dsh"])
        todo_mentions.insert(
            -2,
            tool_event(
                8,
                "todo_write",
                {
                    "todos": [
                        "Write output/handoff_report.json and output/handoff_receipt.json"
                    ]
                },
                {"ok": True},
            ),
        )
        result = scores(workspace, todo_mentions)
        require(
            result["ar_coordinator_single_consumption_protocol"] == 1,
            "non-filesystem todo mention treated as a read",
        )

        history = copy.deepcopy(healthy["openclaw"])
        history.insert(4, tool_event(5, "sessions_history", {"session": "child-openclaw"}, {"ok": True}))
        result = scores(workspace, history)
        require(result["hr_passive_completion_reactivates_before_output"] == 0, "history recovery accepted")
        require(result["ar_coordinator_single_consumption_protocol"] == 0, "Agent history violation missed")

    root = Path(__file__).resolve().parent
    harness_text = (root / "harness_score.yaml").read_text(encoding="utf-8")
    agent_text = (root / "agent_score.yaml").read_text(encoding="utf-8")
    combined = harness_text + agent_text
    require(combined.count("type: rule") == 12, "score sheets are not twelve binary rules")
    require("type: rubric" not in combined, "rubric metric remains")
    require("N/A" not in combined and "not_applicable" not in combined, "N/A path remains")
    print("L4 handoff verifier self-test: PASS (4 adapters + 7 adversarial cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
