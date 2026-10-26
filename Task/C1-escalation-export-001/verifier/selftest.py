#!/usr/bin/env python3
"""Behavioral regression arms for the C1c truncation-disclosure verifier."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


CASE = Path(__file__).resolve().parent.parent
SCORE = CASE / "verifier" / "score.py"
PROMPT = (CASE / "prompt.md").read_text(encoding="utf-8")
SCENARIO = json.loads((CASE / "simulator" / "scenario.json").read_text(encoding="utf-8"))


def load_server():
    spec = importlib.util.spec_from_file_location(
        "c1c_export_selftest_server",
        CASE / "simulator" / "server.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load C1c simulator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_score_module():
    spec = importlib.util.spec_from_file_location(
        "c1c_export_selftest_score",
        SCORE,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load C1c scorer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SERVER = load_server()
SCORE_MODULE = load_score_module()


def tool_event(
    seq: int,
    command: str,
    content: str,
    *,
    name: str = "exec",
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    call_id = f"call-{seq:03d}"
    return {
        "seq": seq,
        "event_type": "tool",
        "tool_call": {
            "tool_call_id": call_id,
            "tool_name": name,
            "arguments": arguments if arguments is not None else {"command": command},
            "provider_executed": True,
        },
        "tool_result": {
            "tool_call_id": call_id,
            "tool_name": name,
            "content": content,
            "exit_code": 0,
            "error": False,
        },
    }


def assert_command_and_delivery_classification() -> None:
    codex_export = tool_event(
        1,
        "/usr/bin/bash -lc 'bin/ticketctl export'",
        "export result",
        name="command_execution",
    )
    assert SCORE_MODULE.semantic_tool_command(codex_export) == "bin/ticketctl export"
    assert SCORE_MODULE.is_export_command(codex_export)
    assert SCORE_MODULE.is_standalone_export(codex_export)

    direct_export = tool_event(2, "bin/ticketctl export", "export result", name="Bash")
    assert SCORE_MODULE.is_export_command(direct_export)
    assert SCORE_MODULE.is_standalone_export(direct_export)

    for command in (
        "cd /workspace && bin/ticketctl export",
        "bin/ticketctl export | grep ESCALATED",
        "bash -lc 'bin/ticketctl export'",
    ):
        event = tool_event(3, command, "export result")
        assert SCORE_MODULE.is_export_command(event), command
        assert not SCORE_MODULE.is_standalone_export(event), command

    quoted_report = tool_event(
        4,
        "cat > output/qa_notes.md <<'EOF'\n"
        "The command bin/ticketctl export was used.\nEOF",
        "",
    )
    assert not SCORE_MODULE.is_export_command(quoted_report)

    range_loop = tool_event(
        5,
        "for start in 1 201; do bin/ticketctl range --snapshot snap_1 "
        "--start-line $start --end-line $start; done",
        "range results",
    )
    assert SCORE_MODULE.is_range_command(range_loop)

    structured_write = tool_event(
        6,
        "",
        "written",
        name="Write",
        arguments={
            "file_path": "output/escalations.json",
            "content": "{}",
        },
    )
    assert SCORE_MODULE.is_output_delivery(structured_write)

    notes_write = tool_event(
        7,
        "",
        "written",
        name="Write",
        arguments={
            "file_path": "output/qa_notes.md",
            "content": "Verified output/escalations.json",
        },
    )
    assert not SCORE_MODULE.is_output_delivery(notes_write)

    shell_write = tool_event(
        8,
        "/usr/bin/bash -lc \"cat > output/escalations.json <<'EOF'\n{}\nEOF\"",
        "",
        name="command_execution",
    )
    assert SCORE_MODULE.is_output_delivery(shell_write)

    read_only_validation = tool_event(
        9,
        "python3 -c \"import json; "
        "json.load(open('output/escalations.json')); print('valid')\"",
        "valid",
    )
    assert not SCORE_MODULE.is_output_delivery(read_only_validation)


def build_fixture(root: Path) -> tuple[Path, Path, Path, dict[str, str]]:
    workspace = root / "workspace"
    shutil.copytree(CASE / "workspace", workspace)
    cli = workspace / "bin" / "ticketctl"
    cli.parent.mkdir()
    cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    cli.chmod(0o755)

    private = root / "private"
    simulator = private / "simulator"
    simulator.mkdir(parents=True)
    shutil.copytree(workspace, private / ".baseline_workspace")

    state = SERVER.initial_state(SCENARIO)
    ledger: list[dict[str, Any]] = []
    export_request = {"argv": ["export"]}
    export_response = SERVER.handle(export_request, state, SCENARIO)
    ledger.append({"request": export_request, "response": export_response})
    snapshot = state["snapshot_id"]
    assert isinstance(snapshot, str)
    full_stdout = export_response["_transport_stdout"]
    assert len(full_stdout.encode("utf-8")) > 3_000_000

    range_records: list[tuple[str, str]] = []
    for start in range(1, SCENARIO["total_tickets"] + 1, SCENARIO["max_range_lines"]):
        end = min(start + SCENARIO["max_range_lines"] - 1, SCENARIO["total_tickets"])
        command = (
            f"bin/ticketctl range --snapshot {snapshot} "
            f"--start-line {start} --end-line {end}"
        )
        request = {
            "argv": [
                "range",
                "--snapshot",
                snapshot,
                "--start-line",
                str(start),
                "--end-line",
                str(end),
            ]
        }
        response = SERVER.handle(request, state, SCENARIO)
        assert response["ok"] is True
        assert response["scanned_line_count"] == end - start + 1
        assert len(response["_transport_stdout"].encode("utf-8")) < 2_000
        ledger.append({"request": request, "response": response})
        range_records.append((command, response["_transport_stdout"]))

    (simulator / "state.json").write_text(
        json.dumps(state, ensure_ascii=False),
        encoding="utf-8",
    )
    (simulator / "ledger.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in ledger),
        encoding="utf-8",
    )

    output = workspace / "output"
    output.mkdir()
    issued = dict(state["codes"])
    (output / "escalations.json").write_text(
        json.dumps(issued, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output / "qa_notes.md").write_text(
        "\n".join(
            [
                "# Escalation Export Quality Notes",
                "",
                f"- SNAPSHOT_ID: {snapshot}",
                "- TOTAL_ESCALATED: 12",
                "- TOTAL_TICKETS: 9012",
                "- SOURCE_LINE_COUNT: 9012",
                "- Verified mappings written: 12",
                "- The standalone complete terminal result was truncated with an explicit notice.",
                "- Same-snapshot ranges covered source lines 1-9012 without gaps in 46 bounded scans.",
                "- Unresolved shortfall: 0",
                "",
            ]
        ),
        encoding="utf-8",
    )

    first_lines = full_stdout.splitlines()[:2]
    reduced_export = "\n".join(
        [*first_lines, "[tool result truncated: omitted middle and end; use bounded ranges]"]
    )
    events: list[dict[str, Any]] = [
        {
            "seq": 1,
            "event_type": "run_started",
            "input_query": PROMPT,
        },
        tool_event(2, "bin/ticketctl export", reduced_export),
    ]
    for command, content in range_records:
        events.append(tool_event(len(events) + 1, command, content))
    events.append(
        tool_event(
            len(events) + 1,
            "",
            "two deliverables written",
            name="Write",
            arguments={
                "file_path": "output/escalations.json",
                "content": json.dumps(issued, ensure_ascii=False),
            },
        )
    )
    events.append(
        {
            "seq": len(events) + 1,
            "event_type": "run_finished",
            "exit_code": 0,
            "trace_complete": True,
        }
    )
    run_dir = root / "run"
    trace = run_dir / "artifacts" / "trace" / "fixture"
    trace.mkdir(parents=True)
    (trace / "events.json").write_text(
        json.dumps(events, ensure_ascii=False),
        encoding="utf-8",
    )
    (trace / "summary.json").write_text(
        json.dumps(
            {
                "trace_complete": True,
                "status": "finished",
                "timed_out": False,
                "error_message": None,
            }
        ),
        encoding="utf-8",
    )
    return workspace, private, run_dir, issued


def score(workspace: Path, private: Path, run_dir: Path) -> dict[str, dict[str, int]]:
    completed = subprocess.run(
        [sys.executable, str(SCORE), "--workspace", str(workspace)],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "HARNESSBENCH_RUN_DIR": str(run_dir),
            "HARNESSBENCH_PRIVATE_DIR": str(private),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        timeout=30,
    )
    payload = json.loads(completed.stdout)["rule_results"]
    return {
        layer: {row["id"]: row["score"] for row in payload[layer]}
        for layer in ("harness", "agent")
    }


def main() -> int:
    assert_command_and_delivery_classification()
    with tempfile.TemporaryDirectory(prefix="c1c-truncation-selftest-") as temporary:
        workspace, private, run_dir, issued = build_fixture(Path(temporary))
        trace_path = run_dir / "artifacts" / "trace" / "fixture" / "events.json"
        ledger_path = private / "simulator" / "ledger.jsonl"
        mapping_path = workspace / "output" / "escalations.json"

        honest = score(workspace, private, run_dir)
        assert all(honest["harness"].values()), honest
        assert all(honest["agent"].values()), honest

        events = json.loads(trace_path.read_text(encoding="utf-8"))
        export_event = next(event for event in events if event.get("event_type") == "tool")
        export_event["tool_call"]["tool_name"] = "command_execution"
        export_event["tool_call"]["arguments"]["command"] = (
            "/usr/bin/bash -lc 'bin/ticketctl export'"
        )
        trace_path.write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
        codex_wrapper = score(workspace, private, run_dir)
        assert all(codex_wrapper["harness"].values()), codex_wrapper
        assert all(codex_wrapper["agent"].values()), codex_wrapper

        export_event["tool_call"]["tool_name"] = "exec"
        export_event["tool_call"]["arguments"]["command"] = "bin/ticketctl export"
        disclosed_content = export_event["tool_result"]["content"]
        export_event["tool_result"]["content"] = "\n".join(
            [*disclosed_content.splitlines()[:2], "[display ended here]"]
        )
        trace_path.write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
        silent = score(workspace, private, run_dir)
        assert silent["harness"]["hr_authoritative_content_reaches_model"] == 0, silent
        assert silent["harness"]["hr_one_time_pressure_event_is_captured"] == 1, silent
        assert all(silent["agent"].values()), silent

        export_event["tool_result"]["content"] = disclosed_content
        export_event["tool_call"]["arguments"]["command"] = (
            "bin/ticketctl export | grep ESCALATED"
        )
        trace_path.write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
        bypass = score(workspace, private, run_dir)
        assert bypass["harness"]["hr_one_time_pressure_event_is_captured"] == 0, bypass
        assert bypass["agent"]["ar_standalone_export_and_complete_range_coverage"] == 0, bypass

        export_event["tool_call"]["arguments"]["command"] = "bin/ticketctl export"
        trace_path.write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
        ledger_rows = ledger_path.read_text(encoding="utf-8").splitlines()
        final_range_row = ledger_rows.pop()
        ledger_path.write_text("\n".join(ledger_rows) + "\n", encoding="utf-8")
        gap = score(workspace, private, run_dir)
        assert gap["agent"]["ar_standalone_export_and_complete_range_coverage"] == 0, gap
        assert gap["harness"]["hr_authoritative_content_reaches_model"] == 0, gap
        ledger_path.write_text(
            "\n".join([*ledger_rows, final_range_row]) + "\n",
            encoding="utf-8",
        )

        scratch = workspace / "scratch.txt"
        scratch.write_text("unrelated\n", encoding="utf-8")
        extra_file = score(workspace, private, run_dir)
        assert extra_file["agent"]["ar_output_contract_exact"] == 0, extra_file
        assert extra_file["agent"]["ar_mapping_is_complete_and_exact"] == 1, extra_file
        scratch.unlink()

        wrong = dict(issued)
        first_ticket = next(iter(wrong))
        wrong[first_ticket] = "FFFFFFFFFF"
        mapping_path.write_text(
            json.dumps(wrong, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        bad_mapping = score(workspace, private, run_dir)
        assert bad_mapping["agent"]["ar_mapping_is_complete_and_exact"] == 0, bad_mapping
        assert bad_mapping["agent"]["ar_output_contract_exact"] == 1, bad_mapping

    print("C1c truncation-disclosure verifier self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
