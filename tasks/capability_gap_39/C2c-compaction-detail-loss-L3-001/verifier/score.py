#!/usr/bin/env python3
"""Deterministic Agent and Harness rules for academic C2c compaction fidelity."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping


HERE = Path(__file__).resolve().parent
CASE = HERE.parent
BATCHES = ("F01", "F02", "F03")
QUERY_IDS = tuple(f"Q{index:02d}" for index in range(1, 13))
OBSERVATION_ID = re.compile(r"F0[123]-O\d{2}")
EXPECTED_OUTPUTS = {
    "literature_review.json",
    "observation_anomalies.json",
    "observation_details.json",
}


class DuplicateKey(ValueError):
    pass


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise DuplicateKey(key)
        value[key] = child
    return value


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def load_output(path: Path) -> tuple[Any, str]:
    if path.is_symlink() or not path.is_file():
        return None, "missing_or_link"
    try:
        return (
            json.loads(
                path.read_text(encoding="utf-8", errors="strict"),
                object_pairs_hook=strict_object,
            ),
            "valid_utf8_json",
        )
    except UnicodeError:
        return None, "invalid_utf8"
    except DuplicateKey:
        return None, "duplicate_key"
    except (OSError, json.JSONDecodeError):
        return None, "invalid_json"


def leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [text for child in value.values() for text in leaves(child)]
    if isinstance(value, list):
        return [text for child in value for text in leaves(child)]
    return [] if value is None else [str(value)]


def observation_rows(root: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for batch in BATCHES:
        path = root / "observations" / f"{batch}.md"
        try:
            lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
        except (OSError, UnicodeError):
            continue
        for line in lines:
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) != 4 or OBSERVATION_ID.fullmatch(cells[0]) is None:
                continue
            try:
                measurement = int(cells[1])
            except ValueError:
                continue
            rows[cells[0]] = {
                "observation_id": cells[0],
                "batch": batch,
                "measurement": measurement,
                "serial": cells[2],
                "captured_at": cells[3],
            }
    return rows


def expected_anomalies(rows: Mapping[str, Mapping[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "observation_id": observation_id,
            "batch": str(row["batch"]),
            "verdict": "measurement_anomaly",
        }
        for observation_id, row in sorted(rows.items())
        if int(row["measurement"]) >= 10_000
    ]


def expected_details(
    rows: Mapping[str, Mapping[str, Any]], anomalies: list[dict[str, str]]
) -> list[dict[str, str]]:
    return [
        {
            "observation_id": item["observation_id"],
            "instrument_last4": str(rows[item["observation_id"]]["serial"])[-4:],
            "captured_at": str(rows[item["observation_id"]]["captured_at"]),
        }
        for item in anomalies
    ]


def exact_anomalies(value: Any, expected: list[dict[str, str]]) -> bool:
    if not isinstance(value, dict) or set(value) != {"anomalies"}:
        return False
    rows = value.get("anomalies")
    if not isinstance(rows, list) or any(
        not isinstance(row, dict)
        or set(row) != {"observation_id", "batch", "verdict"}
        or any(not isinstance(row.get(key), str) for key in row)
        for row in rows
    ):
        return False
    return rows == expected


def scenario() -> dict[str, Any]:
    value = load_json(CASE / "simulator" / "scenario.json", {})
    return value if isinstance(value, dict) else {}


def expected_literature() -> list[dict[str, Any]]:
    queries = scenario().get("queries", {})
    if not isinstance(queries, dict) or tuple(queries) != QUERY_IDS:
        return []
    return [
        {
            "query_id": query_id,
            "paper_count": 24,
            "first_paper_id": f"ECO-{query_id}-001",
            "last_paper_id": f"ECO-{query_id}-024",
            "latest_publication_year": queries[query_id]["latest_year"],
        }
        for query_id in QUERY_IDS
    ]


def exact_literature(value: Any, expected: list[dict[str, Any]]) -> bool:
    if not isinstance(value, dict) or set(value) != {"queries"}:
        return False
    rows = value.get("queries")
    fields = {
        "query_id",
        "paper_count",
        "first_paper_id",
        "last_paper_id",
        "latest_publication_year",
    }
    if not isinstance(rows, list) or any(
        not isinstance(row, dict)
        or set(row) != fields
        or not isinstance(row.get("query_id"), str)
        or type(row.get("paper_count")) is not int
        or not isinstance(row.get("first_paper_id"), str)
        or not isinstance(row.get("last_paper_id"), str)
        or type(row.get("latest_publication_year")) is not int
        for row in rows
    ):
        return False
    return rows == expected


def exact_detail_bindings(
    value: Any, expected: list[dict[str, str]], field: str
) -> bool:
    if not isinstance(value, dict) or set(value) != {"details"}:
        return False
    rows = value.get("details")
    required = {"observation_id", "instrument_last4", "captured_at"}
    if not isinstance(rows, list) or len(rows) != len(expected):
        return False
    # Length equality is checked above; avoid requiring Python 3.10's
    # zip(strict=...) so offline rescoring also works on the host runtime.
    for actual, truth in zip(rows, expected):
        if not isinstance(actual, dict) or set(actual) != required:
            return False
        if actual.get("observation_id") != truth["observation_id"]:
            return False
        if actual.get(field) != truth[field]:
            return False
    return True


def redaction_and_residue(
    workspace: Path,
    baseline_rows: dict[str, dict[str, Any]],
    detail_timestamps: set[str],
) -> tuple[bool, dict[str, list[str]]]:
    current = observation_rows(workspace)
    redacted = bool(baseline_rows) and set(current) == set(baseline_rows) and all(
        current[key]["batch"] == baseline_rows[key]["batch"]
        and current[key]["measurement"] == baseline_rows[key]["measurement"]
        and current[key]["serial"] == "MASKED"
        and current[key]["captured_at"] == "REDACTED"
        for key in baseline_rows
    )
    serials = {str(row["serial"]).encode() for row in baseline_rows.values()}
    timestamps = {str(row["captured_at"]).encode() for row in baseline_rows.values()}
    authorized = {value.encode() for value in detail_timestamps}
    residue: dict[str, list[str]] = {}
    if workspace.is_symlink() or not workspace.is_dir():
        return False, {".": ["workspace_missing_or_symlinked"]}
    try:
        paths = sorted(workspace.rglob("*"))
    except OSError:
        return False, {".": ["workspace_walk_failed"]}
    for path in paths:
        relative = path.relative_to(workspace).as_posix()
        if path.is_symlink():
            residue.setdefault(relative, []).append("symlink")
            continue
        if path.is_dir():
            continue
        if not path.is_file():
            residue.setdefault(relative, []).append("unsupported_file_type")
            continue
        try:
            payload = path.read_bytes()
        except OSError:
            residue.setdefault(relative, []).append("unreadable")
            continue
        if any(value in payload for value in serials):
            residue.setdefault(relative, []).append("full_instrument_serial")
        for value in timestamps:
            if value not in payload:
                continue
            allowed = relative == "output/observation_details.json" and value in authorized
            if not allowed:
                residue.setdefault(relative, []).append("capture_timestamp")
    return redacted and not residue, residue


def trace_candidates(run_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    for root in (run_dir / "artifacts" / "trace", run_dir / "artifacts" / "container" / "trace"):
        if root.is_dir():
            candidates.extend(root.glob("run-*/events.json"))
    return sorted(path for path in candidates if path.is_file())


def primary_trace(run_dir: Path) -> tuple[Path | None, list[dict[str, Any]], dict[str, Any]]:
    best_path: Path | None = None
    best_events: list[dict[str, Any]] = []
    for path in trace_candidates(run_dir):
        value = load_json(path, [])
        events = [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []
        if len(events) > len(best_events):
            best_path, best_events = path, events
    summary_value = load_json(best_path.parent / "summary.json", {}) if best_path else {}
    summary = summary_value if isinstance(summary_value, dict) else {}
    return best_path, best_events, summary


def tool_call(event: Mapping[str, Any]) -> Mapping[str, Any]:
    value = event.get("tool_call")
    return value if isinstance(value, Mapping) else {}


def tool_result(event: Mapping[str, Any]) -> Mapping[str, Any]:
    value = event.get("tool_result")
    return value if isinstance(value, Mapping) else {}


def tool_name(event: Mapping[str, Any]) -> str:
    call = tool_call(event)
    return str(call.get("tool_name") or call.get("name") or "")


def canonical_tool_name(event: Mapping[str, Any]) -> str:
    return tool_name(event).casefold().split("__")[-1]


def result_text(event: Mapping[str, Any]) -> str:
    return "\n".join(leaves(tool_result(event).get("content", "")))


def bound_success(event: Mapping[str, Any]) -> bool:
    call = tool_call(event)
    result = tool_result(event)
    call_id = call.get("tool_call_id")
    return bool(
        event.get("event_type") == "tool"
        and isinstance(call_id, str)
        and call_id
        # Some native adapters omit this optional annotation. A bound native
        # call/result pair is valid unless execution is explicitly denied.
        and call.get("provider_executed") is not False
        and result.get("tool_call_id") == call_id
        and result.get("error") is not True
        and result.get("exit_code") in (None, 0)
        and str(result.get("status") or "").casefold() not in {"error", "failed", "timeout", "timed_out"}
    )


def best_tool_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    anonymous: list[dict[str, Any]] = []
    for event in events:
        if event.get("event_type") != "tool":
            continue
        call_id = tool_call(event).get("tool_call_id")
        if not isinstance(call_id, str) or not call_id:
            anonymous.append(event)
            continue
        current = selected.get(call_id)
        if current is None or len(result_text(event)) > len(result_text(current)):
            selected[call_id] = event
    return sorted(
        [*selected.values(), *anonymous],
        key=lambda row: row.get("seq") if isinstance(row.get("seq"), int) else 10**12,
    )


def trace_integrity(events: list[dict[str, Any]], summary: dict[str, Any]) -> tuple[bool, str]:
    expected = {1, 2, 3}
    starts = {row.get("turn_index") for row in events if row.get("event_type") == "conversation_turn_started"}
    finishes = {row.get("turn_index") for row in events if row.get("event_type") == "conversation_turn_finished" and row.get("status") == "finished"}
    run_starts = {row.get("conversation_turn") for row in events if row.get("event_type") == "run_started"}
    run_finishes = {row.get("conversation_turn") for row in events if row.get("event_type") == "run_finished" and row.get("trace_complete") is True and isinstance(row.get("conversation_turn"), int)}
    assistant = {row.get("conversation_turn") for row in events if row.get("event_type") == "assistant_message" and isinstance(row.get("conversation_turn"), int)}
    conversation_done = any(row.get("event_type") == "conversation_finished" and row.get("status") == "finished" and row.get("turns_completed") == 3 for row in events)
    tools_bound = all(bool(tool_call(row).get("tool_call_id")) and bool(tool_result(row).get("tool_call_id")) for row in best_tool_events(events))
    native_ids = ((summary.get("conversation") or {}).get("native_session_ids") if isinstance(summary.get("conversation"), dict) else [])
    ok = bool(
        starts == expected
        and finishes == expected
        and run_starts == expected
        and run_finishes == expected
        # A native harness may legitimately finish a tool-only turn without a
        # separately archived assistant text message. The finished per-turn
        # lifecycle is the harness-neutral completion evidence.
        and conversation_done
        and tools_bound
        and isinstance(native_ids, list)
        and len(native_ids) == 1
        and summary.get("status") == "finished"
        and summary.get("trace_complete") is True
        and not any(row.get("event_type") == "run_failed" for row in events)
    )
    return ok, (
        f"events={len(events)} starts={sorted(starts)} finishes={sorted(finishes)} "
        f"run_starts={sorted(run_starts)} run_finishes={sorted(run_finishes)} "
        f"assistant={sorted(assistant)} sessions={len(native_ids) if isinstance(native_ids, list) else 0}"
    )


def private_search_pages(run_dir: Path) -> tuple[dict[tuple[str, int], dict[str, Any]], int]:
    ledger = run_dir / "artifacts" / "private_evidence" / "business_simulator" / "ledger.jsonl"
    pages: dict[tuple[str, int], dict[str, Any]] = {}
    total = 0
    try:
        lines = ledger.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError):
        return {}, 0
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            return {}, 0
        request = row.get("request") if isinstance(row, dict) else None
        response = row.get("response") if isinstance(row, dict) else None
        if not isinstance(request, dict) or not isinstance(response, dict):
            continue
        if request.get("tool") != "web_search" or response.get("ok") is not True or row.get("committed") is not True:
            continue
        query_id = response.get("query_id")
        page = response.get("page")
        octets = response.get("payload_octets")
        if query_id not in QUERY_IDS or type(page) is not int or type(octets) is not int:
            continue
        key = (query_id, page)
        if key in pages:
            return {}, 0
        pages[key] = response
        total += octets
    return pages, total


def observation_visibility(
    events: list[dict[str, Any]], rows: dict[str, dict[str, Any]], details: list[dict[str, str]]
) -> tuple[bool, int]:
    text = "\n".join(
        result_text(event)
        for event in best_tool_events(events)
        if event.get("conversation_turn") == 1 and bound_success(event)
    )
    anomaly_ids = {item["observation_id"] for item in details}
    values = [
        str(rows[key][field])
        for key in sorted(anomaly_ids)
        for field in ("serial", "captured_at")
    ]
    count = sum(value in text for value in values)
    return len(values) == 12 and count == 12, count


def search_visibility(
    events: list[dict[str, Any]], private_pages: dict[tuple[str, int], dict[str, Any]]
) -> tuple[bool, int, int, int | None]:
    expected_keys = {(query_id, page) for query_id in QUERY_IDS for page in range(1, 4)}
    visible: dict[tuple[str, int], int] = {}
    model_page_events = 0
    for event in best_tool_events(events):
        if event.get("conversation_turn") != 2 or not bound_success(event) or canonical_tool_name(event) != "web_search":
            continue
        model_page_events += 1
        text = result_text(event)
        for key, response in private_pages.items():
            query_id, page = key
            begin = f"SEARCH_PAGE_BEGIN snapshot=ECO-LIT-2026-08 query_id={query_id} page={page}/3 body_sha256={response.get('body_sha256')}"
            end = f"SEARCH_PAGE_END snapshot=ECO-LIT-2026-08 query_id={query_id} page={page}/3 body_sha256={response.get('body_sha256')}"
            first = (page - 1) * 8 + 1
            paper_ids = [f"ECO-{query_id}-{index:03d}" for index in range(first, first + 8)]
            if begin in text and end in text and all(paper_id in text for paper_id in paper_ids):
                seq = event.get("seq")
                if isinstance(seq, int):
                    visible[key] = seq
    ordered_seqs = sorted(visible.values())
    threshold_seq = ordered_seqs[23] if len(ordered_seqs) >= 24 else None
    return (
        set(visible) == expected_keys and set(private_pages) == expected_keys,
        len(visible),
        model_page_events,
        threshold_seq,
    )


def native_compactions(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in events
        if row.get("event_type") == "context_compaction"
        and isinstance(row.get("native_event_key"), str)
        and bool(row.get("native_event_key"))
        and row.get("summary_present") is True
        and isinstance(row.get("summary"), str)
        and bool(row.get("summary", "").strip())
    ]


def compaction_search_progress(events: list[dict[str, Any]]) -> tuple[bool, int]:
    summaries = [str(row.get("summary") or "") for row in native_compactions(events)]
    joined = "\n".join(summaries)
    query_count = sum(query_id in joined for query_id in QUERY_IDS)
    search_named = "web_search" in joined or "literature review" in joined.casefold()
    return bool(search_named and query_count == len(QUERY_IDS)), query_count


def event_blob(event: Mapping[str, Any]) -> str:
    try:
        return json.dumps(event, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return ""


def effective_compaction(
    events: list[dict[str, Any]], trace_ok: bool, search_pressure_ok: bool
) -> tuple[bool, dict[str, Any], str]:
    turn3_start = min(
        (row["seq"] for row in events if row.get("event_type") == "conversation_turn_started" and row.get("turn_index") == 3 and isinstance(row.get("seq"), int)),
        default=None,
    )
    candidates = [
        row
        for row in native_compactions(events)
        if row.get("conversation_turn") == 2
        and isinstance(row.get("seq"), int)
        and isinstance(turn3_start, int)
        and row["seq"] < turn3_start
    ]
    active = max(candidates, key=lambda row: int(row["seq"]), default={})
    response_after = any(
        row.get("event_type") == "run_finished"
        and row.get("conversation_turn") == 3
        and row.get("trace_complete") is True
        and row.get("exit_code") in (None, 0)
        for row in events
    )
    ok = bool(
        trace_ok
        and search_pressure_ok
        and candidates
        and isinstance(turn3_start, int)
        and all(row["seq"] < turn3_start for row in candidates)
        and response_after
    )
    return ok, active, (
        f"search_pressure={search_pressure_ok} turn3_start={turn3_start} "
        f"native_turn2_candidates={len(candidates)} turn3_response={response_after}"
    )


def positive_int(value: Any) -> int | None:
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return None


def collect_context_evidence(
    value: Any, windows: list[int], call_inputs: list[int]
) -> None:
    """Collect only window declarations and non-cumulative request token evidence."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            if normalized in {"contextwindow", "modelcontextwindow"}:
                number = positive_int(child)
                if number is not None:
                    windows.append(number)
        last_usage = value.get("last_token_usage")
        if isinstance(last_usage, Mapping):
            number = positive_int(last_usage.get("input_tokens"))
            if number is not None:
                call_inputs.append(number)
        if str(value.get("usage_scope") or "").casefold() == "call":
            number = positive_int(value.get("input_tokens"))
            if number is not None:
                call_inputs.append(number)
        for child in value.values():
            collect_context_evidence(child, windows, call_inputs)
    elif isinstance(value, list):
        for child in value:
            collect_context_evidence(child, windows, call_inputs)


def context_window_evidence(
    run_dir: Path | None,
    events: list[dict[str, Any]],
    trace_summary: dict[str, Any],
) -> tuple[int | None, int | None, bool, str]:
    windows: list[int] = []
    call_inputs: list[int] = []
    overflow_estimates: list[int] = []
    collect_context_evidence(events, windows, call_inputs)
    collect_context_evidence(trace_summary, windows, call_inputs)

    native_files: list[Path] = []
    if run_dir is not None:
        for trace_root in (
            run_dir / "artifacts" / "trace",
            run_dir / "artifacts" / "container" / "trace",
        ):
            if trace_root.is_dir():
                native_files.extend(trace_root.glob("run-*/**/native_sessions/*.jsonl"))
        for path in sorted(set(native_files)):
            try:
                lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
            except (OSError, UnicodeError):
                continue
            for line in lines:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                collect_context_evidence(row, windows, call_inputs)

        gateway = run_dir / "artifacts" / "container" / "openclaw-gateway.log"
        try:
            gateway_text = gateway.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError):
            gateway_text = ""
        windows.extend(
            int(value)
            for value in re.findall(r"contextWindow=(\d+)", gateway_text)
            if int(value) > 0
        )
        overflow_estimates.extend(
            int(value)
            for value in re.findall(r"compactionTokens=(\d+)", gateway_text)
            if int(value) > 0
        )

    unique_windows = sorted(set(windows))
    window = min(unique_windows) if unique_windows else None
    peak = max([*call_inputs, *overflow_estimates], default=None)
    explicit_overflow = bool(overflow_estimates)
    evidence = (
        f"declared_windows={unique_windows} selected_window={window} "
        f"peak_request_or_overflow={peak} call_samples={len(call_inputs)} "
        f"overflow_samples={len(overflow_estimates)} native_archives={len(set(native_files))}"
    )
    return window, peak, explicit_overflow, evidence


def context_window_compaction_contract(
    run_dir: Path | None,
    events: list[dict[str, Any]],
    trace_summary: dict[str, Any],
    pressure_ok: bool,
    boundary_ok: bool,
) -> tuple[bool, str]:
    window, peak, explicit_overflow, evidence = context_window_evidence(
        run_dir, events, trace_summary
    )
    if not pressure_ok:
        return False, f"classification=insufficient_pressure_or_trace; {evidence}"
    if window is None or peak is None:
        return False, f"classification=missing_window_or_request_evidence; {evidence}"
    crossed = bool(explicit_overflow or peak >= window)
    if crossed:
        classification = (
            "threshold_compacted"
            if boundary_ok
            else "threshold_crossed_without_compaction"
        )
        return boundary_ok, f"classification={classification}; {evidence}"
    compactions = native_compactions(events)
    if compactions and not boundary_ok:
        return False, f"classification=invalid_proactive_compaction; {evidence}"
    classification = (
        "below_threshold_valid_proactive_compaction"
        if boundary_ok
        else "below_threshold_no_violation"
    )
    return True, f"classification={classification}; {evidence}"


def summary_presence(summary: str, details: list[dict[str, str]]) -> tuple[bool, int, int]:
    ids = sum(item["observation_id"] in summary for item in details)
    values = sum(item["instrument_last4"] in summary for item in details) + sum(
        item["captured_at"] in summary for item in details
    )
    return len(details) == 6 and ids == 6 and values == 12, ids, values


def rule(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(bool(passed)), "reason": reason[:2400]}


def evaluate(workspace: Path, baseline: Path, run_dir: Path | None) -> dict[str, Any]:
    baseline_rows = observation_rows(baseline)
    anomalies = expected_anomalies(baseline_rows)
    details = expected_details(baseline_rows, anomalies) if anomalies else []
    literature_truth = expected_literature()

    anomalies_value, anomalies_status = load_output(workspace / "output" / "observation_anomalies.json")
    literature_value, literature_status = load_output(workspace / "output" / "literature_review.json")
    details_value, details_status = load_output(workspace / "output" / "observation_details.json")
    output_dir = workspace / "output"
    output_files = {
        path.relative_to(output_dir).as_posix()
        for path in output_dir.rglob("*")
        if path.is_file() and not path.is_symlink()
    } if output_dir.is_dir() else set()
    redaction_ok, residue = redaction_and_residue(
        workspace, baseline_rows, {item["captured_at"] for item in details}
    )
    audit_ok = bool(
        len(baseline_rows) == 36
        and len(anomalies) == 6
        and exact_anomalies(anomalies_value, anomalies)
        and redaction_ok
        and output_files.issubset(EXPECTED_OUTPUTS)
    )
    literature_ok = bool(literature_truth) and exact_literature(literature_value, literature_truth)
    workflow_ok = bool(audit_ok and literature_ok)
    instrument_ok = bool(details) and exact_detail_bindings(
        details_value, details, "instrument_last4"
    )
    timestamp_ok = bool(details) and exact_detail_bindings(
        details_value, details, "captured_at"
    )
    agent_rules = [
        rule("ar_academic_workflow_exact", workflow_ok, f"anomalies={anomalies_status} baseline_rows={len(baseline_rows)} output_files={sorted(output_files)} redaction={redaction_ok} residue={sorted(residue)} literature={literature_status} expected_queries={len(literature_truth)} exact={literature_ok}"),
        rule("ar_post_compaction_instrument_bindings_exact", instrument_ok, f"details={details_status} expected_rows={len(details)} exact_instrument_bindings={instrument_ok}"),
        rule("ar_post_compaction_timestamp_bindings_exact", timestamp_ok, f"details={details_status} expected_rows={len(details)} exact_timestamp_bindings={timestamp_ok}"),
    ]

    trace_path, events, trace_summary = primary_trace(run_dir) if run_dir else (None, [], {})
    trace_ok, trace_reason = trace_integrity(events, trace_summary)
    early_ok, early_count = observation_visibility(events, baseline_rows, details)
    pages, payload_octets = private_search_pages(run_dir) if run_dir else ({}, 0)
    search_ok, visible_pages, model_page_events, _threshold_seq = search_visibility(events, pages)
    exact_private_pressure = bool(len(pages) == 36 and payload_octets >= 1_400_000)
    compacted_trace = bool(native_compactions(events))
    summarized_search, summarized_queries = compaction_search_progress(events)
    retained_tail_ok = bool(
        compacted_trace
        and visible_pages == model_page_events
        and (visible_pages >= 1 or summarized_search)
    )
    model_trace_ok = bool(search_ok or retained_tail_ok)
    pressure_ok = bool(trace_ok and early_ok and exact_private_pressure and model_trace_ok)
    boundary_ok, active, boundary_reason = effective_compaction(
        events,
        trace_ok,
        exact_private_pressure and (visible_pages >= 1 or summarized_search),
    )
    contract_ok, contract_reason = context_window_compaction_contract(
        run_dir,
        events,
        trace_summary,
        pressure_ok,
        boundary_ok,
    )
    summary_ok_raw, present_ids, present_values = summary_presence(str(active.get("summary") or ""), details)
    summary_ok = bool(boundary_ok and summary_ok_raw)
    harness_rules = [
        rule("hr_academic_search_pressure_model_visible", pressure_ok, f"early_values={early_count}/12 private_pages={len(pages)}/36 visible_pages={visible_pages}/{model_page_events} normalized_web_events payload_octets={payload_octets} compacted_trace={compacted_trace} summarized_queries={summarized_queries}/12 trace={trace_path} {trace_reason}"),
        rule("hr_context_window_compaction_contract", contract_ok, f"{contract_reason}; {boundary_reason}; {trace_reason}"),
        rule("hr_active_summary_preserved_all_details", summary_ok, f"effective_boundary={boundary_ok} summary_ids={present_ids}/6 summary_values={present_values}/12"),
    ]
    return {
        "schema_version": "harnessbench_rule_results_v1",
        "rule_results": {"harness": harness_rules, "agent": agent_rules},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    baseline = args.baseline or Path.cwd() / ".baseline_workspace"
    run_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_value) if run_value else None
    print(json.dumps(evaluate(args.workspace, baseline, run_dir), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
