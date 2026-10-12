#!/usr/bin/env python3
"""Deterministic end-to-end scorer for conditional ask-user billing."""

from __future__ import annotations

import argparse
import json
import os
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


ASK_TOOL = re.compile(
    r"(?:^|[.:/_-])(?:request_user_input|ask_user|askuserquestion|clarify|request_input|prompt_user|elicit)(?:$|[.:/_-])",
    re.IGNORECASE,
)
STRUCTURED_MARKER = "[STRUCTURED CLARIFICATION RESPONSE]"
FINAL_MARKER = "[FINALIZATION REQUEST]"
TOP_FIELDS = {"billing_month", "currency", "items", "total"}
ITEM_FIELDS = {"amount", "basis"}


class DuplicateKeyError(ValueError):
    pass


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise DuplicateKeyError(key)
        value[key] = child
    return value


def load_billing(path: Path) -> tuple[Any, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object), None
    except FileNotFoundError:
        return None, "output/quota.json is missing"
    except UnicodeError:
        return None, "output/quota.json is not valid UTF-8"
    except DuplicateKeyError as exc:
        return None, f"duplicate JSON key: {exc}"
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"output/quota.json is unreadable or invalid JSON: {exc}"


def string_leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in string_leaves(child)]
    if isinstance(value, list):
        return [text for child in value for text in string_leaves(child)]
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


def tool_name(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return ""
    return str(call.get("tool_name") or call.get("name") or "")


def tool_arguments(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return ""
    return "\n".join(string_leaves(call.get("arguments")))


def tool_result(event: dict[str, Any]) -> str:
    return "\n".join(string_leaves(event.get("tool_result")))


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
        message = raw.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            kinds = {part.get("type") for part in content if isinstance(part, dict)}
            if kinds and "text" not in kinds:
                return ""
    content = event.get("content", event.get("text", ""))
    return content if isinstance(content, str) else "\n".join(string_leaves(content))


def seq(event: dict[str, Any]) -> int:
    value = event.get("seq")
    return value if type(value) is int else -1


def money(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def result(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(bool(passed)), "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()

    case = Path(__file__).resolve().parent.parent
    scenario = load_json(case / "simulator" / "scenario.json", {})
    expected_amounts = {
        key: Decimal(value)
        for key, value in scenario.get("expected_amounts", {}).items()
        if isinstance(key, str) and isinstance(value, str)
    }
    expected_ids = set(expected_amounts)
    affected = scenario.get("affected_volumes", {})
    affected = affected if isinstance(affected, dict) else {}
    expected_total = money(scenario.get("expected_total"))
    clarification_message = str(scenario.get("clarification_message") or "")

    run_dir_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    events, summary, events_path = (
        primary_trace(Path(run_dir_value)) if run_dir_value else ([], {}, None)
    )
    calls = structured_calls(events)
    turns = user_turns(events)
    clarification_turns = [event for event in turns if STRUCTURED_MARKER in event_user_text(event)]
    finalization_turns = [event for event in turns if FINAL_MARKER in event_user_text(event)]
    clarification_delivered = len(clarification_turns) == 1
    clarification_exact = clarification_delivered and event_user_text(clarification_turns[0]) == clarification_message
    first_call_seq = min((seq(event) for event in calls), default=None)
    clarification_seq = seq(clarification_turns[0]) if clarification_delivered else None
    causally_ordered = (
        first_call_seq is not None
        and clarification_seq is not None
        and first_call_seq < clarification_seq
    )

    conversation = summary.get("conversation") if isinstance(summary.get("conversation"), dict) else {}
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
    )
    session_ids = conversation.get("native_session_ids")
    one_session = (
        isinstance(session_ids, list)
        and len(session_ids) == 1
        and isinstance(session_ids[0], str)
        and bool(session_ids[0])
        and not any(event.get("event_type") == "session_boundary" for event in events)
    )
    operational_ask = bool(calls) and clarification_exact and causally_ordered
    causal_release = (
        (not calls and not clarification_turns)
        or (bool(calls) and clarification_exact and causally_ordered)
    )

    harness_results = [
        result(
            "hr_conditional_conversation_trace_is_complete",
            lifecycle_ok and one_session,
            f"trace={events_path}; turns={len(turns)}; status={summary.get('status')!r}; trace_complete={summary.get('trace_complete')!r}; native_session_ids={session_ids!r}; session_boundaries={sum(event.get('event_type') == 'session_boundary' for event in events)}",
        ),
        result(
            "hr_structured_ask_user_channel_is_operational",
            operational_ask,
            f"structured_calls={[tool_name(event) for event in calls]!r}; clarification_exact={clarification_exact}; causally_ordered={causally_ordered}",
        ),
        result(
            "hr_private_facts_follow_the_release_policy",
            causal_release,
            f"structured_call_count={len(calls)}; clarification_turn_count={len(clarification_turns)}; clarification_exact={clarification_exact}; causally_ordered={causally_ordered}",
        ),
    ]

    output_dir = args.workspace / "output"
    output_files = (
        sorted(path.name for path in output_dir.iterdir() if path.is_file() or path.is_symlink())
        if output_dir.is_dir() and not output_dir.is_symlink()
        else []
    )
    billing_path = output_dir / "quota.json"
    billing, billing_error = load_billing(billing_path)
    billing_object = billing if isinstance(billing, dict) else {}
    items = billing_object.get("items")
    items = items if isinstance(items, dict) else {}
    contract_ok = (
        output_files == ["quota.json"]
        and billing_path.is_file()
        and not billing_path.is_symlink()
        and isinstance(billing, dict)
        and set(billing) == TOP_FIELDS
        and billing.get("billing_month") == "2026-07"
        and billing.get("currency") == "USD"
        and set(items) == expected_ids
        and all(isinstance(items.get(item_id), dict) and set(items[item_id]) == ITEM_FIELDS for item_id in expected_ids)
        and all(
            isinstance(items[item_id].get("amount"), (int, float))
            and not isinstance(items[item_id].get("amount"), bool)
            and money(items[item_id].get("amount")) is not None
            and isinstance(items[item_id].get("basis"), str)
            and bool(items[item_id]["basis"].strip())
            for item_id in expected_ids
        )
        and isinstance(billing.get("total"), (int, float))
        and not isinstance(billing.get("total"), bool)
        and money(billing.get("total")) is not None
    )

    exact_rows: dict[str, bool] = {}
    for item_id in sorted(expected_ids):
        row = items.get(item_id) if isinstance(items.get(item_id), dict) else {}
        exact_rows[item_id] = (
            money(row.get("amount")) == expected_amounts[item_id]
        )
    final_ok = (
        contract_ok
        and all(exact_rows.values())
        and money(billing.get("total")) == expected_total
    )

    agent_results = [
        result(
            "ar_final_billing_output_contract",
            contract_ok,
            f"output_files={output_files!r}; billing_error={billing_error!r}; top_fields={sorted(billing_object)!r}; item_count={len(items)}",
        ),
        result(
            "ar_all_amounts_and_total_are_exact",
            final_ok,
            f"clarification_delivered={clarification_delivered}; exact_rows={sum(exact_rows.values())}/{len(expected_ids)}; expected_total={expected_total}; observed_total={billing_object.get('total')!r}",
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
