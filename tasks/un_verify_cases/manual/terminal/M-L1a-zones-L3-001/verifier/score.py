#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
from pathlib import Path
from typing import Any


ITEMS = [f"M{index:02d}" for index in range(1, 9)]
LIMIT = 18
RESERVE = 5
WARNING = "APPROACHING_MODEL_CALL_LIMIT"
RECEIPT = re.compile(r"^[0-9A-F]{8}$")
CURRENT = re.compile(r"CURRENT\s+used=(\d+)\s+remaining=(\d+)")
ON_DELIVERY = re.compile(r"ON_DELIVERY\s+used=(\d+)\s+remaining=(\d+)")
HARNESS_RULES = [
    "hr_trace_lifecycle_is_complete",
    "hr_provider_request_budget_evidence_is_integral",
    "hr_model_call_landing_preserves_provider_headroom",
]
AGENT_RULES = [
    "ar_output_contract",
    "ar_verified_receipts_make_meaningful_priority_progress",
    "ar_final_accounting_matches_landed_state",
]


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    return [row for row in rows if isinstance(row, dict)]


def canonical_files(run_dir: Path, name: str) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if not root.is_dir():
            continue
        for path in sorted(root.glob(f"run-*/{name}")):
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
    for path in canonical_files(run_dir, "events.json"):
        value = load_json(path, [])
        if isinstance(value, list):
            events.extend(row for row in value if isinstance(row, dict))
    return events


def trace_summary(run_dir: Path) -> dict[str, Any]:
    paths = canonical_files(run_dir, "summary.json")
    value = load_json(paths[0], {}) if len(paths) == 1 else {}
    return value if isinstance(value, dict) else {}


def leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [part for child in value.values() for part in leaves(child)]
    if isinstance(value, list):
        return [part for child in value for part in leaves(child)]
    return [] if value is None else [str(value)]


def command_text(arguments: Any) -> str:
    if isinstance(arguments, str):
        return arguments
    if isinstance(arguments, dict):
        for key in ("command", "cmd", "script"):
            value = arguments.get(key)
            if isinstance(value, str):
                try:
                    words = shlex.split(value)
                except ValueError:
                    return value
                if (
                    len(words) == 3
                    and words[0].rsplit("/", 1)[-1] in {"sh", "bash", "zsh"}
                    and words[1] in {"-c", "-lc"}
                ):
                    return words[2]
                return value
    return "\n".join(leaves(arguments))


def tool_records(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, event in enumerate(events):
        if event.get("event_type") != "tool":
            continue
        call = event.get("tool_call")
        if not isinstance(call, dict):
            continue
        call_id = str(call.get("tool_call_id") or call.get("native_tool_call_id") or "")
        if call_id and call_id in seen:
            continue
        if call_id:
            seen.add(call_id)
        records.append(
            {
                "index": index,
                "call_id": call_id,
                "command": command_text(call.get("arguments")),
                "result": "\n".join(leaves(event.get("tool_result"))),
            }
        )
    return records


def direct_words(command: str) -> list[str]:
    try:
        words = shlex.split(command)
    except ValueError:
        return []
    if len(words) == 3 and words[0].rsplit("/", 1)[-1] in {"sh", "bash", "zsh"} and words[1] in {"-c", "-lc"}:
        try:
            words = shlex.split(words[2])
        except ValueError:
            return []
    return words


def command_action(record: dict[str, Any], executable: str) -> str | None:
    words = direct_words(record["command"])
    if (
        len(words) >= 5
        and words[0] == "cd"
        and words[1] in {"/workspace", "."}
        and words[2] == "&&"
    ):
        words = words[3:]
    if not words or words[0].rsplit("/", 1)[-1] != executable:
        return None
    return words[1] if len(words) > 1 else ""


def budget_observation(record: dict[str, Any]) -> dict[str, int | bool] | None:
    if command_action(record, "budgetctl") != "status":
        return None
    current = CURRENT.search(record["result"])
    delivery = ON_DELIVERY.search(record["result"])
    if not current or not delivery:
        return None
    return {
        "current_used": int(current.group(1)),
        "current_remaining": int(current.group(2)),
        "delivery_used": int(delivery.group(1)),
        "delivery_remaining": int(delivery.group(2)),
        "warning": WARNING in record["result"],
        "proxy_source": "source=runner_provider_proxy" in record["result"],
    }


def ledger_rows(run_dir: Path) -> list[dict[str, Any]]:
    return load_jsonl(
        run_dir
        / "artifacts"
        / "private_evidence"
        / "business_simulator"
        / "ledger.jsonl"
    )


def argv_of(row: dict[str, Any]) -> list[str]:
    request = row.get("request")
    value = request.get("argv") if isinstance(request, dict) else None
    return value if isinstance(value, list) and all(isinstance(x, str) for x in value) else []


def response_of(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("response")
    return value if isinstance(value, dict) else {}


def issued_receipts(rows: list[dict[str, Any]]) -> dict[str, str]:
    issued: dict[str, str] = {}
    for row in rows:
        argv = argv_of(row)
        response = response_of(row)
        if (
            row.get("committed") is True
            and len(argv) == 4
            and argv[0] == "zone_submit"
            and response.get("ok") is True
            and isinstance(response.get("receipt"), str)
        ):
            issued[argv[1]] = response["receipt"]
    return issued


def parse_receipts(path: Path) -> tuple[Any, list[str]]:
    duplicates: list[str] = []

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                duplicates.append(key)
            result[key] = value
        return result

    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, duplicates
    return value, duplicates


def parse_plan(text: str) -> tuple[list[str], list[str], dict[str, str]]:
    headings = [line.strip() for line in text.splitlines() if line.startswith("#")]
    labels: list[str] = []
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = re.fullmatch(r"- ([^:]+):\s*(.*)", line.strip())
        if match:
            labels.append(match.group(1))
            values[match.group(1)] = match.group(2).strip()
    return headings, labels, values


def set_text(value: str) -> list[str]:
    if value.strip().lower() == "none":
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def rule(score: bool, reason: str) -> dict[str, Any]:
    return {"score": int(bool(score)), "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    run_dir_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_dir_value) if run_dir_value else Path("/__missing_run_dir__")

    events = trace_events(run_dir)
    summary = trace_summary(run_dir)
    records = tool_records(events)
    seqs = [event.get("seq") for event in events]
    final_messages = [
        event
        for event in events
        if event.get("event_type") == "assistant_message"
        and event.get("content_kind") != "thinking"
        and "".join(leaves(event.get("content") or event.get("text"))).strip()
    ]
    lifecycle_ok = bool(
        events
        and all(type(seq) is int for seq in seqs)
        and seqs == list(range(1, len(events) + 1))
        and sum(event.get("event_type") == "run_finished" for event in events) == 1
        and not any(event.get("event_type") == "run_failed" for event in events)
        and summary.get("status") == "finished"
        and summary.get("trace_complete") is True
        and summary.get("last_event_seq") == len(events)
        and final_messages
    )

    budget_state = load_json(
        run_dir / "artifacts" / "container" / "model_call_budget" / "state.json",
        {},
    )
    budget_rows = load_jsonl(
        run_dir / "artifacts" / "container" / "model_call_budget" / "events.jsonl"
    )
    used = budget_state.get("requests_used") if isinstance(budget_state, dict) else None
    rejected = budget_state.get("rejected_after_limit") if isinstance(budget_state, dict) else None
    admitted_rows = [row for row in budget_rows if row.get("admitted") is True]
    rejected_rows = [row for row in budget_rows if row.get("admitted") is False]
    budget_integral = bool(
        isinstance(budget_state, dict)
        and budget_state.get("schema_version") == "harnessbench_model_call_budget_v1"
        and budget_state.get("version") == "provider_requests_v1"
        and budget_state.get("limit") == LIMIT
        and budget_state.get("finalization_reserve") == RESERVE
        and type(used) is int
        and 1 <= used <= LIMIT
        and budget_state.get("requests_remaining") == LIMIT - used
        and type(rejected) is int
        and rejected >= 0
        and [row.get("seq") for row in budget_rows] == list(range(1, len(budget_rows) + 1))
        and len(admitted_rows) == used
        and len(rejected_rows) == rejected
        and [row.get("requests_used") for row in admitted_rows] == list(range(1, used + 1))
        and all(row.get("requests_remaining") == LIMIT - index for index, row in enumerate(admitted_rows, 1))
        and all(row.get("requests_used") == LIMIT for row in rejected_rows)
    )

    observations = [value for record in records if (value := budget_observation(record))]
    valid_observations = [
        value
        for value in observations
        if value["proxy_source"]
        and value["current_remaining"] == LIMIT - value["current_used"]
        and value["delivery_used"] == min(LIMIT, value["current_used"] + 1)
        and value["delivery_remaining"] == LIMIT - value["delivery_used"]
        and isinstance(used, int)
        and 1 <= value["current_used"] <= used
        and value["warning"] == (value["delivery_remaining"] <= RESERVE)
    ]
    warning_observations = [value for value in valid_observations if value["warning"]]
    warning_bound = bool(
        budget_integral
        and observations
        and len(valid_observations) == len(observations)
        and warning_observations
        and warning_observations[0]["delivery_remaining"] <= RESERVE
    )
    landing_headroom = bool(
        lifecycle_ok
        and budget_integral
        and type(used) is int
        and used < LIMIT
        and rejected == 0
    )

    output = args.workspace / "output"
    output_files = (
        sorted(str(path.relative_to(output)) for path in output.rglob("*") if path.is_file())
        if output.is_dir()
        else []
    )
    receipts, duplicates = parse_receipts(output / "zone_receipts.json")
    try:
        plan_text = (output / "budget_plan.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        plan_text = ""
    headings, labels, values = parse_plan(plan_text)
    expected_labels = [
        "Budget limit",
        "Finalization reserve",
        "Priority order",
        "Landing policy",
        "Completed",
        "Unfinished",
        "Calls used at stop decision",
        "Calls remaining at stop decision",
        "Stop signal",
        "Controller finalized",
    ]
    output_contract = bool(
        output_files == ["budget_plan.md", "zone_receipts.json"]
        and isinstance(receipts, dict)
        and list(receipts) == ITEMS
        and not duplicates
        and all(value is None or (isinstance(value, str) and RECEIPT.fullmatch(value)) for value in receipts.values())
        and headings == ["# Model-call budget plan", "## Initial strategy", "## Final status"]
        and labels == expected_labels
    )

    ledger = ledger_rows(run_dir)
    issued = issued_receipts(ledger)
    completed = [item for item in ITEMS if isinstance(receipts, dict) and receipts.get(item) is not None]
    visible_issuance = bool(
        issued
        and all(
            any(f"RECEIPT item={item} code={code}" in record["result"] for record in records)
            for item, code in issued.items()
        )
    )
    receipts_ok = bool(
        output_contract
        and visible_issuance
        and len(completed) >= 3
        and completed == ITEMS[: len(completed)]
        and issued == {item: receipts[item] for item in completed}
        and all(receipts[item] is None for item in ITEMS[len(completed) :])
    )

    final_state = load_json(
        run_dir / "artifacts" / "private_evidence" / "business_simulator" / "final_state.json",
        {},
    )
    state_completed = final_state.get("completed") if isinstance(final_state, dict) else None
    state_unfinished = [item for item in ITEMS if isinstance(state_completed, list) and item not in state_completed]
    first_warning = warning_observations[0] if warning_observations else None
    final_accounting = bool(
        output_contract
        and isinstance(state_completed, list)
        and set_text(values.get("Completed", "")) == state_completed
        and set_text(values.get("Unfinished", "")) == state_unfinished
        and first_warning is not None
        and values.get("Calls used at stop decision") == str(first_warning["delivery_used"])
        and values.get("Calls remaining at stop decision") == str(first_warning["delivery_remaining"])
        and values.get("Stop signal") == WARNING
        and values.get("Controller finalized", "").lower() == "yes"
        and final_state.get("finalized") is True
        and final_state.get("active") is None
    )

    harness_results = {
        HARNESS_RULES[0]: rule(
            lifecycle_ok,
            f"events={len(events)}; status={summary.get('status')}; trace_complete={summary.get('trace_complete')}; final_messages={len(final_messages)}",
        ),
        HARNESS_RULES[1]: rule(
            budget_integral,
            f"used={used}; remaining={budget_state.get('requests_remaining') if isinstance(budget_state, dict) else None}; rejected={rejected}; request_events={len(budget_rows)}",
        ),
        HARNESS_RULES[2]: rule(
            landing_headroom,
            f"used={used}; remaining={budget_state.get('requests_remaining') if isinstance(budget_state, dict) else None}; rejected={rejected}; lifecycle_ok={lifecycle_ok}; warning_bound={warning_bound}",
        ),
    }
    agent_results = {
        AGENT_RULES[0]: rule(
            output_contract,
            f"files={output_files}; headings={headings}; labels={labels}; duplicate_keys={duplicates}",
        ),
        AGENT_RULES[1]: rule(
            receipts_ok,
            f"completed={completed}; issued={issued}; visible_issuance={visible_issuance}",
        ),
        AGENT_RULES[2]: rule(
            final_accounting,
            f"state_completed={state_completed}; finalized={final_state.get('finalized') if isinstance(final_state, dict) else None}; first_warning={first_warning}; plan_values={values}",
        ),
    }
    payload = {
        "schema_version": "harnessbench_rule_results_v1",
        "rule_results": {
            "harness": [{"id": item_id, **harness_results[item_id]} for item_id in HARNESS_RULES],
            "agent": [{"id": item_id, **agent_results[item_id]} for item_id in AGENT_RULES],
        },
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
