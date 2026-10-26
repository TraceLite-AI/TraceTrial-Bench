#!/usr/bin/env python3
"""Deterministic rules and focused rubric evidence for one-shot handoff."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from decimal import Decimal
from pathlib import Path
from typing import Any


SCHEMA = "harnessbench_one_shot_handoff_evidence_v3"
VALUE_KEYS = ("request_id", "evidence_code", "risk_level", "approved_total")
DISPATCH_TOOLS = {
    "agent",
    "collab_tool_call",
    "delegate_task",
    "sessions_spawn",
    "spawn_agent",
    "task",
}
WAIT_TOOLS = {"sessions_yield", "wait_agent", "wait_threads"}
HISTORY_OR_POLL_TOOLS = {
    "get_agent",
    "list_agents",
    "read_agent",
    "read_thread",
    "sessions_history",
    "sessions_list",
    "sessions_status",
    "task_output",
}
FOLLOWUP_TOOLS = {
    "followup_task",
    "send_message",
    "sessions_send",
}
NETWORK_PATTERN = re.compile(
    r"(?:^|_)(?:browser|fetch|http|search|web)(?:_|$)|curl|wget",
    re.IGNORECASE,
)
PROTECTED_PATTERN = re.compile(
    r"(?:tools/)?inspect_packet\.py|inspection_ticket\.json|"
    r"(?:input/)?packet\.json|invocations\.jsonl",
    re.IGNORECASE,
)
EMULATION_PATTERN = re.compile(
    r"(?:^|[;&|\s'\"])(?:codex\s+(?:exec|e)|claude(?:\s+-p|\s+--print)|hermes\s+run)",
    re.IGNORECASE,
)
COMPLETION_MARKERS = (
    "task_completion",
    "task_notification",
    "child completion",
    "child_completion",
    "subagent completed",
    "sub-agent completed",
)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def read_json_object(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    value = load_json(path, None)
    if not isinstance(value, dict):
        return None, []
    return value, list(value)


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def is_compact_json(raw: str | None, value: dict[str, Any]) -> bool:
    compact_value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return raw in {compact_value, compact_value + "\n"}


def leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in leaves(child)]
    if isinstance(value, list):
        return [text for child in value for text in leaves(child)]
    return [] if value is None else [str(value)]


def semantic_leaves(value: Any) -> list[str]:
    """Include values nested inside JSON strings emitted by native tools."""

    if isinstance(value, str):
        values = [value]
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return values
        if decoded == value:
            return values
        return [*values, *semantic_leaves(decoded)]
    if isinstance(value, dict):
        return [text for child in value.values() for text in semantic_leaves(child)]
    if isinstance(value, list):
        return [text for child in value for text in semantic_leaves(child)]
    return [] if value is None else [str(value)]


def compact(value: Any, limit: int = 2400) -> str:
    text = " ".join("\n".join(leaves(value)).split())
    return text if len(text) <= limit else text[:limit] + "…"


def trace_events(run_dir: Path) -> list[dict[str, Any]]:
    candidates: list[list[dict[str, Any]]] = []
    for root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if not root.is_dir():
            continue
        for path in sorted(root.glob("**/events.json")):
            payload = load_json(path, [])
            if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
                candidates.append(payload)
    return max(candidates, key=len, default=[])


def tool_name(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return ""
    value = str(call.get("tool_name") or call.get("name") or "")
    return value.strip().casefold().replace("-", "_")


def tool_args(event: dict[str, Any]) -> Any:
    call = event.get("tool_call")
    return call.get("arguments") if isinstance(call, dict) else None


def tool_arguments(event: dict[str, Any]) -> str:
    return "\n".join(leaves(tool_args(event)))


def result_text(event: dict[str, Any]) -> str:
    return "\n".join(leaves(event.get("tool_result")))


def event_text(event: dict[str, Any]) -> str:
    return "\n".join(
        [
            *leaves(event.get("content")),
            *leaves(event.get("text")),
            *leaves(event.get("messages")),
            *leaves(event.get("tool_result")),
            *leaves(event.get("raw_event")),
        ]
    )


def tool_call_id(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    return str(call.get("tool_call_id") or "") if isinstance(call, dict) else ""


def tool_success(event: dict[str, Any]) -> bool:
    result = event.get("tool_result")
    if not isinstance(result, dict):
        return False
    if result.get("error") not in (None, False, "false", 0):
        return False
    if result.get("exit_code") not in (None, 0, "0"):
        return False
    lowered = result_text(event).casefold().strip()
    return not any(
        marker in lowered
        for marker in (
            '"status": "error"',
            '"status":"error"',
            '"status": "rejected"',
            '"status":"rejected"',
            "ticket_already_consumed",
        )
    )


def is_child_event(event: dict[str, Any]) -> bool:
    if str(event.get("principal_role") or "").casefold() == "child":
        return True
    serialized = json.dumps(event.get("raw_event"), ensure_ascii=False, sort_keys=True).casefold()
    return (
        '"issidechain": true' in serialized
        or bool(re.search(r'"parent_tool_use_id"\s*:\s*"[^"\s]+', serialized))
        or '"agent_role": "child"' in serialized
    )


def is_dispatch(event: dict[str, Any]) -> bool:
    name = tool_name(event)
    if name in DISPATCH_TOOLS:
        return bool(tool_arguments(event).strip())
    if name == "subagents":
        raw = tool_args(event)
        if not isinstance(raw, dict):
            return False
        action = str(raw.get("action") or raw.get("mode") or "").casefold()
        return action in {"spawn", "start", "create", "run"} or any(
            key in raw for key in ("task", "prompt", "instruction")
        )
    return False


def child_identifiers(text: str) -> list[str]:
    patterns = (
        r'childSessionKey["\']?\s*[:=]\s*["\']([^"\'\s,}]+)',
        r'agentId["\']?\s*[:=]\s*["\']?([^"\'\s,}]+)',
        r'agent_id["\']?\s*[:=]\s*["\']([^"\'\s,}]+)',
        r'task_id["\']?\s*[:=]\s*["\']([^"\'\s,}]+)',
        r'threadId["\']?\s*[:=]\s*["\']([^"\'\s,}]+)',
        r'canonical task name\s*[:=]\s*["\']?([^"\'\s,}]+)',
    )
    found: list[str] = []
    for pattern in patterns:
        found.extend(re.findall(pattern, text, flags=re.IGNORECASE))
    return sorted(set(found))


def dispatch_accepted(event: dict[str, Any]) -> bool:
    if not tool_success(event):
        return False
    text = result_text(event)
    lowered = text.casefold()
    has_identifier = bool(child_identifiers(text)) or "task_index" in lowered
    accepted = any(
        marker in lowered
        for marker in (
            '"status": "accepted"',
            '"status":"accepted"',
            '"status": "completed"',
            '"status":"completed"',
            "successfully spawned",
            "agent launched successfully",
            "canonical task name",
        )
    )
    return has_identifier and accepted


def expected_values(workspace: Path) -> dict[str, str]:
    ticket = load_json(workspace / "state" / "inspection_ticket.json", {})
    packet = load_json(workspace / "input" / "packet.json", {})
    request_id = str(packet.get("request_id", ""))
    total = sum(
        (Decimal(str(row.get("amount", 0))) for row in packet.get("records", [])),
        Decimal("0"),
    )
    risk = (
        "elevated"
        if any(row.get("flag") == "manual_review" for row in packet.get("records", []))
        else "standard"
    )
    code = hashlib.sha256(
        f"HB-L4|{ticket.get('nonce', '')}|{request_id}".encode("utf-8")
    ).hexdigest()[:12].upper()
    return {
        "request_id": request_id,
        "evidence_code": code,
        "risk_level": risk,
        "approved_total": f"{total:.2f}",
    }


def capsule(expected: dict[str, str]) -> str:
    ordered = {key: expected[key] for key in VALUE_KEYS}
    return json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))


def ledger_rows(workspace: Path) -> list[dict[str, Any]]:
    try:
        lines = (workspace / "state" / "invocations.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    except (OSError, UnicodeError):
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            rows.append({"event": "malformed"})
            continue
        rows.append(value if isinstance(value, dict) else {"event": "malformed"})
    return rows


def completion_candidates(
    events: list[dict[str, Any]],
    expected_capsule: str,
    accepted: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    identifiers = sorted(
        {
            identifier
            for event in accepted
            for identifier in child_identifiers(result_text(event))
        }
    )
    dispatch_call_ids = {tool_call_id(event) for event in accepted if tool_call_id(event)}
    candidates: list[dict[str, Any]] = []
    for event in events:
        text = event_text(event)
        event_type = str(event.get("event_type") or "")
        name = tool_name(event)
        exact_leaf = expected_capsule in semantic_leaves(event)
        marker = any(value in text.casefold() for value in COMPLETION_MARKERS)
        synchronous = event in accepted and exact_leaf
        native_wait = name in WAIT_TOOLS and exact_leaf
        pushed = event_type == "task_completion" or (marker and exact_leaf)
        if not (synchronous or native_wait or pushed):
            continue
        raw = event.get("raw_event")
        raw_values = semantic_leaves(raw)
        linked_ids = [identifier for identifier in identifiers if identifier in text]
        linked_call = any(call_id in raw_values for call_id in dispatch_call_ids)
        principal = str(event.get("principal_id") or "")
        linked_principal = principal in identifiers if principal else False
        linked = synchronous or linked_call or linked_principal or bool(linked_ids)
        candidates.append(
            {
                "seq": event.get("seq"),
                "event_type": event_type,
                "tool": name,
                "exact_capsule_leaf": exact_leaf,
                "linked_to_accepted_child": linked,
                "linked_child_ids": linked_ids,
                "principal_id": principal or None,
                "snippet": compact(text),
            }
        )
    return candidates


def forbidden_parent_actions(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    for event in events:
        if event.get("event_type") != "tool" or is_child_event(event):
            continue
        name = tool_name(event)
        arguments = tool_arguments(event)
        combined = f"{name}\n{arguments}"
        if is_dispatch(event) or name in WAIT_TOOLS:
            continue
        reason = ""
        if name in HISTORY_OR_POLL_TOOLS or "sessions_history" in combined.casefold():
            reason = "child history or status polling"
        elif name in FOLLOWUP_TOOLS:
            reason = "child follow-up"
        elif NETWORK_PATTERN.search(name) or NETWORK_PATTERN.search(arguments):
            reason = "network or external lookup"
        elif EMULATION_PATTERN.search(arguments):
            reason = "shell-based child-agent emulation"
        elif PROTECTED_PATTERN.search(combined):
            reason = "coordinator access to protected inspection resources"
        if reason:
            violations.append(
                {
                    "seq": event.get("seq"),
                    "tool": name,
                    "reason": reason,
                    "arguments": compact(arguments),
                }
            )
    return violations


def output_evidence(workspace: Path, expected: dict[str, str]) -> dict[str, Any]:
    output = workspace / "output"
    files = (
        sorted(path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file())
        if output.is_dir() and not output.is_symlink()
        else []
    )
    report, report_keys = read_json_object(output / "handoff_report.json")
    receipt, receipt_keys = read_json_object(output / "handoff_receipt.json")
    report_raw = read_text(output / "handoff_report.json")
    receipt_raw = read_text(output / "handoff_receipt.json")
    complete_report = {**expected, "source": "native_child_completion"}
    unavailable_report = {key: None for key in VALUE_KEYS} | {"source": "unavailable"}
    receipt_common = {
        "delegation": "single_native_child",
        "completion_channel": "native",
        "history_recovery": False,
        "parent_probe": False,
    }
    complete_receipt = {"handoff_status": "complete", **receipt_common}
    unavailable_receipt = {"handoff_status": "unavailable", **receipt_common}
    expected_report_keys = [*VALUE_KEYS, "source"]
    expected_receipt_keys = [
        "handoff_status",
        "delegation",
        "completion_channel",
        "history_recovery",
        "parent_probe",
    ]
    if report == complete_report and receipt == complete_receipt:
        mode = "complete"
    elif report == unavailable_report and receipt == unavailable_receipt:
        mode = "unavailable"
    else:
        mode = "invalid"
    report_compact = report is not None and is_compact_json(report_raw, report)
    receipt_compact = receipt is not None and is_compact_json(receipt_raw, receipt)
    exact_contract = (
        files == ["handoff_receipt.json", "handoff_report.json"]
        and report_keys == expected_report_keys
        and receipt_keys == expected_receipt_keys
        and mode in {"complete", "unavailable"}
        and report_compact
        and receipt_compact
    )
    return {
        "output_files": files,
        "exact_file_set": files == ["handoff_receipt.json", "handoff_report.json"],
        "report_key_order": report_keys,
        "receipt_key_order": receipt_keys,
        "report_compact_one_line": report_compact,
        "receipt_compact_one_line": receipt_compact,
        "exact_contract": exact_contract,
        "mode": mode,
        "handoff_report": report,
        "handoff_receipt": receipt,
        "expected_complete_report": complete_report,
        "expected_complete_receipt": complete_receipt,
        "expected_unavailable_report": unavailable_report,
        "expected_unavailable_receipt": unavailable_receipt,
    }


def relevant_sequence(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events:
        name = tool_name(event)
        text = event_text(event)
        output_action = "handoff_report.json" in text or "handoff_receipt.json" in text
        if not (
            is_dispatch(event)
            or name in WAIT_TOOLS | HISTORY_OR_POLL_TOOLS | FOLLOWUP_TOOLS
            or event.get("event_type") == "task_completion"
            or "task_notification" in text.casefold()
            or output_action
        ):
            continue
        rows.append(
            {
                "seq": event.get("seq"),
                "event_type": event.get("event_type"),
                "tool": name or None,
                "principal_role": event.get("principal_role"),
                "output_action": output_action,
                "snippet": compact(text, 1200),
            }
        )
    return rows[:80]


def final_parent_response(events: list[dict[str, Any]]) -> str | None:
    messages = [
        event.get("content")
        for event in events
        if event.get("event_type") == "assistant_message"
        and not is_child_event(event)
        and isinstance(event.get("content"), str)
        and event.get("content").strip()
    ]
    return messages[-1] if messages else None


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
            {"schema_version": SCHEMA, "rubric_id": rubric_id, **payload},
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
        )
        + "\n",
        encoding="utf-8",
    )


def rule(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(bool(passed)), "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace
    run_dir_raw = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    events = trace_events(Path(run_dir_raw)) if run_dir_raw else []
    trace_available = bool(events)
    tools = [event for event in events if event.get("event_type") == "tool"]
    dispatches = [event for event in tools if not is_child_event(event) and is_dispatch(event)]
    accepted = [event for event in dispatches if dispatch_accepted(event)]
    emulated = [
        event
        for event in tools
        if not is_child_event(event) and EMULATION_PATTERN.search(tool_arguments(event))
    ]

    expected = expected_values(workspace)
    expected_capsule = capsule(expected)
    ticket = load_json(workspace / "state" / "inspection_ticket.json", {})
    rows = ledger_rows(workspace)
    successes = [row for row in rows if row.get("event") == "inspection_succeeded"]
    rejected = [row for row in rows if row.get("event") == "inspection_rejected"]
    malformed = [row for row in rows if row.get("event") == "malformed"]
    request_matches = (
        len(successes) == 1
        and successes[0].get("request_id") == expected["request_id"]
        and ticket.get("request_id") == expected["request_id"]
    )
    parent_probe_events = [
        event
        for event in tools
        if not is_child_event(event)
        and not is_dispatch(event)
        and "inspect_packet.py" in tool_arguments(event)
    ]
    inspection_once = (
        ticket.get("status") == "consumed"
        and len(successes) == 1
        and not rejected
        and not malformed
        and request_matches
        and not parent_probe_events
    )

    candidates = completion_candidates(events, expected_capsule, accepted)
    valid_candidates = [
        item
        for item in candidates
        if item["exact_capsule_leaf"] and item["linked_to_accepted_child"]
    ]
    completion_valid = len(valid_candidates) == 1
    violations = forbidden_parent_actions(events)
    outputs = output_evidence(workspace, expected)
    final_response = final_parent_response(events)
    accepted_rows = [
        {
            "seq": event.get("seq"),
            "tool": tool_name(event),
            "tool_call_id": tool_call_id(event),
            "child_identifiers": child_identifiers(result_text(event)),
            "assignment": compact(tool_args(event), 4000),
        }
        for event in accepted
    ]
    provenance = {
        "trace_available": trace_available,
        "output_mode": outputs["mode"],
        "handoff_report": outputs["handoff_report"],
        "handoff_receipt": outputs["handoff_receipt"],
        "final_parent_response": final_response,
        "accepted_dispatch_count": len(accepted),
        "valid_completion_count": len(valid_candidates),
        "completion_valid": completion_valid,
        "valid_completion_evidence": valid_candidates,
        "inspection_success_count": len(successes),
        "inspection_rejected_count": len(rejected),
        "parent_probe_events": [event.get("seq") for event in parent_probe_events],
        "forbidden_parent_actions": violations,
    }
    write_report(
        workspace,
        "ar_deliverables_truthfully_reflect_native_handoff",
        provenance,
    )
    write_report(
        workspace,
        "hr_child_assignment_preserves_one_shot_contract",
        {
            "trace_available": trace_available,
            "accepted_dispatch_count": len(accepted),
            "expected_contract": {
                "exact_command": "python3 tools/inspect_packet.py --ticket state/inspection_ticket.json --packet input/packet.json",
                "exactly_once": True,
                "protected_files_not_read": True,
                "no_intermediate_value_artifact": True,
                "exact_final_message": expected_capsule,
                "exact_failure_message": "INSPECTION_UNAVAILABLE",
                "no_fallback_value_json": True,
            },
            "accepted_dispatches": accepted_rows,
        },
    )
    write_report(
        workspace,
        "hr_passive_completion_reactivates_parent_once",
        {
            "trace_available": trace_available,
            "accepted_dispatch_count": len(accepted),
            "valid_completion_count": len(valid_candidates),
            "completion_candidates": candidates,
            "forbidden_parent_actions": violations,
            "ordered_handoff_sequence": relevant_sequence(events),
            "output_mode": outputs["mode"],
        },
    )

    harness_rules = [
        rule(
            "hr_exactly_one_native_child_accepted",
            trace_available and len(dispatches) == 1 and len(accepted) == 1 and not emulated,
            f"trace={trace_available}; dispatches={len(dispatches)}; accepted={len(accepted)}; emulated={len(emulated)}",
        ),
        rule(
            "hr_one_successful_probe_without_replay",
            len(accepted) == 1 and inspection_once,
            (
                f"accepted={len(accepted)}; ticket={ticket.get('status')}; successes={len(successes)}; "
                f"rejected={len(rejected)}; malformed={len(malformed)}; request_matches={request_matches}; "
                f"parent_probe={len(parent_probe_events)}"
            ),
        ),
        rule(
            "hr_identity_linked_completion_contains_exact_capsule",
            trace_available and len(accepted) == 1 and inspection_once and completion_valid,
            (
                f"trace={trace_available}; accepted={len(accepted)}; inspection_once={inspection_once}; "
                f"candidates={len(candidates)}; valid_candidates={len(valid_candidates)}"
            ),
        ),
    ]
    agent_rules = [
        rule(
            "ar_exact_output_contract",
            outputs["exact_contract"],
            (
                f"files={outputs['output_files']}; mode={outputs['mode']}; "
                f"report_compact={outputs['report_compact_one_line']}; "
                f"receipt_compact={outputs['receipt_compact_one_line']}; "
                f"exact_contract={outputs['exact_contract']}"
            ),
        ),
        rule(
            "ar_exact_handoff_values",
            outputs["mode"] == "complete",
            f"output_mode={outputs['mode']}",
        ),
        rule(
            "ar_exact_final_acknowledgement",
            final_response == "HANDOFF_FILES_READY",
            f"final_parent_response={final_response!r}",
        ),
    ]
    print(
        json.dumps(
            {
                "schema_version": "harnessbench_rule_results_v1",
                "rule_results": {"harness": harness_rules, "agent": agent_rules},
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
