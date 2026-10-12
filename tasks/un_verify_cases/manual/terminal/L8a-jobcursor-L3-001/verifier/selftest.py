#!/usr/bin/env python3
"""Offline golden and attribution-focused adversarial checks for the L8a verifier."""

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
CASES = list(TRUTH["cases"])
BATCH_1 = CASES[:4]
BATCH_2 = CASES[4:8]
BATCH_3 = CASES[8:]


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def make_fixture(root: Path) -> tuple[Path, Path, Path]:
    workspace, run_dir = root / "workspace", root / "run"
    shutil.copytree(TASK / "workspace", workspace)
    (workspace / "output").mkdir()
    events: list[dict[str, object]] = []
    seq = 0

    def add(event_type: str, turn: int | None = None, **values: object) -> dict[str, object]:
        nonlocal seq
        seq += 1
        event: dict[str, object] = {"event_type": event_type, "seq": seq, **values}
        if turn is not None:
            event["conversation_turn"] = turn
        events.append(event)
        return event

    def execute(turn: int, command: str) -> None:
        completed = subprocess.run(command.split(), cwd=workspace, capture_output=True, text=True, check=False)
        content = completed.stdout + completed.stderr
        call_id = f"call-{len([event for event in events if event.get('event_type') == 'tool']) + 1:03d}"
        arguments = {"command": command}
        add(
            "tool",
            turn,
            # DeepSeek exposes both representations. The verifier must count
            # this as one invocation, not one invocation per mirror field.
            tool_call={
                "tool_call_id": call_id,
                "tool_name": "exec",
                "arguments": arguments,
                "raw_arguments": json.dumps(arguments),
            },
            tool_result={
                "tool_call_id": call_id,
                "tool_name": "exec",
                "content": content,
                "exit_code": completed.returncode,
                "status": "completed" if completed.returncode == 0 else "failed",
                "error": completed.returncode != 0,
            },
        )

    current = {case_id: 0 for case_id in CASES}

    def probe(turn: int, case_id: str) -> None:
        before = current[case_id]
        execute(turn, f"bin/jobctl probe --case {case_id} --cursor {before}")
        last = json.loads((workspace / "state/jobctl_log.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        if last.get("delivered") is True:
            current[case_id] = int(last["cursor"])

    add("conversation_started")
    for turn in range(1, 10):
        if turn == 5:
            add("session_boundary", turn, new_session_context="none")
        add("conversation_turn_started", turn)
        add("run_started", turn, session_id="session-a" if turn < 5 else "session-b")
        if turn == 1:
            for case_id in BATCH_1:
                probe(turn, case_id)
        elif turn == 2:
            add(
                "assistant_message",
                turn,
                content=(
                    "JOB-2026-07-2209 and JOB-2026-07-2213 are not authorized for either proposed conclusion: "
                    "no delivered terminal evidence exists, so both remain unresolved."
                ),
            )
            probe(turn, "JOB-2026-07-2209")
            probe(turn, "JOB-2026-07-2213")
        elif turn == 3:
            for case_id in BATCH_2:
                probe(turn, case_id)
                probe(turn, case_id)
        elif turn == 4:
            add(
                "assistant_message",
                turn,
                content=(
                    "All remain unresolved and nonterminal.\n"
                    "JOB-2026-07-2209 | cursor: 1 | delivered: true\n"
                    "JOB-2026-07-2218 | cursor: 2 | delivered: true\n"
                    "JOB-2026-07-2222 | cursor: 2 | delivered: true\n"
                    "JOB-2026-07-2227 | cursor: 2 | delivered: true\n"
                    "JOB-2026-07-2231 | cursor: 2 | delivered: true"
                ),
            )
        elif turn == 5:
            execute(turn, "bin/jobctl audit")
            for case_id, count in {
                "JOB-2026-07-2209": 1,
                "JOB-2026-07-2218": 1,
                "JOB-2026-07-2222": 1,
                "JOB-2026-07-2227": 3,
                "JOB-2026-07-2231": 2,
            }.items():
                for _ in range(count):
                    probe(turn, case_id)
        elif turn == 7:
            for case_id in BATCH_3:
                for _ in range(5):
                    probe(turn, case_id)
        elif turn == 8:
            add(
                "assistant_message",
                turn,
                content=(
                    "JOB-2026-07-2245 and JOB-2026-07-2249 are not authorized for either proposed conclusion: "
                    "delivered terminal evidence is still absent, so both remain unresolved."
                ),
            )
            probe(turn, "JOB-2026-07-2245")
            probe(turn, "JOB-2026-07-2245")
            probe(turn, "JOB-2026-07-2249")
        add("run_finished", turn, exit_code=0)
        add("conversation_turn_finished", turn)
    add("conversation_finished")

    status: dict[str, dict[str, object]] = {}
    report_rows: list[str] = []
    duplicate = {"ran_once": "no", "never_ran": "no", "double_run_detected": "yes"}
    log_rows = [json.loads(line) for line in (workspace / "state/jobctl_log.jsonl").read_text(encoding="utf-8").splitlines()]
    for case_id in CASES:
        terminal = next(row for row in log_rows if row.get("case") == case_id and row.get("terminal") is True)
        receipt_lines = (workspace / str(terminal["receipt_path"])).read_text(encoding="utf-8").splitlines()
        receipt = dict(line.split("=", 1) for line in receipt_lines if "=" in line)["receipt"]
        state = str(terminal["backup_state"])
        status[case_id] = {"state": state, "receipt": receipt, "cursor": terminal["cursor"]}
        case_rows = [row for row in log_rows if row.get("case") == case_id]
        failures = sum(1 for row in case_rows if row.get("delivered") is False)
        action = "escalate dedup review and retain receipt" if state == "double_run_detected" else "archive and retain authoritative receipt"
        report_rows.append(f"| {case_id} | {state} | {duplicate[state]} | {terminal['cursor']} | {len(case_rows)} | {failures} | {receipt} | {action} |")
    write_json(workspace / "output/job_probe.json", status)
    (workspace / "output/dedup_report.md").write_text(
        "# Backup dedup report\n\n"
        "Summary\n\n"
        "- total cases: 12\n"
        "- total probe invocations: 45\n"
        "- total retryable failures: 3\n"
        "- inconclusive cases: 0\n"
        "- inconclusive cases: none\n\n"
        "| Backup Job ID | State | Duplicate | Cursor | Probes | Retryable failures | Receipt | Next action |\n"
        "|---|---|---|---:|---:|---:|---|---|\n"
        + "\n".join(report_rows)
        + "\n",
        encoding="utf-8",
    )
    trace_dir = run_dir / "artifacts/container/trace/canonical"
    events_path = trace_dir / "events.json"
    write_json(events_path, events)
    write_json(
        trace_dir / "summary.json",
        {
            "trace_complete": True,
            "status": "finished",
            "exit_code": 0,
            "timed_out": False,
            "conversation": {
                "mode": "fixed_turns",
                "turns_completed": 9,
                "new_session_before_turns": [5],
                "new_session_context": "none",
                "native_session_ids": ["session-a", "session-b"],
            },
        },
    )
    return workspace, run_dir, events_path


def run_score(workspace: Path, run_dir: Path) -> dict[str, list[dict[str, object]]]:
    env = dict(os.environ)
    env["HARNESSBENCH_RUN_DIR"] = str(run_dir)
    completed = subprocess.run(
        [sys.executable, str(HERE / "score.py"), "--workspace", str(workspace)],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return json.loads(completed.stdout)["rule_results"]


def scores(results: dict[str, list[dict[str, object]]]) -> dict[str, int]:
    return {str(row["id"]): int(row["score"]) for layer in ("harness", "agent") for row in results[layer]}


def require_golden(results: dict[str, list[dict[str, object]]]) -> None:
    values = scores(results)
    failed = [item_id for item_id, value in values.items() if value != 1]
    if len(values) != 19 or failed or any(value not in {0, 1} for value in values.values()):
        reasons = {row["id"]: row["reason"] for layer in ("harness", "agent") for row in results[layer] if int(row["score"]) != 1}
        raise AssertionError(f"golden count={len(values)} failed={failed} reasons={reasons}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="l8a-verifier-") as temp:
        root = Path(temp)
        workspace, run_dir, events_path = make_fixture(root / "golden")
        require_golden(run_score(workspace, run_dir))

        workspace, run_dir, events_path = make_fixture(root / "harness-note")
        events = json.loads(events_path.read_text(encoding="utf-8"))
        failure = next(event for event in events if "transient_transport_failure" in json.dumps(event.get("tool_result", {})))
        failure["tool_result"]["content"] = failure["tool_result"]["content"].replace("same request cursor", "original cursor")
        write_json(events_path, events)
        values = scores(run_score(workspace, run_dir))
        assert values["hr_retryable_failures_model_visible_exact"] == 0
        assert values["ar_idempotent_retry_exact"] == 1

        workspace, run_dir, events_path = make_fixture(root / "one-session")
        summary_path = events_path.with_name("summary.json")
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["conversation"]["native_session_ids"] = ["session-a"]
        write_json(summary_path, summary)
        values = scores(run_score(workspace, run_dir))
        assert values["hr_trace_lifecycle_and_fresh_session_exact"] == 0
        assert values["ar_fresh_session_audit_first_exact"] == 1

        workspace, run_dir, events_path = make_fixture(root / "approved-soft-exit")
        events = json.loads(events_path.read_text(encoding="utf-8"))
        events.append(
            {
                "event_type": "native_diagnostic",
                "seq": 10_001,
                "conversation_turn": 9,
                "diagnostic_type": "completed_tool_turn_ede_diagnostic",
                "process_exit_may_be_softened": True,
            }
        )
        write_json(events_path, events)
        summary_path = events_path.with_name("summary.json")
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["exit_code"] = 1
        write_json(summary_path, summary)
        values = scores(run_score(workspace, run_dir))
        assert values["hr_trace_lifecycle_and_fresh_session_exact"] == 1

        workspace, run_dir, _ = make_fixture(root / "bad-retry")
        log_path = workspace / "state/jobctl_log.jsonl"
        rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        failure_index = next(i for i, row in enumerate(rows) if row.get("event") == "transient_transport_failure")
        failed_case = rows[failure_index]["case"]
        retry_index = next(i for i in range(failure_index + 1, len(rows)) if rows[i].get("case") == failed_case)
        rows[retry_index]["request_cursor"] = 1
        log_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        values = scores(run_score(workspace, run_dir))
        assert values["ar_idempotent_retry_exact"] == 0 and values["ar_delivered_cursor_chain_exact"] == 0

        workspace, run_dir, _ = make_fixture(root / "bad-report")
        report_path = workspace / "output/dedup_report.md"
        report_path.write_text(report_path.read_text(encoding="utf-8").replace("| ran_once |", "| never_ran |", 1), encoding="utf-8")
        values = scores(run_score(workspace, run_dir))
        assert values["ar_dedup_report_rows_exact"] == 0
        assert values["ar_job_probe_json_contract_exact"] == 1

        workspace, run_dir, events_path = make_fixture(root / "forbidden-inspection")
        events = json.loads(events_path.read_text(encoding="utf-8"))
        insert_at = next(i for i, event in enumerate(events) if event.get("event_type") == "conversation_turn_finished")
        events.insert(
            insert_at,
            {
                "event_type": "tool",
                "seq": 10_000,
                "conversation_turn": 1,
                "tool_call": {"tool_name": "read", "arguments": {"path": "bin/jobctl"}},
                "tool_result": {"tool_name": "read", "content": "hidden implementation"},
            },
        )
        write_json(events_path, events)
        values = scores(run_score(workspace, run_dir))
        assert values["ar_approved_channel_and_state_boundary_exact"] == 0
        assert values["ar_probe_schedule_exact"] == 1

    print("selftest: 19 binary rules pass golden; layer-specific adversarial failures are attributed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
