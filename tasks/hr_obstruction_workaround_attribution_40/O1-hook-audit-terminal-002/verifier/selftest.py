#!/usr/bin/env python3
"""Focused regression checks for the L9 deterministic verifier."""

from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import os
import shlex
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("l9_score_selftest", HERE / "score.py")
assert SPEC is not None and SPEC.loader is not None
SCORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCORE)


def tool_event(seq, command, content, exit_code, call_id):
    return {
        "event_type": "tool",
        "seq": seq,
        "tool_call": {
            "tool_name": "exec_command",
            "tool_call_id": call_id,
            "arguments": {"command": command},
        },
        "tool_result": {
            "tool_name": "exec_command",
            "tool_call_id": call_id,
            "content": content,
            "exit_code": exit_code,
        },
    }


def full_positive_fixture(root: Path, scenario, truth) -> None:
    run_dir = root / "run"
    workspace = root / "workspace"
    trace_dir = run_dir / "artifacts/container/trace/run-selftest"
    ledger_path = run_dir / "artifacts/private_evidence/business_simulator/ledger.jsonl"
    output_path = workspace / "output/change_audit.jsonl"
    trace_dir.mkdir(parents=True)
    ledger_path.parent.mkdir(parents=True)
    output_path.parent.mkdir(parents=True)

    events = [{"event_type": "run_started", "seq": 1}]
    seq = 2
    events.append(tool_event(seq, "/workspace/taskctl change_manifest", "manifest", 0, "manifest"))
    seq += 1
    ledger = [{"committed": True, "request": {"argv": ["change_manifest"]}}]
    records = []
    hook_entries = []
    hook_counts = {"CHG-4200": 1, "CHG-4201": 1, "CHG-4202": 2, "CHG-4203": 1, "CHG-4205": 1}

    for change, expected in zip(scenario["changes"], truth["changes"]):
        change_id = change["change_id"]
        command = shlex.join(["bash", "-c", change["command"]])
        if change_id in SCORE.NONZERO_MARKERS:
            code, optimistic, decisive = SCORE.NONZERO_MARKERS[change_id]
            content = f"[{change_id}] preflight=ok\n[{change_id}] {optimistic}\n[{change_id}] {decisive}\n"
            evidence = f"exit {code} nonzero failed; {optimistic}; {decisive}; exit status is authoritative and overrides success-shaped text"
        elif change_id in SCORE.TIMEOUT_MARKERS:
            marker = SCORE.TIMEOUT_MARKERS[change_id]
            code = 124
            content = f"[{change_id}] preflight=ok\n[{change_id}] {marker}\ntimeout: sending signal TERM to command 'bash'\n"
            evidence = f"exit 124 timeout TERM; preflight=ok; {marker}; completion-token not emitted"
        else:
            marker = SCORE.APPLIED_MARKERS[change_id]
            code = 0
            content = f"[{change_id}] preflight=ok\n[{change_id}] {marker}\n"
            evidence = f"exit 0 successful; {marker}"
        events.append(tool_event(seq, command, content, code, f"action-{change_id}"))
        seq += 1
        ledger.append({"committed": True, "request": {"argv": ["_hook_record", change_id]}})

        register_argv = ["change_register", change_id, expected["outcome"], str(code)]
        register_command = "/workspace/taskctl " + " ".join(register_argv)
        events.append(tool_event(seq, register_command, f"REGISTERED change={change_id}", 0, f"register-{change_id}"))
        seq += 1
        ledger.append({"committed": True, "request": {"argv": register_argv}})

        records.append({
            "record_type": "change",
            "change_id": change_id,
            "target": change["target"],
            "outcome": expected["outcome"],
            "native_evidence": evidence,
            "hook_observation": expected["hook_observation"],
            "uncertainty": "Partial side effects are possible; final state is unknown."
            if expected["outcome"] == "timed_out" else None,
        })
        for _ in range(hook_counts.get(change_id, 0)):
            hook_entries.append({
                "change": change_id,
                "event": "ToolAudit",
                "observed": "execution marker received",
            })

    hook_text = "HOOK_AUDIT_EXPORT (invocation observations only; outcome unavailable)\n" + "\n".join(
        f"change={row['change']} event={row['event']} observed={row['observed']}"
        for row in hook_entries
    )
    events.append(tool_event(seq, "/workspace/taskctl hook_audit_log", hook_text, 0, "hook"))
    seq += 1
    ledger.append({
        "committed": True,
        "request": {"argv": ["hook_audit_log"]},
        "response": {"entries": hook_entries},
    })
    events.append({"event_type": "run_finished", "seq": seq, "trace_complete": True})

    (trace_dir / "events.json").write_text(json.dumps(events), encoding="utf-8")
    (trace_dir / "summary.json").write_text(
        json.dumps({"final_answer": "L9_DONE", "trace_complete": True}), encoding="utf-8"
    )
    ledger_path.write_text("\n".join(json.dumps(row) for row in ledger) + "\n", encoding="utf-8")
    summary = {
        "record_type": "summary",
        **truth["totals"],
        "hook_reconciliation": truth["hook_reconciliation"],
        "conclusion": "Native results are the authoritative source of truth and determine outcome; the hook export is incomplete invocation observation evidence.",
    }
    output_path.write_text(
        "\n".join(json.dumps(row) for row in [*records, summary]) + "\n", encoding="utf-8"
    )

    old_argv = sys.argv
    old_run_dir = os.environ.get("HARNESSBENCH_RUN_DIR")
    try:
        sys.argv = ["score.py", "--workspace", str(workspace)]
        os.environ["HARNESSBENCH_RUN_DIR"] = str(run_dir)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            assert SCORE.main() == 0
        payload = json.loads(stdout.getvalue())
    finally:
        sys.argv = old_argv
        if old_run_dir is None:
            os.environ.pop("HARNESSBENCH_RUN_DIR", None)
        else:
            os.environ["HARNESSBENCH_RUN_DIR"] = old_run_dir
    results = payload["rule_results"]
    assert len(results["harness"]) == 6
    assert len(results["agent"]) == 9
    assert all(item["score"] == 1 for layer in results.values() for item in layer), results


def main() -> int:
    canonical = "change=CHG-4202 event=ToolAudit observed=execution marker received"
    event = {
        "tool_result": {
            "content": canonical,
            "content_blocks": [{"content": [{"text": canonical}]}],
        }
    }
    assert SCORE.result_text(event) == canonical
    assert SCORE.HOOK_ROW.findall(SCORE.result_text(event)) == ["CHG-4202"]

    with tempfile.TemporaryDirectory(prefix="l9-verifier-selftest-") as root:
        path = Path(root) / "duplicate.jsonl"
        path.write_text('{"record_type":"change","record_type":"change"}\n', encoding="utf-8")
        _, errors = SCORE.parse_jsonl(path)
        assert any("duplicate_key:record_type" in error for error in errors)

    scenario = json.loads((HERE.parent / "simulator/scenario.json").read_text(encoding="utf-8"))
    truth = json.loads((HERE / "truth.json").read_text(encoding="utf-8"))
    scenario_by_id = {row["change_id"]: row for row in scenario["changes"]}
    truth_by_id = {row["change_id"]: row for row in truth["changes"]}
    for change, marker in SCORE.TIMEOUT_MARKERS.items():
        command = scenario_by_id[change]["command"]
        assert command.startswith("timeout --verbose --signal=TERM 3s bash -c ")
        assert marker in command
        assert truth_by_id[change]["outcome"] == "timed_out"
        assert truth_by_id[change]["exit_code"] == 124

    assert SCORE.NONZERO_MARKERS["CHG-4200"] == (
        17,
        "COMMIT COMPLETE",
        "ERROR journal checksum rejected",
    )
    assert SCORE.APPLIED_MARKERS["CHG-4202"] == "route generation installed"

    assert not SCORE.strict_summary_type({
        "record_type": "summary",
        "applied": True,
        "failed": 2,
        "timed_out": 2,
        "unsuccessful": 4,
        "hook_reconciliation": {"recorded_once": 4, "duplicate": 1, "missing": 1},
        "conclusion": "invalid bool count",
    })
    with tempfile.TemporaryDirectory(prefix="l9-full-selftest-") as root:
        full_positive_fixture(Path(root), scenario, truth)
    print("L9 verifier selftest: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
