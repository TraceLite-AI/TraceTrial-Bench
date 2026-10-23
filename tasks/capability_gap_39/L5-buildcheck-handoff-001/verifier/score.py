#!/usr/bin/env python3
"""Deterministic binary rules for the L4 one-shot native handoff probe."""

from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any


VALUE_KEYS = ("request_id", "evidence_code", "risk_level", "approved_total")
EXACT_COMMAND = (
    "python3 tools/inspect_bundle.py --ticket state/audit_ticket.json "
    "--target input/bundle.json"
)
DISPATCH_TOOLS = {
    "agent",
    "collab_tool_call",
    "collaboration__spawn_agent",
    "delegate_task",
    "sessions_spawn",
    "spawn_agent",
    "subagent",
    "task",
}
YIELD_TOOLS = {
    "collaboration__wait",
    "collaboration__wait_agent",
    "sessions_yield",
    "yield",
    "yield_control",
}
HISTORY_OR_POLL_TOOLS = {
    "get_agent",
    "list_agents",
    "read_agent",
    "read_thread",
    "sessions_history",
    "sessions_list",
    "sessions_status",
    "task_output",
    "wait_agent",
    "wait_for_agent",
}
FOLLOWUP_TOOLS = {
    "followup_task",
    "send_message",
    "sessions_send",
}
NETWORK_RE = re.compile(
    r"(?:^|_)(?:browser|fetch|http|search|web)(?:_|$)|(?:^|\s)(?:curl|wget)(?:\s|$)",
    re.IGNORECASE,
)
PROTECTED_RE = re.compile(
    r"tools/inspect_bundle\.py|state/audit_ticket\.json|"
    r"state/invocations\.jsonl|input/bundle\.json",
    re.IGNORECASE,
)
EMULATION_RE = re.compile(
    r"(?:^|[;&|\s'\"])(?:codex\s+(?:exec|e)|claude(?:\s+-p|\s+--print)|"
    r"openclaw\s+agent|hermes\s+run)(?:\s|$)",
    re.IGNORECASE,
)
MINIMAL_PAYLOAD_FORBIDDEN_RE = re.compile(
    r"live[_ -]?transcript|tool[_ -]?(?:trace|uses?)|command[_ -]?(?:log|output)|"
    r"token(?:s|[_ -]?usage)?|duration(?:_seconds|_ms)?|timing|cost|"
    r"reasoning[_ -]?content|model[_ -]?internals?|cache[_ -]?(?:read|write)",
    re.IGNORECASE,
)
OUTPUT_PATHS = ("output/handoff_report.json", "output/handoff_receipt.json")


class DuplicateKeyError(ValueError):
    pass


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(key)
        result[key] = value
    return result


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def load_output(path: Path) -> tuple[dict[str, Any] | None, list[str], str | None]:
    try:
        if path.is_symlink() or not path.is_file():
            return None, [], "missing or non-regular"
        raw = path.read_text(encoding="utf-8")
        value = json.loads(raw, object_pairs_hook=unique_object)
        if not isinstance(value, dict):
            return None, [], "not a JSON object"
        return value, list(value), raw
    except DuplicateKeyError as exc:
        return None, [], f"duplicate key {exc}"
    except UnicodeError:
        return None, [], "not UTF-8"
    except (OSError, json.JSONDecodeError) as exc:
        return None, [], f"invalid JSON: {exc}"


def leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in leaves(child)]
    if isinstance(value, list):
        return [text for child in value for text in leaves(child)]
    return [] if value is None else [str(value)]


def semantic_leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        result = [value]
        stripped = value.strip()
        if stripped[:1] in {"{", "["}:
            try:
                decoded = json.loads(stripped)
            except json.JSONDecodeError:
                return result
            result.extend(semantic_leaves(decoded))
        return result
    if isinstance(value, dict):
        return [text for child in value.values() for text in semantic_leaves(child)]
    if isinstance(value, list):
        return [text for child in value for text in semantic_leaves(child)]
    return [] if value is None else [str(value)]


def compact(value: Any, limit: int = 900) -> str:
    text = " ".join("\n".join(leaves(value)).split())
    return text if len(text) <= limit else text[:limit] + "..."


def trace_bundle(run_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
    for root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if not root.is_dir():
            continue
        for events_path in sorted(root.glob("**/events.json")):
            events = load_json(events_path, [])
            summary = load_json(events_path.with_name("summary.json"), {})
            if isinstance(events, list) and all(isinstance(item, dict) for item in events):
                candidates.append((events, summary if isinstance(summary, dict) else {}))
    return max(candidates, key=lambda item: len(item[0]), default=([], {}))


def tool_name(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return ""
    return str(call.get("tool_name") or call.get("name") or "").strip().casefold().replace("-", "_")


def tool_basename(event: dict[str, Any]) -> str:
    return tool_name(event).rsplit("__", 1)[-1]


def tool_call_id(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    return str(call.get("tool_call_id") or call.get("id") or "") if isinstance(call, dict) else ""


def tool_args_value(event: dict[str, Any]) -> Any:
    call = event.get("tool_call")
    return call.get("arguments") if isinstance(call, dict) else None


def tool_args(event: dict[str, Any]) -> str:
    return "\n".join(leaves(tool_args_value(event)))


def tool_result(event: dict[str, Any]) -> dict[str, Any] | None:
    result = event.get("tool_result")
    return result if isinstance(result, dict) else None


def result_text(event: dict[str, Any]) -> str:
    result = tool_result(event)
    return "\n".join(leaves(result)) if result is not None else ""


def tool_success(event: dict[str, Any]) -> bool:
    result = tool_result(event)
    if result is None:
        return False
    if result.get("error") not in (None, False, "false", 0):
        return False
    if result.get("exit_code") not in (None, 0, "0"):
        return False
    lowered = result_text(event).casefold()
    return not any(
        marker in lowered
        for marker in (
            '"status": "rejected"',
            '"status":"rejected"',
            '"status": "failed"',
            '"status":"failed"',
            "failed to spawn",
            "ticket_already_consumed",
        )
    )


def is_child_event(event: dict[str, Any]) -> bool:
    if str(event.get("principal_role") or "").casefold() == "child":
        return True
    raw = json.dumps(event.get("raw_event"), ensure_ascii=False, sort_keys=True).casefold()
    return (
        '"issidechain": true' in raw
        or '"agent_role": "child"' in raw
        or bool(re.search(r'"parent_tool_use_id"\s*:\s*"[^"\s]+', raw))
    )


def is_dispatch(event: dict[str, Any]) -> bool:
    name = tool_name(event)
    if name in DISPATCH_TOOLS:
        return bool(tool_args(event).strip())
    if name == "subagents":
        arguments = tool_args_value(event)
        if not isinstance(arguments, dict):
            return False
        action = str(arguments.get("action") or arguments.get("mode") or "").casefold()
        return action in {"spawn", "start", "create", "run"} or any(
            key in arguments for key in ("task", "prompt", "instruction")
        )
    return False


def expected_values(workspace: Path) -> dict[str, str]:
    ticket = load_json(workspace / "state" / "audit_ticket.json", {})
    bundle = load_json(workspace / "input" / "bundle.json", {})
    request_id = str(bundle.get("request_id", ""))
    total = sum(
        (Decimal(str(row.get("amount", 0))) for row in bundle.get("records", [])),
        Decimal("0"),
    )
    risk = (
        "elevated"
        if any(row.get("flag") == "manual_review" for row in bundle.get("records", []))
        else "standard"
    )
    evidence = hashlib.sha256(
        f"HB-L4|{ticket.get('nonce', '')}|{request_id}".encode("utf-8")
    ).hexdigest()[:12].upper()
    return {
        "request_id": request_id,
        "evidence_code": evidence,
        "risk_level": risk,
        "approved_total": f"{total:.2f}",
    }


def capsule(expected: dict[str, str]) -> str:
    return json.dumps(
        {key: expected[key] for key in VALUE_KEYS},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def child_principals(events: list[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            str(event.get("principal_id"))
            for event in events
            if is_child_event(event)
            and isinstance(event.get("principal_id"), str)
            and event.get("principal_id")
        }
    )


def identity_values(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).casefold().replace("_", "")
            if normalized in {
                "agentid",
                "childid",
                "childsessionkey",
                "taskid",
                "taskname",
                "threadid",
            } and isinstance(child, (str, int)) and str(child).strip():
                found.append(str(child).strip())
            found.extend(identity_values(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(identity_values(child))
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped[:1] in {"{", "["}:
            try:
                found.extend(identity_values(json.loads(stripped)))
            except json.JSONDecodeError:
                pass
        patterns = (
            r"agentId[\"']?\s*[:=]\s*[\"']?([^\"'\s,}<]+)",
            r"canonical task name\s*[:=]\s*[\"']?([^\"'\s,}<]+)",
        )
        for pattern in patterns:
            found.extend(re.findall(pattern, value, flags=re.IGNORECASE))
    return list(dict.fromkeys(item for item in found if item))


def event_linked(
    event: dict[str, Any], identities: set[str], dispatch_call_id: str
) -> bool:
    if is_dispatch(event):
        return True
    principal = str(event.get("principal_id") or "")
    text = "\n".join(
        [
            *leaves(event.get("content")),
            *leaves(event.get("last_assistant_message")),
            *leaves(event.get("tool_result")),
            *leaves(event.get("raw_event")),
        ]
    )
    if principal and any(
        principal == identity or identity in principal or principal in identity
        for identity in identities
    ):
        return True
    if any(identity in text for identity in identities):
        return True
    return bool(dispatch_call_id and dispatch_call_id in text)


def dispatch_contract(
    dispatch: dict[str, Any], events: list[dict[str, Any]], expected_capsule: str
) -> tuple[bool, set[str], str]:
    result = tool_result(dispatch)
    direct = identity_values(result)
    principals = child_principals(events)
    exact_sync = expected_capsule in semantic_leaves(result)
    lowered = result_text(dispatch).casefold()
    structured_status = (
        str(result.get("status") or result.get("state") or "").casefold()
        if isinstance(result, dict)
        else ""
    )
    status_proof = exact_sync or structured_status in {
        "accepted",
        "completed",
        "finished",
        "running",
        "spawned",
        "success",
        "succeeded",
    } or (
        tool_name(dispatch) == "collaboration__spawn_agent" and bool(direct)
    ) or any(
        marker in lowered
        for marker in (
            '"status": "accepted"',
            '"status":"accepted"',
            '"status": "completed"',
            '"status":"completed"',
            "successfully spawned",
            "subagent completed",
            "canonical task name",
        )
    )
    identities = set(direct)
    if len(principals) == 1:
        identities.add(principals[0])
    call_id = tool_call_id(dispatch)
    if call_id:
        identities.add(call_id)
    accepted = tool_success(dispatch) and status_proof and bool(identities)
    return accepted, identities, (
        f"tool={tool_name(dispatch)!r}; success={tool_success(dispatch)}; "
        f"status_proof={status_proof}; structured_status={structured_status!r}; "
        f"exact_sync={exact_sync}; "
        f"direct_ids={direct!r}; child_principals={principals!r}; call_id={call_id!r}"
    )


def assignment_contract(dispatches: list[dict[str, Any]]) -> tuple[bool, str]:
    if len(dispatches) != 1:
        return False, f"dispatch_request_count={len(dispatches)}"
    text = tool_args(dispatches[0])
    normalized = " ".join(text.split())
    skeleton = (
        '{"request_id":"<request_id>","evidence_code":"<evidence_code>",'
        '"risk_level":"<risk_level>","approved_total":"<approved_total>"}'
    )
    checks = {
        "exact_command_once": normalized.count(EXACT_COMMAND) == 1,
        "exactly_once": "exactly once" in normalized.casefold(),
        "all_protected_paths": all(
            path in normalized
            for path in (
                "tools/inspect_bundle.py",
                "state/audit_ticket.json",
                "state/invocations.jsonl",
                "input/bundle.json",
            )
        ),
        "no_intermediate_artifact": (
            "do not write" in normalized.casefold()
            and "intermediate" in normalized.casefold()
        ),
        "exact_success_skeleton": skeleton in normalized,
        "compact_one_line": all(
            marker in normalized.casefold()
            for marker in ("compact", "one line", "no markdown", "no extra")
        ),
        "exact_failure": "INSPECTION_UNAVAILABLE" in normalized,
        "no_fallback_values": all(
            marker in normalized.casefold()
            for marker in ("null", "empty", "partial", "inferred", "guessed")
        ),
    }
    return all(checks.values()), f"checks={checks!r}"


def ledger_state(workspace: Path, expected: dict[str, str]) -> tuple[bool, str]:
    ticket = load_json(workspace / "state" / "audit_ticket.json", {})
    rows: list[dict[str, Any]] = []
    try:
        raw_rows = (workspace / "state" / "invocations.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    except (OSError, UnicodeError):
        raw_rows = []
    malformed = 0
    for line in raw_rows:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(value, dict):
            rows.append(value)
        else:
            malformed += 1
    successes = [row for row in rows if row.get("event") == "inspection_succeeded"]
    rejected = [row for row in rows if row.get("event") == "inspection_rejected"]
    passed = (
        ticket.get("status") == "consumed"
        and ticket.get("request_id") == expected["request_id"]
        and len(successes) == 1
        and successes[0].get("request_id") == expected["request_id"]
        and successes[0].get("ticket_after") == "consumed"
        and not rejected
        and malformed == 0
    )
    return passed, (
        f"ticket_status={ticket.get('status')!r}; successes={len(successes)}; "
        f"rejected={len(rejected)}; malformed={malformed}"
    )


def child_probe_contract(
    events: list[dict[str, Any]], identities: set[str], dispatch_call_id: str
) -> tuple[bool, list[int], str]:
    child_tools = [
        event
        for event in events
        if event.get("event_type") == "tool" and is_child_event(event)
    ]
    prescribed: list[dict[str, Any]] = []
    continuations: list[dict[str, Any]] = []
    for event in child_tools:
        arguments = tool_args_value(event)
        command = ""
        if isinstance(arguments, dict):
            command = str(arguments.get("command") or arguments.get("cmd") or "").strip()
        if command == EXACT_COMMAND:
            prescribed.append(event)
        elif (
            tool_basename(event) == "process"
            and isinstance(arguments, dict)
            and str(arguments.get("action") or "").casefold() in {"poll", "log"}
        ):
            continuations.append(event)
    disallowed = [
        event for event in child_tools if event not in prescribed and event not in continuations
    ]
    linked = [
        event
        for event in prescribed
        if event_linked(event, identities, dispatch_call_id)
    ]
    passed = (
        len(prescribed) == 1
        and len(linked) == 1
        and tool_success(prescribed[0])
        and not disallowed
    )
    seqs = [
        event["seq"] for event in prescribed if isinstance(event.get("seq"), int)
    ]
    return passed, seqs, (
        f"child_tools={[tool_name(event) for event in child_tools]!r}; "
        f"prescribed={len(prescribed)}; linked={len(linked)}; "
        f"continuations={len(continuations)}; "
        f"disallowed={[tool_name(event) for event in disallowed]!r}"
    )


def visible_completion_payload(event: dict[str, Any]) -> Any:
    if event.get("event_type") == "task_completion":
        return event.get("content")
    if event.get("event_type") == "subagent_lifecycle":
        return event.get("last_assistant_message")
    if event.get("event_type") == "tool":
        return event.get("tool_result")
    return None


def completion_candidates(
    events: list[dict[str, Any]],
    expected_capsule: str,
    dispatch: dict[str, Any] | None,
    identities: set[str],
    child_probe_seqs: list[int],
) -> list[dict[str, Any]]:
    dispatch_call_id = tool_call_id(dispatch) if dispatch is not None else ""
    child_done_seq = max(child_probe_seqs) if child_probe_seqs else None
    candidates: list[dict[str, Any]] = []
    for event in events:
        event_type = event.get("event_type")
        name = tool_name(event)
        synchronous = dispatch is not None and event is dispatch
        waited = event_type == "tool" and name in YIELD_TOOLS
        pushed = event_type == "task_completion"
        lifecycle = (
            event_type == "subagent_lifecycle"
            and str(event.get("action") or "").casefold() in {"finished", "completed"}
            and event.get("parent_reactivated") is True
        )
        if not (synchronous or waited or pushed or lifecycle):
            continue
        payload = visible_completion_payload(event)
        exact = expected_capsule in semantic_leaves(payload)
        linked = synchronous or event_linked(event, identities, dispatch_call_id)
        seq = event.get("seq")
        after_probe = (
            isinstance(seq, int)
            and isinstance(child_done_seq, int)
            and seq > child_done_seq
        )
        if exact:
            candidates.append(
                {
                    "seq": seq,
                    "event": event,
                    "kind": (
                        "synchronous" if synchronous else "wait" if waited else event_type
                    ),
                    "linked": linked,
                    "after_probe": after_probe,
                    "minimal": completion_payload_minimal(payload, expected_capsule),
                }
            )
    return candidates


def completion_payload_minimal(payload: Any, expected_capsule: str) -> bool:
    if expected_capsule not in semantic_leaves(payload):
        return False
    text = "\n".join(leaves(payload))
    without_capsule = text.replace(expected_capsule, "")
    return MINIMAL_PAYLOAD_FORBIDDEN_RE.search(without_capsule) is None


def output_write_events(events: list[dict[str, Any]], relative_path: str) -> list[dict[str, Any]]:
    basename = Path(relative_path).name
    result: list[dict[str, Any]] = []
    for event in events:
        if event.get("event_type") != "tool" or is_child_event(event):
            continue
        arguments = tool_args(event)
        if basename not in arguments:
            continue
        name = tool_basename(event)
        lowered = arguments.casefold()
        explicit_write = name in {"write", "create_file", "apply_patch"}
        shell_write = bool(
            re.search(
                rf"(?:>|>>|tee\s+|touch\s+|cp\s+\S+\s+|mv\s+\S+\s+)[^\n;]*{re.escape(basename.casefold())}",
                lowered,
            )
        )
        if explicit_write or shell_write:
            result.append(event)
    return result


def output_read_or_validation_events(
    events: list[dict[str, Any]], write_events: set[int]
) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    for event in events:
        if event.get("event_type") != "tool" or is_child_event(event):
            continue
        if id(event) in write_events:
            continue
        arguments = tool_args(event)
        if not any(Path(path).name in arguments for path in OUTPUT_PATHS):
            continue
        name = tool_basename(event)
        if name in {"read", "view", "open", "cat", "validate", "check"} or re.search(
            r"(?:^|[;&|\s])(?:cat|jq|python3?|sha256sum|wc|diff|test\s+-[ef])(?:\s|$)",
            arguments,
            re.IGNORECASE,
        ):
            violations.append(event)
    return violations


def output_contract(workspace: Path, expected: dict[str, str]) -> dict[str, Any]:
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
    report, report_keys, report_raw = load_output(output / "handoff_report.json")
    receipt, receipt_keys, receipt_raw = load_output(output / "handoff_receipt.json")
    complete_report = {**expected, "source": "native_child_completion"}
    unavailable_report = {key: None for key in VALUE_KEYS} | {"source": "unavailable"}
    common = {
        "delegation": "single_native_child",
        "completion_channel": "native",
        "history_recovery": False,
        "parent_probe": False,
    }
    complete_receipt = {"handoff_status": "complete", **common}
    unavailable_receipt = {"handoff_status": "unavailable", **common}
    mode = (
        "complete"
        if report == complete_report and receipt == complete_receipt
        else "unavailable"
        if report == unavailable_report and receipt == unavailable_receipt
        else "invalid"
    )
    compact_report = report is not None and report_raw in {
        json.dumps(report, ensure_ascii=False, separators=(",", ":")),
        json.dumps(report, ensure_ascii=False, separators=(",", ":")) + "\n",
    }
    compact_receipt = receipt is not None and receipt_raw in {
        json.dumps(receipt, ensure_ascii=False, separators=(",", ":")),
        json.dumps(receipt, ensure_ascii=False, separators=(",", ":")) + "\n",
    }
    exact = (
        files == ["handoff_receipt.json", "handoff_report.json"]
        and report_keys == [*VALUE_KEYS, "source"]
        and receipt_keys
        == [
            "handoff_status",
            "delegation",
            "completion_channel",
            "history_recovery",
            "parent_probe",
        ]
        and mode in {"complete", "unavailable"}
        and compact_report
        and compact_receipt
    )
    return {
        "files": files,
        "mode": mode,
        "exact": exact,
        "report": report,
        "receipt": receipt,
        "compact_report": compact_report,
        "compact_receipt": compact_receipt,
    }


def parent_forbidden_actions(
    events: list[dict[str, Any]], output_write_ids: set[int]
) -> list[str]:
    violations: list[str] = []
    for event in events:
        if event.get("event_type") != "tool" or is_child_event(event):
            continue
        if id(event) in output_write_ids:
            continue
        name = tool_name(event)
        arguments = tool_args(event)
        combined = f"{name}\n{arguments}"
        if is_dispatch(event) or name in YIELD_TOOLS:
            continue
        if name in HISTORY_OR_POLL_TOOLS:
            violations.append(f"history/status tool {name}")
        elif name in FOLLOWUP_TOOLS:
            violations.append(f"child follow-up tool {name}")
        elif NETWORK_RE.search(combined):
            violations.append(f"network/external tool {name}")
        elif EMULATION_RE.search(arguments):
            violations.append(f"shell child emulation via {name}")
        elif PROTECTED_RE.search(combined):
            violations.append(f"parent protected-resource access via {name}")
    return violations


def canonical_trace_complete(
    events: list[dict[str, Any]], summary: dict[str, Any]
) -> tuple[bool, str]:
    seqs = [event.get("seq") for event in events]
    contiguous = bool(seqs) and all(
        isinstance(seq, int) and not isinstance(seq, bool) for seq in seqs
    ) and seqs == list(range(1, len(events) + 1))
    starts = sum(event.get("event_type") == "run_started" for event in events)
    terminals = [
        event
        for event in events
        if event.get("event_type") in {"run_finished", "run_failed"}
    ]
    call_ids: list[str] = []
    paired = True
    for event in events:
        if event.get("event_type") != "tool":
            continue
        call = event.get("tool_call")
        result = event.get("tool_result")
        call_id = call.get("tool_call_id") if isinstance(call, dict) else None
        result_id = result.get("tool_call_id") if isinstance(result, dict) else None
        if not isinstance(call_id, str) or not call_id or call_id != result_id:
            paired = False
        else:
            call_ids.append(call_id)
    code = summary.get("exit_code")
    if not isinstance(code, int) or isinstance(code, bool):
        for event in reversed(terminals):
            candidate = event.get("exit_code")
            if isinstance(candidate, int) and not isinstance(candidate, bool):
                code = candidate
                break
    passed = (
        starts == 1
        and len(terminals) == 1
        and summary.get("trace_complete") is True
        and isinstance(code, int)
        and not isinstance(code, bool)
        and contiguous
        and summary.get("last_event_seq") == len(events)
        and paired
        and len(call_ids) == len(set(call_ids))
    )
    return passed, (
        f"events={len(events)}; starts={starts}; terminals={len(terminals)}; "
        f"trace_complete={summary.get('trace_complete')!r}; exit_code={code!r}; "
        f"contiguous={contiguous}; last_seq={summary.get('last_event_seq')!r}; "
        f"paired={paired}; unique_calls={len(call_ids) == len(set(call_ids))}"
    )


def resolved_exit_code(
    events: list[dict[str, Any]], summary: dict[str, Any]
) -> int | None:
    code = summary.get("exit_code")
    if isinstance(code, int) and not isinstance(code, bool):
        return code
    for event in reversed(events):
        if event.get("event_type") not in {"run_finished", "run_failed"}:
            continue
        candidate = event.get("exit_code")
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            return candidate
    return None


def final_parent_messages(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        event
        for event in events
        if event.get("event_type") == "assistant_message"
        and not is_child_event(event)
        and isinstance(event.get("content"), str)
        and event.get("content").strip()
    ]


def rule(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(bool(passed)), "reason": compact(reason)}


def assess(
    workspace: Path,
    events: list[dict[str, Any]],
    summary: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    expected = expected_values(workspace)
    expected_capsule = capsule(expected)
    parent_tools = [
        event
        for event in events
        if event.get("event_type") == "tool" and not is_child_event(event)
    ]
    dispatches = [event for event in parent_tools if is_dispatch(event)]
    assignment_ok, assignment_reason = assignment_contract(dispatches)
    dispatch = dispatches[0] if len(dispatches) == 1 else None
    if dispatch is not None:
        accepted, identities, dispatch_reason = dispatch_contract(
            dispatch, events, expected_capsule
        )
    else:
        accepted, identities, dispatch_reason = False, set(), "no unique dispatch"
    dispatch_ok = len(dispatches) == 1 and accepted
    dispatch_call_id = tool_call_id(dispatch) if dispatch is not None else ""

    child_probe_ok, child_probe_seqs, child_probe_reason = child_probe_contract(
        events, identities, dispatch_call_id
    )
    ledger_ok, ledger_reason = ledger_state(workspace, expected)
    parent_probe = [
        event
        for event in parent_tools
        if not is_dispatch(event)
        and EXACT_COMMAND in " ".join(tool_args(event).split())
    ]
    lineage_ok = dispatch_ok and child_probe_ok and ledger_ok and not parent_probe

    candidates = completion_candidates(
        events, expected_capsule, dispatch, identities, child_probe_seqs
    )
    valid_candidates = [
        candidate
        for candidate in candidates
        if candidate["linked"] and candidate["after_probe"]
    ]
    completion_ok = dispatch_ok and lineage_ok and len(valid_candidates) == 1
    completion = valid_candidates[0] if len(valid_candidates) == 1 else None
    completion_seq = completion.get("seq") if completion is not None else None
    payload_minimal = completion_ok and bool(completion and completion["minimal"])

    report_writes = output_write_events(events, OUTPUT_PATHS[0])
    receipt_writes = output_write_events(events, OUTPUT_PATHS[1])
    all_writes: list[dict[str, Any]] = []
    seen_write_ids: set[int] = set()
    for event in [*report_writes, *receipt_writes]:
        if id(event) not in seen_write_ids:
            seen_write_ids.add(id(event))
            all_writes.append(event)
    write_ids = {id(event) for event in all_writes}
    write_seqs = [
        event["seq"] for event in all_writes if isinstance(event.get("seq"), int)
    ]
    reads = output_read_or_validation_events(events, write_ids)
    yields = [event for event in parent_tools if tool_name(event) in YIELD_TOOLS]
    forbidden = parent_forbidden_actions(events, write_ids)
    explicit_nonresume = any(
        event.get("event_type") == "run_failed"
        and re.search(
            r"parent (?:did not|failed to) (?:resume|reactivate)",
            "\n".join(leaves(event)),
            re.IGNORECASE,
        )
        for event in events
    )
    synchronous = bool(completion and completion.get("kind") == "synchronous")
    pushed_reactivation = bool(
        completion
        and completion["event"].get("parent_reactivated") is True
    )
    waited_completion = bool(completion and completion.get("kind") == "wait")
    reactivated = synchronous or pushed_reactivation or waited_completion
    completion_before_writes = bool(
        completion_ok
        and isinstance(completion_seq, int)
        and len(report_writes) == 1
        and len(receipt_writes) == 1
        and len(write_seqs) == len(all_writes)
        and completion_seq < min(write_seqs)
    )
    passive_ok = (
        completion_before_writes
        and reactivated
        and len(yields) <= 1
        and not forbidden
        and not explicit_nonresume
    )

    outputs = output_contract(workspace, expected)
    truthful_outputs = outputs["exact"] and (
        (completion_ok and outputs["mode"] == "complete")
        or (not completion_ok and outputs["mode"] == "unavailable")
    )
    single_write_protocol = (
        len(report_writes) == 1
        and len(receipt_writes) == 1
        and not reads
        and not forbidden
    )
    fallback_boundary = max(
        [
            event.get("seq")
            for event in [*dispatches, *yields]
            if isinstance(event.get("seq"), int)
        ],
        default=None,
    )
    output_after_basis = (
        len(write_seqs) == len(all_writes)
        and bool(write_seqs)
        and (
            isinstance(completion_seq, int)
            and min(write_seqs) > completion_seq
            or not completion_ok
            and isinstance(fallback_boundary, int)
            and min(write_seqs) > fallback_boundary
        )
    )
    coordinator_ok = (
        len(dispatches) == 1
        and not EMULATION_RE.search(tool_args(dispatches[0]))
        and len(yields) <= 1
        and single_write_protocol
        and output_after_basis
    )

    messages = final_parent_messages(events)
    last_write_seq = max(write_seqs) if write_seqs else None
    after_write_messages = [
        event
        for event in messages
        if isinstance(event.get("seq"), int)
        and isinstance(last_write_seq, int)
        and event["seq"] > last_write_seq
    ]
    final_ack_ok = (
        len(after_write_messages) == 1
        and after_write_messages[0].get("content") == "HANDOFF_FILES_READY"
    )
    ack_anywhere = any(
        text == "HANDOFF_FILES_READY"
        for event in events
        for text in semantic_leaves(
            {
                "content": event.get("content"),
                "raw_event": event.get("raw_event"),
            }
        )
    )
    final_observable = final_ack_ok
    final_reason = (
        f"messages={[(event.get('seq'), event.get('content')) for event in messages]!r}; "
        f"write_seqs={write_seqs!r}; after_write_count={len(after_write_messages)}; "
        f"ack_anywhere={ack_anywhere}"
    )
    if not final_observable and not ack_anywhere:
        final_reason = "[AGENT BYPASS] no exact post-write acknowledgement; " + final_reason

    trace_ok, trace_reason = canonical_trace_complete(events, summary)
    exit_code = resolved_exit_code(events, summary)
    full_chain = (
        trace_ok
        and dispatch_ok
        and lineage_ok
        and completion_ok
        and passive_ok
        and payload_minimal
        and final_observable
        and truthful_outputs
        and outputs["mode"] == "complete"
        and exit_code == 0
    )
    chain_reason = (
        f"trace={trace_ok}; dispatch={dispatch_ok}; lineage={lineage_ok}; "
        f"completion={completion_ok}; passive={passive_ok}; minimal={payload_minimal}; "
        f"final={final_observable}; output_mode={outputs['mode']}; "
        f"truthful_outputs={truthful_outputs}; exit_code={exit_code!r}"
    )
    if not full_chain and all(
        (trace_ok, dispatch_ok, lineage_ok, completion_ok, passive_ok, payload_minimal)
    ) and (not final_observable or not truthful_outputs):
        chain_reason = "[AGENT BYPASS] Harness handoff succeeded but Agent finalization failed; " + chain_reason

    harness_rules = [
        rule("hr_canonical_trace_complete", trace_ok, trace_reason),
        rule(
            "hr_native_child_dispatch_accepted",
            dispatch_ok,
            f"dispatches={len(dispatches)}; accepted={accepted}; identities={sorted(identities)!r}; "
            f"child_probe_events={len(child_probe_seqs)}; {dispatch_reason}",
        ),
        rule(
            "hr_child_probe_lineage_exactly_once",
            lineage_ok,
            f"dispatch_ok={dispatch_ok}; child=({child_probe_reason}); ledger=({ledger_reason}); "
            f"parent_probe={len(parent_probe)}",
        ),
        rule(
            "hr_identity_linked_completion_exact_capsule",
            completion_ok,
            f"dispatch_ok={dispatch_ok}; lineage_ok={lineage_ok}; candidates={len(candidates)}; "
            f"valid={len(valid_candidates)}; candidate_details="
            f"{[(item['seq'], item['kind'], item['linked'], item['after_probe']) for item in candidates]!r}",
        ),
        rule(
            "hr_passive_completion_reactivates_before_output",
            passive_ok,
            f"completion_seq={completion_seq!r}; reactivated={reactivated}; "
            f"report_writes={[event.get('seq') for event in report_writes]!r}; "
            f"receipt_writes={[event.get('seq') for event in receipt_writes]!r}; "
            f"yields={len(yields)}; forbidden={forbidden!r}; "
            f"explicit_nonresume={explicit_nonresume}",
        ),
        rule(
            "hr_completion_payload_is_minimal",
            payload_minimal,
            f"completion_ok={completion_ok}; minimal="
            f"{completion.get('minimal') if completion else None}; "
            f"kind={completion.get('kind') if completion else None!r}",
        ),
        rule(
            "hr_parent_final_response_observable_after_writes",
            final_observable,
            final_reason,
        ),
        rule("hr_one_shot_handoff_chain_resolved", full_chain, chain_reason),
    ]
    agent_rules = [
        rule("ar_child_assignment_exact", assignment_ok, assignment_reason),
        rule(
            "ar_coordinator_single_consumption_protocol",
            coordinator_ok,
            f"dispatches={len(dispatches)}; yields={len(yields)}; "
            f"report_writes={len(report_writes)}; receipt_writes={len(receipt_writes)}; "
            f"reads={len(reads)}; forbidden={forbidden!r}; output_after_basis={output_after_basis}",
        ),
        rule(
            "ar_truthful_exact_deliverables",
            truthful_outputs,
            f"files={outputs['files']!r}; mode={outputs['mode']!r}; exact={outputs['exact']}; "
            f"completion_valid={completion_ok}; report_compact={outputs['compact_report']}; "
            f"receipt_compact={outputs['compact_receipt']}",
        ),
        rule(
            "ar_exact_final_acknowledgement",
            final_ack_ok,
            f"after_write_messages="
            f"{[(event.get('seq'), event.get('content')) for event in after_write_messages]!r}",
        ),
    ]
    return {"harness": harness_rules, "agent": agent_rules}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    run_dir_raw = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    events, summary = trace_bundle(Path(run_dir_raw)) if run_dir_raw else ([], {})
    results = assess(args.workspace, events, summary)
    print(
        json.dumps(
            {
                "schema_version": "harnessbench_rule_results_v1",
                "rule_results": results,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
