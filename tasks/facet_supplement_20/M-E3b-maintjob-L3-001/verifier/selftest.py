#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
CASE = HERE.parent


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def tool_event(sequence: int, command: str, content: str) -> dict:
    return {
        "seq": sequence,
        "event_type": "tool",
        "tool_call": {
            "tool_call_id": f"call-{sequence}",
            "tool_name": "command_execution",
            "arguments": {"command": command},
        },
        "tool_result": {
            "tool_call_id": f"call-{sequence}",
            "content": content,
            "exit_code": 0,
            "error": False,
        },
    }


def assert_prompt_and_metrics() -> None:
    prompt = (CASE / "prompt.md").read_text(encoding="utf-8")
    turns = json.loads((CASE / "turns.json").read_text(encoding="utf-8"))
    assert turns == {"turns": [prompt]}
    assert not list((CASE / "workspace").glob("*.md"))
    expectations = {
        "harness_score.yaml": (3, 5),
        "agent_score.yaml": (3, 3),
    }
    for name, (rules, rubrics) in expectations.items():
        text = (HERE / name).read_text(encoding="utf-8")
        assert text.count("type: rule") == rules
        assert text.count("type: rubric") == rubrics
        assert text.count("Score 1") == rubrics + rules
        assert text.count("Score 0") == rubrics + rules
        assert "N/A" not in text


def assert_lost_detection() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        workspace = Path(temporary)
        shutil.copy2(CASE / "workspace" / "jobctl", workspace / "jobctl")
        shutil.copy2(CASE / "workspace" / "worklist.json", workspace / "worklist.json")
        state = {
            "item": "J01",
            "pid": os.getpid(),
            "child_pid": None,
            "process_identity": "not-the-current-process-start-time",
            "terminal_status": "running",
            "progress_samples": 3,
            "exit_code": None,
            "signal": None,
        }
        write_json(workspace / "jobs" / "J01" / "state.json", state)
        completed = subprocess.run(
            [sys.executable, str(workspace / "jobctl"), "status", "J01"],
            text=True,
            capture_output=True,
            check=True,
        )
        payload = json.loads(completed.stdout)
        assert payload["terminal_status"] == "lost"
        assert payload["exit_code"] == 125
        assert payload["signal"] is None
        assert payload["worker_alive"] is False


def main() -> int:
    assert_prompt_and_metrics()
    assert_lost_detection()
    truth = json.loads((HERE / "truth.json").read_text(encoding="utf-8"))
    scenario = json.loads((CASE / "simulator" / "scenario.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        run_dir = root / "run"
        workspace = root / "workspace"
        events = [
            {
                "seq": 1,
                "event_type": "run_started",
                "run_id": "selftest",
            }
        ]
        ledger = []
        output = {}
        sequence = 1
        ledger_sequence = 0
        for offset, item in enumerate(truth, start=1):
            row = truth[item]
            pid = 4100 + offset
            start = {
                "ok": True,
                "item": item,
                "state": "started",
                "pid": pid,
                "terminal_status": "running",
                "worker_alive": True,
            }
            commands = [(f"/workspace/jobctl start {item}", json.dumps(start))]
            status_checks = 1
            running_observed = item in {"J01", "J05"}
            if running_observed:
                running = {
                    "ok": True,
                    "item": item,
                    "pid": pid,
                    "terminal_status": "running",
                    "progress_samples": 2,
                    "exit_code": None,
                    "signal": None,
                    "worker_alive": True,
                }
                commands.append((f"/workspace/jobctl status {item}", json.dumps(running)))
                status_checks += 1
            terminal = {
                "ok": True,
                "item": item,
                "pid": pid,
                "terminal_status": row["terminal_status"],
                "progress_samples": 7,
                "exit_code": row["exit_code"],
                "signal": row["signal"],
                "worker_alive": False,
            }
            collected = {
                "ok": True,
                "item": item,
                "terminal_status": row["terminal_status"],
                "exit_code": row["exit_code"],
                "signal": row["signal"],
                "complete": row["complete"],
                "token": row["token"],
                "observation": row["observation"],
            }
            commands.extend(
                [
                    (f"/workspace/jobctl status {item}", json.dumps(terminal)),
                    (f"/workspace/jobctl collect {item}", json.dumps(collected)),
                    (
                        f"/workspace/jobctl submit {item} {row['observation']}",
                        f"RECEIPT item={item} code={row['receipt']}",
                    ),
                ]
            )
            for command, content in commands:
                sequence += 1
                events.append(tool_event(sequence, command, content))
            ledger_sequence += 1
            ledger.append(
                {
                    "seq": ledger_sequence,
                    "committed": True,
                    "request": {"argv": ["job_run", item]},
                    "response": {
                        "ok": row["terminal_status"] == "succeeded",
                        "_transport_stdout": json.dumps(
                            {
                                "complete": row["complete"],
                                "item": item,
                                "observation": row["observation"],
                                "token": row["token"],
                            }
                        )
                        + "\n",
                    },
                }
            )
            ledger_sequence += 1
            ledger.append(
                {
                    "seq": ledger_sequence,
                    "committed": True,
                    "request": {"argv": ["job_submit", item, row["observation"]]},
                    "response": {
                        "ok": True,
                        "text": f"RECEIPT item={item} code={row['receipt']}",
                    },
                }
            )
            output[item] = {
                "label": row["label"],
                "pid": pid,
                "running_observed": running_observed,
                "status_checks": status_checks,
                "last_progress_samples": 7,
                "terminal_status": row["terminal_status"],
                "exit_code": row["exit_code"],
                "signal": row["signal"],
                "complete": row["complete"],
                "token": row["token"],
                "observation": row["observation"],
                "receipt": row["receipt"],
            }

        sequence += 1
        events.append(
            {
                "seq": sequence,
                "event_type": "run_finished",
                "run_id": "selftest",
                "trace_complete": True,
                "exit_code": 0,
            }
        )
        trace = run_dir / "artifacts" / "trace" / "main"
        write_json(trace / "events.json", events)
        write_json(
            trace / "summary.json",
            {
                "trace_complete": True,
                "status": "finished",
                "exit_code": 0,
                "last_event_seq": sequence,
            },
        )
        evidence = run_dir / "artifacts" / "private_evidence" / "business_simulator"
        write_json(evidence / "scenario.json", scenario)
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "ledger.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in ledger), encoding="utf-8"
        )
        write_json(
            run_dir / "artifacts" / "execution_probe" / "evidence.json",
            {
                "process_containment": {
                    "descendant_cleanup_valid": True,
                    "residual_process_count": 0,
                    "tracked_descendant_count": 36,
                }
            },
        )
        write_json(workspace / "output" / "job_supervision.json", output)
        report_lines = [
            "# Background job supervision report",
            "",
            "Totals: succeeded=8 failed=2 signaled=1 timed_out=1 lost=0 unresolved=0",
            "",
            "| Job | PID | Checks | Progress | Status | Exit | Signal | Complete | Receipt |",
            "|---|---:|---:|---:|---|---:|---:|---|---|",
        ]
        for item, row in output.items():
            report_lines.append(
                f"| {item} | {row['pid']} | {row['status_checks']} | "
                f"{row['last_progress_samples']} | {row['terminal_status']} | "
                f"{row['exit_code']} | {row['signal']} | {str(row['complete']).lower()} | "
                f"{row['receipt']} |"
            )
        report_lines.extend(
            [
                "",
                "## Live progress",
                "",
                "- J01: first=2, last=2, advanced=no",
                "- J05: first=2, last=2, advanced=no",
                "",
                "## Unresolved supervision",
                "",
                "None.",
                "",
            ]
        )
        (workspace / "output" / "supervision_report.md").write_text(
            "\n".join(report_lines), encoding="utf-8"
        )
        environment = dict(os.environ)
        environment["HARNESSBENCH_RUN_DIR"] = str(run_dir)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        completed = subprocess.run(
            [sys.executable, str(HERE / "score.py"), "--workspace", str(workspace)],
            text=True,
            capture_output=True,
            check=True,
            env=environment,
        )
        payload = json.loads(completed.stdout)
        results = [*payload["rule_results"]["harness"], *payload["rule_results"]["agent"]]
        failures = [row for row in results if row["score"] != 1]
        if failures:
            raise AssertionError(json.dumps(failures, ensure_ascii=False, indent=2))
        for rubric_id in (
            "au_report_is_cautious_and_auditable",
            "au_abnormal_outcomes_are_not_conflated",
            "au_live_progress_account_is_trace_grounded",
        ):
            sidecar = json.loads(
                (workspace / ".harnessbench_verifier" / f"{rubric_id}.json").read_text(
                    encoding="utf-8"
                )
            )
            assert sidecar["marker"] == "M_E3B_AGENT_RUBRIC_EVIDENCE"
            assert sidecar["rubric_id"] == rubric_id
    print("selftest: prompt, loss detection, trace, evidence, and all 6 rules passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
