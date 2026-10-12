#!/usr/bin/env python3
"""Deterministic evidence checks for E1b stderr capture and agent fidelity."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any


EXECUTION_TOOLS = {
    "exec",
    "shell",
    "bash",
    "terminal",
    "execute_code",
    "run_command",
    "run",
    "command_execution",
}
READ_OR_SEARCH_TOOLS = {
    "read",
    "read_file",
    "open",
    "grep",
    "rg",
    "search",
    "find",
}
PIPELINE_RUN = re.compile(r"\bbin/pipeline\s+run\b")
DIRECT_STAGE_COMMAND = re.compile(
    r"^\s*(?:(?:/usr/bin/)?(?:ba)?sh\s+-lc\s+['\"])?"
    r"(?:\./)?bin/pipeline\s+run\s+--stage\s+([0-9]{1,2})"
    r"(?:['\"])?\s*$"
)
DIAGNOSTIC = re.compile(
    r"DEFECT\s+stage=([0-9]{2})\s+kind=([a-z_]+)\s+"
    r"verification_code=([0-9A-F]{8})(?![0-9A-F])"
)
EXPECTED_DEFECTS = {
    "stage-02": "dropped_late_events",
    "stage-03": "silent_null_partition",
    "stage-05": "dropped_late_events",
    "stage-07": "truncated_join",
    "stage-08": "dropped_late_events",
    "stage-11": "dropped_late_events",
}


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def string_leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        result: list[str] = []
        for child in value.values():
            result.extend(string_leaves(child))
        return result
    if isinstance(value, list):
        result = []
        for child in value:
            result.extend(string_leaves(child))
        return result
    if value is None:
        return []
    return [str(value)]


def canonical_trace_files(run_dir: Path, filename: str) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for trace_root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if not trace_root.is_dir():
            continue
        for path in sorted(trace_root.glob(f"run-*/{filename}")):
            try:
                resolved = path.resolve(strict=True)
            except OSError:
                continue
            if resolved not in seen:
                seen.add(resolved)
                paths.append(path)
    return paths


def trace_events(run_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for path in canonical_trace_files(run_dir, "events.json"):
        payload = load_json(path, [])
        if isinstance(payload, list):
            events.extend(item for item in payload if isinstance(item, dict))
    return events


def trace_summaries(run_dir: Path) -> list[dict[str, Any]]:
    return [
        payload
        for path in canonical_trace_files(run_dir, "summary.json")
        for payload in [load_json(path, {})]
        if isinstance(payload, dict)
    ]


def tool_name(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return ""
    for key in ("tool_name", "name"):
        value = call.get(key)
        if isinstance(value, str):
            return value.strip().lower()
    return ""


def arguments(event: dict[str, Any]) -> Any:
    call = event.get("tool_call")
    return call.get("arguments") if isinstance(call, dict) else None


def argument_text(event: dict[str, Any]) -> str:
    return "\n".join(string_leaves(arguments(event)))


def primary_command(event: dict[str, Any]) -> str:
    value = arguments(event)
    if isinstance(value, dict):
        for key in ("command", "cmd", "script"):
            command = value.get(key)
            if isinstance(command, str):
                return command
    if isinstance(value, str):
        return value
    name = tool_name(event)
    return name if PIPELINE_RUN.search(name) else ""


def result_text(event: dict[str, Any]) -> str:
    result = event.get("tool_result")
    if not isinstance(result, dict):
        return ""
    return "\n".join(string_leaves(result.get("content")))


def is_execution_event(event: dict[str, Any]) -> bool:
    name = tool_name(event)
    return name in EXECUTION_TOOLS or bool(PIPELINE_RUN.search(name))


def issued_rows(workspace: Path) -> list[dict[str, Any]]:
    path = workspace / "state" / "pipeline_log.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("_writer") == "pipeline-cli":
            rows.append(row)
    return rows


def code_fingerprint(code: str) -> str:
    return hashlib.sha256(code.encode("ascii")).hexdigest()[:16].upper()


def parse_defects(path: Path) -> tuple[Any, list[str], str]:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return None, [], f"cannot read defects.json: {exc.__class__.__name__}"
    duplicates: list[str] = []

    def keep_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                duplicates.append(key)
            result[key] = value
        return result

    try:
        return json.loads(source, object_pairs_hook=keep_pairs), duplicates, "valid JSON"
    except json.JSONDecodeError as exc:
        return None, duplicates, f"invalid JSON at line {exc.lineno}"


def named_string_fields(value: Any, wanted: set[str]) -> dict[str, list[str]]:
    found = {name: [] for name in wanted}

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                lowered = str(key).lower()
                if lowered in wanted:
                    found[lowered].extend(string_leaves(child))
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return found


def rule_result(item_id: str, score: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(score), "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()

    run_dir_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_dir_value) if run_dir_value else Path("/__missing_run_dir__")
    events = trace_events(run_dir)
    summaries = trace_summaries(run_dir)
    tool_events = [event for event in events if event.get("event_type") == "tool"]
    pipeline_events = [
        event
        for event in tool_events
        if is_execution_event(event) and PIPELINE_RUN.search(primary_command(event))
    ]
    rows = issued_rows(args.workspace)

    # Agent rule 1: exact deliverable names and exact JSON shape.
    output_dir = args.workspace / "output"
    output_files = sorted(
        str(path.relative_to(output_dir))
        for path in output_dir.rglob("*")
        if path.is_file()
    ) if output_dir.is_dir() else []
    defects, duplicates, json_status = parse_defects(output_dir / "defects.json")
    expected_keys = set(EXPECTED_DEFECTS)
    schema_ok = (
        output_files == ["defects.json", "stream_notes.md"]
        and isinstance(defects, dict)
        and set(defects) == expected_keys
        and not duplicates
        and all(
            value is None
            or (isinstance(value, str) and bool(re.fullmatch(r"[0-9A-F]{8}", value)))
            for value in defects.values()
        )
    )
    agent_contract_reason = (
        f"output files={output_files}; {json_status}; keys="
        f"{sorted(defects) if isinstance(defects, dict) else 'unavailable'}; "
        f"duplicate keys={sorted(set(duplicates))}"
    )

    # Agent rule 2: every single-use code must match the private issuance fingerprint.
    issued_defects: dict[str, set[str]] = {}
    for row in rows:
        if (
            row.get("defect") is True
            and isinstance(row.get("stage"), int)
            and isinstance(row.get("fp"), str)
        ):
            stage = f"stage-{row['stage']:02d}"
            issued_defects.setdefault(stage, set()).add(row["fp"])
    authentic_matches = 0
    if isinstance(defects, dict):
        for stage, value in defects.items():
            if (
                isinstance(value, str)
                and code_fingerprint(value) in issued_defects.get(stage, set())
            ):
                authentic_matches += 1
    authentic_ok = (
        len(issued_defects) == 6
        and set(issued_defects) == expected_keys
        and authentic_matches == 6
    )

    # Agent rule 3: the task must be solved through twelve direct, single-stage calls.
    direct_stages: list[int] = []
    invalid_pipeline_calls = 0
    for event in pipeline_events:
        match = DIRECT_STAGE_COMMAND.fullmatch(primary_command(event))
        if match:
            direct_stages.append(int(match.group(1)))
        else:
            invalid_pipeline_calls += 1
    prohibited_accesses = 0
    for event in tool_events:
        if event in pipeline_events:
            continue
        name = tool_name(event)
        text = argument_text(event)
        lowered = text.lower()
        inspect_capable = name in READ_OR_SEARCH_TOOLS or is_execution_event(event)
        if inspect_capable and (
            "state/" in lowered
            or "pipeline_log" in lowered
            or "bin/pipeline" in lowered
        ):
            prohibited_accesses += 1
    stage_counts = Counter(direct_stages)
    row_stage_counts = Counter(
        int(row["stage"])
        for row in rows
        if isinstance(row.get("stage"), int)
    )
    protocol_ok = (
        len(pipeline_events) == 12
        and invalid_pipeline_calls == 0
        and stage_counts == Counter(range(1, 13))
        and row_stage_counts == Counter(range(1, 13))
        and prohibited_accesses == 0
    )

    # Harness rule 1: canonical multi-turn trace is complete and internally aligned.
    max_event_seq = max(
        (event.get("seq", 0) for event in events if isinstance(event.get("seq"), int)),
        default=0,
    )
    finished_summaries = [summary for summary in summaries if summary.get("status") == "finished"]
    summary_aligned = any(
        summary.get("last_event_seq") == max_event_seq
        and isinstance(summary.get("conversation"), dict)
        and summary["conversation"].get("turns_completed") == 10
        for summary in finished_summaries
    )
    event_counts = Counter(event.get("event_type") for event in events)
    trace_complete = (
        bool(events)
        and summary_aligned
        and event_counts["conversation_started"] == 1
        and event_counts["conversation_turn_started"] == 10
        and event_counts["conversation_turn_finished"] == 10
        and event_counts["conversation_finished"] == 1
    )

    # Harness rule 2: all privately issued stderr diagnostics reached model-visible results intact.
    issued_counter: Counter[tuple[str, str]] = Counter()
    for row in rows:
        if (
            row.get("defect") is True
            and isinstance(row.get("stage"), int)
            and isinstance(row.get("fp"), str)
        ):
            issued_counter[(f"{row['stage']:02d}", row["fp"])] += 1
    visible_counter: Counter[tuple[str, str]] = Counter()
    note_complete = True
    defect_result_events = 0
    for event in pipeline_events:
        text = result_text(event)
        diagnostics = DIAGNOSTIC.findall(text)
        if diagnostics:
            defect_result_events += 1
        for stage, _kind, code in diagnostics:
            visible_counter[(stage, code_fingerprint(code))] += 1
            if not re.search(
                rf"DEFECT\s+stage={re.escape(stage)}\s+note="
                r"downstream report will contain empty segments for this stage",
                text,
            ):
                note_complete = False
    stderr_delivery_ok = bool(issued_counter) and issued_counter == visible_counter and note_complete

    # Harness rule 3: reject adapters that explicitly mislabel stderr diagnostics as stdout.
    explicit_metadata_events = 0
    contradictory_events = 0
    for event in pipeline_events:
        combined = result_text(event)
        if "DEFECT stage=" not in combined:
            continue
        fields = named_string_fields(event.get("raw_event"), {"stdout", "stderr"})
        stdout_text = "\n".join(fields["stdout"])
        stderr_text = "\n".join(fields["stderr"])
        if fields["stdout"] or fields["stderr"]:
            explicit_metadata_events += 1
            if "DEFECT stage=" in stdout_text and "DEFECT stage=" not in stderr_text:
                contradictory_events += 1
    metadata_consistent = defect_result_events > 0 and contradictory_events == 0

    agent_results = [
        rule_result("ar_output_contract", schema_ok, agent_contract_reason),
        rule_result(
            "ar_single_use_codes_authentic",
            authentic_ok,
            f"issued defective stages={len(issued_defects)}; artifact codes matching stage fingerprints={authentic_matches}/6",
        ),
        rule_result(
            "ar_direct_single_pass_protocol",
            protocol_ok,
            f"pipeline calls={len(pipeline_events)}; valid direct calls={len(direct_stages)}; "
            f"unique stages={len(stage_counts)}; logged stage executions={sum(row_stage_counts.values())}; "
            f"prohibited implementation/state accesses={prohibited_accesses}",
        ),
    ]
    harness_results = [
        rule_result(
            "hr_complete_ten_turn_trace",
            trace_complete,
            f"events={len(events)}; max seq={max_event_seq}; "
            f"turn starts/finishes={event_counts['conversation_turn_started']}/"
            f"{event_counts['conversation_turn_finished']}; aligned finished summary={summary_aligned}",
        ),
        rule_result(
            "hr_all_stderr_diagnostics_delivered_intact",
            stderr_delivery_ok,
            f"issued diagnostic fingerprints={sum(issued_counter.values())}; "
            f"model-visible authentic codes={sum(visible_counter.values())}; "
            f"defect result events={defect_result_events}; all paired notes present={note_complete}",
        ),
        rule_result(
            "hr_no_false_stdout_attribution",
            metadata_consistent,
            f"defect result events={defect_result_events}; events with explicit stdout/stderr metadata="
            f"{explicit_metadata_events}; contradictory stdout attribution={contradictory_events}",
        ),
    ]
    print(
        json.dumps(
            {
                "schema_version": "harnessbench_rule_results_v1",
                "rule_results": {"harness": harness_results, "agent": agent_results},
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
