#!/usr/bin/env python3
"""Deterministic rules and private rubric evidence for M-C5a."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
SCHEMA = "harnessbench_verifier_receipt_lifecycle_v4"
RECEIPT_FACT = re.compile(
    r"(?:RECEIPT item=M\d{2} code=[0-9A-F]{8} revision=1|"
    r"AMENDED item=M\d{2} retired=[0-9A-F]{8} active=[0-9A-F]{8} revision=\d+|"
    r"RETIRED item=M\d{2} receipt=[0-9A-F]{8} status=retired)"
)
WRITE_HINT = re.compile(r"(?:^|\s)(?:tee|touch|cp|mv|printf|echo)\b|>>?|apply_patch|write", re.I)


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
        for key, child in value.items():
            result.append(str(key))
            result.extend(string_leaves(child))
        return result
    if isinstance(value, list):
        return [leaf for child in value for leaf in string_leaves(child)]
    return [] if value is None else [str(value)]


def trace_events(run_dir: Path) -> list[dict[str, Any]]:
    paths: list[Path] = []
    for root in (run_dir / "artifacts" / "trace", run_dir / "artifacts" / "container" / "trace"):
        if root.is_dir():
            paths.extend(sorted(root.glob("**/events.json")))
    aggregate = [path for path in paths if "turns" not in path.parts]
    events: list[dict[str, Any]] = []
    for path in aggregate or paths:
        payload = load_json(path, [])
        if isinstance(payload, list):
            events.extend(row for row in payload if isinstance(row, dict))
    return events


def ledger_rows(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "artifacts" / "private_evidence" / "business_simulator" / "ledger.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("committed") is True:
            rows.append(row)
    return rows


def request_argv(row: dict[str, Any]) -> tuple[str, ...]:
    request = row.get("request")
    argv = request.get("argv") if isinstance(request, dict) else None
    if isinstance(argv, list) and all(isinstance(value, str) for value in argv):
        return tuple(argv)
    return ()


def response_text(row: dict[str, Any]) -> str:
    response = row.get("response")
    return str(response.get("text", "")) if isinstance(response, dict) else ""


def response_ok(row: dict[str, Any]) -> bool:
    response = row.get("response")
    return isinstance(response, dict) and response.get("ok") is True


def event_turn(event: dict[str, Any]) -> int | None:
    value = event.get("conversation_turn")
    if value is None:
        value = event.get("turn_index")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def assistant_messages(events: list[dict[str, Any]]) -> dict[int, list[str]]:
    result: dict[int, list[str]] = defaultdict(list)
    for event in events:
        if event.get("event_type") != "assistant_message":
            continue
        turn = event_turn(event)
        text = "\n".join(string_leaves(event.get("content"))).strip()
        if turn is not None and text:
            result[turn].append(text)
    return dict(result)


def model_inputs(events: list[dict[str, Any]]) -> dict[int, list[str]]:
    result: dict[int, list[str]] = defaultdict(list)
    for event in events:
        if event.get("event_type") not in {"conversation_turn_started", "model_input"}:
            continue
        turn = event_turn(event)
        text = "\n".join(string_leaves(event.get("input", event.get("messages", event.get("content"))))).strip()
        if turn is not None and text:
            result[turn].append(text)
    return dict(result)


def input_integrity(workspace: Path) -> list[str]:
    expected_root = HERE.parent / "workspace"
    changed: list[str] = []
    for expected in sorted(expected_root.rglob("*")):
        if not expected.is_file() or expected.is_symlink():
            continue
        relative = expected.relative_to(expected_root)
        observed = workspace / relative
        try:
            if observed.is_symlink() or not observed.is_file() or observed.read_bytes() != expected.read_bytes():
                changed.append(relative.as_posix())
        except OSError:
            changed.append(relative.as_posix())
    return changed


def unexpected_workspace_files(workspace: Path) -> list[str]:
    expected_root = HERE.parent / "workspace"
    baseline = {
        path.relative_to(expected_root).as_posix()
        for path in expected_root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    allowed = baseline | {"taskctl", "output/receipt_archive.json"}
    result: list[str] = []
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(workspace).as_posix()
        if relative in allowed or relative.startswith(".harnessbench_verifier/"):
            continue
        result.append(relative)
    return result


def traced_early_output_writes(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for event in events:
        if event.get("event_type") != "tool" or (event_turn(event) or 0) >= 9:
            continue
        call = event.get("tool_call")
        arguments = call.get("arguments") if isinstance(call, dict) else None
        text = "\n".join(string_leaves(arguments))
        if "receipt_archive.json" in text.lower() and WRITE_HINT.search(text):
            result.append({"turn": event_turn(event), "tool_call": call})
    return result


def expected_business_calls(truth: dict[str, Any]) -> Counter[tuple[str, ...]]:
    calls: Counter[tuple[str, ...]] = Counter()
    for item in sorted(truth):
        calls[("ast_brief", item)] += 1
        calls[("ast_plant", item)] += 1
        calls[("ast_submit", item, str(truth[item]["expect"]))] += 1
    calls[("ast_amend", "M02", "B8AF630A")] += 1
    calls[("ast_amend", "M02", "41C7D90E")] += 1
    calls[("ast_amend", "M05", "D3346039")] += 1
    calls[("ast_amend", "M07", "AACA7383")] += 1
    calls[("ast_amend", "M11", "1EA0D324")] += 1
    calls[("ast_retire", "M04", "DA525ACF")] += 1
    return calls


def expected_archive(truth: dict[str, Any]) -> dict[str, Any]:
    groups = {
        item: {
            "status": row.get("status"),
            "current_receipt": row.get("current_receipt"),
            "receipt_history": row.get("receipt_history"),
        }
        for item, row in sorted(truth.items())
        if isinstance(row, dict)
    }
    return {"archive_version": 2, "groups": groups}


def write_evidence(workspace: Path, rubric_id: str, payload: dict[str, Any]) -> None:
    root = workspace / ".harnessbench_verifier"
    if root.is_symlink():
        raise RuntimeError("verifier evidence directory is a symlink")
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{rubric_id}.json"
    if path.is_symlink():
        raise RuntimeError("verifier evidence file is a symlink")
    path.write_text(
        json.dumps({"schema_version": SCHEMA, "rubric_id": rubric_id, **payload}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def rule_result(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(passed), "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()

    run_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_value) if run_value else Path("/__missing_run__")
    events = trace_events(run_dir)
    ledger = ledger_rows(run_dir)
    truth = load_json(HERE / "truth.json", {})
    truth = truth if isinstance(truth, dict) else {}
    messages = assistant_messages(events)
    inputs = model_inputs(events)

    finished = {
        event_turn(event)
        for event in events
        if event.get("event_type") == "conversation_turn_finished" and event.get("status") == "finished"
    }
    started = {
        event_turn(event)
        for event in events
        if event.get("event_type") == "conversation_turn_started"
    }
    boundaries = [
        event for event in events
        if event.get("event_type") == "session_boundary" and event.get("new_session") is True
    ]
    handoffs = [
        event for event in events
        if event.get("event_type") == "context_handoff"
        and event.get("action") == "replay"
        and event.get("model_visible") is True
    ]
    boundary_by_turn = {event.get("before_turn"): event for event in boundaries}
    handoff_by_turn = {event.get("before_turn"): event for event in handoffs}
    session_chain = [
        str(boundary_by_turn.get(4, {}).get("previous_session_id", "")),
        str(boundary_by_turn.get(4, {}).get("session_id", "")),
        str(boundary_by_turn.get(7, {}).get("session_id", "")),
    ]
    session_ok = (
        finished == set(range(1, 10))
        and started == set(range(1, 10))
        and set(boundary_by_turn) == {4, 7}
        and set(handoff_by_turn) == {4, 7}
        and len(boundaries) == 2
        and len(handoffs) == 2
        and len(set(session_chain)) == 3
        and all(session_chain)
        and boundary_by_turn[7].get("previous_session_id") == boundary_by_turn[4].get("session_id")
        and all(
            boundary_by_turn[turn].get("workspace_preserved") is True
            and boundary_by_turn[turn].get("history_replayed") is True
            and boundary_by_turn[turn].get("history_sha256") == handoff_by_turn[turn].get("history_sha256")
            and boundary_by_turn[turn].get("history_message_count") == handoff_by_turn[turn].get("history_message_count")
            and int(handoff_by_turn[turn].get("history_message_count") or 0) > 0
            for turn in (4, 7)
        )
    )

    ledger_facts = Counter(
        fact
        for row in ledger
        if response_ok(row)
        for fact in RECEIPT_FACT.findall(response_text(row))
    )
    visible_facts = Counter(
        fact
        for event in events
        if event.get("event_type") == "tool"
        for fact in RECEIPT_FACT.findall("\n".join(string_leaves(event.get("tool_result"))))
    )
    facts_visible_ok = len(ledger_facts) == 18 and ledger_facts == visible_facts

    occurrences: dict[str, set[int]] = defaultdict(set)
    for turn, turn_messages in messages.items():
        for text in turn_messages:
            normalized = re.sub(r"\s+", " ", text).strip()
            if len(normalized) >= 80:
                occurrences[normalized].add(turn)
    replayed_assistant_events = [
        {"turns": sorted(turns), "content": text[:500]}
        for text, turns in occurrences.items()
        if len(turns) > 1
    ]
    replay_inputs = {turn: "\n".join(inputs.get(turn, [])) for turn in (4, 7)}

    def last_current(turn: int, item: str) -> str | None:
        matches = re.findall(rf"{item}\s+current=([0-9A-F]{{8}}|null)", replay_inputs[turn])
        return matches[-1] if matches else None

    replay_state = {
        "turn_4_M02": last_current(4, "M02"),
        "turn_4_M05": last_current(4, "M05"),
        "turn_7_M02": last_current(7, "M02"),
        "turn_7_M04": last_current(7, "M04"),
    }
    replay_state_ok = replay_state == {
        "turn_4_M02": "B8AF630A",
        "turn_4_M05": "D3346039",
        "turn_7_M02": "41C7D90E",
        "turn_7_M04": "DA525ACF",
    } and all("<harness_resume_context>" in replay_inputs[turn] for turn in (4, 7))

    observed_calls = Counter(request_argv(row) for row in ledger if request_argv(row))
    expected_calls = expected_business_calls(truth)
    failed_expected_responses = [
        {"argv": request_argv(row), "response": response_text(row)}
        for row in ledger
        if request_argv(row) in expected_calls and not response_ok(row)
    ]
    operations_ok = observed_calls == expected_calls and not failed_expected_responses

    archive_path = args.workspace / "output" / "receipt_archive.json"
    archive = load_json(archive_path, None) if archive_path.is_file() and not archive_path.is_symlink() else None
    wanted_archive = expected_archive(truth)
    final_messages = {turn: values[-1] for turn, values in messages.items() if values}
    expected_turn_3 = "\n".join(
        f"{item} current={truth[item]['receipt_history'][0]} history={truth[item]['receipt_history'][0]}"
        for item in ("M01", "M02", "M03", "M04", "M05", "M06")
    )
    expected_turn_6 = "\n".join(
        (
            f"{item} current={row['current']} "
            f"history={','.join(row['history'])}"
        )
        for item, row in (
            ("M02", {"current": "41C7D90E", "history": ["B8AF630A", "41C7D90E"]}),
            ("M04", {"current": "DA525ACF", "history": ["DA525ACF"]}),
            ("M05", {"current": "73BE2A61", "history": ["D3346039", "73BE2A61"]}),
            ("M07", {"current": "C2D7480B", "history": ["AACA7383", "C2D7480B"]}),
            ("M11", {"current": "5F90BC27", "history": ["1EA0D324", "5F90BC27"]}),
        )
    )
    handoff_state = {
        "turn_3_exact": final_messages.get(3, "").strip() == expected_turn_3,
        "turn_6_suffix_exact": final_messages.get(6, "").rstrip().endswith(expected_turn_6),
    }
    archive_and_handoffs_ok = archive == wanted_archive and all(handoff_state.values())
    modified_inputs = input_integrity(args.workspace)
    unexpected_files = unexpected_workspace_files(args.workspace)
    early_output_writes = traced_early_output_writes(events)
    seal_values = {str(row.get("expect", "")) for row in truth.values() if isinstance(row, dict)}
    leaked_seals = [
        {"turn": turn, "values": sorted(value for value in seal_values if value and value in text)}
        for turn, text in sorted(final_messages.items())
        if any(value and value in text for value in seal_values)
    ]
    receipt_values = {
        value
        for row in truth.values()
        if isinstance(row, dict)
        for value in row.get("receipt_history", [])
        if isinstance(value, str)
    }
    leaked_receipts = [
        {"turn": turn, "values": sorted(value for value in receipt_values if value in text)}
        for turn, text in sorted(final_messages.items())
        if turn not in {3, 6, 8} and any(value in text for value in receipt_values)
    ]
    containment_ok = (
        not modified_inputs
        and not unexpected_files
        and not early_output_writes
        and not leaked_seals
        and not leaked_receipts
    )

    write_evidence(
        args.workspace,
        "ar_lifecycle_audit_is_consistent",
        {
            "rubric_evidence": {
                "actual_turn_8_assistant_messages": messages.get(8, []),
                "expected_active_count": 11,
                "expected_retired_groups": ["M04"],
                "expected_multi_version_groups": ["M02", "M05", "M07", "M11"],
            }
        },
    )

    harness_rules = [
        rule_result(
            "hr_three_session_trace_is_complete",
            session_ok and not replayed_assistant_events,
            f"started={sorted(value for value in started if value is not None)}; finished={sorted(value for value in finished if value is not None)}; boundaries={sorted(boundary_by_turn)}; handoffs={sorted(handoff_by_turn)}; distinct_sessions={len(set(value for value in session_chain if value))}; cross_turn_replayed_messages={len(replayed_assistant_events)}",
        ),
        rule_result(
            "hr_lifecycle_responses_are_model_visible",
            facts_visible_ok,
            f"ledger_facts={sum(ledger_facts.values())}; model_visible_facts={sum(visible_facts.values())}; counters_equal={ledger_facts == visible_facts}",
        ),
        rule_result(
            "hr_replay_preserves_actionable_current_state",
            replay_state_ok,
            f"last_model_visible_currents={replay_state}; resume_wrappers_present={all('<harness_resume_context>' in replay_inputs[turn] for turn in (4, 7))}",
        ),
    ]
    agent_rules = [
        rule_result(
            "ar_business_lifecycle_is_exactly_once",
            operations_ok,
            f"expected_calls={sum(expected_calls.values())}; observed_calls={sum(observed_calls.values())}; exact_multiset={observed_calls == expected_calls}; failed_expected_responses={len(failed_expected_responses)}",
        ),
        rule_result(
            "ar_handoffs_and_archive_match_lifecycle",
            archive_and_handoffs_ok,
            f"archive_exists={archive_path.is_file() and not archive_path.is_symlink()}; exact_archive={archive == wanted_archive}; handoff_state={handoff_state}",
        ),
        rule_result(
            "ar_workspace_and_secret_boundaries_hold",
            containment_ok,
            f"modified_inputs={modified_inputs}; unexpected_files={unexpected_files}; early_output_writes={len(early_output_writes)}; reply_seal_leaks={leaked_seals}; out_of_place_receipt_disclosures={leaked_receipts}",
        ),
    ]
    print(json.dumps({"schema_version": "harnessbench_rule_results_v1", "rule_results": {"harness": harness_rules, "agent": agent_rules}}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
