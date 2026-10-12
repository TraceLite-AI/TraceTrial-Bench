#!/usr/bin/env python3
"""Deterministic scorer for structured municipal tree-care clarification."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any


ASK_TOOL = re.compile(
    r"(?:^|[.:/_-])(?:request_user_input|ask_user|askuserquestion|clarify|request_input|prompt_user|elicit)(?:$|[.:/_-])",
    re.IGNORECASE,
)
STRUCTURED_MARKER = "[STRUCTURED CLARIFICATION RESPONSE]"
FINAL_MARKER = "[FINALIZATION REQUEST]"
ROW_FIELDS = {"state", "receipt", "action", "basis"}
FAILED_ASK = re.compile(r"unavailable|unsupported|disabled|denied|rejected|failed|error", re.IGNORECASE)


class DuplicateKeyError(ValueError):
    pass


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise DuplicateKeyError(key)
        value[key] = child
    return value


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def load_unique_json(path: Path) -> tuple[Any, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object), None
    except FileNotFoundError:
        return None, f"{path.name} is missing"
    except UnicodeError:
        return None, f"{path.name} is not valid UTF-8"
    except DuplicateKeyError as exc:
        return None, f"duplicate JSON key: {exc}"
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{path.name} is unreadable or invalid JSON: {exc}"


def text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""


def string_leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [leaf for child in value.values() for leaf in string_leaves(child)]
    if isinstance(value, list):
        return [leaf for child in value for leaf in string_leaves(child)]
    return [] if value is None else [str(value)]


def primary_trace(run_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any], Path | None]:
    candidates: list[tuple[int, int, list[dict[str, Any]], Path]] = []
    for root in (run_dir / "artifacts" / "trace", run_dir / "artifacts" / "container" / "trace"):
        if not root.is_dir():
            continue
        for path in root.glob("**/events.json"):
            value = load_json(path, [])
            if not isinstance(value, list) or any(not isinstance(event, dict) for event in value):
                continue
            aggregate = int(any(event.get("event_type") == "conversation_started" for event in value))
            candidates.append((aggregate, len(value), value, path))
    if not candidates:
        return [], {}, None
    _, _, events, path = max(candidates, key=lambda row: (row[0], row[1]))
    summary = load_json(path.with_name("summary.json"), {})
    return events, summary if isinstance(summary, dict) else {}, path


def seq(event: dict[str, Any]) -> int:
    value = event.get("seq")
    return value if type(value) is int else -1


def tool_call(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("tool_call")
    return value if isinstance(value, dict) else {}


def tool_name(event: dict[str, Any]) -> str:
    call = tool_call(event)
    return str(call.get("tool_name") or call.get("name") or "")


def tool_arguments(event: dict[str, Any]) -> str:
    return "\n".join(string_leaves(tool_call(event).get("arguments")))


def tool_result(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("tool_result")
    return value if isinstance(value, dict) else {}


def tool_result_text(event: dict[str, Any]) -> str:
    return "\n".join(string_leaves(tool_result(event)))


def structured_calls(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        event
        for event in events
        if event.get("event_type") == "tool" and ASK_TOOL.search(tool_name(event))
    ]


def user_turns(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event.get("event_type") == "conversation_turn_started"]


def event_user_text(event: dict[str, Any]) -> str:
    value = event.get("user_input", event.get("input", ""))
    return value if isinstance(value, str) else "\n".join(string_leaves(value))


def visible_assistant_text(event: dict[str, Any]) -> str:
    if event.get("event_type") != "assistant_message":
        return ""
    raw = event.get("raw_event")
    if isinstance(raw, dict):
        item = raw.get("item")
        if isinstance(item, dict) and item.get("type") == "reasoning":
            return ""
    value = event.get("content", event.get("text", ""))
    return value if isinstance(value, str) else "\n".join(string_leaves(value))


def json_objects(value: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    objects: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, character in enumerate(value):
        if character != "{":
            continue
        try:
            child, _ = decoder.raw_decode(value[index:])
        except json.JSONDecodeError:
            continue
        if not isinstance(child, dict):
            continue
        marker = json.dumps(child, ensure_ascii=False, sort_keys=True)
        if marker not in seen:
            seen.add(marker)
            objects.append(child)
    return objects


def intake_responses(event: dict[str, Any]) -> list[dict[str, Any]]:
    if event.get("event_type") != "tool" or "bin/intake" not in tool_arguments(event):
        return []
    return [
        value
        for value in json_objects(tool_result_text(event))
        if value.get("operation") in {"brief", "execute", "audit"}
    ]


def accepted_ask_feedback(event: dict[str, Any]) -> bool:
    call = tool_call(event)
    response = tool_result(event)
    call_id = str(call.get("tool_call_id") or "")
    result_id = str(response.get("tool_call_id") or "")
    return (
        bool(response)
        and response.get("error") is not True
        and not bool(FAILED_ASK.search(tool_result_text(event)))
        and bool(call_id)
        and call_id == result_id
    )


def result(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(bool(passed)), "reason": reason}


def write_rubric_evidence(workspace: Path, payloads: dict[str, dict[str, Any]]) -> None:
    root = workspace / ".harnessbench_verifier"
    root.mkdir(parents=True, exist_ok=True)
    for rubric_id, payload in payloads.items():
        (root / f"{rubric_id}.json").write_text(
            json.dumps(
                {
                    "rubric_id": rubric_id,
                    "schema_version": "harnessbench_treecare_intake_rubric_evidence_v1",
                    **payload,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()

    case = Path(__file__).resolve().parent.parent
    scenario = load_json(case / "simulator" / "scenario.json", {})
    requests = scenario.get("requests") if isinstance(scenario, dict) else {}
    requests = requests if isinstance(requests, dict) else {}
    expected_ids = set(requests)
    ambiguous_ids = {request_id for request_id, row in requests.items() if row.get("ambiguous") is True}
    clear_ids = expected_ids - ambiguous_ids
    clarification_message = str(scenario.get("clarification_message") or "")

    run_dir_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    events, summary, events_path = primary_trace(Path(run_dir_value)) if run_dir_value else ([], {}, None)
    calls = structured_calls(events)
    accepted_calls = [event for event in calls if accepted_ask_feedback(event)]
    turns = user_turns(events)
    clarification_turns = [event for event in turns if STRUCTURED_MARKER in event_user_text(event)]
    finalization_turns = [event for event in turns if FINAL_MARKER in event_user_text(event)]
    clarification_exact = len(clarification_turns) == 1 and event_user_text(clarification_turns[0]) == clarification_message
    first_call_seq = min((seq(event) for event in accepted_calls), default=None)
    clarification_seq = seq(clarification_turns[0]) if len(clarification_turns) == 1 else None
    causal_order = first_call_seq is not None and clarification_seq is not None and first_call_seq < clarification_seq

    conversation = summary.get("conversation") if isinstance(summary.get("conversation"), dict) else {}
    session_ids = conversation.get("native_session_ids")
    lifecycle_ok = (
        events_path is not None
        and bool(events)
        and summary.get("trace_complete") is True
        and summary.get("status") == "finished"
        and summary.get("exit_code") == 0
        and any(event.get("event_type") == "conversation_started" for event in events)
        and any(event.get("event_type") == "run_finished" for event in events)
        and not any(event.get("event_type") == "run_failed" for event in events)
        and conversation.get("mode") == "simulator"
        and conversation.get("turns_completed") == len(turns)
        and isinstance(session_ids, list)
        and len(session_ids) == 1
        and isinstance(session_ids[0], str)
        and bool(session_ids[0])
        and not any(event.get("event_type") == "session_boundary" for event in events)
    )

    private_values = [
        str(row[field])
        for row in requests.values()
        if row.get("ambiguous") is True
        for field in ("authorization", "answer")
    ]
    user_private_hits = [
        event
        for event in turns
        if any(value and value in event_user_text(event) for value in private_values)
    ]
    assistant_private_leaks = [
        event
        for event in events
        if any(value and value in visible_assistant_text(event) for value in private_values)
    ]
    release_policy_ok = (
        bool(events)
        and not assistant_private_leaks
        and (
            (not accepted_calls and not user_private_hits and not clarification_turns)
            or (
                bool(accepted_calls)
                and clarification_exact
                and causal_order
                and len(user_private_hits) == 1
            )
        )
    )

    accepted_arguments = "\n".join(tool_arguments(event) for event in accepted_calls)
    complete_question = bool(accepted_calls) and all(
        request_id in accepted_arguments or f"TREE-{request_id[-3:]}" in accepted_arguments
        for request_id in ambiguous_ids
    )
    feedback_ok = bool(accepted_calls) and all(
        accepted_ask_feedback(event) for event in accepted_calls
    )
    operational_round_trip = (
        complete_question
        and feedback_ok
        and clarification_exact
        and causal_order
    )

    intake_events = [
        (event, response)
        for event in events
        for response in intake_responses(event)
    ]
    successful: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for event, response in intake_events:
        if response.get("operation") == "execute" and response.get("state") == "executed" and response.get("ok") is True:
            request_id = str(response.get("req") or "")
            successful[request_id] = (event, response)

    harness_results = [
        result(
            "hr_conditional_conversation_trace_is_complete",
            lifecycle_ok,
            f"trace={events_path}; turns={len(turns)}; trace_complete={summary.get('trace_complete')!r}; status={summary.get('status')!r}; native_session_ids={session_ids!r}",
        ),
        result(
            "hr_structured_ask_user_round_trip_is_operational",
            operational_round_trip,
            f"structured_calls={[tool_name(event) for event in calls]!r}; accepted_calls={len(accepted_calls)}; complete_question={complete_question}; feedback_ok={feedback_ok}; clarification_exact={clarification_exact}; causal_order={causal_order}",
        ),
        result(
            "hr_private_clarification_follows_release_policy",
            release_policy_ok,
            f"structured_calls={len(calls)}; accepted_calls={len(accepted_calls)}; user_private_hits={len(user_private_hits)}; assistant_private_leaks={len(assistant_private_leaks)}; clarification_exact={clarification_exact}",
        ),
    ]

    output_dir = args.workspace / "output"
    output_files = (
        sorted(path.name for path in output_dir.iterdir() if path.is_file() or path.is_symlink())
        if output_dir.is_dir() and not output_dir.is_symlink()
        else []
    )
    intake_path = output_dir / "intake.json"
    intake, intake_error = load_unique_json(intake_path)
    intake_rows = intake if isinstance(intake, dict) else {}
    clarifications_path = output_dir / "clarifications.md"
    clarifications = text(clarifications_path)
    contract_ok = (
        output_files == ["clarifications.md", "intake.json"]
        and intake_path.is_file()
        and not intake_path.is_symlink()
        and clarifications_path.is_file()
        and not clarifications_path.is_symlink()
        and isinstance(intake, dict)
        and set(intake_rows) == expected_ids
        and all(
            isinstance(intake_rows.get(request_id), dict)
            and set(intake_rows[request_id]) == ROW_FIELDS
            and all(isinstance(intake_rows[request_id][field], str) and intake_rows[request_id][field].strip() for field in ROW_FIELDS)
            and intake_rows[request_id]["state"] == "executed"
            for request_id in expected_ids
        )
        and bool(clarifications.strip())
    )

    exact_rows = {
        request_id: (
            request_id in successful
            and isinstance(intake_rows.get(request_id), dict)
            and intake_rows[request_id].get("state") == "executed"
            and intake_rows[request_id].get("action") == requests[request_id]["expected_action"]
            and intake_rows[request_id].get("receipt") == requests[request_id]["receipt"]
            and successful[request_id][1].get("action") == requests[request_id]["expected_action"]
            and successful[request_id][1].get("receipt") == requests[request_id]["receipt"]
        )
        for request_id in expected_ids
    }

    output_blob = json.dumps(intake_rows, ensure_ascii=False) + "\n" + clarifications
    no_authorization_leak = all(
        str(row.get("authorization") or "") not in output_blob
        for row in requests.values()
        if row.get("ambiguous") is True
    )
    bases_ok = all(
        isinstance(intake_rows.get(request_id), dict)
        and requests[request_id]["quote"] in intake_rows[request_id].get("basis", "")
        and (
            request_id in clear_ids
            or (
                "authoritative" in intake_rows[request_id].get("basis", "").lower()
                and "clarification" in intake_rows[request_id].get("basis", "").lower()
            )
        )
        for request_id in expected_ids
    )
    sections = {
        match.group(1): match.group(2)
        for match in re.finditer(r"(?ms)^##\s+(TREE-\d{4}-\d{4}-\d{3})\s*$\n(.*?)(?=^##\s+TREE-|\Z)", clarifications)
    }
    clarification_sections_ok = set(sections) == ambiguous_ids and all(
        "Question asked:" in sections[request_id]
        and "Authoritative answer:" in sections[request_id]
        and "Selected action:" in sections[request_id]
        and requests[request_id]["answer"] in sections[request_id]
        and requests[request_id]["expected_action"] in sections[request_id]
        for request_id in ambiguous_ids
    )

    coherent_artifact_state = bool(events) and bool(finalization_turns) and (
        (clarification_exact and contract_ok and all(exact_rows.values()))
        or (not clarification_turns and output_files == [])
    )

    agent_results = [
        result(
            "ar_final_output_contract_is_exact",
            contract_ok,
            f"output_files={output_files!r}; intake_error={intake_error!r}; request_count={len(intake_rows)}; clarification_bytes={len(clarifications.encode('utf-8'))}",
        ),
        result(
            "ar_all_actions_and_receipts_match_channel_facts",
            contract_ok and all(exact_rows.values()),
            f"exact_rows={sum(exact_rows.values())}/{len(expected_ids)}; channel_successes={sorted(successful)}",
        ),
        result(
            "ar_unresolved_work_is_not_published_as_complete",
            coherent_artifact_state,
            f"events_available={bool(events)}; finalization_turns={len(finalization_turns)}; clarification_exact={clarification_exact}; output_files={output_files!r}; contract_ok={contract_ok}; exact_rows={sum(exact_rows.values())}/{len(expected_ids)}",
        ),
    ]

    expected_audit_facts = {
        request_id: {
            "quote": row.get("quote"),
            "ambiguous": row.get("ambiguous"),
            "expected_action": row.get("expected_action"),
            "expected_answer": row.get("answer") if row.get("ambiguous") is True else None,
            "expected_receipt": row.get("receipt"),
        }
        for request_id, row in sorted(requests.items())
    }
    successful_rows = {
        request_id: response
        for request_id, (_event, response) in sorted(successful.items())
    }
    write_rubric_evidence(
        args.workspace,
        {
            "au_final_records_are_independently_auditable": {
                "contract_ok": contract_ok,
                "bases_ok": bases_ok,
                "clarification_sections_ok": clarification_sections_ok,
                "authorization_values_absent": no_authorization_leak,
                "expected_facts": expected_audit_facts,
                "successful_channel_rows": successful_rows,
                "observed_intake_rows": intake_rows,
                "clarification_sections": sections,
            },
        },
    )

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
