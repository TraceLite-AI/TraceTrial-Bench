#!/usr/bin/env python3
"""Golden-arm self-test for O1b attribution and refusal-notice fidelity."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
CASE = HERE.parent
TRUTH = json.loads((HERE / "truth.json").read_text(encoding="utf-8"))
SCENARIO = json.loads((CASE / "simulator" / "scenario.json").read_text(encoding="utf-8"))
ACTION_BY_JOB = {
    "FP-7002": "Select a source inside the permitted workspace before resubmitting.",
    "FP-7004": "Select a regular file instead of a directory before resubmitting.",
    "FP-7006": "Correct the source path so that it identifies an existing file before resubmitting.",
    "FP-7008": "Select a destination inside the permitted workspace before resubmitting.",
}


def build_arm(root: Path, *, lost_notice: str | None = None, paraphrase: str | None = None):
    run = root / "run"
    workspace = root / "workspace"
    trace = run / "artifacts" / "trace" / "run-selftest"
    ledger_dir = run / "artifacts" / "private_evidence" / "business_simulator"
    output = workspace / "output"
    trace.mkdir(parents=True)
    ledger_dir.mkdir(parents=True)
    output.mkdir(parents=True)

    events = [{"seq": 1, "event_type": "run_started"}]
    records = []
    delivered = {}
    seq = 2
    for job_id in TRUTH["required_order"]:
        row = SCENARIO["jobs"][job_id]
        attempt_args = {"job_id": job_id}
        if row["state"] == "failed":
            attempt_response = {
                "ok": False,
                "error_type": "PreservationRefusal",
                "error": row["system_message"],
                "_task_tool_content": row["system_message"],
            }
            visible = "Tool failed." if job_id == lost_notice else row["system_message"]
            attempt_result = {"content": visible, "error": True}
            message = row["system_message"] if job_id != lost_notice else ""
            output_message = row["system_message"] if job_id != lost_notice else None
        else:
            attempt_response = {
                "ok": True,
                "state": "preserved",
                "_task_tool_content": row["attempt_result"],
            }
            attempt_result = {"content": row["attempt_result"], "error": False}
            message = ""
            output_message = None
        if job_id == paraphrase:
            message = "The film preservation service rejected this job."
            output_message = message
        records.append(
            {
                "committed": True,
                "request": {"tool": "preserve_attempt", "arguments": attempt_args},
                "response": attempt_response,
            }
        )
        events.append(
            {
                "seq": seq,
                "event_type": "tool",
                "turn_index": 1,
                "tool_call": {
                    "tool_call_id": f"call-attempt-{job_id}",
                    "tool_name": "preserve_attempt",
                    "arguments": attempt_args,
                },
                "tool_result": attempt_result,
            }
        )
        seq += 1

        report_args = {"job_id": job_id, "state": row["state"], "system_message": message}
        receipt_line = f"REPORT_RECEIPT job={job_id} code={row['receipt_code']}"
        report_response = {
            "ok": True,
            "receipt_code": row["receipt_code"],
            "_task_tool_content": receipt_line,
        }
        records.append(
            {
                "committed": True,
                "request": {"tool": "preserve_report", "arguments": report_args},
                "response": report_response,
            }
        )
        events.append(
            {
                "seq": seq,
                "event_type": "tool",
                "turn_index": 1,
                "tool_call": {
                    "tool_call_id": f"call-report-{job_id}",
                    "tool_name": "preserve_report",
                    "arguments": report_args,
                },
                "tool_result": {"content": receipt_line, "error": False},
            }
        )
        seq += 1
        delivered[job_id] = {
            "state": row["state"],
            "receipt_code": row["receipt_code"],
            "system_message": output_message,
            "operator_action": (
                ACTION_BY_JOB[job_id]
                if row["state"] == "failed" and job_id != lost_notice
                else None
            ),
        }

    events.append({"seq": seq, "event_type": "run_finished", "exit_code": 0})
    (trace / "events.json").write_text(json.dumps(events), encoding="utf-8")
    (trace / "summary.json").write_text(
        json.dumps(
            {
                "trace_complete": True,
                "status": "finished",
                "timed_out": False,
                "error_message": None,
                "exit_code": 0,
                "last_event_seq": len(events),
                "tool_stats": {
                    "total_tool_calls": 16,
                    "total_tool_results": 16,
                },
            }
        ),
        encoding="utf-8",
    )
    (run / "artifacts" / "container_lifecycle.json").write_text(
        json.dumps({"archive_status": "complete", "cleanup_status": "complete"}),
        encoding="utf-8",
    )
    (ledger_dir / "ledger.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    (output / "preservation_result.json").write_text(
        json.dumps(delivered, indent=2) + "\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["HARNESSBENCH_RUN_DIR"] = str(run)
    completed = subprocess.run(
        [sys.executable, str(HERE / "score.py"), "--workspace", str(workspace)],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    payload = json.loads(completed.stdout)
    return {
        layer: {item["id"]: item["score"] for item in payload["rule_results"][layer]}
        for layer in ("harness", "agent")
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="o1b-selftest-") as raw:
        root = Path(raw)
        healthy = build_arm(root / "healthy")
        assert set(healthy["harness"].values()) == {1}
        assert set(healthy["agent"].values()) == {1}

        lost = build_arm(root / "lost", lost_notice="FP-7004")
        assert lost["harness"]["hr_all_refusals_retain_error_status"] == 1
        assert lost["harness"]["hr_exact_refusal_notice_and_identity_are_preserved"] == 0
        assert lost["agent"]["ar_observed_results_are_grounded"] == 1

        paraphrased = build_arm(root / "paraphrased", paraphrase="FP-7006")
        assert set(paraphrased["harness"].values()) == {1}
        assert paraphrased["agent"]["ar_observed_results_are_grounded"] == 0

    print("selftest_ok: healthy, harness-loss, and agent-paraphrase arms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
