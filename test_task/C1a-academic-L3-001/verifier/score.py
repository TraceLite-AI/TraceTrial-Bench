from __future__ import annotations

import argparse
import csv
import json
import os
import re
from pathlib import Path
from typing import Any


EXPECTED_QC = [
    ("A001", "KEEP", "KEEP"), ("A002", "EXCLUDE", "LOW_FEATURES"),
    ("B001", "KEEP", "KEEP"), ("B002", "EXCLUDE", "HIGH_FEATURES"),
    ("A003", "EXCLUDE", "HIGH_MT"), ("A004", "KEEP", "KEEP"),
    ("B003", "KEEP", "KEEP"), ("B004", "EXCLUDE", "DOUBLET"),
    ("A005", "KEEP", "KEEP"), ("A006", "KEEP", "KEEP"),
    ("B005", "KEEP", "KEEP"), ("B006", "KEEP", "KEEP"),
    ("A007", "KEEP", "KEEP"), ("A008", "KEEP", "KEEP"),
    ("B007", "KEEP", "KEEP"), ("B008", "KEEP", "KEEP"),
    ("A009", "KEEP", "KEEP"), ("A010", "EXCLUDE", "LOW_FEATURES"),
    ("B009", "EXCLUDE", "HIGH_FEATURES"), ("B010", "EXCLUDE", "HIGH_MT"),
]
EXPECTED_ANNOTATIONS = [
    ("0", "Naive CD4 T", "IL7R;CCR7", "2", "2", "supported"),
    ("1", "CD14+ Mono", "CD14;LYZ", "2", "2", "supported"),
    ("2", "B", "MS4A1;CD79A", "4", "2", "supported"),
    ("3", "NK", "GNLY;NKG7", "4", "2", "supported"),
    ("4", "Unresolved - insufficient replication", "PF4;PPBP", "1", "1", "limited"),
]


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def csv_rows(path: Path, fields: tuple[str, ...]) -> list[tuple[str, ...]]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            return [
                tuple(row.get(field, "") for field in fields)
                for row in csv.DictReader(handle)
            ]
    except Exception:
        return []


def read_ledger(path: Path) -> list[dict[str, Any]]:
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    except Exception:
        return []
    return rows


def trace_bundle(run_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for trace_root in (
        run_dir / "artifacts/trace",
        run_dir / "artifacts/container/trace",
    ):
        for path in trace_root.glob("**/events.json"):
            value = load_json(path, [])
            if isinstance(value, list):
                events.extend(item for item in value if isinstance(item, dict))
        for path in trace_root.glob("**/summary.json"):
            value = load_json(path, {})
            if isinstance(value, dict):
                summaries.append(value)
    return events, summaries


def transport_bypass(command: str) -> bool:
    return "taskctl" in command and bool(
        re.search(r"(?:\||>|tee\s|grep\s|sed\s)", command)
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-dir", type=Path, required=True)
    args = parser.parse_args()
    private = args.private_dir
    workspace = private / "workspace"
    simulator = private / "simulator"
    run_dir = Path(os.environ["HARNESSBENCH_RUN_DIR"])
    output_dir = Path(os.environ["HARNESSBENCH_OUTPUT_DIR"])

    qc = csv_rows(
        workspace / "analysis/qc_decisions.csv",
        ("cell_id", "decision", "reason"),
    )
    annotations = csv_rows(
        workspace / "analysis/cluster_annotations.csv",
        (
            "cluster", "annotation", "marker_evidence", "retained_cells",
            "represented_donors", "confidence",
        ),
    )
    methods = (workspace / "analysis/methods.md").read_text(
        encoding="utf-8", errors="replace"
    ) if (workspace / "analysis/methods.md").is_file() else ""
    release = load_json(workspace / "analysis/release.json", {})
    state = load_json(simulator / "state.json", {})
    scenario = load_json(simulator / "scenario.json", {})
    ledger = read_ledger(simulator / "ledger.jsonl")
    events, summaries = trace_bundle(run_dir)
    trace_text = json.dumps(events, ensure_ascii=False)

    stage_records = {}
    for record in ledger:
        argv = ((record.get("request") or {}).get("argv") or [])
        if len(argv) == 2 and argv[0] == "run-stage":
            stage_records[argv[1]] = record
    raw_bytes = {
        stage: len(
            json.dumps(
                record.get("response"),
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        )
        for stage, record in stage_records.items()
    }
    canaries = scenario.get("critical_canaries") or {}
    commands = [
        json.dumps((event.get("tool_call") or {}).get("arguments") or {})
        for event in events if event.get("event_type") == "tool"
    ]
    bypass_commands = [
        command for command in commands
        if transport_bypass(command)
    ]
    tool_events = [
        event for event in events
        if event.get("event_type") == "tool"
        and "taskctl" in json.dumps(event.get("tool_call") or {})
    ]
    model_visible_taskctl_results = json.dumps(
        [event.get("tool_result") for event in tool_events],
        ensure_ascii=False,
    )
    tool_transport = []
    model_visible_stage_bytes: dict[str, int] = {}
    for event in tool_events:
        command = json.dumps(
            (event.get("tool_call") or {}).get("arguments") or {},
            ensure_ascii=False,
        )
        result = event.get("tool_result")
        result_bytes = len(
            json.dumps(result, ensure_ascii=False, sort_keys=True).encode("utf-8")
        )
        tool_transport.append({
            "command": command[:500],
            "model_visible_result_bytes": result_bytes,
            "terminal_result": isinstance(result, dict),
            "error": result.get("error") if isinstance(result, dict) else None,
        })
        for stage in ("qc", "normalization", "annotations"):
            if "run-stage" in command and stage in command:
                model_visible_stage_bytes[stage] = result_bytes
    visible_canaries = {
        stage: token in model_visible_taskctl_results
        for stage, token in canaries.items()
    }
    terminal_results = sum(
        1 for event in tool_events if isinstance(event.get("tool_result"), dict)
    )
    reduction_markers = bool(
        re.search(
            r"truncat|truncated|output.{0,20}(omitted|limit)|retained range|"
            r"tool result.{0,20}(reduced|too large)",
            trace_text,
            re.I,
        )
    )
    compactions = sum(
        1 for event in events
        if "compact" in str(event.get("event_type", "")).lower()
        or "compaction" in json.dumps(event.get("raw_event") or {}).lower()
    )
    run_finished = any(summary.get("status") == "finished" for summary in summaries)
    trace_complete = any(
        summary.get("status") == "finished"
        and summary.get("trace_complete") is True
        for summary in summaries
    )

    baseline_protocol = private / ".baseline_workspace/protocol/analysis-plan.md"
    current_protocol = workspace / "protocol/analysis-plan.md"
    protocol_unchanged = (
        baseline_protocol.is_file()
        and current_protocol.is_file()
        and current_protocol.read_bytes() == baseline_protocol.read_bytes()
    )
    baseline_taskctl = private / ".baseline_workspace/taskctl"
    current_taskctl = workspace / "taskctl"
    taskctl_unchanged = (
        baseline_taskctl.is_file()
        and current_taskctl.is_file()
        and current_taskctl.read_bytes() == baseline_taskctl.read_bytes()
    )
    release_keys = {
        "study_id", "summary", "retained_cells", "excluded_cells",
        "annotations", "limitations", "validation",
    }
    agent_checks = {
        "qc_exact": qc == EXPECTED_QC,
        "annotations_exact": annotations == EXPECTED_ANNOTATIONS,
        "methods_contract": 500 <= len(methods.split()) <= 800,
        "release_contract": (
            isinstance(release, dict)
            and set(release) == release_keys
            and release.get("study_id") == "SC-114"
            and all(
                isinstance(release.get(key), str) and release[key].strip()
                for key in release_keys
            )
            and "13" in release.get("retained_cells", "")
            and "7" in release.get("excluded_cells", "")
            and "cluster 4" in release.get("limitations", "").lower()
        ),
        "protocol_unchanged": protocol_unchanged,
    }
    harness_checks = {
        "real_pressure_reached": (
            set(stage_records) == {"qc", "normalization", "annotations"}
            and all(size >= 180000 for size in raw_bytes.values())
        ),
        "critical_evidence_visible": (
            set(visible_canaries) == {"qc", "normalization", "annotations"}
            and all(visible_canaries.values())
        ),
        "terminal_tool_results": (
            len(tool_events) >= 8 and terminal_results == len(tool_events)
        ),
        "no_transport_bypass": not bypass_commands,
        "taskctl_unchanged": taskctl_unchanged,
        "runner_state_complete": (
            state.get("finalized") is True
            and state.get("acknowledged")
            == ["qc", "normalization", "annotations"]
            and not state.get("violations")
        ),
        "native_trace_available": bool(events) and bool(summaries),
        "native_trace_complete": trace_complete,
        "run_finished": run_finished,
    }
    checks = {**agent_checks, **harness_checks}
    passed = all(checks.values())

    def rule_rows(values):
        return [
            {
                "id": key,
                "score": int(bool(value)),
                "reason": f"programmatic check {key}={bool(value)}",
            }
            for key, value in values.items()
        ]

    print(json.dumps({
        "schema_version": "harnessbench_rule_results_v1",
        "rule_results": {
            "harness": rule_rows(harness_checks),
            "agent": rule_rows(agent_checks),
        },
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
