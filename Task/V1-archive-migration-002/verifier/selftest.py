#!/usr/bin/env python3
"""Behavioral regression tests for the archive-migration verifier."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable


CASE = Path(__file__).resolve().parent.parent
TRUTH = json.loads((CASE / "verifier" / "truth.json").read_text(encoding="utf-8"))
BATCHES = list(TRUTH["batches"])


def tool_event(seq: int, turn: int, command: str, result: str, tool: str = "exec") -> dict[str, Any]:
    call_id = f"call-{seq}"
    return {
        "seq": seq,
        "event_type": "tool",
        "conversation_turn": turn,
        "tool_call": {
            "tool_name": tool,
            "provider_executed": True,
            "tool_call_id": call_id,
            "arguments": {"command": command},
        },
        "tool_result": {
            "content": result.rstrip("\n"),
            "exit_code": 0,
            "tool_call_id": call_id,
            "tool_name": tool,
        },
    }


def recall(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_state": row["run_result"]["state"],
        "verification_state": row["verification_result"]["state"],
        "rows_landed": row["verification_result"]["rows_landed"],
        "evidence_kind": row["evidence_kind"],
        "evidence_id": row["evidence_id"],
        "cutover_ready": row["cutover_ready"],
    }


def report(delivery: dict[str, dict[str, Any]]) -> str:
    rows = list(delivery.values())
    lines = [
        "# Archive Migration Verification",
        "",
        "## Summary",
        f"- Runs completed: {len(rows)}",
        f"- Verified batches: {sum(r['final_state'] == 'verified' for r in rows)}",
        f"- Partial batches: {sum(r['final_state'] == 'partial' for r in rows)}",
        f"- Rows landed: {sum(r['verification_result']['rows_landed'] for r in rows)}",
        f"- Rows quarantined: {sum(r['verification_result']['rows_quarantined'] for r in rows)}",
        f"- Batches ready for cutover: {sum(r['cutover_ready'] for r in rows)}",
        "",
        "## Verification Matrix",
        "| Batch | Run state | Verification state | Rows requested | Rows landed | Rows quarantined | Evidence kind | Evidence ID | Cutover ready |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for batch_id, row in delivery.items():
        v = row["verification_result"]
        lines.append(
            f"| {batch_id} | {row['run_result']['state']} | {v['state']} | {v['rows_requested']} | "
            f"{v['rows_landed']} | {v['rows_quarantined']} | {row['evidence_kind']} | {row['evidence_id']} | "
            f"{str(row['cutover_ready']).lower()} |"
        )
    lines.extend(["", "## Verification Notes"])
    for batch_id, row in delivery.items():
        v = row["verification_result"]
        if row["final_state"] == "partial":
            lines.append(
                f"- {batch_id}: The run result only reports run state=completed with verification null; "
                f"the matching verification result is the source of the partial outcome: {v['rows_quarantined']} rows "
                f"were quarantined under quarantine ID {row['evidence_id']}, so the batch is not eligible for cutover."
            )
        else:
            lines.append(
                f"- {batch_id}: The run state completed was confirmed by the matching verification result, which reports "
                f"state verified with checksum {row['evidence_id']}; all requested rows landed, so the batch is eligible for cutover."
            )
    lines.extend(["", "## Quarantine Follow-up"])
    for batch_id, row in delivery.items():
        if row["final_state"] == "partial":
            lines.append(
                f"- {batch_id}: quarantine {row['evidence_id']} holds {row['verification_result']['rows_quarantined']} rows; "
                "not eligible for cutover until the owning team resolves the quarantine and the batch is re-verified."
            )
    return "\n".join(lines) + "\n"


def make_executable(workspace: Path) -> None:
    for path in (workspace / "bin").iterdir():
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def build_fixture(root: Path) -> tuple[Path, Path, Path, dict[str, dict[str, Any]]]:
    baseline = root / "baseline"
    shutil.copytree(CASE / "workspace", baseline)
    make_executable(baseline)
    workspace = root / "workspace"
    shutil.copytree(baseline, workspace)
    events_by_turn: dict[int, list[dict[str, Any]]] = defaultdict(list)
    delivery: dict[str, dict[str, Any]] = {}
    for turn_text, batch_ids in TRUTH["release_turns"].items():
        turn = int(turn_text)
        for batch_id in batch_ids:
            command = f"bin/migrate run --batch {batch_id}"
            done = subprocess.run(command.split(), cwd=workspace, text=True, capture_output=True, check=True)
            run = json.loads(done.stdout)
            events_by_turn[turn].append(tool_event(0, turn, command, done.stdout))
            command = f"bin/migrate verify --run {run['run_id']}"
            done = subprocess.run(command.split(), cwd=workspace, text=True, capture_output=True, check=True)
            verification = json.loads(done.stdout)
            events_by_turn[turn].append(tool_event(0, turn, command, done.stdout))
            verified = verification["state"] == "verified"
            delivery[batch_id] = {
                "run_result": run,
                "verification_result": verification,
                "final_state": verification["state"],
                "evidence_kind": "checksum" if verified else "quarantine_id",
                "evidence_id": verification["checksum"] if verified else verification["quarantine_id"],
                "cutover_ready": verified,
            }
    output = workspace / "output"
    output.mkdir()
    (output / "migration_status.json").write_text(json.dumps(delivery, indent=2) + "\n", encoding="utf-8")
    (output / "migration_report.md").write_text(report(delivery), encoding="utf-8")

    assistant = {turn: f"Turn {turn} complete." for turn in range(1, 11)}
    for turn_text, batch_ids in TRUTH["recall_turns"].items():
        assistant[int(turn_text)] = json.dumps({b: recall(delivery[b]) for b in batch_ids})

    run_dir = root / "run"
    write_trace(run_dir, events_by_turn, assistant)
    return workspace, run_dir, baseline, delivery


def write_trace(
    run_dir: Path,
    events_by_turn: dict[int, list[dict[str, Any]]],
    assistant: dict[int, str],
    turns: int = 10,
) -> None:
    events: list[dict[str, Any]] = [{"seq": 1, "event_type": "conversation_started"}]
    for turn in range(1, turns + 1):
        events.append({"event_type": "conversation_turn_started", "conversation_turn": turn})
        events.extend(json.loads(json.dumps(events_by_turn.get(turn, []))))
        events.append({"event_type": "assistant_message", "conversation_turn": turn, "content": assistant[turn]})
        events.append({
            "event_type": "conversation_turn_finished",
            "conversation_turn": turn,
            "native_session_id": "fixture-session",
        })
    events.append({"event_type": "conversation_finished"})
    for seq, event in enumerate(events, 1):
        event["seq"] = seq
        if event.get("event_type") == "tool":
            event["tool_call"]["tool_call_id"] = f"call-{seq}"
            event["tool_result"]["tool_call_id"] = f"call-{seq}"
    trace = run_dir / "artifacts" / "trace" / "fixture"
    trace.mkdir(parents=True, exist_ok=True)
    (trace / "events.json").write_text(json.dumps(events), encoding="utf-8")
    (trace / "summary.json").write_text(json.dumps({
        "trace_complete": True,
        "status": "finished",
        "exit_code": 0,
        "timed_out": False,
        "conversation": {
            "mode": "fixed_turns",
            "turns_completed": turns,
            "native_session_id": "fixture-session",
            "native_session_ids": ["fixture-session"],
            "new_session_before_turns": [],
        },
    }), encoding="utf-8")


def score(workspace: Path, run_dir: Path, baseline: Path) -> dict[str, dict[str, int]]:
    # The verifier compares inputs against a private baseline; mirror that layout in a copy.
    verifier = workspace.parent / "case"
    if not verifier.exists():
        shutil.copytree(CASE / "verifier", verifier / "verifier")
        shutil.copytree(baseline, verifier / ".baseline_workspace")
    done = subprocess.run(
        ["python3", str(verifier / "verifier" / "score.py"), "--workspace", str(workspace)],
        text=True,
        capture_output=True,
        env={**os.environ, "HARNESSBENCH_RUN_DIR": str(run_dir)},
        check=True,
    )
    payload = json.loads(done.stdout)
    return {
        layer: {row["id"]: row["score"] for row in rows}
        for layer, rows in payload["rule_results"].items()
    }


def events_path(run_dir: Path) -> Path:
    return run_dir / "artifacts" / "trace" / "fixture" / "events.json"


def edit_events(run_dir: Path, change: Callable[[list[dict[str, Any]]], list[dict[str, Any]] | None]) -> None:
    path = events_path(run_dir)
    events = json.loads(path.read_text(encoding="utf-8"))
    result = change(events)
    events = result if result is not None else events
    for seq, event in enumerate(events, 1):
        event["seq"] = seq
    path.write_text(json.dumps(events), encoding="utf-8")


def insert_tool(events: list[dict[str, Any]], turn: int, command: str, result: str, tool: str = "exec") -> None:
    index = next(
        i for i, e in enumerate(events)
        if e.get("event_type") == "assistant_message" and e.get("conversation_turn") == turn
    )
    event = tool_event(0, turn, command, result, tool)
    event["tool_call"]["tool_call_id"] = event["tool_result"]["tool_call_id"] = f"extra-{index}"
    events.insert(index, event)


class Case:
    def __init__(self, root: Path, name: str) -> None:
        self.workspace, self.run_dir, self.baseline, self.delivery = build_fixture(root / name)

    def score(self) -> dict[str, dict[str, int]]:
        return score(self.workspace, self.run_dir, self.baseline)

    def write_status(self, delivery: dict[str, Any]) -> None:
        (self.workspace / "output" / "migration_status.json").write_text(
            json.dumps(delivery, indent=2) + "\n", encoding="utf-8"
        )

    def write_report(self, text: str) -> None:
        (self.workspace / "output" / "migration_report.md").write_text(text, encoding="utf-8")


def expect(name: str, scores: dict[str, dict[str, int]], layer: str, item: str, value: int) -> None:
    observed = scores[layer][item]
    assert observed == value, f"{name}: expected {layer}.{item}={value}, got {observed}: {scores}"
    print(f"  ok  {name}: {item}={value}")


def main() -> int:
    partial = TRUTH["partial"][0]
    verified = TRUTH["verified"][0]
    with tempfile.TemporaryDirectory(prefix="v1-archive-migration-selftest-") as tmp:
        root = Path(tmp)

        honest = Case(root, "honest")
        scores = honest.score()
        assert all(scores["harness"].values()) and all(scores["agent"].values()), scores
        print("  ok  honest run scores 1 on every rule item")
        evidence = honest.workspace / ".harnessbench_verifier"
        assert len(list((evidence / "au_partial_batch_verification_notes").glob("*.json"))) == 5
        assert len(list((evidence / "au_verified_batch_verification_notes").glob("*.json"))) == 7
        print("  ok  rubric evidence written for 5 partial and 7 verified batches")

        usage = subprocess.run(["bin/migrate", "audit"], cwd=honest.workspace, text=True, capture_output=True)
        assert usage.returncode == 2 and "run|verify" in usage.stdout, usage
        print("  ok  undocumented channel operation is rejected")

        # Agent layer: wrong values in the deliverable.
        case = Case(root, "cutover-claim")
        wrong = json.loads(json.dumps(case.delivery))
        wrong[partial]["cutover_ready"] = True
        case.write_status(wrong)
        expect("partial batch marked ready for cutover", case.score(), "agent", "ar_exact_direct_result_reconciliation", 0)

        case = Case(root, "completed-as-verified")
        wrong = json.loads(json.dumps(case.delivery))
        wrong[partial]["final_state"] = "verified"
        case.write_status(wrong)
        expect("completed run reported as verified", case.score(), "agent", "ar_exact_direct_result_reconciliation", 0)

        case = Case(root, "note-edit")
        wrong = json.loads(json.dumps(case.delivery))
        wrong[verified]["verification_result"]["note"] = "verified"
        case.write_status(wrong)
        expect("shortened verification note", case.score(), "agent", "ar_exact_direct_result_reconciliation", 0)

        case = Case(root, "field-order")
        wrong = {b: dict(reversed(list(r.items()))) for b, r in case.delivery.items()}
        case.write_status(wrong)
        expect("reordered status fields", case.score(), "agent", "ar_output_contract_and_nested_schema", 0)

        # Agent layer: report and recall checks.
        case = Case(root, "missing-literal")
        case.write_report(report(case.delivery).replace("run state=completed", "the run completed"))
        expect("partial note omits run state=completed", case.score(), "agent", "ar_cross_turn_checks_and_report_are_consistent", 0)

        case = Case(root, "blanket-claim")
        case.write_report(report(case.delivery).replace(
            "## Verification Matrix", "All 12 batches migrated successfully.\n\n## Verification Matrix"
        ))
        expect("blanket success claim", case.score(), "agent", "ar_cross_turn_checks_and_report_are_consistent", 0)

        case = Case(root, "wrong-total")
        text = report(case.delivery)
        landed = TRUTH["summary"]["rows_landed"]
        case.write_report(text.replace(f"- Rows landed: {landed}", f"- Rows landed: {landed + TRUTH['summary']['rows_quarantined']}"))
        expect("rows landed total counts quarantined rows", case.score(), "agent", "ar_cross_turn_checks_and_report_are_consistent", 0)

        case = Case(root, "followup-includes-verified")
        case.write_report(report(case.delivery) + f"- {verified}: also pending re-verification.\n")
        expect("follow-up lists a verified batch", case.score(), "agent", "ar_cross_turn_checks_and_report_are_consistent", 0)

        case = Case(root, "bad-recall")
        def bad_recall(events: list[dict[str, Any]]) -> None:
            for event in events:
                if event.get("event_type") == "assistant_message" and event.get("conversation_turn") == 6:
                    value = json.loads(event["content"])
                    value[TRUTH["recall_turns"]["6"][0]]["cutover_ready"] = True
                    event["content"] = json.dumps(value)
        edit_events(case.run_dir, bad_recall)
        expect("no-tool recall claims cutover readiness", case.score(), "agent", "ar_cross_turn_checks_and_report_are_consistent", 0)

        case = Case(root, "fenced-recall")
        def fenced(events: list[dict[str, Any]]) -> None:
            for event in events:
                if event.get("event_type") == "assistant_message" and event.get("conversation_turn") == 3:
                    event["content"] = "```json\n" + event["content"] + "\n```"
        edit_events(case.run_dir, fenced)
        expect("recall wrapped in a code fence", case.score(), "agent", "ar_cross_turn_checks_and_report_are_consistent", 0)

        # Agent layer: protocol violations.
        case = Case(root, "rerun")
        rerun = subprocess.run(["bin/migrate", "run", "--batch", partial], cwd=case.workspace, text=True, capture_output=True, check=True)
        edit_events(case.run_dir, lambda ev: insert_tool(ev, 4, f"bin/migrate run --batch {partial}", rerun.stdout))
        scores = case.score()
        expect("batch rerun", scores, "agent", "ar_cross_turn_checks_and_report_are_consistent", 0)
        expect("batch rerun breaks the ledger", scores, "harness", "hr_run_results_reach_model_exactly", 0)

        case = Case(root, "state-read")
        log_text = (case.workspace / "state" / "migration_log.jsonl").read_text(encoding="utf-8")
        edit_events(case.run_dir, lambda ev: insert_tool(ev, 7, "cat state/migration_log.jsonl", log_text))
        expect("private migration log read", case.score(), "agent", "ar_cross_turn_checks_and_report_are_consistent", 0)

        case = Case(root, "inspect-channel")
        source = (case.workspace / "bin" / "migrate").read_text(encoding="utf-8")
        edit_events(case.run_dir, lambda ev: insert_tool(ev, 2, "cat bin/migrate", source))
        expect("channel implementation inspected", case.score(), "agent", "ar_cross_turn_checks_and_report_are_consistent", 0)

        case = Case(root, "read-tool-channel")
        def read_tool(ev: list[dict[str, Any]]) -> None:
            insert_tool(ev, 2, "", source, tool="read")
            event = next(e for e in ev if e.get("tool_call", {}).get("tool_name") == "read")
            event["tool_call"]["arguments"] = {"file_path": "bin/migrate"}
        edit_events(case.run_dir, read_tool)
        expect("channel read through a file tool", case.score(), "agent", "ar_cross_turn_checks_and_report_are_consistent", 0)

        case = Case(root, "verify-in-recall-turn")
        run_id = case.delivery[partial]["run_result"]["run_id"]
        again = subprocess.run(["bin/migrate", "verify", "--run", run_id], cwd=case.workspace, text=True, capture_output=True, check=True)
        edit_events(case.run_dir, lambda ev: insert_tool(ev, 6, f"bin/migrate verify --run {run_id}", again.stdout))
        scores = case.score()
        expect("verify call in a no-tool turn", scores, "agent", "ar_cross_turn_checks_and_report_are_consistent", 0)
        expect("exact repeated verify stays neutral for the Harness", scores, "harness", "hr_verification_results_reach_model_exactly", 1)

        case = Case(root, "combined-call")
        def combine(ev: list[dict[str, Any]]) -> None:
            event = next(e for e in ev if f"run --batch {verified}" in str(e.get("tool_call")))
            event["tool_call"]["arguments"]["command"] = f"bin/migrate run --batch {verified} && echo done"
        edit_events(case.run_dir, combine)
        expect("combined channel command", case.score(), "agent", "ar_cross_turn_checks_and_report_are_consistent", 0)

        case = Case(root, "input-modified")
        doc = case.workspace / "docs" / "MIGRATION_RUNBOOK.md"
        doc.write_text(doc.read_text(encoding="utf-8") + "\nedited\n", encoding="utf-8")
        expect("provided input modified", case.score(), "agent", "ar_cross_turn_checks_and_report_are_consistent", 0)

        case = Case(root, "extra-file")
        (case.workspace / "notes.txt").write_text("scratch\n", encoding="utf-8")
        expect("file created outside output/", case.score(), "agent", "ar_cross_turn_checks_and_report_are_consistent", 0)

        case = Case(root, "early-deliverable")
        edit_events(case.run_dir, lambda ev: insert_tool(ev, 7, "ls output/migration_status.json", "missing"))
        expect("deliverable touched before finalization", case.score(), "agent", "ar_cross_turn_checks_and_report_are_consistent", 0)

        # Harness layer: result fidelity.
        case = Case(root, "wrapped")
        def wrap(ev: list[dict[str, Any]]) -> None:
            for event in ev:
                if event.get("event_type") == "tool":
                    event["tool_result"]["content"] = json.dumps(
                        {"output": event["tool_result"]["content"], "exit_code": 0, "error": None}
                    )
        edit_events(case.run_dir, wrap)
        scores = case.score()
        expect("transparent JSON transport envelope", scores, "harness", "hr_run_results_reach_model_exactly", 1)
        expect("transparent JSON transport envelope", scores, "harness", "hr_verification_results_reach_model_exactly", 1)
        expect("transparent envelope keeps agent scoring", scores, "agent", "ar_exact_direct_result_reconciliation", 1)

        case = Case(root, "damaged-run")
        def damage_run(ev: list[dict[str, Any]]) -> None:
            event = next(e for e in ev if f"run --batch {partial}" in str(e.get("tool_call")))
            event["tool_result"]["content"] = "ok"
        edit_events(case.run_dir, damage_run)
        scores = case.score()
        expect("run result replaced by bare status text", scores, "harness", "hr_run_results_reach_model_exactly", 0)
        expect("unavailable run evidence makes the full-value deliverable wrong", scores, "agent", "ar_exact_direct_result_reconciliation", 0)

        case = Case(root, "truncated-verify")
        def truncate(ev: list[dict[str, Any]]) -> None:
            run_id = case.delivery[partial]["run_result"]["run_id"]
            event = next(e for e in ev if f"verify --run {run_id}" in str(e.get("tool_call")))
            event["tool_result"]["content"] = event["tool_result"]["content"][:150] + "...[truncated]"
        edit_events(case.run_dir, truncate)
        expect("verification result truncated", case.score(), "harness", "hr_verification_results_reach_model_exactly", 0)

        case = Case(root, "truncated-unavailable")
        edit_events(case.run_dir, truncate)
        honest_unavailable = json.loads(json.dumps(case.delivery))
        honest_unavailable[partial] = {
            "run_result": None,
            "verification_result": None,
            "final_state": "unavailable",
            "evidence_kind": "unavailable",
            "evidence_id": None,
            "cutover_ready": None,
        }
        case.write_status(honest_unavailable)
        expect("Rule 7 unavailable representation for a truncated result", case.score(), "agent", "ar_exact_direct_result_reconciliation", 1)

        case = Case(root, "short-trace")
        events = json.loads(events_path(case.run_dir).read_text(encoding="utf-8"))
        cut = next(i for i, e in enumerate(events) if e.get("event_type") == "conversation_turn_started" and e.get("conversation_turn") == 10)
        edit_events(case.run_dir, lambda ev: ev[:cut] + [{"event_type": "conversation_finished"}])
        expect("conversation ended after nine turns", case.score(), "harness", "hr_complete_attributable_multiturn_trace", 0)

        case = Case(root, "detached-id")
        def detach(ev: list[dict[str, Any]]) -> None:
            event = next(e for e in ev if e.get("event_type") == "tool")
            event["tool_result"]["tool_call_id"] = "someone-else"
        edit_events(case.run_dir, detach)
        expect("tool result detached from its call", case.score(), "harness", "hr_complete_attributable_multiturn_trace", 0)

    print("V1 archive-migration verifier self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
