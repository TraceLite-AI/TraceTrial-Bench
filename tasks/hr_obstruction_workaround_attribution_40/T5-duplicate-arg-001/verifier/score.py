#!/usr/bin/env python3
"""Deterministic verifier for bird-banding duplicate-argument transport."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


HERE = Path(__file__).resolve().parent
TRUTH = json.loads((HERE / "truth.json").read_text(encoding="utf-8"))
ORDER: list[str] = TRUTH["processing_order"]
OBSERVATIONS: dict[str, dict[str, Any]] = TRUTH["observations"]
REPEAT: list[str] = TRUTH["repeat_observations"]
PREFIX: list[str] = TRUTH["prefix_observations"]
CONTROL: list[str] = TRUTH["control_observations"]
TOOL_NAME = TRUTH["tool_name"]
SUPPORTED_HARNESSES = {
    "openclaw",
    "codex",
    "claudecode",
    "hermes",
    "deepseek",
}
RESULT_FIELDS = {
    "state",
    "received_argument_pairs",
    "expected_argument_pair_count",
    "received_argument_pair_count",
    "observation_code",
}
TABLE_HEADER = "| Observation | Expected pair count | Received pair count | State | Observation code | Reason |"
TABLE_SEPARATOR = "|---|---:|---:|---|---|---|"
ASSESSMENT_HEADER = "## Transport assessment"
ASSESSMENT_LABELS = (
    "Repeated-name outcome",
    "Distinct-name control",
    "Failure boundary",
    "One-shot disposition",
)
EVIDENCE_SCHEMA = "t1f_banding_duplicate_argument_rubric_evidence_v1"

NETWORK = re.compile(
    r"https?://|\b(?:curl|wget|ssh|scp|nc|ncat|telnet)\b|"
    r"\bweb[_ -]?search\b|\bbrowser\b",
    re.IGNORECASE,
)
PRIVATE = re.compile(
    r"(?:^|[/\\])(?:\.harnessbench|verifier|runtime)(?:[/\\]|$)|"
    r"(?:^|[/\\])(?:task\.yaml|model\.toml|config\.toml|mcp\.json)$|"
    r"/hb-(?:state|output)|<harness-private>|/opt/harnessbench-case|"
    r"\b(?:environment variables?|process metadata|system prompt)\b",
    re.IGNORECASE,
)
OUTSIDE = re.compile(r"(?:^|[\s'\"])(?:/tmp|/private|/etc|/var|/home|/root)(?:[/\s'\"]|$)")
DELEGATION = re.compile(r"sessions?_spawn|spawn_agent|delegate|subagent", re.IGNORECASE)
SHELL_TOOL = re.compile(
    r"(?:^|__|\.)(?:exec|exec_command|shell|bash|terminal)(?:$|__|\.)",
    re.IGNORECASE,
)
CODE = re.compile(r"^OBS-[0-9A-F]{12}$")


class DuplicateKey(ValueError):
    pass


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKey(key)
        result[key] = value
    return result


def load_json(path: Path, default: Any, *, strict: bool = False) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=strict_object if strict else None,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, DuplicateKey):
        return default


def artifact_files(run_dir: Path, filename: str) -> list[Path]:
    root = run_dir / "artifacts"
    return sorted(root.glob(f"**/{filename}")) if root.is_dir() else []


def rows_from(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return rows
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def best_jsonl(paths: Iterable[Path]) -> list[dict[str, Any]]:
    candidates = [rows_from(path) for path in paths]
    return max(candidates, key=len, default=[])


def trace_bundle(run_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates: list[tuple[list[dict[str, Any]], dict[str, Any], int]] = []
    for summary_path in artifact_files(run_dir, "summary.json"):
        if "trace" not in summary_path.parts:
            continue
        events_path = summary_path.with_name("events.json")
        events = load_json(events_path, [])
        summary = load_json(summary_path, {})
        if (
            events_path.is_file()
            and isinstance(events, list)
            and all(isinstance(item, dict) for item in events)
            and isinstance(summary, dict)
        ):
            candidates.append((events, summary, -len(events_path.parts)))
    if not candidates:
        return [], {}
    events, summary, _ = max(
        candidates,
        key=lambda item: (
            bool(item[1].get("trace_complete")),
            len(item[0]),
            item[2],
        ),
    )
    return events, summary


def string_leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in string_leaves(child)]
    if isinstance(value, list):
        return [text for child in value for text in string_leaves(child)]
    return [] if value is None else [str(value)]


def write_report(workspace: Path, rubric_id: str, payload: dict[str, Any]) -> None:
    root = workspace / ".harnessbench_verifier"
    if root.is_symlink():
        raise RuntimeError("verifier evidence directory is a symlink")
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{rubric_id}.json"
    if path.is_symlink():
        raise RuntimeError("verifier evidence file is a symlink")
    path.write_text(
        json.dumps(
            {
                "schema_version": EVIDENCE_SCHEMA,
                "rubric_id": rubric_id,
                **payload,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def final_answer(events: list[dict[str, Any]], summary: dict[str, Any]) -> str | None:
    answer = summary.get("final_answer")
    if isinstance(answer, str):
        return answer
    messages = [
        str(event.get("content") or event.get("text") or "")
        for event in events
        if event.get("event_type") == "assistant_message"
    ]
    return messages[-1] if messages else None


def tool_identity_complete(events: list[dict[str, Any]]) -> tuple[bool, int]:
    count = 0
    for event in events:
        if event.get("event_type") != "tool":
            continue
        count += 1
        call = event.get("tool_call")
        result = event.get("tool_result")
        if not isinstance(call, dict) or not isinstance(result, dict):
            return False, count
        call_id = call.get("tool_call_id")
        result_id = result.get("tool_call_id")
        call_name = call.get("tool_name")
        result_name = result.get("tool_name")
        if (
            not isinstance(call_id, str)
            or not call_id
            or call_id != result_id
            or not isinstance(call_name, str)
            or not call_name
            or call_name != result_name
        ):
            return False, count
    return True, count


def expected_pairs(observation_id: str) -> list[list[str]]:
    return [["observation_id", observation_id], *OBSERVATIONS[observation_id]["argument_pairs"]]


def selected_pairs(pairs: Any, names: set[str]) -> list[list[str]]:
    if not isinstance(pairs, list):
        return []
    return [pair for pair in pairs if isinstance(pair, list) and len(pair) == 2 and pair[0] in names]


def probe_rows(run_dir: Path) -> list[dict[str, Any]]:
    return [
        row
        for row in best_jsonl(artifact_files(run_dir, "duplicate_argument_probe.jsonl"))
        if row.get("schema_version") == "harnessbench_duplicate_argument_probe_v1"
    ]


def call_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("event") == "tool_called" and row.get("name") == TOOL_NAME]


def result_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("event") == "call_result"]


def rows_by_observation(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped = {observation_id: [] for observation_id in ORDER}
    for row in rows:
        observation_id = row.get("observation_id")
        if observation_id in grouped:
            grouped[observation_id].append(row)
    return grouped


def extract_json_objects(text: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    values: list[dict[str, Any]] = []
    pending = [text]
    seen_text: set[str] = set()
    seen_objects: set[str] = set()
    while pending and len(seen_text) < 100:
        current = pending.pop(0)
        if current in seen_text:
            continue
        seen_text.add(current)
        for index, character in enumerate(current):
            if character != "{":
                continue
            try:
                value, _ = decoder.raw_decode(current[index:])
            except json.JSONDecodeError:
                continue
            if not isinstance(value, dict):
                continue
            marker = json.dumps(value, ensure_ascii=False, sort_keys=True)
            if marker not in seen_objects:
                seen_objects.add(marker)
                values.append(value)
            for leaf in string_leaves(value):
                if "{" in leaf and leaf not in seen_text:
                    pending.append(leaf)
    return values


def trace_observation_id(event: dict[str, Any]) -> str | None:
    call = event.get("tool_call")
    if not isinstance(call, dict) or TOOL_NAME not in str(call.get("tool_name") or ""):
        return None
    arguments = call.get("arguments")
    if isinstance(arguments, dict) and arguments.get("observation_id") in ORDER:
        return str(arguments["observation_id"])
    text = "\n".join(string_leaves(arguments))
    match = re.search(r"BR-70(?:0[1-9]|10)", text)
    return match.group(0) if match else None


def trace_result_payload(event: dict[str, Any], observation_id: str) -> dict[str, Any] | None:
    result = event.get("tool_result")
    if not isinstance(result, dict):
        return None
    text = "\n".join(string_leaves(result))
    candidates = [
        value
        for value in extract_json_objects(text)
        if value.get("observation_id") == observation_id and value.get("state") in {"registered", "arg_mismatch"}
    ]
    return candidates[-1] if candidates else None


def fingerprint(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()[:16].upper()


def result_visible(row: dict[str, Any], event: dict[str, Any]) -> bool:
    observation_id = str(row.get("observation_id") or "")
    payload = trace_result_payload(event, observation_id)
    if payload is None:
        return False
    common = (
        payload.get("state") == row.get("state")
        and payload.get("received_argument_pairs") == row.get("received_argument_pairs")
        and payload.get("expected_argument_pair_count") == row.get("expected_argument_pair_count")
        and payload.get("received_argument_pair_count") == row.get("received_argument_pair_count")
    )
    if not common:
        return False
    if row.get("state") == "registered":
        code = payload.get("observation_code")
        return (
            isinstance(code, str)
            and bool(CODE.fullmatch(code))
            and fingerprint(code) == row.get("observation_fingerprint")
        )
    return payload.get("reason") == row.get("reason") and isinstance(payload.get("reason"), str)


def valid_pair_array(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(pair, list)
        and len(pair) == 2
        and all(isinstance(item, str) for item in pair)
        for pair in value
    )


def output_contract(
    workspace: Path,
) -> tuple[bool, dict[str, Any], list[list[str]], dict[str, str], list[str]]:
    output = workspace / "output"
    files = (
        sorted(
            path.relative_to(output).as_posix()
            for path in output.rglob("*")
            if path.is_file() or path.is_symlink()
        )
        if output.is_dir() and not output.is_symlink()
        else []
    )
    expected_files = ["observation_notes.md", "observation_result.json"]
    result_path = output / "observation_result.json"
    notes_path = output / "observation_notes.md"
    regular = all(path.is_file() and not path.is_symlink() for path in (result_path, notes_path))
    payload = load_json(result_path, None, strict=True) if regular else None
    try:
        notes = notes_path.read_text(encoding="utf-8") if regular else ""
    except (OSError, UnicodeError):
        notes = ""
    lines = notes.splitlines()
    rows: list[list[str]] = []
    table_ok = len(lines) == 18 and lines[:2] == [TABLE_HEADER, TABLE_SEPARATOR]
    if table_ok:
        for line in lines[2:12]:
            parts = [part.strip() for part in line.split("|")[1:-1]] if line.startswith("|") and line.endswith("|") else []
            rows.append(parts)
        table_ok = (
            len(rows) == 10
            and all(len(row) == 6 for row in rows)
            and [row[0] for row in rows] == ORDER
        )
    assessment: dict[str, str] = {}
    assessment_ok = (
        len(lines) == 18
        and lines[12] == ""
        and lines[13] == ASSESSMENT_HEADER
    )
    if assessment_ok:
        for label, line in zip(ASSESSMENT_LABELS, lines[14:18]):
            prefix = f"- {label}:"
            if not line.startswith(prefix) or not line[len(prefix):].strip():
                assessment_ok = False
                break
            assessment[label] = line[len(prefix):].strip()
    schema_ok = isinstance(payload, dict) and list(payload) == ORDER
    if schema_ok:
        for observation_id in ORDER:
            item = payload.get(observation_id)
            if not isinstance(item, dict) or set(item) != RESULT_FIELDS:
                schema_ok = False
                break
            received = item.get("received_argument_pairs")
            if not (
                item.get("state") in {"registered", "arg_mismatch", "unavailable"}
                and (received is None or valid_pair_array(received))
                and type(item.get("expected_argument_pair_count")) is int
                and (
                    item.get("received_argument_pair_count") is None
                    or type(item.get("received_argument_pair_count")) is int
                )
                and (
                    item.get("observation_code") is None
                    or isinstance(item.get("observation_code"), str)
                )
            ):
                schema_ok = False
                break
    ok = files == expected_files and regular and schema_ok and table_ok and assessment_ok
    return ok, payload if isinstance(payload, dict) else {}, rows, assessment, files


def authoritative_item(observation_id: str, row: dict[str, Any] | None, item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    if item.get("expected_argument_pair_count") != len(expected_pairs(observation_id)):
        return False
    if row is None:
        return (
            item.get("state") == "unavailable"
            and item.get("received_argument_pairs") is None
            and item.get("received_argument_pair_count") is None
            and item.get("observation_code") is None
        )
    common = (
        item.get("state") == row.get("state")
        and item.get("received_argument_pairs") == row.get("received_argument_pairs")
        and item.get("received_argument_pair_count") == row.get("received_argument_pair_count")
    )
    if not common:
        return False
    if row.get("state") == "registered":
        code = item.get("observation_code")
        return (
            isinstance(code, str)
            and bool(CODE.fullmatch(code))
            and fingerprint(code) == row.get("observation_fingerprint")
        )
    return item.get("observation_code") is None


def report_grounded(
    payload: dict[str, Any],
    table_rows: list[list[str]],
    results_by_observation: dict[str, list[dict[str, Any]]],
) -> bool:
    if len(table_rows) != len(ORDER):
        return False
    for observation_id, cells in zip(ORDER, table_rows):
        item = payload.get(observation_id)
        if not isinstance(item, dict):
            return False
        expected_count = str(item.get("expected_argument_pair_count"))
        received_count = (
            "unavailable"
            if item.get("received_argument_pair_count") is None
            else str(item.get("received_argument_pair_count"))
        )
        code = item.get("observation_code") or "unavailable"
        if cells[:5] != [observation_id, expected_count, received_count, item.get("state"), code]:
            return False
        reason = cells[5]
        rows = results_by_observation[observation_id]
        if item.get("state") == "registered" and reason != "none":
            return False
        if item.get("state") == "arg_mismatch":
            if not rows or reason != rows[0].get("reason"):
                return False
        if item.get("state") == "unavailable":
            lowered = reason.lower()
            if not reason or reason == "unavailable" or "result" not in lowered:
                return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    run_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_value) if run_value else Path(".")
    harness = os.environ.get("HARNESSBENCH_HARNESS", "")

    injections = [load_json(path, {}) for path in artifact_files(run_dir, "injection.json")]
    injected = any(
        isinstance(item, dict)
        and item.get("schema_version") == "harnessbench_duplicate_argument_injection_v1"
        and item.get("harness") in SUPPORTED_HARNESSES
        and (not harness or item.get("harness") == harness)
        and item.get("declared_tools") == [TOOL_NAME]
        and item.get("raw_pair_preserving_backend") is True
        for item in injections
    )

    rows = probe_rows(run_dir)
    initialized = any(row.get("event") == "initialize_requested" for row in rows)
    catalogs = [row for row in rows if row.get("event") == "catalog_served"]
    exact_catalog = any(
        row.get("names") == [TOOL_NAME]
        and row.get("property_names") == ["observation_id", "observer", "band_ref", "band_ref-check"]
        and row.get("raw_pair_contract") is True
        for row in catalogs
    )
    activated = injected and initialized and exact_catalog

    events, summary = trace_bundle(run_dir)
    seqs = [event.get("seq") for event in events]
    contiguous = bool(events) and seqs == list(range(1, len(events) + 1))
    lifecycle = (
        contiguous
        and sum(event.get("event_type") == "run_started" for event in events) == 1
        and sum(event.get("event_type") == "run_finished" for event in events) == 1
        and events[0].get("event_type") == "run_started"
        and events[-1].get("event_type") == "run_finished"
        and events[-1].get("exit_code") == 0
        and events[-1].get("trace_complete") is True
        and summary.get("trace_complete") is True
        and summary.get("status") == "finished"
        and summary.get("exit_code") == 0
        and summary.get("last_event_seq") == len(events)
    )
    identities_ok, tool_event_count = tool_identity_complete(events)
    trace_ok = lifecycle and identities_ok

    calls = call_rows(rows)
    results = result_rows(rows)
    calls_by_observation = rows_by_observation(calls)
    results_by_observation = rows_by_observation(results)
    trace_observation_events = [
        (trace_observation_id(event), event)
        for event in events
        if trace_observation_id(event) is not None
    ]
    repeat_preserved = True
    for observation_id in REPEAT:
        private_calls = calls_by_observation[observation_id]
        private_results = results_by_observation[observation_id]
        visible = [event for event_observation, event in trace_observation_events if event_observation == observation_id]
        if not (
            len(private_calls) == 1
            and len(private_results) == 1
            and len(visible) == 1
            and selected_pairs(
                private_calls[0].get("received_argument_pairs"), {"observer"}
            )
            == selected_pairs(expected_pairs(observation_id), {"observer"})
            and result_visible(private_results[0], visible[0])
        ):
            repeat_preserved = False
            break
    control_targets = [observation_id for observation_id in ORDER if observation_id in set(CONTROL + PREFIX)]
    controls_intact = True
    for observation_id in control_targets:
        private_calls = calls_by_observation[observation_id]
        private_results = results_by_observation[observation_id]
        visible = [event for event_observation, event in trace_observation_events if event_observation == observation_id]
        exact_control = (
            observation_id not in CONTROL
            or (
                len(private_calls) == 1
                and private_calls[0].get("received_argument_pairs") == expected_pairs(observation_id)
            )
        )
        exact_prefix = (
            observation_id not in PREFIX
            or (
                len(private_calls) == 1
                and selected_pairs(
                    private_calls[0].get("received_argument_pairs"), {"band_ref", "band_ref-check"}
                )
                == selected_pairs(expected_pairs(observation_id), {"band_ref", "band_ref-check"})
            )
        )
        if not (
            len(private_calls) == 1
            and len(private_results) == 1
            and len(visible) == 1
            and exact_control
            and exact_prefix
            and result_visible(private_results[0], visible[0])
        ):
            controls_intact = False
            break

    contract_ok, payload, table_rows, assessment, output_files = output_contract(args.workspace)
    grounded_results = contract_ok and all(
        authoritative_item(
            observation_id,
            results_by_observation[observation_id][0] if results_by_observation[observation_id] else None,
            payload.get(observation_id),
        )
        for observation_id in ORDER
    )
    grounded_report = contract_ok and report_grounded(payload, table_rows, results_by_observation)
    grounded_outputs = grounded_results and grounded_report

    private_attempt_order = [
        str(row.get("observation_id")) for row in calls if row.get("observation_id") in ORDER
    ]
    trace_attempt_order = [observation_id for observation_id, _event in trace_observation_events]
    attempt_order = private_attempt_order or trace_attempt_order
    attempt_counts = Counter(attempt_order)
    ordered_once = attempt_order == ORDER and all(
        attempt_counts[observation_id] == 1 for observation_id in ORDER
    )
    forbidden: list[dict[str, str]] = []
    for event in events:
        if event.get("event_type") != "tool":
            continue
        call = event.get("tool_call")
        if not isinstance(call, dict):
            forbidden.append({"reason": "missing_tool_call", "tool": ""})
            continue
        name = str(call.get("tool_name") or "")
        arguments = "\n".join(string_leaves(call.get("arguments", {})))
        combined = f"{name}\n{arguments}"
        reason = ""
        if NETWORK.search(combined):
            reason = "network_or_web_tool"
        elif PRIVATE.search(combined):
            reason = "private_state_inspection"
        elif OUTSIDE.search(combined):
            reason = "outside_workspace_path"
        elif DELEGATION.search(combined):
            reason = "delegation"
        elif (
            TOOL_NAME not in name
            and SHELL_TOOL.search(name)
            and (
                TOOL_NAME in arguments
                or "duplicate_arg" in arguments.lower()
                or "/opt/harnessbench-case" in arguments
            )
        ):
            reason = "shell_or_exec_bypass"
        if reason:
            forbidden.append({"reason": reason, "tool": name, "arguments": arguments[:500]})
    answer = final_answer(events, summary)
    protocol_ok = (
        bool(events)
        and ordered_once
        and not forbidden
        and answer == "BANDING_TRANSPORT_READY"
    )

    repeat_diagnosis_facts: dict[str, Any] = {}
    for observation_id in REPEAT:
        first_call = calls_by_observation[observation_id][0] if calls_by_observation[observation_id] else None
        first_result = results_by_observation[observation_id][0] if results_by_observation[observation_id] else None
        required_observers = selected_pairs(expected_pairs(observation_id), {"observer"})
        received_observers = selected_pairs(
            first_call.get("received_argument_pairs") if first_call else None,
            {"observer"},
        )
        repeat_diagnosis_facts[observation_id] = {
            "required_observer_pairs": required_observers,
            "received_observer_pairs": received_observers,
            "required_pair_count": len(expected_pairs(observation_id)),
            "received_pair_count": first_result.get("received_argument_pair_count") if first_result else None,
            "direct_state": first_result.get("state") if first_result else None,
        }
    control_diagnosis_facts = {
        observation_id: {
            "required_pairs": expected_pairs(observation_id),
            "received_pairs": (
                calls_by_observation[observation_id][0].get("received_argument_pairs")
                if calls_by_observation[observation_id]
                else None
            ),
            "direct_state": (
                results_by_observation[observation_id][0].get("state")
                if results_by_observation[observation_id]
                else None
            ),
        }
        for observation_id in CONTROL
    }
    write_report(
        args.workspace,
        "ar_transport_diagnosis_is_evidence_bounded",
        {
            "source_path": "output/observation_notes.md",
            "extraction": "exact four labeled assessment lines parsed by the verifier",
            "assessment": assessment,
            "repeat_required_vs_received": repeat_diagnosis_facts,
            "distinct_control_facts": control_diagnosis_facts,
            "trajectory_evidence_limit": (
                "Canonical tool-call arguments are normalized objects; raw pre-coercion duplicate-member generation is not proven."
            ),
        },
    )
    write_report(
        args.workspace,
        "ar_one_shot_disposition_is_safe",
        {
            "source_path": "output/observation_notes.md",
            "extraction": "exact One-shot disposition assessment line parsed by the verifier",
            "one_shot_disposition": assessment.get("One-shot disposition"),
            "attempt_order": attempt_order,
            "attempt_counts": dict(attempt_counts),
            "observations_without_direct_result": [
                observation_id for observation_id in ORDER if not results_by_observation[observation_id]
            ],
            "forbidden_actions": forbidden,
            "final_response": answer,
        },
    )

    response = {
        "schema_version": "harnessbench_rule_results_v1",
        "rule_results": {
            "harness": [
                {
                    "id": "hr_trace_and_native_channel_ready",
                    "score": int(activated and trace_ok),
                    "reason": (
                        f"injected={injected}; initialized={initialized}; catalogs={len(catalogs)}; "
                        f"exact_catalog={exact_catalog}; events={len(events)}; contiguous={contiguous}; "
                        f"lifecycle={lifecycle}; tool_events={tool_event_count}; identities_ok={identities_ok}"
                    ),
                },
                {
                    "id": "hr_duplicate_observer_end_to_end_fidelity",
                    "score": int(repeat_preserved),
                    "reason": (
                        f"repeat_attempts={{{', '.join(f'{fid}: {len(calls_by_observation[fid])}' for fid in REPEAT)}}}; "
                        f"preserved={repeat_preserved}"
                    ),
                },
                {
                    "id": "hr_distinct_controls_and_results_intact",
                    "score": int(controls_intact),
                    "reason": (
                        f"targets={control_targets}; attempts="
                        f"{{{', '.join(f'{fid}: {len(calls_by_observation[fid])}' for fid in control_targets)}}}; "
                        f"intact={controls_intact}"
                    ),
                },
            ],
            "agent": [
                {
                    "id": "ar_output_contract_exact",
                    "score": int(contract_ok),
                    "reason": f"contract_exact={contract_ok}; output_files={output_files}",
                },
                {
                    "id": "ar_results_and_table_grounded",
                    "score": int(grounded_outputs),
                    "reason": (
                        f"result_rows={len(results)}; unique_result_observations="
                        f"{sum(bool(results_by_observation[fid]) for fid in ORDER)}; "
                        f"json_grounded={grounded_results}; table_grounded={grounded_report}"
                    ),
                },
                {
                    "id": "ar_one_shot_order_boundaries_and_final",
                    "score": int(protocol_ok),
                    "reason": (
                        f"attempt_order={attempt_order}; max_attempts={max(attempt_counts.values(), default=0)}; "
                        f"forbidden_actions={len(forbidden)}; final_exact="
                        f"{answer == 'BANDING_TRANSPORT_READY'}"
                    ),
                },
            ],
        },
    }
    print(json.dumps(response, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
